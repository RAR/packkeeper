"""Setting definitions, value encoding and the write rules.

The tables come from settings_table.py, vendored unchanged from
esphome-eg4-bms (components/eg4_bms/settings_table.py), so the component and
this tool share one source of truth. Anything this tool adds on top is here.

All checks run on raw register values. Values in units are Decimals so that
step checks and degF conversion are exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from fractions import Fraction

from . import settings_table

REG_BLOCK = 0x2D  # parameter block FC03 0x2D x91, 0x2D-0x87
BLOCK_COUNT = 0x5B
REG_BLOCK_LAST = REG_BLOCK + BLOCK_COUNT - 1
REG_CLOCK = 0x2D  # packed clock, 0x2D-0x2E, high word first


def is_threshold_register(reg: int) -> bool:
    """Writable thresholds: not the clock (0x2D-0x2E), not identity (0x69-0x7F)."""
    return 0x2F <= reg <= 0x68 or 0x80 <= reg <= REG_BLOCK_LAST


# Tightened on top of the vendored LIMITS. The pack acks a balance delta below
# its floor and keeps the old value (0.010 V acked, 0.039 V kept; 0.020 V held).
LIMIT_OVERRIDES = {
    "balance_delta_voltage": (0.020, 0.1, 0.001),
}

# settings_table.py has 0x52 = short circuit and 0x54 = discharge OC 2;
# REGISTER_MAP.md says the reverse. Both ship as 300 A, so no read tells them
# apart. Until a deliberate test settles it, they are only set together, to
# the same value.
LINKED_PAIRS = [("short_circuit_current", "discharge_overcurrent_2_protection")]

GROUPS = [
    ("Balancing", ("balance_", "low_capacity")),
    ("Cell voltage", ("cell_",)),
    ("Pack voltage", ("pack_",)),
    ("Charge current", ("charge_overcurrent",)),
    ("Discharge current", ("discharge_overcurrent", "short_circuit")),
    ("Charge temperature", ("charge_under", "charge_over")),
    ("Discharge temperature", ("discharge_under", "discharge_over")),
    ("MOSFET temperature", ("mos_",)),
    ("Ambient temperature", ("ambient_",)),
    ("Heater", ("heater_",)),
]

_WORDS = {
    "overvoltage": "OV",
    "undervoltage": "UV",
    "overcurrent": "OC",
    "overtemp": "OT",
    "undertemp": "UT",
    "mos": "MOSFET",
}


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


def _group(key: str) -> str:
    for name, prefixes in GROUPS:
        if key.startswith(prefixes):
            return name
    return "Other"


def _label(key: str) -> str:
    words = [_WORDS.get(w, w) for w in key.split("_")]
    text = " ".join(words)
    return text[0].upper() + text[1:]


def _build() -> dict[str, Setting]:
    out = {}
    for key, (reg, mult, offset, unit, _cls, decimals) in settings_table.SETTINGS.items():
        lo, hi, step = LIMIT_OVERRIDES.get(key, settings_table.LIMITS[key])
        out[key] = Setting(
            key=key,
            register=reg,
            multiplier=_dec(mult),
            offset=_dec(offset),
            unit=unit,
            decimals=decimals,
            minimum=_dec(lo),
            maximum=_dec(hi),
            step=_dec(step),
            group=_group(key),
            label=_label(key),
        )
    return out


SETTINGS: dict[str, Setting] = _build()
BY_REGISTER: dict[int, Setting] = {s.register: s for s in SETTINGS.values()}


@dataclass(frozen=True)
class Rule:
    lower: str
    upper: str
    strict: bool

    def holds(self, raw: dict[str, int]) -> bool:
        lo, hi = raw[self.lower], raw[self.upper]
        return lo < hi if self.strict else lo <= hi

    def describe(self, raw: dict[str, int] | None = None, fahrenheit: bool = False) -> str:
        lo, hi = SETTINGS[self.lower], SETTINGS[self.upper]
        rel = "below" if self.strict else "at or below"
        if raw is None:
            return f"{lo.label} must stay {rel} {hi.label}"
        return (
            f"{lo.label} ({lo.format(raw[self.lower], fahrenheit)}) must stay {rel} "
            f"{hi.label} ({hi.format(raw[self.upper], fahrenheit)})"
        )


ORDER: list[Rule] = [Rule(lo, hi, strict) for lo, hi, strict in settings_table.ORDER]


def _ranks() -> dict[str, int]:
    """Height of each setting in the ORDER graph (0 = nothing below it)."""
    below: dict[str, list[str]] = {k: [] for k in SETTINGS}
    for r in ORDER:
        below[r.upper].append(r.lower)
    memo: dict[str, int] = {}

    def rank(k: str) -> int:
        if k not in memo:
            memo[k] = 1 + max((rank(b) for b in below[k]), default=-1)
        return memo[k]

    return {k: rank(k) for k in SETTINGS}


RANK = _ranks()
