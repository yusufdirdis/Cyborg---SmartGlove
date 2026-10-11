"""Online hardware bridge: laptop-side gates, the arm page, and the Railway relay."""

import json
import os
import threading
import time
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import bridge as bridge_module
from app.bridge import ArmState, Bridge, LocalAPI, arm_app, handle
from app.main import app
from app.relay import hub

TOKEN = "bridge-test-token-0123456789"
MOVE = "/api/hardware/position/execute"
MACRO_RUN = "/api/macros/0b9c2a3e-1111-4222-8333-944455556666/hardware/run"


class FakeLocal:
    def __init__(self):
        self.calls = []

    def __call__(self, method, path):
        self.calls.append((method, path))
        return 200, {"ok": True, "path": path}


# ---------- laptop: arm state and decisions ----------

def test_each_arm_creates_a_new_code_that_dies_on_disarm():
    state = ArmState()
    assert not state.armed() and state.code is None
    state.arm()
    first = state.code
    assert state.armed() and len(first) == 6 and not set(first) & set("0O1IL")
    assert state.check_code(first.lower()) is None  # case-insensitive
    state.disarm()
    assert state.code is None and state.check_code(first) is not None
    state.arm()
    assert state.code != first


def test_arm_lasts_thirty_minutes_and_extends():
    state = ArmState()
    state.arm()
    assert 29 * 60 < state.armed_until - time.time() <= 30 * 60
    until = state.armed_until
    assert state.extend() and state.armed_until >= until + 30 * 60 - 1
    state.armed_until = time.time() - 1
    assert not state.armed() and state.code is None and "ran out" in state.reason


def test_five_wrong_codes_disarm():
    state = ArmState()
    state.arm()
    for _ in range(4):
        assert state.check_code("WRONG1")
    assert state.armed()
    assert "re-arm" in state.check_code("WRONG1")
    assert not state.armed() and state.code is None


def test_moves_need_arm_and_the_right_code_but_status_and_stop_do_not():
    state, local = ArmState(), FakeLocal()
    assert handle(state, "POST", MOVE, "", local)[0] == 403  # not armed
    assert handle(state, "GET", "/api/hardware/status", "", local)[0] == 200
    state.arm()
    status, body = handle(state, "POST", MOVE, "NOPE00", local)
    assert status == 403 and body["operator_code_required"] is True
    assert handle(state, "POST", MOVE, state.code, local)[0] == 200
    assert handle(state, "POST", MACRO_RUN, state.code, local)[0] == 200  # whole macros are allowed
    assert handle(state, "POST", "/api/control/stop", "", local)[0] == 200
    assert not state.armed()  # Stop from the website disarms
    assert local.calls == [("GET", "/api/hardware/status"), ("POST", MOVE), ("POST", MACRO_RUN), ("POST", "/api/control/stop")]


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/hardware/jog/execute"),
    ("PUT", "/api/calibration/1"),
    ("PUT", "/api/settings/providers/gemini"),
    ("POST", "/api/macros"),
    ("DELETE", "/api/macros/abc"),
    ("POST", "/api/hardware/position/execute/../../calibration/1"),
])
def test_bridge_refuses_anything_off_its_allow_list(method, path):
    state, local = ArmState(), FakeLocal()
    state.arm()
    assert handle(state, method, path, state.code, local)[0] == 403
    assert local.calls == []


def test_bridge_only_talks_to_a_local_api():
    with pytest.raises(SystemExit):
        LocalAPI("https://api.givemeahand.fit")
    LocalAPI("http://127.0.0.1:8000")


# ---------- laptop: arm page ----------

@pytest.fixture
def page_bridge():
    b = Bridge("wss://example.invalid/ws/bridge", TOKEN, LocalAPI("http://127.0.0.1:9"))
    b.local_ok = True
    b.local_stop = lambda reason: b.state.disarm(reason)
    return b


def csrf_of(client):
    html = client.get("/").text
    return html.split('const CSRF = "')[1].split('"')[0]


