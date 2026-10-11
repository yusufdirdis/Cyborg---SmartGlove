# Local vision backend

This module adds local object detection and hand imitation behind the existing FastAPI app. It does not import the servo driver, the simulation routes, calibration data, or hardware-write controls. Every imitation response is normalized from 0 to 1 and states `execution_enabled: false` and `hardware_commands_sent: false`.

## Run

From `backend/`, create/activate the project's Python environment and install `requirements.txt`, then start the existing API:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-vision.txt
HANDOFF_LIVE_POSITIONS=0 HANDOFF_ENABLE_HARDWARE_WRITES=0 uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open the development-only viewer at <http://127.0.0.1:8000/vision/ui>. The first inference downloads the small pretrained `yolo11n.pt` COCO detector through Ultralytics and the MediaPipe Hand Landmarker task model. They run locally after download. The task file is stored under `backend/models/`; downloaded `.pt` and `.task` checkpoints are ignored by Git.

`yolo11n.pt` is the nano YOLO11 object-detection model pretrained on COCO. Its class list includes cup, bottle, cell phone, and keyboard. `HANDOFF_YOLO_MODEL` can name another Ultralytics-compatible checkpoint or local path. The default device is Ultralytics automatic local device selection; set `HANDOFF_YOLO_DEVICE=cpu` to force CPU inference. No training is started by this application.

The stream defaults to `http://192.168.4.1:81/stream`, commonly used when an ESP32 camera is in access-point mode. Set `HANDOFF_CAMERA_URL` to the actual camera URL or enter it in Vision Lab. Webcam fallback is off by default so a failed ESP32 connection cannot silently switch to the Mac camera. Enable **Allow webcam fallback** to use webcam index 0 when the ESP32 fails, or set `HANDOFF_WEBCAM_FALLBACK=1`. Set `HANDOFF_WEBCAM_INDEX` to select another webcam. Choose **Mac webcam** in the viewer to use a webcam directly.

The camera worker starts only after **Start inference** is clicked. Pressing Start again reconnects the selected source. The viewer remembers its camera settings in browser storage. If the ESP32 cannot be opened and fallback is enabled, the status and viewer identify the active webcam fallback. Turn off **Webcam fallback** to keep the viewer on the ESP32 connection error and avoid switching to the Mac camera. Disconnected sources are retried and report their configured URL or webcam index. Static JPEG/PNG files can be sent to the image endpoint without starting a camera.

## API

All JSON routes use the existing `/api` prefix.

| Method and path | Response |
|---|---|
| `GET /api/vision/health` | Dependency/model paths, local-runtime flag, and `hardware_commands_enabled: false`. |
| `GET /api/vision/status` | `started`, camera connection/state/source, `camera_notice` (including when webcam fallback is active), FPS, processed frame count, component status, errors, and active config. |
| `GET /api/vision/detections` | `{detections:[{label,class_id,confidence,box:[x1,y1,x2,y2]}],fps,updated_at}`. Boxes are image pixels; confidence is 0–1. |
| `GET /api/vision/hand` | `{hand: null}` when absent, otherwise handedness, gesture/confidence, normalized 21-point landmarks, and normalized finger-flexion/opposition estimates. |
| `GET /api/vision/gesture` | `{name,confidence}`. Supported labels: `OPEN_HAND`, `FIST`, `POINT`, `PEACE`, `PINCH`, `THUMBS_UP`, or `UNKNOWN`. |
| `GET /api/vision/imitation` | Gesture and finger estimates, servo channel mapping and normalized targets, `execution_enabled:false`, and `hardware_commands_sent:false`. |
| `POST /api/vision/start` | Starts a source. Optional JSON fields: `source` (`esp32` or `webcam`), `camera_url`, `webcam_index`, `fallback_to_webcam`, `object_detection_enabled`, `hand_tracking_enabled`, `confidence_threshold`. |
| `POST /api/vision/stop` | Stops camera inference. |
| `POST /api/vision/image` | Raw JPEG/PNG body (max 12 MB), `Content-Type: image/jpeg` or `image/png`; returns detections, hand, gesture, imitation, errors, and raw plus annotated JPEG data URLs. |
| `GET /api/vision/stream.mjpg?view=default` | Multipart MJPEG stream of the raw live camera frames. Inference continues in the backend. |
| `GET /api/vision/stream.mjpg?view=object-detection` | Multipart MJPEG stream with YOLO boxes and hand landmark overlays. |

The local page opens in Default view. Its Object detection option changes only the camera preview; YOLO, hand tracking, gesture classification, and imitation estimates continue running and updating in both views.

Hand landmark coordinates are normalized to the frame (`x` and `y` in 0–1, `z` relative depth). Finger values are geometry-based visual estimates, where 0 is extended and 1 is curled. The gesture vote uses a short temporal window; finger values use an exponential moving average. This is a development estimate, not a calibrated grasp or anatomical measurement.

The servo mapping follows the builder-confirmed repository mapping: ID 1 pinky, 2 ring, 3 middle, 4 index, 5 thumb flexion, and 6 thumb opposition. Imitation target values are normalized values only. They must not be converted into counts or forwarded to physical routes without a separate review, calibration, and explicit user-controlled integration.

## Dataset and future training

Keep future datasets outside the application module, for example in `backend/datasets/hand_gestures/`, and store large checkpoints in `backend/models/`. These folders are for developer-managed data; no dataset is committed by this feature.

For a custom YOLO gesture detector, annotate a full hand bounding box per example and label it with one of the desired classes (`OPEN_HAND`, `FIST`, `POINT`, `PEACE`, `PINCH`, `THUMBS_UP`). Example `data.yaml`:

```yaml
path: datasets/hand_gestures
train: images/train
val: images/val
names:
  0: OPEN_HAND
  1: FIST
  2: POINT
  3: PEACE
  4: PINCH
  5: THUMBS_UP
```

After collecting and reviewing a consented, representative dataset, train explicitly from `backend/` (this command is documentation only and has not been run):

```bash
.venv/bin/yolo detect train model=yolo11n.pt data=datasets/hand_gestures/data.yaml imgsz=640 epochs=100 project=models/gesture-detection name=run1
.venv/bin/yolo detect val model=models/gesture-detection/run1/weights/best.pt data=datasets/hand_gestures/data.yaml
```

Set `HANDOFF_YOLO_MODEL` to the selected checkpoint for a later detection-model integration. Any custom class names in that checkpoint are surfaced in the detections API, so gesture class labels can be reviewed alongside the independent geometry-based `gesture` output. A custom detector classifies hand boxes but does not produce the 21 landmarks; keep MediaPipe landmark tracking independent, or prepare a separately annotated 21-keypoint dataset and custom pose checkpoint if training hand pose later. Do not train on this model path during ordinary app startup.

## Limitations

- YOLO weights and the MediaPipe task model loaded successfully. Static image inference produced 21 landmarks and an `OPEN_HAND` result. Live combined webcam inference ran at approximately 15 FPS after warm-up on this MacBook Air; detection rates depend on model/device and camera resolution.
- The default ESP32 URL (`http://192.168.4.1:81/stream`) was not reachable in this session. The configured webcam fallback connected successfully, but the actual ESP32 stream still needs testing on the camera's network.
- Geometry-based gesture categories depend on camera view, occlusion, handedness, and orientation. `THUMBS_UP` includes a simple image-up check.
- Common objects are COCO classes; unsupported or custom objects need a separately trained checkpoint.
- Static image inference and camera inference load models lazily. The first run may download model artifacts and take longer.
