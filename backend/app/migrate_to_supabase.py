"""Copy local SQLite macros and calibration into the Supabase `handoff` schema.

Run from backend/ with SUPABASE_DB_URL set (backend/.env works):

    python -m app.migrate_to_supabase            # refuses ids that already exist
    python -m app.migrate_to_supabase --overwrite

Macro UUIDs and timestamps are kept, calibration JSON is copied verbatim
(including verified: false), and the result is checked row by row. Re-running
after a successful copy changes nothing. Log tables are not migrated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import assistant  # noqa: F401  (loads backend/.env)
from .main import DATA_UPGRADES
from .store import PostgresStore, SQLiteStore


def _ts(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _normal(value) -> str:
    return json.dumps(value, sort_keys=True)


def migrate(sqlite_path: Path, url: str, overwrite: bool = False) -> dict:
    from psycopg.types.json import Jsonb

    source, target = SQLiteStore(lambda: sqlite_path), PostgresStore(url)
    try:
        # Bring both sides to the same data version (raw counts) before comparing or copying.
        source.init_schema(DATA_UPGRADES)
        target.init_schema(DATA_UPGRADES)
        return _copy(source, target, overwrite, Jsonb)
    finally:
        target.close()


def _copy(source, target, overwrite, Jsonb) -> dict:
    macros, calibration = source.list_macros(), source.list_calibration()
    existing = {str(r["id"]) for r in target._run("select id from handoff.macros where id = any(%s::uuid[])", ([m["id"] for m in macros],), fetch="all")}
    same = {m["id"] for m in macros if m["id"] in existing and (target.get_macro(m["id"]) or {}).get("steps") == m["steps"]}
    conflicts = existing - same
    if conflicts and not overwrite:
        raise SystemExit(f"{len(conflicts)} macro id(s) already exist in Supabase with different content. Re-run with --overwrite to replace them.")
    for m in macros:
        if m["id"] in same and not overwrite:
            continue
        target._run("""insert into handoff.macros (id, name, description, kind, steps, revision, created_at, updated_at)
                       values (%s::uuid,%s,%s,%s,%s,1,%s,%s)
                       on conflict (id) do update set name=excluded.name, description=excluded.description, kind=excluded.kind,
                         steps=excluded.steps, created_at=excluded.created_at, updated_at=excluded.updated_at""",
                    (m["id"], m["name"], m["description"], m["kind"], Jsonb(m["steps"]), _ts(m["created_at"]), _ts(m["updated_at"])))
    for servo_id, data in calibration.items():
        data = {k: v for k, v in data.items() if k != "revision"}
        target._run("""insert into handoff.calibration (servo_id, data) values (%s,%s)
                       on conflict (servo_id) do update set data=excluded.data where %s or handoff.calibration.data = excluded.data""",
                    (servo_id, Jsonb(data), overwrite))
    # Verify: every local macro and calibration row now matches Supabase exactly.
    mismatched = [m["id"] for m in macros if _normal((target.get_macro(m["id"]) or {}).get("steps")) != _normal(m["steps"])]
    remote_cal = target.list_calibration()
    cal_mismatch = [sid for sid, data in calibration.items()
                    if _normal({k: v for k, v in remote_cal.get(sid, {}).items() if k != "revision"}) != _normal({k: v for k, v in data.items() if k != "revision"})]
    if mismatched or cal_mismatch:
        raise SystemExit(f"Verification failed. Macros: {mismatched}. Calibration servos: {cal_mismatch} "
                         "(existing Supabase calibration differs; use --overwrite to replace it).")
    return {"macros": len(macros), "already_there": len(same), "calibration": len(calibration)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", type=Path, default=Path(os.environ.get("HANDOS_DB", Path(__file__).resolve().parents[1] / "handos.sqlite3")))
    parser.add_argument("--overwrite", action="store_true", help="replace Supabase rows that differ from SQLite")
    args = parser.parse_args(argv)
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        print("Set SUPABASE_DB_URL (backend/.env) first.", file=sys.stderr)
        return 2
    if not args.sqlite.exists():
        print(f"No SQLite database at {args.sqlite}.", file=sys.stderr)
        return 2
    result = migrate(args.sqlite, url, args.overwrite)
    print(f"Copied {result['macros']} macros ({result['already_there']} already matched) and {result['calibration']} calibration rows. Verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
