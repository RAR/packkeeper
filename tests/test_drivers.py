import hashlib
from pathlib import Path

import pytest

from eg4tool.settings import LinkedPair, Rule, SettingTable

VENDORED_SHA256 = "de30c596b51dc56acefa0e36a00903b3a6a56220ae54a31d9517da3a19ed78bd"
SRC = Path(__file__).parent.parent / "src" / "eg4tool"


def test_vendored_table_is_byte_for_byte_upstream():
    data = (SRC / "drivers" / "eg4_ll" / "settings_table.py").read_bytes()
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
from eg4tool.drivers.eg4_ll import DRIVER as EG4_LL  # noqa: E402
from eg4tool.planner import make_plan  # noqa: E402

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
