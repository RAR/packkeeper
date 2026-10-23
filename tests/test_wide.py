import pytest

from packkeeper.settings import SettingError, SettingTable

SPEC = {
    "amps": (0x202, 0.001, 0, "A", None, 3, 2, False),
    "cold": (0x206, 0.1, 0, "°C", None, 1, 2, True),
    "small": (0x20A, 1, 0, "s", None, 0),
}
LIMITS = {"amps": (1, 300, 0.001), "cold": (-20, 10, 0.1), "small": (1, 600, 1)}


def table(word_order="high_first"):
    return SettingTable.build(SPEC, LIMITS, [], stride=2, word_order=word_order)


def test_wide_setting_registers_follow_the_stride():
    t = table()
    assert t["amps"].registers == (0x202, 0x204)
    assert t["small"].registers == (0x20A,)
    assert t.covering[0x204] is t["amps"] and 0x204 not in t.by_register


def test_encode_decode_high_and_low_first():
    s = table()["amps"]
    assert s.encode(150000) == [0x0002, 0x49F0]
    assert s.decode([0x0002, 0x49F0]) == 150000
    lo = table("low_first")["amps"]
    assert lo.encode(150000) == [0x49F0, 0x0002]
    assert lo.decode([0x49F0, 0x0002]) == 150000


def test_signed_values():
    s = table()["cold"]
    assert s.encode(-50) == [0xFFFF, 0xFFCE]
    assert s.decode([0xFFFF, 0xFFCE]) == -50
    assert s.parse("-5") == -50 and s.format(-50) == "-5.0 °C"
    assert s.limit_error(-50) is None and s.limit_error(-201) is not None


def test_raw_range_uses_the_width():
    s = table()["amps"]
    assert s.raw_bounds == (0, 0xFFFFFFFF)
    assert s.parse("300") == 300000  # far above 16 bits, still fits
    with pytest.raises(SettingError, match="does not fit"):
        s.encode(1 << 32)
    assert table()["cold"].raw_bounds == (-(1 << 31), (1 << 31) - 1)


def test_six_field_spec_is_one_unsigned_register():
    s = table()["small"]
    assert (s.width, s.signed, s.stride) == (1, False, 2)
    with pytest.raises(SettingError, match="does not fit"):
        s.raw(70000)


def test_overlapping_settings_are_refused():
    spec = {"a": (0x10, 1, 0, None, None, 0, 2, False), "b": (0x11, 1, 0, None, None, 0)}
    with pytest.raises(ValueError, match="0x11"):
        SettingTable.build(spec, {"a": (0, 9, 1), "b": (0, 9, 1)}, [])
