"""Lazy local camera, Ultralytics YOLO, and MediaPipe inference service."""
from __future__ import annotations

import importlib.util
import json
import os
import requests
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .gestures import HAND_CONNECTIONS, GestureSmoother, classify_hand
from .imitation import imitation_output


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HAND_MODEL_PATH = BACKEND_ROOT / "models" / "hand_landmarker.task"
DEFAULT_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


class VisionService:
    """Owns inference and camera lifecycle. It deliberately has no servo driver."""

    def __init__(self):
        self._lock = threading.RLock()
        self._inference_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._object_model = None
        self._hand_landmarker = None
        self._object_failed = False
        self._hand_failed = False
        self._last_video_timestamp = 0
        self._smoother = GestureSmoother()
        self._fps_samples: deque[float] = deque(maxlen=25)
        self.config = {
            "source": os.getenv("HANDOFF_CAMERA_SOURCE", "esp32").lower(),
            "camera_url": os.getenv("HANDOFF_CAMERA_URL", "http://192.168.4.1:81/stream"),
            "webcam_index": int(os.getenv("HANDOFF_WEBCAM_INDEX", "0")),
            "fallback_to_webcam": os.getenv("HANDOFF_WEBCAM_FALLBACK", "0").lower() not in {"0", "false", "no"},
            "object_detection_enabled": True,
            "hand_tracking_enabled": True,
            "confidence_threshold": float(os.getenv("HANDOFF_YOLO_CONFIDENCE", "0.35")),
            "yolo_model": os.getenv("HANDOFF_YOLO_MODEL", "yolo11n.pt"),
            "yolo_device": os.getenv("HANDOFF_YOLO_DEVICE", ""),
            "hand_model_path": os.getenv("HANDOFF_HAND_MODEL_PATH", str(DEFAULT_HAND_MODEL_PATH)),
        }
        self._state = {
            "started": False,
            "camera_connected": False,
            "camera_state": "stopped",
            "active_source": None,
            "camera_notice": None,
            "fps": 0.0,
            "frames_processed": 0,
            "updated_at": None,
            "error": None,
            "components": {"object_detection": "idle", "hand_tracking": "idle"},
        }
        self._detections: list[dict] = []
        self._hand: dict | None = None
        self._gesture = {"name": "UNKNOWN", "confidence": 0.0}
        self._imitation = imitation_output(None)
        self._latest_raw_jpeg: bytes | None = None
        self._latest_jpeg: bytes | None = None

    def health(self) -> dict:
        with self._lock:
            config = dict(self.config)
        return {
            "ok": True,
            "runtime": "local",
            "dependencies": {
                "opencv": importlib.util.find_spec("cv2") is not None,
                "ultralytics": importlib.util.find_spec("ultralytics") is not None,
                "mediapipe": importlib.util.find_spec("mediapipe") is not None,
            },
            "models": {
                "yolo": config["yolo_model"],
                "hand_landmarker_path": config["hand_model_path"],
                "hand_landmarker_downloaded": Path(config["hand_model_path"]).is_file(),
            },
            "hardware_commands_enabled": False,
        }

    def camera_devices(self) -> dict:
        """List connected cameras and the OpenCV index used to select each one."""
        if sys.platform == "darwin":
            try:
                result = subprocess.run(
                    ["system_profiler", "SPCameraDataType", "-json"],
                    capture_output=True, text=True, check=True, timeout=15,
                )
                entries = json.loads(result.stdout).get("SPCameraDataType", [])
                default_index = next(
                    (index for index, entry in enumerate(entries)
                     if any(term in (entry.get("_name") or "").lower()
                            for term in ("macbook", "facetime", "built-in", "built in"))),
                    0 if entries else None,
                )
                cameras = [
                    {"index": index, "name": entry.get("_name") or f"Camera {index}",
                     "is_default": index == default_index}
                    for index, entry in enumerate(entries)
                ]
                return {"cameras": cameras, "default_index": default_index, "error": None}
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
                return {"cameras": [], "default_index": None,
                        "error": f"Could not list Mac cameras: {exc}"}

        try:
            import cv2
        except Exception as exc:
            return {"cameras": [], "default_index": None, "error": f"OpenCV is unavailable: {exc}"}

        cameras = []
        for index in range(10):
            capture = None
            try:
                capture = self._open_capture(cv2, "webcam", index)
                if capture.isOpened():
                    cameras.append({"index": index, "name": f"Camera {index}", "is_default": index == 0})
            except Exception:
                pass
            finally:
                if capture is not None:
                    capture.release()
        return {"cameras": cameras, "default_index": 0 if cameras else None, "error": None}

    def status(self) -> dict:
        with self._lock:
            components = dict(self._state["components"])
            if self.config["object_detection_enabled"] and self._object_model is not None:
                components["object_detection"] = "ready"
            if self.config["hand_tracking_enabled"] and self._hand_landmarker is not None:
                components["hand_tracking"] = "ready"
            return {**self._state, "components": components, "config": dict(self.config),
                    "detections_count": len(self._detections)}

    def detections(self) -> dict:
        with self._lock:
            return {"detections": list(self._detections), "fps": self._state["fps"],
                    "updated_at": self._state["updated_at"]}

    def hand(self) -> dict:
        with self._lock:
            return {"hand": self._hand}

    def gesture(self) -> dict:
        with self._lock:
            return dict(self._gesture)

    def imitation(self) -> dict:
        with self._lock:
            return dict(self._imitation)

    def latest_jpeg(self, view: str = "object-detection") -> bytes | None:
        with self._lock:
            if view == "default":
                return self._latest_raw_jpeg
            return self._latest_jpeg

    def start(self, options: dict | None = None) -> dict:
        options = options or {}
        with self._lock:
            changed = any(key in options and options[key] is not None and self.config.get(key) != options[key]
                          for key in ("source", "camera_url", "webcam_index", "fallback_to_webcam",
                                      "object_detection_enabled", "hand_tracking_enabled", "confidence_threshold"))
            running = self._thread and self._thread.is_alive() and not self._stop_event.is_set()
            if running and not changed:
                return self.status()
        if running and changed:
            self.stop()
        previous = self._thread
        if previous and previous.is_alive() and self._stop_event.is_set():
            previous.join(timeout=2.0)
            if previous.is_alive():
                raise RuntimeError("Vision worker is still stopping; retry in a moment")
        with self._lock:
            for key in ("source", "camera_url", "webcam_index", "fallback_to_webcam",
                        "object_detection_enabled", "hand_tracking_enabled", "confidence_threshold"):
                if key in options and options[key] is not None:
                    self.config[key] = options[key]
            self._state.update(started=True, camera_connected=False, camera_state="connecting",
                               camera_notice=None, error=None)
            self._latest_raw_jpeg = None
            self._latest_jpeg = None
            self._state["components"] = {
                "object_detection": "loading" if self.config["object_detection_enabled"] else "disabled",
                "hand_tracking": "loading" if self.config["hand_tracking_enabled"] else "disabled",
            }
            self._smoother.reset()
            self._fps_samples.clear()
            if self._thread and self._thread.is_alive() and not self._stop_event.is_set():
                return self.status()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._camera_loop, name="handoff-vision", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self._stop_event.set()
        with self._lock:
            self._state.update(started=False, camera_connected=False, camera_state="stopping",
                               camera_notice=None)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if not thread or not thread.is_alive():
                self._state["camera_state"] = "stopped"
                self._state["active_source"] = None
        return self.status()

    def _load_object_model(self):
        with self._inference_lock:
            if self._object_model is not None:
                return self._object_model
            if self._object_failed:
                raise RuntimeError("Object detector is unavailable; see /api/vision/health")
            try:
                from ultralytics import YOLO

                self._object_model = YOLO(self.config["yolo_model"])
                with self._lock:
                    self._state["components"]["object_detection"] = "ready"
                return self._object_model
            except Exception as exc:
                self._object_failed = True
                with self._lock:
                    self._state["components"]["object_detection"] = f"error: {exc}"
                raise

    def _load_hand_landmarker(self):
        with self._inference_lock:
            if self._hand_landmarker is not None:
                return self._hand_landmarker
            if self._hand_failed:
                raise RuntimeError("Hand tracker is unavailable; see /api/vision/health")
            try:
                import mediapipe as mp

                model_path = Path(self.config["hand_model_path"]).expanduser()
                if not model_path.is_file():
                    model_path.parent.mkdir(parents=True, exist_ok=True)
                    partial = model_path.with_suffix(model_path.suffix + ".download")
                    try:
                        with requests.get(DEFAULT_HAND_MODEL_URL, stream=True, timeout=(5, 30)) as response:
                            response.raise_for_status()
                            with partial.open("wb") as downloaded:
                                for chunk in response.iter_content(chunk_size=1024 * 256):
                                    if chunk:
                                        downloaded.write(chunk)
                        os.replace(partial, model_path)
                    finally:
                        partial.unlink(missing_ok=True)
                options = mp.tasks.vision.HandLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
                    running_mode=mp.tasks.vision.RunningMode.VIDEO,
                    num_hands=1,
                    min_hand_detection_confidence=0.5,
                    min_hand_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self._hand_landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
                with self._lock:
                    self._state["components"]["hand_tracking"] = "ready"
                return self._hand_landmarker
            except Exception as exc:
                self._hand_failed = True
                with self._lock:
                    self._state["components"]["hand_tracking"] = f"error: {exc}"
                raise

    def _object_detections(self, frame) -> list[dict]:
        model = self._load_object_model()
        kwargs = {"conf": self.config["confidence_threshold"], "verbose": False, "max_det": 50}
        if self.config["yolo_device"]:
            kwargs["device"] = self.config["yolo_device"]
        with self._inference_lock:
            prediction = model.predict(frame, **kwargs)[0]
        detections = []
        if prediction.boxes is None:
            return detections
        names = prediction.names
        for box in prediction.boxes:
            xyxy = box.xyxy[0].detach().cpu().tolist()
            class_id = int(box.cls[0].detach().cpu().item())
            detections.append({
                "label": str(names[class_id]), "class_id": class_id,
                "confidence": round(float(box.conf[0].detach().cpu().item()), 4),
                "box": [round(float(value), 1) for value in xyxy],
            })
        return detections

    def _hand_result(self, frame, *, smooth: bool) -> dict | None:
        import cv2
        import mediapipe as mp

        tracker = self._load_hand_landmarker()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        with self._inference_lock:
            timestamp_ms = max(int(time.monotonic() * 1000), self._last_video_timestamp + 1)
            self._last_video_timestamp = timestamp_ms
            result = tracker.detect_for_video(image, timestamp_ms)
        if not result.hand_landmarks:
            if smooth:
                self._smoother.reset()
            return None
        points = [[point.x, point.y, point.z] for point in result.hand_landmarks[0]]
        handedness = None
        hand_score = 0.85
        if result.handedness and result.handedness[0]:
            handedness = result.handedness[0][0].category_name
            hand_score = float(result.handedness[0][0].score)
        hand = classify_hand(points, handedness, hand_score)
        return self._smoother.update(hand) if smooth else hand

    @staticmethod
    def _annotate(frame, detections: list[dict], hand: dict | None):
        import cv2

        output = frame.copy()
        for detection in detections:
            x1, y1, x2, y2 = [int(value) for value in detection["box"]]
            label = f"{detection['label']} {detection['confidence']:.2f}"
            cv2.rectangle(output, (x1, y1), (x2, y2), (40, 220, 120), 2)
            cv2.putText(output, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (40, 220, 120), 2, cv2.LINE_AA)
        if hand:
            height, width = output.shape[:2]
            landmarks = hand["landmarks"]
            coords = [(int(p["x"] * width), int(p["y"] * height)) for p in landmarks]
            for start, end in HAND_CONNECTIONS:
                cv2.line(output, coords[start], coords[end], (255, 170, 50), 2, cv2.LINE_AA)
            for x, y in coords:
                cv2.circle(output, (x, y), 3, (60, 240, 255), -1, cv2.LINE_AA)
            caption = f"{hand['gesture']} {hand['confidence']:.2f}"
            cv2.putText(output, caption, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                        (255, 170, 50), 2, cv2.LINE_AA)
        return output

    def analyze_frame(self, frame, *, source: str, publish: bool) -> dict:
        import cv2

        # Keep the camera preview independent of inference. Model libraries can
        # mutate their input buffers, so encode the raw view from a snapshot.
        raw_frame = frame.copy()
        raw_encoded_ok, raw_encoded = cv2.imencode(
            ".jpg", raw_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82]
        )
        raw_jpeg = raw_encoded.tobytes() if raw_encoded_ok else None
        detections: list[dict] = []
        hand = None
        errors: dict[str, str] = {}
        if self.config["object_detection_enabled"]:
            try:
                detections = self._object_detections(frame)
            except Exception as exc:
                errors["object_detection"] = str(exc)
        if self.config["hand_tracking_enabled"]:
            try:
                hand = self._hand_result(frame, smooth=publish)
            except Exception as exc:
                errors["hand_tracking"] = str(exc)
        annotated = self._annotate(frame, detections, hand)
        encoded_ok, encoded = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        jpeg = encoded.tobytes() if encoded_ok else None
        gesture = {"name": hand["gesture"], "confidence": hand["confidence"]} if hand else {"name": "UNKNOWN", "confidence": 0.0}
        imitation = imitation_output(hand)
        now = time.time()
        result = {"source": source, "detections": detections, "hand": hand,
                  "gesture": gesture, "imitation": imitation, "errors": errors,
                  "annotated_image": f"data:image/jpeg;base64,{__import__('base64').b64encode(jpeg).decode('ascii')}" if jpeg and not publish else None,
                  "raw_image": f"data:image/jpeg;base64,{__import__('base64').b64encode(raw_jpeg).decode('ascii')}" if raw_jpeg and not publish else None,
                  "updated_at": now}
        if publish:
            with self._lock:
                self._detections = detections
                self._hand = hand
                self._gesture = gesture
                self._imitation = imitation
                self._latest_raw_jpeg = raw_jpeg
                self._latest_jpeg = jpeg
                self._state["frames_processed"] += 1
                self._state["updated_at"] = now
                self._state["error"] = "; ".join(f"{k}: {v}" for k, v in errors.items()) or None
                self._state["components"].update({k: f"error: {v}" for k, v in errors.items()})
        return result

    def analyze_static_image(self, image_bytes: bytes) -> dict:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode the image. Send a JPEG or PNG image.")
        return self.analyze_frame(frame, source="static_image", publish=False)

    def _camera_candidates(self, cv2) -> list[tuple[str, object]]:
        with self._lock:
            config = dict(self.config)
        if config["source"] not in {"esp32", "webcam"}:
            raise ValueError("source must be 'esp32' or 'webcam'")
        primary = ("esp32", config["camera_url"]) if config["source"] == "esp32" else ("webcam", config["webcam_index"])
        candidates = [primary]
        if config["source"] == "esp32" and config["fallback_to_webcam"]:
            candidates.append(("webcam", config["webcam_index"]))
        return candidates

    @staticmethod
    def _open_capture(cv2, source_name: str, value):
        if source_name == "esp32":
            try:
                timeout_params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000]
                return cv2.VideoCapture(value, cv2.CAP_FFMPEG, timeout_params)
            except (TypeError, cv2.error):
                return cv2.VideoCapture(value)
        return cv2.VideoCapture(int(value))

    def _camera_loop(self) -> None:
        try:
            import cv2
        except Exception as exc:
            with self._lock:
                self._state.update(started=False, camera_connected=False, camera_state="error",
                                   error=f"OpenCV is unavailable: {exc}")
            return

        while not self._stop_event.is_set():
            capture = None
            active_source = None
            try:
                candidates = self._camera_candidates(cv2)
            except Exception as exc:
                with self._lock:
                    self._state.update(camera_connected=False, camera_state="error", error=str(exc))
                return
            attempts = []
            for source_name, value in candidates:
                try:
                    capture = self._open_capture(cv2, source_name, value)
                    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    if capture.isOpened():
                        active_source = source_name
                        break
                    attempts.append(f"{source_name} source did not open ({value})")
                    capture.release()
                except Exception as exc:
                    attempts.append(f"{source_name} source error ({value}): {exc}")
                    if capture is not None:
                        capture.release()
                capture = None
            if capture is None or not capture.isOpened():
                with self._lock:
                    config = dict(self.config)
                if config["source"] == "esp32":
                    detail = (f"ESP32 stream did not open at {config['camera_url']}. Check that the camera is powered, "
                              "on the same network, and serving the MJPEG stream.")
                else:
                    detail = f"Could not open webcam index {config['webcam_index']}. Check camera permissions and that the device is available."
                additional_attempts = [attempt for attempt in attempts
                                       if config["source"] != "esp32" or not attempt.startswith("esp32 source did not open")]
                if additional_attempts:
                    detail += " " + "; ".join(additional_attempts)
                with self._lock:
                    self._state.update(camera_connected=False, camera_state="disconnected",
                                       active_source=None,
                                       camera_notice=None, error=detail)
                self._stop_event.wait(2.0)
                continue
            with self._lock:
                config = dict(self.config)
            notice = None
            if active_source == "webcam" and config["source"] == "esp32":
                notice = (f"ESP32 stream did not open at {config['camera_url']}; "
                          f"showing webcam {config['webcam_index']} fallback.")
            with self._lock:
                self._state.update(camera_connected=True, camera_state="connected",
                                   active_source=active_source, camera_notice=notice, error=None)
            failed_reads = 0
            while not self._stop_event.is_set():
                ok, frame = capture.read()
                if not ok or frame is None:
                    failed_reads += 1
                    if failed_reads >= 3:
                        with self._lock:
                            self._state.update(camera_connected=False, camera_state="disconnected",
                                               camera_notice=None,
                                               error=f"{active_source} camera stream stopped; reconnecting.")
                        break
                    self._stop_event.wait(0.1)
                    continue
                failed_reads = 0
                now = time.monotonic()
                self._fps_samples.append(now)
                fps = (len(self._fps_samples) - 1) / max(self._fps_samples[-1] - self._fps_samples[0], 1e-6) if len(self._fps_samples) > 1 else 0.0
                with self._lock:
                    self._state["fps"] = round(fps, 1)
                try:
                    self.analyze_frame(frame, source=active_source, publish=True)
                except Exception as exc:
                    with self._lock:
                        self._state["error"] = str(exc)
            capture.release()
            self._stop_event.wait(0.7)
        with self._lock:
            self._state.update(started=False, camera_connected=False, camera_state="stopped", active_source=None)


vision_service = VisionService()
