"""Confirmed single-servo position control using the existing Feetech driver.

This is intentionally separate from simulation and macro playback. Hardware
writes are disabled unless explicitly enabled when starting the local API.
"""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from feetech_tuna import FeetechTuna
from feetech_tuna.feetech_tuna import COMM_SUCCESS

from .bench_jog import (
    ACCELERATION,
    BAUD_RATE,
    JOG_COUNTS,
    SPEED_STEPS_PER_SECOND,
    BenchJogError,
    read_snapshot,
    target_for,
    validate_snapshot,
)
from .hardware_boundary import ActuationApproval, gated_jog_count, gated_target_count
from .servo_status import _port, _scan_lock


class HardwareControlError(Exception):
    pass


class FeetechServoTransport:
    """Small adapter around the supplied Feetech-tuna serial driver."""

    def __init__(self, driver: FeetechTuna):
        self.driver = driver

    def read_position(self, servo_id: int) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            value = self.driver.readReg(servo_id, 56)
        if value is None:
            raise HardwareControlError(f"Could not read Servo {servo_id}'s position.")
        return int(value)

    def write_position(self, servo_id: int, count: int, speed: int) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            result, error = self.driver.packetHandler.WritePosEx(servo_id, count, speed, ACCELERATION)
        if result != COMM_SUCCESS or error:
            raise HardwareControlError("The controller rejected the movement command. Cut power if the glove moves unexpectedly.")

    def disable_torque(self) -> list[int]:
        failed = []
        for servo_id in range(1, 7):
            with contextlib.redirect_stdout(io.StringIO()):
                if self.driver.writeReg(servo_id, 40, 0) is not True:
                    failed.append(servo_id)
        return failed


@dataclass(frozen=True)
class PendingJog:
    token: str
    servo_id: int
    delta: int
    start: int
    target: int
    phrase: str
    calibration: str
    expires_at: float


@dataclass(frozen=True)
class PendingPosition:
    token: str
    servo_id: int
    start: int
    target: int
    speed: int
    phrase: str
    calibration: str
    expires_at: float


_pending: PendingJog | None = None
_pending_lock = threading.Lock()
_position_pending: PendingPosition | None = None
_position_pending_lock = threading.Lock()
POSITION_SPEED_MIN = 50
POSITION_SPEED_MAX = 500
POSITION_SPEED_DEFAULT = 250


def writes_enabled() -> bool:
    return os.environ.get("HANDOFF_ENABLE_HARDWARE_WRITES") == "1"


def clear_pending() -> None:
    global _pending, _position_pending
    with _pending_lock:
        _pending = None
    with _position_pending_lock:
        _position_pending = None


def pending_servo_id(token: str) -> int | None:
    with _pending_lock:
        if _pending and _pending.token == token and time.monotonic() <= _pending.expires_at:
            return _pending.servo_id
    return None


def pending_position_servo_id(token: str) -> int | None:
    with _position_pending_lock:
        if _position_pending and _position_pending.token == token and time.monotonic() <= _position_pending.expires_at:
            return _position_pending.servo_id
    return None


def _check_calibration(calibration, start: int, target: int) -> None:
    if not calibration.jog_limits_verified:
        raise HardwareControlError("Confirm this servo's measured direct-test limits in Calibration first.")
    if not (calibration.min_count <= start <= calibration.max_count and calibration.min_count <= target <= calibration.max_count):
        raise HardwareControlError("The current position or proposed target is outside this servo's measured limits.")


def _check_bus_still(driver) -> None:
    """Allow one moving servo at a time on the shared power bus."""
    with contextlib.redirect_stdout(io.StringIO()):
        states = [driver.readReg(servo_id, 66) for servo_id in range(1, 7)]
    if any(state != 0 for state in states):
        raise HardwareControlError("A servo is moving or its movement state is unavailable. Wait for every servo to stop.")


