"""Persistence for macros, calibration and the audit logs.

SQLite is the default. Setting SUPABASE_DB_URL switches to the `handoff` schema in
Supabase Postgres, with no silent fallback: a database outage raises StoreUnavailable
(the API answers 503) and nothing is written to SQLite instead. Stop state never lives
here, so the software stop keeps working during an outage.

Log rows are best-effort audit records. They are written after the action they describe
and are never read back to trigger motion.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

LOG_TABLES = {
    "device_sessions": ("id", "port", "api_pid", "readings", "instance"),
    "estop_checks": ("session_id", "off_body", "power_cut_tested", "cord_path_clear", "context", "instance"),
    "hardware_moves": ("session_id", "kind", "servo_ids", "request", "ok", "result", "error", "instance"),
    "ai_proposals": ("id", "provider", "command", "status", "instance"),
    "vision_detections": ("label", "confidence", "source", "instance"),
}
JSON_COLUMNS = {"readings", "request", "result", "command", "servo_ids"}
PROPOSAL_STATUSES = {"proposed", "confirmed", "saved", "cancelled", "expired"}


# (data version, step-positions transform), applied in order and once per database.
Upgrades = list[tuple[int, Callable[[dict], dict]]]


def upgrade_steps(steps: list[dict], transform: Callable[[dict], dict]) -> list[dict]:
    return [{**step, "positions": {str(k): v for k, v in transform(step["positions"]).items()}} for step in steps]


SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def db_schema() -> str:
    """Postgres schema for this API: `handoff` (live site) or e.g. `handoff_staging` for a test environment."""
    name = os.environ.get("HANDOFF_DB_SCHEMA", "handoff").strip() or "handoff"
    if not SCHEMA_NAME.match(name) or name in ("public", "auth", "storage", "extensions"):
        raise ValueError(f"HANDOFF_DB_SCHEMA must be a lowercase identifier like handoff_staging, not {name!r}.")
    return name


class StoreUnavailable(Exception):
    """The configured database could not be reached. Never fall back to another store."""


class RevisionConflict(Exception):
    """Someone else saved this record first (for example the local and Railway APIs)."""


def instance() -> str:
    return os.environ.get("HANDOFF_INSTANCE", "local")[:40] or "local"


def _epoch(value: Any) -> float:
    return value.timestamp() if isinstance(value, datetime) else float(value)


def _log_row(table: str, row: dict) -> dict:
    if table not in LOG_TABLES:
        raise ValueError(f"Unknown log table {table}")
    unknown = set(row) - set(LOG_TABLES[table])
    if unknown:
        raise ValueError(f"Unknown columns for {table}: {sorted(unknown)}")
    return {"instance": instance(), **row}


class SQLiteStore:
    kind = "sqlite"

    def __init__(self, path: Callable[[], Path]):
        self.path = path  # read on every call so tests can point the app at a scratch file

    def connect(self):
        path = Path(self.path())
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self, upgrades: Upgrades) -> None:
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS macros (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, kind TEXT NOT NULL, steps TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS calibration (servo_id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for target, transform in upgrades:
                if version >= target:
                    continue
                for row in list(conn.execute("SELECT id, steps FROM macros")):
                    conn.execute("UPDATE macros SET steps=? WHERE id=?", (json.dumps(upgrade_steps(json.loads(row["steps"]), transform)), row["id"]))
                conn.execute(f"PRAGMA user_version = {int(target)}")
                version = target
            for table in ("macros", "calibration"):
                if "revision" not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            conn.execute("CREATE TABLE IF NOT EXISTS device_sessions (id TEXT PRIMARY KEY, started_at REAL NOT NULL, port TEXT, api_pid INTEGER, readings TEXT, instance TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS estop_checks (id TEXT PRIMARY KEY, session_id TEXT, checked_at REAL NOT NULL, off_body INTEGER NOT NULL, power_cut_tested INTEGER NOT NULL, cord_path_clear INTEGER NOT NULL, context TEXT NOT NULL, instance TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS hardware_moves (id TEXT PRIMARY KEY, session_id TEXT, at REAL NOT NULL, kind TEXT NOT NULL, servo_ids TEXT NOT NULL, request TEXT, ok INTEGER NOT NULL, result TEXT, error TEXT, instance TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS ai_proposals (id TEXT PRIMARY KEY, provider TEXT NOT NULL, command TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, resolved_at REAL, instance TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS vision_detections (id TEXT PRIMARY KEY, received_at REAL NOT NULL, label TEXT NOT NULL, confidence REAL NOT NULL, source TEXT NOT NULL, instance TEXT NOT NULL)")

    def count_macros(self) -> int:
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM macros").fetchone()[0]

    @staticmethod
    def _macro(row) -> dict:
        return {"id": row["id"], "name": row["name"], "description": row["description"], "kind": row["kind"], "steps": json.loads(row["steps"]), "created_at": row["created_at"], "updated_at": row["updated_at"], "revision": row["revision"] if "revision" in row.keys() else 1}

    def list_macros(self) -> list[dict]:
        with self.connect() as conn:
            return [self._macro(r) for r in conn.execute("SELECT * FROM macros ORDER BY updated_at DESC")]

    def get_macro(self, macro_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone()
        return self._macro(row) if row else None

    def save_macro(self, data: dict, macro_id: str | None = None, expected_revision: int | None = None) -> dict:
        now = time.time()
        with self.connect() as conn:
            current = conn.execute("SELECT created_at, revision FROM macros WHERE id=?", (macro_id,)).fetchone() if macro_id else None
            if current and expected_revision is not None and current["revision"] != expected_revision:
                raise RevisionConflict("This macro changed since you loaded it. Reload and try again.")
            macro_id = macro_id or str(uuid4())
            conn.execute("INSERT OR REPLACE INTO macros (id, name, description, kind, steps, created_at, updated_at, revision) VALUES (?,?,?,?,?,?,?,?)",
                         (macro_id, data["name"], data["description"], data["kind"], json.dumps(data["steps"]), current["created_at"] if current else now, now, current["revision"] + 1 if current else 1))
            return self._macro(conn.execute("SELECT * FROM macros WHERE id=?", (macro_id,)).fetchone())

    def delete_macro(self, macro_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM macros WHERE id=?", (macro_id,)).rowcount > 0

    def list_calibration(self) -> dict[int, dict]:
        with self.connect() as conn:
            return {r["servo_id"]: {**json.loads(r["data"]), "revision": r["revision"] if "revision" in r.keys() else 1} for r in conn.execute("SELECT * FROM calibration ORDER BY servo_id")}

    def get_calibrations(self, servo_ids: list[int]) -> dict[int, dict]:
        with self.connect() as conn:
            rows = conn.execute(f"SELECT servo_id, data FROM calibration WHERE servo_id IN ({','.join('?' for _ in servo_ids)})", servo_ids)
            return {r["servo_id"]: json.loads(r["data"]) for r in rows}

    def ensure_calibration(self, servo_id: int, data: dict) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO calibration (servo_id, data) VALUES (?,?)", (servo_id, json.dumps(data)))

    def put_calibration(self, servo_id: int, data: dict, expected_revision: int | None = None) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM calibration WHERE servo_id=?", (servo_id,)).fetchone()
            if row and expected_revision is not None and row["revision"] != expected_revision:
                raise RevisionConflict("This calibration changed since you loaded it. Reload and try again.")
            revision = row["revision"] + 1 if row else 1
            conn.execute("INSERT OR REPLACE INTO calibration (servo_id, data, revision) VALUES (?,?,?)", (servo_id, json.dumps(data), revision))
            return revision

    def log(self, table: str, row: dict) -> None:
        row = _log_row(table, row)
        stamp = {"device_sessions": "started_at", "estop_checks": "checked_at", "hardware_moves": "at", "ai_proposals": "created_at", "vision_detections": "received_at"}[table]
        values = {"id": str(uuid4()), **{k: json.dumps(v) if k in JSON_COLUMNS else v for k, v in row.items()}, stamp: time.time()}
        with self.connect() as conn:
            conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))

    def update_proposal(self, proposal_id: str, status: str) -> None:
        if status not in PROPOSAL_STATUSES:
            raise ValueError(status)
        with self.connect() as conn:
            conn.execute("UPDATE ai_proposals SET status=?, resolved_at=? WHERE id=?", (status, time.time(), proposal_id))

    def rows(self, table: str) -> list[dict]:
        """Test and migration helper; never used to drive the app."""
        if table not in LOG_TABLES:
            raise ValueError(table)
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


class PostgresStore:
    """The `handoff` schema in the shared Resume Fit Supabase project, through the session pooler."""
    kind = "postgres"

    def __init__(self, url: str, schema: str | None = None):
        from psycopg_pool import ConnectionPool

        self.schema = schema or db_schema()

        self.pool = ConnectionPool(url, min_size=0, max_size=5, timeout=8, open=False,
                                   kwargs={"autocommit": True, "connect_timeout": 8})
        self.logs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="handoff-log")  # logs never delay a response

    def _sql(self, sql: str) -> str:
        """Queries are written against `handoff.`; point them at this API's schema (validated identifier)."""
        return re.sub(r"(?<![\w.])handoff\.", f"{self.schema}.", sql)

    def _run(self, sql: str, params: tuple | list = (), fetch: str | None = None):
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import PoolTimeout

        try:
            with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute(self._sql(sql), params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return cur.rowcount
        except (psycopg.OperationalError, PoolTimeout) as exc:
            raise StoreUnavailable("Database unavailable. Check SUPABASE_DB_URL and that the Supabase project is not paused.") from exc

    def init_schema(self, upgrades: Upgrades) -> None:
        import psycopg
        from psycopg.types.json import Jsonb
        from psycopg_pool import PoolTimeout

        self.pool.open(wait=False)
        # The schema comes from backend/supabase/handoff_schema.sql, applied once by an admin.
        if self._run("select to_regclass('handoff.meta') as t", fetch="one")["t"] is None:
            raise StoreUnavailable(f"The {self.schema} schema is missing or outdated. Apply backend/supabase/handoff_schema.sql "
                                   f"(python -m app.schema_sql --schema {self.schema}) first.")
        try:
            # One transaction with the version row locked, so the local and Railway APIs never upgrade twice.
            with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                cur.execute(self._sql("select value from handoff.meta where key = 'data_version' for update"))
                version = int(cur.fetchone()[0])
                for target, transform in upgrades:
                    if version >= target:
                        continue
                    cur.execute(self._sql("select id, steps from handoff.macros"))
                    for macro_id, steps in cur.fetchall():
                        cur.execute(self._sql("update handoff.macros set steps = %s where id = %s"), (Jsonb(upgrade_steps(steps, transform)), macro_id))
                    version = target
                cur.execute(self._sql("update handoff.meta set value = %s where key = 'data_version'"), (str(version),))
        except (psycopg.OperationalError, PoolTimeout) as exc:
            raise StoreUnavailable("Database unavailable. Check SUPABASE_DB_URL and that the Supabase project is not paused.") from exc

    def count_macros(self) -> int:
        return self._run("select count(*) as n from handoff.macros", fetch="one")["n"]

    @staticmethod
    def _macro(row) -> dict:
        return {"id": str(row["id"]), "name": row["name"], "description": row["description"], "kind": row["kind"], "steps": row["steps"], "created_at": _epoch(row["created_at"]), "updated_at": _epoch(row["updated_at"]), "revision": row["revision"]}

    def list_macros(self) -> list[dict]:
        return [self._macro(r) for r in self._run("select * from handoff.macros order by updated_at desc", fetch="all")]

    def get_macro(self, macro_id: str) -> dict | None:
        try:
            row = self._run("select * from handoff.macros where id = %s::uuid", (macro_id,), fetch="one")
        except StoreUnavailable:
            raise
        except Exception:  # not a uuid
            return None
        return self._macro(row) if row else None

    def save_macro(self, data: dict, macro_id: str | None = None, expected_revision: int | None = None) -> dict:
        from psycopg.types.json import Jsonb

        if macro_id is None:
            row = self._run("insert into handoff.macros (name, description, kind, steps) values (%s,%s,%s,%s) returning *",
                            (data["name"], data["description"], data["kind"], Jsonb(data["steps"])), fetch="one")
            return self._macro(row)
        row = self._run("""insert into handoff.macros as m (id, name, description, kind, steps) values (%s::uuid,%s,%s,%s,%s)
                           on conflict (id) do update set name=excluded.name, description=excluded.description, kind=excluded.kind,
                             steps=excluded.steps, revision=m.revision + 1, updated_at=now()
                           where %s::int is null or m.revision = %s::int
                           returning *""",
                        (macro_id, data["name"], data["description"], data["kind"], Jsonb(data["steps"]), expected_revision, expected_revision), fetch="one")
        if row is None:
            raise RevisionConflict("This macro changed since you loaded it. Reload and try again.")
        return self._macro(row)

    def delete_macro(self, macro_id: str) -> bool:
        if self.get_macro(macro_id) is None:
            return False
        return self._run("delete from handoff.macros where id = %s::uuid", (macro_id,)) > 0

    def list_calibration(self) -> dict[int, dict]:
        return {r["servo_id"]: {**r["data"], "revision": r["revision"]} for r in self._run("select * from handoff.calibration order by servo_id", fetch="all")}

    def get_calibrations(self, servo_ids: list[int]) -> dict[int, dict]:
        return {r["servo_id"]: r["data"] for r in self._run("select servo_id, data from handoff.calibration where servo_id = any(%s)", (servo_ids,), fetch="all")}

    def ensure_calibration(self, servo_id: int, data: dict) -> None:
        from psycopg.types.json import Jsonb

        self._run("insert into handoff.calibration (servo_id, data) values (%s,%s) on conflict (servo_id) do nothing", (servo_id, Jsonb(data)))

    def put_calibration(self, servo_id: int, data: dict, expected_revision: int | None = None) -> int:
        from psycopg.types.json import Jsonb

        row = self._run("""insert into handoff.calibration as c (servo_id, data) values (%s,%s)
                           on conflict (servo_id) do update set data=excluded.data, revision=c.revision + 1, updated_at=now()
                           where %s::int is null or c.revision = %s::int
                           returning revision""", (servo_id, Jsonb(data), expected_revision, expected_revision), fetch="one")
        if row is None:
            raise RevisionConflict("This calibration changed since you loaded it. Reload and try again.")
        return row["revision"]

    def _log_now(self, table: str, row: dict) -> None:
        from psycopg.types.json import Jsonb

        values = {k: Jsonb(v) if k in JSON_COLUMNS else v for k, v in row.items()}
        self._run(f"insert into handoff.{table} ({','.join(values)}) values ({','.join('%s' for _ in values)})", list(values.values()))

    def log(self, table: str, row: dict) -> None:
        row = _log_row(table, row)
        future = self.logs.submit(self._log_now, table, row)
        future.add_done_callback(lambda f: f.exception() and print(f"handoff: log to {table} failed: {f.exception()}"))

    def update_proposal(self, proposal_id: str, status: str) -> None:
        if status not in PROPOSAL_STATUSES:
            raise ValueError(status)
        self.logs.submit(self._run, "update handoff.ai_proposals set status=%s, resolved_at=now() where id=%s", (status, proposal_id))

    def rows(self, table: str) -> list[dict]:
        if table not in LOG_TABLES:
            raise ValueError(table)
        return self._run(f"select * from handoff.{table}", fetch="all")

    def close(self) -> None:
        self.logs.shutdown(wait=True)
        self.pool.close()


def make_store(sqlite_path: Callable[[], Path]):
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    return PostgresStore(url) if url else SQLiteStore(sqlite_path)
