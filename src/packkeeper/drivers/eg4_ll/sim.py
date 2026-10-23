"""The simulated EG4-LL pack: the shipped block, the captured live values,
the balance-delta floor and a clock that runs once set."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ... import modbus
from ...sim import FakeModbusDevice
from .profiles import EG4_SHIPPED_FRAME, REALTIME_FRAMES
from .registers import REG_BLOCK, REG_CLOCK, is_writable, pack_clock, unpack_clock

IMAGE_SIZE = 0x88

# The real pack keeps 0.039 V when asked for 0.010 V and holds 0.020 V.
DEFAULT_FLOORS = {0x39: 20}


class FakePack(FakeModbusDevice):
    def __init__(
        self,
        floors: dict[int, int] | None = None,
        blank_thresholds: bool = False,
        state_file: Path | None = None,
    ):
        image = [0] * IMAGE_SIZE
        rt = modbus.registers_of(REALTIME_FRAMES[0]) + modbus.registers_of(REALTIME_FRAMES[1])
        image[: len(rt)] = rt
        block = modbus.registers_of(EG4_SHIPPED_FRAME)
        image[REG_BLOCK : REG_BLOCK + len(block)] = block
        super().__init__(
            image,
            is_writable,
            floors=DEFAULT_FLOORS if floors is None else floors,
            blank_thresholds=blank_thresholds,
            state_file=state_file,
            state_from=REG_BLOCK,
        )
        self._clock_base: tuple[datetime, datetime] | None = None

    def on_write(self, start: int, values: list[int]) -> None:
        if start == REG_CLOCK:
            t = unpack_clock(self.image[REG_CLOCK] << 16 | self.image[REG_CLOCK + 1])
            self._clock_base = (datetime.now(), t) if t else None

    def on_read(self, start: int, count: int, regs: list[int]) -> None:
        # Serve a running clock once it has been set; until then the captured value.
        if self._clock_base is None:
            return
        set_at, base = self._clock_base
        packed = pack_clock(base + (datetime.now() - set_at))
        for reg, v in ((REG_CLOCK, packed >> 16), (REG_CLOCK + 1, packed & 0xFFFF)):
            if start <= reg < start + count:
                regs[reg - start] = v
