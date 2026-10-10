#!/usr/bin/env python3
"""Basic interactive communication and movement checks for ST3215 servos 1–8."""

import argparse
import contextlib
import io
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

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

SERVO_1 = 1
SERVO_2 = 2
SERVO_3 = 3
SERVO_4 = 4
SERVO_5 = 5
SERVO_6 = 6
SERVO_7 = 7
SERVO_8 = 8
SERVO_IDS = (SERVO_1, SERVO_2, SERVO_3, SERVO_4, SERVO_5, SERVO_6, SERVO_7, SERVO_8)

# STS/SMS control table addresses used by the existing Feetech-tuna driver.
REG_PRESENT_POSITION = 56
REG_PRESENT_VOLTAGE = 62
REG_PRESENT_TEMPERATURE = 63
REG_MOVING = 66


@dataclass
class ServoSnapshot:
    servo_id: int
    found: bool
    position: Optional[int] = None
    voltage_v: Optional[float] = None
    temperature_c: Optional[int] = None
    moving: Optional[bool] = None


class ServoCommandError(Exception):
    """A command could not be completed using the current bus state."""


class SixServoTest:
    def __init__(self, driver: FeetechTuna, sleep_fn=time.sleep):
        self.driver = driver
        self.sleep = sleep_fn
        self.found_ids: List[int] = []
        self.start_positions: Dict[int, int] = {}
        self.pending_targets: Dict[int, int] = {}

    def _read_register(self, servo_id: int, address: int) -> Optional[int]:
        # The existing wrapper prints per-register messages; keep this CLI's
        # discovery/status output as one simple table instead.
        with contextlib.redirect_stdout(io.StringIO()):
            value = self.driver.readReg(servo_id, address)
        return None if value is None else int(value)

    def _read_snapshot(self, servo_id: int) -> ServoSnapshot:
        position = self._read_register(servo_id, REG_PRESENT_POSITION)
        voltage_raw = self._read_register(servo_id, REG_PRESENT_VOLTAGE)
        temperature = self._read_register(servo_id, REG_PRESENT_TEMPERATURE)
        moving = self._read_register(servo_id, REG_MOVING)
        return ServoSnapshot(
            servo_id=servo_id,
            found=True,
            position=position,
            voltage_v=None if voltage_raw is None else voltage_raw / 10.0,
            temperature_c=temperature,
            moving=None if moving is None else bool(moving),
        )

    def discover(self) -> None:
        """Ping IDs 1–8 and read telemetry without sending any writes."""
        self.found_ids.clear()
        self.start_positions.clear()
        print("Discovering servo IDs 1–8 (read only)...")
        rows: Dict[int, ServoSnapshot] = {}
        for servo_id in SERVO_IDS:
            _model, result, _error = self.driver.packetHandler.ping(servo_id)
            if result == COMM_SUCCESS:
                self.found_ids.append(servo_id)
                snapshot = self._read_snapshot(servo_id)
                rows[servo_id] = snapshot
                if snapshot.position is not None:
                    self.start_positions[servo_id] = snapshot.position
            else:
                rows[servo_id] = ServoSnapshot(servo_id=servo_id, found=False)
        self._print_table(rows)
        print(f"Saved startup positions for {len(self.start_positions)} servo(s).")

    @staticmethod
    def _print_table(rows: Dict[int, ServoSnapshot]) -> None:
        print("ID | Status     | Position | Voltage | Temp | Moving")
        print("---+------------+----------+---------+------+-------")
        for servo_id in sorted(rows):
            snapshot = rows[servo_id]
            if not snapshot.found:
                print(f"{servo_id:>2} | NOT FOUND  |        - |       - |    - |      -")
                continue
            position = "-" if snapshot.position is None else str(snapshot.position)
            voltage = "-" if snapshot.voltage_v is None else f"{snapshot.voltage_v:.1f} V"
            temperature = "-" if snapshot.temperature_c is None else f"{snapshot.temperature_c} C"
            moving = "-" if snapshot.moving is None else ("YES" if snapshot.moving else "NO")
            print(
                f"{servo_id:>2} | FOUND      | {position:>8} | {voltage:>7} | "
                f"{temperature:>4} | {moving:>6}"
            )

    def show_status(self, servo_id: Optional[int] = None) -> None:
        selected = [servo_id] if servo_id is not None else list(SERVO_IDS)
        rows: Dict[int, ServoSnapshot] = {}
        for current_id in selected:
            if current_id not in self.found_ids:
                rows[current_id] = ServoSnapshot(servo_id=current_id, found=False)
            else:
                rows[current_id] = self._read_snapshot(current_id)
        self._print_table(rows)

    def _require_found(self, servo_id: int) -> None:
        if servo_id not in SERVO_IDS:
            raise ServoCommandError("Servo ID must be from 1 through 8.")
        if servo_id not in self.found_ids:
            raise ServoCommandError(f"Servo {servo_id} was not found during discovery.")

    @staticmethod
    def _validate_position(position: int) -> None:
        if not POSITION_MIN <= position <= POSITION_MAX:
            raise ServoCommandError(
                f"Position {position} is outside the STS position range "
                f"{POSITION_MIN}–{POSITION_MAX}."
            )

    def _read_position(self, servo_id: int) -> int:
        self._require_found(servo_id)
        position = self._read_register(servo_id, REG_PRESENT_POSITION)
        if position is None:
            raise ServoCommandError(f"Could not read the position of Servo {servo_id}.")
        return position

    def _wait_for_targets(self, targets: Dict[int, int]) -> Dict[int, int]:
        """Pause at least one second, then wait for the requested positions."""
        deadline = time.monotonic() + MOVE_TIMEOUT_SECONDS
        self.sleep(MOVE_PAUSE_SECONDS)
        while True:
            positions: Dict[int, int] = {}
            all_stopped_at_target = True
            for servo_id, target in targets.items():
                position = self._read_position(servo_id)
                moving = self._read_register(servo_id, REG_MOVING)
                if moving is None:
                    raise ServoCommandError(f"Could not read Servo {servo_id}'s moving state.")
                positions[servo_id] = position
                if moving != 0 or abs(position - target) > POSITION_TOLERANCE:
                    all_stopped_at_target = False

            if all_stopped_at_target:
                return positions

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                details = ", ".join(
                    f"ID {servo_id}: {positions[servo_id]} (target {targets[servo_id]})"
                    for servo_id in sorted(targets)
                )
                raise ServoCommandError(
                    f"Timed out waiting for servos to reach their targets: {details}."
                )
            self.sleep(min(MOVE_POLL_SECONDS, remaining))

    def _move_to_from(self, servo_id: int, target: int, start: int) -> int:
        self._require_found(servo_id)
        self._validate_position(target)
        print(f"Servo {servo_id} | starting position={start} | target position={target}")
        result, error = self.driver.packetHandler.WritePosEx(
            servo_id, target, SERVO_SPEED, SERVO_ACCELERATION
        )
        if result != COMM_SUCCESS:
            detail = self.driver.packetHandler.getTxRxResult(result)
            raise ServoCommandError(
                f"Could not command Servo {servo_id}: {detail or result} (servo error {error})."
            )
        end = self._wait_for_targets({servo_id: target})[servo_id]
        print(f"Servo {servo_id} | ending position={end}")
        return end

    def move_to(self, servo_id: int, target: int) -> int:
        start = self._read_position(servo_id)
        return self._move_to_from(servo_id, target, start)

    def move_by(self, servo_id: int, offset: int) -> int:
        start = self._read_position(servo_id)
        return self._move_to_from(servo_id, start + offset, start)

    def home(self, servo_id: int) -> int:
        self._require_found(servo_id)
        if servo_id not in self.start_positions:
            raise ServoCommandError(f"No startup position was saved for Servo {servo_id}.")
        return self.move_to(servo_id, self.start_positions[servo_id])

    def test_one(self, servo_id: int) -> int:
        self._require_found(servo_id)
        if servo_id not in self.start_positions:
            raise ServoCommandError(f"No startup position was saved for Servo {servo_id}.")
        start = self.start_positions[servo_id]
        self._validate_position(start + 50)
        self._validate_position(start - 50)
        print(f"Testing Servo {servo_id} from saved startup position {start}.")
        self.move_to(servo_id, start)
        self.move_to(servo_id, start + 50)
        self.move_to(servo_id, start)
        self.move_to(servo_id, start - 50)
        end = self.move_to(servo_id, start)
        return end

    def test_all(self) -> None:
        for servo_id in SERVO_IDS:
            if servo_id not in self.found_ids:
                print(f"Skipping Servo {servo_id}: not found.")
                continue
            print(f"\nTESTING SERVO {servo_id}")
            end = self.test_one(servo_id)
            print(f"Servo {servo_id} test complete | final position={end}")

    def _sync_move(self, targets: Dict[int, int], starts: Dict[int, int]) -> Dict[int, int]:
        for servo_id, target in targets.items():
            self._require_found(servo_id)
            self._validate_position(target)

        print("Synchronized targets:")
        for servo_id in sorted(targets):
            print(
                f"Servo {servo_id} | starting position={starts[servo_id]} | "
                f"target position={targets[servo_id]}"
            )

        handler = self.driver.packetHandler
        handler.groupSyncWrite.clearParam()
        try:
            for servo_id in sorted(targets):
                added = handler.SyncWritePosEx(
                    servo_id,
                    targets[servo_id],
                    SERVO_SPEED,
                    SERVO_ACCELERATION,
                )
                if not added:
                    raise ServoCommandError(f"Could not queue Servo {servo_id} for sync write.")
            result = handler.groupSyncWrite.txPacket()
            if result != COMM_SUCCESS:
                detail = handler.getTxRxResult(result)
                raise ServoCommandError(f"Synchronized position write failed: {detail or result}.")
        finally:
            handler.groupSyncWrite.clearParam()

        ends = self._wait_for_targets(targets)
        for servo_id in sorted(targets):
            print(f"Servo {servo_id} | ending position={ends[servo_id]}")
        return ends

    def set_target(self, servo_id: int, position: int) -> None:
        self._require_found(servo_id)
        self._validate_position(position)
        self.pending_targets[servo_id] = position
        print(f"Queued Servo {servo_id} target position={position}.")

    def show_targets(self) -> None:
        if not self.pending_targets:
            print("No targets are queued.")
            return
        print("Queued targets (type `send` to move them together):")
        for servo_id, position in sorted(self.pending_targets.items()):
            print(f"Servo {servo_id} | target position={position}")

    def send_targets(self, targets: Dict[int, int]) -> None:
        if not targets:
            raise ServoCommandError("Specify at least one servo and target position.")
        starts = {servo_id: self._read_position(servo_id) for servo_id in sorted(targets)}
        self._sync_move(targets, starts)

    def send_pending_targets(self) -> None:
        targets = dict(self.pending_targets)
        self.send_targets(targets)
        self.pending_targets.clear()

    def sync_move_by(self, offset: int) -> None:
        if not self.found_ids:
            raise ServoCommandError("No detected servos are available for synchronized movement.")
        starts = {servo_id: self._read_position(servo_id) for servo_id in self.found_ids}
        targets = {servo_id: position + offset for servo_id, position in starts.items()}
        self._sync_move(targets, starts)

    def sync_goto(self, target: int) -> None:
        self._require_all_servos()
        starts = {servo_id: self._read_position(servo_id) for servo_id in SERVO_IDS}
        self._sync_move(
            {servo_id: target for servo_id in SERVO_IDS},
            starts,
        )

    def _require_all_servos(self) -> None:
        if tuple(self.found_ids) != SERVO_IDS:
            missing = [servo_id for servo_id in SERVO_IDS if servo_id not in self.found_ids]
            raise ServoCommandError(
                "This command requires all eight servos to be found; missing IDs: "
                + ", ".join(str(servo_id) for servo_id in missing)
            )

    def sync_test(self) -> None:
        self._require_all_servos()
        starts = {servo_id: self._read_position(servo_id) for servo_id in SERVO_IDS}
        for position in starts.values():
            self._validate_position(position + 50)
            self._validate_position(position - 50)
        self._sync_move(
            {servo_id: position + 50 for servo_id, position in starts.items()},
            starts,
        )
        self._sync_move(starts, {servo_id: self._read_position(servo_id) for servo_id in SERVO_IDS})
        self._sync_move(
            {servo_id: position - 50 for servo_id, position in starts.items()},
            {servo_id: self._read_position(servo_id) for servo_id in SERVO_IDS},
        )
        ends = self._sync_move(
            starts,
            {servo_id: self._read_position(servo_id) for servo_id in SERVO_IDS},
        )
        print("Synchronized test complete. Final positions:")
        for servo_id in SERVO_IDS:
            print(f"Servo {servo_id} | final position={ends[servo_id]}")


