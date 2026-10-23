"""Setting definitions, value encoding and the write rules, for any BMS.

A driver builds a SettingTable from its own register map. All checks run on
raw register values. Values in units are Decimals so that step checks and
degF conversion are exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from fractions import Fraction
from typing import Callable, Iterable, Iterator


class SettingError(ValueError):
    pass


def _dec(x) -> Decimal:
    return Decimal(str(x))


@dataclass(frozen=True)
class Setting:
    key: str
    register: int
    multiplier: Decimal
    offset: Decimal
    unit: str | None
    decimals: int
    minimum: Decimal
    maximum: Decimal
    step: Decimal
    group: str
    label: str

    @property
    def is_temperature(self) -> bool:
        return self.unit == "°C"

    @property
    def display_unit(self) -> str:
        if self.unit:
            return self.unit
        if self.key.endswith("_delay") or self.key.endswith("_release_time"):
            return "s"
        return ""

    def value(self, raw: int) -> Decimal:
        return raw * self.multiplier + self.offset

    def raw(self, value: Decimal) -> int:
        """Exact raw value; refuses a value between two register steps."""
        with localcontext() as ctx:
            ctx.traps[Inexact] = True  # never round a long input onto a step
            try:
                r = (value - self.offset) / self.multiplier
            except Inexact:
                raise SettingError(f"{self.label}: {value} is not a multiple of {self.multiplier}") from None
        if r != r.to_integral_value():
            raise SettingError(f"{self.label}: {value} is not a multiple of {self.multiplier}")
        r = int(r)
        if not 0 <= r <= 0xFFFF:
            raise SettingError(f"{self.label}: {value} does not fit the register")
        return r

    @property
    def raw_min(self) -> int:
        return self.raw(self.minimum)

    @property
    def raw_max(self) -> int:
        return self.raw(self.maximum)

    @property
    def raw_step(self) -> int:
        return int(self.step / self.multiplier)

    def limit_error(self, raw: int) -> str | None:
        """Why `raw` may not be written, or None if LIMITS allow it."""
        if not self.raw_min <= raw <= self.raw_max:
            return (
                f"{self.label} {self.format(raw)} is outside the safe range "
                f"{self.format(self.raw_min)} .. {self.format(self.raw_max)}"
            )
        if (raw - self.raw_min) % self.raw_step:
            u = f" {self.display_unit}" if self.display_unit else ""
            return f"{self.label} {self.value(raw)}{u} is not on the {self.step} step"
        return None

    def format(self, raw: int, fahrenheit: bool = False, unit: bool = True) -> str:
        v = self.value(raw)
        if self.is_temperature and fahrenheit:
            f = c_to_f(v)
            text = f"{f:.1f}".rstrip("0").rstrip(".")
            return f"{text} °F" if unit else text
        text = f"{v:.{self.decimals}f}"
        u = self.display_unit
        return f"{text} {u}" if unit and u else text

    def parse(self, text: str, fahrenheit: bool = False) -> int:
        """Raw value for user input such as '3.65', '3.65V', '140F' or '60C'."""
        s = text.strip().replace(" ", "")
        in_f = fahrenheit and self.is_temperature
        for suffix, is_f in (("°F", True), ("F", True), ("°C", False), ("C", False)):
            if self.is_temperature and s.upper().endswith(suffix.upper()):
                s, in_f = s[: -len(suffix)], is_f
                break
        else:
            u = self.display_unit
            if u and s.lower().endswith(u.lower()):
                s = s[: -len(u)]
        try:
            v = Decimal(s)
        except InvalidOperation:
            raise SettingError(f"{self.label}: '{text}' is not a number") from None
        if not v.is_finite():
            raise SettingError(f"{self.label}: '{text}' is not a number")
        if in_f:
            v = f_to_c_exact(v, self.label)
        return self.raw(v)


def c_to_f(c: Decimal) -> Decimal:
    return c * 9 / 5 + 32


def f_to_c_exact(f: Decimal, label: str = "temperature") -> Decimal:
    """degF to whole degC; refuses a degF value that is not a whole degC."""
    c = (Fraction(f) - 32) * Fraction(5, 9)
    if c.denominator != 1:
        near = round(c)
        raise SettingError(
            f"{label}: {f} °F is {float(c):.2f} °C, not a whole °C "
            f"(nearest: {near} °C = {c_to_f(Decimal(near))} °F)"
        )
    return Decimal(c.numerator)


@dataclass(frozen=True)
class Rule:
    lower: str
    upper: str
    strict: bool

    def holds(self, raw: dict[str, int]) -> bool:
        lo, hi = raw[self.lower], raw[self.upper]
        return lo < hi if self.strict else lo <= hi


@dataclass(frozen=True)
class LinkedPair:
    """Two settings that may only be set together, to the same value."""

    a: str
    b: str
    reason: str

    def __iter__(self) -> Iterator[str]:
        return iter((self.a, self.b))


def default_label(key: str) -> str:
    text = key.replace("_", " ")
    return text[0].upper() + text[1:]


class SettingTable:
    """One BMS's settings, the order rules between them and the linked pairs."""

    def __init__(self, settings: dict[str, Setting], order: list[Rule], linked: list[LinkedPair]):
        for r in order:
            for k in (r.lower, r.upper):
                if k not in settings:
                    raise ValueError(f"order rule names unknown setting '{k}'")
        for p in linked:
            for k in p:
                if k not in settings:
                    raise ValueError(f"linked pair names unknown setting '{k}'")
        self.settings = settings
        self.by_register: dict[int, Setting] = {s.register: s for s in settings.values()}
        self.order = list(order)
        self.linked = list(linked)
        self.rank = self._ranks()

    @classmethod
    def build(
        cls,
        table: dict[str, tuple],
        limits: dict[str, tuple],
        order: Iterable[tuple[str, str, bool]],
        linked: Iterable[LinkedPair] = (),
        overrides: dict[str, tuple] | None = None,
        group: Callable[[str], str] | None = None,
        label: Callable[[str], str] | None = None,
    ) -> "SettingTable":
        """From an esphome-style table: key -> (register, multiplier, offset, unit, class, decimals)."""
        overrides = overrides or {}
        settings = {}
        for key, (reg, mult, offset, unit, _cls, decimals) in table.items():
            lo, hi, step = overrides.get(key, limits[key])
            settings[key] = Setting(
                key=key,
                register=reg,
                multiplier=_dec(mult),
                offset=_dec(offset),
                unit=unit,
                decimals=decimals,
                minimum=_dec(lo),
                maximum=_dec(hi),
                step=_dec(step),
                group=group(key) if group else "Settings",
                label=label(key) if label else default_label(key),
            )
        return cls(settings, [Rule(lo, hi, strict) for lo, hi, strict in order], list(linked))

    def _ranks(self) -> dict[str, int]:
        """Height of each setting in the order graph (0 = nothing below it)."""
        below: dict[str, list[str]] = {k: [] for k in self.settings}
        for r in self.order:
            below[r.upper].append(r.lower)
        memo: dict[str, int] = {}

        def rank(k: str) -> int:
            if k not in memo:
                memo[k] = 1 + max((rank(b) for b in below[k]), default=-1)
            return memo[k]

        return {k: rank(k) for k in self.settings}

    def __getitem__(self, key: str) -> Setting:
        return self.settings[key]

    def __contains__(self, key: object) -> bool:
        return key in self.settings

    def __iter__(self) -> Iterator[str]:
        return iter(self.settings)

    def __len__(self) -> int:
        return len(self.settings)

    def get(self, key: str) -> Setting | None:
        return self.settings.get(key)

    def items(self):
        return self.settings.items()

    def pair(self, key: str) -> LinkedPair | None:
        return next((p for p in self.linked if key in (p.a, p.b)), None)

    def partner(self, key: str) -> str | None:
        p = self.pair(key)
        return None if p is None else (p.b if key == p.a else p.a)

    def describe_rule(self, rule: Rule, raw: dict[str, int] | None = None, fahrenheit: bool = False) -> str:
        lo, hi = self.settings[rule.lower], self.settings[rule.upper]
        rel = "below" if rule.strict else "at or below"
        if raw is None:
            return f"{lo.label} must stay {rel} {hi.label}"
        return (
            f"{lo.label} ({lo.format(raw[rule.lower], fahrenheit)}) must stay {rel} "
            f"{hi.label} ({hi.format(raw[rule.upper], fahrenheit)})"
        )


# Transitional: the EG4-LL names other modules still import. Removed in Task 6.
_LEGACY = ("SETTINGS", "BY_REGISTER", "ORDER", "RANK", "LINKED_PAIRS", "TABLE")


def __getattr__(name: str):
    if name in _LEGACY:
        from .drivers.eg4_ll.table import TABLE

        return {
            "SETTINGS": TABLE.settings,
            "BY_REGISTER": TABLE.by_register,
            "ORDER": TABLE.order,
            "RANK": TABLE.rank,
            "LINKED_PAIRS": TABLE.linked,
            "TABLE": TABLE,
        }[name]
    if name in ("REG_BLOCK", "BLOCK_COUNT", "REG_BLOCK_LAST", "REG_CLOCK", "is_threshold_register"):
        from .drivers.eg4_ll import registers

        return getattr(registers, {"is_threshold_register": "is_writable"}.get(name, name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
