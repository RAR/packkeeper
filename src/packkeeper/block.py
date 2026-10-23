"""A settings block read from a BMS, bound to the driver that knows its layout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .drivers.base import Driver


def _a(label: str) -> str:
    return ("an " if label[:1].upper() in "AEFHILMNORSX" else "a ") + label


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
    """The device answered the settings read with a block the driver refuses."""


class DriverMismatch(Exception):
    """Two blocks (or a block and a device) belong to different BMS types."""


@dataclass(frozen=True)
class Identity:
    model: str = ""
    firmware: str = ""
    serial: str = ""
    clock: datetime | None = None
    clock_raw: int | None = None  # for showing an invalid clock


@dataclass(frozen=True)
class Block:
    """The settings block, unknown registers included."""

    driver: "Driver" = field(repr=False)
    registers: tuple[int, ...]

    def __post_init__(self):
        n = self.driver.block_count
        if len(self.registers) != n:
            raise ValueError(f"a {self.driver.label} settings block has {n} registers, got {len(self.registers)}")
        for v in self.registers:
            if not 0 <= v <= 0xFFFF:
                raise ValueError(f"register value {v} does not fit 16 bits")

    @property
    def start(self) -> int:
        return self.driver.block_start

    def reg(self, register: int) -> int:
        return self.registers[register - self.start]

    def regs(self, start: int, count: int) -> list[int]:
        i = start - self.start
        return list(self.registers[i : i + count])

    def raw(self, key: str) -> int:
        return self.reg(self.driver.table[key].register)

    def raws(self) -> dict[str, int]:
        return {k: self.reg(s.register) for k, s in self.driver.table.items()}

    @property
    def identity(self) -> "Identity":
        return self.driver.identity(self)

    @property
    def model(self) -> str:
        return self.identity.model

    @property
    def firmware(self) -> str:
        return self.identity.firmware

    @property
    def serial(self) -> str:
        return self.identity.serial

    @property
    def clock(self) -> datetime | None:
        return self.identity.clock

    @property
    def clock_raw(self) -> int | None:
        return self.identity.clock_raw

    @property
    def thresholds_blank(self) -> bool:
        return all(v == 0 for r, v in self.threshold_registers().items())

    def check_not_blank(self) -> "Block":
        reason = self.driver.blank_reason(self)
        if reason:
            raise BlankBlockError(reason)
        return self

    def same_driver(self, other: "Block", what: str = "the other block") -> None:
        if other.driver is not self.driver:
            raise DriverMismatch(
                f"{what} is from {_a(other.driver.label)} BMS and this one is {_a(self.driver.label)}; "
                "settings never move between different BMS types"
            )

    def with_raws(self, changes: dict[int, int]) -> "Block":
        regs = list(self.registers)
        for register, raw in changes.items():
            regs[register - self.start] = raw
        return Block(self.driver, tuple(regs))

    def threshold_registers(self) -> dict[int, int]:
        """Every register the driver allows writing, known or not."""
        return {
            self.start + i: v for i, v in enumerate(self.registers) if self.driver.is_writable(self.start + i)
        }

    def unknown_registers(self) -> dict[int, int]:
        """Writable-range registers no setting decodes; never written."""
        known = self.driver.table.by_register
        return {r: v for r, v in self.threshold_registers().items() if r not in known}
