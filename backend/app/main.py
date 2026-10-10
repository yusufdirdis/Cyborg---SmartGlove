"""handoff API. Physical status reads are separate from simulation control."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

SERVO_NAMES = {1: "Pinky finger", 2: "Ring finger", 3: "Middle finger", 4: "Index finger", 5: "Thumb flexion (up/down)", 6: "Thumb side-to-side"}
# The first release used index, middle, thumb flexion, thumb opposition, ring,
# pinky for IDs 1–6. Preserve each finger's pose when upgrading saved macros.
LEGACY_TO_CURRENT_ID = {1: 4, 2: 3, 3: 5, 4: 6, 5: 2, 6: 1}
DB_PATH = Path(os.environ.get("HANDOS_DB", Path(__file__).resolve().parents[1] / "handos.sqlite3"))


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
        Pose(positions=self.positions)
        return self


class MacroInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    kind: Literal["pose", "sequence"]
    steps: list[Step] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_kind(self):
        if self.kind == "pose" and len(self.steps) != 1:
            raise ValueError("Pose macros have exactly one step")
        return self


class CalibrationInput(BaseModel):
    open_count: int = Field(ge=0, le=4095)
    closed_count: int = Field(ge=0, le=4095)
    min_count: int = Field(ge=0, le=4095)
    max_count: int = Field(ge=0, le=4095)
    direction: Literal["increasing", "decreasing"]
    speed: int = Field(ge=1, le=100)
    verified: bool = False

    @model_validator(mode="after")
    def valid_limits(self):
        if self.min_count >= self.max_count or not (self.min_count <= self.open_count <= self.max_count and self.min_count <= self.closed_count <= self.max_count):
            raise ValueError("Open and closed must fall within the min/max limits")
        if abs(self.closed_count - self.open_count) < 10:
            raise ValueError("Open and closed must be at least 10 counts apart")
        if (self.closed_count > self.open_count) != (self.direction == "increasing"):
            raise ValueError("Direction must match the open and closed values")
        return self


class ImportPayload(BaseModel):
    version: Literal[1, 2]
    macros: list[MacroInput] = Field(max_length=100)


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS macros (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL, steps TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS calibration (servo_id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            for row in list(conn.execute("SELECT id, steps FROM macros")):
                steps = json.loads(row["steps"])
                for step in steps:
                    step["positions"] = remap_legacy_positions(step["positions"])
                conn.execute("UPDATE macros SET steps=? WHERE id=?", (json.dumps(steps), row["id"]))
            conn.execute("PRAGMA user_version = 1")
        if conn.execute("SELECT COUNT(*) FROM macros").fetchone()[0] == 0:
            samples = [
                ("Open hand", "All fingers relaxed", [0, 0, 0, 0, 0, 0]),
                ("Gentle fist", "A light, even curl", [75, 75, 75, 75, 75, 65]),
                ("Point", "Index extended, other fingers curled", [80, 80, 80, 0, 75, 55]),
                ("Pinch", "Thumb meets index", [0, 0, 0, 45, 55, 75]),
                ("Peace sign", "Index and middle extended", [80, 80, 0, 0, 70, 50]),
            ]
            for name, description, values in samples:
                save_macro(conn, MacroInput(name=name, description=description, kind="pose", steps=[Step(positions=dict(zip(SERVO_NAMES, values)), duration_ms=700)]))
        for sid in SERVO_NAMES:
            conn.execute("INSERT OR IGNORE INTO calibration VALUES (?,?)", (sid, json.dumps({"open_count": 2048, "closed_count": 2448, "min_count": 1800, "max_count": 2600, "direction": "increasing", "speed": 30, "verified": False})))


def save_macro(conn, macro: MacroInput, macro_id: str | None = None):
    now = time.time()
    macro_id = macro_id or str(uuid4())
    current = conn.execute("SELECT created_at FROM macros WHERE id=?", (macro_id,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO macros VALUES (?,?,?,?,?,?,?)", (macro_id, macro.name.strip(), macro.description, macro.kind, json.dumps([s.model_dump() for s in macro.steps]), current[0] if current else now, now))
    return macro_id


def macro_row(row):
    return {"id": row["id"], "name": row["name"], "description": row["description"], "kind": row["kind"], "steps": json.loads(row["steps"]), "created_at": row["created_at"], "updated_at": row["updated_at"]}


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
        self.listeners: set[WebSocket] = set()

    def snapshot(self):
        return {"mode": "simulation", "positions": self.positions, "target": self.target, "stopped": self.stopped, "playing": self.playing, "last_event": self.last_event, "vision": self.vision, "devices": {"controller": "Not connected", "glasses": "Not connected", "camera": "Browser preview only"}}

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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield
    if sim.task and not sim.task.done():
        sim.task.cancel()


app = FastAPI(title="handoff", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/status")
def status():
    return sim.snapshot()


@app.get("/api/servos")
def servos():
    return [{"id": sid, "name": name} for sid, name in SERVO_NAMES.items()]


@app.get("/api/hardware/status")
def hardware_status():
    from .servo_status import scan_servos

    return scan_servos()


@app.get("/api/macros")
def macros():
    with db() as conn:
        return [macro_row(r) for r in conn.execute("SELECT * FROM macros ORDER BY updated_at DESC")]


@app.post("/api/macros", status_code=201)
def create_macro(payload: MacroInput):
    with db() as conn:
        macro_id = save_macro(conn, payload)
        return macro_row(conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone())


@app.put("/api/macros/{macro_id}")
def update_macro(macro_id: str, payload: MacroInput):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM macros WHERE id=?", (macro_id,)).fetchone():
            raise HTTPException(404, "Macro not found")
        save_macro(conn, payload, macro_id)
        return macro_row(conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone())


@app.delete("/api/macros/{macro_id}", status_code=204)
def delete_macro(macro_id: str):
    with db() as conn:
        if conn.execute("DELETE FROM macros WHERE id=?", (macro_id,)).rowcount == 0:
            raise HTTPException(404, "Macro not found")


@app.get("/api/macros/export")
def export_macros():
    return {"version": 2, "macros": [{k: m[k] for k in ("name", "description", "kind", "steps")} for m in macros()]}


@app.post("/api/macros/import")
def import_macros(payload: ImportPayload):
    with db() as conn:
        for macro in payload.macros:
            data = macro.model_dump()
            if payload.version == 1:
                for step in data["steps"]:
                    step["positions"] = remap_legacy_positions(step["positions"])
            save_macro(conn, MacroInput.model_validate(data))
    return {"imported": len(payload.macros)}


@app.post("/api/macros/{macro_id}/play")
async def play_macro(macro_id: str):
    with db() as conn:
        row = conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Macro not found")
    if sim.stopped:
        raise HTTPException(409, "Software stop is latched")
    if sim.task and not sim.task.done():
        sim.task.cancel()
    steps = [Step.model_validate(s) for s in json.loads(row["steps"])]
    sim.task = asyncio.create_task(sim.play(steps))
    return {"accepted": True, "mode": "simulation"}


@app.post("/api/control/pose")
async def control_pose(pose: Pose):
    if sim.stopped:
        raise HTTPException(409, "Software stop is latched")
    if sim.task and not sim.task.done():
        sim.task.cancel()
    sim.task = asyncio.create_task(sim.play([Step(positions=pose.positions, duration_ms=350)]))
    return {"accepted": True, "mode": "simulation"}


@app.post("/api/control/stop")
async def stop():
    await sim.stop()
    return sim.snapshot()


@app.post("/api/control/reset")
async def reset():
    await sim.reset()
    return sim.snapshot()


@app.get("/api/calibration")
def calibration():
    with db() as conn:
        return [{"servo_id": r["servo_id"], "name": SERVO_NAMES[r["servo_id"]], **json.loads(r["data"])} for r in conn.execute("SELECT * FROM calibration ORDER BY servo_id")]


@app.put("/api/calibration/{servo_id}")
def update_calibration(servo_id: int, payload: CalibrationInput):
    if servo_id not in SERVO_NAMES:
        raise HTTPException(404, "Servo not found")
    with db() as conn:
        conn.execute("UPDATE calibration SET data=? WHERE servo_id=?", (payload.model_dump_json(), servo_id))
    return {"servo_id": servo_id, "name": SERVO_NAMES[servo_id], **payload.model_dump()}


class Detection(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    source: Literal["demo", "external"] = "demo"


@app.post("/api/vision/detection")
async def vision_detection(payload: Detection):
    sim.vision = {**payload.model_dump(), "received_at": time.time()}
    sim.last_event = "Detection event received; no macro was triggered"
    await sim.broadcast()
    return {"received": True, "action": "none"}


@app.websocket("/ws/telemetry")
async def telemetry(ws: WebSocket):
    await ws.accept()
    sim.listeners.add(ws)
    await ws.send_json(sim.snapshot())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        sim.listeners.discard(ws)
