"""One-shot, small bench movement for unloaded ST3215 servos.

This is a terminal tool, not an API route. It does not change calibration and
does nothing to a motor unless --execute and an exact interactive confirmation
are both supplied. Stop the handoff API before opening the same serial port.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import time
from dataclasses import dataclass
from typing import Callable

from feetech_tuna import FeetechTuna
from feetech_tuna.feetech_tuna import COMM_SUCCESS


BAUD_RATE = 1_000_000
MODEL_ST3215 = 777
JOG_COUNTS = 10
SPEED_STEPS_PER_SECOND = 50
ACCELERATION = 5
POSITION_MARGIN = 100
MAX_TEMPERATURE_C = 50
MIN_VOLTAGE_V = 6.0
MAX_VOLTAGE_V = 12.6
POSITION_TOLERANCE = 5
MAX_STORED_TARGET_ERROR = 10
SETTLE_TIMEOUT_SECONDS = 3.0


class BenchJogError(Exception):
    """The preflight or one-shot movement did not complete safely."""


@dataclass(frozen=True)
class Snapshot:
    model: int
    position: int | None
    goal_position: int | None
    voltage_v: float | None
    temperature_c: int | None
    moving: int | None
    mode: int | None
    torque_enabled: int | None


def read_snapshot(driver: FeetechTuna, servo_id: int) -> Snapshot:
    with contextlib.redirect_stdout(io.StringIO()):
        model, result, error = driver.packetHandler.ping(servo_id)
        if result != COMM_SUCCESS or error:
            raise BenchJogError(f"Servo {servo_id} did not answer the preflight ping.")
        position = driver.readReg(servo_id, 56)
        goal_position = driver.readReg(servo_id, 42)
        voltage = driver.readReg(servo_id, 62)
        temperature = driver.readReg(servo_id, 63)
        moving = driver.readReg(servo_id, 66)
        mode = driver.readReg(servo_id, 33)
        torque = driver.readReg(servo_id, 40)
    return Snapshot(model, position, goal_position, None if voltage is None else voltage / 10, temperature, moving, mode, torque)


def target_for(snapshot: Snapshot, delta: int) -> int:
    if delta not in (-JOG_COUNTS, JOG_COUNTS):
        raise BenchJogError(f"Only a single +{JOG_COUNTS} or -{JOG_COUNTS} count jog is allowed.")
    if snapshot.model != MODEL_ST3215:
        raise BenchJogError(f"Unexpected servo model {snapshot.model}; expected {MODEL_ST3215}.")
    if snapshot.mode != 0:
        raise BenchJogError("Servo is not in position mode; no movement sent.")
    if snapshot.moving != 0:
        raise BenchJogError("Servo is moving or movement state is unknown; no movement sent.")
    if snapshot.voltage_v is None or not MIN_VOLTAGE_V <= snapshot.voltage_v <= MAX_VOLTAGE_V:
        raise BenchJogError("Servo supply voltage is outside the conservative bench range; no movement sent.")
    if snapshot.temperature_c is None or snapshot.temperature_c >= MAX_TEMPERATURE_C:
        raise BenchJogError("Servo is too warm or temperature is unknown; no movement sent.")
    if snapshot.position is None or not POSITION_MARGIN <= snapshot.position <= 4095 - POSITION_MARGIN:
        raise BenchJogError("Servo position is missing or too close to the numeric range edge.")
    if snapshot.goal_position is None or abs(snapshot.goal_position - snapshot.position) > MAX_STORED_TARGET_ERROR:
        raise BenchJogError("Stored target is far from the present position. Do not enable torque in this state.")
    if snapshot.torque_enabled != 1:
        raise BenchJogError("Servo torque is not enabled; no movement sent.")
    target = snapshot.position + delta
    if not POSITION_MARGIN <= target <= 4095 - POSITION_MARGIN:
        raise BenchJogError("Proposed target is too close to the numeric range edge.")
    return target


def run_jog(
    driver: FeetechTuna,
    servo_id: int,
    delta: int,
    execute: bool,
    input_fn: Callable[[str], str] = input,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> Snapshot | None:
    if servo_id not in range(1, 7):
        raise BenchJogError("Only glove servo IDs 1–6 are allowed.")
    before = read_snapshot(driver, servo_id)
    print(
        f"Servo {servo_id} preflight: position={before.position}, stored target={before.goal_position}, "
        f"voltage={before.voltage_v}, temperature={before.temperature_c}, "
        f"moving={before.moving}, mode={before.mode}, torque={before.torque_enabled}."
    )
    target = target_for(before, delta)
    print(
        f"Servo {servo_id}: {before.position} → {target} counts; "
        f"{before.voltage_v:.1f} V, {before.temperature_c}°C, position mode, torque on."
    )
    print(f"Proposed one-shot jog: {SPEED_STEPS_PER_SECOND} steps/s, acceleration {ACCELERATION}. No automatic return.")
    if not execute:
        print("DRY RUN: no motor command sent. Add --execute only after the off-body power-cut test.")
        return None

    phrase = f"JOG {servo_id} {delta:+d} TO {target}"
    if input_fn(f"Type {phrase} to send this one motor command: ").strip() != phrase:
        print("Cancelled; no motor command sent.")
        return None

    current = read_snapshot(driver, servo_id)
    current_target = target_for(current, delta)
    if abs(current.position - before.position) > 2 or current_target != target:
        raise BenchJogError("The servo changed after confirmation; no motor command sent.")
    result, error = driver.packetHandler.WritePosEx(
        servo_id, target, SPEED_STEPS_PER_SECOND, ACCELERATION
    )
    if result != COMM_SUCCESS or error:
        raise BenchJogError("The servo rejected the movement command. Cut power if anything moves unexpectedly.")

    deadline = monotonic_fn() + SETTLE_TIMEOUT_SECONDS
    while True:
        after = read_snapshot(driver, servo_id)
        if after.position is not None and abs(after.position - target) <= POSITION_TOLERANCE and after.moving == 0:
            print(f"Servo {servo_id} stopped at {after.position} counts. No further motor command was sent.")
            return after
        if monotonic_fn() >= deadline:
            raise BenchJogError("Servo did not settle at the small target. Cut servo power and inspect it before retrying.")
        sleep_fn(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight or one-shot small unloaded ST3215 jog")
    parser.add_argument("--port", required=True, help="USB servo controller port, such as /dev/cu.usbmodem...")
    parser.add_argument("--servo", type=int, required=True, choices=range(1, 7), metavar="1..6")
    parser.add_argument("--delta", type=int, required=True, choices=(-JOG_COUNTS, JOG_COUNTS))
    parser.add_argument("--execute", action="store_true", help="Allow one movement after an exact interactive confirmation")
    args = parser.parse_args()

    driver = FeetechTuna()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            opened = driver.openSerialPort(args.port, BAUD_RATE, servoFamily="sms_sts")
        if not opened:
            raise BenchJogError("Could not open the servo controller. Stop the handoff API and check the USB port.")
        run_jog(driver, args.servo, args.delta, args.execute)
        return 0
    except BenchJogError as exc:
        print(f"Stopped: {exc}")
        return 2
    finally:
        if getattr(getattr(driver, "porthandler", None), "is_open", False):
            with contextlib.redirect_stdout(io.StringIO()):
                driver.closeSerialPort()


if __name__ == "__main__":
    raise SystemExit(main())
