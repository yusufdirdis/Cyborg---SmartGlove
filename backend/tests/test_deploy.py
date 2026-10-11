"""Cloud deployment gates: passcodes, cloud-only routes, and the hardware boundary."""

import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import assistant, servo_status
from app.main import allowed_cors_origins, app

PASSCODE = "deployment-test-passcode-123"
REMOTE_CLIENT = ("203.0.113.17", 41000)


def remote_client():
    return TestClient(app, client=REMOTE_CLIENT)


def test_http_passcode_and_public_health(monkeypatch):
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    monkeypatch.delenv("HANDOFF_CLOUD", raising=False)
    with remote_client() as client:
        assert client.get("/api/health").status_code == 200
        denied = client.get("/api/macros", headers={"Origin": "http://localhost:5173"})
        assert denied.status_code == 401
        assert denied.json() == {"detail": "Passcode required"}
        assert denied.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert client.get("/api/macros", headers={"X-Handoff-Passcode": PASSCODE}).status_code == 200
        preflight = client.options("/api/macros", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-handoff-passcode",
        })
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_websocket_passcode_and_unset_local_behavior(monkeypatch):
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    monkeypatch.delenv("HANDOFF_CLOUD", raising=False)
    with remote_client() as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/ws/telemetry"):
                pass
        assert denied.value.code == 4401
        with client.websocket_connect(f"/ws/telemetry?passcode={PASSCODE}") as socket:
            assert socket.receive_json()["mode"] == "simulation"

    monkeypatch.delenv("HANDOFF_PASSCODE")
    with TestClient(app) as client:
        assert client.get("/api/macros").status_code == 200
        with client.websocket_connect("/ws/telemetry") as socket:
            assert socket.receive_json()["mode"] == "simulation"


def test_cloud_without_passcode_is_public_but_calibration_and_hardware_stay_local(monkeypatch, tmp_path):
    import app.main as main_module

    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "public.sqlite3")
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.delenv("HANDOFF_PASSCODE", raising=False)
    with remote_client() as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/macros").status_code == 200
        with client.websocket_connect("/ws/telemetry") as ws:
            assert "positions" in ws.receive_json()
        cal = client.get("/api/calibration").json()[0]
        data = {k: v for k, v in cal.items() if k not in ("servo_id", "name", "revision")}
        blocked = client.put("/api/calibration/1", json={**data, "verified": True})
        assert blocked.status_code == 403 and "local site" in blocked.json()["detail"]
        assert client.get("/api/calibration").json()[0]["verified"] is False
        hardware = client.post("/api/hardware/live", json={"moves": [{"servo_id": 1, "target": 2000, "speed": 100}]})
        assert hardware.status_code == 403
        assert client.post("/api/control/stop").status_code == 200
        client.post("/api/control/reset")


@pytest.mark.parametrize("method,path,body", [
    ("put", "/api/settings/providers/gemini", {"model": "gemini-test"}),
    ("delete", "/api/settings/providers/gemini/key", None),
])
def test_cloud_never_saves_provider_keys_on_the_server(monkeypatch, method, path, body):
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    with remote_client() as client:
        options = {"headers": {"X-Handoff-Passcode": PASSCODE}}
        if body is not None:
            options["json"] = body
        response = getattr(client, method)(path, **options)
    assert response.status_code == 403
    assert "kept in your browser" in response.json()["detail"]


def test_cloud_live_session_accepts_authenticated_remote_request(monkeypatch):
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    visitor_key = "AIzaVisitorKey-1234567890abcdWXYZ"
    used = []
    monkeypatch.setattr(assistant, "_sdk_create_token", lambda key, _config: used.append(key) or "auth_tokens/cloud-test")
    with remote_client() as client:
        response = client.post("/api/assistant/live/session", headers={"X-Handoff-Passcode": PASSCODE, "X-Gemini-Key": visitor_key})
    assert response.status_code == 200
    assert response.json()["token"] == "auth_tokens/cloud-test"
    assert used == [visitor_key] and visitor_key not in response.text


def test_cloud_hardware_status_never_scans_and_writes_stay_local(monkeypatch):
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    monkeypatch.delenv("HANDOFF_ENABLE_HARDWARE_WRITES", raising=False)
    monkeypatch.setattr(servo_status, "scan_servos", lambda: pytest.fail("Cloud must not scan USB hardware"))
    headers = {"X-Handoff-Passcode": PASSCODE}
    with remote_client() as client:
        status = client.get("/api/hardware/status", headers=headers)
        write = client.post("/api/hardware/live", json={"moves": [{"servo_id": 1, "target": 2000, "speed": 100}]}, headers=headers)
    assert status.status_code == 200
    assert status.json()["connected"] is False
    assert status.json()["error"] == "Hardware is only available on the local API."
    assert write.status_code == 403


def test_allowed_cors_origins_include_configured_origins_without_wildcard(monkeypatch):
    monkeypatch.setenv("HANDOFF_CORS_ORIGINS", "https://handoff.vercel.app, https://preview.vercel.app/ , *")
    assert allowed_cors_origins() == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "https://handoff.vercel.app",
        "https://preview.vercel.app",
    ]


@pytest.mark.parametrize("writes_flag", [None, "1"])
def test_cloud_forces_motor_writes_off_so_stop_and_chat_stay_public(monkeypatch, tmp_path, writes_flag):
    # main defaults local writes on; the hosted API must still treat writes as off for remote visitors.
    import app.main as main_module

    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "writes.sqlite3")
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.delenv("HANDOFF_PASSCODE", raising=False)
    if writes_flag is None:
        monkeypatch.delenv("HANDOFF_ENABLE_HARDWARE_WRITES", raising=False)
    else:
        monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", writes_flag)
    with remote_client() as client:
        assert client.get("/api/hardware/control").json()["writes_enabled"] is False
        assert client.post("/api/control/stop").status_code == 200
        assert client.post("/api/control/reset").status_code == 200
        chat = client.post("/api/assistant/chat", json={"provider": "offline", "message": "peace sign"})
        assert chat.status_code == 200 and chat.json()["proposal"]
        assert client.post("/api/macros/does-not-matter/hardware/run").status_code == 403
