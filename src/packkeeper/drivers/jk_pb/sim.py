"""The simulated JK-PB: a plausible 16S LiFePO4 pack. Invented test data,
not JK's defaults. Its stride and word order can differ from the driver's,
to exercise the refusals."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...sim import FakeModbusDevice
from . import registers as R
from .table import FIELDS

SETTING_VALUES = {
    "smart_sleep_voltage": 3000, "cell_uv_protection": 2800, "cell_uv_release": 2900,
    "cell_ov_protection": 3650, "cell_ov_release": 3590, "balance_trigger_delta": 10,
    "soc_100_voltage": 3450, "soc_0_voltage": 3000, "charge_voltage": 3550, "float_voltage": 3400,
    "power_off_voltage": 2700, "charge_oc_current": 150000, "charge_oc_delay": 30,
    "charge_oc_release_time": 60, "discharge_oc_current": 200000, "discharge_oc_delay": 30,
    "discharge_oc_release_time": 60, "short_circuit_release_time": 60, "max_balance_current": 2000,
    "charge_ot_protection": 550, "charge_ot_release": 500, "discharge_ot_protection": 650,
    "discharge_ot_release": 600, "charge_ut_protection": 20, "charge_ut_release": 50,
    "mos_ot_protection": 900, "mos_ot_release": 800, "design_capacity": 200000,
    "short_circuit_delay": 1500, "balance_start_voltage": 3300,
}
CLOCK = datetime(2026, 10, 23, 8, 0, 0)
FLOOR_KEY, FLOOR = "max_balance_current", 300  # keeps the old value below 0.3 A, like a pack would


def _text(s: str, nbytes: int) -> list[int]:
    b = s.encode().ljust(nbytes, b"\0")
    return [b[i] << 8 | b[i + 1] for i in range(0, nbytes, 2)]


class FakeJK(FakeModbusDevice):
    def __init__(
        self,
        driver,
        *,
        stride: int | None = None,
        word_order: str = R.WORD_ORDER,
        floors: dict[int, int] | None = None,
        blank_thresholds: bool = False,
        state_file: Path | None = None,
    ):
        self.sim_stride = driver.stride if stride is None else stride
        self.word_order = word_order
        image = [0] * 0x1700

        def put(area: int, offset: int, words: list[int]) -> None:
            for i, w in enumerate(words):
                image[R.addr(area, offset + 2 * i, self.sim_stride)] = w

        def u32(v: int) -> list[int]:
            v &= 0xFFFFFFFF
            words = [v >> 16, v & 0xFFFF]
            return words[::-1] if word_order == "low_first" else words

        for key, raw in SETTING_VALUES.items():
            put(R.SETTINGS, FIELDS[key][0], u32(raw))
        put(R.SETTINGS, 0x6C, u32(16))  # cell count
        for off in (0x70, 0x74, 0x78):  # charge, discharge, balance switches on
            put(R.SETTINGS, off, u32(1))
        for off in range(0x88, 0x108, 4):
            put(R.SETTINGS, off, u32(1200))  # balance-wire resistance, uΩ
        put(R.SETTINGS, 0x108, u32(1))  # device address
        put(R.SETTINGS, 0x10C, u32(5))  # pre-charge time
        put(R.SETTINGS, 0x114, [0x0010])  # LCD always on
        put(R.SETTINGS, 0x118, [24 << 8])  # smart-sleep time 24 h

        put(R.DEVICE, 0x00, _text("JK_PB2A16S20P", 16))
        put(R.DEVICE, 0x10, _text("19A", 8))
        put(R.DEVICE, 0x18, _text("19.08", 8))
        put(R.DEVICE, 0x20, u32(3_600_000))
        put(R.DEVICE, 0x24, u32(12))

        for i in range(16):
            put(R.LIVE, 2 * i, [3300 + i])
        put(R.LIVE, 0x40, u32(0xFFFF))
        put(R.LIVE, 0x8A, [312])  # MOSFET 31.2 °C
        put(R.LIVE, 0x90, u32(52830))
        put(R.LIVE, 0x94, u32(169000))
        put(R.LIVE, 0x98, u32(-3200))
        put(R.LIVE, 0x9C, [245])
        put(R.LIVE, 0x9E, [251])
        put(R.LIVE, 0xA6, [76])  # balancer off, SOC 76 %
        put(R.LIVE, 0xA8, u32(152000))
        put(R.LIVE, 0xAC, u32(200000))
        put(R.LIVE, 0xB0, u32(37))
        put(R.LIVE, 0xB8, [100 << 8])  # SOH 100 %
        put(R.LIVE, 0xC0, [0x0101])  # charge on, discharge on
        put(R.LIVE, 0xD0, [0b111 << 8])  # MOSFET sensor and battery sensors 1-2 present
        put(R.LIVE, R.CLOCK_OFFSET, u32(R.pack_clock(CLOCK)))

        # What the pack accepts as settings, at its own addresses.
        self._writable = {R.addr(R.SETTINGS, FIELDS[k][0] + 2 * i, self.sim_stride) for k in FIELDS for i in range(2)}
        low = R.addr(R.SETTINGS, FIELDS[FLOOR_KEY][0] + (2 if word_order == "high_first" else 0), self.sim_stride)
        super().__init__(
            image,
            lambda a: a in self._writable,
            floors={low: FLOOR} if floors is None else floors,
            blank_thresholds=blank_thresholds,
            state_file=state_file,
            state_from=R.SETTINGS,
            stride=self.sim_stride,
        )
        self.clock_set = R.addr(R.COMMANDS, R.CLOCK_SET_OFFSET, self.sim_stride)
        self.clock_at = R.addr(R.LIVE, R.CLOCK_OFFSET, self.sim_stride)
        self._clock_base: tuple[datetime, datetime] | None = None

    def _u32_at(self, address: int) -> int:
        words = [self.image[address], self.image[address + self.sim_stride]]
        if self.word_order == "low_first":
            words.reverse()
        return words[0] << 16 | words[1]

    def on_write(self, start: int, values: list[int]) -> None:
        if start == self.clock_set:
            self._clock_base = (datetime.now(), R.unpack_clock(self._u32_at(self.clock_set)))

    def on_read(self, start: int, count: int, regs: list[int]) -> None:
        if self._clock_base is None:
            return
        set_at, base = self._clock_base
        secs = R.pack_clock(base + (datetime.now() - set_at))
        words = [secs >> 16, secs & 0xFFFF]
        if self.word_order == "low_first":
            words.reverse()
        for k, w in enumerate(words):
            i, rem = divmod(self.clock_at + k * self.sim_stride - start, self.sim_stride)
            if rem == 0 and 0 <= i < count:
                regs[i] = w
