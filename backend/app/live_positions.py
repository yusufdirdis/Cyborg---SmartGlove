"""Read-only live servo positions for the 3D hand.

This module only reads the present-position register. It never enables torque
or sends a target, and it skips a poll whenever another status check or move
holds the controller.
"""

from __future__ import annotations

import contextlib
import io
import os
import time

from feetech_tuna import FeetechTuna

from .servo_status import BAUD_RATE, SERVO_IDS, _port, _scan_lock

POLL_INTERVAL_S = 0.1  # 10 readings a second while a controller answers
IDLE_INTERVAL_S = 1.0  # no website open, or no controller plugged in
PRESENT_POSITION_REG = 56


def enabled() -> bool:
    return os.environ.get("HANDOFF_LIVE_POSITIONS", "1") == "1"


def empty_reading(error: str | None = None) -> dict:
    return {"connected": False, "positions": {sid: None for sid in SERVO_IDS}, "read_at": None, "error": error}


def read_positions() -> dict | None:
    """Read IDs 1–6 once. Returns None when the controller is busy, so the caller keeps its last reading."""
    port, error = _port()
    if not port:
        return empty_reading(error)
    if not _scan_lock.acquire(blocking=False):
        return None
    driver = FeetechTuna()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if not driver.openSerialPort(port, BAUD_RATE, servoFamily="sms_sts"):
                return empty_reading("Could not open the servo controller.")
            positions = {sid: driver.readReg(sid, PRESENT_POSITION_REG) for sid in SERVO_IDS}
        answered = sum(value is not None for value in positions.values())
        return {"connected": answered == len(SERVO_IDS), "positions": positions, "read_at": time.time(),
                "error": None if answered == len(SERVO_IDS) else f"{answered} of 6 servos returned a position."}
    except Exception as exc:
        return empty_reading(f"Could not read servo positions: {exc}")
    finally:
        if getattr(getattr(driver, "porthandler", None), "is_open", False):
            with contextlib.redirect_stdout(io.StringIO()):
                driver.closeSerialPort()
        _scan_lock.release()
