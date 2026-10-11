#!/usr/bin/env python3
"""Set targets for multiple Feetech ST3215 servos and send them together."""

import argparse
import contextlib
import io
import sys
import time
from typing import Dict, Iterable, Optional

from feetech_tuna import FeetechTuna
from feetech_tuna.feetech_tuna import COMM_SUCCESS


BAUD_RATE = 1_000_000
SERVO_SPEED = 2400
SERVO_ACCELERATION = 50
MOVE_PAUSE_SECONDS = 1.0
MOVE_POLL_SECONDS = 0.1
MOVE_TIMEOUT_SECONDS = 30.0
POSITION_TOLERANCE = 5
POSITION_MIN = 0
POSITION_MAX = 4000
SERVO_IDS = (1, 2, 3, 4, 5, 6, 7, 8)
REG_PRESENT_POSITION = 56
REG_TORQUE_ENABLE = 40
REG_MOVING = 66


class CommandError(Exception):
    """A requested operation cannot be completed."""


class MultiServoMover:
    def __init__(self, driver: FeetechTuna):
        self.driver = driver
        self.found_ids = []
        self.pending_targets: Dict[int, int] = {}

    def read_position(self, servo_id: int) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            position = self.driver.readReg(servo_id, REG_PRESENT_POSITION)
        if position is None:
            raise CommandError(f"Could not read Servo {servo_id}'s position.")
        return int(position)

    def read_register(self, servo_id: int, address: int) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            value = self.driver.readReg(servo_id, address)
        if value is None:
            raise CommandError(f"Could not read Servo {servo_id}, register {address}.")
        return int(value)

    def discover(self) -> None:
        print("Discovering servo IDs 1–8 (read only)...")
        self.found_ids.clear()
        rows = {}
        for servo_id in SERVO_IDS:
            _model, result, _error = self.driver.packetHandler.ping(servo_id)
            if result == COMM_SUCCESS:
                self.found_ids.append(servo_id)
                try:
                    rows[servo_id] = self.read_position(servo_id)
                except CommandError:
                    rows[servo_id] = None
            else:
                rows[servo_id] = None

        print("ID | Status     | Current position")
        print("---+------------+-----------------")
        for servo_id in SERVO_IDS:
            if servo_id not in self.found_ids:
                print(f"{servo_id:>2} | NOT FOUND  | -")
            elif rows[servo_id] is None:
                print(f"{servo_id:>2} | FOUND      | read failed")
            else:
                print(f"{servo_id:>2} | FOUND      | {rows[servo_id]}")
        print(f"Detected {len(self.found_ids)} servo(s).")

    def validate_target(self, servo_id: int, position: int) -> None:
        if servo_id not in SERVO_IDS:
            raise CommandError("Servo ID must be from 1 through 8.")
        if servo_id not in self.found_ids:
            raise CommandError(f"Servo {servo_id} was not found during discovery.")
        if not POSITION_MIN <= position <= POSITION_MAX:
            raise CommandError(
                f"Position for Servo {servo_id} must be {POSITION_MIN}–{POSITION_MAX}."
            )

    def set_target(self, servo_id: int, position: int) -> None:
        self.validate_target(servo_id, position)
        self.pending_targets[servo_id] = position
        print(f"Queued Servo {servo_id} → {position}.")

    def show_targets(self) -> None:
        if not self.pending_targets:
            print("No targets are queued.")
            return
        print("Queued targets (type `send` to move them together):")
        for servo_id, position in sorted(self.pending_targets.items()):
            print(f"  Servo {servo_id} → {position}")

    def send_targets(self, targets: Dict[int, int], *, clear_pending: bool = False) -> None:
        if not targets:
            raise CommandError("Specify at least one servo and target position.")
        for servo_id, position in targets.items():
            self.validate_target(servo_id, position)

        # Read all starting positions before sending any target, so a read
        # failure cannot leave only part of this requested group in motion.
        starts = {servo_id: self.read_position(servo_id) for servo_id in sorted(targets)}
        torque_off = [servo_id for servo_id in sorted(targets)
                      if self.read_register(servo_id, REG_TORQUE_ENABLE) != 1]
        if torque_off:
            ids = ", ".join(str(servo_id) for servo_id in torque_off)
            raise CommandError(f"Torque is off for Servo(s) {ids}; no targets were sent.")
        print("Sending synchronized targets:")
        for servo_id in sorted(targets):
            print(
                f"  Servo {servo_id} | starting={starts[servo_id]} "
                f"| target={targets[servo_id]}"
            )

        handler = self.driver.packetHandler
        handler.groupSyncWrite.clearParam()
        try:
            for servo_id in sorted(targets):
                queued = handler.SyncWritePosEx(
                    servo_id,
                    targets[servo_id],
                    SERVO_SPEED,
                    SERVO_ACCELERATION,
                )
                if not queued:
                    raise CommandError(f"Could not queue Servo {servo_id} for synchronized write.")
            result = handler.groupSyncWrite.txPacket()
            if result != COMM_SUCCESS:
                detail = handler.getTxRxResult(result)
                raise CommandError(f"Synchronized write failed: {detail or result}.")
        finally:
            handler.groupSyncWrite.clearParam()

        if clear_pending:
            self.pending_targets.clear()
        self.wait_for_targets(targets)
        print("Positions after the move:")
        for servo_id in sorted(targets):
            print(f"  Servo {servo_id} | ending position={self.read_position(servo_id)}")

    def wait_for_targets(self, targets: Dict[int, int]) -> None:
        """Wait until each selected servo stops within the target tolerance."""
        deadline = time.monotonic() + MOVE_TIMEOUT_SECONDS
        time.sleep(MOVE_PAUSE_SECONDS)
        while True:
            positions = {servo_id: self.read_position(servo_id)
                         for servo_id in sorted(targets)}
            moving = {servo_id: self.read_register(servo_id, REG_MOVING)
                      for servo_id in sorted(targets)}
            unfinished = [servo_id for servo_id, target in targets.items()
                          if moving[servo_id] != 0 or
                          abs(positions[servo_id] - target) > POSITION_TOLERANCE]
            if not unfinished:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                details = ", ".join(
                    f"Servo {servo_id}: {positions[servo_id]} (target {targets[servo_id]}, "
                    f"moving={moving[servo_id]})"
                    for servo_id in sorted(unfinished)
                )
                raise CommandError(f"Timed out waiting for targets: {details}.")
            time.sleep(min(MOVE_POLL_SECONDS, remaining))

    def send_pending(self) -> None:
        self.send_targets(dict(self.pending_targets), clear_pending=True)

    def show_status(self) -> None:
        print("ID | Status     | Current position")
        print("---+------------+-----------------")
        for servo_id in SERVO_IDS:
            if servo_id not in self.found_ids:
                print(f"{servo_id:>2} | NOT FOUND  | -")
                continue
            try:
                position = self.read_position(servo_id)
            except CommandError:
                print(f"{servo_id:>2} | FOUND      | read failed")
            else:
                print(f"{servo_id:>2} | FOUND      | {position}")


