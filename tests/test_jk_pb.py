import json
from datetime import datetime

import pytest

from packkeeper import backup, drivers
from packkeeper.device import PackError
from packkeeper.drivers.jk_pb import DRIVER as JK
from packkeeper.drivers.jk_pb import registers
from packkeeper.drivers.jk_pb.driver import JkPB
from packkeeper.drivers.jk_pb.sim import FakeJK
from packkeeper.planner import make_plan
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan, set_clock
from packkeeper.session import SessionLog


def jk(driver=JK, **kw):
    sim = FakeJK(driver, **kw)
    return sim, driver.open_device(sim, SessionLog())


def tx(dev):
    return [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]


def test_registered_with_30_settings():
    assert drivers.get("jk-pb") is JK and JK.label == "JK-PB"
    assert len(JK.table) == 30
    assert JK.table["cell_ov_protection"].registers == (0x100C, 0x100E)
    assert JK.table["charge_ut_protection"].signed and JK.table["short_circuit_delay"].unit == "µs"
    assert not JK.is_writable(0x106C) and not JK.is_writable(0x1108)  # cell count, device address
    assert not JK.is_writable(registers.addr(registers.COMMANDS, 0x12, 2))


def test_read_decodes_settings_identity_clock_and_info():
    _sim, dev = jk()
    b = dev.read_block()
    assert dev.write_refusal is None
    assert b.raw("cell_ov_protection") == 3650 and b.raw("charge_ut_protection") == 20
    assert b.model == "JK_PB2A16S20P" and b.firmware == "19.08 (hw 19A)" and b.serial == ""
    assert b.clock == datetime(2026, 10, 23, 8, 0, 0)
    assert JK.identity_warnings(b) == []
    info = dict(JK.info_lines(b))
    assert info["Cells"] == "16" and info["Address"] == "1" and "balancer on" in info["Switches"]


def test_live_decode():
    _sim, dev = jk()
    rt = dev.read_live()
    assert rt.cell_count == 16 and rt.cells[0] == 3.300
    assert rt.pack_voltage == 52.83 and rt.current == -3.2 and rt.soc == 76 and rt.soh == 100
    assert ("MOSFET", 31) in rt.temps and rt.max_temp == 25
    assert rt.charge_allowed and rt.discharge_allowed and rt.status_text == "Discharging"
    assert rt.remaining_ah == 152.0 and rt.full_capacity_ah == 200.0 and rt.cycles == 37


def test_plan_orders_and_rules():
    _sim, dev = jk()
    b = dev.read_block()
    p = make_plan(b, {"cell_ov_protection": 3600, "cell_ov_release": 3550})
    assert p.ok, p.errors
    assert [c.key for c in p.changes] == ["cell_ov_release", "cell_ov_protection"]  # moving down: bottom first
    bad = make_plan(b, {"charge_voltage": 3650})  # must stay below OV protection 3.650
    assert not bad.ok and any("Charge voltage" in e for e in bad.errors)
    assert not make_plan(b, {"cell_ov_protection": 3700}).ok  # above 3.650 V


def test_write_confirmed_with_one_frame_at_the_byte_address():
    sim, dev = jk()
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert res.ok and res.results[0].status == CONFIRMED
    assert sim.writes == [(0x100C, [0x0000, 0x0E10])]
    assert any(t.startswith("01 10 10 0C 00 02 04 00 00 0E 10") for t in tx(dev))


def test_dropped_write_is_not_applied():
    _sim, dev = jk()
    res = apply_plan(dev, make_plan(dev.read_block(), {"max_balance_current": 200}))  # 0.2 A, below the sim floor
    assert res.results[0].status == NOT_APPLIED


def test_wrong_numbering_turns_writes_off():
    sim, dev = jk(stride=1)  # the pack addresses by word, this build expects bytes
    dev.read_block()
    assert dev.write_refusal and "0x100C" in dev.write_refusal and "0x1006" in dev.write_refusal
    n = len(sim.writes)
    with pytest.raises(PackError, match="writes are off"):
        dev.write_setting(JK.table["cell_ov_protection"], 3600)
    assert len(sim.writes) == n == 0


def test_word_addressed_build_works_on_a_word_addressed_pack():
    d = JkPB(stride=1)
    assert d.table["cell_ov_protection"].registers == (0x1006, 0x1007)
    sim, dev = jk(d, stride=1)
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert dev.write_refusal is None and res.ok and sim.writes == [(0x1006, [0, 3600])]


def test_wrong_word_order_is_refused():
    from packkeeper.block import BlankBlockError

    _sim, dev = jk(word_order="low_first")
    with pytest.raises(BlankBlockError, match="word order"):
        dev.read_block()


def test_all_zero_settings_are_refused():
    from packkeeper.block import BlankBlockError

    _sim, dev = jk(blank_thresholds=True)
    with pytest.raises(BlankBlockError):
        dev.read_block()


def test_clock_set_and_read_back():
    sim, dev = jk()
    t = datetime(2026, 10, 23, 9, 30, 0)
    res = set_clock(dev, t)
    assert res.ok and res.after is not None
    assert sim.writes[-1][0] == registers.addr(registers.COMMANDS, 0x12, 2)


def test_backup_round_trip_keeps_read_only_registers(tmp_path):
    _sim, dev = jk()
    b = dev.read_block()
    f = tmp_path / "jk.json"
    backup.save(f, b)
    data = json.loads(f.read_text())
    assert data["bms"] == "jk-pb" and [a["start"] for a in data["areas"]] == [0x1000, 0x1400, 0x1300]
    assert backup.load(f).block == b
    assert b.reg(0x106C + 2) == 16  # cell count low word kept


def test_cli_with_address(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--bms", "jk-pb", "--simulate", "--address", "1", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "JK_PB2A16S20P" in out and "Cells     16" in out
    assert main(["set", "--bms", "jk-pb", "--simulate", "--dry-run", "--log-dir", str(tmp_path),
                 "cell_ov_protection", "3.6"]) == 0
    assert "01 10 10 0C 00 02 04 00 00 0E 10" in capsys.readouterr().out
