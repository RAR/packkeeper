"""Registers a pack does not send: kept as "not sent", never padded and never written."""

import json

import pytest

from packkeeper import backup
from packkeeper.block import BlankBlockError, MissingRegister
from packkeeper.device import PackError
from packkeeper.planner import make_plan, targets_from_block
from packkeeper.service import CONFIRMED, StaleError, apply_plan, diff_blocks
from packkeeper.session import SessionLog


def jbd(**kw):
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim = FakeJbd(**kw)
    return sim, DRIVER.open_device(sim, SessionLog(), kw.get("address", 0))


def short_pack(start: int, end: int):
    """A pack whose block read at `start` stops at `end`."""
    sim, dev = jbd()
    sim.sends[start] = end
    return sim, dev


def test_a_short_reply_marks_the_rest_not_sent_and_the_read_still_works():
    sim, dev = short_pack(0x1C00, 0x1C86)  # the last register, static sleep delay, is not sent
    block = dev.read_block()
    assert block.missing_registers == {0x1C86}
    assert block.missing_keys == ["static_sleep_delay"]
    assert not block.has("static_sleep_delay") and block.has("standby_sleep_current")
    with pytest.raises(MissingRegister, match="0x1C86"):
        block.raw("static_sleep_delay")
    assert "static_sleep_delay" not in block.raws()
    assert block.raw("sleep_voltage") == 2900
    assert dev.write_refusal is None
    assert any("WARN" in line and "1 register" in line and "0x1C86" in line for line in dev.log.lines)


def test_a_setting_the_pack_did_not_send_cannot_be_written():
    sim, dev = short_pack(0x1C00, 0x1C86)
    plan = make_plan(dev.read_block(), {"static_sleep_delay": 60})
    assert not plan.ok and "did not send" in plan.errors[0]
    assert "static_sleep_delay" in plan.problem_keys


def test_writing_a_sent_setting_on_a_short_pack_is_confirmed():
    sim, dev = short_pack(0x1C00, 0x1C86)
    plan = make_plan(dev.read_block(), {"standby_sleep_current": 20})
    assert plan.ok
    res = apply_plan(dev, plan)
    assert res.ok and res.results[0].status == CONFIRMED
    assert not res.final.has("static_sleep_delay")


def test_a_rule_partner_that_was_not_sent_blocks_the_change():
    sim, dev = short_pack(0x1800, 0x18CA)  # SOC low protection, recovery and delay not sent
    block = dev.read_block()
    assert block.missing_keys == ["soc_low_protection", "soc_low_protection_recovery", "soc_low_protection_delay"]
    plan = make_plan(block, {"soc_low_alarm": 12})
    assert not plan.ok
    assert any("SOC low" in e and "did not send" in e for e in plan.errors)
    assert make_plan(block, {"cell_diff_protection_delay": 3000}).ok  # nothing to do with the missing ones


def test_restore_onto_a_short_pack_leaves_the_missing_setting_alone():
    _, full = jbd()
    source = full.read_block()
    sim, dev = short_pack(0x1C00, 0x1C86)
    plan = make_plan(dev.read_block(), {**targets_from_block(source), "standby_sleep_current": 20}, source=source)
    assert plan.ok and [c.key for c in plan.changes] == ["standby_sleep_current"]
    assert any("pack did not send" in n and "Static sleep delay" in n for n in plan.notes)


def test_restore_from_a_short_backup_leaves_the_missing_setting_alone(tmp_path):
    _, short = short_pack(0x1C00, 0x1C86)
    path = tmp_path / "short.json"
    backup.save(path, short.read_block())
    data = json.loads(path.read_text())
    pack_area = data["areas"][1]
    assert pack_area["registers"][-1] is None
    assert data["settings"]["static_sleep_delay"]["raw"] is None
    loaded = backup.load(path).block
    assert not loaded.has("static_sleep_delay") and loaded.raw("standby_sleep_current") == 10

    sim, dev = jbd()
    targets = targets_from_block(loaded)
    assert "static_sleep_delay" not in targets
    plan = make_plan(dev.read_block(), targets, source=loaded)
    assert plan.ok and plan.empty
    assert any("source has no value" in n and "Static sleep delay" in n for n in plan.notes)