@contextlib.contextmanager
def _driver(*, lock_timeout: float = 0):
    if not _scan_lock.acquire(timeout=lock_timeout):
        raise HardwareControlError("The controller is busy. Wait for the status check and retry.")
    driver = FeetechTuna()
    try:
        port, error = _port()
        if not port:
            raise HardwareControlError(error or "No compatible USB servo controller is connected.")
        with contextlib.redirect_stdout(io.StringIO()):
            opened = driver.openSerialPort(port, BAUD_RATE, servoFamily="sms_sts")
        if not opened:
            raise HardwareControlError("Could not open the USB servo controller.")
        yield driver
    finally:
        if getattr(getattr(driver, "porthandler", None), "is_open", False):
            with contextlib.redirect_stdout(io.StringIO()):
                driver.closeSerialPort()
        _scan_lock.release()


def prepare_jog(servo_id: int, delta: int, calibration, stopped: bool) -> dict:
    global _pending
    clear_pending()
    if servo_id not in range(1, 7) or delta not in (-JOG_COUNTS, JOG_COUNTS):
        raise HardwareControlError("Choose Servo 1–6 and a single 10-count step.")
    try:
        with _driver() as driver:
            before = read_snapshot(driver, servo_id)
        target = target_for(before, delta)
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    blocked_reasons = []
    try:
        _check_calibration(calibration, before.position, target)
    except HardwareControlError as exc:
        blocked_reasons.append(str(exc))
    if stopped:
        blocked_reasons.append("Software stop is latched. Reset it before preparing another move.")
    if not writes_enabled():
        blocked_reasons.append("Motor movement is locked for now. This preview does not move the glove.")
    blocked_reason = " ".join(blocked_reasons) or None
    base = {"servo_id": servo_id, "start": before.position, "target": target,
            "delta": delta, "can_execute": blocked_reason is None, "blocked_reason": blocked_reason}
    if blocked_reason:
        return base
    phrase = f"MOVE {servo_id} {delta:+d} TO {target}"
    pending = PendingJog(str(uuid4()), servo_id, delta, before.position, target, phrase,
                         calibration.model_dump_json(), time.monotonic() + 30)
    with _pending_lock:
        _pending = pending
    return {**base, "token": pending.token, "phrase": phrase, "expires_in_seconds": 30}


def execute_jog(token: str, phrase: str, *, off_body: bool, power_cut_tested: bool,
                cord_path_clear: bool, calibration, stopped: bool,
                is_stopped: Callable[[], bool] | None = None) -> dict:
    global _pending
    with _pending_lock:
        pending = _pending
        _pending = None  # One use, including failed confirmations.
    if not pending or pending.token != token or time.monotonic() > pending.expires_at:
        raise HardwareControlError("Move preview expired. Check the move again.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical movement is disabled or software stop is latched.")
    if not (off_body and power_cut_tested and cord_path_clear):
        raise HardwareControlError("Confirm off-body setup, power cutoff, and a clear cord path for this move.")
    if phrase.strip() != pending.phrase:
        raise HardwareControlError("Confirmation text did not match. No command was sent.")
    if calibration.model_dump_json() != pending.calibration:
        raise HardwareControlError("Calibration changed. Check the move again.")
    try:
        with _driver() as driver:
            current = read_snapshot(driver, pending.servo_id)
            target = target_for(current, pending.delta)
            if abs(current.position - pending.start) > 2 or target != pending.target:
                raise HardwareControlError("Servo position changed since the preview. No command was sent.")
            try:
                gated_jog_count(current.position, target, calibration,
                                ActuationApproval(approved=True,
                                                  physical_estop_tested_this_session=power_cut_tested),
                                stopped)
            except PermissionError as exc:
                raise HardwareControlError(str(exc)) from exc
            if is_stopped and is_stopped():
                raise HardwareControlError("Software stop was pressed before the command. No move was sent.")
            FeetechServoTransport(driver).write_position(pending.servo_id, target, SPEED_STEPS_PER_SECOND)
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "servo_id": pending.servo_id, "target": pending.target,
            "message": "One small motor command sent. Check the new position before any further move."}


