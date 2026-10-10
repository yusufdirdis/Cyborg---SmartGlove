"""Hardware command tests use a fake serial driver; no USB writes occur."""

import pytest
from fastapi.testclient import TestClient

from app import hardware_control as control
from app.hardware_boundary import ActuationApproval, gated_jog_count, gated_target_count
from app import main as main_module
from app.main import CalibrationInput, app


def measured_calibration():
    return CalibrationInput(open_count=950, closed_count=1050, min_count=900,
                            max_count=1100, direction="increasing", speed=20,
                            verified=False, jog_limits_verified=True)


class FakeDriver:
    calls = []

    def __init__(self):
        self.packetHandler = self
        self.porthandler = type("Port", (), {"is_open": False})()
        self.groupSyncWrite = self
        self.queued = []

    def openSerialPort(self, port, baudrate, servoFamily):
        self.calls.append(("open", port))
        self.porthandler.is_open = True
        return True

    def closeSerialPort(self):
        self.calls.append(("close",))
        self.porthandler.is_open = False

    def ping(self, servo_id):
        self.calls.append(("ping", servo_id))
        return 777, 0, 0

    def readReg(self, servo_id, address):
        self.calls.append(("read", servo_id, address))
        return {56: 1000, 42: 1000, 62: 124, 63: 35, 66: 0, 33: 0, 40: 1}[address]

    def WritePosEx(self, servo_id, target, speed, acceleration):
        self.calls.append(("move", servo_id, target, speed, acceleration))
        return 0, 0

    def clearParam(self):
        self.queued = []

    def SyncWritePosEx(self, servo_id, target, speed, acceleration):
        self.queued.append((servo_id, target, speed, acceleration))
        self.calls.append(("queue", servo_id, target, speed, acceleration))
        return True

    def txPacket(self):
        self.calls.append(("sync_packet", tuple(self.queued)))
        return 0

    def writeReg(self, servo_id, address, value):
        self.calls.append(("write", servo_id, address, value))
        return True


@pytest.fixture
def fake_controller(monkeypatch):
    FakeDriver.calls = []
    control.clear_pending()
    monkeypatch.setattr(control, "FeetechTuna", FakeDriver)
    monkeypatch.setattr(control, "_port", lambda: ("/dev/cu.fake", None))
    yield FakeDriver.calls
    control.clear_pending()


def test_preview_is_read_only_when_writes_disabled(fake_controller, monkeypatch):
    monkeypatch.delenv("HANDOFF_ENABLE_HARDWARE_WRITES", raising=False)
    preview = control.prepare_jog(1, 10, measured_calibration(), False)
    assert preview["start"] == 1000 and preview["target"] == 1010
    assert preview["can_execute"] is False and "token" not in preview
    assert not any(call[0] in ("move", "write") for call in fake_controller)


