"""Print backend/supabase/handoff_schema.sql for another schema, such as a test environment.

    python -m app.schema_sql --schema handoff_staging > staging.sql

The live site uses the file as is (schema `handoff`). The same least-privilege role,
handoff_app, gets access to the new schema too; nothing outside it is touched.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .store import SCHEMA_NAME

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "supabase" / "handoff_schema.sql"


def render(schema: str) -> str:
    if not SCHEMA_NAME.match(schema) or schema in ("public", "auth", "storage", "extensions"):
        raise ValueError(f"Not an allowed schema name: {schema!r}")
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    # Rename the schema everywhere (handoff., schema handoff, 'handoff'), never the handoff_app role.
    return re.sub(r"(?<![\w.])handoff(?!\w)", schema, sql)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", required=True)
    args = parser.parse_args(argv)
    sys.stdout.write(render(args.schema))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