def parse_target_assignments(tokens: Iterable[str]) -> Dict[int, int]:
    targets: Dict[int, int] = {}
    parts = list(tokens)
    index = 0
    while index < len(parts):
        token = parts[index]
        try:
            if index + 2 < len(parts) and parts[index + 1] == "=":
                servo_text, position_text = token, parts[index + 2]
                index += 3
            else:
                servo_text, position_text = token.split("=", 1)
                index += 1
            servo_id = int(servo_text)
            position = int(position_text)
        except ValueError as exc:
            raise CommandError(
                f"Invalid target `{token}`. Use ID=POSITION, for example 2=2048."
            ) from exc
        if servo_id in targets:
            raise CommandError(f"Servo {servo_id} appears more than once in this command.")
        targets[servo_id] = position
    return targets


def print_help() -> None:
    print(
        "Commands:\n"
        "  set <id> <position>       Queue or replace one servo's target\n"
        "  targets                   Show queued targets\n"
        "  send                      Send all queued targets together\n"
        "  sync <id=pos> [...]       Set and send targets together immediately\n"
        "  status                    Read current positions for IDs 1–8\n"
        "  clear                     Clear queued targets without moving\n"
        "  help                      Show these commands\n"
        "  exit                      Close the serial connection"
    )


def run_command(mover: MultiServoMover, line: str) -> bool:
    parts = line.split()
    if not parts:
        return True
    command = parts[0].lower()
    if command in ("exit", "quit"):
        return False
    if command == "help" and len(parts) == 1:
        print_help()
    elif command == "set" and len(parts) == 3:
        try:
            servo_id, position = int(parts[1]), int(parts[2])
        except ValueError as exc:
            raise CommandError("Servo ID and position must be integers.") from exc
        mover.set_target(servo_id, position)
    elif command == "targets" and len(parts) == 1:
        mover.show_targets()
    elif command == "send" and len(parts) == 1:
        mover.send_pending()
    elif command in ("sync", "sync-goto") and len(parts) >= 2:
        mover.send_targets(parse_target_assignments(parts[1:]))
    elif command == "status" and len(parts) == 1:
        mover.show_status()
    elif command == "clear" and len(parts) == 1:
        mover.pending_targets.clear()
        print("Queued targets cleared; nothing moved.")
    else:
        print("Unknown command or wrong number of arguments. Type `help` for usage.")
    return True


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move specified Feetech ST3215 servos together using SyncWrite."
    )
    parser.add_argument("port", help="Serial port, for example /dev/cu.usbmodem5B901507591")
    args = parser.parse_args(argv)

    driver = FeetechTuna()
    if not driver.openSerialPort(args.port, BAUD_RATE, servoFamily="sms_sts"):
        print(f"Could not open {args.port} at {BAUD_RATE} baud.", file=sys.stderr)
        return 1

    try:
        mover = MultiServoMover(driver)
        mover.discover()
        print_help()
        while True:
            try:
                if not run_command(mover, input("multi-servo> ")):
                    break
            except CommandError as error:
                print(f"Error: {error}")
            except KeyboardInterrupt:
                print("\nInterrupted. Type `exit` to close the connection.")
    except KeyboardInterrupt:
        print("\nClosing serial connection.")
    finally:
        driver.closeSerialPort()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
