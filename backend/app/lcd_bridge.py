"""Torque-readiness feed for the 16x2 LCD on an ESP (display only).

It never opens the servo port and never sends a motor
command: it reads state the API already has (software stop, the write flag, the last
/api/hardware/status result, the torque-enable route) and writes one line per state to the
ESP's own USB serial port. Disabled unless HANDOFF_LCD_PORT is set.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

STALE_SECONDS = 45
ERROR_SECONDS = 10
HEARTBEAT_SECONDS = 1.0
TICK_SECONDS = 0.25

_last_reading: dict[str, Any] | None = None
_last_reading_at = 0.0
_torque_busy = False
_torque_error: tuple[int, float] | None = None


def enabled() -> bool:
    return bool(os.environ.get("HANDOFF_LCD_PORT"))


def record_reading(reading: dict[str, Any]) -> None:
    """Call with every /api/hardware/status result."""
    global _last_reading, _last_reading_at
    _last_reading, _last_reading_at = reading, time.monotonic()


def torque_started() -> None:
    global _torque_busy, _torque_error
    _torque_busy, _torque_error = True, None


def torque_finished(error: str | None = None) -> None:
    """Call when the torque-enable route returns; pass the error text if it failed."""
    global _torque_busy, _torque_error
    _torque_busy = False
    if error:
        match = re.search(r"Servo (\d)", error)
        _torque_error = (int(match.group(1)) if match else 0, time.monotonic())


def compute_state(*, stopped: bool, writes: bool, reading: dict[str, Any] | None, reading_age: float,
                  busy: bool, error: tuple[int, float] | None, now: float) -> str:
    """Pure function: the line the LCD should show. Priority follows docs/lcd-display/SPEC.md."""
    if stopped:
        return "STATE STOPPED"
    if not writes:
        return "STATE LOCKED"
    if not reading or not reading.get("connected") or not reading.get("servos"):
        return "STATE OFFLINE"
    if reading_age > STALE_SECONDS:
        return "STATE UNKNOWN"
    if busy:
        return "STATE ALIGNING"
    if error and now - error[1] <= ERROR_SECONDS:
        return f"STATE NOT_ENABLED {error[0]}"
    servos = reading["servos"]
    if len(servos) == 6 and all(s.get("torque_enabled") is True for s in servos):
        return "STATE TORQUE_ON"
    settled = sum(1 for s in servos if s.get("moving") is False)
    return "STATE READY" if settled >= 6 else f"STATE SETTLING {settled}"


def current_line(stopped: bool) -> str:
    from .hardware_control import writes_enabled

    now = time.monotonic()
    return compute_state(stopped=stopped, writes=writes_enabled(), reading=_last_reading,
                         reading_age=now - _last_reading_at if _last_reading else 0.0,
                         busy=_torque_busy, error=_torque_error, now=now)


async def run(is_stopped) -> None:
    """Background task: write the state on change and every second; reopen the port if it drops."""
    import serial  # pyserial, already in requirements.txt

    port_name = os.environ["HANDOFF_LCD_PORT"]
    port = None
    last_line, last_sent = "", 0.0
    while True:
        try:
            if port is None:
                port = serial.Serial(port_name, 115200, timeout=0, write_timeout=0.2)
            line = current_line(is_stopped())
            now = time.monotonic()
            if line != last_line or now - last_sent >= HEARTBEAT_SECONDS:
                port.write((line + "\n").encode("ascii"))
                last_line, last_sent = line, now
        except Exception:  # unplugged or busy: the ESP falls back to NO DATA after 5 s
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
            port = None
            await asyncio.sleep(2)
        await asyncio.sleep(TICK_SECONDS)
