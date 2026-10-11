"""handoff API. Physical status reads are separate from simulation control."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import time
from uuid import uuid4 as _uuid4
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

from . import assistant
from .relay import BridgeUnavailable, bridge_token, hub as bridge_hub, relayed
from .store import RevisionConflict, StoreUnavailable, make_store
from .vision.api import router as vision_router


SERVO_NAMES = {1: "Pinky finger", 2: "Ring finger", 3: "Middle finger", 4: "Index finger", 5: "Thumb flexion (up/down)", 6: "Thumb side-to-side"}
MAX_SERVO_COUNT = 4000  # User-requested app cap; not a measured mechanical limit.
# The first release used index, middle, thumb flexion, thumb opposition, ring,
# pinky for IDs 1–6. Preserve each finger's pose when upgrading saved macros.
LEGACY_TO_CURRENT_ID = {1: 4, 2: 3, 3: 5, 4: 6, 5: 2, 6: 1}
DB_PATH = Path(os.environ.get("HANDOS_DB", Path(__file__).resolve().parents[1] / "handos.sqlite3"))


def percent_to_counts(positions: dict) -> dict[str, int]:
    return {str(sid): int(value) * 40 for sid, value in positions.items()}


def remap_legacy_positions(positions: dict) -> dict[int, int]:
    return {
        current_id: positions[old_id] if old_id in positions else positions[str(old_id)]
        for old_id, current_id in LEGACY_TO_CURRENT_ID.items()
    }


class Pose(BaseModel):
    positions: dict[int, int]

    @model_validator(mode="after")
    def all_servos(self):
        if set(self.positions) != set(SERVO_NAMES) or any(not 0 <= value <= 100 for value in self.positions.values()):
            raise ValueError("A pose needs six servo percentages from 0 to 100")
        return self


class Step(BaseModel):
    positions: dict[int, int]
    duration_ms: int = Field(ge=100, le=10000)

    @model_validator(mode="after")
    def valid_pose(self):
        if set(self.positions) != set(SERVO_NAMES) or any(not 0 <= value <= MAX_SERVO_COUNT for value in self.positions.values()):
            raise ValueError("A macro step needs six raw servo targets from 0 to 4000")
        return self


class MacroInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    kind: Literal["pose", "sequence"]
    source: Literal["you", "assistant"] = "you"
    steps: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_kind(self):
        if self.kind == "pose" and len(self.steps) != 1:
            raise ValueError("Pose macros have exactly one step")
        return self


class CalibrationInput(BaseModel):
    open_count: int = Field(ge=0, le=MAX_SERVO_COUNT)
    closed_count: int = Field(ge=0, le=MAX_SERVO_COUNT)
    min_count: int = Field(ge=0, le=MAX_SERVO_COUNT)
    max_count: int = Field(ge=0, le=MAX_SERVO_COUNT)
    direction: Literal["increasing", "decreasing"]
    speed: int = Field(ge=1, le=100)
    verified: bool = False
    jog_limits_verified: bool = False

    @model_validator(mode="after")
    def valid_limits(self):
        if self.min_count >= self.max_count or not (self.min_count <= self.open_count <= self.max_count and self.min_count <= self.closed_count <= self.max_count):
            raise ValueError("Open and closed must fall within the min/max limits")
        if abs(self.closed_count - self.open_count) < 10:
            raise ValueError("Open and closed must be at least 10 counts apart")
        if (self.closed_count > self.open_count) != (self.direction == "increasing"):
            raise ValueError("Direction must match the open and closed values")
        return self


class MacroUpdate(MacroInput):
    revision: int | None = None  # the revision the editor loaded; a mismatch is a 409


class CalibrationUpdate(CalibrationInput):
    revision: int | None = None


store = make_store(lambda: DB_PATH)
PLACEHOLDER_CALIBRATION = {"open_count": 2048, "closed_count": 2448, "min_count": 1800, "max_count": 2600, "direction": "increasing", "speed": 30, "verified": False, "jog_limits_verified": False}


def db():
    """The local SQLite connection (tests and the migration script use it directly)."""
    return store.connect()


SAMPLES = [
    ("Open hand", "All fingers relaxed", [0, 0, 0, 0, 0, 0]),
    ("Gentle fist", "A light, even curl", [75, 75, 75, 75, 75, 65]),
    ("Point", "Index extended, other fingers curled", [80, 80, 80, 0, 75, 55]),
    ("Pinch", "Thumb meets index", [0, 0, 0, 45, 55, 75]),
    ("Peace sign", "Index and middle extended", [80, 80, 0, 0, 70, 50]),
]
SAMPLE_MACROS = [(name, description) for name, description, _ in SAMPLES]


def label_source(name: str, description: str, source: str) -> str:
    """Data version 3: the five built-in samples and assistant saves are "From the assistant"."""
    return "assistant" if (name, description) in SAMPLE_MACROS or description == "Saved from AI assistant" else source


# (data version, kind, transform). Each store applies, once and in order, the ones it has not applied yet.
DATA_UPGRADES = [(1, "positions", lambda positions: remap_legacy_positions(positions)),
                 (2, "positions", percent_to_counts),
                 (3, "source", label_source)]


def init_db():
    store.init_schema(DATA_UPGRADES)
    if store.count_macros() == 0:
        for name, description, values in SAMPLES:
            save_macro(MacroInput(name=name, description=description, kind="pose", source="assistant", steps=[Step(positions=dict(zip(SERVO_NAMES, [v * 40 for v in values])), duration_ms=700)]))
    for sid in SERVO_NAMES:
        store.ensure_calibration(sid, PLACEHOLDER_CALIBRATION)


def save_macro(macro: MacroInput, macro_id: str | None = None, expected_revision: int | None = None) -> dict:
    # The store keeps an existing macro's source: editing never changes who made it.
    data = {"name": macro.name.strip(), "description": macro.description, "kind": macro.kind, "source": macro.source,
            "steps": [s.model_dump() for s in macro.steps]}
    return store.save_macro(data, macro_id, expected_revision)


def calibration_for(servo_ids: list[int]) -> dict:
    """Validated calibration for the given servos; missing ones are simply absent."""
    return {sid: CalibrationInput.model_validate(data) for sid, data in store.get_calibrations(servo_ids).items()}


_device_session: str | None = None


def record(table: str, **row) -> None:
    """Best-effort audit log. A failure is printed and never changes the response."""
    try:
        store.log(table, row)
    except Exception as exc:
        print(f"handoff: could not log {table}: {exc}")


def record_proposal(proposal_id: str, status: str) -> None:
    try:
        store.update_proposal(proposal_id, status)
    except Exception as exc:
        print(f"handoff: could not log proposal {proposal_id}: {exc}")


def record_move(kind: str, servo_ids: list[int], request: dict, run, confirm=None):
    """Runs one hardware call, then logs it (and its E-stop confirmation) whatever the outcome."""
    from .hardware_control import HardwareControlError

    try:
        result = run()
    except HardwareControlError as exc:
        _log_move(kind, servo_ids, request, confirm, ok=False, result=None, error=str(exc))
        raise HTTPException(409, str(exc)) from exc
    _log_move(kind, servo_ids, request, confirm, ok=True, result=result if isinstance(result, dict) else None, error=None)
    return result


def _log_move(kind, servo_ids, request, confirm, **outcome) -> None:
    if confirm is not None:
        record("estop_checks", session_id=_device_session, off_body=confirm.off_body, power_cut_tested=confirm.power_cut_tested,
               cord_path_clear=confirm.cord_path_clear, context=kind)
    record("hardware_moves", session_id=_device_session, kind=kind, servo_ids=list(servo_ids), request=request, **outcome)


class SimulationAdapter:
    """Transport boundary for a future hardware driver. This class cannot address hardware."""
    def __init__(self):
        self.positions = {sid: 0 for sid in SERVO_NAMES}
        self.target = dict(self.positions)
        self.stopped = False
        self.playing = False
        self.task: asyncio.Task | None = None
        self.last_event = "Simulation ready"
        self.vision = None
        # Read-only physical servo positions (raw counts) for the 3D hand.
        self.physical = {"connected": False, "positions": {sid: None for sid in SERVO_NAMES}, "read_at": None, "error": None}
        self.listeners: set[WebSocket] = set()

    def snapshot(self):
        return {"mode": "simulation", "positions": self.positions, "target": self.target, "stopped": self.stopped, "playing": self.playing, "last_event": self.last_event, "vision": self.vision, "physical": self.physical, "bridge": bridge_hub.public_status(), "devices": {"controller": "Not connected", "glasses": "Not connected", "camera": "Browser preview only"}}

    async def broadcast(self):
        message = self.snapshot()
        for ws in list(self.listeners):
            try:
                await ws.send_json(message)
            except Exception:
                self.listeners.discard(ws)

    async def stop(self):
        self.stopped = True
        self.playing = False
        self.target = dict(self.positions)
        self.last_event = "Software stop latched"
        if self.task and not self.task.done():
            self.task.cancel()
        await self.broadcast()

    async def reset(self):
        self.stopped = False
        self.last_event = "Software stop reset"
        await self.broadcast()

    async def move(self, positions: dict[int, int], duration_ms: int = 400):
        if self.stopped:
            raise HTTPException(409, "Software stop is latched")
        self.target = dict(positions)
        start = dict(self.positions)
        ticks = max(1, duration_ms // 50)
        for n in range(1, ticks + 1):
            if self.stopped:
                return
            self.positions = {sid: round(start[sid] + (positions[sid] - start[sid]) * n / ticks) for sid in SERVO_NAMES}
            await self.broadcast()
            await asyncio.sleep(duration_ms / ticks / 1000)
        self.positions = dict(positions)
        await self.broadcast()

    async def play(self, steps: list[Step]):
        try:
            self.playing = True
            self.last_event = "Playing simulated macro"
            await self.broadcast()
            for step in steps:
                await self.move(step.positions, step.duration_ms)
        except asyncio.CancelledError:
            pass
        finally:
            self.playing = False
            await self.broadcast()


sim = SimulationAdapter()


async def refresh_physical(read) -> dict | None:
    """Store one read-only position reading and broadcast it if anything but its timestamp changed."""
    reading = await asyncio.to_thread(read)
    if reading is None:  # controller busy with a status check or move
        return None
    changed = {k: v for k, v in reading.items() if k != "read_at"} != {k: v for k, v in sim.physical.items() if k != "read_at"}
    sim.physical = reading
    if changed:
        await sim.broadcast()
    return reading


async def poll_physical_positions():
    from .live_positions import IDLE_INTERVAL_S, POLL_INTERVAL_S, read_positions

    while True:
        try:
            reading = await refresh_physical(read_positions) if sim.listeners else None
        except Exception as exc:  # keep polling; a USB hiccup must not end live updates
            reading = None
            sim.physical = {**sim.physical, "connected": False, "error": f"Live position read failed: {exc}"}
        await asyncio.sleep(POLL_INTERVAL_S if reading and reading["connected"] else IDLE_INTERVAL_S)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .live_positions import enabled as live_positions_enabled
    from .vision.service import vision_service

    init_db()
    poller = asyncio.create_task(poll_physical_positions()) if live_positions_enabled() else None
    yield
    vision_service.stop()
    if poller:
        poller.cancel()
    if sim.task and not sim.task.done():
        sim.task.cancel()


async def _bridge_changed():
    """Hosted API: the 3D glove and Overview read the laptop's positions through the bridge (read-only)."""
    if cloud_mode():
        sim.physical = bridge_hub.physical or {"connected": False, "positions": {sid: None for sid in SERVO_NAMES},
                                               "read_at": None, "error": "The glove is offline (no bridge)." if not bridge_hub.online else None}
    await sim.broadcast()