def test_a_backup_edited_around_a_missing_register_is_refused(tmp_path):
    _, short = short_pack(0x1C00, 0x1C86)
    data = backup.to_dict(short.read_block())
    data["settings"]["static_sleep_delay"]["raw"] = 60
    with pytest.raises(backup.BackupError, match="static_sleep_delay"):
        backup.from_dict(data)
    data = backup.to_dict(short.read_block())
    data["areas"][1]["registers"][-1] = 60  # a value for a register the pack never sent
    with pytest.raises(backup.BackupError, match="static_sleep_delay"):
        backup.from_dict(data)


def test_diff_shows_a_setting_sent_on_one_side_only():
    _, full = jbd()
    _, short = short_pack(0x1C00, 0x1C86)
    rows, _ = diff_blocks(short.read_block(), full.read_block())
    (row,) = rows
    assert row.key == "static_sleep_delay" and row.a is None and row.b == 120


def test_a_pack_that_starts_sending_more_is_a_stale_plan():
    sim, dev = short_pack(0x1C00, 0x1C86)
    plan = make_plan(dev.read_block(), {"standby_sleep_current": 20})
    sim.sends[0x1C00] = 0x1C88
    with pytest.raises(StaleError, match="not sent"):
        apply_plan(dev, plan)
    assert sim.writes == []


def test_a_short_version_block_turns_writes_off_but_the_read_works():
    sim, dev = short_pack(0x2810, 0x2818)  # protocol version (the write key) not sent
    block = dev.read_block()
    assert block.model == "" and block.raw("cell_ov_protection") == 3650
    assert dev.write_refusal and "0x281A" in dev.write_refusal
    assert block.identity_warnings() == []
    assert [label for label, _ in block.info_lines()] == ["RS485", "SOC/SOH"]


def test_no_settings_sent_at_all_is_refused():
    sim, dev = jbd()
    for start in (0x1800, 0x1C00, 0x2000):
        sim.sends[start] = start
    with pytest.raises(BlankBlockError, match="none of its settings"):
        dev.read_block()


def test_jk_a_refused_identity_area_is_not_sent_but_settings_still_read():
    from packkeeper.drivers.jk_pb import DRIVER as JK, registers as R
    from packkeeper.drivers.jk_pb.sim import FakeJK

    sim = FakeJK(JK)
    sim.readable = lambda a: not R.DEVICE <= a < R.DEVICE + 0x40
    dev = JK.open_device(sim, SessionLog())
    block = dev.read_block()
    assert block.model == "" and block.raw("cell_ov_protection") == 3650
    assert all(not R.DEVICE <= a < R.DEVICE + 0x40 for a in block.present_registers)
    assert any("WARN" in line and "refused" in line for line in dev.log.lines)


def test_jk_a_refused_settings_area_still_fails_the_read():
    from packkeeper.drivers.jk_pb import DRIVER as JK, registers as R
    from packkeeper.drivers.jk_pb.sim import FakeJK

    sim = FakeJK(JK)
    sim.readable = lambda a: not R.SETTINGS <= a < R.SETTINGS + 0x100
    dev = JK.open_device(sim, SessionLog())
    with pytest.raises(PackError, match="exception 2"):
        dev.read_block()


def test_cli_diff_prints_not_sent(tmp_path, capsys, monkeypatch):
    from packkeeper import cli

    _, short = short_pack(0x1C00, 0x1C86)
    path = tmp_path / "short.json"
    backup.save(path, short.read_block())
    assert cli.main(["diff", str(path), "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "Static sleep delay" in out and "not sent" in out
