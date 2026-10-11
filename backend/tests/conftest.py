import os

# Never poll a real USB controller from the test suite; live-position tests call the reader directly.
os.environ.setdefault("HANDOFF_LIVE_POSITIONS", "0")
# Tests always use scratch SQLite, even when backend/.env points at Supabase (dotenv never overrides a set key).
os.environ["SUPABASE_DB_URL"] = ""
