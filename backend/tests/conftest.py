import os

# Keep tests simulation-only unless an individual test opts into fake hardware writes.
os.environ["HANDOFF_ENABLE_HARDWARE_WRITES"] = "0"

# Never poll a real USB controller from the test suite; live-position tests call the reader directly.
os.environ.setdefault("HANDOFF_LIVE_POSITIONS", "0")