def test_arm_page_arms_shows_code_and_stops(page_bridge):
    client = TestClient(arm_app(page_bridge))
    token = csrf_of(client)
    assert client.post("/arm").status_code == 403  # no CSRF token
    assert client.post("/arm", headers={"X-CSRF-Token": token}).status_code == 200
    state = client.get("/state").json()
    assert state["armed"] and state["code"] == page_bridge.state.code
    assert client.post("/arm", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/stop", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.get("/state").json()["armed"] is False


def test_arm_page_is_loopback_only(page_bridge):
    remote = TestClient(arm_app(page_bridge), client=("203.0.113.5", 4000))
    assert remote.get("/").status_code == 403 and remote.get("/state").status_code == 403
    rebound = TestClient(arm_app(page_bridge), base_url="http://evil.example")
    assert rebound.get("/state").status_code == 403  # DNS-rebinding style Host header


# ---------- Railway: relay ----------

@pytest.fixture
def cloud(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "relay.sqlite3")
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.delenv("HANDOFF_PASSCODE", raising=False)
    monkeypatch.setenv("HANDOFF_BRIDGE_TOKEN", TOKEN)
    yield
    hub.ws = None
    hub.pending.clear()


def remote():
    return TestClient(app, client=("203.0.113.9", 5000))


def hello(ws, token=TOKEN, armed=True):
    ws.send_text(json.dumps({"type": "hello", "token": token, "state": {"armed": armed, "armed_until": time.time() + 600, "session": "s1", "local_api": True}}))


def test_bridge_socket_needs_the_token_and_allows_one_bridge(cloud):
    with remote() as client:
        with client.websocket_connect("/ws/bridge") as ws:
            hello(ws, token="wrong")
            with pytest.raises(Exception):
                ws.receive_text()
        with client.websocket_connect("/ws/bridge") as first:
            hello(first)
            assert json.loads(first.receive_text())["type"] == "welcome"
            assert client.get("/api/status").json()["bridge"]["online"] is True
            with client.websocket_connect("/ws/bridge") as second:
                hello(second)
                with pytest.raises(Exception):
                    second.receive_text()


def test_bridge_socket_is_closed_without_cloud_token(monkeypatch):
    monkeypatch.setenv("HANDOFF_CLOUD", "1")
    monkeypatch.delenv("HANDOFF_BRIDGE_TOKEN", raising=False)
    with remote() as client, pytest.raises(Exception):
        with client.websocket_connect("/ws/bridge") as ws:
            ws.receive_text()


def test_offline_bridge_keeps_hardware_blocked(cloud):
    with remote() as client:
        response = client.post(MOVE, json={"token": "t"})
        assert response.status_code == 403 and "offline" in response.json()["detail"]
        assert client.get("/api/status").json()["bridge"]["online"] is False


def test_requests_are_relayed_with_the_operator_code_and_answered(cloud):
    with remote() as client, client.websocket_connect("/ws/bridge") as ws:
        hello(ws)
        ws.receive_text()  # welcome
        result = {}
        worker = threading.Thread(target=lambda: result.update(r=client.post(MOVE, json={"token": "abc"}, headers={"X-Handoff-Operator": "K7Q2MX"})))
        worker.start()
        request = json.loads(ws.receive_text())
        assert request == {"type": "request", "id": request["id"], "method": "POST", "path": MOVE, "body": {"token": "abc"}, "operator_code": "K7Q2MX"}
        ws.send_text(json.dumps({"type": "response", "id": request["id"], "status": 200, "body": {"sent": True}}))
        worker.join(10)
        assert result["r"].status_code == 200 and result["r"].json() == {"sent": True}


def test_non_allow_listed_routes_are_never_relayed(cloud):
    with remote() as client, client.websocket_connect("/ws/bridge") as ws:
        hello(ws)
        ws.receive_text()
        assert client.post("/api/hardware/jog/execute", json={"token": "x", "phrase": "x", "off_body": True, "power_cut_tested": True, "cord_path_clear": True}).status_code == 403
        cal = client.get("/api/calibration").json()[0]
        assert client.put("/api/calibration/1", json={k: v for k, v in cal.items() if k not in ("servo_id", "name", "revision")}).status_code == 403


def test_bridge_timeout_is_504(cloud, monkeypatch):
    monkeypatch.setattr("app.relay.REQUEST_TIMEOUT_S", 0.3)
    with remote() as client, client.websocket_connect("/ws/bridge") as ws:
        hello(ws)
        ws.receive_text()
        response = client.post(MOVE, json={"token": "abc"})
        assert response.status_code == 504
        json.loads(ws.receive_text())  # the request that was never answered


def test_website_stop_is_forwarded_to_the_bridge(cloud):
    with remote() as client, client.websocket_connect("/ws/bridge") as ws:
        hello(ws)
        ws.receive_text()
        assert client.post("/api/control/stop").status_code == 200
        forwarded = json.loads(ws.receive_text())
        assert (forwarded["method"], forwarded["path"]) == ("POST", "/api/control/stop")
        ws.send_text(json.dumps({"type": "response", "id": forwarded["id"], "status": 200, "body": {}}))
        client.post("/api/control/reset")
