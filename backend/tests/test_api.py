import json
import os
import sqlite3
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

from fastapi.testclient import TestClient
from app import main as main_module
from app.main import app
from app.main import CalibrationInput
from app.hardware_boundary import ActuationApproval, gated_counts
import pytest


def pose(value=0):
    return {str(i): value for i in range(1, 7)}


def test_macro_round_trip():
    with TestClient(app) as client:
        payload = {"name": "Test wave", "description": "Two steps", "kind": "sequence", "steps": [{"positions": pose(1200), "duration_ms": 200}, {"positions": pose(3500), "duration_ms": 200}]}
        created = client.post("/api/macros", json=payload)
        assert created.status_code == 201
        macro_id = created.json()["id"]
        assert len(created.json()["steps"]) == 2
        payload["name"] = "Edited wave"
        assert client.put(f"/api/macros/{macro_id}", json=payload).json()["name"] == "Edited wave"
        assert any(m["name"] == "Edited wave" for m in client.get("/api/macros").json())
        assert client.get("/api/macros/export").status_code in (404, 405)
        assert client.post("/api/macros/import", json={"version": 3, "macros": [payload]}).status_code in (404, 405)
        assert client.delete(f"/api/macros/{macro_id}").status_code == 204


def test_user_servo_ids_keep_finger_meaning():
    with TestClient(app) as client:
        assert client.get("/api/servos").json() == [
            {"id": 1, "name": "Pinky finger"},
            {"id": 2, "name": "Ring finger"},
            {"id": 3, "name": "Middle finger"},
            {"id": 4, "name": "Index finger"},
            {"id": 5, "name": "Thumb flexion (up/down)"},
            {"id": 6, "name": "Thumb side-to-side"},
        ]


def test_existing_macros_are_remapped_once_on_database_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE macros (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL, steps TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        old_step = [{"positions": {str(i): i * 10 for i in range(1, 7)}, "duration_ms": 700}]
        conn.execute("INSERT INTO macros VALUES (?,?,?,?,?,?,?)", ("old", "Old gesture", "", "pose", json.dumps(old_step), 1.0, 1.0))
    monkeypatch.setattr(main_module, "DB_PATH", path)
    main_module.init_db()
    main_module.init_db()
    with main_module.db() as conn:
        stored = json.loads(conn.execute("SELECT steps FROM macros WHERE id='old'").fetchone()[0])
        assert stored[0]["positions"] == {"1": 2400, "2": 2000, "3": 800, "4": 400, "5": 1200, "6": 1600}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_macro_source_defaults_to_you_and_survives_edits():
    with TestClient(app) as client:
        payload = {"name": "Origin check", "description": "", "kind": "pose", "steps": [{"positions": pose(100), "duration_ms": 300}]}
        created = client.post("/api/macros", json=payload).json()
        assert created["source"] == "you"
        assistant = client.post("/api/macros", json={**payload, "name": "Origin check AI", "source": "assistant"}).json()
        assert assistant["source"] == "assistant"
        edited = client.put(f"/api/macros/{assistant['id']}", json={**payload, "name": "Origin check AI edited"}).json()
        assert edited["source"] == "assistant"
        assert client.post("/api/macros", json={**payload, "source": "robot"}).status_code == 422
        for macro_id in (created["id"], assistant["id"]):
            client.delete(f"/api/macros/{macro_id}")


def test_seeded_samples_are_from_the_assistant(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "seeded.sqlite3")
    main_module.init_db()
    with main_module.db() as conn:
        rows = conn.execute("SELECT name, source FROM macros").fetchall()
    assert len(rows) == 5 and {row["source"] for row in rows} == {"assistant"}


def test_existing_database_gains_source_column(tmp_path, monkeypatch):
    path = tmp_path / "v2.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE macros (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL, steps TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
        step = json.dumps([{"positions": pose(0), "duration_ms": 700}])
        conn.execute("INSERT INTO macros VALUES (?,?,?,?,?,?,?)", ("a", "Open hand", "All fingers relaxed", "pose", step, 1.0, 1.0))
        conn.execute("INSERT INTO macros VALUES (?,?,?,?,?,?,?)", ("b", "Wave", "Saved from AI assistant", "pose", step, 1.0, 1.0))
        conn.execute("INSERT INTO macros VALUES (?,?,?,?,?,?,?)", ("c", "Mine", "", "pose", step, 1.0, 1.0))
        conn.execute("PRAGMA user_version = 2")
    monkeypatch.setattr(main_module, "DB_PATH", path)
    main_module.init_db()
    main_module.init_db()
    with main_module.db() as conn:
        sources = {row["id"]: row["source"] for row in conn.execute("SELECT id, source FROM macros")}
        assert sources == {"a": "assistant", "b": "assistant", "c": "you"}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_fresh_sample_gestures_use_correct_finger_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "fresh.sqlite3")
    main_module.init_db()
    with main_module.db() as conn:
        point = json.loads(conn.execute("SELECT steps FROM macros WHERE name='Point'").fetchone()[0])[0]["positions"]
        pinch = json.loads(conn.execute("SELECT steps FROM macros WHERE name='Pinch'").fetchone()[0])[0]["positions"]
    assert point == {"1": 3200, "2": 3200, "3": 3200, "4": 0, "5": 3000, "6": 2200}
    assert pinch == {"1": 0, "2": 0, "3": 0, "4": 1800, "5": 2200, "6": 3000}