bridge_hub.on_change = _bridge_changed
app = FastAPI(title="handoff", version="0.1.0", lifespan=lifespan)
@app.exception_handler(StoreUnavailable)
async def store_unavailable(_request: Request, exc: StoreUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(RevisionConflict)
async def revision_conflict(_request: Request, exc: RevisionConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


LOCAL_CORS_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


def cloud_mode() -> bool:
    return os.environ.get("HANDOFF_CLOUD", "").strip() == "1"


def allowed_cors_origins() -> list[str]:
    extras = [origin.strip().rstrip("/") for origin in os.environ.get("HANDOFF_CORS_ORIGINS", "").split(",")]
    return list(dict.fromkeys([*LOCAL_CORS_ORIGINS, *(origin for origin in extras if origin and origin != "*")]))


@app.middleware("http")
async def relay_hardware_to_bridge(request: Request, call_next):
    """Cloud mode: allow-listed hardware requests go to the laptop bridge, which owns every decision."""
    if not cloud_mode() or not relayed(request.method, request.url.path):
        return await call_next(request)
    if not bridge_hub.online:
        if request.method == "GET":
            return await call_next(request)  # status/control answer "no hardware" as before
        return JSONResponse(status_code=403, content={"detail": "The glove is offline. Ask the person at the glove to start the bridge."})
    body = None
    if request.method == "POST":
        raw = await request.body()
        if raw:
            try:
                body = json.loads(raw)
            except ValueError:
                return JSONResponse(status_code=422, content={"detail": "Send a JSON body."})
    try:
        status_code, payload = await bridge_hub.request(request.method, request.url.path, body,
                                                        request.headers.get("X-Handoff-Operator", "").strip())
    except BridgeUnavailable as exc:
        return JSONResponse(status_code=504, content={"detail": str(exc)})
    return JSONResponse(status_code=status_code, content=payload)


@app.middleware("http")
async def require_demo_passcode(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or path == "/api/health":
        return await call_next(request)
    expected = os.environ.get("HANDOFF_PASSCODE", "")  # optional: the public demo runs without one
    if not expected:
        return await call_next(request)
    supplied = request.headers.get("X-Handoff-Passcode", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse(status_code=401, content={"detail": "Passcode required"})
    request.state.handoff_passcode_authenticated = True
    return await call_next(request)


@app.middleware("http")
async def visitor_provider_keys(request: Request, call_next):
    """Cloud mode: AI routes use the keys this visitor sent, for this request only."""
    if not cloud_mode() or not request.url.path.startswith(("/api/assistant/", "/api/settings/")):
        return await call_next(request)
    values = {}
    for name, (key_header, model_header) in assistant.VISITOR_HEADERS.items():
        provider = assistant.PROVIDERS[name]
        for header, variable, limit in ((key_header, provider.key_env, 500), (model_header, provider.model_env, 200)):
            value = request.headers.get(header, "").strip()
            if not value:
                continue
            if len(value) > limit or not SETTING_VALUE.match(value):
                return JSONResponse(status_code=422, content={"detail": f"The {provider.label} key or model has unsupported characters."})
            values[variable] = value
    token = assistant.set_visitor_settings(values)
    try:
        return await call_next(request)
    finally:
        assistant.reset_visitor_settings(token)


# Keep CORS outside the passcode middleware so 401/503 responses still carry
# the headers browsers need to display the passcode gate instead of a fetch error.
app.add_middleware(CORSMiddleware, allow_origins=allowed_cors_origins(), allow_methods=["*"], allow_headers=["*"])
app.include_router(vision_router)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    return sim.snapshot()


@app.get("/api/servos")
def servos():
    return [{"id": sid, "name": name} for sid, name in SERVO_NAMES.items()]


@app.get("/api/hardware/status")
def hardware_status():
    if cloud_mode():
        return {"connected": False, "port": None, "servos": [], "error": "Hardware is only available on the local API.", "checked_at": time.time()}
    from .servo_status import scan_servos

    global _device_session
    result = scan_servos()
    if result.get("connected") and _device_session is None:
        _device_session = str(_uuid4())
        record("device_sessions", id=_device_session, port=result.get("port"), api_pid=os.getpid(), readings=result)
    return result


class JogPreviewInput(BaseModel):
    servo_id: int = Field(ge=1, le=6)
    delta: Literal[-10, 10]


class JogConfirmInput(BaseModel):
    token: str
    phrase: str
    off_body: bool
    power_cut_tested: bool
    cord_path_clear: bool


class PositionPreviewInput(BaseModel):
    servo_id: int = Field(ge=1, le=6)
    target: int = Field(ge=0, le=MAX_SERVO_COUNT)
    speed: int = Field(ge=50, le=2400)


class GroupPreviewInput(BaseModel):
    moves: list[PositionPreviewInput] = Field(min_length=2, max_length=6)


class LiveMoveInput(BaseModel):
    moves: list[PositionPreviewInput] = Field(min_length=1, max_length=6)


class TorqueEnableInput(BaseModel):
    servo_ids: list[int] = Field(min_length=1, max_length=6)


def _macro_fingerprint(row: dict) -> str:
    return json.dumps({"id": row["id"], "name": row["name"], "kind": row["kind"],
                       "steps": row["steps"]}, sort_keys=True, separators=(",", ":"))


def _local_hardware_request(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin")
    if client_host not in ("127.0.0.1", "::1", "testclient") or (
        origin and origin not in ("http://127.0.0.1:5173", "http://localhost:5173")
    ):
        raise HTTPException(403, "Physical controls are available only from the local website.")


@app.get("/api/hardware/control")
def hardware_control_status():
    from .hardware_control import POSITION_SPEED_DEFAULT, POSITION_SPEED_MAX, POSITION_SPEED_MIN, writes_enabled

    return {"writes_enabled": writes_enabled(), "step_counts": 10,
            "position_speed_min": POSITION_SPEED_MIN, "position_speed_max": POSITION_SPEED_MAX,
            "position_speed_default": POSITION_SPEED_DEFAULT}


@app.post("/api/hardware/jog/preview")
def preview_hardware_jog(payload: JogPreviewInput, request: Request):
    from .hardware_control import HardwareControlError, prepare_jog

    _local_hardware_request(request)
    calibration = calibration_for([payload.servo_id]).get(payload.servo_id)
    if calibration is None:
        raise HTTPException(404, "Servo calibration not found")
    try:
        return prepare_jog(payload.servo_id, payload.delta, calibration, sim.stopped)
    except HardwareControlError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/hardware/jog/execute")
def execute_hardware_jog(payload: JogConfirmInput, request: Request):
    from .hardware_control import HardwareControlError, execute_jog

    _local_hardware_request(request)
    # The token binds the servo and target; reload its calibration so a changed
    # record cannot be accepted between preview and execution.
    from .hardware_control import pending_servo_id

    servo_id = pending_servo_id(payload.token)
    if servo_id is None:
        raise HTTPException(409, "Move preview expired. Check the move again.")
    calibration = calibration_for([servo_id]).get(servo_id)
    if calibration is None:
        raise HTTPException(409, "Servo calibration is unavailable.")
    return record_move("jog", [servo_id], {"token": payload.token}, lambda: execute_jog(
        payload.token, payload.phrase, off_body=payload.off_body, power_cut_tested=payload.power_cut_tested,
        cord_path_clear=payload.cord_path_clear, calibration=calibration, stopped=sim.stopped,
        is_stopped=lambda: sim.stopped), payload)


@app.post("/api/hardware/position/preview")
def preview_hardware_position(payload: PositionPreviewInput, request: Request):
    from .hardware_control import HardwareControlError, prepare_position

    _local_hardware_request(request)
    calibration = calibration_for([payload.servo_id]).get(payload.servo_id)
    if calibration is None:
        raise HTTPException(404, "Servo calibration not found")
    try:
        return prepare_position(payload.servo_id, payload.target, payload.speed, calibration, sim.stopped)
    except HardwareControlError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/hardware/position/execute")
def execute_hardware_position(payload: JogConfirmInput, request: Request):
    from .hardware_control import HardwareControlError, execute_position, pending_position_servo_id

    _local_hardware_request(request)
    servo_id = pending_position_servo_id(payload.token)
    if servo_id is None:
        raise HTTPException(409, "Move preview expired. Check the move again.")
    calibration = calibration_for([servo_id]).get(servo_id)
    if calibration is None:
        raise HTTPException(409, "Servo calibration is unavailable.")
    return record_move("position", [servo_id], {"token": payload.token}, lambda: execute_position(
        payload.token, payload.phrase, off_body=payload.off_body, power_cut_tested=payload.power_cut_tested,
        cord_path_clear=payload.cord_path_clear, calibration=calibration, stopped=sim.stopped,
        is_stopped=lambda: sim.stopped), payload)


@app.post("/api/hardware/group/preview")
def preview_hardware_group(payload: GroupPreviewInput, request: Request):
    from .hardware_control import HardwareControlError, prepare_group

    _local_hardware_request(request)
    ids = [move.servo_id for move in payload.moves]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Each servo can appear only once in a group move.")
    calibrations = calibration_for(ids)
    if set(calibrations) != set(ids):
        raise HTTPException(409, "Calibration is unavailable for a selected servo.")
    try:
        return prepare_group([(move.servo_id, move.target, move.speed) for move in payload.moves],
                             calibrations, sim.stopped)
    except HardwareControlError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/hardware/group/execute")
def execute_hardware_group(payload: JogConfirmInput, request: Request):
    from .hardware_control import HardwareControlError, execute_group, pending_group_servo_ids

    _local_hardware_request(request)
    ids = pending_group_servo_ids(payload.token)
    if ids is None:
        raise HTTPException(409, "Move preview expired. Check the group move again.")
    calibrations = calibration_for(ids)
    if set(calibrations) != set(ids):
        raise HTTPException(409, "Calibration is unavailable for a selected servo.")
    return record_move("group", list(ids), {"token": payload.token}, lambda: execute_group(
        payload.token, payload.phrase, off_body=payload.off_body, power_cut_tested=payload.power_cut_tested,
        cord_path_clear=payload.cord_path_clear, calibrations=calibrations, stopped=sim.stopped,
        is_stopped=lambda: sim.stopped), payload)


@app.post("/api/hardware/live")
def move_hardware_live(payload: LiveMoveInput, request: Request):
    from .hardware_control import HardwareControlError, move_live

    _local_hardware_request(request)
    ids = [move.servo_id for move in payload.moves]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Each servo can appear only once in a live move.")
    return record_move("live", ids, payload.model_dump(), lambda: move_live(
        [(move.servo_id, move.target, move.speed) for move in payload.moves], sim.stopped, is_stopped=lambda: sim.stopped))


@app.post("/api/hardware/torque/enable")
def enable_hardware_torque(payload: TorqueEnableInput, request: Request):
    from .hardware_control import HardwareControlError, enable_torque

    _local_hardware_request(request)
    return record_move("torque_enable", payload.servo_ids, payload.model_dump(), lambda: enable_torque(payload.servo_ids, sim.stopped))


@app.get("/api/macros")
def macros():
    return store.list_macros()


@app.post("/api/macros", status_code=201)
def create_macro(payload: MacroInput):
    return save_macro(payload)


@app.put("/api/macros/{macro_id}")
def update_macro(macro_id: str, payload: MacroUpdate):
    if store.get_macro(macro_id) is None:
        raise HTTPException(404, "Macro not found")
    return save_macro(payload, macro_id, payload.revision)


@app.delete("/api/macros/{macro_id}", status_code=204)
def delete_macro(macro_id: str):
    if not store.delete_macro(macro_id):
        raise HTTPException(404, "Macro not found")


@app.post("/api/macros/{macro_id}/play")
async def play_macro(macro_id: str):
    row = store.get_macro(macro_id)
    if row is None:
        raise HTTPException(404, "Macro not found")
    if sim.stopped:
        raise HTTPException(409, "Software stop is latched")
    if sim.task and not sim.task.done():
        sim.task.cancel()
    steps = [Step.model_validate(s) for s in row["steps"]]
    simulated = [Step(positions={sid: round(count * 100 / MAX_SERVO_COUNT)
                                 for sid, count in step.positions.items()},
                      duration_ms=step.duration_ms) for step in steps]
    sim.task = asyncio.create_task(sim.play(simulated))
    return {"accepted": True, "mode": "simulation"}


@app.post("/api/macros/{macro_id}/hardware/run")
def run_hardware_macro(macro_id: str, request: Request):
    from .hardware_control import HardwareControlError, run_macro

    _local_hardware_request(request)
    row = store.get_macro(macro_id)
    if row is None:
        raise HTTPException(404, "Macro not found")
    steps = [Step.model_validate(item).model_dump() for item in row["steps"]]
    servo_ids = sorted({int(sid) for step in row["steps"] for sid in step["positions"]})
    return record_move("macro", servo_ids, {"macro_id": macro_id}, lambda: run_macro(
        row["name"], _macro_fingerprint(row), steps, stopped=sim.stopped, is_stopped=lambda: sim.stopped))


@app.post("/api/control/pose")
async def control_pose(pose: Pose):
    if sim.stopped:
        raise HTTPException(409, "Software stop is latched")
    if sim.task and not sim.task.done():
        sim.task.cancel()
    sim.task = asyncio.create_task(sim.play([Step(positions=pose.positions, duration_ms=350)]))
    return {"accepted": True, "mode": "simulation"}


async def latch_stop() -> dict:
    """The one full stop path: simulation latch, best-effort torque-off, no pending proposals."""
    from .hardware_control import disable_torque

    await sim.stop()
    assistant.clear_proposals()
    if cloud_mode() and bridge_hub.online:
        asyncio.create_task(bridge_hub.stop())  # the laptop stops and disarms too
    hardware_stop = await asyncio.to_thread(disable_torque)
    return {**sim.snapshot(), "hardware_stop": hardware_stop}


@app.post("/api/control/stop")
async def stop(request: Request):
    from .hardware_control import writes_enabled

    if writes_enabled():
        _local_hardware_request(request)
    return await latch_stop()


@app.post("/api/control/reset")
async def reset():
    await sim.reset()
    assistant.clear_proposals()
    return sim.snapshot()


@app.get("/api/calibration")
def calibration():
    return [{"servo_id": sid, "name": SERVO_NAMES[sid], **data} for sid, data in store.list_calibration().items()]


@app.put("/api/calibration/{servo_id}")
def update_calibration(servo_id: int, payload: CalibrationUpdate):
    if cloud_mode():
        # Calibration limits gate the real glove (the local API reads the same rows), so they are only edited next to it.
        raise HTTPException(403, "Calibration can only be changed from the local site next to the glove.")
    if servo_id not in SERVO_NAMES:
        raise HTTPException(404, "Servo not found")
    data = CalibrationInput.model_validate(payload.model_dump(exclude={"revision"})).model_dump()
    revision = store.put_calibration(servo_id, data, payload.revision)
    return {"servo_id": servo_id, "name": SERVO_NAMES[servo_id], **data, "revision": revision}


class Detection(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    source: Literal["demo", "external"] = "demo"


@app.post("/api/vision/detection")
async def vision_detection(payload: Detection):
    sim.vision = {**payload.model_dump(), "received_at": time.time()}
    record("vision_detections", label=payload.label, confidence=payload.confidence, source=payload.source)
    sim.last_event = "Detection event received; no macro was triggered"
    await sim.broadcast()
    return {"received": True, "action": "none"}


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(max_length=2000)


class ChatInput(BaseModel):
    provider: str
    message: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=10)


@app.get("/api/assistant/providers")
def assistant_providers():
    return assistant.provider_list()


@app.post("/api/assistant/chat")
async def assistant_chat(payload: ChatInput, request: Request):
    """Drafts proposals only. A stop request is the single action taken directly."""
    from .hardware_control import writes_enabled

    if writes_enabled():
        _local_hardware_request(request)
    if payload.provider not in assistant.PROVIDERS:
        raise HTTPException(404, "Unknown assistant provider")
    saved = [{"id": m["id"], "name": m["name"], "steps": len(m["steps"])} for m in macros()]
    reply, command = await assistant.ask(payload.provider, payload.message, [t.model_dump() for t in payload.history], SERVO_NAMES, saved)
    response = {"provider": payload.provider, "reply": reply, "proposal": None, "stopped": False}
    if command and command.type == "stop":
        response.update(stopped=True, hardware_stop=(await latch_stop())["hardware_stop"])
    elif command and sim.stopped and command.type != "save_macro":
        response["reply"] = f"{reply} Software stop is latched, so nothing was proposed. Reset first."
    elif command:
        response["proposal"] = assistant.create_proposal(command, payload.provider)
        record("ai_proposals", id=response["proposal"]["id"], provider=payload.provider, command=response["proposal"]["command"], status="proposed")
    return response


@app.post("/api/assistant/transcribe")
async def assistant_transcribe(request: Request):
    """Raw audio body in, transcript out. The text goes back to the chat box, never to motion."""
    from .hardware_control import writes_enabled

    if writes_enabled():
        _local_hardware_request(request)
    mime_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime_type not in assistant.AUDIO_TYPES:
        raise HTTPException(415, "Send a recorded audio clip (for example audio/webm).")
    if int(request.headers.get("content-length") or 0) > assistant.MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep voice commands short.")
    audio = await request.body()
    if len(audio) > assistant.MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep voice commands short.")
    if not audio:
        raise HTTPException(422, "The recording was empty.")
    saved = [{"id": m["id"], "name": m["name"]} for m in macros()]
    try:
        text = await assistant.transcribe(audio, mime_type, SERVO_NAMES, saved)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except assistant.TranscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"text": text, "provider": "gemini"}


class LiveSaveStep(BaseModel):
    positions: list[int]
    duration_ms: int


class LiveProposalInput(BaseModel):
    action: Literal["pose", "macro", "save_macro"]
    positions: list[int] | None = None
    label: str | None = Field(default=None, max_length=60)
    macro_id: str | None = None
    name: str | None = Field(default=None, max_length=60)
    steps: list[LiveSaveStep] | None = None
    append_to_macro_id: str | None = None


@app.post("/api/assistant/live/session")
async def assistant_live_session(request: Request):
    """Hands the browser a single-use ephemeral token. The prompt and tools stay server-side."""
    _local_or_cloud_live_request(request)
    saved = [{"id": m["id"], "name": m["name"]} for m in macros()]
    try:
        created = await asyncio.wait_for(asyncio.to_thread(assistant.create_live_token, SERVO_NAMES, saved), assistant.PROVIDER_TIMEOUT_S + 2)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except assistant.LiveTokenError as exc:
        raise HTTPException(502, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(502, "Gemini Live session timed out") from exc
    return {**created, "system_instruction": assistant.live_instruction(SERVO_NAMES, saved), "tools": assistant.LIVE_TOOLS}


@app.post("/api/assistant/live/proposal")
async def assistant_live_proposal(payload: LiveProposalInput, request: Request):
    """Turns a Live tool call into the same single-use proposal as chat. It never moves anything."""
    _local_or_cloud_live_request(request)
    if sim.stopped and payload.action != "save_macro":
        raise HTTPException(409, "Software stop is latched")
    saved = [{"id": m["id"], "name": m["name"], "steps": len(m["steps"])} for m in macros()]
    reply, command = assistant.parse_output(json.dumps({"reply": "", **payload.model_dump(exclude_none=True)}), list(SERVO_NAMES), saved)
    if command is None:
        raise HTTPException(422, reply)
    proposal = assistant.create_proposal(command, "gemini-live")
    record("ai_proposals", id=proposal["id"], provider="gemini-live", command=proposal["command"], status="proposed")
    return proposal


def save_macro_from_command(command: assistant.Command) -> dict:
    """Saves through the ordinary macro helpers. It never touches the simulation or hardware."""
    steps = [Step.model_validate(s) for s in command.steps or []]
    if command.append_to:
        row = store.get_macro(command.append_to["macro_id"])
        if row is None:
            raise HTTPException(409, "That macro no longer exists, so nothing was saved.")
        existing = [Step.model_validate(s) for s in row["steps"]]
        if len(existing) + len(steps) > 30:
            raise HTTPException(409, "That macro would pass 30 steps, so nothing was saved.")
        payload = MacroInput(name=row["name"], description=row["description"], kind="sequence", steps=[*existing, *steps])
        saved = save_macro(payload, row["id"], row["revision"])
    else:
        payload = MacroInput(name=command.name, description="Saved from AI assistant", kind="pose" if len(steps) == 1 else "sequence", source="assistant", steps=steps)
        saved = save_macro(payload)
    return {"saved": True, "mode": "saved", "macro": saved}


@app.post("/api/assistant/proposals/{proposal_id}/confirm")
async def confirm_proposal(proposal_id: str):
    try:
        command = assistant.claim_proposal(proposal_id)
    except KeyError as exc:
        raise HTTPException(404, "Proposal not found") from exc
    except assistant.ProposalGone as exc:
        record_proposal(proposal_id, "expired")
        raise HTTPException(410, str(exc)) from exc
    if command.type == "save_macro":
        result = save_macro_from_command(command)
        record_proposal(proposal_id, "saved")
        return result
    result = await (control_pose(Pose(positions=command.positions)) if command.type == "pose" else play_macro(command.macro_id))
    record_proposal(proposal_id, "confirmed")
    return result


@app.post("/api/assistant/proposals/{proposal_id}/cancel")
def cancel_proposal(proposal_id: str):
    try:
        assistant.cancel_proposal(proposal_id)
    except KeyError as exc:
        raise HTTPException(404, "Proposal not found") from exc
    record_proposal(proposal_id, "cancelled")
    return {"cancelled": True}


SETTINGS_LOCAL_ONLY = "Provider settings are available only from the local website."
SETTING_VALUE = re.compile(r"^[\w.\-~:/+=@]+$")


class ProviderSettingsInput(BaseModel):
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)

    @field_validator("api_key", "model")
    @classmethod
    def single_safe_token(cls, value, info):
        if value is None:
            return None
        value = value.strip()
        if info.field_name == "api_key" and not value:
            raise ValueError("Paste a key, or use Clear key to remove it")
        if value and not SETTING_VALUE.match(value):
            raise ValueError("Use a single value without spaces, quotes or line breaks")
        return value


def _local_settings_request(request: Request) -> None:
    """Settings write secrets, so only loopback clients and pages are accepted, on any dev port."""
    client_host = request.client.host if request.client else ""
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            local_origin = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port is not None and not parsed.path
        except ValueError:
            local_origin = False
    if client_host not in ("127.0.0.1", "::1", "testclient") or (origin and not local_origin):
        raise HTTPException(403, SETTINGS_LOCAL_ONLY)


def _provider_settings_request(request: Request) -> None:
    if cloud_mode():
        raise HTTPException(403, "Keys are kept in your browser in the demo. Nothing is saved on the server.")
    _local_settings_request(request)


def _provider_settings_read(request: Request) -> None:
    """Listing and testing work in the cloud too: they only use the visitor's own request headers."""
    if cloud_mode():
        return
    _local_settings_request(request)


def _local_or_cloud_live_request(request: Request) -> None:
    if cloud_mode():  # visitors use their own Gemini key; the passcode middleware (if set) already ran
        return
    _local_settings_request(request)


def _settings_provider(name: str) -> None:
    if name not in assistant.CONFIGURABLE:
        raise HTTPException(404, "Unknown configurable provider")


@app.get("/api/settings/providers")
def provider_settings(request: Request):
    _provider_settings_read(request)
    return assistant.settings_list()


@app.put("/api/settings/providers/{name}")
def update_provider_settings(name: str, payload: ProviderSettingsInput, request: Request):
    _provider_settings_request(request)
    _settings_provider(name)
    assistant.update_settings(name, payload.api_key, payload.model)
    return next(p for p in assistant.settings_list() if p["name"] == name)


@app.delete("/api/settings/providers/{name}/key")
def clear_provider_key(name: str, request: Request):
    _provider_settings_request(request)
    _settings_provider(name)
    assistant.clear_key(name)
    return next(p for p in assistant.settings_list() if p["name"] == name)


@app.post("/api/settings/providers/{name}/test")
async def test_provider_settings(name: str, request: Request):
    _provider_settings_read(request)
    _settings_provider(name)
    return await assistant.test_provider(name)


@app.websocket("/ws/bridge")
async def bridge_socket(ws: WebSocket):
    """The laptop bridge connects here (outbound from the laptop). Cloud mode with a bridge token only."""
    if not cloud_mode() or not bridge_token():
        await ws.close(code=4404)
        return
    await bridge_hub.serve(ws)


@app.websocket("/ws/telemetry")
async def telemetry(ws: WebSocket):
    expected = os.environ.get("HANDOFF_PASSCODE", "")
    supplied = ws.query_params.get("passcode", "")
    if expected and not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        await ws.close(code=4401)
        return
    await ws.accept()
    sim.listeners.add(ws)
    await ws.send_json(sim.snapshot())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        sim.listeners.discard(ws)
