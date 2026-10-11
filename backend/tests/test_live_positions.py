"""Live position reads use a fake serial driver; nothing is written to a servo."""

import asyncio

from fastapi.testclient import TestClient

from app import live_positions as live
from app import main as main_module
from app.main import app
from app.servo_status import _scan_lock


class FakeDriver:
    calls = []
    positions = {1: 1000, 2: 1100, 3: 1200, 4: 1300, 5: 1400, 6: 1500}

    def __init__(self):
        self.porthandler = type("Port", (), {"is_open": False})()

    def openSerialPort(self, port, baudrate, servoFamily):
        self.calls.append(("open", port))
        self.porthandler.is_open = True
        return True

    def closeSerialPort(self):
        self.calls.append(("close",))
        self.porthandler.is_open = False

    def readReg(self, servo_id, address):
        self.calls.append(("read", servo_id, address))
        return self.positions.get(servo_id)

    def __getattr__(self, name):  # any write or move method would be a bug
        raise AssertionError(f"Live position reads must not call {name}")


def use_fake(monkeypatch, positions=None):
    FakeDriver.calls = []
    monkeypatch.setattr(FakeDriver, "positions", positions or FakeDriver.positions)
    monkeypatch.setattr(live, "FeetechTuna", FakeDriver)
    monkeypatch.setattr(live, "_port", lambda: ("/dev/cu.fake", None))


def test_reads_only_present_position_and_releases_port(monkeypatch):
    use_fake(monkeypatch)
    reading = live.read_positions()
    assert reading["connected"] is True
    assert reading["positions"] == FakeDriver.positions
    assert {call[2] for call in FakeDriver.calls if call[0] == "read"} == {56}
    assert FakeDriver.calls[-1] == ("close",)
    assert not _scan_lock.locked()


def test_no_controller_reports_disconnected_without_opening(monkeypatch):
    use_fake(monkeypatch)
    monkeypatch.setattr(live, "_port", lambda: (None, None))
    reading = live.read_positions()
    assert reading["connected"] is False
    assert set(reading["positions"].values()) == {None}
    assert FakeDriver.calls == []


def test_missing_servo_is_reported(monkeypatch):
    use_fake(monkeypatch, {1: 1000, 2: 1100, 3: 1200, 4: 1300, 5: 1400})
    reading = live.read_positions()
    assert reading["connected"] is False
    assert reading["error"] == "5 of 6 servos returned a position."


def test_busy_controller_skips_the_poll(monkeypatch):
    use_fake(monkeypatch)
    assert _scan_lock.acquire(blocking=False)
    try:
        assert live.read_positions() is None
    finally:
        _scan_lock.release()
    assert FakeDriver.calls == []


def test_physical_reading_reaches_snapshot_without_moving_simulation(monkeypatch):
    use_fake(monkeypatch)
    with TestClient(app) as client:
        before = client.get("/api/status").json()
        assert before["physical"]["connected"] is False
        asyncio.run(main_module.refresh_physical(live.read_positions))
        after = client.get("/api/status").json()
        assert after["physical"]["connected"] is True
        assert after["physical"]["positions"] == {str(k): v for k, v in FakeDriver.positions.items()}
        for key in ("positions", "target", "playing", "stopped"):
            assert after[key] == before[key]
    main_module.sim.physical = live.empty_reading()
