"""How frames are built and checked, one class per wire protocol.

A driver names its framing; each device gets its own instance, so per-session
state (such as JBD's write key) never leaks between connections.
"""

from __future__ import annotations

from typing import Protocol

from . import modbus


class Framing(Protocol):
    name: str
    max_read: int  # registers per read request
    head_len: int  # bytes needed before reply_length can answer
    request_gap: float  # seconds the line must be idle after a reply before the next request

    def read_request(self, address: int, start: int, count: int) -> bytes: ...
    def parse_read(self, request: bytes, reply: bytes) -> list[int]: ...
    def write_request(self, address: int, start: int, values: list[int]) -> bytes: ...
    def parse_write(self, request: bytes, reply: bytes) -> None: ...
    def reply_length(self, head: bytes) -> int | None: ...
    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]: ...


class ModbusFraming:
    name = "Modbus RTU"
    max_read = modbus.MAX_READ
    head_len = 3
    request_gap = 0.0

    def read_request(self, address: int, start: int, count: int) -> bytes:
        return modbus.read_request(address, start, count)

    def parse_read(self, request: bytes, reply: bytes) -> list[int]:
        return modbus.parse_read_reply(request, reply)

    def write_request(self, address: int, start: int, values: list[int]) -> bytes:
        return modbus.write_request(address, start, values)

    def parse_write(self, request: bytes, reply: bytes) -> None:
        modbus.parse_write_reply(request, reply)

    def reply_length(self, head: bytes) -> int | None:
        return modbus.expected_reply_length(head)

    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]:
        """Drop leading bytes that cannot start a reply to this request."""
        address, function = request[0], request[1]
        ok_functions = (function, function | modbus.EXCEPTION_FLAG)
        i = 0
        while i < len(buf):
            if buf[i] == address and (i + 1 >= len(buf) or buf[i + 1] in ok_functions):
                break
            i += 1
        return buf[i:], buf[:i]


MODBUS = ModbusFraming()
