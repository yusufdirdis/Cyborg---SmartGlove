"""Gemini Live tests replace the token SDK call; no network request is ever made."""

import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

from app import assistant
from app.main import app

KEY = "AIzaTestKey-1234567890abcdWXYZ"
TOKEN = "auth_tokens/ephemeral-test-token"


@pytest.fixture
def live(monkeypatch):
    """Fake the SDK token call and give the gemini provider a key."""
    calls = []

    def fake_sdk(key, config):
        calls.append((key, config))
        return TOKEN

    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(assistant, "_sdk_create_token", fake_sdk)
    assistant.clear_proposals()
    yield calls
    assistant.clear_proposals()


def session(client, headers=None):
    return client.post("/api/assistant/live/session", headers=headers or {})


def proposal(client, body):
    return client.post("/api/assistant/live/proposal", json=body)


def test_session_returns_token_instruction_and_tools_without_the_key(live):
    with TestClient(app) as client:
        response = session(client)
        assert response.status_code == 200
        body = response.json()
        assert body["token"] == TOKEN and body["expires_at"]
        assert body["model"] == assistant.live_model()
        assert KEY not in response.text
        assert live[0][0] == KEY
        assert live[0][1]["live_connect_constraints"]["model"] == assistant.live_model()
        instruction = body["system_instruction"]
        assert "Pinky finger" in instruction and "Peace sign" in instruction
        assert "0 = finger fully open/relaxed, 100 = fully flexed" in instruction
        assert "Confirm" in instruction and "stop_hand" in instruction
        names = {d["name"] for d in body["tools"][0]["functionDeclarations"]}
        assert names == {"propose_pose", "propose_macro", "propose_save_macro", "stop_hand"}


def test_live_model_env_override(live, monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_MODEL", "gemini-live-test")
    with TestClient(app) as client:
        body = session(client).json()
        assert body["model"] == "gemini-live-test"
        assert live[0][1]["live_connect_constraints"]["model"] == "gemini-live-test"


def test_token_config_locks_instruction_tools_and_transcriptions(live):
    with TestClient(app) as client:
        session(client)
    config = live[0][1]
    assert config["uses"] == 1
    constrained = config["live_connect_constraints"]["config"]
    assert "Pinky finger" in constrained["system_instruction"] and "Confirm" in constrained["system_instruction"]
    assert constrained["session_resumption"] == {} and constrained["response_modalities"] == ["AUDIO"]
    assert constrained["input_audio_transcription"] == {} and constrained["output_audio_transcription"] == {}
    names = {d["name"] for d in constrained["tools"][0]["function_declarations"]}
    assert names == {"propose_pose", "propose_macro", "propose_save_macro", "stop_hand"}


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_token_client_uses_the_pinned_api_version(monkeypatch):
    captured = {}

    class FakeAuthTokens:
        def create(self, config):
            return type("Token", (), {"name": TOKEN})()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.auth_tokens = FakeAuthTokens()

    import google.genai

    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setattr(google.genai, "Client", FakeClient)
    with TestClient(app) as client:
        assert session(client).status_code == 200
    assert assistant.LIVE_API_VERSION == "v1alpha"
    assert captured == {"api_key": KEY, "http_options": {"api_version": assistant.LIVE_API_VERSION}}


def test_live_tools_have_no_hardware_tool(live):
    declarations = {d["name"]: d for d in assistant.LIVE_TOOLS[0]["functionDeclarations"]}
    assert set(declarations) == {"propose_pose", "propose_macro", "propose_save_macro", "stop_hand"}
    pose = declarations["propose_pose"]["parameters"]
    assert pose["type"].upper() == "OBJECT" and pose["required"] == ["positions", "label"]
    positions = pose["properties"]["positions"]
    assert positions["type"].upper() == "ARRAY" and positions["items"]["type"].upper() == "INTEGER"
    assert declarations["propose_macro"]["parameters"]["required"] == ["macro_id"]
    assert declarations["stop_hand"].get("parameters") is None


def test_session_requires_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        response = session(client)
        assert response.status_code == 409 and "Settings" in response.json()["detail"]


def test_session_is_local_only(live):
    with TestClient(app) as client:
        assert session(client, {"Origin": "https://other.example"}).status_code == 403


def test_session_google_error_is_502_with_key_redacted(live, monkeypatch):
    def boom(key, config):
        raise RuntimeError(f"upstream said no for {KEY}")

    monkeypatch.setattr(assistant, "_sdk_create_token", boom)
    with TestClient(app) as client:
        response = session(client)
        assert response.status_code == 502
        assert "RuntimeError" in response.json()["detail"] and KEY not in response.json()["detail"]


def test_pose_proposal_never_moves(live):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        response = proposal(client, {"action": "pose", "positions": [10, 20, 30, 40, 50, 60], "label": "Test grip"})
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "gemini-live"
        assert body["command"] == {"type": "pose", "positions": {"1": 10, "2": 20, "3": 30, "4": 40, "5": 50, "6": 60}, "label": "Test grip"}
        after = client.get("/api/status").json()
        assert (after["positions"], after["target"], after["playing"]) == (before["positions"], before["target"], before["playing"])


def test_macro_proposal_uses_a_saved_macro(live):
    with TestClient(app) as client:
        macro_id = next(m["id"] for m in client.get("/api/macros").json() if m["name"] == "Peace sign")
        body = proposal(client, {"action": "macro", "macro_id": macro_id}).json()
        assert body["provider"] == "gemini-live"
        assert body["command"] == {"type": "macro", "macro_id": macro_id, "name": "Peace sign"}


@pytest.mark.parametrize("body,reason", [
    ({"action": "pose", "positions": [1, 2, 3], "label": "Short"}, "six values"),
    ({"action": "pose", "positions": [0, 0, 0, 0, 0, 101], "label": "Too far"}, "0 to 100"),
    ({"action": "macro", "macro_id": "not-a-macro"}, "couldn't find"),
], ids=["short-pose", "out-of-range", "unknown-macro"])
def test_invalid_proposals_are_422_and_do_not_move(live, body, reason):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        response = proposal(client, body)
        assert response.status_code == 422 and reason in response.json()["detail"]
        after = client.get("/api/status").json()
        assert (after["positions"], after["target"], after["playing"]) == (before["positions"], before["target"], before["playing"])


def test_proposal_while_stopped_is_409(live):
    with TestClient(app) as client:
        assert client.post("/api/control/stop").json()["stopped"] is True
        try:
            assert proposal(client, {"action": "pose", "positions": [1] * 6, "label": "Flat"}).status_code == 409
        finally:
            client.post("/api/control/reset")


def test_proposal_is_local_only(live):
    with TestClient(app) as client:
        headers = {"Origin": "https://other.example"}
        response = client.post("/api/assistant/live/proposal", json={"action": "pose", "positions": [1] * 6, "label": "Flat"}, headers=headers)
        assert response.status_code == 403


def test_confirm_moves_the_simulation(live):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        created = proposal(client, {"action": "pose", "positions": [5] * 6, "label": "Flat"}).json()
        assert client.post(f"/api/assistant/proposals/{created['id']}/confirm").json() == {"accepted": True, "mode": "simulation"}
        assert client.get("/api/status").json()["target"] == created["command"]["positions"]
        assert client.post(f"/api/assistant/proposals/{created['id']}/confirm").status_code == 410
