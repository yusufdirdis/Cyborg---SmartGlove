"""Save-macro proposal tests use fake providers; no network and no USB writes occur."""

import json
import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

from app import assistant
from app import hardware_control as control
from app.main import app

SERVO_IDS = list(range(1, 7))


def positions(value):
    return {str(sid): value for sid in SERVO_IDS}


def api_step(value, duration_ms=500):
    return {"positions": positions(value), "duration_ms": duration_ms}


def step(value, duration_ms=500):
    return {"positions": [value] * 6, "duration_ms": duration_ms}


def save_text(name, steps, append_to=None):
    body = {"reply": "Ready to save.", "action": "save_macro", "name": name, "steps": steps}
    if append_to is not None:
        body["append_to_macro_id"] = append_to
    return json.dumps(body)


def command_steps(values, duration_ms=500):
    return [{"positions": positions(value), "duration_ms": duration_ms} for value in values]


@pytest.fixture
def fake(monkeypatch):
    """Route the 'gemini' provider to a canned response and give it a key."""
    state = {"text": json.dumps({"reply": "Okay.", "action": "none"})}
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", lambda system, messages: state["text"])
    assistant.clear_proposals()
    yield state
    assistant.clear_proposals()


def chat(client, message="save that"):
    response = client.post("/api/assistant/chat", json={"provider": "gemini", "message": message, "history": []})
    assert response.status_code == 200
    return response.json()


def live(client, body):
    return client.post("/api/assistant/live/proposal", json=body)


def unchanged(client, before):
    after = client.get("/api/status").json()
    assert (after["positions"], after["target"], after["playing"]) == (before["positions"], before["target"], before["playing"])


