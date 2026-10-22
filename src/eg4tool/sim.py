"""An in-process fake pack for tests and for trying the tool without hardware.

It serves registers 0x00-0x87 over the same transact() interface as the
serial transport and applies FC16 writes. Like the real pack it can ack a
write and silently not store it (`floors`: register -> lowest raw value it
keeps), and it can answer the parameter read with every threshold zero, as
the RS485 paralleling bus does.
"""

from __future__ import annotations

import json
import struct
from datetime import datetime
from pathlib import Path

from . import modbus
from .block import pack_clock, unpack_clock
from .profiles import EG4_SHIPPED, REALTIME_FRAMES, registers_of
from .settings import REG_BLOCK, is_threshold_register
from .transport import NoReply

IMAGE_SIZE = 0x88

# The real pack keeps 0.039 V when asked for 0.010 V and holds 0.020 V.
DEFAULT_FLOORS = {0x39: 20}


class FakePack:
    def __init__(
        self,
        floors: dict[int, int] | None = None,
        blank_thresholds: bool = False,
        state_file: Path | None = None,
    ):
        self.floors = DEFAULT_FLOORS if floors is None else floors
        self.blank_thresholds = blank_thresholds
        self.state_file = state_file
        self.drop_replies = 0  # next N requests get no reply
        self.drop_acks = 0  # next N writes are applied but not acked
        self.silent_after_write = 0  # after a write, ignore this many requests
        self.refuse = {}  # register -> Modbus exception code answered to a write
        self.writes: list[tuple[int, list[int]]] = []
        self._clock_base: tuple[datetime, datetime] | None = None
        self.image = [0] * IMAGE_SIZE
        rt = registers_of(REALTIME_FRAMES[0]) + registers_of(REALTIME_FRAMES[1])
        self.image[: len(rt)] = rt
        self.image[REG_BLOCK : REG_BLOCK + len(EG4_SHIPPED.block.registers)] = EG4_SHIPPED.block.registers
        self.port = "simulated pack"
        if state_file and state_file.exists():
            saved = json.loads(state_file.read_text())
            for reg, v in saved.get("registers", {}).items():
                self.image[int(reg, 0)] = v

    def transact(self, request: bytes) -> bytes:
        if not modbus.crc_ok(request):
            raise NoReply("simulated pack ignores frames with a bad CRC")
        if self.drop_replies:
            self.drop_replies -= 1
            raise NoReply("simulated pack did not answer")
        address, function = request[0], request[1]
        start, count = struct.unpack(">HH", request[2:6])
        if function == modbus.FC_READ:
            if start + count > IMAGE_SIZE:
                return self._exception(address, function, 2)
            regs = list(self.image[start : start + count])
            self._clock_tick(start, count, regs)
            if self.blank_thresholds:
                regs = [0 if is_threshold_register(start + i) else v for i, v in enumerate(regs)]
            body = struct.pack(">BBB", address, function, 2 * count) + b"".join(
                struct.pack(">H", v) for v in regs
            )
            return modbus.with_crc(body)
        if function == modbus.FC_WRITE:
            values = [
                struct.unpack(">H", request[7 + 2 * i : 9 + 2 * i])[0] for i in range(count)
            ]
            if start + count > IMAGE_SIZE:
                return self._exception(address, function, 2)
            if start in self.refuse:
                return self._exception(address, function, self.refuse[start])
            self.writes.append((start, values))
            for i, v in enumerate(values):
                reg = start + i
                if reg in self.floors and v < self.floors[reg]:
                    continue  # acked, not stored
                self.image[reg] = v
            if start == 0x2D:
                t = unpack_clock(self.image[0x2D] << 16 | self.image[0x2E])
                self._clock_base = (datetime.now(), t) if t else None
            self._save()
            self.drop_replies += self.silent_after_write
            if self.drop_acks:
                self.drop_acks -= 1
                raise NoReply("simulated pack applied the write but did not ack")
            return modbus.with_crc(request[:6])
        return self._exception(address, function, 1)

    def _clock_tick(self, start: int, count: int, regs: list[int]) -> None:
        # Serve a running clock once it has been set; until then the captured value.
        if self._clock_base is None:
            return
        set_at, base = self._clock_base
        now = base + (datetime.now() - set_at)
        packed = pack_clock(now)
        for reg, v in ((0x2D, packed >> 16), (0x2E, packed & 0xFFFF)):
            if start <= reg < start + count:
                regs[reg - start] = v

    def _save(self) -> None:
        if self.state_file:
            regs = {f"0x{r:02X}": self.image[r] for r in range(REG_BLOCK, IMAGE_SIZE)}
            self.state_file.write_text(json.dumps({"registers": regs}, indent=1))

    @staticmethod
    def _exception(address: int, function: int, code: int) -> bytes:
        return modbus.with_crc(bytes([address, function | modbus.EXCEPTION_FLAG, code]))

    def close(self) -> None:
        pass
