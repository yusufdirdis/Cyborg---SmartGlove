"""Assistant tests use fake providers; no network calls and no USB writes occur."""

import json
import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

from app import assistant
from app import hardware_control as control
from app import main as main_module
from app.main import app


def reply(**body):
    return json.dumps({"reply": "Okay.", "action": "none", **body})


@pytest.fixture
def fake(monkeypatch):
    """Route the 'gemini' provider to a canned response and give it a key."""
    state = {"text": reply()}
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", lambda system, messages: state["text"])
    assistant.clear_proposals()
    yield state
    assistant.clear_proposals()


def chat(client, message="hello", provider="gemini"):
    response = client.post("/api/assistant/chat", json={"provider": provider, "message": message, "history": []})
    assert response.status_code == 200
    return response.json()


def peace_sign_id(client):
    return next(m["id"] for m in client.get("/api/macros").json() if m["name"] == "Peace sign")


def test_pose_reply_only_proposes_until_confirmed(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        fake["text"] = reply(action="pose", positions=[10, 20, 30, 40, 50, 60], label="Test grip")
        body = chat(client, "make a test grip")
        proposal = body["proposal"]
        assert proposal["command"] == {"type": "pose", "positions": {"1": 10, "2": 20, "3": 30, "4": 40, "5": 50, "6": 60}, "label": "Test grip"}
        assert body["stopped"] is False
        after = client.get("/api/status").json()
        assert (after["positions"], after["target"], after["playing"]) == (before["positions"], before["target"], before["playing"])
        confirmed = client.post(f"/api/assistant/proposals/{proposal['id']}/confirm")
        assert confirmed.json() == {"accepted": True, "mode": "simulation"}
        assert client.get("/api/status").json()["target"] == proposal["command"]["positions"]
        assert client.post(f"/api/assistant/proposals/{proposal['id']}/confirm").status_code == 410


def test_macro_reply_needs_a_saved_macro(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        macro_id = peace_sign_id(client)
        fake["text"] = reply(action="macro", macro_id=macro_id)
        proposal = chat(client, "peace sign")["proposal"]
        assert proposal["command"] == {"type": "macro", "macro_id": macro_id, "name": "Peace sign"}
        assert client.post(f"/api/assistant/proposals/{proposal['id']}/confirm").json()["mode"] == "simulation"
        fake["text"] = reply(action="macro", macro_id="not-a-macro")
        assert chat(client)["proposal"] is None


@pytest.mark.parametrize("text", [
    "not json at all",
    reply(action="pose", positions=[0, 0, 0, 0, 0, 101]),
    reply(action="pose", positions=[0, 0, 0]),
    reply(action="jog", servo_id=1, delta=10),
    reply(action="position", servo_id=1, target=2048),
])
def test_invalid_or_hardware_replies_become_text_only(fake, text):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        fake["text"] = text
        body = chat(client)
        assert body["proposal"] is None and body["stopped"] is False
        assert body["reply"]


def test_provider_failure_degrades_to_text(fake, monkeypatch):
    def boom(system, messages):
        raise TimeoutError("slow")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", boom)
    with TestClient(app) as client:
        body = chat(client)
        assert body["proposal"] is None and "nothing was moved" in body["reply"].lower()


def test_provider_without_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with TestClient(app) as client:
        providers = {p["name"]: p for p in client.get("/api/assistant/providers").json()}
        assert providers["openrouter"]["available"] is False
        assert providers["offline"]["available"] is True
        assert providers["gemini"]["model"]
        body = chat(client, provider="openrouter")
        assert body["proposal"] is None
        assert client.post("/api/assistant/chat", json={"provider": "nope", "message": "hi"}).status_code == 404


def test_stop_reply_latches_and_clears_pending(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        fake["text"] = reply(action="pose", positions=[50] * 6, label="Half")
        pending = chat(client)["proposal"]
        fake["text"] = reply(action="stop")
        body = chat(client, "stop")
        assert body["stopped"] is True and body["hardware_stop"]["attempted"] is False
        assert client.get("/api/status").json()["stopped"] is True
        assert client.post(f"/api/assistant/proposals/{pending['id']}/confirm").status_code == 410
        fake["text"] = reply(action="pose", positions=[50] * 6, label="Half")
        assert chat(client)["proposal"] is None
        client.post("/api/control/reset")


def test_confirm_while_stopped_is_rejected(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        fake["text"] = reply(action="pose", positions=[50] * 6, label="Half")
        pending = chat(client)["proposal"]
        main_module.sim.stopped = True
        try:
            assert client.post(f"/api/assistant/proposals/{pending['id']}/confirm").status_code == 409
        finally:
            client.post("/api/control/reset")


def test_expired_and_cancelled_proposals(fake, monkeypatch):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        fake["text"] = reply(action="pose", positions=[5] * 6, label="Tiny")
        first, second = chat(client)["proposal"], chat(client)["proposal"]
        assert client.post(f"/api/assistant/proposals/{first['id']}/cancel").json() == {"cancelled": True}
        assert client.post(f"/api/assistant/proposals/{first['id']}/confirm").status_code == 410
        real = assistant.time.monotonic
        monkeypatch.setattr(assistant.time, "monotonic", lambda: real() + assistant.PROPOSAL_TTL_S + 1)
        assert client.post(f"/api/assistant/proposals/{second['id']}/confirm").status_code == 410
        assert client.post("/api/assistant/proposals/unknown/confirm").status_code == 404


def test_offline_provider_maps_phrases_without_network():
    assistant.clear_proposals()
    with TestClient(app) as client:
        client.post("/api/control/reset")
        assert chat(client, "Make a peace sign please", "offline")["proposal"]["command"]["name"] == "Peace sign"
        assert chat(client, "open your hand", "offline")["proposal"]["command"]["type"] in ("macro", "pose")
        assert chat(client, "what can you do?", "offline")["proposal"] is None
        assert chat(client, "STOP", "offline")["stopped"] is True
        client.post("/api/control/reset")


def test_assistant_never_reaches_hardware_moves(fake, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("assistant reached a hardware move")
    monkeypatch.setattr(control, "execute_jog", forbidden)
    monkeypatch.setattr(control, "execute_position", forbidden)
    monkeypatch.setattr(control.FeetechServoTransport, "write_position", forbidden)
    with TestClient(app) as client:
        client.post("/api/control/reset")
        for text in (reply(action="pose", positions=[30] * 6, label="Grip"), reply(action="macro", macro_id=peace_sign_id(client))):
            fake["text"] = text
            proposal = chat(client)["proposal"]
            assert client.post(f"/api/assistant/proposals/{proposal['id']}/confirm").status_code == 200
        fake["text"] = reply(action="stop")
        assert chat(client)["stopped"] is True
        client.post("/api/control/reset")


def test_stop_reply_sends_torque_off_when_writes_enabled(fake, monkeypatch):
    from test_hardware_control import FakeDriver

    FakeDriver.calls = []
    monkeypatch.setattr(control, "FeetechTuna", FakeDriver)
    monkeypatch.setattr(control, "_port", lambda: ("/dev/cu.fake", None))
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    with TestClient(app) as client:
        fake["text"] = reply(action="stop")
        body = chat(client, "stop")
        assert body["hardware_stop"]["attempted"] is True
        assert [c for c in FakeDriver.calls if c[0] == "write"] == [("write", sid, 40, 0) for sid in range(1, 7)]
        assert not any(c[0] == "move" for c in FakeDriver.calls)
        monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "0")
        client.post("/api/control/reset")
