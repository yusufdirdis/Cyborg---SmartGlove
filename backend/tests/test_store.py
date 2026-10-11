"""Store tests: SQLite logs, revision conflicts, and no silent fallback when Supabase is down."""

import os
from pathlib import Path

os.environ["HANDOS_DB"] = str(Path(__file__).parent / "test.sqlite3")

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.hardware_control import HardwareControlError
from app.main import app
from app.store import PostgresStore, SQLiteStore, make_store


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "store.sqlite3")
    return main_module.store


def test_make_store_uses_postgres_only_when_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert isinstance(make_store(lambda: tmp_path / "x.sqlite3"), SQLiteStore)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@127.0.0.1:1/postgres")
    assert isinstance(make_store(lambda: tmp_path / "x.sqlite3"), PostgresStore)


def test_proposals_and_detections_are_logged(scratch_db, monkeypatch):
    monkeypatch.setenv("HANDOFF_INSTANCE", "railway")
    with TestClient(app) as client:
        client.post("/api/control/reset")
        chat = client.post("/api/assistant/chat", json={"provider": "offline", "message": "peace sign"}).json()
        proposal_id = chat["proposal"]["id"]
        assert client.post(f"/api/assistant/proposals/{proposal_id}/confirm").status_code == 200
        assert client.post("/api/vision/detection", json={"label": "cup", "confidence": 0.9}).json()["action"] == "none"
    [row] = scratch_db.rows("ai_proposals")
    assert (row["id"], row["provider"], row["status"], row["instance"]) == (proposal_id, "offline", "confirmed", "railway")
    assert row["resolved_at"] is not None
    [detection] = scratch_db.rows("vision_detections")
    assert (detection["label"], detection["source"]) == ("cup", "demo")


def test_cancelled_proposal_is_logged(scratch_db):
    with TestClient(app) as client:
        client.post("/api/control/reset")
        proposal_id = client.post("/api/assistant/chat", json={"provider": "offline", "message": "peace sign"}).json()["proposal"]["id"]
        client.post(f"/api/assistant/proposals/{proposal_id}/cancel")
    assert scratch_db.rows("ai_proposals")[0]["status"] == "cancelled"


def test_hardware_moves_and_estop_checks_are_logged(scratch_db):
    with TestClient(app):
        pass  # creates the tables
    confirm = main_module.JogConfirmInput(token="t", phrase="p", off_body=True, power_cut_tested=True, cord_path_clear=True)
    assert main_module.record_move("position", [3], {"token": "t"}, lambda: {"sent": True}, confirm) == {"sent": True}

    def refused():
        raise HardwareControlError("Servo 3 is moving.")
    with pytest.raises(main_module.HTTPException) as refused_exc:
        main_module.record_move("live", [3], {"moves": []}, refused)
    assert refused_exc.value.status_code == 409
    moves = sorted(scratch_db.rows("hardware_moves"), key=lambda r: r["ok"])
    assert [(m["kind"], m["ok"], m["error"]) for m in moves] == [("live", 0, "Servo 3 is moving."), ("position", 1, None)]
    [check] = scratch_db.rows("estop_checks")
    assert (check["power_cut_tested"], check["context"]) == (1, "position")


def test_log_failure_never_changes_a_response(scratch_db, monkeypatch):
    def broken(table, row):
        raise RuntimeError("disk full")
    with TestClient(app) as client:
        monkeypatch.setattr(main_module.store, "log", broken)
        assert client.post("/api/vision/detection", json={"label": "cup", "confidence": 0.5}).status_code == 200
        assert client.post("/api/control/stop").status_code == 200
        assert main_module.record_move("torque_enable", [1], {}, lambda: {"enabled": [1]}) == {"enabled": [1]}
        client.post("/api/control/reset")


def test_stale_revision_is_a_conflict(scratch_db):
    with TestClient(app) as client:
        macro = client.get("/api/macros").json()[0]
        body = {k: macro[k] for k in ("name", "description", "kind", "steps")}
        updated = client.put(f"/api/macros/{macro['id']}", json={**body, "name": "First edit", "revision": macro["revision"]})
        assert updated.status_code == 200 and updated.json()["revision"] == macro["revision"] + 1
        stale = client.put(f"/api/macros/{macro['id']}", json={**body, "name": "Second edit", "revision": macro["revision"]})
        assert stale.status_code == 409
        cal = client.get("/api/calibration").json()[0]
        data = {k: v for k, v in cal.items() if k not in ("servo_id", "name", "revision")}
        assert client.put("/api/calibration/1", json={**data, "revision": cal["revision"]}).json()["revision"] == cal["revision"] + 1
        assert client.put("/api/calibration/1", json={**data, "revision": cal["revision"]}).status_code == 409
        assert client.put("/api/calibration/1", json=data).status_code == 200  # no revision: last write wins


def test_unreachable_supabase_is_503_with_no_sqlite_fallback(scratch_db, monkeypatch):
    with TestClient(app) as client:
        before = len(client.get("/api/macros").json())
    pg = PostgresStore("postgresql://u:p@127.0.0.1:1/postgres")
    pg.pool.timeout = 1
    pg.pool.open(wait=False)
    monkeypatch.setattr(main_module, "store", pg)
    client = TestClient(app)  # no lifespan: the store is already chosen
    try:
        assert client.get("/api/macros").status_code == 503
        body = {"name": "Lost", "kind": "pose", "steps": [{"positions": {str(i): 0 for i in range(1, 7)}, "duration_ms": 500}]}
        assert client.post("/api/macros", json=body).status_code == 503
        assert client.post("/api/control/stop").status_code == 200
        assert client.post("/api/control/reset").status_code == 200
    finally:
        pg.pool.close()
    assert len(SQLiteStore(lambda: main_module.DB_PATH).list_macros()) == before


@pytest.mark.skipif(not os.environ.get("HANDOFF_TEST_PG_URL"), reason="set HANDOFF_TEST_PG_URL to test against a real handoff schema")
def test_postgres_store_round_trip():
    pg = PostgresStore(os.environ["HANDOFF_TEST_PG_URL"])
    pg.init_schema(lambda p: p)
    try:
        steps = [{"positions": {str(i): 10 for i in range(1, 7)}, "duration_ms": 500}]
        saved = pg.save_macro({"name": "pytest macro", "description": "", "kind": "pose", "steps": steps})
        again = pg.save_macro({**saved, "name": "pytest macro 2"}, saved["id"], saved["revision"])
        assert again["revision"] == saved["revision"] + 1 and pg.get_macro(saved["id"])["name"] == "pytest macro 2"
        assert pg.delete_macro(saved["id"]) and pg.get_macro(saved["id"]) is None
    finally:
        pg.pool.close()
