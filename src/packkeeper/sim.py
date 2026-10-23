"""An in-process fake Modbus device for tests and for trying the tool without hardware.

It serves a register image over the same transact() interface as the
serial transport and applies FC16 writes. Like a real pack it can ack a
write and silently not store it (`floors`: register -> lowest raw value it
keeps), and it can answer a read with every writable register zero.
`stride` is the address step between registers (2 for a byte-addressed map);
`answer_to` makes it ignore other addresses.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Callable

from . import modbus
from .transport import NoReply


class FakeModbusDevice:
    def __init__(
        self,
        image: list[int],
        is_writable: Callable[[int], bool],
        *,
        floors: dict[int, int] | None = None,
        blank_thresholds: bool = False,
        state_file: Path | None = None,
        state_from: int = 0,
        stride: int = 1,
        readable: Callable[[int], bool] | None = None,
        answer_to: int | None = None,
    ):
        self.image = list(image)
        self.is_writable = is_writable
        self.floors = floors or {}
        self.blank_thresholds = blank_thresholds
        self.state_file = state_file
        self.state_from = state_from
        self.stride = stride
        self.readable = readable
        self.answer_to = answer_to
        self.drop_replies = 0  # next N requests get no reply
        self.drop_acks = 0  # next N writes are applied but not acked
        self.silent_after_write = 0  # after a write, ignore this many requests
        self.refuse: dict[int, int] = {}  # register -> Modbus exception code answered to a write
        self.writes: list[tuple[int, list[int]]] = []
        self.port = "simulated pack"
        if state_file and state_file.exists():
            saved = json.loads(state_file.read_text())
            for reg, v in saved.get("registers", {}).items():
                self.image[int(reg, 0)] = v

    def on_read(self, start: int, count: int, regs: list[int]) -> None:
        """Hook: adjust registers being served (e.g. a running clock)."""

    def on_write(self, start: int, values: list[int]) -> None:
        """Hook: react to a stored write."""

    def transact(self, request: bytes) -> bytes:
        if not modbus.crc_ok(request):
            raise NoReply("simulated pack ignores frames with a bad CRC")
        if self.drop_replies:
            self.drop_replies -= 1
            raise NoReply("simulated pack did not answer")
        address, function = request[0], request[1]
        if self.answer_to is not None and address != self.answer_to:
            raise NoReply(f"simulated pack only answers at address {self.answer_to}")
        start, count = struct.unpack(">HH", request[2:6])
        size = len(self.image)
        addrs = [start + i * self.stride for i in range(count)]
        bad = addrs[-1] >= size if addrs else True
        if not bad and self.readable is not None:
            bad = not all(self.readable(a) for a in addrs)
        if function == modbus.FC_READ:
            if bad:
                return self._exception(address, function, 2)
            regs = [self.image[a] for a in addrs]
            self.on_read(start, count, regs)
            if self.blank_thresholds:
                regs = [0 if self.is_writable(a) else v for a, v in zip(addrs, regs)]
            body = struct.pack(">BBB", address, function, 2 * count) + b"".join(struct.pack(">H", v) for v in regs)
            return modbus.with_crc(body)
        if function == modbus.FC_WRITE:
            values = [struct.unpack(">H", request[7 + 2 * i : 9 + 2 * i])[0] for i in range(count)]
            if bad:
                return self._exception(address, function, 2)
            if start in self.refuse:
                return self._exception(address, function, self.refuse[start])
            self.writes.append((start, values))
            for reg, v in zip(addrs, values):
                if reg in self.floors and v < self.floors[reg]:
                    continue  # acked, not stored
                self.image[reg] = v
            self.on_write(start, values)
            self._save()
            self.drop_replies += self.silent_after_write
            if self.drop_acks:
                self.drop_acks -= 1
                raise NoReply("simulated pack applied the write but did not ack")
            return modbus.with_crc(request[:6])
        return self._exception(address, function, 1)

    def _save(self) -> None:
        if self.state_file:
            regs = {f"0x{r:02X}": self.image[r] for r in range(self.state_from, len(self.image), self.stride)}
            self.state_file.write_text(json.dumps({"registers": regs}, indent=1))

    @staticmethod
    def _exception(address: int, function: int, code: int) -> bytes:
        return modbus.with_crc(bytes([address, function | modbus.EXCEPTION_FLAG, code]))

    def close(self) -> None:
        pass
