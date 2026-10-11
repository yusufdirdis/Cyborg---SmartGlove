"""LCD bridge: state priority, truth rules, and no motor access."""
from app import lcd_bridge
from app.lcd_bridge import compute_state


def servos(moving_ids=(), torque=False):
    return [{"id": i, "moving": i in moving_ids, "torque_enabled": torque} for i in range(1, 7)]


def state(**over):
    base = dict(stopped=False, writes=True, reading={"connected": True, "servos": servos()}, reading_age=1.0,
                busy=False, error=None, now=100.0)
    base.update(over)
    return compute_state(**base)


def test_priority_and_truth():
    assert state(stopped=True) == "STATE STOPPED"
    assert state(writes=False) == "STATE LOCKED"
    assert state(reading=None) == "STATE OFFLINE"
    assert state(reading={"connected": False, "servos": []}) == "STATE OFFLINE"
    assert state(reading_age=46) == "STATE UNKNOWN"
    assert state(busy=True) == "STATE ALIGNING"
    assert state(error=(3, 95.0)) == "STATE NOT_ENABLED 3"
    assert state(error=(3, 80.0)) == "STATE READY"  # error older than 10 s is dropped


def test_settle_count_and_torque_on():
    assert state(reading={"connected": True, "servos": servos(moving_ids=(3, 5))}) == "STATE SETTLING 4"
    assert state() == "STATE READY"
    assert state(reading={"connected": True, "servos": servos(torque=True)}) == "STATE TORQUE_ON"


def test_torque_error_parses_servo_id():
    lcd_bridge.torque_started()
    lcd_bridge.torque_finished("Servo 3 is moving; wait for it to stop before enabling torque.")
    assert lcd_bridge._torque_error[0] == 3
    lcd_bridge.torque_finished(None)


def test_bridge_is_off_without_a_port(monkeypatch):
    monkeypatch.delenv("HANDOFF_LCD_PORT", raising=False)
    assert lcd_bridge.enabled() is False


def test_bridge_never_touches_motor_writes():
    source = open(lcd_bridge.__file__).read()
    for forbidden in ("enable_torque(", "move_live", "run_macro", "execute_", "disable_torque", "scan_servos"):
        assert forbidden not in source
