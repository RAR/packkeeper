"""Modbus RTU framing: FC03 reads and FC16 writes, checked by hand.

Only the two function codes the pack uses: FC03 (read holding registers) and
FC16 (write multiple registers). Frames are built and checked by hand; the
shapes follow components/eg4_modbus/eg4_modbus_frame.h in esphome-eg4-bms.
"""

from __future__ import annotations

import struct

from .errors import DeviceRefused, FrameError

FC_READ = 0x03
FC_WRITE = 0x10
EXCEPTION_FLAG = 0x80
MAX_READ = 125  # FC03 limit

EXCEPTION_NAMES = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "slave device failure",
    6: "slave device busy",
}


class ModbusError(FrameError):
    """A reply that is malformed, mismatched or a Modbus exception."""


class ModbusException(ModbusError, DeviceRefused):
    def __init__(self, function: int, code: int):
        self.function = function
        self.code = code
        name = EXCEPTION_NAMES.get(code, "unknown")
        super().__init__(f"pack answered FC{function:02X} with exception {code} ({name})")


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(body: bytes) -> bytes:
    return body + struct.pack("<H", crc16(body))


def crc_ok(frame: bytes) -> bool:
    return len(frame) >= 4 and crc16(frame[:-2]) == struct.unpack("<H", frame[-2:])[0]


def read_request(address: int, start: int, count: int) -> bytes:
    if not 1 <= count <= MAX_READ:
        raise ValueError(f"FC03 count {count} out of range")
    return with_crc(struct.pack(">BBHH", address, FC_READ, start, count))


def write_request(address: int, start: int, values: list[int]) -> bytes:
    if not 1 <= len(values) <= 123:
        raise ValueError(f"FC16 count {len(values)} out of range")
    for v in values:
        if not 0 <= v <= 0xFFFF:
            raise ValueError(f"register value {v} does not fit 16 bits")
    body = struct.pack(">BBHHB", address, FC_WRITE, start, len(values), 2 * len(values))
    body += b"".join(struct.pack(">H", v) for v in values)
    return with_crc(body)


def expected_reply_length(head: bytes) -> int | None:
    """Total reply length once the first 3 bytes are known (None if fewer)."""
    if len(head) < 3:
        return None
    function = head[1]
    if function & EXCEPTION_FLAG:
        return 5
    if function == FC_READ:
        return 5 + head[2]
    if function == FC_WRITE:
        return 8
    raise ModbusError(f"unexpected function code 0x{function:02X} in reply")


def parse_read_reply(request: bytes, reply: bytes) -> list[int]:
    address, _fc, _start, count = struct.unpack(">BBHH", request[:6])
    _check_common(address, FC_READ, reply)
    if reply[2] != 2 * count or len(reply) != 5 + 2 * count:
        raise ModbusError(f"FC03 reply carries {reply[2]} bytes, expected {2 * count}")
    data = reply[3:-2]
    return [struct.unpack(">H", data[i : i + 2])[0] for i in range(0, len(data), 2)]


def parse_write_reply(request: bytes, reply: bytes) -> None:
    address, _fc, start, count = struct.unpack(">BBHH", request[:6])
    _check_common(address, FC_WRITE, reply)
    if len(reply) != 8:
        raise ModbusError(f"FC16 ack is {len(reply)} bytes, expected 8")
    r_start, r_count = struct.unpack(">HH", reply[2:6])
    if (r_start, r_count) != (start, count):
        raise ModbusError(
            f"FC16 ack is for 0x{r_start:04X} x{r_count}, expected 0x{start:04X} x{count}"
        )


def _check_common(address: int, function: int, reply: bytes) -> None:
    if len(reply) < 5:
        raise ModbusError(f"reply too short ({len(reply)} bytes)")
    if not crc_ok(reply):
        raise ModbusError("reply CRC mismatch")
    if reply[0] != address:
        raise ModbusError(f"reply from address {reply[0]}, expected {address}")
    if reply[1] == function | EXCEPTION_FLAG:
        raise ModbusException(function, reply[2])
    if reply[1] != function:
        raise ModbusError(f"reply function 0x{reply[1]:02X}, expected 0x{function:02X}")


def registers_of(frame: bytes) -> list[int]:
    """The registers carried by a captured FC03 reply."""
    if not crc_ok(frame) or frame[1] != FC_READ:
        raise ValueError("not a valid FC03 reply")
    data = frame[3:-2]
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


def foreign_requests(data: bytes, address: int) -> list[int]:
    """Addresses that CRC-valid FC03/FC16 requests in `data` are sent to, other than `address`:
    bytes from another master polling the same line."""
    found: list[int] = []
    for i in range(len(data) - 7):
        unit, function = data[i], data[i + 1]
        if not 1 <= unit <= 247 or unit == address:
            continue
        if function == FC_READ:
            n = 8
        elif function == FC_WRITE and i + 6 < len(data):
            n = 9 + data[i + 6]
        else:
            continue
        if crc_ok(data[i : i + n]) and unit not in found:
            found.append(unit)
    return found
