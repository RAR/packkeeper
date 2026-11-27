"""JBD-UP ("ES") wire format: Modbus-like frames with their own function codes.

    addr | func | start (BE) | end (BE) | len (BE) | data[len] | CRC16-Modbus (LE)

Registers are byte addresses (end = start + bytes). Reads are 0x78 with no
data; writes are 0x79 with data = key byte, 'JBD', then the values. Sources:
protocol notes reverse engineered from JBD's ES-UP apps, syssi/esphome-jbd-bms
jbd_bms_up, and the PhracturedBlue ECO-WORTHY gist.
"""

from __future__ import annotations

import struct

from ...errors import FrameError
from ...modbus import crc_ok, with_crc

FN_READ = 0x78
FN_WRITE = 0x79
DEFAULT_KEY = 0x11  # the app's key when the BMS reports protocol version 0
MAX_REPLY = 490  # data bytes per reply


class JbdError(FrameError):
    pass


def _head(frame: bytes) -> tuple[int, int, int, int, int]:
    return struct.unpack(">BBHHH", frame[:8])


class JbdFraming:
    name = "JBD-UP"
    max_read = MAX_REPLY // 2
    head_len = 8
    # A real pack now and then ignores a request sent the moment its last reply ends (2026-10-24 logs).
    request_gap = 0.1

    def __init__(self, key: int = DEFAULT_KEY, request_ends: dict[int, int] | None = None):
        self.key = key
        self.request_ends = dict(request_ends or {})

    def read_request(self, address: int, start: int, count: int) -> bytes:
        end = max(start + 2 * count, self.request_ends.get(start, 0))
        return with_crc(struct.pack(">BBHHH", address, FN_READ, start, end, 0))

    def write_request(self, address: int, start: int, values: list[int]) -> bytes:
        data = b"".join(struct.pack(">H", v) for v in values)
        head = struct.pack(">BBHHH", address, FN_WRITE, start, start + len(data), len(data) + 4)
        return with_crc(head + bytes([self.key]) + b"JBD" + data)

    def reply_length(self, head: bytes) -> int | None:
        if len(head) < self.head_len:
            return None
        return 10 + struct.unpack(">H", head[6:8])[0]

    def _check(self, request: bytes, reply: bytes) -> bytes:
        addr, fn, start, end, _n = _head(request)
        if len(reply) < 10 or not crc_ok(reply):
            raise JbdError("reply CRC mismatch or too short")
        r_addr, r_fn, r_start, _r_end, r_len = _head(reply)
        if (r_addr, r_fn) != (addr, fn):
            raise JbdError(f"reply from {r_addr:02X}/{r_fn:02X}, expected {addr:02X}/{fn:02X}")
        if r_start != start:
            raise JbdError(f"reply for 0x{r_start:04X}, expected 0x{start:04X}")
        if len(reply) != 10 + r_len:
            raise JbdError(f"reply length field {r_len} does not match its {len(reply) - 10} data bytes")
        return reply[8:-2]

    def parse_read(self, request: bytes, reply: bytes) -> list[int]:
        """The words the pack sent, as many as it sent: like the stock app, any
        length is accepted, and the caller decides whether it is enough."""
        data = self._check(request, reply)
        return [struct.unpack(">H", data[i : i + 2])[0] for i in range(0, len(data) - 1, 2)]

    def parse_write(self, request: bytes, reply: bytes) -> None:
        self._check(request, reply)

    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]:
        i = 0
        while i < len(buf):
            if buf[i] == request[0] and (i + 1 >= len(buf) or buf[i + 1] == request[1]):
                break
            i += 1
        return buf[i:], buf[:i]
