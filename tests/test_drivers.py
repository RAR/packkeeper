import hashlib
from pathlib import Path

import pytest

from eg4tool.settings import LinkedPair, Rule, SettingTable

VENDORED_SHA256 = "de30c596b51dc56acefa0e36a00903b3a6a56220ae54a31d9517da3a19ed78bd"
SRC = Path(__file__).parent.parent / "src" / "eg4tool"


def test_vendored_table_is_byte_for_byte_upstream():
    data = (SRC / "drivers" / "eg4_ll" / "settings_table.py").read_bytes()
    data = data.replace(b"\r\n", b"\n")  # a Windows checkout may convert line endings
    assert hashlib.sha256(data).hexdigest() == VENDORED_SHA256


def small_table() -> SettingTable:
    spec = {
        "low": (0x10, 0.01, 0, "V", None, 2),
        "high": (0x11, 0.01, 0, "V", None, 2),
        "a": (0x12, 1, 0, "A", None, 0),
        "b": (0x13, 1, 0, "A", None, 0),
    }
    limits = {"low": (2.0, 3.0, 0.01), "high": (3.0, 4.0, 0.01), "a": (1, 9, 1), "b": (1, 9, 1)}
    return SettingTable.build(spec, limits, [("low", "high", True)], linked=[LinkedPair("a", "b", "why")])


def test_setting_table_basics():
    t = small_table()
    assert list(t) == ["low", "high", "a", "b"] and len(t) == 4
    assert t["high"].register == 0x11 and t.by_register[0x12].key == "a"
    assert t.order == [Rule("low", "high", True)]
    assert t.rank == {"low": 0, "high": 1, "a": 0, "b": 0}
    assert t.partner("a") == "b" and t.partner("b") == "a" and t.partner("low") is None
    assert t.pair("b").reason == "why"
    a, b = t.linked[0]
    assert (a, b) == ("a", "b")
    assert t.describe_rule(t.order[0]) == "Low must stay below High"


def test_setting_table_rejects_unknown_keys():
    spec = {"low": (0x10, 0.01, 0, "V", None, 2)}
    with pytest.raises(ValueError, match="nope"):
        SettingTable.build(spec, {"low": (2.0, 3.0, 0.01)}, [("low", "nope", True)])


def test_eg4_table_matches_the_old_globals():
    from eg4tool.drivers.eg4_ll.table import TABLE
    assert len(TABLE) == 51
    assert TABLE["balance_delta_voltage"].minimum == TABLE["balance_delta_voltage"].multiplier * 20
    assert [tuple(p) for p in TABLE.linked] == [("short_circuit_current", "discharge_overcurrent_2_protection")]
    assert TABLE["cell_overvoltage_protection"].group == "Cell voltage"
    assert TABLE["mos_overtemp_warning"].label == "MOSFET OT warning"


def test_registry_has_eg4_ll_by_default():
    from eg4tool import drivers
    assert drivers.DEFAULT == "eg4-ll"
    d = drivers.get("eg4-ll")
    assert d.label == "EG4-LL" and d.block_start == 0x2D and d.block_count == 91
    with pytest.raises(drivers.UnknownDriver, match="nope"):
        drivers.get("nope")


def test_eg4_profile_block_is_bound_to_its_driver():
    from eg4tool.drivers.eg4_ll import DRIVER
    ref = DRIVER.profiles["eg4-shipped"].block
    assert ref.driver is DRIVER
    assert ref.identity.model == "ZTR-16S200A-10KW-STD2"
    assert DRIVER.identity_warnings(ref) == []
    assert DRIVER.identity_warnings(ref.with_raws({0x69: 0x4142})) != []  # model text "AB..."


def test_block_length_comes_from_the_driver():
    from eg4tool.drivers.eg4_ll import DRIVER
    with pytest.raises(ValueError, match="91"):
        DRIVER.block([0] * 90)


def test_eg4_pages_cover_every_setting_once():
    from eg4tool.drivers.eg4_ll import DRIVER
    assert sorted(DRIVER.ordered_keys) == sorted(DRIVER.table)
    assert all(DRIVER.pages[DRIVER.page_of[k]] for k in DRIVER.table)


import json  # noqa: E402

from eg4tool import backup  # noqa: E402
from eg4tool.block import DriverMismatch  # noqa: E402
from eg4tool.drivers.eg4_ll import DRIVER as EG4_LL  # noqa: E402
from eg4tool.planner import make_plan  # noqa: E402
from eg4tool.service import diff_blocks  # noqa: E402

REF = EG4_LL.profiles["eg4-shipped"].block


def test_backup_records_the_bms_type(tmp_path):
    f = tmp_path / "b.json"
    backup.save(f, REF)
    data = json.loads(f.read_text())
    assert data["format_version"] == 2 and data["bms"] == "eg4-ll"
    assert backup.load(f).block.driver is EG4_LL


def test_version_1_backup_loads_as_eg4_ll():
    data = backup.to_dict(REF)
    data["format_version"] = 1
    del data["bms"]
    assert backup.from_dict(data).block == REF


def test_backup_naming_an_unknown_bms_is_refused():
    data = backup.to_dict(REF)
    data["bms"] = "mystery"
    with pytest.raises(backup.BackupError, match="mystery"):
        backup.from_dict(data)


