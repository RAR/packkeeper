import pytest

from packkeeper.planner import make_plan, targets_from_block
from packkeeper.session import SessionLog
from packkeeper.settings import SettingError

from unsafe_toy_driver import UNSAFE_TOY


def dev():
    sim = UNSAFE_TOY.simulator()
    return sim, UNSAFE_TOY.open_device(sim, SessionLog())


def test_settings_carry_unsafe_choices_and_bits():
    t = UNSAFE_TOY.table
    assert t["cells"].unsafe.startswith("A wrong count") and t["volts"].unsafe is None
    assert t.unsafe_keys == ["cells", "chem", "flags"]
    assert t["chem"].format(1) == "NCM" and t["chem"].format(7) == "unknown (7)"
    assert t["chem"].parse("ncm") == 1 and t["chem"].parse("0") == 0
    assert t["chem"].limit_error(1) is None and "LFP" in t["chem"].limit_error(5)
    assert t["flags"].format(0x0015) == "0x0015 (heater, buzzer, anti-theft)"
    assert t["flags"].format(0) == "0x0000 (none)"
    assert t["flags"].parse("0x0011") == 0x11 and t["flags"].parse("0x0015 (heater, buzzer, anti-theft)") == 0x15
    with pytest.raises(SettingError):
        t["chem"].parse("diesel")


def test_unsafe_changes_need_the_switch():
    _sim, d = dev()
    b = d.read_block()
    p = make_plan(b, {"cells": 15})
    assert not p.ok and "unsafe setting" in p.errors[0] and "cells" in p.problem_keys
    p = make_plan(b, {"cells": 15}, allow_unsafe=True)
    assert p.ok and [c.key for c in p.unsafe] == ["cells"]
    assert make_plan(b, {"volts": 350}).unsafe == []
    assert make_plan(b, {"cells": 16}).ok  # unchanged: nothing to gate


def test_restore_targets_leave_unsafe_settings_out_without_the_switch():
    _sim, d = dev()
    b = d.read_block()
    assert set(targets_from_block(b)) == {"volts", "mode"}
    assert set(targets_from_block(b, allow_unsafe=True)) == set(UNSAFE_TOY.table)


def run_cli(monkeypatch, tmp_path, capsys, argv, answer):
    from packkeeper import drivers
    from packkeeper.cli import main

    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt), answer)[1])
    code = main([*argv, "--bms", "unsafe-toy", "--simulate", "--log-dir", str(tmp_path)])
    return code, capsys.readouterr()


def test_cli_refuses_unsafe_without_the_switch(monkeypatch, tmp_path, capsys):
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "cells", "15"], "yes")
    assert code == 2 and "unsafe setting" in out.out


def test_cli_unsafe_needs_typed_unsafe(monkeypatch, tmp_path, capsys):
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "--unsafe", "cells", "15"], "yes")
    assert code == 1 and "Type 'unsafe'" in out.out and "A wrong count" in out.out and "Nothing was written" in out.out
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "--unsafe", "cells", "15"], "unsafe")
    assert code == 0 and "confirmed" in out.out


def test_cli_keys_marks_unsafe_and_lists_choices(monkeypatch, tmp_path, capsys):
    from packkeeper import drivers
    from packkeeper.cli import main

    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    assert main(["keys", "--bms", "unsafe-toy"]) == 0
    out = capsys.readouterr().out
    assert "[unsafe]" in out and "LFP, NCM" in out
