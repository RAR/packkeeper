from pathlib import Path

import pytest

from packkeeper.drivers.jbd_up.framing import DEFAULT_KEY, JbdError, JbdFraming
from packkeeper.transport import NoReply

F = JbdFraming()


def test_frames_match_the_captured_examples():
    assert F.read_request(0, 0x1000, 0x50).hex(" ").upper() == "00 78 10 00 10 A0 00 00 BE 7E"
    assert F.read_request(0, 0x1800, 0x80).hex(" ").upper() == "00 78 18 00 19 00 00 00 BC 88"
    assert F.write_request(0, 0x1806, [3650]).hex(" ").upper() == "00 79 18 06 18 08 00 06 11 4A 42 44 0E 42 FC 5C"
    assert DEFAULT_KEY == 0x11 and F.max_read == 245 and F.head_len == 8


def test_key_byte_is_per_instance():
    f = JbdFraming(key=0x12)
    assert f.write_request(0, 0x1806, [3650])[8] == 0x12 and JbdFraming().key == 0x11


def reply(addr, fn, start, end, payload):
    from packkeeper.modbus import with_crc
    import struct
    return with_crc(struct.pack(">BBHHH", addr, fn, start, end, len(payload)) + payload)


def test_parse_read_and_reply_length():
    req = F.read_request(2, 0x1800, 2)
    r = reply(2, 0x78, 0x1800, 0x1804, bytes.fromhex("0E10 0DAC"))
    assert F.reply_length(r[:8]) == len(r) and F.reply_length(r[:7]) is None
    assert F.parse_read(req, r) == [0x0E10, 0x0DAC]
    for bad in (
        r[:-1] + bytes([r[-1] ^ 1]),  # CRC
        reply(3, 0x78, 0x1800, 0x1804, bytes(4)),  # address
        reply(2, 0x78, 0x1802, 0x1806, bytes(4)),  # start
    ):
        with pytest.raises(JbdError):
            F.parse_read(req, bad)
    # Any length is accepted, as the stock app does; read_area judges whether it is enough.
    assert F.parse_read(req, reply(2, 0x78, 0x1800, 0x1804, bytes.fromhex("0E10"))) == [0x0E10]
    assert F.parse_read(req, reply(2, 0x78, 0x1800, 0x1900, bytes(6))) == [0, 0, 0]


def test_parse_write_ack_and_resync():
    req = F.write_request(0, 0x1806, [3600])
    ack = reply(0, 0x79, 0x1806, 0x1808, b"")
    F.parse_write(req, ack)
    with pytest.raises(JbdError):
        F.parse_write(req, reply(0, 0x79, 0x1800, 0x1802, b""))
    buf, dropped = F.resync(b"\xAA\x00\x13" + ack, req)
    assert buf == ack and dropped == b"\xAA\x00\x13"


def test_fake_pack_serves_reads_and_checks_the_key():
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim = FakeJbd(address=0, serial="SIM-PACK-1")
    r = sim.transact(F.read_request(0, 0x1800, 6))
    assert F.parse_read(F.read_request(0, 0x1800, 6), r)[3] == 3650  # cell OV protection
    F.parse_write(F.write_request(0, 0x1806, [3600]), sim.transact(F.write_request(0, 0x1806, [3600])))
    assert sim.word(0x1806) == 3600 and sim.writes == [(0x1806, [3600])]
    with pytest.raises(NoReply):
        sim.transact(JbdFraming(key=0x42).write_request(0, 0x1806, [3500]))  # wrong key: ignored
    assert sim.word(0x1806) == 3600
    with pytest.raises(NoReply):
        sim.transact(F.read_request(1, 0x1800, 6))  # not its address


def test_fake_bus_routes_by_address():
    from packkeeper.drivers.jbd_up.sim import FakeJbd, FakeJbdBus

    bus = FakeJbdBus([FakeJbd(address=0, serial="A"), FakeJbd(address=5, serial="B")])
    assert bus.transact(F.read_request(5, 0x1800, 1))
    with pytest.raises(NoReply):
        bus.transact(F.read_request(3, 0x1800, 1))