def test_one_use_confirmation_and_stale_calibration_block_writes(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = measured_calibration()
    preview = control.prepare_jog(1, -10, calibration, False)
    with pytest.raises(control.HardwareControlError, match="did not match"):
        control.execute_jog(preview["token"], "wrong", off_body=True,
                            power_cut_tested=True, cord_path_clear=True,
                            calibration=calibration, stopped=False)
    assert control.pending_servo_id(preview["token"]) is None

    preview = control.prepare_jog(1, -10, calibration, False)
    changed = calibration.model_copy(update={"min_count": 920})
    with pytest.raises(control.HardwareControlError, match="Calibration changed"):
        control.execute_jog(preview["token"], preview["phrase"], off_body=True,
                            power_cut_tested=True, cord_path_clear=True,
                            calibration=changed, stopped=False)
    assert not any(call[0] == "move" for call in fake_controller)

    preview = control.prepare_jog(1, -10, calibration, False)
    result = control.execute_jog(preview["token"], preview["phrase"], off_body=True,
                                 power_cut_tested=True, cord_path_clear=True,
                                 calibration=calibration, stopped=False)
    assert result["accepted"] is True
    assert [call for call in fake_controller if call[0] == "move"] == [("move", 1, 990, 50, 5)]
    with pytest.raises(control.HardwareControlError, match="expired"):
        control.execute_jog(preview["token"], preview["phrase"], off_body=True,
                            power_cut_tested=True, cord_path_clear=True,
                            calibration=calibration, stopped=False)


def test_unverified_or_out_of_range_direct_limits_never_prepare_write(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    unverified = measured_calibration().model_copy(update={"jog_limits_verified": False, "verified": True})
    assert control.prepare_jog(1, 10, unverified, False)["can_execute"] is False
    placeholder = CalibrationInput(open_count=2048, closed_count=2448, min_count=1800,
                                   max_count=2600, direction="increasing", speed=30,
                                   verified=False, jog_limits_verified=True)
    assert control.prepare_jog(1, 10, placeholder, False)["can_execute"] is False
    assert not any(call[0] == "move" for call in fake_controller)


def test_measured_direct_limits_do_not_require_gesture_endpoints(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    direct_only = CalibrationInput(open_count=2048, closed_count=2448, min_count=0,
                                   max_count=4000, direction="increasing", speed=30,
                                   verified=False, jog_limits_verified=True)
    preview = control.prepare_jog(1, 10, direct_only, False)
    assert preview["can_execute"] is True and preview["target"] == 1010
    assert not any(call[0] == "move" for call in fake_controller)


def test_jog_gate_rejects_large_or_out_of_range_steps():
    calibration = measured_calibration()
    approval = ActuationApproval(True, True)
    assert gated_jog_count(1000, 1010, calibration, approval, False) == 1010
    with pytest.raises(PermissionError):
        gated_jog_count(1000, 1050, calibration, approval, False)
    with pytest.raises(PermissionError):
        gated_jog_count(1095, 1105, calibration, approval, False)
    with pytest.raises(PermissionError):
        gated_jog_count(1000, 1010, calibration, ActuationApproval(True, False), False)


def test_stop_pressed_during_recheck_blocks_write(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = measured_calibration()
    preview = control.prepare_jog(1, 10, calibration, False)
    with pytest.raises(control.HardwareControlError, match="stop was pressed"):
        control.execute_jog(preview["token"], preview["phrase"], off_body=True,
                            power_cut_tested=True, cord_path_clear=True,
                            calibration=calibration, stopped=False, is_stopped=lambda: True)
    assert not any(call[0] == "move" for call in fake_controller)


def test_software_stop_sends_torque_off_when_hardware_writes_enabled(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    result = control.disable_torque()
    assert result["attempted"] is True and result["failed_ids"] == []
    assert [call for call in fake_controller if call[0] == "write"] == [
        ("write", servo_id, 40, 0) for servo_id in range(1, 7)
    ]


def test_api_rejects_nonlocal_hardware_request(monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    with TestClient(app) as client:
        result = client.post("/api/hardware/jog/preview", json={"servo_id": 1, "delta": 10},
                             headers={"Origin": "https://other.example"})
        stop = client.post("/api/control/stop", headers={"Origin": "https://other.example"})
    assert result.status_code == 403
    assert stop.status_code == 403


def test_position_preview_is_read_only_and_respects_write_lock(fake_controller, monkeypatch):
    monkeypatch.delenv("HANDOFF_ENABLE_HARDWARE_WRITES", raising=False)
    calibration = CalibrationInput(open_count=2048, closed_count=2448, min_count=0,
                                   max_count=4000, direction="increasing", speed=30,
                                   verified=False, jog_limits_verified=True)
    preview = control.prepare_position(1, 3500, 250, calibration, False)
    assert preview["start"] == 1000 and preview["target"] == 3500
    assert preview["can_execute"] is False and "token" not in preview
    assert not any(call[0] in ("move", "write") for call in fake_controller)


def test_position_command_is_single_use_and_speed_is_passed_to_driver(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = CalibrationInput(open_count=2048, closed_count=2448, min_count=0,
                                   max_count=4000, direction="increasing", speed=30,
                                   verified=False, jog_limits_verified=True)
    preview = control.prepare_position(1, 3500, 400, calibration, False)
    assert preview["can_execute"] is True
    assert not any(call[0] == "move" for call in fake_controller)
    with pytest.raises(control.HardwareControlError, match="power cutoff"):
        control.execute_position(preview["token"], preview["phrase"], off_body=True,
                                 power_cut_tested=False, cord_path_clear=True,
                                 calibration=calibration, stopped=False)
    assert not any(call[0] == "move" for call in fake_controller)
    preview = control.prepare_position(1, 3500, 400, calibration, False)
    result = control.execute_position(preview["token"], preview["phrase"], off_body=True,
                                      power_cut_tested=True, cord_path_clear=True,
                                      calibration=calibration, stopped=False)
    assert result["accepted"] is True
    assert [call for call in fake_controller if call[0] == "move"] == [("move", 1, 3500, 400, 50)]
    with pytest.raises(control.HardwareControlError, match="expired"):
        control.execute_position(preview["token"], preview["phrase"], off_body=True,
                                 power_cut_tested=True, cord_path_clear=True,
                                 calibration=calibration, stopped=False)


def test_position_move_rechecks_calibration_stop_and_target(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = measured_calibration()
    assert control.prepare_position(1, 1200, 250, calibration, False)["can_execute"] is False
    preview = control.prepare_position(1, 1050, 250, calibration, False)
    changed = calibration.model_copy(update={"max_count": 1200})
    with pytest.raises(control.HardwareControlError, match="Calibration changed"):
        control.execute_position(preview["token"], preview["phrase"], off_body=True,
                                 power_cut_tested=True, cord_path_clear=True,
                                 calibration=changed, stopped=False)
    preview = control.prepare_position(1, 1050, 250, calibration, False)
    with pytest.raises(control.HardwareControlError, match="stop was pressed"):
        control.execute_position(preview["token"], preview["phrase"], off_body=True,
                                 power_cut_tested=True, cord_path_clear=True,
                                 calibration=calibration, stopped=False, is_stopped=lambda: True)
    assert not any(call[0] == "move" for call in fake_controller)
    with pytest.raises(control.HardwareControlError, match="speed"):
        control.prepare_position(1, 1050, 2401, calibration, False)
    approval = ActuationApproval(True, True)
    assert gated_target_count(1000, 1050, calibration, approval, False) == 1050
    with pytest.raises(PermissionError):
        gated_target_count(1000, 1200, calibration, approval, False)


def test_position_route_rejects_nonlocal_and_invalid_speed(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    with TestClient(app) as client:
        foreign = client.post("/api/hardware/position/preview", json={"servo_id": 1, "target": 3500, "speed": 250},
                              headers={"Origin": "https://other.example"})
        invalid = client.post("/api/hardware/position/preview", json={"servo_id": 1, "target": 3500, "speed": 2401})
    assert foreign.status_code == 403
    assert invalid.status_code == 422
    assert not any(call[0] == "move" for call in fake_controller)


def test_group_move_sends_one_packet_after_all_checks(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = measured_calibration()
    moves = [(1, 1050, 2400), (2, 1060, 1200)]
    calibrations = {1: calibration, 2: calibration}
    preview = control.prepare_group(moves, calibrations, False)
    assert preview["can_execute"] is True
    assert not any(call[0] in ("queue", "sync_packet") for call in fake_controller)
    result = control.execute_group(preview["token"], preview["phrase"], off_body=True,
                                   power_cut_tested=True, cord_path_clear=True,
                                   calibrations=calibrations, stopped=False)
    assert result["accepted"] is True
    assert [call for call in fake_controller if call[0] == "sync_packet"] == [
        ("sync_packet", ((1, 1050, 2400, 50), (2, 1060, 1200, 50)))
    ]
    assert not any(call[0] == "move" for call in fake_controller)
    with pytest.raises(control.HardwareControlError, match="expired"):
        control.execute_group(preview["token"], preview["phrase"], off_body=True,
                              power_cut_tested=True, cord_path_clear=True,
                              calibrations=calibrations, stopped=False)


def test_group_move_aborts_without_any_packet_on_changed_calibration_or_stop(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibration = measured_calibration()
    moves = [(1, 1050, 2400), (2, 1060, 1200)]
    calibrations = {1: calibration, 2: calibration}
    preview = control.prepare_group(moves, calibrations, False)
    changed = {1: calibration, 2: calibration.model_copy(update={"max_count": 1200})}
    with pytest.raises(control.HardwareControlError, match="Calibration changed"):
        control.execute_group(preview["token"], preview["phrase"], off_body=True,
                              power_cut_tested=True, cord_path_clear=True,
                              calibrations=changed, stopped=False)
    preview = control.prepare_group(moves, calibrations, False)
    with pytest.raises(control.HardwareControlError, match="stop was pressed"):
        control.execute_group(preview["token"], preview["phrase"], off_body=True,
                              power_cut_tested=True, cord_path_clear=True,
                              calibrations=calibrations, stopped=False, is_stopped=lambda: True)
    assert not any(call[0] in ("queue", "sync_packet") for call in fake_controller)


def test_group_api_previews_then_sends_one_packet(fake_controller, monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "group.sqlite3")
    with TestClient(app) as client:
        for servo_id in (1, 2):
            saved = measured_calibration().model_dump()
            assert client.put(f"/api/calibration/{servo_id}", json=saved).status_code == 200
        payload = {"moves": [{"servo_id": 1, "target": 1050, "speed": 2400},
                             {"servo_id": 2, "target": 1060, "speed": 1200}]}
        preview = client.post("/api/hardware/group/preview", json=payload)
        assert preview.status_code == 200 and preview.json()["can_execute"] is True
        assert not any(call[0] == "sync_packet" for call in fake_controller)
        confirmation = {"token": preview.json()["token"], "phrase": preview.json()["phrase"],
                        "off_body": True, "power_cut_tested": True, "cord_path_clear": True}
        sent = client.post("/api/hardware/group/execute", json=confirmation)
        assert sent.status_code == 200 and sent.json()["accepted"] is True
    assert len([call for call in fake_controller if call[0] == "sync_packet"]) == 1


def test_live_edits_share_one_packet_without_a_confirmation_token(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibrations = {1: measured_calibration(), 2: measured_calibration()}
    result = control.move_live([(1, 1050, 2400), (2, 1060, 1200)], calibrations, False)
    assert result["accepted"] is True
    assert [call for call in fake_controller if call[0] == "sync_packet"] == [
        ("sync_packet", ((1, 1050, 2400, 50), (2, 1060, 1200, 50)))
    ]


def test_live_edit_blocks_unmeasured_target_and_stop_before_packet(fake_controller, monkeypatch):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    calibrations = {1: measured_calibration(), 2: measured_calibration()}
    with pytest.raises(control.HardwareControlError, match="outside"):
        control.move_live([(1, 1050, 2400), (2, 2000, 1200)], calibrations, False)
    with pytest.raises(control.HardwareControlError, match="stop"):
        control.move_live([(1, 1050, 2400)], calibrations, False, is_stopped=lambda: True)
    assert not any(call[0] == "sync_packet" for call in fake_controller)


def test_live_api_sends_selected_targets_without_confirmation(fake_controller, monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ENABLE_HARDWARE_WRITES", "1")
    monkeypatch.setattr(main_module, "DB_PATH", tmp_path / "live.sqlite3")
    with TestClient(app) as client:
        for servo_id in (1, 2):
            assert client.put(f"/api/calibration/{servo_id}", json=measured_calibration().model_dump()).status_code == 200
        payload = {"moves": [{"servo_id": 1, "target": 1050, "speed": 2400},
                             {"servo_id": 2, "target": 1060, "speed": 1200}]}
        response = client.post("/api/hardware/live", json=payload)
        assert response.status_code == 200 and response.json()["accepted"] is True
        foreign = client.post("/api/hardware/live", json=payload, headers={"Origin": "https://other.example"})
        assert foreign.status_code == 403
    assert len([call for call in fake_controller if call[0] == "sync_packet"]) == 1
