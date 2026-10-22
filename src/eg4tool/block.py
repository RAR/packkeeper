"""Decoded views of the parameter block and the realtime registers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .settings import BLOCK_COUNT, BY_REGISTER, REG_BLOCK, REG_CLOCK, SETTINGS, is_threshold_register

REG_MODEL, MODEL_REGS = 0x69, 11
REG_FIRMWARE, FIRMWARE_REGS = 0x75, 3
REG_SERIAL, SERIAL_REGS = 0x78, 8


def pack_clock(t: datetime) -> int:
    """BMS clock: year-2000 << 26 | month << 22 | day << 17 | hour << 12 | min << 6 | sec."""
    if not 2000 <= t.year <= 2063:
        raise ValueError(f"year {t.year} does not fit the BMS clock")
    return (
        (t.year - 2000) << 26 | t.month << 22 | t.day << 17 | t.hour << 12 | t.minute << 6 | t.second
    )


def unpack_clock(packed: int) -> datetime | None:
    try:
        return datetime(
            2000 + (packed >> 26),
            (packed >> 22) & 0x0F,
            (packed >> 17) & 0x1F,
            (packed >> 12) & 0x1F,
            (packed >> 6) & 0x3F,
            packed & 0x3F,
        )
    except ValueError:
        return None


def ascii_text(regs: list[int]) -> str:
    out = []
    for r in regs:
        for c in (r >> 8, r & 0xFF):
            if c == 0:
                return "".join(out).rstrip()
            if 0x20 <= c <= 0x7E:
                out.append(chr(c))
    return "".join(out).rstrip()


class BlankBlockError(Exception):
    """The port answered the parameter read with every threshold zero."""


@dataclass(frozen=True)
class Block:
    """The 91 registers of the parameter block, 0x2D-0x87, unknowns included."""

    registers: tuple[int, ...]

    def __post_init__(self):
        if len(self.registers) != BLOCK_COUNT:
            raise ValueError(f"a parameter block has {BLOCK_COUNT} registers, got {len(self.registers)}")
        for v in self.registers:
            if not 0 <= v <= 0xFFFF:
                raise ValueError(f"register value {v} does not fit 16 bits")

    def reg(self, register: int) -> int:
        return self.registers[register - REG_BLOCK]

    def regs(self, start: int, count: int) -> list[int]:
        i = start - REG_BLOCK
        return list(self.registers[i : i + count])

    def raw(self, key: str) -> int:
        return self.reg(SETTINGS[key].register)

    def raws(self) -> dict[str, int]:
        return {k: self.reg(s.register) for k, s in SETTINGS.items()}

    @property
    def thresholds_blank(self) -> bool:
        return all(
            v == 0 for i, v in enumerate(self.registers) if is_threshold_register(REG_BLOCK + i)
        )

    def check_not_blank(self) -> "Block":
        if self.thresholds_blank:
            raise BlankBlockError(
                "the pack answered with every threshold zero. That is what the RS485 "
                "paralleling bus returns: the parameter block only comes over RS232, with the "
                "adapter in the pack's right-most paralleling port. Refusing to use this read."
            )
        return self

    @property
    def model(self) -> str:
        return ascii_text(self.regs(REG_MODEL, MODEL_REGS))

    @property
    def firmware(self) -> str:
        return ascii_text(self.regs(REG_FIRMWARE, FIRMWARE_REGS))

    @property
    def serial(self) -> str:
        return ascii_text(self.regs(REG_SERIAL, SERIAL_REGS))

    @property
    def clock_raw(self) -> int:
        return self.reg(REG_CLOCK) << 16 | self.reg(REG_CLOCK + 1)

    @property
    def clock(self) -> datetime | None:
        return unpack_clock(self.clock_raw)

    def with_raws(self, changes: dict[int, int]) -> "Block":
        regs = list(self.registers)
        for register, raw in changes.items():
            regs[register - REG_BLOCK] = raw
        return Block(tuple(regs))

    def unknown_registers(self) -> dict[int, int]:
        """Threshold-range registers no setting decodes; never written."""
        return {
            REG_BLOCK + i: v
            for i, v in enumerate(self.registers)
            if is_threshold_register(REG_BLOCK + i) and REG_BLOCK + i not in BY_REGISTER
        }

    def threshold_registers(self) -> dict[int, int]:
        return {
            REG_BLOCK + i: v
            for i, v in enumerate(self.registers)
            if is_threshold_register(REG_BLOCK + i)
        }


# Realtime registers 0x00-0x26 (EG4-LL Modbus protocol V01.06).
STATUS_BITS = {0x01: "Charging", 0x02: "Discharging", 0x04: "Protect", 0x08: "Charge limited"}
ALARM_BITS = {
    0x0001: "Pack OV",
    0x0002: "Cell OV",
    0x0004: "Pack UV",
    0x0008: "Cell UV",
    0x0010: "Charge OC",
    0x0020: "Discharge OC",
    0x0040: "Abnormal temp",
    0x0080: "MOSFET overheat",
    0x0100: "Charge OT",
    0x0200: "Discharge OT",
    0x0400: "Charge UT",
    0x0800: "Discharge UT",
    0x1000: "Low capacity",
}
WARNING_BITS = {**ALARM_BITS, 0x2000: "Float stopped"}
PROTECTION_BITS = {**ALARM_BITS, 0x2000: "Discharge short circuit"}
ERROR_BITS = {0x0001: "Voltage error", 0x0002: "Temperature error", 0x0004: "Current flow error", 0x0010: "Cell unbalance"}
CHARGE_BLOCKERS = 0x0001 | 0x0002 | 0x0010 | 0x0100 | 0x0400
DISCHARGE_BLOCKERS = 0x0004 | 0x0008 | 0x0020 | 0x0200 | 0x0800 | 0x2000


def decode_bits(value: int, names: dict[int, str]) -> list[str]:
    out = [name for bit, name in names.items() if value & bit]
    unknown = value & ~sum(names)
    if unknown:
        out.append(f"unknown 0x{unknown:04X}")
    return out


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _s8(v: int) -> int:
    return v - 0x100 if v & 0x80 else v


@dataclass
class Realtime:
    registers: list[int]  # 0x00-0x26
    pack_voltage: float = 0.0
    current: float = 0.0
    cells: list[float] = field(default_factory=list)
    pcb_temp: int = 0
    avg_temp: int = 0
    max_temp: int = 0
    remaining_ah: int = 0
    max_charge_current: int = 0
    soh: int = 0
    soc: int = 0
    status: int = 0
    warning: int = 0
    protection: int = 0
    error: int = 0
    cycles: int = 0
    full_capacity_ah: float = 0.0
    temps: list[int] = field(default_factory=list)
    cell_count: int = 0
    design_capacity_ah: float = 0.0
    balancing: int = 0

    @classmethod
    def decode(cls, r: list[int]) -> "Realtime":
        if len(r) != 0x27:
            raise ValueError(f"realtime image is 39 registers, got {len(r)}")
        count = r[0x24] if 0 < r[0x24] <= 16 else 16
        rt = cls(registers=list(r))
        rt.pack_voltage = r[0x00] / 100
        rt.current = _s16(r[0x01]) / 100
        rt.cells = [r[0x02 + i] / 1000 for i in range(count)]
        rt.pcb_temp, rt.avg_temp, rt.max_temp = _s16(r[0x12]), _s16(r[0x13]), _s16(r[0x14])
        rt.remaining_ah = r[0x15]
        rt.max_charge_current = r[0x16]
        rt.soh, rt.soc = r[0x17], r[0x18]
        rt.status, rt.warning, rt.protection, rt.error = r[0x19], r[0x1A], r[0x1B], r[0x1C]
        rt.cycles = r[0x1D] << 16 | r[0x1E]
        rt.full_capacity_ah = (r[0x1F] << 16 | r[0x20]) / 3_600_000
        rt.temps = [t for v in r[0x21:0x24] for t in (_s8(v >> 8), _s8(v & 0xFF))]
        rt.cell_count = r[0x24]
        rt.design_capacity_ah = r[0x25] / 10
        rt.balancing = r[0x26]
        return rt

    @property
    def status_text(self) -> str:
        parts = decode_bits(self.status & 0xFF, STATUS_BITS) or ["Standby"]
        if self.status & 0x8000:
            parts.insert(0, "Heating")
        return ", ".join(parts)

    @property
    def warnings(self) -> list[str]:
        return decode_bits(self.warning, WARNING_BITS)

    @property
    def protections(self) -> list[str]:
        return decode_bits(self.protection, PROTECTION_BITS)

    @property
    def errors(self) -> list[str]:
        return decode_bits(self.error, ERROR_BITS)

    @property
    def charge_allowed(self) -> bool:
        return not self.protection & CHARGE_BLOCKERS

    @property
    def discharge_allowed(self) -> bool:
        return not self.protection & DISCHARGE_BLOCKERS

    @property
    def cell_min(self) -> float:
        return min(self.cells, default=0.0)

    @property
    def cell_max(self) -> float:
        return max(self.cells, default=0.0)

    @property
    def cell_delta(self) -> float:
        return self.cell_max - self.cell_min

    @property
    def balancing_cells(self) -> list[int]:
        return [i + 1 for i in range(16) if self.balancing >> i & 1]

    def summary_lines(self) -> list[str]:
        lines = [
            f"Pack       {self.pack_voltage:6.2f} V   {self.current:+7.2f} A   SOC {self.soc}%   SOH {self.soh}%",
            f"Status     {self.status_text}",
            f"Capacity   {self.remaining_ah} Ah left of {self.full_capacity_ah:.1f} Ah full "
            f"(design {self.design_capacity_ah:.1f} Ah), {self.cycles} cycles",
            f"Cells      min {self.cell_min:.3f} V  max {self.cell_max:.3f} V  "
            f"delta {self.cell_delta * 1000:.0f} mV",
        ]
        for row in range(0, len(self.cells), 8):
            chunk = self.cells[row : row + 8]
            lines.append(
                "  " + "  ".join(f"{row + i + 1:2d}:{v:.3f}" for i, v in enumerate(chunk))
            )
        lines += [
            f"Temps      PCB {self.pcb_temp} °C  avg {self.avg_temp} °C  max {self.max_temp} °C  "
            f"sensors {', '.join(str(t) for t in self.temps)} °C",
            f"Warnings   {', '.join(self.warnings) or 'none'}",
            f"Protection {', '.join(self.protections) or 'none'}  "
            f"(charge {'allowed' if self.charge_allowed else 'BLOCKED'}, "
            f"discharge {'allowed' if self.discharge_allowed else 'BLOCKED'})",
            f"Errors     {', '.join(self.errors) or 'none'}",
        ]
        if self.balancing_cells:
            lines.append(f"Balancing  cells {', '.join(map(str, self.balancing_cells))}")
        return lines
