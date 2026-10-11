import os

# Never poll a real USB controller from the test suite; live-position tests call the reader directly.
os.environ.setdefault("HANDOFF_LIVE_POSITIONS", "0")