from datetime import datetime  # noqa: E402

from packkeeper import backup, drivers  # noqa: E402
from packkeeper.block import BlankBlockError  # noqa: E402
from packkeeper.device import PackError  # noqa: E402
from packkeeper.planner import identity_warnings, make_plan  # noqa: E402
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan, set_clock  # noqa: E402
from packkeeper.session import SessionLog  # noqa: E402


def jbd(**kw):
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim = FakeJbd(**kw)
    return sim, DRIVER.open_device(sim, SessionLog(), kw.get("address", 0))


def test_registered_with_its_tables():
    from packkeeper.drivers.jbd_up import DRIVER as D

    assert drivers.get("jbd-up") is D and D.serial.baud == 9600 and D.broadcast_address is None
    t = D.table
    assert t["cell_ov_protection"].register == 0x1806 and t["cell_ov_protection"].unit == "V"
    assert t["charge_ot_protection"].format(t["charge_ot_protection"].parse("55")) == "55.0 °C"
    assert t["charge_ot_protection"].parse("55") == 1050
    assert set(t.unsafe_keys) == {
        "cells_in_series", "shunt_resistance", "short_circuit_current", "short_circuit_delay_step",
        "pack_type", "cell_maker", "soc_config_bits", "function_bits", "can_protocol", "rs485_protocol", "can_baud",
    }
    for reg in (0x290C, 0x290E, 0x2910, 0x2914, 0x2100, 0x1C82, 0x1C88, 0x2004, 0x2006, 0x200A, 0x1C34, 0x2800):
        assert not D.is_writable(reg), hex(reg)


def test_read_decodes_identity_and_settings():
    _sim, dev = jbd()
    b = dev.read_block()
    assert dev.write_refusal is None and dev.framing.key == 0x11
    assert b.model == "JBD-UP16S010" and b.serial == "SIM-PACK-1"
    assert b.firmware == "app 2.6 (hw 2, protocol 1.1)"
    assert b.clock == datetime(2026, 10, 23, 8, 0, 0)
    assert b.raw("cell_ov_protection") == 3650 and b.raw("charge_oc1_protection") == 1050
    assert b.raw("pack_type") == 0 and b.driver.table["pack_type"].format(0) == "LFP"


def test_live_decode():
    _sim, dev = jbd()
    rt = dev.read_live()
    assert rt.cell_count == 16 and rt.cells[0] == 3.3 and rt.pack_voltage == 52.83
    assert rt.current == -3.2 and rt.soc == 76 and rt.soh == 100 and rt.cycles == 37
    assert ("MOSFET", 31) in rt.temps and rt.max_temp == 25
    assert rt.status_text == "Discharging" and rt.charge_allowed and rt.discharge_allowed
    assert rt.remaining_ah == 76.0 and rt.full_capacity_ah == 100.0


def test_write_confirmed_with_the_key_byte():
    sim, dev = jbd()
    p = make_plan(dev.read_block(), {"cell_ov_protection": 3600})
    assert p.ok, p.errors
    assert p.frames(dev.address, dev.framing)[0][:12].hex(" ").upper() == "00 79 18 06 18 08 00 06 11 4A 42 44"
    res = apply_plan(dev, p)
    assert res.ok and res.results[0].status == CONFIRMED and sim.writes == [(0x1806, [3600])]


def test_wrong_key_is_not_applied():
    sim, dev = jbd(key=0x13)  # the pack wants 0x13 but reports protocol 0x11
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert res.results[0].status == NOT_APPLIED and sim.word(0x1806) == 3650


def test_dropped_write_is_not_applied():
    _sim, dev = jbd()
    res = apply_plan(dev, make_plan(dev.read_block(), {"balance_start_delta": 10}))
    assert res.results[0].status == NOT_APPLIED


