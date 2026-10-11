"""Geometry-based gesture labels and finger-flexion estimates for 21 hand landmarks."""
from __future__ import annotations

from collections import Counter, deque
from math import acos, degrees, hypot


FINGER_JOINTS = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17),
)


def _point_distance(a: list[float], b: list[float]) -> float:
    return hypot(a[0] - b[0], a[1] - b[1])


def _joint_angle(a: list[float], b: list[float], c: list[float]) -> float:
    """Return the 2D angle ABC in degrees, clamped against float drift."""
    ba = (a[0] - b[0], a[1] - b[1])
    bc = (c[0] - b[0], c[1] - b[1])
    denominator = hypot(*ba) * hypot(*bc)
    if denominator < 1e-8:
        return 180.0
    cosine = max(-1.0, min(1.0, (ba[0] * bc[0] + ba[1] * bc[1]) / denominator))
    return degrees(acos(cosine))


def _flexion(angle: float) -> float:
    # Relative curl estimate only. These are not anatomical angles or servo calibration.
    return round(max(0.0, min(1.0, (165.0 - angle) / 80.0)), 3)


def classify_hand(points: list[list[float]], handedness: str | None = None,
                  landmark_confidence: float = 0.85) -> dict:
    """Estimate gesture/flexion from normalized x/y/z points in MediaPipe index order."""
    if len(points) != 21 or any(len(point) < 2 for point in points):
        raise ValueError("A hand requires exactly 21 normalized landmarks")

    flexions: dict[str, float] = {}
    for name, (mcp, pip, dip, _tip) in FINGER_JOINTS.items():
        flexions[name] = _flexion(_joint_angle(points[mcp], points[pip], points[dip]))
    thumb_angle = _joint_angle(points[2], points[3], points[4])
    flexions["thumb_bend"] = _flexion(thumb_angle)

    palm_width = max(_point_distance(points[5], points[17]), 1e-4)
    pinch_ratio = _point_distance(points[4], points[8]) / palm_width
    thumb_to_index_base = _point_distance(points[4], points[5]) / palm_width
    flexions["thumb_opposition"] = round(max(0.0, min(1.0, 1.0 - thumb_to_index_base)), 3)

    extended = {name: value < 0.36 for name, value in flexions.items() if name in FINGER_JOINTS}
    curled = {name: value > 0.57 for name, value in flexions.items() if name in FINGER_JOINTS}
    four_curled = all(curled.values())
    four_extended = all(extended.values())
    gesture = "UNKNOWN"
    confidence = 0.0

    if pinch_ratio < 0.30:
        gesture = "PINCH"
        confidence = min(0.99, max(0.55, 0.99 - pinch_ratio * 1.4))
    elif four_curled and flexions["thumb_bend"] > 0.35:
        gesture = "FIST"
        confidence = min(0.98, 0.62 + sum(flexions[name] for name in FINGER_JOINTS) / 10)
    elif extended["index"] and curled["middle"] and curled["ring"] and curled["pinky"]:
        gesture = "POINT"
        confidence = min(0.97, 0.65 + (sum(flexions[n] for n in ("middle", "ring", "pinky")) / 12))
    elif extended["index"] and extended["middle"] and curled["ring"] and curled["pinky"]:
        gesture = "PEACE"
        confidence = min(0.97, 0.65 + (flexions["ring"] + flexions["pinky"]) / 7)
    elif four_curled and flexions["thumb_bend"] < 0.35 and points[4][1] < points[2][1] - 0.04:
        gesture = "THUMBS_UP"
        confidence = min(0.96, 0.68 + sum(flexions[name] for name in FINGER_JOINTS) / 12)
    elif four_extended and flexions["thumb_bend"] < 0.45 and pinch_ratio >= 0.30:
        gesture = "OPEN_HAND"
        confidence = min(0.98, max(0.55, 0.98 - sum(flexions[name] for name in FINGER_JOINTS) / 8))

    confidence = round(max(0.0, min(1.0, confidence * max(0.0, min(1.0, landmark_confidence)))), 3)
    return {
        "gesture": gesture,
        "confidence": confidence,
        "handedness": handedness,
        "fingers": flexions,
        "pinch_ratio": round(pinch_ratio, 3),
        "landmarks": [{"x": round(p[0], 5), "y": round(p[1], 5), "z": round(p[2], 5) if len(p) > 2 else 0.0}
                      for p in points],
        "landmark_count": 21,
    }


class GestureSmoother:
    """Short rolling vote and EMA to make live labels and flexion values less jumpy."""
    def __init__(self, window: int = 5, alpha: float = 0.38):
        self.votes: deque[tuple[str, float]] = deque(maxlen=window)
        self.alpha = alpha
        self.flexions: dict[str, float] = {}

    def update(self, hand: dict) -> dict:
        self.votes.append((hand["gesture"], hand["confidence"]))
        counts = Counter(name for name, score in self.votes if name != "UNKNOWN" and score >= 0.5)
        stable = counts.most_common(1)[0][0] if counts else "UNKNOWN"
        scores = [score for name, score in self.votes if name == stable]
        for name, value in hand["fingers"].items():
            previous = self.flexions.get(name, value)
            self.flexions[name] = round(previous + self.alpha * (value - previous), 3)
        return {**hand, "gesture": stable, "confidence": round(sum(scores) / len(scores), 3) if scores else 0.0,
                "fingers": dict(self.flexions), "smoothing_window": len(self.votes)}

    def reset(self) -> None:
        self.votes.clear()
        self.flexions.clear()
