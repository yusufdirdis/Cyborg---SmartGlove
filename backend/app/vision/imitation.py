"""Normalized, simulation-only mapping from visual finger estimates to glove channels."""
from __future__ import annotations


# Keep in sync with the builder-confirmed mapping in README.md.
SERVO_FINGERS = {
    1: ("pinky", "Pinky finger"),
    2: ("ring", "Ring finger"),
    3: ("middle", "Middle finger"),
    4: ("index", "Index finger"),
    5: ("thumb_bend", "Thumb flexion (up/down)"),
    6: ("thumb_opposition", "Thumb side-to-side"),
}


def imitation_output(hand: dict | None) -> dict:
    """Return normalized targets only. This module has no hardware transport dependency."""
    if not hand:
        return {
            "gesture": "UNKNOWN", "confidence": 0.0,
            "fingers": {name: 0.0 for name in ("index", "middle", "ring", "pinky", "thumb_bend", "thumb_opposition")},
            "servo_action": "NONE", "servo_targets": {}, "target_unit": "normalized_0_to_1",
            "execution_enabled": False, "hardware_commands_sent": False,
        }
    values = {name: round(max(0.0, min(1.0, float(value))) , 3)
              for name, value in hand["fingers"].items()}
    targets = {str(servo_id): {"movement": movement, "normalized_target": values[finger]}
               for servo_id, (finger, movement) in SERVO_FINGERS.items()}
    gesture = hand["gesture"]
    return {
        "gesture": gesture,
        "confidence": hand["confidence"],
        "fingers": values,
        "servo_action": gesture if gesture != "UNKNOWN" else "NONE",
        "servo_targets": targets,
        "target_unit": "normalized_0_to_1",
        "execution_enabled": False,
        "hardware_commands_sent": False,
    }