def test_rules_and_unsafe_gate():
    _sim, dev = jbd()
    b = dev.read_block()
    assert not make_plan(b, {"cell_ov_alarm": 3700}).ok  # above its range and above protection 3650
    assert not make_plan(b, {"cell_ov_protection_recovery": 3650}).ok  # must stay below protection
    assert not make_plan(b, {"cells_in_series": 15}).ok
    assert make_plan(b, {"cells_in_series": 15}, allow_unsafe=True).ok


def test_large_capacity_turns_writes_off():
    _sim, dev = jbd(large_capacity=True)
    dev.read_block()
    assert dev.write_refusal and "large-capacity" in dev.write_refusal
    with pytest.raises(PackError):
        dev.write_setting(dev.driver.table["cell_ov_protection"], 3600)


def test_blank_and_implausible_reads_are_refused():
    sim, dev = jbd()
    sim.blank = True
    with pytest.raises(BlankBlockError):
        dev.read_block()
    sim, dev = jbd()
    sim.mem[0x2032] = 0
    with pytest.raises(BlankBlockError, match="cells in series"):
        dev.read_block()


def test_protocol_zero_warns_and_uses_the_default_key():
    _sim, dev = jbd(protocol=0)
    b = dev.read_block()
    assert dev.framing.key == 0x11 and dev.driver.identity_warnings(b)


def test_clock_set_and_read_back():
    sim, dev = jbd()
    res = set_clock(dev, datetime(2026, 10, 24, 9, 30, 0))
    assert res.ok and sim.writes[-1][0] == 0x2800 and len(sim.writes[-1][1]) == 6


def test_backup_round_trip_and_serial_warning(tmp_path):
    _sim, dev_a = jbd(serial="PACK-A")
    _sim, dev_b = jbd(serial="PACK-B")
    a, b = dev_a.read_block(), dev_b.read_block()
    f = tmp_path / "a.json"
    backup.save(f, a)
    assert backup.load(f).block == a
    assert any("PACK-A" in w and "PACK-B" in w for w in identity_warnings(b, a, "the backup"))


def test_find_on_a_bus():
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd, FakeJbdBus
    from packkeeper.find import scan

    bus = FakeJbdBus([FakeJbd(address=a, serial=f"P{a}") for a in (0, 2, 5)])
    found = scan(DRIVER, bus, DRIVER.find_addresses, SessionLog())
    assert [(f.address, f.serial) for f in found] == [(0, "P0"), (2, "P2"), (5, "P5")]


