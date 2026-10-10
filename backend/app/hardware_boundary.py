"""Contract for future physical movement. Not connected to API control routes.

The read-only status route is separate. Before adding any motor writes, a
physical transport must call this gate for every pose or sequence step.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .main import CalibrationInput, SERVO_NAMES


class ServoTransport(Protocol):
    def read_position(self, servo_id: int) -> int: ...
    def write_position(self, servo_id: int, count: int, speed: int) -> None: ...
    def disable_torque(self) -> None: ...


@dataclass(frozen=True)
class ActuationApproval:
    """One use approval must come from an explicit UI confirmation."""
    approved: bool
    physical_estop_tested_this_session: bool


def gated_counts(percentages: dict[int, int], calibration: dict[int, CalibrationInput], approval: ActuationApproval, stopped: bool) -> dict[int, tuple[int, int]]:
    if stopped:
        raise PermissionError("Software stop is latched")
    if not approval.approved or not approval.physical_estop_tested_this_session:
        raise PermissionError("Explicit confirmation and a tested physical E-stop are required")
    if set(percentages) != set(SERVO_NAMES) or set(calibration) != set(SERVO_NAMES):
        raise PermissionError("All six servos require a complete pose and calibration")
    result = {}
    for sid in SERVO_NAMES:
        c = calibration[sid]
        if not c.verified:
            raise PermissionError(f"Servo {sid} has not been calibrated")
        p = percentages[sid]
        if not 0 <= p <= 100:
            raise ValueError("Pose percentage outside 0–100")
        count = round(c.open_count + (c.closed_count - c.open_count) * p / 100)
        if not c.min_count <= count <= c.max_count:
            raise PermissionError(f"Servo {sid} target exceeds measured limits")
        result[sid] = (count, c.speed)
    return result