def test_stop_latches_and_reset_allows_simulation():
    with TestClient(app) as client:
        assert client.post("/api/control/stop").json()["stopped"] is True
        assert client.post("/api/control/pose", json={"positions": pose(50)}).status_code == 409
        assert client.post("/api/control/reset").json()["stopped"] is False
        assert client.post("/api/control/pose", json={"positions": pose(50)}).json()["mode"] == "simulation"


def test_calibration_gate_and_vision_never_moves():
    with TestClient(app) as client:
        bad = {"open_count": 300, "closed_count": 700, "min_count": 500, "max_count": 800, "direction": "increasing", "speed": 30, "verified": True}
        assert client.put("/api/calibration/1", json=bad).status_code == 422
        good = {**bad, "min_count": 250}
        assert client.put("/api/calibration/1", json=good).status_code == 200
        too_high = {**good, "max_count": 4001}
        assert client.put("/api/calibration/1", json=too_high).status_code == 422
        at_cap = {**good, "max_count": 4000}
        assert client.put("/api/calibration/1", json=at_cap).status_code == 200
        before = client.get("/api/status").json()["positions"]
        result = client.post("/api/vision/detection", json={"label": "bottle", "confidence": .9, "source": "demo"})
        assert result.json()["action"] == "none"
        assert client.get("/api/status").json()["positions"] == before


def test_telemetry_connects():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/telemetry") as ws:
            state = ws.receive_json()
            assert state["mode"] == "simulation"
            assert set(map(int, state["positions"])) == set(range(1, 7))


def test_future_hardware_gate_requires_every_condition():
    c = CalibrationInput(open_count=300, closed_count=700, min_count=250, max_count=800, direction="increasing", speed=20, verified=True)
    all_cal = {i: c for i in range(1, 7)}
    all_pose = {i: 50 for i in range(1, 7)}
    with pytest.raises(PermissionError):
        gated_counts(all_pose, all_cal, ActuationApproval(True, False), False)
    with pytest.raises(PermissionError):
        gated_counts(all_pose, all_cal, ActuationApproval(True, True), True)
    with pytest.raises(PermissionError):
        gated_counts(all_pose, {**all_cal, 6: c.model_copy(update={"verified": False})}, ActuationApproval(True, True), False)
    assert gated_counts(all_pose, all_cal, ActuationApproval(True, True), False)[1] == (500, 20)
    placeholders = c.model_copy(update={"open_count": 2048, "closed_count": 2448,
                                        "min_count": 0, "max_count": 4000})
    with pytest.raises(PermissionError, match="starter"):
        gated_counts(all_pose, {**all_cal, 1: placeholders}, ActuationApproval(True, True), False)


def test_read_only_hardware_status_uses_six_ids_and_never_writes(monkeypatch):
    import feetech_tuna
    from app import servo_status

    calls = []

    class Packet:
        def ping(self, servo_id):
            calls.append(("ping", servo_id))
            return 777, 0, 0

    class Driver:
        packetHandler = Packet()

        def openSerialPort(self, port, baudrate, servoFamily):
            calls.append(("open", port, baudrate, servoFamily))
            self.porthandler = type("Port", (), {"is_open": True})()
            return True

        def readReg(self, servo_id, address):
            calls.append(("read", servo_id, address))
            return {56: 1000 + servo_id, 62: 124, 63: 35, 66: 0, 33: 0, 40: 1}[address]

        def closeSerialPort(self):
            calls.append(("close",))

    monkeypatch.setattr(servo_status, "_port", lambda: ("/dev/cu.test", None))
    monkeypatch.setattr(feetech_tuna, "FeetechTuna", Driver)
    with TestClient(app) as client:
        response = client.get("/api/hardware/status")
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert [servo["id"] for servo in response.json()["servos"]] == list(range(1, 7))
    assert all(servo["mode"] == 0 and servo["torque_enabled"] is True for servo in response.json()["servos"])
    assert [entry[1] for entry in calls if entry[0] == "ping"] == list(range(1, 7))
    assert calls[-1] == ("close",)
