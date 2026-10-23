import pytest

from packkeeper import modbus
from packkeeper.errors import FrameError
from packkeeper.framing import MODBUS, ModbusFraming
from packkeeper.session import SessionLog


def test_modbus_framing_builds_todays_frames():
    assert MODBUS.read_request(1, 0x2D, 0x5B) == modbus.read_request(1, 0x2D, 0x5B)
    assert MODBUS.write_request(1, 0x43, [3650]) == modbus.write_request(1, 0x43, [3650])
    assert MODBUS.max_read == modbus.MAX_READ == 125 and MODBUS.head_len == 3


def test_modbus_framing_parses_and_measures_replies():
    req = MODBUS.read_request(1, 0x2D, 2)
    reply = modbus.with_crc(bytes([1, 3, 4, 0x0E, 0x42, 0x00, 0x01]))
    assert MODBUS.reply_length(reply[:3]) == len(reply)
    assert MODBUS.parse_read(req, reply) == [0x0E42, 0x0001]
    with pytest.raises(FrameError):
        MODBUS.parse_read(req, reply[:-1] + b"\x00")


def test_modbus_error_is_a_frame_error():
    assert issubclass(modbus.ModbusError, FrameError)


def test_resync_uses_the_framing():
    from packkeeper.transport import resync

    good = modbus.with_crc(bytes([1, 3, 2, 0, 5]))
    req = MODBUS.read_request(1, 0, 1)
    assert MODBUS.resync(b"\x00\xff" + good, req) == (good, b"\x00\xff")
    assert resync(b"\x00\xff" + good, 1, 3) == (good, b"\x00\xff")  # the old helper still works


def test_each_device_gets_its_own_framing():
    from toy_driver import TOY

    class Counting(type(TOY)):
        made = 0

        def new_framing(self):
            Counting.made += 1
            return ModbusFraming()

    d = Counting()
    a = d.open_device(d.simulator(), SessionLog())
    b = d.open_device(d.simulator(), SessionLog())
    assert a.framing is not b.framing and Counting.made == 2
    a.read_block()  # still reads through the framing


def test_plan_frames_use_the_given_framing():
    from packkeeper.planner import make_plan
    from toy_driver import TOY

    class Tagged(ModbusFraming):
        def write_request(self, address, start, values):
            return b"T" + super().write_request(address, start, values)

    dev = TOY.open_device(TOY.simulator(), SessionLog())
    plan = make_plan(dev.read_block(), {"low_cut": 290})
    assert plan.frames(framing=Tagged())[0][:1] == b"T"
    assert plan.frames() == [TOY.write_frame(TOY.table["low_cut"], 290)]
