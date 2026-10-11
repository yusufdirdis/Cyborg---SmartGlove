import math
import json
from types import SimpleNamespace
import pytest

from fastapi.testclient import TestClient

from app import main as main_module
from app.vision.gestures import GestureSmoother, classify_hand
from app.vision.imitation import imitation_output
from app.vision.service import VisionService, vision_service
from app.vision import service as vision_service_module


def open_hand_points():
    return [
        [0.50, 0.80, 0], [0.42, 0.70, 0], [0.37, 0.62, 0], [0.31, 0.55, 0], [0.25, 0.48, 0],
        [0.42, 0.58, 0], [0.40, 0.43, 0], [0.39, 0.30, 0], [0.38, 0.17, 0],
        [0.50, 0.55, 0], [0.50, 0.40, 0], [0.50, 0.26, 0], [0.50, 0.12, 0],
        [0.58, 0.57, 0], [0.60, 0.43, 0], [0.61, 0.31, 0], [0.62, 0.19, 0],
        [0.66, 0.62, 0], [0.69, 0.50, 0], [0.70, 0.40, 0], [0.71, 0.30, 0],
    ]


def curl(points, start, joint, end, angle=100):
    direction = (points[start][0] - points[joint][0], points[start][1] - points[joint][1])
    length = math.hypot(*direction)
    ux, uy = direction[0] / length, direction[1] / length
    radians = math.radians(angle)
    rotated = (ux * math.cos(radians) - uy * math.sin(radians),
               ux * math.sin(radians) + uy * math.cos(radians))
    points[end] = [points[joint][0] + rotated[0] * 0.1, points[joint][1] + rotated[1] * 0.1, 0]


def gesture_pose(curl_fingers=(), thumb_bent=False, pinch=False):
    points = open_hand_points()
    indices = {"index": (5, 6, 7), "middle": (9, 10, 11),
               "ring": (13, 14, 15), "pinky": (17, 18, 19)}
    for name in curl_fingers:
        curl(points, *indices[name])
    if thumb_bent:
        curl(points, 2, 3, 4)
    if pinch:
        points[4] = [points[8][0] + 0.005, points[8][1] + 0.005, 0]
    return points


def test_landmark_geometry_recognizes_open_hand_and_reports_21_points():
    hand = classify_hand(open_hand_points(), "Right", 0.9)
    assert hand["gesture"] == "OPEN_HAND"
    assert hand["landmark_count"] == 21
    assert len(hand["landmarks"]) == 21
    assert set(hand["fingers"]) == {"index", "middle", "ring", "pinky", "thumb_bend", "thumb_opposition"}
    assert all(0 <= value <= 1 for value in hand["fingers"].values())


def test_geometry_classifier_covers_all_requested_gestures():
    poses = {
        "OPEN_HAND": gesture_pose(),
        "FIST": gesture_pose(("index", "middle", "ring", "pinky"), thumb_bent=True),
        "POINT": gesture_pose(("middle", "ring", "pinky")),
        "PEACE": gesture_pose(("ring", "pinky")),
        "PINCH": gesture_pose(pinch=True),
        "THUMBS_UP": gesture_pose(("index", "middle", "ring", "pinky")),
    }
    assert {classify_hand(points)["gesture"] for points in poses.values()} == set(poses)


def test_smoother_emits_stable_gesture_and_smoothed_finger_values():
    smoother = GestureSmoother(window=3)
    hand = classify_hand(open_hand_points())
    results = [smoother.update(hand) for _ in range(3)]
    assert results[-1]["gesture"] == "OPEN_HAND"
    assert results[-1]["smoothing_window"] == 3


def test_imitation_uses_current_builder_servo_map_and_never_enables_execution():
    hand = classify_hand(open_hand_points())
    result = imitation_output(hand)
    assert result["servo_targets"]["1"]["movement"] == "Pinky finger"
    assert result["servo_targets"]["4"]["movement"] == "Index finger"
    assert result["servo_targets"]["5"]["movement"] == "Thumb flexion (up/down)"
    assert result["execution_enabled"] is False
    assert result["hardware_commands_sent"] is False
    assert result["target_unit"] == "normalized_0_to_1"


def test_start_is_idempotent_when_camera_already_running():
    class RunningThread:
        @staticmethod
        def is_alive():
            return True

    service = VisionService()
    service._thread = RunningThread()
    service._state.update(started=True, camera_connected=True, camera_state="connected", active_source="webcam")
    state = service.start({})
    assert state["camera_connected"] is True
    assert state["camera_state"] == "connected"


