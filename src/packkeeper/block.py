"""A settings block read from a BMS, bound to the driver that knows its layout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

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


class MissingRegister(Exception):
    """A value the pack did not send when the block was read."""


class Area(NamedTuple):
    """Registers read in one go: register i is at start + i * stride."""

    start: int
    count: int
    stride: int = 1

    def addresses(self) -> range:
        return range(self.start, self.start + self.count * self.stride, self.stride)


@dataclass(frozen=True)
class Identity:
    model: str = ""
    firmware: str = ""
    serial: str = ""
    clock: datetime | None = None
    clock_raw: int | None = None  # for showing an invalid clock


@dataclass(frozen=True)
class Block:
    """The registers of every area the driver reads, unknown ones included.

    A register the pack did not send (a short reply, or a refused read of an
    area with no settings in it) is None: shown as not sent, saved as null,
    never written, and never guessed.
    """

    driver: "Driver" = field(repr=False)
    areas: tuple[tuple[int | None, ...], ...]

    def __post_init__(self):
        layout = self.driver.areas
        if len(self.areas) != len(layout):
            raise ValueError(f"a {self.driver.label} block has {len(layout)} area(s), got {len(self.areas)}")
        for area, values in zip(layout, self.areas):
            if len(values) != area.count:
                raise ValueError(
                    f"a {self.driver.label} settings block has {area.count} registers at 0x{area.start:02X}, "
                    f"got {len(values)}"
                )
            for v in values:
                if v is not None and not 0 <= v <= 0xFFFF:
                    raise ValueError(f"register value {v} does not fit 16 bits")

    @property
    def registers(self) -> tuple[int | None, ...]:
        return tuple(v for values in self.areas for v in values)

    def _locate(self, address: int) -> tuple[int, int]:
        for n, area in enumerate(self.driver.areas):
            i, rem = divmod(address - area.start, area.stride)
            if rem == 0 and 0 <= i < area.count:
                return n, i
        raise KeyError(f"0x{address:02X} is not in a {self.driver.label} block")

    def sent(self, register: int) -> bool:
        n, i = self._locate(register)
        return self.areas[n][i] is not None

    def reg(self, register: int) -> int:
        n, i = self._locate(register)
        v = self.areas[n][i]
        if v is None:
            raise MissingRegister(f"the pack did not send 0x{register:02X}")
        return v

    def regs(self, start: int, count: int) -> list[int]:
        n, i = self._locate(start)
        if i + count > len(self.areas[n]):
            raise KeyError(f"0x{start:02X} x{count} runs past the end of its area")
        values = self.areas[n][i : i + count]
        if None in values:
            stride = self.driver.areas[n].stride
            raise MissingRegister(f"the pack did not send 0x{start + values.index(None) * stride:02X}")
        return list(values)

    def addresses(self) -> list[tuple[int, int | None]]:
        """(address, value) for every register, area by area; None if not sent."""
        return [(a, v) for area, values in zip(self.driver.areas, self.areas) for a, v in zip(area.addresses(), values)]

    @property
    def present_registers(self) -> set[int]:
        return {a for a, v in self.addresses() if v is not None}

    @property
    def missing_registers(self) -> set[int]:
        return {a for a, v in self.addresses() if v is None}

    def has(self, key: str) -> bool:
        """Whether the pack sent every register of this setting."""
        return all(self.sent(r) for r in self.driver.table[key].registers)

    @property
    def missing_keys(self) -> list[str]:
        return [k for k in self.driver.table if not self.has(k)]

    def raw(self, key: str) -> int:
        s = self.driver.table[key]
        try:
            return s.decode([self.reg(r) for r in s.registers])
        except MissingRegister:
            raise MissingRegister(f"the pack did not send {s.label} (0x{s.register:02X})") from None

    def raws(self) -> dict[str, int]:
        """Every setting the pack sent."""
        return {k: self.raw(k) for k in self.driver.table if self.has(k)}

    @property
    def identity(self) -> "Identity":
        try:
            return self.driver.identity(self)
        except MissingRegister:
            return Identity()

    def info_lines(self) -> list[tuple[str, str]]:
        try:
            return self.driver.info_lines(self)
        except MissingRegister:
            return []

    def identity_warnings(self) -> list[str]:
        try:
            return self.driver.identity_warnings(self)
        except MissingRegister:
            return []

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

    def blank_reason(self) -> str | None:
        """Why this read must not be used, or None."""
        if not self.threshold_registers():
            return f"the {self.driver.label} sent none of its settings registers; refusing to use this read"
        try:
            return self.driver.blank_reason(self)
        except MissingRegister as e:
            return f"{e}, which is needed to check this read; refusing to use it"

    def check_not_blank(self) -> "Block":
        reason = self.blank_reason()
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
        """A copy with these values: a setting's start address takes its whole raw value."""
        areas = [list(values) for values in self.areas]
        by_register = self.driver.table.by_register
        for register, raw in changes.items():
            s = by_register.get(register)
            pairs = zip(s.registers, s.encode(raw)) if s else ((register, raw),)
            for address, word in pairs:
                n, i = self._locate(address)
                if areas[n][i] is None:
                    raise MissingRegister(f"the pack did not send 0x{address:02X}; it cannot be given a value")
                areas[n][i] = word
        return Block(self.driver, tuple(tuple(a) for a in areas))

    def threshold_registers(self) -> dict[int, int]:
        """Every register the driver allows writing, known or not, that the pack sent."""
        return {a: v for a, v in self.addresses() if v is not None and self.driver.is_writable(a)}

    def unknown_registers(self) -> dict[int, int]:
        """Writable registers no setting covers; never written."""
        covering = self.driver.table.covering
        return {r: v for r, v in self.threshold_registers().items() if r not in covering}
