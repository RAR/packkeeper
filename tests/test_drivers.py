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
