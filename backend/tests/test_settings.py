"""Provider settings tests write a scratch .env; the real backend/.env is never touched."""

import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

from app import assistant
from app.main import app

KEY = "AIzaTestKey-1234567890abcdWXYZ"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("UNRELATED=keep-me\n", encoding="utf-8")
    monkeypatch.setenv("HANDOS_ENV_FILE", str(path))
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "HF_TOKEN", "HF_MODEL"):
        monkeypatch.setenv(name, "placeholder")  # Records the original so route writes are undone.
        monkeypatch.delenv(name)
    return path


def rows(client):
    return {p["name"]: p for p in client.get("/api/settings/providers").json()}


def test_save_masks_key_and_writes_env(env_file):
    with TestClient(app) as client:
        assert rows(client)["gemini"]["key_set"] is False
        saved = client.put("/api/settings/providers/gemini", json={"api_key": f"  {KEY} "})
        assert saved.status_code == 200
        listing = client.get("/api/settings/providers")
        assert KEY not in listing.text and KEY not in saved.text
        gemini = rows(client)["gemini"]
        assert gemini["key_set"] is True and gemini["key_hint"] == "••••WXYZ"
        text = env_file.read_text(encoding="utf-8")
        assert KEY in text and "UNRELATED=keep-me" in text
        assert os.environ["GEMINI_API_KEY"] == KEY
        providers = {p["name"]: p for p in client.get("/api/assistant/providers").json()}
        assert providers["gemini"]["available"] is True


def test_model_override_and_reset(env_file):
    with TestClient(app) as client:
        client.put("/api/settings/providers/openrouter", json={"model": "some/model-x"})
        assert rows(client)["openrouter"]["model"] == "some/model-x"
        assert assistant.PROVIDERS["openrouter"].model == "some/model-x"
        assert rows(client)["openrouter"]["key_set"] is False
        client.put("/api/settings/providers/openrouter", json={"model": ""})
        row = rows(client)["openrouter"]
        assert row["model"] == row["default_model"] == assistant.OpenRouterProvider.default_model
        assert "OPENROUTER_MODEL" not in env_file.read_text(encoding="utf-8")


def test_clear_key_makes_provider_unavailable(env_file):
    with TestClient(app) as client:
        client.put("/api/settings/providers/huggingface", json={"api_key": "hf_abcdefgh1234"})
        assert client.delete("/api/settings/providers/huggingface/key").status_code == 200
        assert rows(client)["huggingface"]["key_set"] is False
        assert "HF_TOKEN" not in env_file.read_text(encoding="utf-8")
        assert assistant.PROVIDERS["huggingface"].available() is False


@pytest.mark.parametrize("body", [
    {"api_key": "abc\nEVIL=1"},
    {"api_key": "abc\rEVIL=1"},
    {"model": "m\nEVIL=1"},
    {"api_key": "x" * 501},
    {"api_key": "   "},
])
def test_rejects_injection_and_bad_values(env_file, body):
    with TestClient(app) as client:
        assert client.put("/api/settings/providers/gemini", json=body).status_code == 422
        assert "EVIL" not in env_file.read_text(encoding="utf-8")


def test_settings_are_local_only(env_file):
    with TestClient(app) as client:
        other = {"Origin": "https://other.example"}
        assert client.get("/api/settings/providers", headers=other).status_code == 403
        assert client.put("/api/settings/providers/gemini", json={"api_key": KEY}, headers=other).status_code == 403
        assert client.delete("/api/settings/providers/gemini/key", headers=other).status_code == 403
        assert client.post("/api/settings/providers/gemini/test", headers=other).status_code == 403
        assert KEY not in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5174", "http://localhost:5175", "http://[::1]:5173", None])
def test_settings_accept_any_local_dev_port(env_file, origin):
    headers = {"Origin": origin} if origin else {}
    with TestClient(app) as client:
        assert client.put("/api/settings/providers/gemini", json={"api_key": KEY}, headers=headers).status_code == 200


@pytest.mark.parametrize("origin", ["https://127.0.0.1:5173.evil.example", "http://127.0.0.1.evil.example:5173", "http://192.168.1.5:5173", "null"])
def test_settings_reject_lookalike_origins(env_file, origin):
    with TestClient(app) as client:
        assert client.put("/api/settings/providers/gemini", json={"api_key": KEY}, headers={"Origin": origin}).status_code == 403


def test_unknown_provider_and_offline_are_not_configurable(env_file):
    with TestClient(app) as client:
        assert "offline" not in rows(client)
        for name in ("offline", "nope"):
            assert client.put(f"/api/settings/providers/{name}", json={"api_key": KEY}).status_code == 404
            assert client.post(f"/api/settings/providers/{name}/test").status_code == 404


def test_connection_test_reports_ok_and_failure(env_file, monkeypatch):
    with TestClient(app) as client:
        assert client.post("/api/settings/providers/gemini/test").json()["ok"] is False
        client.put("/api/settings/providers/gemini", json={"api_key": KEY})
        monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", lambda system, messages: '{"reply":"ok","action":"none"}')
        result = client.post("/api/settings/providers/gemini/test").json()
        assert result["ok"] is True and result["latency_ms"] >= 0

        def boom(system, messages):
            raise PermissionError(f"bad key {KEY}")
        monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", boom)
        failed = client.post("/api/settings/providers/gemini/test").json()
        assert failed["ok"] is False and "PermissionError" in failed["message"] and KEY not in failed["message"]