def test_plan_takes_rules_from_the_block_driver():
    p = make_plan(REF, {"cell_overvoltage_warning": 3900})
    assert not p.ok and any("Cell OV warning" in e for e in p.errors)
    p = make_plan(REF, {"short_circuit_current": 25000})
    assert any("which of 0x52 / 0x54 is which is not settled yet" in e for e in p.errors)


def test_dry_run_frames_use_the_driver():
    p = make_plan(REF, {"cell_overvoltage_warning": 3650})
    assert p.frames() == [EG4_LL.write_frame(0x43, 3650)]


def test_cli_bms_option(tmp_path, capsys):
    from eg4tool.cli import main
    assert main(["keys", "--bms", "eg4-ll"]) == 0
    assert "cell_overvoltage_protection" in capsys.readouterr().out
    assert main(["info", "--simulate", "--bms", "eg4-ll", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ZTR-16S200A" in out and "SOC 67%" in out


def test_cli_rejects_unknown_bms(capsys):
    from eg4tool.cli import main
    with pytest.raises(SystemExit):
        main(["keys", "--bms", "nope"])
    assert "nope" in capsys.readouterr().err


def test_cli_profile_names_come_from_the_driver(tmp_path, capsys):
    from eg4tool.cli import main
    assert main(["diff", "--profile", "nope", "--simulate", "--log-dir", str(tmp_path)]) == 2
    assert "eg4-shipped" in capsys.readouterr().err


import ast

from eg4tool import drivers
from eg4tool.planner import targets_from_block
from eg4tool.service import CONFIRMED, NOT_APPLIED, apply_plan
from eg4tool.session import SessionLog

from toy_driver import TOY  # tests/ is on sys.path under pytest's rootdir conftest-less mode

TOY_REF = TOY.profiles["toy-default"].block


def toy_device(**kw):
    sim = TOY.simulator()
    for k, v in kw.items():
        setattr(sim, k, v)
    return sim, TOY.open_device(sim, SessionLog())


def test_toy_plan_moves_a_chain_up_top_first():
    p = make_plan(TOY_REF, {"high_cut": 370, "top_cut": 380})
    assert p.ok, p.errors
    assert [c.key for c in p.changes] == ["top_cut", "high_cut"]


def test_toy_rules_and_limits_are_the_toys():
    assert not make_plan(TOY_REF, {"high_cut": 290}).ok  # below its 3.30 V floor and below low_cut
    p = make_plan(TOY_REF, {"trip_a": 120})
    assert not p.ok and "toy pair" in p.errors[0]
    assert make_plan(TOY_REF, {"trip_a": 120, "trip_b": 120}).ok


def test_toy_write_is_read_back_and_addressed_to_7():
    sim, dev = toy_device()
    res = apply_plan(dev, make_plan(dev.read_block(), {"low_cut": 290}))
    assert res.ok and res.results[0].status == CONFIRMED and sim.image[0x101] == 290
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert any(frame.startswith("07 10 01 01") for frame in tx)


def test_toy_dropped_write_is_not_applied():
    sim, dev = toy_device(floors={0x106: 110})
    res = apply_plan(dev, make_plan(dev.read_block(), {"hot_cut": 90}))  # raw 90 = 50 °C, below the floor
    assert res.results[0].status == NOT_APPLIED


def test_toy_backup_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "toy.json"
    backup.save(f, TOY_REF)
    assert json.loads(f.read_text())["bms"] == "toy"
    with pytest.raises(backup.BackupError, match="toy"):
        backup.load(f)  # not registered
    monkeypatch.setitem(drivers.DRIVERS, "toy", TOY)
    b = backup.load(f)
    assert b.block.driver is TOY and b.block == TOY_REF


def test_nothing_crosses_between_bms_types():
    with pytest.raises(DriverMismatch):
        diff_blocks(REF, TOY_REF)
    with pytest.raises(DriverMismatch):
        make_plan(REF, targets_from_block(TOY_REF), source=TOY_REF)
    from eg4tool.planner import identity_warnings
    with pytest.raises(DriverMismatch):
        identity_warnings(REF, TOY_REF, "the backup")
    _sim, dev = toy_device()
    with pytest.raises(DriverMismatch):
        apply_plan(dev, make_plan(REF, {"cell_overvoltage_warning": 3650}))
    assert _sim.writes == []


def test_generic_code_never_imports_the_eg4_driver():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "drivers":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("eg4_ll" in n for n in names):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


def test_apply_plan_refuses_a_forged_change_to_a_non_writable_register():
    import dataclasses
    from eg4tool.planner import Change
    sim, dev = toy_device()
    plan = make_plan(dev.read_block(), {"low_cut": 290})
    assert plan.ok
    forged = dataclasses.replace(TOY.table["low_cut"], register=0x100)
    plan.changes.append(Change(forged, 0, 1))
    with pytest.raises(ValueError, match="0x100"):
        apply_plan(dev, plan)
    assert sim.writes == []


def test_toy_device_refuses_to_write_a_non_writable_register():
    from eg4tool.device import PackError
    sim, dev = toy_device()
    with pytest.raises(PackError):
        dev.write_setting(0x100, 1)
    assert sim.writes == []


def test_backup_with_a_malformed_bms_field_is_refused():
    data = backup.to_dict(REF)
    data["bms"] = ["eg4-ll"]
    with pytest.raises(backup.BackupError, match="must name a BMS type"):
        backup.from_dict(data)
    del data["bms"]
    with pytest.raises(backup.BackupError, match="must name a BMS type"):
        backup.from_dict(data)
