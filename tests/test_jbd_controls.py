"""JBD-UP live controls: frames byte for byte as captured, checked against the simulator."""

import threading
from pathlib import Path

import pytest

import packkeeper.drivers.jbd_up.controls as JC
from packkeeper.controls import CONFIRMED, NOT_CONFIRMED, UNVERIFIED, control_frames
from packkeeper.device import PackError
from packkeeper.drivers.jbd_up import DRIVER, registers as R
from packkeeper.drivers.jbd_up.framing import JbdFraming
from packkeeper.drivers.jbd_up.sim import FakeJbd
from packkeeper.service import run_control
from packkeeper.session import SessionLog

FRAMES = Path(__file__).parent / "jbd_up_control_frames.txt"


def captured() -> dict[str, tuple[bytes, bytes]]:
    out = {}
    for line in FRAMES.read_text().splitlines():
        if line and not line.startswith("#"):
            label, req, rep = line.split("|")
            out[label] = (bytes.fromhex(req), bytes.fromhex(rep))
    return out


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(JC, "MOS_WAIT_S", 0.2)
    monkeypatch.setattr(JC, "QUIET_WAIT_S", 0.3)
    monkeypatch.setattr(JC, "BACK_WAIT_S", 0.5)


def jbd(**kw):
    sim = FakeJbd(**kw)
    sim.restart_s = 0.1
    return sim, DRIVER.open_device(sim, SessionLog(), kw.get("address", 0))


def by_key():
    return {c.key: c for c in DRIVER.controls()}


def test_the_driver_lists_three_controls_and_their_registers():
    c = by_key()
    assert list(c) == ["restart", "standby", "mosfet-test"]
    assert [c[k].typed_word for k in c] == [None, "standby", "test"]
    assert DRIVER.control_registers == {0x2900, 0x2902, 0x2908, 0x2912}
    assert [(s.register, s.value) for s in c["mosfet-test"].steps] == [
        (0x2900, 0x5A01), (0x2902, 0x0001), (0x2902, 0x0003), (0x2900, 0x0000)]
    assert [(s.register, s.value) for s in c["mosfet-test"].cleanup] == [(0x2900, 0x0000)]


def test_frames_are_byte_for_byte_the_captured_ones():
    cap = captured()
    framing = JbdFraming(key=0x11, request_ends=R.REQUEST_ENDS)  # the key the stock app used

    class At2:
        address = 2

    At2.framing = framing
    frames = {label: f for c in DRIVER.controls() for label, f in control_frames(At2, c)}
    assert frames["Restart"] == cap["restart"][0]
    assert frames["Standby sleep"] == cap["standby"][0]
    assert frames["Tooling mode (both MOSFETs off)"] == cap["tooling"][0]
    assert frames["Charge MOSFET on"] == cap["mos charge"][0]
    assert frames["Both MOSFETs on"] == cap["mos both"][0]
    assert frames["Normal mode (the BMS drives the MOSFETs)"] == cap["normal"][0]
    assert framing.read_request(2, R.DEBUG.start, R.DEBUG.count) == cap["debug read"][0]
    for label in ("restart", "standby", "tooling", "mos charge", "mos both", "normal"):
        req, rep = cap[label]
        framing.parse_write(req, rep)  # the pack's acks parse
    req, rep = cap["debug read"]
    assert framing.parse_read(req, rep) == [0] * 7


def test_mosfet_test_confirmed_step_by_step():
    sim, dev = jbd()
    res = run_control(dev, by_key()["mosfet-test"])
    assert res.ok, res.stopped
    assert [r.status for r in res.results] == [CONFIRMED] * 4
    assert "charge off, discharge off" in res.results[0].detail
    assert "charge on, discharge off" in res.results[1].detail
    assert "debug mode reads normal" in res.results[3].detail
    assert [(s, v) for s, v in sim.writes] == [(0x2900, [0x5A01]), (0x2902, [1]), (0x2902, [3]), (0x2900, [0])]


def test_mosfets_that_stay_on_fail_the_test_and_it_returns_to_normal():
    sim, dev = jbd()
    sim.stuck_mosfets = True
    res = run_control(dev, by_key()["mosfet-test"])
    assert not res.ok and res.results[0].status == NOT_CONFIRMED
    assert res.results[-1].cleanup and res.results[-1].status == CONFIRMED
    assert sim.writes == [(0x2900, [0x5A01]), (0x2900, [0])] and sim.mem[0x2900] == 0


