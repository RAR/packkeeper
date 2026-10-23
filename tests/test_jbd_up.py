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
        reply(2, 0x78, 0x1802, 0x1806, bytes(4)),  # range
        reply(2, 0x78, 0x1800, 0x1804, bytes(2)),  # short
    ):
        with pytest.raises(JbdError):
            F.parse_read(req, bad)


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