def test_chat_save_proposal_shape_and_no_motion(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        fake["text"] = save_text("Wave two step", [step(20, 300), step(80, 300)])
        proposal = chat(client)["proposal"]
        assert proposal["provider"] == "gemini"
        assert proposal["command"] == {"type": "save_macro", "name": "Wave two step",
                                       "steps": command_steps([20, 80], 300), "append_to": None}
        unchanged(client, before)


def test_live_save_proposal_shape_and_no_motion(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        response = live(client, {"action": "save_macro", "name": "Live wave", "steps": [step(30)]})
        assert response.status_code == 200
        proposal = response.json()
        assert proposal["provider"] == "gemini-live"
        assert proposal["command"] == {"type": "save_macro", "name": "Live wave",
                                       "steps": command_steps([30]), "append_to": None}
        unchanged(client, before)


def test_confirm_creates_pose_then_sequence(fake):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        created = live(client, {"action": "save_macro", "name": "One step save", "steps": [step(15)]}).json()
        response = client.post(f"/api/assistant/proposals/{created['id']}/confirm")
        assert response.status_code == 200
        body = response.json()
        assert body["saved"] is True and body["mode"] == "saved"
        assert body["macro"]["kind"] == "pose" and body["macro"]["name"] == "One step save"
        assert body["macro"]["description"] == "Saved from AI assistant"
        assert body["macro"]["steps"] == [api_step(15)]
        unchanged(client, before)
        assert client.post(f"/api/assistant/proposals/{created['id']}/confirm").status_code == 410
        client.delete(f"/api/macros/{body['macro']['id']}")

        created = live(client, {"action": "save_macro", "name": "Two step save", "steps": [step(10), step(70, 900)]}).json()
        body = client.post(f"/api/assistant/proposals/{created['id']}/confirm").json()
        assert body["macro"]["kind"] == "sequence"
        assert body["macro"]["steps"] == [api_step(10), api_step(70, 900)]
        client.delete(f"/api/macros/{body['macro']['id']}")


def test_append_converts_pose_to_sequence_and_keeps_name(fake):
    with TestClient(app) as client:
        base = client.post("/api/macros", json={"name": "Append base", "description": "original notes",
                                                "kind": "pose", "steps": [api_step(10)]}).json()
        created = live(client, {"action": "save_macro", "name": "ignored", "steps": [step(60, 400)],
                                "append_to_macro_id": base["id"]}).json()
        assert created["command"]["append_to"] == {"macro_id": base["id"], "name": "Append base"}
        body = client.post(f"/api/assistant/proposals/{created['id']}/confirm").json()
        assert body["macro"]["id"] == base["id"] and body["macro"]["name"] == "Append base"
        assert body["macro"]["description"] == "original notes" and body["macro"]["kind"] == "sequence"
        assert body["macro"]["steps"] == [api_step(10), api_step(60, 400)]
        client.delete(f"/api/macros/{base['id']}")


@pytest.mark.parametrize("payload,reason", [
    ({"name": "Too many", "steps": [step(10)] * 31}, "1 to 30"),
    ({"name": "Bad duration", "steps": [step(10, 50)]}, "100 to 10000"),
    ({"name": "Bad position", "steps": [{"positions": [0, 0, 0, 0, 0, 101], "duration_ms": 500}]}, "0 to 100"),
    ({"steps": [step(10)]}, "needs a name"),
    ({"name": "No target", "steps": [step(10)], "append_to_macro_id": "not-a-macro"}, "couldn't find"),
], ids=["too-many-steps", "bad-duration", "bad-position", "missing-name", "unknown-target"])
def test_invalid_saves_are_text_only(fake, payload, reason):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        fake["text"] = json.dumps({"reply": "Ready.", "action": "save_macro", **payload})
        body = chat(client)
        assert body["proposal"] is None and "nothing was saved" in body["reply"].lower()
        response = live(client, {"action": "save_macro", **payload})
        assert response.status_code == 422 and reason in response.json()["detail"]
        unchanged(client, before)


def test_save_is_allowed_while_stopped(fake):
    with TestClient(app) as client:
        assert client.post("/api/control/stop").json()["stopped"] is True
        try:
            fake["text"] = save_text("Stopped save", [step(25)])
            assert chat(client)["proposal"] is not None
            created = live(client, {"action": "save_macro", "name": "Stopped live save", "steps": [step(35)]})
            assert created.status_code == 200
            assert live(client, {"action": "pose", "positions": [50] * 6, "label": "Half"}).status_code == 409
            assert live(client, {"action": "macro", "macro_id": "anything"}).status_code == 409
            saved = client.post(f"/api/assistant/proposals/{created.json()['id']}/confirm").json()
            assert saved["saved"] is True
            client.delete(f"/api/macros/{saved['macro']['id']}")
        finally:
            client.post("/api/control/reset")


def test_confirm_rechecks_append_target(fake):
    with TestClient(app) as client:
        base = client.post("/api/macros", json={"name": "Vanish base", "description": "",
                                                "kind": "pose", "steps": [api_step(10)]}).json()
        created = live(client, {"action": "save_macro", "name": "x", "steps": [step(20)],
                                "append_to_macro_id": base["id"]}).json()
        assert client.delete(f"/api/macros/{base['id']}").status_code == 204
        response = client.post(f"/api/assistant/proposals/{created['id']}/confirm")
        assert response.status_code == 409 and "no longer exists" in response.json()["detail"]


def test_confirm_rechecks_thirty_step_cap(fake):
    with TestClient(app) as client:
        base = client.post("/api/macros", json={"name": "Full base", "description": "",
                                                "kind": "sequence", "steps": [api_step(10)] * 29}).json()
        created = live(client, {"action": "save_macro", "name": "x", "steps": [step(20)],
                                "append_to_macro_id": base["id"]}).json()
        updated = {"name": base["name"], "description": base["description"], "kind": base["kind"],
                   "steps": [api_step(10)] * 30}
        assert client.put(f"/api/macros/{base['id']}", json=updated).status_code == 200
        response = client.post(f"/api/assistant/proposals/{created['id']}/confirm")
        assert response.status_code == 409 and "30 steps" in response.json()["detail"]
        client.delete(f"/api/macros/{base['id']}")


def test_save_confirm_never_reaches_hardware(fake, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("save reached a hardware move")

    monkeypatch.setattr(control, "execute_jog", forbidden)
    monkeypatch.setattr(control, "execute_position", forbidden)
    monkeypatch.setattr(control.FeetechServoTransport, "write_position", forbidden)
    with TestClient(app) as client:
        client.post("/api/control/reset")
        base = client.post("/api/macros", json={"name": "Hw base", "description": "",
                                                "kind": "pose", "steps": [api_step(5)]}).json()
        created = live(client, {"action": "save_macro", "name": "Hw new", "steps": [step(30), step(60)]}).json()
        saved = client.post(f"/api/assistant/proposals/{created['id']}/confirm")
        assert saved.status_code == 200
        client.delete(f"/api/macros/{saved.json()['macro']['id']}")
        created = live(client, {"action": "save_macro", "name": "x", "steps": [step(45)],
                                "append_to_macro_id": base["id"]}).json()
        assert client.post(f"/api/assistant/proposals/{created['id']}/confirm").status_code == 200
        client.delete(f"/api/macros/{base['id']}")
