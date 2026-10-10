"""Transcription tests use a fake Gemini call; no audio leaves the machine."""

import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

from app import assistant
from app.main import app

CLIP = b"\x1aE\xdf\xa3fake-webm-opus-bytes"


@pytest.fixture
def gemini(monkeypatch):
    calls = []

    def fake(audio, mime_type, vocabulary):
        calls.append((audio, mime_type, vocabulary))
        return "  make a peace sign \n"
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "transcribe", fake)
    return calls


def post(client, body=CLIP, mime="audio/webm;codecs=opus", headers=None):
    return client.post("/api/assistant/transcribe", content=body, headers={"Content-Type": mime, **(headers or {})})


def test_transcribes_with_vocabulary_and_never_moves(gemini):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        before = client.get("/api/status").json()
        response = post(client)
        assert response.status_code == 200
        assert response.json() == {"text": "make a peace sign", "provider": "gemini"}
        audio, mime, vocabulary = gemini[0]
        assert audio == CLIP and mime == "audio/webm"
        assert "Peace sign" in vocabulary and "Pinky finger" in vocabulary
        after = client.get("/api/status").json()
        assert (after["target"], after["playing"]) == (before["target"], before["playing"])


def test_requires_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        response = post(client)
        assert response.status_code == 409 and "Settings" in response.json()["detail"]


@pytest.mark.parametrize("body,mime,status", [
    (b"", "audio/webm", 422),
    (CLIP, "text/plain", 415),
    (CLIP, "application/json", 415),
    (b"x" * (assistant.MAX_AUDIO_BYTES + 1), "audio/webm", 413),
], ids=["empty", "text", "json", "too-large"])
def test_rejects_bad_clips(gemini, body, mime, status):
    with TestClient(app) as client:
        assert post(client, body, mime).status_code == status
        assert gemini == []


def test_provider_failure_is_502_without_key(gemini, monkeypatch):
    def boom(audio, mime_type, vocabulary):
        raise RuntimeError("upstream said no for key test-key")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "transcribe", boom)
    with TestClient(app) as client:
        response = post(client)
        assert response.status_code == 502
        assert "RuntimeError" in response.json()["detail"] and "test-key" not in response.json()["detail"]


def test_empty_transcript_is_reported(gemini, monkeypatch):
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "transcribe", lambda audio, mime_type, vocabulary: "   ")
    with TestClient(app) as client:
        assert post(client).json() == {"text": "", "provider": "gemini"}


def test_transcribe_rejects_remote_origin_when_writes_enabled(gemini, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    with TestClient(app) as client:
        assert post(client, headers={"Origin": "https://other.example"}).status_code == 403