def parse_integer(value: str, description: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ServoCommandError(f"{description} must be an integer.") from exc


def print_help() -> None:
    print(
        "Commands:\n"
        "  list                       Refresh status for IDs 1–8\n"
        "  status                     Show status for IDs 1–8\n"
        "  status <id>                Show one servo's status\n"
        "  move <id> <offset>         Move from its current position by counts\n"
        "  goto <id> <position>       Move to a raw position\n"
        "  home <id>                  Return to its startup position\n"
        "  test <id>                  Test one servo around its startup position\n"
        "  test-all                   Test found servos individually, in ID order\n"
        "  sync-test                  Run the synchronized eight-servo test\n"
        "  sync-move <offset>         Move every detected servo by counts together\n"
        "  sync-goto <position>       Send one raw target to all eight servos together\n"
        "  set <id> <position>        Queue one servo's target without moving yet\n"
        "  targets                    Show queued targets\n"
        "  send                       Move all queued servos together\n"
        "  sync <id=pos> [...]        Send different targets together immediately\n"
        "  clear                      Discard queued targets without moving\n"
        "  help                       Show these commands\n"
        "  exit                       Close the connection and quit"
    )


def run_command(tester: SixServoTest, line: str) -> bool:
    parts = line.split()
    if not parts:
        return True
    command = parts[0].lower()
    if command in ("exit", "quit"):
        return False
    if command == "help" and len(parts) == 1:
        print_help()
    elif command == "list" and len(parts) == 1:
        tester.show_status()
    elif command == "status" and len(parts) == 1:
        tester.show_status()
    elif command == "status" and len(parts) == 2:
        servo_id = parse_integer(parts[1], "Servo ID")
        if servo_id not in SERVO_IDS:
            raise ServoCommandError("Servo ID must be from 1 through 8.")
        tester.show_status(servo_id)
    elif command == "move" and len(parts) == 3:
        tester.move_by(parse_integer(parts[1], "Servo ID"), parse_integer(parts[2], "Offset"))
    elif command == "goto" and len(parts) == 3:
        tester.move_to(parse_integer(parts[1], "Servo ID"), parse_integer(parts[2], "Position"))
    elif command == "home" and len(parts) == 2:
        tester.home(parse_integer(parts[1], "Servo ID"))
    elif command == "test" and len(parts) == 2:
        tester.test_one(parse_integer(parts[1], "Servo ID"))
    elif command == "test-all" and len(parts) == 1:
        tester.test_all()
    elif command == "sync-test" and len(parts) == 1:
        tester.sync_test()
    elif command == "sync-move" and len(parts) == 2:
        tester.sync_move_by(parse_integer(parts[1], "Offset"))
    elif command == "sync-goto" and len(parts) == 2:
        tester.sync_goto(parse_integer(parts[1], "Position"))
    elif command == "set" and len(parts) == 3:
        tester.set_target(
            parse_integer(parts[1], "Servo ID"),
            parse_integer(parts[2], "Position"),
        )
    elif command == "targets" and len(parts) == 1:
        tester.show_targets()
    elif command == "send" and len(parts) == 1:
        tester.send_pending_targets()
    elif command == "sync" and len(parts) >= 2:
        tester.send_targets(parse_position_targets(parts[1:]))
    elif command == "clear" and len(parts) == 1:
        tester.pending_targets.clear()
        print("Queued targets cleared; nothing moved.")
    else:
        print("Unknown command or wrong number of arguments. Type `help` for commands.")
    return True


def parse_position_targets(tokens: Iterable[str]) -> Dict[int, int]:
    targets: Dict[int, int] = {}
    for token in tokens:
        try:
            servo_text, position_text = token.split("=", 1)
            servo_id = int(servo_text)
            position = int(position_text)
        except ValueError as exc:
            raise ServoCommandError(
                f"Invalid target `{token}`. Use ID=POSITION, for example 2=2048."
            ) from exc
        if servo_id in targets:
            raise ServoCommandError(f"Servo {servo_id} appears more than once in this command.")
        targets[servo_id] = position
    return targets


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discover and test Feetech ST3215 servos with IDs 1 through 8."
    )
    parser.add_argument("port", help="Serial port, for example /dev/cu.usbmodem5B901507591")
    args = parser.parse_args(argv)

    driver = FeetechTuna()
    if not driver.openSerialPort(args.port, BAUD_RATE, servoFamily="sms_sts"):
        print(f"Could not open {args.port} at {BAUD_RATE} baud.", file=sys.stderr)
        return 1

    try:
        tester = SixServoTest(driver)
        tester.discover()
        print_help()
        while True:
            try:
                line = input("servo-test> ")
                if not run_command(tester, line):
                    break
            except ServoCommandError as error:
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
