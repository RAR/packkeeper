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


from packkeeper.block import Area  # noqa: E402
from packkeeper.session import SessionLog  # noqa: E402

from wide_toy_driver import WIDE  # noqa: E402


def wide_device(**kw):
    sim = WIDE.simulator()
    for k, v in kw.items():
        setattr(sim, k, v)
    return sim, WIDE.open_device(sim, SessionLog())


def test_area_addresses_follow_the_stride():
    assert list(Area(0x200, 3, 2).addresses()) == [0x200, 0x202, 0x204]
    assert Area(0x2D, 91) == (0x2D, 91, 1)


def test_block_reads_wide_and_signed_values():
    _sim, dev = wide_device()
    b = dev.read_block()
    assert b.raw("amps") == 150000 and b.raw("cold") == -50
    assert b.reg(0x20A) == 9 and b.unknown_registers() == {}  # 0x20A is not writable here
    assert b.regs(0x202, 2) == [0x0002, 0x49F0]
    with pytest.raises(KeyError):
        b.reg(0x203)  # between two registers of a stride-2 area


def test_with_raws_splits_a_wide_value():
    _sim, dev = wide_device()
    b = dev.read_block().with_raws({0x206: -100})
    assert b.regs(0x206, 2) == [0xFFFF, 0xFF9C] and b.raw("cold") == -100


def test_long_areas_are_read_in_chunks_at_stride_addresses():
    from packkeeper import device

    class Long(type(WIDE)):
        areas = (Area(0x1000, 141, 2),)

    image = [7] * 0x1200
    sim = WIDE.simulator()
    sim.image, sim.stride = image, 2
    dev = Long().open_device(sim, SessionLog())
    regs = dev.read_area(Long.areas[0])
    assert len(regs) == 141 and device.MAX_READ == 125
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert [t[:17] for t in tx] == ["09 03 10 00 00 7D", "09 03 10 FA 00 10"]


def test_eg4_still_reads_its_block_in_one_frame():
    from packkeeper.drivers.eg4_ll import DRIVER
    from packkeeper.drivers.eg4_ll.sim import FakePack

    dev = DRIVER.open_device(FakePack(), SessionLog())
    dev.read_block()
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert tx == ["01 03 00 2D 00 5B 94 38"]


from packkeeper.planner import make_plan  # noqa: E402
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan  # noqa: E402


def test_a_wide_setting_is_one_fc16_frame():
    _sim, dev = wide_device()
    p = make_plan(dev.read_block(), {"amps": 120000})
    assert p.ok, p.errors
    (frame,) = p.frames()
    assert frame[:7] == bytes([9, 0x10, 0x02, 0x02, 0x00, 0x02, 0x04])
    assert frame[7:11] == bytes([0x00, 0x01, 0xD4, 0xC0])  # 120000 = 0x0001D4C0, high word first
    assert p.frames(address=3)[0][0] == 3


def test_wide_write_is_confirmed_by_read_back():
    sim, dev = wide_device()
    res = apply_plan(dev, make_plan(dev.read_block(), {"cold": -100}))
    assert res.ok and res.results[0].status == CONFIRMED and res.results[0].reported == -100
    assert sim.writes == [(0x206, [0xFFFF, 0xFF9C])]


def test_wide_write_dropped_on_the_low_word_is_not_applied():
    sim, dev = wide_device(floors={0x204: 0xF000})
    res = apply_plan(dev, make_plan(dev.read_block(), {"amps": 120000}))
    assert res.results[0].status == NOT_APPLIED and res.stopped


def test_planner_and_device_check_every_register_of_a_setting():
    import dataclasses

    from packkeeper.device import PackError
    from packkeeper.planner import Change

    sim, dev = wide_device()
    s = WIDE.table["amps"]
    forged = dataclasses.replace(s, register=0x208)  # covers 0x208 (writable) and 0x20A (not)
    with pytest.raises(PackError, match="0x20A"):
        dev.write_setting(forged, 1000)
    plan = make_plan(dev.read_block(), {"amps": 120000})
    plan.changes.append(Change(forged, 0, 1000))
    with pytest.raises(ValueError, match="0x20A"):
        apply_plan(dev, plan)
    assert sim.writes == []
from packkeeper.device import PackError  # noqa: E402
from packkeeper.transport import NoReply  # noqa: E402


class Addressed(type(WIDE)):
    address_choices = range(0, 248)
    refusal = None

    def check_device(self, device, block):
        return self.refusal


def test_session_address_reaches_every_frame():
    d = Addressed()
    sim = d.simulator()
    sim.answer_to = 5
    dev = d.open_device(sim, SessionLog(), address=5)
    block = dev.read_block()
    assert dev.address == 5 and make_plan(block, {"amps": 120000}).frames(dev.address)[0][0] == 5
    with pytest.raises(NoReply):
        d.open_device(sim, SessionLog()).read_block()  # default address 9: no answer


def test_address_outside_the_choices_is_refused():
    with pytest.raises(ValueError, match="address"):
        Addressed().open_device(WIDE.simulator(), SessionLog(), address=300)
    with pytest.raises(ValueError, match="address"):
        WIDE.open_device(WIDE.simulator(), SessionLog(), address=5)  # no choices: fixed at 9


def test_a_write_refusal_stops_every_write_before_any_frame():
    from packkeeper.service import set_clock

    d = Addressed()
    d.refusal = "numbering not confirmed"
    sim = d.simulator()
    dev = d.open_device(sim, SessionLog())
    block = dev.read_block()  # reads still work
    assert dev.write_refusal == "numbering not confirmed"
    with pytest.raises(PackError, match="numbering not confirmed"):
        dev.write_setting(d.table["amps"], 120000)
    with pytest.raises(PackError, match="numbering not confirmed"):
        apply_plan(dev, make_plan(block, {"amps": 120000}))
    assert sim.writes == []
    d.supports_clock = True
    with pytest.raises(PackError, match="numbering not confirmed"):
        set_clock(dev, __import__("datetime").datetime(2026, 1, 1))
    assert any("writes are off" in line for line in dev.log.lines)


def test_cli_address_option(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--simulate", "--address", "5", "--log-dir", str(tmp_path)]) == 2
    assert "--address" in capsys.readouterr().err  # EG4-LL is fixed at address 1
