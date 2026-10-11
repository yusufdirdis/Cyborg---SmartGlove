"""Cloud demo: each visitor's own AI key is used for that request only and never stored or echoed."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import assistant
from app.main import app

PASSCODE = "visitor-key-test-passcode"
KEY_A = "AIzaVisitorA-1234567890abcdAAAA"
KEY_B = "AIzaVisitorB-1234567890abcdBBBB"
SERVER_KEY = "AIzaServerKey-1234567890abcdSRVR"


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.setenv("HANDOFF_PASSCODE", PASSCODE)
    monkeypatch.setenv("GEMINI_API_KEY", SERVER_KEY)  # visitors must never fall back to this
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "visitor.sqlite3")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("HANDOS_ENV_FILE", str(env))
    return env


def headers(key=None, model=None):
    h = {"X-Handoff-Passcode": PASSCODE}
    if key:
        h["X-Gemini-Key"] = key
    if model:
        h["X-Gemini-Model"] = model
    return h


def gemini(providers):
    return next(p for p in providers if p["name"] == "gemini")


def test_provider_list_reflects_only_the_visitors_own_key(cloud):
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        assert gemini(client.get("/api/assistant/providers", headers=headers()).json())["available"] is False
        mine = gemini(client.get("/api/assistant/providers", headers=headers(KEY_A, "gemini-custom")).json())
        assert mine["available"] is True and mine["model"] == "gemini-custom"
        rows = {r["name"]: r for r in client.get("/api/settings/providers", headers=headers(KEY_A)).json()}
        assert rows["gemini"]["storage"] == "browser" and rows["gemini"]["key_set"] is True
        assert KEY_A not in client.get("/api/settings/providers", headers=headers(KEY_A)).text


def test_chat_uses_the_visitor_key_and_never_stores_or_echoes_it(cloud, monkeypatch):
    seen = []

    def complete(system, messages):
        seen.append(assistant._key("GEMINI_API_KEY"))
        return '{"reply": "Peace sign ready.", "action": "pose", "positions": [80, 80, 0, 0, 70, 50], "label": "Peace sign"}'
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", complete)
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        client.post("/api/control/reset", headers=headers())
        response = client.post("/api/assistant/chat", headers=headers(KEY_A), json={"provider": "gemini", "message": "peace sign"})
        assert response.status_code == 200 and response.json()["proposal"]
        assert KEY_A not in response.text
    assert seen == [KEY_A]
    assert "GEMINI_API_KEY" not in cloud.read_text(encoding="utf-8")
    assert os.environ["GEMINI_API_KEY"] == SERVER_KEY
    assert all(KEY_A not in str(row) for row in main_module.store.rows("ai_proposals"))


def test_no_visitor_key_means_no_key_even_if_the_server_has_one(cloud, monkeypatch):
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", lambda s, m: pytest.fail("server key must not be used"))
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        reply = client.post("/api/assistant/chat", headers=headers(), json={"provider": "gemini", "message": "peace sign"}).json()
        assert reply["proposal"] is None and "no API key" in reply["reply"]
        assert client.post("/api/settings/providers/gemini/test", headers=headers()).json()["message"] == "No API key entered."


def test_concurrent_visitors_never_see_each_others_keys(cloud, monkeypatch):
    barrier = threading.Barrier(2, timeout=5)

    def complete(system, messages):
        barrier.wait()  # both requests are inside a provider call at the same time
        time.sleep(0.05)
        return '{"reply": "%s", "action": "none"}' % assistant._key("GEMINI_API_KEY")[-4:]
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", complete)
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        def ask(key):
            return client.post("/api/assistant/chat", headers=headers(key), json={"provider": "gemini", "message": "hi"}).json()["reply"]
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.map(ask, [KEY_A, KEY_B])
    assert (a, b) == ("AAAA", "BBBB")


def test_provider_errors_hide_the_visitor_key(cloud, monkeypatch):
    def boom(system, messages):
        raise PermissionError(f"invalid key {KEY_A}")
    monkeypatch.setattr(assistant.PROVIDERS["gemini"], "complete", boom)
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        result = client.post("/api/settings/providers/gemini/test", headers=headers(KEY_A)).json()
    assert result["ok"] is False and KEY_A not in result["message"]


@pytest.mark.parametrize("bad", ["abc\nEVIL=1", "x" * 501, "key with spaces"])
def test_malformed_visitor_keys_are_rejected(cloud, bad):
    with TestClient(app, client=("203.0.113.9", 5000)) as client:
        response = client.get("/api/assistant/providers", headers={**headers(), "X-Gemini-Key": bad})
    assert response.status_code == 422


def test_local_mode_ignores_visitor_headers(monkeypatch):
    monkeypatch.delenv("HANDOFF_CLOUD", raising=False)
    monkeypatch.delenv("HANDOFF_PASSCODE", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", SERVER_KEY)
    with TestClient(app) as client:
        rows = {r["name"]: r for r in client.get("/api/settings/providers", headers={"X-Gemini-Key": KEY_A}).json()}
    assert rows["gemini"]["storage"] == "server" and rows["gemini"]["key_hint"] == "••••SRVR"
