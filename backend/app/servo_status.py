"""Read-only status from the supplied Feetech/ST3215 control library.

This module never enables torque or sends target positions. Confirmed manual
position commands use a separate hardware control module.
"""

from __future__ import annotations

import contextlib
import io
import os
import threading
import time

from serial.tools import list_ports


SERVO_IDS = range(1, 7)
BAUD_RATE = 1_000_000
SUPPORTED_USB_IDS = {(0x1A86, 0x55D3), (0x1A86, 0x7523)}
_scan_lock = threading.Lock()


def _port() -> tuple[str | None, str | None]:
    configured = os.environ.get("HANDOFF_SERVO_PORT", "").strip()
    if configured:
        return configured, None
    matches = sorted(
        port.device
        for port in list_ports.comports()
        if (port.vid, port.pid) in SUPPORTED_USB_IDS
        and (port.device.startswith("/dev/cu.") or port.device.startswith("/dev/ttyUSB"))
    )
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "More than one compatible USB serial controller was found. Set HANDOFF_SERVO_PORT."
    return None, None


def scan_servos() -> dict:
    """Ping and read IDs 1–6 once, then release the serial port."""
    result = {"connected": False, "port": None, "servos": [], "error": None, "checked_at": time.time()}
    if not _scan_lock.acquire(blocking=False):
        result["error"] = "A status check is already in progress."
        return result
    try:
        port, error = _port()
        result["port"] = port
        result["error"] = error
        if not port:
            return result

        from feetech_tuna import FeetechTuna
        from feetech_tuna.feetech_tuna import COMM_SUCCESS

        driver = FeetechTuna()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                if not driver.openSerialPort(port, BAUD_RATE, servoFamily="sms_sts"):
                    result["error"] = "Could not open the servo controller."
                    return result
                for servo_id in SERVO_IDS:
                    model, response, _ = driver.packetHandler.ping(servo_id)
                    if response != COMM_SUCCESS:
                        continue
                    position = driver.readReg(servo_id, 56)
                    voltage = driver.readReg(servo_id, 62)
                    temperature = driver.readReg(servo_id, 63)
                    moving = driver.readReg(servo_id, 66)
                    mode = driver.readReg(servo_id, 33)
                    torque = driver.readReg(servo_id, 40)
                    result["servos"].append(
                        {
                            "id": servo_id,
                            "model": model,
                            "position": position,
                            "voltage_v": None if voltage is None else voltage / 10,
                            "temperature_c": temperature,
                            "moving": None if moving is None else bool(moving),
                            "mode": mode,
                            "torque_enabled": None if torque is None else bool(torque),
                        }
                    )
            result["connected"] = len(result["servos"]) == len(SERVO_IDS) and all(
                servo[key] is not None
                for servo in result["servos"]
                for key in ("position", "voltage_v", "temperature_c", "moving")
            )
            if not result["connected"]:
                result["error"] = f"{len(result['servos'])} of 6 servos responded with status."
        except Exception as exc:
            result["error"] = f"Could not read the servo controller: {exc}"
        finally:
            if getattr(getattr(driver, "porthandler", None), "is_open", False):
                with contextlib.redirect_stdout(io.StringIO()):
                    driver.closeSerialPort()
        return result
    finally:
        _scan_lock.release()
