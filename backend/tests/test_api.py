import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

from fastapi.testclient import TestClient
from app.main import app
from app.main import CalibrationInput
from app.hardware_boundary import ActuationApproval, gated_counts
import pytest


def pose(value=0):
    return {str(i): value for i in range(1, 7)}


def test_macro_round_trip_and_import_export():
    with TestClient(app) as client:
        payload = {"name": "Test wave", "description": "Two steps", "kind": "sequence", "steps": [{"positions": pose(20), "duration_ms": 200}, {"positions": pose(70), "duration_ms": 200}]}
        created = client.post("/api/macros", json=payload)
        assert created.status_code == 201
        macro_id = created.json()["id"]
        assert len(created.json()["steps"]) == 2
        payload["name"] = "Edited wave"
        assert client.put(f"/api/macros/{macro_id}", json=payload).json()["name"] == "Edited wave"
        exported = client.get("/api/macros/export").json()
        assert exported["version"] == 1
        assert any(m["name"] == "Edited wave" for m in exported["macros"])
        assert client.post("/api/macros/import", json={"version": 1, "macros": [payload]}).json() == {"imported": 1}
        assert client.delete(f"/api/macros/{macro_id}").status_code == 204


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
            return {56: 1000 + servo_id, 62: 124, 63: 35, 66: 0}[address]

        def closeSerialPort(self):
            calls.append(("close",))

    monkeypatch.setattr(servo_status, "_port", lambda: ("/dev/cu.test", None))
    monkeypatch.setattr(feetech_tuna, "FeetechTuna", Driver)
    with TestClient(app) as client:
        response = client.get("/api/hardware/status")
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert [servo["id"] for servo in response.json()["servos"]] == list(range(1, 7))
    assert [entry[1] for entry in calls if entry[0] == "ping"] == list(range(1, 7))
    assert calls[-1] == ("close",)
