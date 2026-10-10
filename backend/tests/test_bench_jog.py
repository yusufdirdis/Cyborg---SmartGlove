import pytest

from app.bench_jog import BenchJogError, run_jog


class FakePacket:
    def __init__(self, driver):
        self.driver = driver

    def ping(self, servo_id):
        return 777, 0, 0

    def WritePosEx(self, servo_id, position, speed, acceleration):
        self.driver.writes.append((servo_id, position, speed, acceleration))
        self.driver.position = position
        self.driver.goal_position = position
        return 0, 0


class FakeDriver:
    def __init__(self):
        self.packetHandler = FakePacket(self)
        self.position = 1000
        self.goal_position = 1000
        self.mode = 0
        self.writes = []

    def readReg(self, servo_id, address):
        return {56: self.position, 42: self.goal_position, 62: 124, 63: 35, 66: 0, 33: self.mode, 40: 1}[address]


def test_bench_jog_is_read_only_by_default_and_requires_exact_phrase():
    driver = FakeDriver()
    assert run_jog(driver, 1, 10, False) is None
    assert driver.writes == []
    assert run_jog(driver, 1, 10, True, input_fn=lambda _: "yes") is None
    assert driver.writes == []
    result = run_jog(driver, 1, 10, True, input_fn=lambda _: "JOG 1 +10 TO 1010")
    assert result.position == 1010
    assert driver.writes == [(1, 1010, 50, 5)]


def test_bench_jog_rejects_unsafe_mode_and_changed_start():
    driver = FakeDriver()
    driver.mode = 1
    with pytest.raises(BenchJogError, match="position mode"):
        run_jog(driver, 1, 10, True, input_fn=lambda _: "JOG 1 +10 TO 1010")
    assert driver.writes == []

    driver.mode = 0
    def change_during_confirmation(_):
        driver.position = 1005
        return "JOG 1 +10 TO 1010"

    with pytest.raises(BenchJogError, match="changed after confirmation"):
        run_jog(driver, 1, 10, True, input_fn=change_during_confirmation)
    assert driver.writes == []


def test_bench_jog_rejects_stale_goal_before_any_write():
    driver = FakeDriver()
    driver.goal_position = 0
    with pytest.raises(BenchJogError, match="Stored target is far"):
        run_jog(driver, 1, 10, True, input_fn=lambda _: "JOG 1 +10 TO 1010")
    assert driver.writes == []