def prepare_position(servo_id: int, target: int, speed: int, calibration, stopped: bool) -> dict:
    global _position_pending
    clear_pending()
    if servo_id not in range(1, 7) or not POSITION_SPEED_MIN <= speed <= POSITION_SPEED_MAX:
        raise HardwareControlError("Choose Servo 1–6 and a speed from 50 to 500 counts per second.")
    try:
        with _driver() as driver:
            before = read_snapshot(driver, servo_id)
            validate_snapshot(before, margin=0)
            _check_bus_still(driver)
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    blocked_reasons = []
    try:
        _check_calibration(calibration, before.position, target)
        if target == before.position:
            blocked_reasons.append("Servo is already at that count.")
    except HardwareControlError as exc:
        blocked_reasons.append(str(exc))
    if stopped:
        blocked_reasons.append("Software stop is latched. Reset it before preparing another move.")
    if not writes_enabled():
        blocked_reasons.append("Motor movement is locked. Start the local API with hardware writes enabled.")
    blocked_reason = " ".join(blocked_reasons) or None
    base = {"servo_id": servo_id, "start": before.position, "target": target,
            "speed": speed, "can_execute": blocked_reason is None, "blocked_reason": blocked_reason}
    if blocked_reason:
        return base
    phrase = f"MOVE {servo_id} TO {target} AT {speed}"
    pending = PendingPosition(str(uuid4()), servo_id, before.position, target, speed,
                              phrase, calibration.model_dump_json(), time.monotonic() + 30)
    with _position_pending_lock:
        _position_pending = pending
    return {**base, "token": pending.token, "phrase": phrase, "expires_in_seconds": 30}


def execute_position(token: str, phrase: str, *, off_body: bool, power_cut_tested: bool,
                     cord_path_clear: bool, calibration, stopped: bool,
                     is_stopped: Callable[[], bool] | None = None) -> dict:
    global _position_pending
    with _position_pending_lock:
        pending = _position_pending
        _position_pending = None  # One use, including rejected confirmations.
    if not pending or pending.token != token or time.monotonic() > pending.expires_at:
        raise HardwareControlError("Move preview expired. Check the move again.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical movement is disabled or software stop is latched.")
    if not (off_body and power_cut_tested and cord_path_clear):
        raise HardwareControlError("Confirm off-body setup, power cutoff, and a clear cord path for this move.")
    if phrase.strip() != pending.phrase:
        raise HardwareControlError("Confirmation text did not match. No command was sent.")
    if calibration.model_dump_json() != pending.calibration:
        raise HardwareControlError("Calibration changed. Check the move again.")
    try:
        with _driver() as driver:
            current = read_snapshot(driver, pending.servo_id)
            validate_snapshot(current, margin=0)
            _check_bus_still(driver)
            if abs(current.position - pending.start) > 2:
                raise HardwareControlError("Servo position changed since the preview. No command was sent.")
            try:
                gated_target_count(current.position, pending.target, calibration,
                                   ActuationApproval(approved=True,
                                                     physical_estop_tested_this_session=power_cut_tested),
                                   stopped)
            except PermissionError as exc:
                raise HardwareControlError(str(exc)) from exc
            if is_stopped and is_stopped():
                raise HardwareControlError("Software stop was pressed before the command. No move was sent.")
            FeetechServoTransport(driver).write_position(pending.servo_id, pending.target, pending.speed)
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "servo_id": pending.servo_id, "target": pending.target,
            "speed": pending.speed, "message": "One position command sent. Watch the glove and use Stop or the physical power cutoff if needed."}


def disable_torque() -> dict:
    """Best-effort software stop. The independent power cutoff is still essential."""
    clear_pending()
    if not writes_enabled():
        return {"attempted": False, "message": "Physical writes are disabled."}
    try:
        with _driver(lock_timeout=2.0) as driver:
            failed = FeetechServoTransport(driver).disable_torque()
            return {"attempted": True, "failed_ids": failed,
                    "message": "Torque-off sent. Cut servo power physically if anything continues moving."}
    except HardwareControlError as exc:
        return {"attempted": False, "message": f"Could not send torque-off: {exc}. Cut servo power physically."}