def test_camera_stream_keeps_raw_and_annotated_views_separate():
    service = VisionService()
    raw = b"raw-frame"
    annotated = b"annotated-frame"
    service._latest_raw_jpeg = raw
    service._latest_jpeg = annotated
    assert service.latest_jpeg("default") is raw
    assert service.latest_jpeg("object-detection") is annotated


def test_raw_camera_jpeg_is_encoded_before_inference_can_mutate_the_frame(monkeypatch):
    cv2 = pytest.importorskip("cv2")  # optional: requirements-vision.txt
    import numpy as np

    service = VisionService()
    service.config["object_detection_enabled"] = True
    service.config["hand_tracking_enabled"] = False
    original_color = (30, 120, 220)
    frame = np.full((8, 8, 3), original_color, dtype=np.uint8)

    def mutate_frame(image):
        image[:] = 0
        return []

    monkeypatch.setattr(service, "_object_detections", mutate_frame)
    monkeypatch.setattr(service, "_annotate", lambda image, detections, hand: image)
    service.analyze_frame(frame, source="webcam", publish=True)

    raw = cv2.imdecode(np.frombuffer(service.latest_jpeg("default"), dtype=np.uint8), cv2.IMREAD_COLOR)
    annotated = cv2.imdecode(np.frombuffer(service.latest_jpeg("object-detection"), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert np.allclose(raw.mean(axis=(0, 1)), original_color, atol=8)
    assert annotated.mean() == 0


def test_status_reports_loaded_models_as_ready_after_a_reconnect():
    service = VisionService()
    service._object_model = object()
    service._hand_landmarker = object()
    service._state["components"] = {"object_detection": "loading", "hand_tracking": "loading"}
    assert service.status()["components"] == {"object_detection": "ready", "hand_tracking": "ready"}


def test_macos_camera_inventory_names_the_builtin_and_external_inputs(monkeypatch):
    monkeypatch.setattr(vision_service_module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        vision_service_module.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"SPCameraDataType": [
            {"_name": "USB Camera"}, {"_name": "MacBook Air Camera"},
        ]})),
    )

    result = VisionService().camera_devices()

    assert result["default_index"] == 1
    assert result["cameras"] == [
        {"index": 0, "name": "USB Camera", "is_default": False},
        {"index": 1, "name": "MacBook Air Camera", "is_default": True},
    ]


def test_vision_camera_inventory_endpoint(monkeypatch):
    expected = {"cameras": [{"index": 0, "name": "MacBook Air Camera", "is_default": True}],
                "default_index": 0, "error": None}
    monkeypatch.setattr(vision_service, "camera_devices", lambda: expected)
    with TestClient(main_module.app) as client:
        response = client.get("/api/vision/cameras")
    assert response.status_code == 200
    assert response.json() == expected


def test_single_frame_endpoint_returns_the_selected_camera_view(monkeypatch):
    monkeypatch.setattr(vision_service, "latest_jpeg", lambda view: b"jpeg-frame")
    monkeypatch.setattr(vision_service, "status", lambda: {"started": True})
    with TestClient(main_module.app) as client:
        response = client.get("/api/vision/frame.jpg?view=default")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"jpeg-frame"


def test_vision_api_health_and_development_page():
    with TestClient(main_module.app) as client:
        health = client.get("/api/vision/health")
        assert health.status_code == 200
        assert health.json()["runtime"] == "local"
        assert health.json()["hardware_commands_enabled"] is False
        page = client.get("/vision/ui")
        assert page.status_code == 200
        assert "Vision Lab" in page.text
        assert "NO SERVO COMMANDS" in page.text
        assert "Default view" in page.text
        assert "Object detection view" in page.text
        assert "Inference continues in the backend in both views" in page.text
        assert client.get("/api/vision/gesture").json() == {"name": "UNKNOWN", "confidence": 0.0}
        assert client.get("/api/vision/stream.mjpg?view=unknown").status_code == 422
        imitation = client.get("/api/vision/imitation").json()
        assert imitation["execution_enabled"] is False
        assert imitation["hardware_commands_sent"] is False


def test_static_image_api_rejects_invalid_media_type_and_returns_schema(monkeypatch):
    monkeypatch.setattr(vision_service, "analyze_static_image",
                        lambda data: {"source": "static_image", "detections": [], "hand": None})
    with TestClient(main_module.app) as client:
        assert client.post("/api/vision/image", content=b"x").status_code == 415
        response = client.post("/api/vision/image", content=b"sample", headers={"content-type": "image/jpeg"})
        assert response.status_code == 200
        assert response.json() == {"source": "static_image", "detections": [], "hand": None}
