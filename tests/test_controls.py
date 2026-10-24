"""Generic live controls: fixed one-register steps, each checked, with cleanup on failure or stop."""

import threading

import pytest

from packkeeper import drivers, modbus
from packkeeper.controls import (
    CONFIRMED, NOT_CONFIRMED, NOT_SENT, UNVERIFIED, Check, Control, Step, control_frames,
)
from packkeeper.device import PackError
from packkeeper.service import run_control
from packkeeper.session import SessionLog
from packkeeper.transport import NoReply
from toy_driver import Toy

REG_A, REG_B, REG_C = 0x50, 0x51, 0x52


class ToyWithControls(Toy):
    control_registers = frozenset({REG_A, REG_B})


class ToyWritesOff(ToyWithControls):
    def check_device(self, device, block):
        return "a test refusal"


def toy(driver=None):
    driver = driver or ToyWithControls()
    sim = driver.simulator()
    return sim, driver.open_device(sim, SessionLog())


def holds(register, value):
    def check(dev, acked):
        v = dev.transport.image[register]
        return Check(v == value, f"0x{register:02X} reads {v}")
    return check


def control(*steps, cleanup=()):
    return Control("demo", "Demo", "a test control", steps=tuple(steps), cleanup=tuple(cleanup))


def test_frames_are_the_fixed_steps_then_the_cleanup():
    sim, dev = toy()
    c = control(Step("A on", REG_A, 1, holds(REG_A, 1)), cleanup=[Step("A off", REG_A, 0, holds(REG_A, 0))])
    assert control_frames(dev, c) == [
        ("A on", modbus.write_request(7, REG_A, [1])),
        ("only if stopped or a step fails: A off", modbus.write_request(7, REG_A, [0])),
    ]


def test_every_step_confirmed():
    sim, dev = toy()
    seen = []
    res = run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1)), Step("B", REG_B, 2, holds(REG_B, 2))),
                      progress=seen.append)
    assert res.ok and res.stopped is None
    assert [r.status for r in res.results] == [CONFIRMED, CONFIRMED] and seen == res.results
    assert sim.writes == [(REG_A, [1]), (REG_B, [2])]
    assert any("control demo confirmed" in line for line in dev.log.lines)


def test_stops_at_the_first_unconfirmed_step_and_runs_cleanup():
    sim, dev = toy()
    c = control(Step("A", REG_A, 1, holds(REG_A, 9)), Step("B", REG_B, 2, holds(REG_B, 2)),
                cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert not res.ok and res.stopped.startswith("A: NOT confirmed")
    assert [(r.step.label, r.status, r.cleanup) for r in res.results] == [
        ("A", NOT_CONFIRMED, False), ("undo A", CONFIRMED, True)]
    assert sim.writes == [(REG_A, [1]), (REG_A, [0])]


def test_nothing_is_sent_while_writes_are_off():
    sim, dev = toy(ToyWritesOff())
    with pytest.raises(PackError, match="a test refusal"):
        run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1))))
    assert sim.writes == []


def test_send_control_refuses_a_register_the_driver_does_not_list():
    sim, dev = toy()
    with pytest.raises(PackError, match="not a control register"):
        dev.send_control(Step("C", REG_C, 1, holds(REG_C, 1)))
    assert sim.writes == []


def test_a_refused_step_is_not_sent_and_needs_no_cleanup():
    sim, dev = toy()
    c = control(Step("C", REG_C, 1, holds(REG_C, 1)), cleanup=[Step("undo", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert [r.status for r in res.results] == [NOT_SENT] and sim.writes == []


def test_stop_after_a_step_runs_cleanup():
    sim, dev = toy()
    stop = threading.Event()

    def check_and_stop(dev, acked):
        stop.set()  # the user clicks Stop while step A is being checked
        return holds(REG_A, 1)(dev, acked)

    c = control(Step("A", REG_A, 1, check_and_stop), Step("B", REG_B, 2, holds(REG_B, 2)),
                cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c, stop=stop)
    assert res.stopped == "stopped by the user" and not res.ok
    assert sim.writes == [(REG_A, [1]), (REG_A, [0])]


def test_stop_before_anything_was_sent_sends_nothing():
    sim, dev = toy()
    stop = threading.Event()
    stop.set()
    res = run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1)),
                                   cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))]), stop=stop)
    assert res.stopped == "stopped by the user" and res.results == [] and sim.writes == []


def test_no_ack_and_no_read_back_is_unverified():
    sim, dev = toy()
    sim.drop_acks = 1

    def no_read_back(dev, acked):
        raise NoReply("silent")

    res = run_control(dev, control(Step("A", REG_A, 1, no_read_back)))
    (r,) = res.results
    assert r.status == UNVERIFIED and "(no ack)" in r.detail and "read-back failed" in r.detail
    assert sim.writes == [(REG_A, [1])]


def test_ctrl_c_runs_cleanup():
    sim, dev = toy()

    def interrupted(dev, acked):
        raise KeyboardInterrupt

    c = control(Step("A", REG_A, 1, interrupted), cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert res.stopped == "interrupted" and sim.writes == [(REG_A, [1]), (REG_A, [0])]
    assert res.results[-1].cleanup and res.results[-1].status == CONFIRMED


def test_eg4_and_jk_have_no_controls():
    for name in ("eg4-ll", "jk-pb"):
        d = drivers.get(name)
        assert d.controls() == () and d.control_registers == frozenset()