def test_an_ignored_mos_write_fails_and_returns_to_normal():
    sim, dev = jbd()
    sim.ignore = {0x2902}
    res = run_control(dev, by_key()["mosfet-test"])
    assert [r.status for r in res.results[:2]] == [CONFIRMED, NOT_CONFIRMED]
    assert res.results[-1].cleanup and sim.writes[-1] == (0x2900, [0])


def test_stop_returns_to_normal():
    sim, dev = jbd()
    stop = threading.Event()
    res = run_control(dev, by_key()["mosfet-test"], progress=lambda r: stop.set(), stop=stop)
    assert res.stopped == "stopped by the user"
    assert sim.writes == [(0x2900, [0x5A01]), (0x2900, [0])]
    assert dev.read_live().charge_allowed and dev.read_live().discharge_allowed


def test_restart_confirmed_when_the_pack_goes_quiet_then_answers():
    sim, dev = jbd()
    res = run_control(dev, by_key()["restart"])
    assert res.ok and "went quiet, then answered again" in res.results[0].detail
    assert sim.writes == [(0x2912, [0x4A4A])]


def test_a_restart_never_seen_is_unverified():
    sim, dev = jbd()
    sim.restart_s = 0
    res = run_control(dev, by_key()["restart"])
    assert not res.ok and res.results[0].status == UNVERIFIED and "never went quiet" in res.results[0].detail


def test_a_pack_that_never_comes_back_is_not_confirmed():
    sim, dev = jbd()
    sim.restart_s = None
    res = run_control(dev, by_key()["restart"])
    assert res.results[0].status == NOT_CONFIRMED and "has not answered" in res.results[0].detail


def test_standby_is_confirmed_by_its_ack_and_reports_the_status():
    sim, dev = jbd()
    res = run_control(dev, by_key()["standby"])
    assert res.ok and "Standby" in res.results[0].detail
    assert sim.writes == [(0x2908, [0xA501])]


def test_standby_without_an_ack_is_unverified():
    sim, dev = jbd()
    dev.read_block()
    assert JC.standby(dev, False).ok is None


def test_writes_off_sends_no_control():
    sim, dev = jbd(large_capacity=True)  # turns writes off on every read
    with pytest.raises(PackError, match="writes are off"):
        run_control(dev, by_key()["restart"])
    assert sim.writes == []


def control_tx(tmp_path) -> list[str]:
    return [line for log in tmp_path.glob("*.log") for line in log.read_text().splitlines()
            if " TX " in line and " 79 29 " in line]


def test_cli_lists_the_controls(capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up"]) == 0
    out = capsys.readouterr().out
    assert "restart" in out and "standby" in out and "mosfet-test" in out


def test_cli_dry_run_shows_the_frames_and_sends_nothing(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up", "--simulate", "--dry-run", "--log-dir", str(tmp_path),
                 "mosfet-test"]) == 0
    out = capsys.readouterr().out
    assert "00 79 29 00 29 02 00 06 11 4A 42 44 5A 01" in out and "Dry run: nothing sent." in out
    assert "only if stopped or a step fails" in out
    assert control_tx(tmp_path) == []


def test_cli_needs_the_typed_word(tmp_path, capsys, monkeypatch):
    from packkeeper.cli import main

    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert main(["control", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path), "mosfet-test"]) == 2
    assert "Nothing was sent." in capsys.readouterr().out and control_tx(tmp_path) == []


def test_cli_runs_the_mosfet_test(tmp_path, capsys, monkeypatch):
    from packkeeper.cli import main

    monkeypatch.setattr("builtins.input", lambda prompt="": "test")
    assert main(["control", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path), "mosfet-test"]) == 0
    out = capsys.readouterr().out
    assert out.count("confirmed") >= 4 and "Control confirmed." in out
    assert len(control_tx(tmp_path)) == 4


def test_cli_warns_when_cleanup_was_not_confirmed(tmp_path, capsys, monkeypatch):
    import packkeeper.cli as cli
    from packkeeper.controls import UNVERIFIED, ControlResult, StepResult

    control = by_key()["mosfet-test"]
    res = ControlResult(control, [StepResult(control.cleanup[0], UNVERIFIED, "x", cleanup=True)], "error: x")
    monkeypatch.setattr(cli, "run_control", lambda *a, **k: res)
    monkeypatch.setattr("builtins.input", lambda prompt="": "test")
    assert cli.main(["control", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path), "mosfet-test"]) == 1
    assert "WARNING: the cleanup was not confirmed" in capsys.readouterr().out


def test_cli_unknown_control_and_bms_without_controls(capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up", "--simulate", "nope"]) == 2
    assert "unknown control 'nope'" in capsys.readouterr().err
    assert main(["control"]) == 2
    assert "has no controls" in capsys.readouterr().err