def test_cli_info_and_dry_run(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "JBD-UP16S010" in out and "SIM-PACK-1" in out
    assert main(["set", "--bms", "jbd-up", "--simulate", "--dry-run", "--address", "0", "--log-dir", str(tmp_path),
                 "cell_ov_protection", "3.6"]) == 0
    out = capsys.readouterr().out
    assert "00 79 18 06 18 08 00 06 11 4A 42 44 0E 10" in out and "broadcast" not in out


def test_v1_pack_with_zero_fan_registers_can_still_write():
    sim, dev = jbd()
    sim.mem[0x1C8A] = sim.mem[0x1C8C] = 0
    p = make_plan(dev.read_block(), {"cell_ov_protection": 3600})
    assert p.ok, p.errors
    assert apply_plan(dev, p).results[0].status == CONFIRMED


def test_live_decode_stops_at_the_end_of_the_data():
    from packkeeper.drivers.jbd_up.live import decode

    _sim, dev = jbd()
    regs = dev.read_live().registers
    off = 0x44 + 2 * regs[0x42 // 2]
    cut = regs[: (off + 2) // 2]  # ends right after the sensor count
    rt = decode(cut)
    assert not any(n.startswith("sensor") for n, _ in rt.temps) and rt.max_temp is None


def test_key_comes_from_the_pack_protocol_register():
    sim, dev = jbd(protocol=0x13, key=0x13)
    b = dev.read_block()
    assert dev.framing.key == 0x13
    p = make_plan(b, {"cell_ov_protection": 3600})
    assert p.frames(dev.address, dev.framing)[0][8] == 0x13
    assert apply_plan(dev, p).results[0].status == CONFIRMED


def test_full_capacity_above_design_can_still_plan_and_apply():
    sim, dev = jbd()
    sim.mem[0x2002] = 10250  # 102.50 Ah learned on a 100.00 Ah pack
    p = make_plan(dev.read_block(), {"cell_ov_protection": 3600})
    assert p.ok, p.errors
    res = apply_plan(dev, p)
    assert res.ok and res.results[0].status == CONFIRMED


def test_apply_plan_refuses_a_forged_unsafe_change():
    from packkeeper.planner import Change

    sim, dev = jbd()
    block = dev.read_block()
    p = make_plan(block, {"cell_ov_protection": 3600})
    cells = dev.driver.table["cells_in_series"]
    p.changes.append(Change(cells, block.raw("cells_in_series"), block.raw("cells_in_series") - 1))
    with pytest.raises(ValueError, match="unsafe setting"):
        apply_plan(dev, p)
    assert sim.writes == []


REAL_FRAMES = Path(__file__).parent / "jbd_up_real_frames.txt"


def real_frames() -> dict[bytes, bytes]:
    pairs = {}
    for line in REAL_FRAMES.read_text().splitlines():
        if line and not line.startswith("#"):
            req, rep = line.split("|")
            pairs[bytes.fromhex(req)] = bytes.fromhex(rep)
    return pairs


class ReplayTransport:
    """Answers only the requests captured from the real pack, byte for byte."""

    def __init__(self):
        self.frames = real_frames()

    def transact(self, request: bytes) -> bytes:
        if request not in self.frames:
            raise NoReply(f"not captured: {request.hex(' ')}")
        return self.frames[request]


def test_real_pack_requests_are_the_ones_it_answered():
    from packkeeper.drivers.jbd_up import DRIVER, registers as R

    f = DRIVER.new_framing()
    sent = {f.read_request(2, a.start, a.count) for a in (R.VERSION, R.PROTECTION, R.LIVE)}
    assert sent == set(real_frames())


def test_real_pack_replies_fill_their_areas():
    from packkeeper.drivers.jbd_up import DRIVER, registers as R

    dev = DRIVER.open_device(ReplayTransport(), SessionLog(), 2)
    version = dev.read_area(R.VERSION)  # 44 bytes sent for a 48-byte request
    protection = dev.read_area(R.PROTECTION)  # 208 for 256
    live_regs = dev.read_area(R.LIVE)  # 162 for 160
    assert (len(version), len(protection), len(live_regs)) == (R.VERSION.count, R.PROTECTION.count, R.LIVE.count)
    assert version[(R.REG_PROTOCOL - R.VERSION.start) // 2] == 0x0026
    assert protection[3] == 3600  # cell OV protection, mV
    rt = DRIVER.decode_live(live_regs)
    assert rt.pack_voltage == 53.96 and rt.current == 2.25 and rt.cell_count == 16 and rt.cycles == 84


def test_find_sees_a_pack_that_sends_a_short_version_block():
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd, FakeJbdBus
    from packkeeper.find import scan

    found = scan(DRIVER, FakeJbdBus([FakeJbd(address=2, serial="REAL-LENGTHS")]), DRIVER.find_addresses, SessionLog())
    assert [(f.address, f.serial) for f in found] == [(2, "REAL-LENGTHS")]


def test_sim_answers_with_the_real_pack_block_lengths():
    from packkeeper.drivers.jbd_up import DRIVER, registers as R
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim, f = FakeJbd(address=2), DRIVER.new_framing()
    for area, sent in ((R.VERSION, 44), (R.PROTECTION, 208), (R.LIVE, 162)):
        assert sim.transact(f.read_request(2, area.start, area.count))[6:8] == sent.to_bytes(2, "big")


def test_a_reply_too_short_for_its_area_is_refused():
    from packkeeper.drivers.jbd_up import registers as R
    from packkeeper.errors import FrameError

    sim, dev = jbd()
    sim.sends[R.PACK.start] = R.PACK.start + 2 * R.PACK.count - 2  # one register short
    with pytest.raises(FrameError, match="0x1C00"):
        dev.read_area(R.PACK)
