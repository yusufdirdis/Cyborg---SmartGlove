"""HTTP API for local vision inference and its development-only viewer."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .service import vision_service


router = APIRouter()
DEV_UI_PATH = Path(__file__).with_name("dev_ui.html")
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _local_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            local_origin = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                            and parsed.port is not None and not parsed.path)
        except ValueError:
            local_origin = False
    else:
        local_origin = True
    if client_host not in {"127.0.0.1", "::1", "testclient"} or not local_origin:
        raise HTTPException(403, "Vision controls are available only from the local development interface.")


class VisionStartInput(BaseModel):
    source: Literal["esp32", "webcam"] | None = None
    camera_url: str | None = Field(default=None, min_length=8, max_length=500)
    webcam_index: int | None = Field(default=None, ge=0, le=20)
    fallback_to_webcam: bool | None = None
    object_detection_enabled: bool | None = None
    hand_tracking_enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.05, le=0.95)


@router.get("/api/vision/health", summary="Check local vision runtime and model configuration")
def vision_health():
    return vision_service.health()


@router.get("/api/vision/cameras", summary="List connected local camera inputs")
def vision_cameras(request: Request):
    _local_request(request)
    return vision_service.camera_devices()


@router.get("/api/vision/status", summary="Read camera and inference status")
def vision_status():
    return vision_service.status()


@router.get("/api/vision/detections", summary="Read the latest YOLO object detections")
def vision_detections():
    return vision_service.detections()


@router.get("/api/vision/hand", summary="Read the latest 21-point hand landmarks and finger estimates")
def vision_hand():
    return vision_service.hand()


@router.get("/api/vision/gesture", summary="Read the temporally smoothed hand gesture")
def vision_gesture():
    return vision_service.gesture()


@router.get("/api/vision/imitation", summary="Read normalized, simulation-only servo targets")
def vision_imitation():
    return vision_service.imitation()


@router.post("/api/vision/start", summary="Start local camera inference")
def vision_start(request: Request, payload: VisionStartInput | None = None):
    _local_request(request)
    options = payload.model_dump(exclude_none=True) if payload else {}
    if "camera_url" in options and not options["camera_url"].startswith(("http://", "https://")):
        raise HTTPException(422, "camera_url must be an http or https stream URL")
    try:
        return vision_service.start(options)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/vision/stop", summary="Stop local camera inference")
def vision_stop(request: Request):
    _local_request(request)
    return vision_service.stop()


@router.post("/api/vision/image", summary="Run object and hand inference on a static JPEG or PNG")
async def vision_static_image(request: Request):
    _local_request(request)
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"image/jpeg", "image/png"}:
        raise HTTPException(415, "Send a JPEG or PNG image as the raw request body.")
    content_length = int(request.headers.get("content-length") or 0)
    if content_length > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds the 12 MB development limit.")
    body = await request.body()
    if not body:
        raise HTTPException(422, "Image body is empty.")
    if len(body) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image exceeds the 12 MB development limit.")
    try:
        return vision_service.analyze_static_image(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, f"Local vision inference failed: {exc}") from exc


@router.get("/api/vision/stream.mjpg", summary="View raw or annotated live camera frames")
async def vision_video_stream(view: Literal["default", "object-detection"] = "default"):
    async def frames():
        last_frame = None
        while vision_service.status()["started"]:
            frame = vision_service.latest_jpeg(view)
            if frame is not None and frame is not last_frame:
                last_frame = frame
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            await asyncio.sleep(0.08)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/api/vision/frame.jpg", summary="Fetch one raw or annotated camera frame")
def vision_camera_frame(view: Literal["default", "object-detection"] = "default"):
    frame = vision_service.latest_jpeg(view)
    if not vision_service.status()["started"] or frame is None:
        raise HTTPException(503, "The camera has not produced a frame yet.")
    return Response(frame, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/vision/ui", response_class=HTMLResponse, include_in_schema=False)
def vision_development_ui():
    try:
        return DEV_UI_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, "Vision development interface is unavailable") from exc
