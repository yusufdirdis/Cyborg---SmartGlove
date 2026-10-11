"""Physical servo position control using the existing Feetech driver.

Live edits and macro steps use the same synchronized transport and health
checks. Local hardware writes are enabled by default; set
HANDOFF_ENABLE_HARDWARE_WRITES=0 to disable them at startup.
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

    def write_position(self, servo_id: int, count: int, speed: int, acceleration: int | None = None) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            result, error = self.driver.packetHandler.WritePosEx(
                servo_id, count, speed, POSITION_ACCELERATION if acceleration is None else acceleration
            )
        if result != COMM_SUCCESS or error:
            raise HardwareControlError("The controller rejected the movement command. Cut power if the glove moves unexpectedly.")

    def write_positions_together(self, moves: list[tuple[int, int, int]]) -> None:
        handler = self.driver.packetHandler
        packet = handler.groupSyncWrite
        packet.clearParam()
        try:
            for servo_id, count, speed in moves:
                if not handler.SyncWritePosEx(servo_id, count, speed, POSITION_ACCELERATION):
                    raise HardwareControlError(f"Could not queue Servo {servo_id}; no group command was sent.")
            if packet.txPacket() != COMM_SUCCESS:
                raise HardwareControlError("Group command failed to send. Cut power if the glove moves unexpectedly.")
        finally:
            packet.clearParam()

    def wait_for_targets(self, targets: dict[int, int], is_stopped: Callable[[], bool] | None = None) -> dict[int, int]:
        """Wait until each requested position is reached and motion stops."""
        deadline = time.monotonic() + LIVE_MOVE_TIMEOUT_SECONDS
        time.sleep(min(LIVE_MOVE_INITIAL_WAIT_SECONDS, LIVE_MOVE_TIMEOUT_SECONDS))
        positions: dict[int, int] = {}
        while True:
            if is_stopped and is_stopped():
                raise HardwareControlError("Software Stop was pressed while the servos were moving.")
            states = {}
            for servo_id in sorted(targets):
                with contextlib.redirect_stdout(io.StringIO()):
                    position = self.driver.readReg(servo_id, 56)
                    moving = self.driver.readReg(servo_id, 66)
                if position is None or moving is None:
                    raise HardwareControlError(f"Could not verify Servo {servo_id}'s movement.")
                positions[servo_id] = int(position)
                states[servo_id] = int(moving)
            unfinished = [servo_id for servo_id, target in targets.items()
                          if states[servo_id] != 0 or abs(positions[servo_id] - target) > LIVE_POSITION_TOLERANCE]
            if not unfinished:
                return positions
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                details = ", ".join(
                    f"Servo {servo_id}: {positions[servo_id]} (target {targets[servo_id]}, moving={states[servo_id]})"
                    for servo_id in sorted(unfinished)
                )
                raise HardwareControlError(f"Timed out waiting for servos to settle: {details}.")
            time.sleep(min(LIVE_MOVE_POLL_SECONDS, remaining))

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


@dataclass(frozen=True)
class PendingGroup:
    token: str
    moves: tuple[tuple[int, int, int, int], ...]  # id, start, target, speed
    phrase: str
    calibrations: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True)
class PendingMacro:
    token: str
    macro_fingerprint: str
    steps: tuple[tuple[tuple[int, int, int], ...], ...]  # id, raw count, speed
    starts: tuple[tuple[int, int], ...]
    expires_at: float


_pending: PendingJog | None = None
_pending_lock = threading.Lock()
_position_pending: PendingPosition | None = None
_position_pending_lock = threading.Lock()
_group_pending: PendingGroup | None = None
_group_pending_lock = threading.Lock()
_macro_pending: PendingMacro | None = None
_macro_pending_lock = threading.Lock()
POSITION_SPEED_MIN = 50
POSITION_SPEED_MAX = 2400
POSITION_SPEED_DEFAULT = 2400
POSITION_ACCELERATION = 50
LIVE_POSITION_TOLERANCE = 5
LIVE_MOVE_INITIAL_WAIT_SECONDS = 1.0
LIVE_MOVE_POLL_SECONDS = 0.1
LIVE_MOVE_TIMEOUT_SECONDS = 30.0
MACRO_PREVIEW_SECONDS = 120


def writes_enabled() -> bool:
    # The hosted API (HANDOFF_CLOUD=1) has no USB glove: writes are always off there, whatever the flag says.
    if os.environ.get("HANDOFF_CLOUD", "").strip() == "1":
        return False
    return os.environ.get("HANDOFF_ENABLE_HARDWARE_WRITES", "1") == "1"


def clear_pending() -> None:
    global _pending, _position_pending, _group_pending, _macro_pending
    with _pending_lock:
        _pending = None
    with _position_pending_lock:
        _position_pending = None
    with _group_pending_lock:
        _group_pending = None
    with _macro_pending_lock:
        _macro_pending = None


def prepare_macro(name: str, macro_fingerprint: str, steps: list[dict], stopped: bool) -> dict:
    """Preview raw macro targets using the same count and health checks as live manual control."""
    global _macro_pending
    with _macro_pending_lock:
        _macro_pending = None
    if not 1 <= len(steps) <= 30:
        raise HardwareControlError("A hardware macro must contain 1–30 steps.")
    blocked: list[str] = []
    try:
        with _driver(lock_timeout=2.0) as driver:
            _check_bus_still(driver)
            snapshots = {servo_id: read_snapshot(driver, servo_id) for servo_id in range(1, 7)}
        for servo_id, snapshot in snapshots.items():
            _validate_live_snapshot(snapshot, servo_id)
    except (BenchJogError, HardwareControlError) as exc:
        blocked.append(str(exc))
        snapshots = locals().get("snapshots", {})

    preview_steps = []
    command_steps = []
    for index, step in enumerate(steps, start=1):
        targets = step.get("positions", {})
        mapped: dict[int, int] = {}
        for servo_id in range(1, 7):
            count = targets.get(servo_id, targets.get(str(servo_id)))
            if not isinstance(count, int) or not 0 <= count <= 4000:
                blocked.append(f"Step {index}: invalid raw target for Servo {servo_id}; use 0–4000 counts.")
                continue
            mapped[servo_id] = count
        speed_moves = []
        for servo_id, count in mapped.items():
            speed_moves.append((servo_id, count, POSITION_SPEED_DEFAULT))
        command_steps.append(tuple(speed_moves))
        preview_steps.append({"step": index, "duration_ms": step.get("duration_ms"),
                              "moves": [{"servo_id": sid, "target": count, "speed": speed}
                                        for sid, count, speed in speed_moves]})

    if stopped:
        blocked.append("Software stop is latched.")
    if not writes_enabled():
        blocked.append("Motor movement is locked. Start the local API with hardware writes enabled.")
    if blocked:
        return {"can_execute": False, "steps": preview_steps, "blocked_reason": " ".join(dict.fromkeys(blocked))}

    starts = tuple((sid, snapshots[sid].position) for sid in range(1, 7))
    pending = PendingMacro(str(uuid4()), macro_fingerprint, tuple(command_steps), starts,
                           time.monotonic() + MACRO_PREVIEW_SECONDS)
    with _macro_pending_lock:
        _macro_pending = pending
    return {"can_execute": True, "steps": preview_steps, "token": pending.token,
            "expires_in_seconds": MACRO_PREVIEW_SECONDS,
            "message": "Each step sends all six raw targets together at the Manual Control default speed. Step duration is simulation timing only."}


def execute_macro(token: str, macro_fingerprint: str, *, stopped: bool,
                  is_stopped: Callable[[], bool] | None = None) -> dict:
    """Run a previewed macro immediately. The user's Test on glove click is the instruction;
    health, write-mode, stop and changed-macro checks still apply."""
    global _macro_pending
    with _macro_pending_lock:
        pending = _macro_pending
        _macro_pending = None
    if not pending or pending.token != token or time.monotonic() > pending.expires_at:
        raise HardwareControlError("Macro preview expired. Review the hardware move again.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical movement is disabled or software stop is latched.")
    if macro_fingerprint != pending.macro_fingerprint:
        raise HardwareControlError("Macro changed after preview. Review it again.")
    if is_stopped and is_stopped():
        raise HardwareControlError("Software stop was pressed before the macro. No move was sent.")

    completed = []
    try:
        # Keep the shared serial bus reserved across the entire macro so a
        # status scan or another control request cannot interleave steps.
        with _driver(lock_timeout=2.0) as driver:
            _check_bus_still(driver)
            for servo_id, start in pending.starts:
                current = read_snapshot(driver, servo_id)
                _validate_live_snapshot(current, servo_id)
                if abs(current.position - start) > 10:
                    raise HardwareControlError(f"Servo {servo_id} changed position since preview. No macro command was sent.")
            transport = FeetechServoTransport(driver)
            for index, moves in enumerate(pending.steps, start=1):
                if is_stopped and is_stopped():
                    raise HardwareControlError(f"Software Stop was pressed before step {index}; remaining steps were not sent.")
                if not writes_enabled():
                    raise HardwareControlError("Physical movement was disabled; remaining macro steps were not sent.")
                # Reuse Manual Control's live health checks and synchronized
                # transport for every all-servo raw-count step.
                _check_bus_still(driver)
                for servo_id, _target, _speed in moves:
                    current = read_snapshot(driver, servo_id)
                    _validate_live_snapshot(current, servo_id)
                if is_stopped and is_stopped():
                    raise HardwareControlError(f"Software Stop was pressed before step {index}; remaining steps were not sent.")
                transport.write_positions_together(list(moves))
                final_positions = transport.wait_for_targets(
                    {servo_id: target for servo_id, target, _speed in moves}, is_stopped
                )
                completed.append({"step": index, "final_positions": final_positions})
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "steps_completed": completed,
            "message": "Macro completed on the glove."}


def run_macro(name: str, macro_fingerprint: str, steps: list[dict], *, stopped: bool,
              is_stopped: Callable[[], bool] | None = None) -> dict:
    """Test on glove: one click sends the macro. Servo health, write mode and Stop still apply."""
    preview = prepare_macro(name, macro_fingerprint, steps, stopped)
    if not preview["can_execute"]:
        raise HardwareControlError(f"{preview['blocked_reason']} No motor command was sent.")
    return execute_macro(preview["token"], macro_fingerprint, stopped=stopped, is_stopped=is_stopped)


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


def pending_group_servo_ids(token: str) -> list[int] | None:
    with _group_pending_lock:
        if _group_pending and _group_pending.token == token and time.monotonic() <= _group_pending.expires_at:
            return [servo_id for servo_id, _, _, _ in _group_pending.moves]
    return None


def _check_calibration(calibration, start: int, target: int) -> None:
    if not calibration.jog_limits_verified:
        raise HardwareControlError("Measured direct-test limits are missing for this servo; physical movement is blocked.")
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
            FeetechServoTransport(driver).write_position(pending.servo_id, target, SPEED_STEPS_PER_SECOND, ACCELERATION)
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "servo_id": pending.servo_id, "target": pending.target,
            "message": "One small motor command sent. Check the new position before any further move."}


def prepare_position(servo_id: int, target: int, speed: int, calibration, stopped: bool) -> dict:
    global _position_pending
    clear_pending()
    if servo_id not in range(1, 7) or not POSITION_SPEED_MIN <= speed <= POSITION_SPEED_MAX:
        raise HardwareControlError("Choose Servo 1–6 and a speed from 50 to 2400 counts per second.")
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


def prepare_group(moves: list[tuple[int, int, int]], calibrations: dict[int, object], stopped: bool) -> dict:
    """Preview one packet that moves two to six servos together."""
    global _group_pending
    clear_pending()
    ids = [servo_id for servo_id, _, _ in moves]
    if not 2 <= len(moves) <= 6 or len(ids) != len(set(ids)) or any(
        servo_id not in range(1, 7) or not 0 <= target <= 4000 or
        not POSITION_SPEED_MIN <= speed <= POSITION_SPEED_MAX
        for servo_id, target, speed in moves
    ):
        raise HardwareControlError("Select 2–6 distinct servos, with targets 0–4000 and speeds 50–2400.")
    try:
        with _driver() as driver:
            _check_bus_still(driver)
            snapshots = {servo_id: read_snapshot(driver, servo_id) for servo_id in ids}
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    blocked = []
    preview_moves = []
    for servo_id, target, speed in moves:
        snapshot = snapshots[servo_id]
        try:
            validate_snapshot(snapshot, margin=0)
            _check_calibration(calibrations[servo_id], snapshot.position, target)
            if target == snapshot.position:
                blocked.append(f"Servo {servo_id} is already at its target.")
        except (BenchJogError, HardwareControlError) as exc:
            blocked.append(f"Servo {servo_id}: {exc}")
        preview_moves.append({"servo_id": servo_id, "start": snapshot.position, "target": target, "speed": speed})
    if stopped:
        blocked.append("Software stop is latched.")
    if not writes_enabled():
        blocked.append("Motor movement is locked. Start the local API with hardware writes enabled.")
    base = {"moves": preview_moves, "can_execute": not blocked, "blocked_reason": " ".join(blocked) or None}
    if blocked:
        return base
    phrase = "MOVE TOGETHER " + ", ".join(f"{servo_id}:{target}@{speed}" for servo_id, target, speed in moves)
    pending = PendingGroup(str(uuid4()),
                           tuple((servo_id, snapshots[servo_id].position, target, speed) for servo_id, target, speed in moves),
                           phrase, tuple(calibrations[servo_id].model_dump_json() for servo_id in ids),
                           time.monotonic() + 30)
    with _group_pending_lock:
        _group_pending = pending
    return {**base, "token": pending.token, "phrase": phrase, "expires_in_seconds": 30}


def execute_group(token: str, phrase: str, *, off_body: bool, power_cut_tested: bool,
                  cord_path_clear: bool, calibrations: dict[int, object], stopped: bool,
                  is_stopped: Callable[[], bool] | None = None) -> dict:
    global _group_pending
    with _group_pending_lock:
        pending = _group_pending
        _group_pending = None
    if not pending or pending.token != token or time.monotonic() > pending.expires_at:
        raise HardwareControlError("Move preview expired. Check the group move again.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical movement is disabled or software stop is latched.")
    if not (off_body and power_cut_tested and cord_path_clear):
        raise HardwareControlError("Confirm off-body setup, power cutoff, and clear cord paths for this move.")
    if phrase.strip() != pending.phrase:
        raise HardwareControlError("Confirmation text did not match. No command was sent.")
    if tuple(calibrations[servo_id].model_dump_json() for servo_id, _, _, _ in pending.moves) != pending.calibrations:
        raise HardwareControlError("Calibration changed. Check the group move again.")
    try:
        with _driver() as driver:
            _check_bus_still(driver)
            for servo_id, start, target, _ in pending.moves:
                current = read_snapshot(driver, servo_id)
                validate_snapshot(current, margin=0)
                if abs(current.position - start) > 10:
                    raise HardwareControlError(f"Servo {servo_id} changed position since preview. No command was sent.")
                try:
                    gated_target_count(current.position, target, calibrations[servo_id],
                                       ActuationApproval(approved=True,
                                                         physical_estop_tested_this_session=power_cut_tested), stopped)
                except PermissionError as exc:
                    raise HardwareControlError(f"Servo {servo_id}: {exc}") from exc
            if is_stopped and is_stopped():
                raise HardwareControlError("Software stop was pressed before the group command. No move was sent.")
            FeetechServoTransport(driver).write_positions_together(
                [(servo_id, target, speed) for servo_id, _, target, speed in pending.moves]
            )
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "moves": [
        {"servo_id": servo_id, "target": target, "speed": speed}
        for servo_id, _, target, speed in pending.moves
    ], "message": "One synchronized packet sent. Watch every selected servo and use the physical power cutoff if needed."}


def move_live(moves: list[tuple[int, int, int]], stopped: bool,
              is_stopped: Callable[[], bool] | None = None) -> dict:
    """Send a single goto or one synchronized packet for grouped edits."""
    ids = [servo_id for servo_id, _, _ in moves]
    if not 1 <= len(moves) <= 6 or len(set(ids)) != len(ids) or any(
        servo_id not in range(1, 7) or not 0 <= target <= 4000 or
        not POSITION_SPEED_MIN <= speed <= POSITION_SPEED_MAX
        for servo_id, target, speed in moves
    ):
        raise HardwareControlError("Choose distinct servos with targets 0–4000 and speeds 50–2400.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical movement is disabled or software stop is latched.")
    try:
        with _driver(lock_timeout=2.0) as driver:
            for servo_id, target, _ in moves:
                snapshot = read_snapshot(driver, servo_id)
                # A live edit can arrive while the last goal is in progress, so
                # the stored goal and moving flag are intentionally not a block.
                _validate_live_snapshot(snapshot, servo_id)
            if is_stopped and is_stopped():
                raise HardwareControlError("Software stop was pressed. No move was sent.")
            transport = FeetechServoTransport(driver)
            if len(moves) == 1:
                # A single target uses the driver's direct goto command.
                # SyncWrite is reserved for genuinely grouped changes so
                # multiple servos start together in one broadcast packet.
                servo_id, target, speed = moves[0]
                transport.write_position(servo_id, target, speed)
            else:
                transport.write_positions_together(moves)
            final_positions = transport.wait_for_targets(
                {servo_id: target for servo_id, target, _ in moves}, is_stopped
            )
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"accepted": True, "moves": [
        {"servo_id": servo_id, "target": target, "speed": speed}
        for servo_id, target, speed in moves
    ], "final_positions": final_positions,
        "message": "Synchronized targets settled." if len(moves) > 1 else "Position target settled."}


def _validate_live_snapshot(snapshot, servo_id: int) -> None:
    """Shared health checks for Manual Control and physical macro steps."""
    if snapshot.model != 777 or snapshot.mode != 0 or snapshot.torque_enabled != 1:
        raise HardwareControlError(f"Servo {servo_id} is unavailable for position control.")
    if snapshot.voltage_v is None or not 6.0 <= snapshot.voltage_v <= 12.6:
        raise HardwareControlError(f"Servo {servo_id} voltage is out of range.")
    if snapshot.temperature_c is None or snapshot.temperature_c >= 50:
        raise HardwareControlError(f"Servo {servo_id} is too warm or has no temperature reading.")
    if snapshot.position is None or not 0 <= snapshot.position <= 4000:
        raise HardwareControlError(f"Servo {servo_id} position is unavailable or outside 0–4000 counts.")


def enable_torque(servo_ids: list[int], stopped: bool) -> dict:
    """Align selected torque-off servos to their present positions, then enable torque."""
    if not 1 <= len(servo_ids) <= 6 or len(set(servo_ids)) != len(servo_ids) or any(
        servo_id not in range(1, 7) for servo_id in servo_ids
    ):
        raise HardwareControlError("Choose distinct servo IDs from 1 through 6.")
    if not writes_enabled() or stopped:
        raise HardwareControlError("Physical control is disabled or software stop is latched.")
    try:
        with _driver(lock_timeout=2.0) as driver:
            snapshots = {}
            for servo_id in servo_ids:
                snapshot = read_snapshot(driver, servo_id)
                if snapshot.model != 777 or snapshot.mode != 0:
                    raise HardwareControlError(f"Servo {servo_id} is not available in position mode.")
                if snapshot.moving != 0:
                    raise HardwareControlError(f"Servo {servo_id} is moving; wait for it to stop before enabling torque.")
                if snapshot.torque_enabled != 0:
                    raise HardwareControlError(f"Servo {servo_id} torque is already on or unavailable; no torque changes were made.")
                if snapshot.position is None or not 0 <= snapshot.position <= 4000 or snapshot.goal_position is None:
                    raise HardwareControlError(f"Servo {servo_id} has an invalid current or stored position; torque was not enabled.")
                if snapshot.voltage_v is None or not 6.0 <= snapshot.voltage_v <= 12.6:
                    raise HardwareControlError(f"Servo {servo_id} voltage is out of range.")
                if snapshot.temperature_c is None or snapshot.temperature_c >= 50:
                    raise HardwareControlError(f"Servo {servo_id} is too warm or has no temperature reading.")
                snapshots[servo_id] = snapshot
            # A torque-off servo can retain a stale goal count. Replace that goal
            # with its live position first, preventing torque-on from pulling it
            # toward an old target. This writes no movement command.
            aligned_ids = []
            for servo_id, snapshot in snapshots.items():
                if snapshot.goal_position != snapshot.position:
                    with contextlib.redirect_stdout(io.StringIO()):
                        if driver.writeReg(servo_id, 42, snapshot.position) is not True:
                            raise HardwareControlError(f"Could not align Servo {servo_id}'s stored target; torque was not enabled.")
                        aligned = driver.readReg(servo_id, 42)
                    if aligned != snapshot.position:
                        raise HardwareControlError(f"Servo {servo_id}'s stored target did not verify; torque was not enabled.")
                    aligned_ids.append(servo_id)
            for servo_id in servo_ids:
                with contextlib.redirect_stdout(io.StringIO()):
                    if driver.writeReg(servo_id, 40, 1) is not True:
                        raise HardwareControlError(f"Could not enable torque on Servo {servo_id}.")
                    torque = driver.readReg(servo_id, 40)
                if torque != 1:
                    raise HardwareControlError(f"Servo {servo_id} did not confirm torque enabled.")
    except BenchJogError as exc:
        raise HardwareControlError(str(exc)) from exc
    return {"enabled_ids": servo_ids, "aligned_ids": aligned_ids,
            "message": "Stored targets aligned to current positions; torque enabled for selected servos."}


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
