# BMS Drivers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move everything EG4-LL specific behind a `Driver` interface so a second BMS can be added as one package, with EG4-LL as the only shipped driver and no change to its behaviour.

**Architecture:** A `Driver` object per BMS family describes its setting table, register layout, identity/clock/live decoding, page layout, profiles, texts and simulator. A `Block` carries its driver, so the planner, service, backup, CLI and GUI get everything from the block or device in hand and never import EG4 names. A test-only toy driver proves the engine has no hidden EG4 assumptions.

**Tech Stack:** Python 3.11+, PySide6, pyserial, pytest. Venv: `.venv` (run tests with `.venv/bin/pytest -q`; lint with `uvx ruff check --select F,E9,B --ignore B905 src tests`).

**Spec:** `docs/superpowers/specs/2026-10-22-bms-drivers-design.md`

## Global Constraints

- Work on branch `bms-drivers`; `main` stays as the user tests it on the real pack.
- `settings_table.py` is moved with `git mv` and never edited: sha256 `de30c596b51dc56acefa0e36a00903b3a6a56220ae54a31d9517da3a19ed78bd`.
- EG4-LL behaviour is unchanged: same frames, same order, same refusals, same backups (plus the `bms` field). Wording may change only where named in a task.
- Existing tests keep every assertion. Only imports, fixtures and call sites change (for example `Pack(...)` becomes `EG4_LL.open_device(...)`).
- Generic modules (everything outside `src/eg4tool/drivers/`) never import `eg4tool.drivers.eg4_ll`. They reach drivers through the `eg4tool.drivers` registry only.
- No real-pack writes. Tests run against simulators only.
- Each task ends with the whole suite passing, ruff clean, and a commit. Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq
  ```

## File map

| File | After this plan |
|---|---|
| `src/eg4tool/settings.py` | `Setting`, `Rule`, `LinkedPair`, `SettingTable`, °F helpers (generic) |
| `src/eg4tool/block.py` | `Block` (bound to a driver), `Identity`, `BlankBlockError`, `DriverMismatch`, `ascii_text` |
| `src/eg4tool/live.py` | **new**: `Live`, `decode_bits` |
| `src/eg4tool/errors.py` | **new**: `DeviceRefused` |
| `src/eg4tool/device.py` | **renamed from `pack.py`**: `Device` protocol, `ModbusDevice`, `PackError` |
| `src/eg4tool/sim.py` | `FakeModbusDevice` (generic) |
| `src/eg4tool/layout.py` | `Section`, `Page`, `TEMP`, `WPR`, `COLUMN_HELP` only |
| `src/eg4tool/transport.py` | `SerialParams` added; `SerialTransport(port, timeout, params)` |
| `src/eg4tool/modbus.py` | `ModbusException` also a `DeviceRefused`; `registers_of` moves here |
| `src/eg4tool/planner.py`, `service.py`, `backup.py` | take the table and driver from the block / device |
| `src/eg4tool/drivers/__init__.py` | **new**: registry `DRIVERS`, `DEFAULT`, `get`, `UnknownDriver` |
| `src/eg4tool/drivers/base.py` | **new**: `Driver`, `Profile` |
| `src/eg4tool/drivers/eg4_ll/__init__.py` | **new**: exports `DRIVER` |
| `src/eg4tool/drivers/eg4_ll/settings_table.py` | **moved**, unchanged |
| `src/eg4tool/drivers/eg4_ll/table.py` | **new**: `TABLE` from the vendored table + overrides, linked pair, groups, labels |
| `src/eg4tool/drivers/eg4_ll/registers.py` | **new**: block range, writable ranges, clock packing, identity registers, texts |
| `src/eg4tool/drivers/eg4_ll/live.py` | **new**: EG4 live decode → `Live` |
| `src/eg4tool/drivers/eg4_ll/layout.py` | **new**: EG4 `PAGES` (moved from `layout.py`) |
| `src/eg4tool/drivers/eg4_ll/profiles.py` | **moved from `profiles.py`** |
| `src/eg4tool/drivers/eg4_ll/sim.py` | **new**: `FakePack(FakeModbusDevice)` with the shipped image and the clock |
| `src/eg4tool/drivers/eg4_ll/driver.py` | **new**: `Eg4LL(Driver)`, `DRIVER` |
| `tests/toy_driver.py` | **new**: test-only toy driver |
| `tests/test_drivers.py` | **new**: toy, cross-driver, backup v1/v2, import boundary, vendored hash |

---

### Task 0: Branch

- [ ] **Step 1:** `git switch -c bms-drivers` and confirm `.venv/bin/pytest -q` gives `62 passed`.

---

### Task 1: SettingTable and the EG4 table module

Moves the EG4 table build out of `settings.py` into `drivers/eg4_ll/table.py`. Legacy names stay reachable from `settings.py` through a module `__getattr__` until Task 6, so nothing else changes yet.

**Files:**
- Modify: `src/eg4tool/settings.py`
- Create: `src/eg4tool/drivers/__init__.py` (empty for now), `src/eg4tool/drivers/eg4_ll/__init__.py` (empty for now), `src/eg4tool/drivers/eg4_ll/table.py`
- Move: `src/eg4tool/settings_table.py` → `src/eg4tool/drivers/eg4_ll/settings_table.py`
- Test: `tests/test_drivers.py`

**Interfaces:**
- Produces:
  - `settings.LinkedPair(a: str, b: str, reason: str)`, iterable as `(a, b)`
  - `settings.SettingTable(settings: dict[str, Setting], order: list[Rule], linked: list[LinkedPair])` with `.settings`, `.by_register`, `.order`, `.linked`, `.rank`, `__getitem__`, `__contains__`, `__iter__`, `__len__`, `get(key)`, `items()`, `pair(key) -> LinkedPair | None`, `partner(key) -> str | None`, `describe_rule(rule, raw=None, fahrenheit=False) -> str`
  - `SettingTable.build(table, limits, order, linked=(), overrides=None, group=None, label=None) -> SettingTable`
  - `eg4tool.drivers.eg4_ll.table.TABLE`

- [ ] **Step 1: Write the failing tests** in a new `tests/test_drivers.py`:

```python
import hashlib
from pathlib import Path

import pytest

from eg4tool.settings import LinkedPair, Rule, SettingTable

VENDORED_SHA256 = "de30c596b51dc56acefa0e36a00903b3a6a56220ae54a31d9517da3a19ed78bd"
SRC = Path(__file__).parent.parent / "src" / "eg4tool"


def test_vendored_table_is_byte_for_byte_upstream():
    data = (SRC / "drivers" / "eg4_ll" / "settings_table.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == VENDORED_SHA256


def small_table() -> SettingTable:
    spec = {
        "low": (0x10, 0.01, 0, "V", None, 2),
        "high": (0x11, 0.01, 0, "V", None, 2),
        "a": (0x12, 1, 0, "A", None, 0),
        "b": (0x13, 1, 0, "A", None, 0),
    }
    limits = {"low": (2.0, 3.0, 0.01), "high": (3.0, 4.0, 0.01), "a": (1, 9, 1), "b": (1, 9, 1)}
    return SettingTable.build(spec, limits, [("low", "high", True)], linked=[LinkedPair("a", "b", "why")])


def test_setting_table_basics():
    t = small_table()
    assert list(t) == ["low", "high", "a", "b"] and len(t) == 4
    assert t["high"].register == 0x11 and t.by_register[0x12].key == "a"
    assert t.order == [Rule("low", "high", True)]
    assert t.rank == {"low": 0, "high": 1, "a": 0, "b": 0}
    assert t.partner("a") == "b" and t.partner("b") == "a" and t.partner("low") is None
    assert t.pair("b").reason == "why"
    a, b = t.linked[0]
    assert (a, b) == ("a", "b")
    assert t.describe_rule(t.order[0]) == "Low must stay below High"


def test_setting_table_rejects_unknown_keys():
    spec = {"low": (0x10, 0.01, 0, "V", None, 2)}
    with pytest.raises(ValueError, match="nope"):
        SettingTable.build(spec, {"low": (2.0, 3.0, 0.01)}, [("low", "nope", True)])


def test_eg4_table_matches_the_old_globals():
    from eg4tool.drivers.eg4_ll.table import TABLE
    assert len(TABLE) == 53
    assert TABLE["balance_delta_voltage"].minimum == TABLE["balance_delta_voltage"].multiplier * 20
    assert [tuple(p) for p in TABLE.linked] == [("short_circuit_current", "discharge_overcurrent_2_protection")]
    assert TABLE["cell_overvoltage_protection"].group == "Cell voltage"
    assert TABLE["mos_overtemp_warning"].label == "MOSFET OT warning"
```

(53 is `len(settings_table.SETTINGS)`. Check it with `python -c "from eg4tool.settings import SETTINGS; print(len(SETTINGS))"` before writing the test and use the printed number.)

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py -q`
Expected: FAIL with `ImportError: cannot import name 'LinkedPair'`.

- [ ] **Step 3: Move the vendored table**

```bash
mkdir -p src/eg4tool/drivers/eg4_ll
touch src/eg4tool/drivers/__init__.py src/eg4tool/drivers/eg4_ll/__init__.py
git mv src/eg4tool/settings_table.py src/eg4tool/drivers/eg4_ll/settings_table.py
```

- [ ] **Step 4: Rewrite `settings.py`**

Keep `SettingError`, `_dec`, `Setting`, `c_to_f`, `f_to_c_exact` exactly as they are. Delete `REG_*`, `is_threshold_register`, `LIMIT_OVERRIDES`, `LINKED_PAIRS`, `GROUPS`, `_WORDS`, `_group`, `_label`, `_build`, `SETTINGS`, `BY_REGISTER`, `ORDER`, `_ranks`, `RANK` and `Rule.describe`. Change the module docstring and imports, and add the following:

```python
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
```

After `f_to_c_exact`:

```python
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
```

- [ ] **Step 5: Create `drivers/eg4_ll/registers.py`** (only the register-range part now; Task 2 adds the rest):

```python
"""EG4-LL register layout (RS232 on the right-most paralleling port)."""

from __future__ import annotations

REG_BLOCK = 0x2D  # parameter block FC03 0x2D x91, 0x2D-0x87
BLOCK_COUNT = 0x5B
REG_BLOCK_LAST = REG_BLOCK + BLOCK_COUNT - 1
REG_CLOCK = 0x2D  # packed clock, 0x2D-0x2E, high word first


def is_writable(reg: int) -> bool:
    """Writable thresholds: not the clock (0x2D-0x2E), not identity (0x69-0x7F)."""
    return 0x2F <= reg <= 0x68 or 0x80 <= reg <= REG_BLOCK_LAST
```

- [ ] **Step 6: Create `drivers/eg4_ll/table.py`**. Its contents are the code removed from `settings.py`:

```python
"""EG4-LL settings: the vendored table plus this tool's tightening.

settings_table.py is vendored unchanged from esphome-eg4-bms
(components/eg4_bms/settings_table.py, branch feat/rs232-service cb105bf),
so the component and this tool share one source of truth.
"""

from __future__ import annotations

from ...settings import LinkedPair, SettingTable
from . import settings_table

# Tightened on top of the vendored LIMITS. The pack acks a balance delta below
# its floor and keeps the old value (0.010 V acked, 0.039 V kept; 0.020 V held).
LIMIT_OVERRIDES = {
    "balance_delta_voltage": (0.020, 0.1, 0.001),
}

# settings_table.py has 0x52 = short circuit and 0x54 = discharge OC 2;
# REGISTER_MAP.md says the reverse. Both ship as 300 A, so no read tells them
# apart. Until a deliberate test settles it, they are only set together, to
# the same value.
LINKED = [
    LinkedPair(
        "short_circuit_current",
        "discharge_overcurrent_2_protection",
        "which of 0x52 / 0x54 is which is not settled yet",
    )
]

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


def _group(key: str) -> str:
    for name, prefixes in GROUPS:
        if key.startswith(prefixes):
            return name
    return "Other"


def _label(key: str) -> str:
    words = [_WORDS.get(w, w) for w in key.split("_")]
    text = " ".join(words)
    return text[0].upper() + text[1:]


TABLE = SettingTable.build(
    settings_table.SETTINGS,
    settings_table.LIMITS,
    settings_table.ORDER,
    linked=LINKED,
    overrides=LIMIT_OVERRIDES,
    group=_group,
    label=_label,
)
```

- [ ] **Step 7: Point the remaining `Rule.describe` callers at the table**

In `planner.py`, `from .settings import ... Rule ...` stays. Add `TABLE` to that import. Then replace each `r.describe(current, fahrenheit)` with `TABLE.describe_rule(r, current, fahrenheit)`, and `rule.describe(final, fahrenheit)` with `TABLE.describe_rule(rule, final, fahrenheit)`. Also update `settings.py`'s own import of `settings_table`: remove `from . import settings_table`.

Run: `grep -rn "\.describe(" src/eg4tool | grep -i rule`
Expected: no hits.

- [ ] **Step 8: Run everything**

Run: `.venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests`
Expected: `66 passed`, ruff clean. If ruff flags `F401`/`F822` for the `__getattr__` names imported elsewhere, it doesn't: those are runtime lookups.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "Move the EG4-LL setting table behind a generic SettingTable"
```

---

### Task 2: Driver, registry, driver-bound Block, Live, Device and simulator

The core switch. After this task a `Block` knows its driver, `ModbusDevice` replaces `Pack`, `Live` replaces `Realtime`, and the EG4-LL specifics live in `drivers/eg4_ll/`. The planner, service and backup still use the Task 1 legacy names (Task 3 converts them). The CLI and GUI use `drivers.get(drivers.DEFAULT)` until Tasks 4 and 5 add the selector.

**Files:**
- Create: `src/eg4tool/errors.py`, `src/eg4tool/live.py`, `src/eg4tool/drivers/base.py`, `src/eg4tool/drivers/eg4_ll/{driver,live,layout,profiles,sim}.py`
- Modify: `src/eg4tool/drivers/__init__.py`, `src/eg4tool/drivers/eg4_ll/__init__.py`, `src/eg4tool/drivers/eg4_ll/registers.py`, `block.py`, `modbus.py`, `transport.py`, `sim.py`, `layout.py`, `service.py`, `cli.py`, `gui.py`, `settings_view.py`, `qtcommon.py`
- Rename: `src/eg4tool/pack.py` → `src/eg4tool/device.py`
- Delete: `src/eg4tool/profiles.py` (moved)
- Test: `tests/test_engine.py`, `tests/test_gui.py`, `tests/test_layout.py` (imports and fixtures only), `tests/test_drivers.py`

**Interfaces:**
- Consumes: `SettingTable`, `TABLE` (Task 1)
- Produces:
  - `errors.DeviceRefused(Exception)`; `modbus.ModbusException(ModbusError, DeviceRefused)`; `modbus.registers_of(frame) -> list[int]`
  - `transport.SerialParams(baud=115200, bytesize=8, parity="N", stopbits=1)`; `SerialTransport(port, timeout=REPLY_TIMEOUT_S, params=SerialParams())`
  - `block.Identity(model="", firmware="", serial="", clock=None, clock_raw=None)`
  - `block.Block(driver, registers: tuple[int, ...])`: `reg`, `regs`, `raw`, `raws`, `identity`, `model`, `firmware`, `serial`, `clock`, `clock_raw`, `thresholds_blank`, `check_not_blank`, `with_raws`, `unknown_registers`, `threshold_registers`, `same_driver(other, what)`
  - `block.DriverMismatch(Exception)`, `block.BlankBlockError(Exception)`, `block.ascii_text(regs)`
  - `live.Live` (fields below), `live.decode_bits(value, names)`
  - `drivers.base.Profile(name, label, short, block)`, `drivers.base.Driver` (attributes and methods below)
  - `drivers.DRIVERS: dict[str, Driver]`, `drivers.DEFAULT = "eg4-ll"`, `drivers.get(name) -> Driver`, `drivers.UnknownDriver(Exception)`
  - `device.ModbusDevice(driver, transport, log)`: `driver`, `transport`, `log`, `address`, `read(start, count, retries)`, `read_block()`, `read_live(retries)`, `write_setting(register, raw) -> bool`, `write_clock(t) -> bool`, `close()`; `device.PackError`; `device.READ_RETRIES`
  - `sim.FakeModbusDevice(image, is_writable, *, floors=None, blank_thresholds=False, state_file=None, state_from=0)`
  - `drivers.eg4_ll.DRIVER`, `drivers.eg4_ll.sim.FakePack(floors=None, blank_thresholds=False, state_file=None)`, `drivers.eg4_ll.registers.{pack_clock, unpack_clock}`, `drivers.eg4_ll.profiles.{EG4_SHIPPED_FRAME, REALTIME_FRAMES}`

- [ ] **Step 1: Write the failing driver tests** (append to `tests/test_drivers.py`):

```python
def test_registry_has_eg4_ll_by_default():
    from eg4tool import drivers
    assert drivers.DEFAULT == "eg4-ll"
    d = drivers.get("eg4-ll")
    assert d.label == "EG4-LL" and d.block_start == 0x2D and d.block_count == 91
    with pytest.raises(drivers.UnknownDriver, match="nope"):
        drivers.get("nope")


def test_eg4_profile_block_is_bound_to_its_driver():
    from eg4tool.drivers.eg4_ll import DRIVER
    ref = DRIVER.profiles["eg4-shipped"].block
    assert ref.driver is DRIVER
    assert ref.identity.model == "ZTR-16S200A-10KW-STD2"
    assert DRIVER.identity_warnings(ref) == []
    assert DRIVER.identity_warnings(ref.with_raws({0x69: 0x4142})) != []  # model text "AB..."


def test_block_length_comes_from_the_driver():
    from eg4tool.drivers.eg4_ll import DRIVER
    with pytest.raises(ValueError, match="91"):
        DRIVER.block([0] * 90)


def test_eg4_pages_cover_every_setting_once():
    from eg4tool.drivers.eg4_ll import DRIVER
    assert sorted(DRIVER.ordered_keys) == sorted(DRIVER.table)
    assert all(DRIVER.pages[DRIVER.page_of[k]] for k in DRIVER.table)
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py -q`
Expected: the 4 new tests FAIL (`ImportError: cannot import name 'DEFAULT'` and similar).

- [ ] **Step 3: `errors.py` and `modbus.py`**

`src/eg4tool/errors.py`:

```python
"""Errors shared by every kind of device."""


class DeviceRefused(Exception):
    """The device answered, but refused the request (for Modbus: an exception reply)."""
```

In `modbus.py`, change the docstring's first line to `"""Modbus RTU framing: FC03 reads and FC16 writes, checked by hand.`. Import `from .errors import DeviceRefused`, change `class ModbusException(ModbusError):` to `class ModbusException(ModbusError, DeviceRefused):`. Then add, moved from `profiles.py`:

```python
def registers_of(frame: bytes) -> list[int]:
    """The registers carried by a captured FC03 reply."""
    if not crc_ok(frame) or frame[1] != FC_READ:
        raise ValueError("not a valid FC03 reply")
    data = frame[3:-2]
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]
```

- [ ] **Step 4: `transport.py`**: replace `BAUD = 115200` with:

```python
@dataclass(frozen=True)
class SerialParams:
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1
```

Then change `SerialTransport.__init__`:

```python
    def __init__(self, port: str, timeout: float = REPLY_TIMEOUT_S, params: SerialParams = SerialParams()):
        ...
        self._serial = serial.Serial(
            port, params.baud, bytesize=params.bytesize, parity=params.parity, stopbits=params.stopbits,
            timeout=0.05, write_timeout=1.0, **extra,
        )
```

Also change "pack.py checks the rest" in its docstring to "device.py checks the rest".

- [ ] **Step 5: `live.py`** (new). The generic reading, built from today's `Realtime` without the decode:

```python
"""A live reading, the same shape for every BMS. Drivers decode into it."""

from __future__ import annotations

from dataclasses import dataclass, field


def decode_bits(value: int, names: dict[int, str]) -> list[str]:
    out = [name for bit, name in names.items() if value & bit]
    unknown = value & ~sum(names)
    if unknown:
        out.append(f"unknown 0x{unknown:04X}")
    return out


@dataclass
class Live:
    pack_voltage: float
    current: float
    soc: int
    soh: int
    cells: list[float]
    status_text: str
    temps: list[tuple[str, int]] = field(default_factory=list)  # (label, °C)
    max_temp: int | None = None
    warnings: list[str] = field(default_factory=list)
    protections: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    charge_allowed: bool | None = None
    discharge_allowed: bool | None = None
    balancing_cells: list[int] = field(default_factory=list)
    remaining_ah: float | None = None
    full_capacity_ah: float | None = None
    design_capacity_ah: float | None = None
    cycles: int | None = None
    extra: list[tuple[str, str]] = field(default_factory=list)
    registers: list[int] = field(default_factory=list)

    @property
    def cell_count(self) -> int:
        return len(self.cells)

    @property
    def cell_min(self) -> float:
        return min(self.cells, default=0.0)

    @property
    def cell_max(self) -> float:
        return max(self.cells, default=0.0)

    @property
    def cell_delta(self) -> float:
        return self.cell_max - self.cell_min

    def summary_lines(self) -> list[str]:
        lines = [
            f"Pack       {self.pack_voltage:6.2f} V   {self.current:+7.2f} A   SOC {self.soc}%   SOH {self.soh}%",
            f"Status     {self.status_text}",
        ]
        if self.full_capacity_ah is not None:
            design = f" (design {self.design_capacity_ah:.1f} Ah)" if self.design_capacity_ah is not None else ""
            cycles = f", {self.cycles} cycles" if self.cycles is not None else ""
            lines.append(
                f"Capacity   {self.remaining_ah} Ah left of {self.full_capacity_ah:.1f} Ah full{design}{cycles}"
            )
        lines.append(
            f"Cells      min {self.cell_min:.3f} V  max {self.cell_max:.3f} V  delta {self.cell_delta * 1000:.0f} mV"
        )
        for row in range(0, len(self.cells), 8):
            chunk = self.cells[row : row + 8]
            lines.append("  " + "  ".join(f"{row + i + 1:2d}:{v:.3f}" for i, v in enumerate(chunk)))
        if self.temps:
            lines.append("Temps      " + "  ".join(f"{label} {t} °C" for label, t in self.temps))
        lines.append(f"Warnings   {', '.join(self.warnings) or 'none'}")
        allowed = ""
        if self.charge_allowed is not None and self.discharge_allowed is not None:
            allowed = (
                f"  (charge {'allowed' if self.charge_allowed else 'BLOCKED'}, "
                f"discharge {'allowed' if self.discharge_allowed else 'BLOCKED'})"
            )
        lines.append(f"Protection {', '.join(self.protections) or 'none'}{allowed}")
        lines.append(f"Errors     {', '.join(self.errors) or 'none'}")
        if self.balancing_cells:
            lines.append(f"Balancing  cells {', '.join(map(str, self.balancing_cells))}")
        for label, text in self.extra:
            lines.append(f"{label:10s} {text}")
        return lines
```

Wording change (allowed): the "Temps" line becomes a labelled list (`PCB 23 °C  average 24 °C  max 24 °C  sensor 1 22 °C ...`).

- [ ] **Step 6: `block.py`**. Replace the whole module:

```python
"""A settings block read from a BMS, bound to the driver that knows its layout."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .drivers.base import Driver


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
                f"{what} is from a {other.driver.label} BMS and this one is a {self.driver.label}; "
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
```

- [ ] **Step 7: `drivers/base.py`** (new):

```python
"""What a BMS driver provides. Subclass Driver, set the class attributes and
override the methods marked as required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from .. import modbus
from ..block import Block, Identity
from ..layout import Page
from ..live import Live
from ..settings import SettingTable
from ..transport import SerialParams


@dataclass(frozen=True)
class Profile:
    """A built-in reference block, e.g. the values a pack ships with."""

    name: str
    label: str
    short: str
    block: Block


class Driver:
    name: str  # CLI --bms value and backup "bms" field, e.g. "eg4-ll"
    label: str  # shown to people, e.g. "EG4-LL"
    table: SettingTable
    block_start: int
    block_count: int
    serial: SerialParams = SerialParams()
    address: int = 1
    pages: tuple[Page, ...] = ()
    live_reads: tuple[tuple[int, int], ...] = ()  # (start, count) reads, concatenated for decode_live
    supports_clock: bool = False
    connect_note_html: str = ""  # banner above the connect bar
    connection_help: str = ""  # shown when the device does not answer
    no_reply_hint: str = ""  # one sentence for the status bar
    setup_hint: str = ""  # how to connect a pack, e.g. for the copy steps
    clock_note: str = ""

    # --- required -----------------------------------------------------------

    def is_writable(self, register: int) -> bool:
        raise NotImplementedError

    def decode_live(self, registers: list[int]) -> Live:
        raise NotImplementedError

    def simulator(self, state_file: Path | None = None):
        """A transport that behaves like a device of this kind."""
        raise NotImplementedError

    # --- optional -----------------------------------------------------------

    @cached_property
    def profiles(self) -> dict[str, Profile]:
        return {}

    def identity(self, block: Block) -> Identity:
        return Identity()

    def blank_reason(self, block: Block) -> str | None:
        """Why this read must not be used, or None."""
        if block.thresholds_blank:
            return f"the {self.label} answered with every writable register zero; refusing to use this read"
        return None

    def identity_warnings(self, block: Block) -> list[str]:
        """Soft checks that the device really is this kind of BMS."""
        return []

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        raise NotImplementedError(f"{self.label} has no clock")

    # --- provided -----------------------------------------------------------

    def block(self, registers) -> Block:
        return Block(self, tuple(registers))

    def write_frame(self, register: int, raw: int) -> bytes:
        return modbus.write_request(self.address, register, [raw])

    def clock_frame(self, t: datetime) -> bytes:
        register, values = self.clock_write(t)
        return modbus.write_request(self.address, register, values)

    def open_device(self, transport, log):
        from ..device import ModbusDevice

        return ModbusDevice(self, transport, log)

    @cached_property
    def ordered_keys(self) -> list[str]:
        return [k for page in self.pages for k in page.keys]

    @cached_property
    def page_of(self) -> dict[str, int]:
        return {k: i for i, page in enumerate(self.pages) for k in page.keys}

    def __repr__(self) -> str:
        return f"<{self.label} driver>"
```

- [ ] **Step 8: Split `layout.py`**

Move the `PAGES = (...)` tuple to `src/eg4tool/drivers/eg4_ll/layout.py`, unchanged. That file starts with:

```python
"""How the EG4-LL settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, WPR, Page, Section

PAGES = (
    ...  # the existing tuple, verbatim
)
```

(Import only the names the tuple uses; check with ruff.) In `src/eg4tool/layout.py`, delete `PAGES`, `PAGE_OF` and `ORDERED_KEYS`, and change the docstring to:

```python
"""How settings are arranged on screen: the shapes drivers fill in. No Qt
here, so they can be tested.

Threshold triples are rows of a Warning / Protection / Release matrix;
over-current settings are stage rows of trip current and delay.
"""
```

- [ ] **Step 9: Finish `drivers/eg4_ll/registers.py`**. Append the clock packing and identity registers (moved from `block.py`) and the texts (moved from `pack.py`, `gui.py`, `settings_view.py`):

```python
REG_MODEL, MODEL_REGS = 0x69, 11
REG_FIRMWARE, FIRMWARE_REGS = 0x75, 3
REG_SERIAL, SERIAL_REGS = 0x78, 8
ADDRESS = 1  # over RS232 the pack answers at address 1 whatever the DIP ID


def pack_clock(t: datetime) -> int:
    ...  # verbatim from block.py


def unpack_clock(packed: int) -> datetime | None:
    ...  # verbatim from block.py


BLANK_MESSAGE = (
    "the pack answered with every threshold zero. That is what the RS485 "
    "paralleling bus returns: the parameter block only comes over RS232, with the "
    "adapter in the pack's right-most paralleling port. Refusing to use this read."
)

CONNECTION_HELP = """..."""  # verbatim from pack.py

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables from the pack's paralleling ports, then plug the "
    "USB-RS232 adapter into its <b>right-most</b> paralleling port. The DIP ID can stay as it is."
)
NO_REPLY_HINT = "Check the link cables are unplugged and the adapter is in the right-most paralleling port."
SETUP_HINT = "link cables unplugged, adapter in its right-most paralleling port"
CLOCK_NOTE = (
    "The BMS clock is local time packed as year-2000, month, day, hour, minute, second "
    "(registers 0x2D-0x2E). Setting it is read back to confirm."
)
```

Add `from datetime import datetime` at the top.

- [ ] **Step 10: `drivers/eg4_ll/live.py`**. Move the bit tables, `_s16`, `_s8` and the decode from `block.py`, returning `Live`:

```python
"""EG4-LL live registers 0x00-0x26 (EG4-LL Modbus protocol V01.06)."""

from __future__ import annotations

from ...live import Live, decode_bits

STATUS_BITS = ...  # verbatim
ALARM_BITS = ...  # verbatim
WARNING_BITS = ...  # verbatim
PROTECTION_BITS = ...  # verbatim
ERROR_BITS = ...  # verbatim
CHARGE_BLOCKERS = ...  # verbatim
DISCHARGE_BLOCKERS = ...  # verbatim


def _s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def _s8(v: int) -> int:
    return v - 0x100 if v & 0x80 else v


def status_text(status: int) -> str:
    parts = decode_bits(status & 0xFF, STATUS_BITS) or ["Standby"]
    if status & 0x8000:
        parts.insert(0, "Heating")
    return ", ".join(parts)


def decode(r: list[int]) -> Live:
    if len(r) != 0x27:
        raise ValueError(f"realtime image is 39 registers, got {len(r)}")
    count = r[0x24] if 0 < r[0x24] <= 16 else 16
    pcb, avg, top = _s16(r[0x12]), _s16(r[0x13]), _s16(r[0x14])
    sensors = [t for v in r[0x21:0x24] for t in (_s8(v >> 8), _s8(v & 0xFF))]
    protection = r[0x1B]
    return Live(
        pack_voltage=r[0x00] / 100,
        current=_s16(r[0x01]) / 100,
        soc=r[0x18],
        soh=r[0x17],
        cells=[r[0x02 + i] / 1000 for i in range(count)],
        status_text=status_text(r[0x19]),
        temps=[("PCB", pcb), ("average", avg), ("max", top)]
        + [(f"sensor {i + 1}", t) for i, t in enumerate(sensors)],
        max_temp=top,
        warnings=decode_bits(r[0x1A], WARNING_BITS),
        protections=decode_bits(protection, PROTECTION_BITS),
        errors=decode_bits(r[0x1C], ERROR_BITS),
        charge_allowed=not protection & CHARGE_BLOCKERS,
        discharge_allowed=not protection & DISCHARGE_BLOCKERS,
        balancing_cells=[i + 1 for i in range(16) if r[0x26] >> i & 1],
        remaining_ah=r[0x15],
        full_capacity_ah=(r[0x1F] << 16 | r[0x20]) / 3_600_000,
        design_capacity_ah=r[0x25] / 10,
        cycles=r[0x1D] << 16 | r[0x1E],
        extra=[("Max charge", f"{r[0x16]} A")],
        registers=list(r),
    )
```

- [ ] **Step 11: `drivers/eg4_ll/profiles.py`**: `git mv src/eg4tool/profiles.py src/eg4tool/drivers/eg4_ll/profiles.py`, then edit it. Keep the docstring and the two frame constants. Delete `registers_of`, `Profile`, `EG4_SHIPPED` and `PROFILES`, and add:

```python
from ... import modbus
from ..base import Profile


def build(driver) -> dict[str, Profile]:
    shipped = Profile(
        name="eg4-shipped",
        label="EG4 shipped settings (ZTR-16S200A-10KW-STD2, BMS fw S02T11, captured 2026-10-20)",
        short="EG4 shipped values",
        block=driver.block(modbus.registers_of(EG4_SHIPPED_FRAME)),
    )
    return {shipped.name: shipped}
```

- [ ] **Step 12: Generic `sim.py`**. Replace it with a parameterised Modbus fake; its body is today's `FakePack` without the EG4 image and clock:

```python
"""An in-process fake Modbus device for tests and for trying the tool without hardware.

It serves a register image over the same transact() interface as the
serial transport and applies FC16 writes. Like a real pack it can ack a
write and silently not store it (`floors`: register -> lowest raw value it
keeps), and it can answer a read with every writable register zero.
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
    ):
        self.image = list(image)
        self.is_writable = is_writable
        self.floors = floors or {}
        self.blank_thresholds = blank_thresholds
        self.state_file = state_file
        self.state_from = state_from
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
        start, count = struct.unpack(">HH", request[2:6])
        size = len(self.image)
        if function == modbus.FC_READ:
            if start + count > size:
                return self._exception(address, function, 2)
            regs = list(self.image[start : start + count])
            self.on_read(start, count, regs)
            if self.blank_thresholds:
                regs = [0 if self.is_writable(start + i) else v for i, v in enumerate(regs)]
            body = struct.pack(">BBB", address, function, 2 * count) + b"".join(struct.pack(">H", v) for v in regs)
            return modbus.with_crc(body)
        if function == modbus.FC_WRITE:
            values = [struct.unpack(">H", request[7 + 2 * i : 9 + 2 * i])[0] for i in range(count)]
            if start + count > size:
                return self._exception(address, function, 2)
            if start in self.refuse:
                return self._exception(address, function, self.refuse[start])
            self.writes.append((start, values))
            for i, v in enumerate(values):
                reg = start + i
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
            regs = {f"0x{r:02X}": self.image[r] for r in range(self.state_from, len(self.image))}
            self.state_file.write_text(json.dumps({"registers": regs}, indent=1))

    @staticmethod
    def _exception(address: int, function: int, code: int) -> bytes:
        return modbus.with_crc(bytes([address, function | modbus.EXCEPTION_FLAG, code]))

    def close(self) -> None:
        pass
```

- [ ] **Step 13: `drivers/eg4_ll/sim.py`** (new):

```python
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
```

- [ ] **Step 14: `drivers/eg4_ll/driver.py`** (new) and the package `__init__`:

```python
"""The EG4-LL driver: lithium rack packs with the ZTR BMS, RS232 on the
right-most paralleling port, Modbus RTU at 115200 8N1, address 1."""

from __future__ import annotations

from datetime import datetime
from functools import cached_property
from pathlib import Path

from ...block import Block, Identity, ascii_text
from ...transport import SerialParams
from ..base import Driver, Profile
from . import live, registers
from .layout import PAGES
from .table import TABLE


class Eg4LL(Driver):
    name = "eg4-ll"
    label = "EG4-LL"
    table = TABLE
    block_start = registers.REG_BLOCK
    block_count = registers.BLOCK_COUNT
    serial = SerialParams(baud=115200)
    address = registers.ADDRESS
    pages = PAGES
    live_reads = ((0x00, 0x12), (0x12, 0x15))
    supports_clock = True
    connect_note_html = registers.CONNECT_NOTE_HTML
    connection_help = registers.CONNECTION_HELP
    no_reply_hint = registers.NO_REPLY_HINT
    setup_hint = registers.SETUP_HINT
    clock_note = registers.CLOCK_NOTE

    def is_writable(self, register: int) -> bool:
        return registers.is_writable(register)

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    def identity(self, block: Block) -> Identity:
        packed = block.reg(registers.REG_CLOCK) << 16 | block.reg(registers.REG_CLOCK + 1)
        return Identity(
            model=ascii_text(block.regs(registers.REG_MODEL, registers.MODEL_REGS)),
            firmware=ascii_text(block.regs(registers.REG_FIRMWARE, registers.FIRMWARE_REGS)),
            serial=ascii_text(block.regs(registers.REG_SERIAL, registers.SERIAL_REGS)),
            clock=registers.unpack_clock(packed),
            clock_raw=packed,
        )

    def blank_reason(self, block: Block) -> str | None:
        return registers.BLANK_MESSAGE if block.thresholds_blank else None

    def identity_warnings(self, block: Block) -> list[str]:
        # Every EG4-LL seen so far reports a ZTR- model. Warn only: other variants may differ.
        model = block.model
        if model.startswith("ZTR-"):
            return []
        return [f"the model text '{model}' is not the ZTR-... seen on EG4-LL packs; is the right BMS type selected?"]

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        packed = registers.pack_clock(t)
        return registers.REG_CLOCK, [packed >> 16, packed & 0xFFFF]

    @cached_property
    def profiles(self) -> dict[str, Profile]:
        from .profiles import build

        return build(self)

    def simulator(self, state_file: Path | None = None):
        from . import sim  # looked up per call, so tests can patch sim.FakePack

        return sim.FakePack(state_file=state_file)


DRIVER = Eg4LL()
```

`drivers/eg4_ll/__init__.py`:

```python
from .driver import DRIVER

__all__ = ["DRIVER"]
```

`drivers/__init__.py`:

```python
"""The BMS drivers this tool knows, by name. Generic code reaches drivers only through here."""

from __future__ import annotations

from .base import Driver, Profile
from .eg4_ll import DRIVER as _EG4_LL

DEFAULT = "eg4-ll"
DRIVERS: dict[str, Driver] = {_EG4_LL.name: _EG4_LL}

__all__ = ["DEFAULT", "DRIVERS", "Driver", "Profile", "UnknownDriver", "get"]


class UnknownDriver(Exception):
    pass


def get(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError:
        raise UnknownDriver(f"unknown BMS type '{name}' (known: {', '.join(sorted(DRIVERS))})") from None
```

- [ ] **Step 15: `pack.py` → `device.py`**: `git mv src/eg4tool/pack.py src/eg4tool/device.py`, then:
  - Docstring: `"""Talking to one device: reads, single-register writes, every frame logged."""`
  - Delete `ADDRESS` and `CONNECTION_HELP` (moved to registers), and the imports of `Block`, `Realtime`, `pack_clock` and the `settings` names.
  - Add a `Device` protocol and turn `Pack` into `ModbusDevice`:

```python
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from . import modbus
from .block import Block
from .live import Live
from .session import SessionLog
from .transport import NoReply

if TYPE_CHECKING:
    from .drivers.base import Driver

READ_RETRIES = 3


class Transport(Protocol):
    def transact(self, request: bytes) -> bytes: ...
    def close(self) -> None: ...


class PackError(Exception):
    pass


class Device(Protocol):
    driver: "Driver"
    log: SessionLog

    def read_block(self) -> Block: ...
    def read_live(self, retries: int = READ_RETRIES) -> Live: ...
    def write_setting(self, register: int, raw: int) -> bool: ...
    def write_clock(self, t: datetime) -> bool: ...
    def close(self) -> None: ...


class ModbusDevice:
    def __init__(self, driver: "Driver", transport: Transport, log: SessionLog):
        self.driver = driver
        self.transport = transport
        self.log = log
        self.address = driver.address

    # _transact and read: unchanged from Pack

    def read_block(self) -> Block:
        """The settings block; refuses a read the driver calls blank."""
        d = self.driver
        block = d.block(self.read(d.block_start, d.block_count))
        reason = d.blank_reason(block)
        if reason:
            self.log.error(f"refusing this read: {reason}")
        return block.check_not_blank()

    def read_live(self, retries: int = READ_RETRIES) -> Live:
        regs: list[int] = []
        for start, count in self.driver.live_reads:
            regs += self.read(start, count, retries)
        return self.driver.decode_live(regs)

    def write_setting(self, register: int, raw: int) -> bool:
        """One FC16 single-register write. True if acked, False if no ack.

        The ack proves nothing: callers must re-read the block to confirm.
        """
        if not self.driver.is_writable(register):
            raise PackError(f"refusing to write 0x{register:02X}: not a writable settings register")
        return self._write(register, [raw])

    def write_clock(self, t: datetime) -> bool:
        if not self.driver.supports_clock:
            raise PackError(f"the {self.driver.label} has no clock to set")
        register, values = self.driver.clock_write(t)
        return self._write(register, values)

    # _write and close: unchanged from Pack
```

- [ ] **Step 16: Update `service.py` for the device**: import `from .device import Device` and `from .errors import DeviceRefused` (instead of `Pack` and `ModbusException`). Type hints `pack: Pack` become `pack: Device`. `pack.write_register(` becomes `pack.write_setting(`, and `except ModbusException as e:` becomes `except DeviceRefused as e:`. In `set_clock`, replace the log line with `pack.log.info(f"setting the BMS clock to {t}")` and drop the `REG_CLOCK` import.

- [ ] **Step 17: Minimal CLI / GUI / qtcommon / settings_view updates** (the selector comes in Tasks 4 and 5):
  - `cli.py`:
    - Imports: replace `from .pack import CONNECTION_HELP, Pack, PackError` with `from .device import ModbusDevice, PackError`, and `from .profiles import PROFILES` with `from . import drivers`. Add the module constant `DRIVER = drivers.get(drivers.DEFAULT)  # Task 4 replaces this with --bms`.
    - `PROFILES` becomes `DRIVER.profiles` (three places), and `CONNECTION_HELP` becomes `DRIVER.connection_help`.
    - `open_pack`: `transport = DRIVER.simulator(state_file=args.sim_state)`, `transport = SerialTransport(port, params=DRIVER.serial)`, `return DRIVER.open_device(transport, log)`, and the return type is `ModbusDevice`.
    - `pack.read_realtime()` becomes `pack.read_live()` (twice).
    - In `cmd_clock`, delete the two local imports and use `frame = DRIVER.clock_frame(target)`.
    - `print_identity` stays.
  - `qtcommon.py`: `from .pack import CONNECTION_HELP` becomes `from . import drivers`, and `CONNECTION_HELP` becomes `drivers.get(drivers.DEFAULT).connection_help`. Task 5 passes the driver.
  - `gui.py`:
    - `from .block import Block, Realtime` becomes `from .block import Block` plus `from .live import Live`. `Realtime` becomes `Live` in type hints, and `from .pack import Pack` becomes `from .device import ModbusDevice`, so `self.pack: ModbusDevice | None`.
    - Add the module constant `DRIVER = drivers.get(drivers.DEFAULT)  # Task 5 replaces this with the BMS selector`, after `from . import drivers`.
    - `toggle_connect`: `transport = DRIVER.simulator()` and `SerialTransport(device, params=DRIVER.serial)`. `self.pack = DRIVER.open_device(transport, self.log)`.
    - `poll_live` uses `pack.read_live(retries=1)`.
    - `LiveTab.show_realtime`: `self.temp.set(t(rt.max_temp) if rt.max_temp is not None else "—")`. The temperatures label becomes `f"<b>Temperatures:</b> {', '.join(f'{label} {t(v)}' for label, v in rt.temps)}"`. For the capacity label, keep the current text when `rt.full_capacity_ah is not None`, otherwise set it to `""`. For the status line, only add `· charge …, discharge …` when `rt.charge_allowed is not None`.
    - `ClockTab.set_clock`: delete the two local imports and use `frame = self.main.pack.driver.clock_frame(now).hex(" ").upper()`.
  - `settings_view.py`:
    - `from .layout import COLUMN_HELP, ORDERED_KEYS, PAGE_OF, PAGES, TEMP, Page` becomes `from .layout import COLUMN_HELP, TEMP, Page`, and `from .profiles import PROFILES` becomes `from . import drivers`.
    - Add `DRIVER = drivers.get(drivers.DEFAULT)  # Task 5 replaces this` and `PAGES, ORDERED_KEYS, PAGE_OF = DRIVER.pages, DRIVER.ordered_keys, DRIVER.page_of`.
    - `PROFILES.values()` becomes `DRIVER.profiles.values()`.
  - `planner.py`, `backup.py`: no change (the Task 1 legacy names still resolve).

- [ ] **Step 18: Update the tests' imports and fixtures** (no assertion changes):
  - `tests/test_engine.py` imports:

```python
from eg4tool import backup, modbus
from eg4tool.block import BlankBlockError
from eg4tool.device import ModbusDevice
from eg4tool.drivers.eg4_ll import DRIVER as EG4_LL
from eg4tool.drivers.eg4_ll.live import decode as decode_live
from eg4tool.drivers.eg4_ll.profiles import EG4_SHIPPED_FRAME, REALTIME_FRAMES
from eg4tool.drivers.eg4_ll.registers import pack_clock, unpack_clock
from eg4tool.drivers.eg4_ll.sim import FakePack
from eg4tool.modbus import registers_of
from eg4tool.planner import make_plan, targets_from_block
from eg4tool.service import CONFIRMED, NOT_APPLIED, NOT_ATTEMPTED, StaleError, apply_plan, diff_blocks, set_clock
from eg4tool.session import SessionLog
from eg4tool.settings import ORDER, SETTINGS, Rule, SettingError, f_to_c_exact
```

  - `REF = EG4_SHIPPED.block` becomes `REF = EG4_LL.profiles["eg4-shipped"].block`.
  - `fake_pack`: `return sim, EG4_LL.open_device(sim, SessionLog())`.
  - `Realtime.decode(` becomes `decode_live(`.
  - `Block(tuple(...))` becomes `EG4_LL.block(...)`: two places, the blank tests at lines ~126 and ~396.
  - `Pack(FakePack(), log)`, `Pack(transport.SerialTransport("FAKE"), log)` and `Pack(transport.SerialTransport("FAKE"), SessionLog())` become `EG4_LL.open_device(...)`.
  - In `test_pack_refuses_non_threshold_writes`, `from eg4tool.pack import PackError` becomes `from eg4tool.device import PackError`, and `pack.write_register(reg, 1)` becomes `pack.write_setting(reg, 1)`.
  - `tests/test_gui.py`: every `from eg4tool import sim` becomes `from eg4tool.drivers.eg4_ll import sim`. In `connect_sim`, the patch becomes `sim.FakePack = lambda **kw: orig(**sim_kw, **kw)`. In `test_restore_over_vendor_reset` and `test_silent_port_pauses_live_and_keeps_disconnect`, the replacement functions take `**kw` and call `orig(**kw)`.
  - `tests/test_layout.py`: replace its imports with

```python
from eg4tool.drivers.eg4_ll import DRIVER

ORDERED_KEYS, PAGES, SETTINGS = DRIVER.ordered_keys, DRIVER.pages, DRIVER.table
```

- [ ] **Step 19: Run everything**

Run: `.venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests`
Expected: `70 passed`, ruff clean. Also confirm the CLI still runs end to end: `.venv/bin/eg4tool info --simulate --log-dir "$TMPDIR"` prints the model, firmware, clock and the live summary.

- [ ] **Step 20: Commit**

```bash
git add -A src tests
git commit -m "Add BMS drivers: driver-bound Block, ModbusDevice, Live, EG4-LL driver package"
```

---

### Task 3: Planner, service and backup from the block's driver; cross-driver refusals; backup v2

**Files:**
- Modify: `src/eg4tool/planner.py`, `src/eg4tool/service.py`, `src/eg4tool/backup.py`
- Test: `tests/test_drivers.py`, `tests/test_engine.py` (two call sites)

**Interfaces:**
- Consumes: `Block.driver`, `Block.same_driver`, `DriverMismatch`, `SettingTable`, `drivers.get`, `UnknownDriver`
- Produces:
  - `make_plan(base, targets, rules=None, linked=None, fahrenheit=False, source=None) -> Plan`
  - `planner._order(table, current, wanted, rules)`
  - `Plan.frames() -> list[bytes]` (address from the driver; `Change.frame` removed)
  - `identity_warnings(pack, source, source_name)` (raises `DriverMismatch`)
  - `apply_plan(pack: Device, plan, progress)` (raises `DriverMismatch` if `pack.driver is not plan.base.driver`)
  - `diff_blocks(a, b) -> tuple[list[DiffRow], list[int]]` with `DiffRow(key, a, b, setting)`
  - `register_diff(a, b)`, `describe_register(table, reg, raw)`
  - `backup.FORMAT_VERSION = 2`, a `"bms"` field, `Backup.block.driver`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_drivers.py`):

```python
import json

from eg4tool import backup
from eg4tool.drivers.eg4_ll import DRIVER as EG4_LL
from eg4tool.planner import make_plan

REF = EG4_LL.profiles["eg4-shipped"].block


def test_backup_records_the_bms_type(tmp_path):
    f = tmp_path / "b.json"
    backup.save(f, REF)
    data = json.loads(f.read_text())
    assert data["format_version"] == 2 and data["bms"] == "eg4-ll"
    assert backup.load(f).block.driver is EG4_LL


def test_version_1_backup_loads_as_eg4_ll():
    data = backup.to_dict(REF)
    data["format_version"] = 1
    del data["bms"]
    assert backup.from_dict(data).block == REF


def test_backup_naming_an_unknown_bms_is_refused():
    data = backup.to_dict(REF)
    data["bms"] = "mystery"
    with pytest.raises(backup.BackupError, match="mystery"):
        backup.from_dict(data)


def test_plan_takes_rules_from_the_block_driver():
    p = make_plan(REF, {"cell_overvoltage_warning": 3900})
    assert not p.ok and any("Cell OV warning" in e for e in p.errors)
    p = make_plan(REF, {"short_circuit_current": 25000})
    assert any("which of 0x52 / 0x54 is which is not settled yet" in e for e in p.errors)


def test_dry_run_frames_use_the_driver():
    p = make_plan(REF, {"cell_overvoltage_warning": 3650})
    assert p.frames() == [EG4_LL.write_frame(0x43, 3650)]
```

Cross-driver refusals are tested in Task 6 with the toy driver.

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py -q`
Expected: `test_backup_records_the_bms_type` fails (`format_version` 1, no `bms`); `test_version_1...` fails with `KeyError: 'bms'`; the frames test fails because `EG4_LL.write_frame` exists but `Plan.frames` takes an address (the result is the same, so this test may pass already; that is fine).

- [ ] **Step 3: `planner.py`**
  - Imports: `from .block import Block`, plus `from .settings import LinkedPair, Rule, Setting, SettingTable`. Remove `modbus`, `ADDRESS`, `TABLE`, and the legacy names.
  - `Change.frame` is deleted.
  - `Plan.frames`:

```python
    def frames(self) -> list[bytes]:
        d = self.base.driver
        return [d.write_frame(c.register, c.new) for c in self.changes]
```

  - `make_plan`:

```python
def make_plan(
    base: Block,
    targets: dict[str, int],
    rules: list[Rule] | None = None,
    linked: list[LinkedPair] | None = None,
    fahrenheit: bool = False,
    source: Block | None = None,
) -> Plan:
    """Plan writes that take `base` to `targets` ({setting key: raw})."""
    if source is not None:
        base.same_driver(source, "the source")
    table = base.driver.table
    rules = table.order if rules is None else rules
    linked = table.linked if linked is None else linked
    plan = Plan(base=base)
    base.check_not_blank()
    current = base.raws()
    ...
```

  - In the body, `SETTINGS` becomes `table` (`table.get(key)`, `table[k]`, `k in table`). `is_threshold_register(s.register)` becomes `base.driver.is_writable(s.register)`. `TABLE.describe_rule` becomes `table.describe_rule`.
  - The linked loop becomes `for pair in linked:` with `a, b = pair`. Its error ends with `f"must be set to the same value: {pair.reason}"`, and its note is `f"{table[a].label} and {table[b].label} are written together ({pair.reason})"`.
  - `_order(table, current, wanted, rules)`: `RANK[k]` becomes `table.rank[k]`, and `SETTINGS[k].register` becomes `table[k].register`. The call site becomes `_order(table, current, wanted, rules)`.
  - `targets_from_block`: `for k in SETTINGS` becomes `for k in source.driver.table`.
  - `identity_warnings` starts with `pack.same_driver(source, source_name)`.

Wording change (allowed): the linked-pair error and note now quote the pair's reason ("which of 0x52 / 0x54 is which is not settled yet"); `test_linked_pair_must_match` still matches `"same value"`.

- [ ] **Step 4: `service.py`**
  - Remove `from .settings import ...` entirely, and add `from .block import Block, DriverMismatch` and `from .settings import Setting, SettingTable`.
  - `register_diff`:

```python
def register_diff(a: Block, b: Block) -> list[int]:
    """Writable registers (known or not) that differ between two blocks."""
    a.same_driver(b)
    ra, rb = a.threshold_registers(), b.threshold_registers()
    return [r for r in ra if ra[r] != rb[r]]


def describe_register(table: SettingTable, reg: int, raw: int) -> str:
    s = table.by_register.get(reg)
    return f"{s.label} = {s.format(raw)}" if s else f"unknown register 0x{reg:02X} = {raw}"
```

  - `apply_plan`: after the `plan.ok` check, add

```python
    if pack.driver is not plan.base.driver:
        raise DriverMismatch(
            f"this plan is for a {plan.base.driver.label} BMS and the connected device is a {pack.driver.label}"
        )
    table = plan.base.driver.table
```

    and pass `table` to both `describe_register(` calls.
  - `_log_summary(log, out, table)`: loop `for a, b in table.linked`, with `SETTINGS` becoming `table`. Pass `table` at the three call sites.
  - `set_clock`: raise `PackError(f"the {pack.driver.label} has no clock to set")` when `not pack.driver.supports_clock` (import `PackError` from `.device`).
  - `DiffRow`:

```python
@dataclass
class DiffRow:
    key: str
    a: int
    b: int
    setting: Setting


def diff_blocks(a: Block, b: Block) -> tuple[list[DiffRow], list[int]]:
    """Known settings that differ, and unknown writable registers that differ."""
    a.same_driver(b)
    table = a.driver.table
    rows = [DiffRow(k, a.raw(k), b.raw(k), s) for k, s in table.items() if a.raw(k) != b.raw(k)]
    unknown = [r for r in register_diff(a, b) if r not in table.by_register]
    return rows, unknown
```

- [ ] **Step 5: `backup.py`**
  - Imports: `from . import __version__, drivers`, `from .block import Block, BlankBlockError`. The `settings` import goes.
  - `FORMAT_VERSION = 2`, plus `READABLE_VERSIONS = (1, 2)  # 1: before drivers, always EG4-LL`.
  - `to_dict` uses `d = block.driver`. Insert `"bms": d.name,` after `"format_version"`. `REG_BLOCK`/`BLOCK_COUNT` become `d.block_start`/`d.block_count`, and `SETTINGS.items()` becomes `d.table.items()`. The `serial_note` stays.
  - `from_dict`:

```python
def from_dict(data: dict, where: str = "backup") -> Backup:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError(f"{where} is not an eg4tool backup")
    version = data.get("format_version")
    if version not in READABLE_VERSIONS:
        raise BackupError(f"{where}: unsupported format version {version}")
    name = data.get("bms") if version >= 2 else drivers.DEFAULT
    try:
        driver = drivers.get(name)
    except drivers.UnknownDriver as e:
        raise BackupError(f"{where}: {e}") from None
    if data.get("start_register") != driver.block_start or data.get("register_count") != driver.block_count:
        raise BackupError(
            f"{where}: not a {driver.label} settings block (0x{driver.block_start:02X} x{driver.block_count})"
        )
    regs = data.get("registers")
    ...  # unchanged list check
    try:
        block = driver.block(regs)
        block.check_not_blank()
    except (ValueError, BlankBlockError) as e:
        raise BackupError(f"{where}: {e}") from None
    for key, entry in (data.get("settings") or {}).items():
        s = driver.table.get(key)
        ...  # unchanged
```

- [ ] **Step 6: Update the two `_order` call sites in `tests/test_engine.py`**: the monkeypatched lambda becomes `lambda _table, cur, want, _rules: ([], {...})`, and the direct call becomes `planner._order(REF.driver.table, REF.raws(), {...}, rules)`.

- [ ] **Step 7: Run everything**

Run: `.venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests`
Expected: `75 passed`, ruff clean. `grep -n "SETTINGS\|LINKED_PAIRS\|ORDER\b\|RANK\|is_threshold_register\|REG_BLOCK\|BLOCK_COUNT" src/eg4tool/{planner,service,backup}.py` prints nothing.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "Planner, service and backups take everything from the block's driver; backup format 2 records the BMS"
```

---

### Task 4: CLI `--bms`

**Files:**
- Modify: `src/eg4tool/cli.py`
- Test: `tests/test_drivers.py`

**Interfaces:**
- Consumes: `drivers.DRIVERS`, `drivers.get`, `Driver.*`
- Produces: the `--bms NAME` common option (default `eg4-ll`), also on `keys`; `open_pack(args, near) -> ModbusDevice`; `driver_of(args) -> Driver`

- [ ] **Step 1: Write the failing tests**:

```python
def test_cli_bms_option(tmp_path, capsys):
    from eg4tool.cli import main
    assert main(["keys", "--bms", "eg4-ll"]) == 0
    assert "cell_overvoltage_protection" in capsys.readouterr().out
    assert main(["info", "--simulate", "--bms", "eg4-ll", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ZTR-16S200A" in out and "SOC 67%" in out


def test_cli_rejects_unknown_bms(capsys):
    from eg4tool.cli import main
    with pytest.raises(SystemExit):
        main(["keys", "--bms", "nope"])
    assert "nope" in capsys.readouterr().err


def test_cli_profile_names_come_from_the_driver(tmp_path, capsys):
    from eg4tool.cli import main
    assert main(["diff", "--profile", "nope", "--simulate", "--log-dir", str(tmp_path)]) == 2
    assert "eg4-shipped" in capsys.readouterr().err
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py -q -k cli`
Expected: FAIL (`unrecognized arguments: --bms`).

- [ ] **Step 3: Implement in `cli.py`**
  - Delete the module constants `DRIVER` and `ONE_PACK_NOTE`, and add:

```python
import re

from .block import BlankBlockError, Block, DriverMismatch


def driver_of(args):
    return drivers.get(getattr(args, "bms", None) or drivers.DEFAULT)


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)
```

  - `main()`:
    - The `NoReply` handler prints `driver_of(args).connection_help`.
    - Add `DriverMismatch` and `drivers.UnknownDriver` to the exit-2 tuple.
  - `build_parser()`:
    - Description: `"Back up, compare and restore BMS protection settings, one pack at a time. Supported: " + ", ".join(d.label for d in drivers.DRIVERS.values()) + "."`.
    - Common options: `common.add_argument("--bms", choices=sorted(drivers.DRIVERS), default=drivers.DEFAULT, help="BMS type (default: %(default)s)")`. Add the same `--bms` argument to the `keys` subparser.
    - Both `--profile` arguments lose `choices` and get `help="a built-in profile (see 'eg4tool keys --profiles')"`.
    - `keys` also gets `s.add_argument("--profiles", action="store_true", help="list the built-in profiles instead")`.
  - `open_pack`: `driver = driver_of(args)`; `log.info(f"BMS type {driver.label}")`. The simulator branch uses `driver.simulator(state_file=args.sim_state)` and the serial branch uses `SerialTransport(port, params=driver.serial)`; `return driver.open_device(transport, log)`.
  - `print_identity(block)`:

```python
def print_identity(block: Block) -> None:
    ident = block.identity
    print(f"BMS       {block.driver.label}")
    print(f"Model     {ident.model}")
    print(f"Firmware  {ident.firmware}")
    if ident.serial:
        print(f"Serial    {ident.serial}  (the same on every pack; does not identify it)")
    if block.driver.supports_clock:
        bad = f"invalid (0x{ident.clock_raw:08X})" if ident.clock_raw is not None else "invalid"
        print(f"BMS clock {ident.clock or bad}")
    for w in block.driver.identity_warnings(block):
        print(f"warning: {w}")
```

  - `cmd_backup`: `len(SETTINGS)` becomes `len(block.driver.table)`.
  - `load_source(args)`:

```python
def load_source(args) -> tuple[Block, str]:
    if getattr(args, "profile", None):
        profiles = driver_of(args).profiles
        if args.profile not in profiles:
            raise CliError(f"unknown profile '{args.profile}' (known: {', '.join(sorted(profiles))})")
        prof = profiles[args.profile]
        return prof.block, prof.label
    b = backup.load(args.file)
    return b.block, f"{args.file} ({b.created})"
```

  - `cmd_diff`: unchanged apart from `diff_blocks` now raising on a cross-driver source.
  - `parse_pairs(pairs, fahrenheit, table)`: `SETTINGS` becomes `table`, and the linked loop is `for a, b in table.linked:`. `cmd_set` resolves `table = driver_of(args).table` before opening the port and passes it.
  - `skip_set(text, table)`: `set(SETTINGS)` becomes `set(table)`. Its callers pass `driver_of(args).table`.
  - `cmd_copy`:
    - `note = plain(driver.connect_note_html)` and print it where `ONE_PACK_NOTE` was.
    - The source prompt is `f"Set up the SOURCE pack ({driver.setup_hint}), then press Enter"`. The target prompt is `f"Move the adapter to the TARGET pack ({driver.setup_hint}), then press Enter"`.
    - The default save name is `f"{driver.name}-source-{datetime.now():%Y%m%d-%H%M%S}.json"`.
  - `run_write`: `make_plan(block, targets, fahrenheit=f, source=source)`.
  - `cmd_clock`: `driver = pack.driver`; if `not driver.supports_clock`: `raise CliError(f"the {driver.label} has no clock")`. Use `frame = driver.clock_frame(target)`.
  - `cmd_live`: `pack.read_live()`.
  - `cmd_keys`:

```python
def cmd_keys(args) -> int:
    driver = driver_of(args)
    if args.profiles:
        for name, prof in driver.profiles.items():
            print(f"{name:20s} {prof.label}")
        return 0
    f = args.fahrenheit
    group = None
    for key, s in driver.table.items():
        ...  # unchanged body
```

- [ ] **Step 4: Run everything**

Run: `.venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests`
Expected: `78 passed`, ruff clean. Then run `.venv/bin/eg4tool restore --profile eg4-shipped --simulate --dry-run --log-dir "$TMPDIR"` and check it prints "The pack already has these values."

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "CLI: --bms selects the driver; profiles, keys and texts come from it"
```

---

### Task 5: GUI BMS selector and per-driver Settings tab

**Files:**
- Modify: `src/eg4tool/gui.py`, `src/eg4tool/settings_view.py`, `src/eg4tool/qtcommon.py`
- Test: `tests/test_gui.py`

**Interfaces:**
- Consumes: `drivers.DRIVERS`, `drivers.get`, `Driver.*`
- Produces:
  - `MainWindow.bms: QComboBox` (data = driver name), the `MainWindow.driver` property, `MainWindow.install_settings(driver)`
  - `SettingsTab(main, driver)` with a `.driver` attribute; `SettingsPage(page, fields, table)`
  - `qtcommon.error_text(e, driver=None)`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_gui.py`):

```python
def test_bms_selector_shows_the_driver_and_locks_while_connected(window):
    app, w = window
    assert w.bms.currentData() == "eg4-ll" and w.bms.currentText() == "EG4-LL"
    assert w.settings.driver.name == "eg4-ll"
    connect_sim(app, w)
    assert not w.bms.isEnabled()
    w.toggle_connect()
    assert w.bms.isEnabled()


def test_backup_from_another_bms_is_not_offered(window, monkeypatch, tmp_path):
    app, w = window
    connect_sim(app, w)
    from eg4tool import backup
    from eg4tool.block import Block

    class Other:
        label = "Other BMS"
    foreign = Block.__new__(Block)
    object.__setattr__(foreign, "driver", Other())
    object.__setattr__(foreign, "registers", w.settings.block.registers)
    monkeypatch.setattr(backup, "load", lambda p: backup.Backup(foreign, "now", "x"))
    monkeypatch.setattr(gui.settings_view.QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "x.json"), ""))
    errors = []
    monkeypatch.setattr(gui.settings_view.QMessageBox, "critical", lambda *a: errors.append(a[2]))
    n = len(w.settings.sources)
    assert w.settings.open_backup() is False
    assert len(w.settings.sources) == n and "Other BMS" in errors[0]
```

Add `from eg4tool import settings_view` exposure in `gui.py` (`from . import settings_view` at the top) so the monkeypatch path `gui.settings_view` works.

- [ ] **Step 2: Run them and see them fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_gui.py -q -k "bms or another"`
Expected: FAIL (`AttributeError: 'MainWindow' object has no attribute 'bms'`).

- [ ] **Step 3: `qtcommon.py`**:

```python
from . import backup, drivers
from .block import BlankBlockError, DriverMismatch
...

def error_text(e: Exception, driver=None) -> str:
    if isinstance(e, NoReply):
        help_text = (driver or drivers.get(drivers.DEFAULT)).connection_help
        return f"{e}\n\n{help_text}" if help_text else str(e)
    if isinstance(e, (BlankBlockError, StaleError, SettingError, backup.BackupError, DriverMismatch)):
        return str(e)
    return f"{type(e).__name__}: {e}"
```

- [ ] **Step 4: `settings_view.py`**:
  - Delete the module constants `DRIVER`, `PAGES`, `ORDERED_KEYS`, `PAGE_OF` and `PARTNER`, and the imports of `drivers` and `SETTINGS`/`LINKED_PAIRS` (keep `SettingError`).
  - `SettingsPage.__init__(self, page, fields, table)`: store `self.table = table`, and `SETTINGS[k]` becomes `table[k]` in the tooltip and in `update_units`.
  - `SettingsTab.__init__(self, main, driver)`:
    - Set `self.driver = driver` and `self.table = driver.table`.
    - `self.sources = [Source(p.short, p.block, p.label) for p in driver.profiles.values()]`.
    - `for key in driver.ordered_keys:`, and `self.pages = [SettingsPage(p, self.fields, self.table) for p in driver.pages]`.
  - Throughout the class, `SETTINGS[` becomes `self.table[`, `ORDERED_KEYS` becomes `self.driver.ordered_keys`, `PAGE_OF[` becomes `self.driver.page_of[`, `PAGES` becomes `self.driver.pages`, and `PARTNER.get(key)` becomes `self.table.partner(key)`.
  - The linked-pair status in `on_commit`:

```python
                pair = self.table.pair(key)
                if pair is not None and key in self.staged:
                    other = self.table.partner(key)
                    self.main.status(
                        f"{self.table[key].label} and {self.table[other].label} are set together ({pair.reason})."
                    )
```

  - `save_backup`: the default name is `f"{self.driver.name}-backup-{datetime.now():%Y%m%d-%H%M%S}.json"`, and `qtcommon.error_text(e, self.driver)`.
  - `open_backup`, after a successful load:

```python
        if b.block.driver is not self.driver:
            QMessageBox.critical(
                self, "Cannot use this file",
                f"This backup is from a {b.block.driver.label} BMS; the selected BMS type is {self.driver.label}. "
                "Settings never move between different BMS types.",
            )
            return False
```

  - `keep_for_copy`, step 2 text: `f"2. On the target pack: {self.driver.setup_hint}. Connect.\n"`.

- [ ] **Step 5: `gui.py`**:
  - Imports: `from . import __version__, backup, drivers, qtcommon, settings_view`. Delete the module constants `DRIVER` and `ONE_PACK_NOTE`.
  - `MainWindow.__init__`:
    - `self.note = note` (keep a reference).
    - Before the port widgets: `bar.addWidget(QLabel("BMS:"))`, `self.bms = QComboBox()`, then `for d in drivers.DRIVERS.values(): self.bms.addItem(d.label, d.name)`, the index set to `self.bms.findData(drivers.DEFAULT)`, and `bar.addWidget(self.bms)`.
    - `self.settings = SettingsTab(self, self.driver)`. Connect `self.bms.currentIndexChanged.connect(self.driver_changed)`, and call `self.driver_changed()` after the tabs are added.
  - New methods:

```python
    @property
    def driver(self):
        return drivers.get(self.bms.currentData())

    def driver_changed(self):
        d = self.driver
        self.note.setText(d.connect_note_html)
        self.note.setVisible(bool(d.connect_note_html))
        self.clock.note.setText(d.clock_note)
        self.tabs.setTabVisible(self.tabs.indexOf(self.clock), d.supports_clock)
        if self.settings.driver is not d:
            self.install_settings(d)

    def install_settings(self, driver):
        old = self.settings
        i = self.tabs.indexOf(old)
        self.settings = SettingsTab(self, driver)
        self.settings.review_requested.connect(self.review)
        self.tabs.removeTab(i)
        self.tabs.insertTab(i, self.settings, "Settings")
        old.deleteLater()
        self.settings.set_block(None)
```

  - `ClockTab`: keep the note as `self.note`, with `QLabel()` empty at construction (text set by `driver_changed`).
  - `set_connected`: `self.bms.setEnabled(not on)`.
  - `toggle_connect`: `driver = self.driver`. Use `driver.simulator()` and `SerialTransport(device, params=driver.serial)`. Add `self.log.info(f"BMS type {driver.label}")` and `self.pack = driver.open_device(transport, self.log)`.
  - `got_block`:

```python
        warnings = block.driver.identity_warnings(block)
        for warning in warnings:
            self.log.warn(warning)
        text = f"<b>{block.model}</b> · firmware {block.firmware}"
        if warnings:
            text += f" · <b style='color:{WARN.name()}'>check BMS type</b>"
        self.identity.setText(text)
        self.identity.setToolTip("\n".join(warnings))
```

  - `got_live`: `self.status(f"The pack is not answering. {self.pack.driver.no_reply_hint}", error=True)`.
  - `review()` job: `make_plan(block, targets, fahrenheit=fahrenheit, source=src.block if from_source else None)`.
  - Every `error_text(err)` becomes `error_text(err, self.driver)` in `MainWindow`, and `error_text(err, self.main.driver)` in `ReviewDialog` and `ClockTab`.

- [ ] **Step 6: Run everything**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests`
Expected: `80 passed`, ruff clean. Then take screenshots with `EG4TOOL_SHOTS=$TMPDIR/shots` and view `live.png` and a Settings page: the BMS combo sits left of the port, the banner is unchanged, and the Live temperatures line is the labelled list.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "GUI: BMS selector, Settings tab built from the driver, cross-BMS backups refused"
```

---

### Task 6: Toy driver, import boundary, remove legacy names, README

**Files:**
- Create: `tests/toy_driver.py`
- Modify: `tests/test_drivers.py`, `tests/test_gui.py`, `tests/test_engine.py` (imports), `src/eg4tool/settings.py` (remove `__getattr__`), `README.md`, `docs/superpowers/specs/2026-10-22-bms-drivers-design.md` (status line)

**Interfaces:**
- Consumes: everything above.
- Produces: `tests/toy_driver.TOY` (name `"toy"`, address 7, block 0x100 ×8).

- [ ] **Step 1: Write `tests/toy_driver.py`**:

```python
"""A made-up BMS that shares nothing with EG4-LL: other registers, address,
baud, rules and layout. It proves the engine has no hidden EG4 assumptions.
Test-only; never registered in the shipped app."""

from functools import cached_property

from eg4tool.block import Identity, ascii_text
from eg4tool.drivers.base import Driver, Profile
from eg4tool.layout import TEMP, Page, Section
from eg4tool.live import Live
from eg4tool.settings import LinkedPair, SettingTable
from eg4tool.sim import FakeModbusDevice
from eg4tool.transport import SerialParams

START, COUNT = 0x100, 8  # 0x100 name, 0x101-0x106 settings, 0x107 unknown

SPEC = {
    "low_cut": (0x101, 0.01, 0, "V", None, 2),
    "high_cut": (0x102, 0.01, 0, "V", None, 2),
    "top_cut": (0x103, 0.01, 0, "V", None, 2),
    "trip_a": (0x104, 1, 0, "A", None, 0),
    "trip_b": (0x105, 1, 0, "A", None, 0),
    "hot_cut": (0x106, 1, -40, "°C", None, 0),
}
LIMITS = {
    "low_cut": (2.5, 3.0, 0.01),
    "high_cut": (3.3, 3.7, 0.01),
    "top_cut": (3.4, 3.8, 0.01),
    "trip_a": (10, 200, 1),
    "trip_b": (10, 200, 1),
    "hot_cut": (40, 80, 1),
}
ORDER = [("low_cut", "high_cut", True), ("high_cut", "top_cut", True)]

IMAGE = [0] * (START + COUNT)
IMAGE[0:6] = [5120, 150, 80, 3200, 3210, 3205]  # 51.20 V, +1.50 A, 80 %, three cells
IMAGE[START:] = [0x5459, 280, 350, 365, 100, 100, 100, 7]  # "TY", ..., hot_cut 60 °C, unknown 7


class Toy(Driver):
    name = "toy"
    label = "Toy BMS"
    table = SettingTable.build(SPEC, LIMITS, ORDER, linked=[LinkedPair("trip_a", "trip_b", "toy pair")])
    block_start = START
    block_count = COUNT
    serial = SerialParams(baud=9600)
    address = 7
    live_reads = ((0x00, 6),)
    pages = (
        Page(
            "All",
            (
                Section("Voltage", ("Low", "High", "Top"), (("Cell", ("low_cut", "high_cut", "top_cut")),), unit="V"),
                Section("Current", ("A", "B"), (("Trip", ("trip_a", "trip_b")),), unit="A"),
                Section("Heat", ("Cut",), (("Hot", ("hot_cut",)),), unit=TEMP),
            ),
        ),
    )
    connection_help = "toy: check the toy cable"

    def is_writable(self, register):
        return START + 1 <= register < START + COUNT

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(
            pack_voltage=r[0] / 100, current=r[1] / 100, soc=r[2], soh=100,
            cells=[v / 1000 for v in r[3:6]], status_text="Idle",
        )

    @cached_property
    def profiles(self):
        return {"toy-default": Profile("toy-default", "Toy defaults", "Toy defaults", self.block(IMAGE[START:]))}

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, state_file=state_file, state_from=START)


TOY = Toy()
```

- [ ] **Step 2: Write the toy and boundary tests** (append to `tests/test_drivers.py`):

```python
import ast

from eg4tool import drivers
from eg4tool.planner import targets_from_block
from eg4tool.service import CONFIRMED, NOT_APPLIED, apply_plan
from eg4tool.session import SessionLog

from toy_driver import TOY  # tests/ is on sys.path under pytest's rootdir conftest-less mode

TOY_REF = TOY.profiles["toy-default"].block


def toy_device(**kw):
    sim = TOY.simulator()
    for k, v in kw.items():
        setattr(sim, k, v)
    return sim, TOY.open_device(sim, SessionLog())


def test_toy_plan_moves_a_chain_up_top_first():
    p = make_plan(TOY_REF, {"high_cut": 370, "top_cut": 380})
    assert p.ok, p.errors
    assert [c.key for c in p.changes] == ["top_cut", "high_cut"]


def test_toy_rules_and_limits_are_the_toys():
    assert not make_plan(TOY_REF, {"high_cut": 290}).ok  # below its 3.30 V floor and below low_cut
    p = make_plan(TOY_REF, {"trip_a": 120})
    assert not p.ok and "toy pair" in p.errors[0]
    assert make_plan(TOY_REF, {"trip_a": 120, "trip_b": 120}).ok


def test_toy_write_is_read_back_and_addressed_to_7():
    sim, dev = toy_device()
    res = apply_plan(dev, make_plan(dev.read_block(), {"low_cut": 290}))
    assert res.ok and res.results[0].status == CONFIRMED and sim.image[0x101] == 290
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert any(frame.startswith("07 10 01 01") for frame in tx)


def test_toy_dropped_write_is_not_applied():
    sim, dev = toy_device(floors={0x106: 110})
    res = apply_plan(dev, make_plan(dev.read_block(), {"hot_cut": 50}))  # 50 °C = raw 90, below the floor
    assert res.results[0].status == NOT_APPLIED


def test_toy_backup_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "toy.json"
    backup.save(f, TOY_REF)
    assert json.loads(f.read_text())["bms"] == "toy"
    with pytest.raises(backup.BackupError, match="toy"):
        backup.load(f)  # not registered
    monkeypatch.setitem(drivers.DRIVERS, "toy", TOY)
    b = backup.load(f)
    assert b.block.driver is TOY and b.block == TOY_REF


def test_nothing_crosses_between_bms_types():
    with pytest.raises(DriverMismatch):
        diff_blocks(REF, TOY_REF)
    with pytest.raises(DriverMismatch):
        make_plan(REF, targets_from_block(TOY_REF), source=TOY_REF)
    from eg4tool.planner import identity_warnings
    with pytest.raises(DriverMismatch):
        identity_warnings(REF, TOY_REF, "the backup")
    _sim, dev = toy_device()
    with pytest.raises(DriverMismatch):
        apply_plan(dev, make_plan(REF, {"cell_overvoltage_warning": 3650}))
    assert _sim.writes == []


def test_generic_code_never_imports_the_eg4_driver():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "drivers":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            if any("eg4_ll" in n for n in names):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
```

`from toy_driver import TOY` works because pytest's default `prepend` import mode puts `tests/` (which has no `__init__.py`) on `sys.path`. Also add these imports at the top of the file, beside the Task 3 ones: `from eg4tool.block import DriverMismatch` and `from eg4tool.service import diff_blocks`.

- [ ] **Step 3: GUI toy test** (append to `tests/test_gui.py`):

```python
def test_gui_runs_a_toy_bms(tmp_path, monkeypatch):
    from eg4tool import drivers, qtcommon
    from toy_driver import TOY
    monkeypatch.setitem(drivers.DRIVERS, "toy", TOY)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    try:
        w.bms.setCurrentIndex(w.bms.findData("toy"))
        assert w.settings.driver is TOY and set(w.settings.fields) == set(TOY.table)
        assert not w.tabs.isTabVisible(w.tabs.indexOf(w.clock))
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
        wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
        assert w.identity.text().startswith("<b>TY</b>")
        assert w.live.soc.value.text() == "80 %"
        edit(w, "low_cut", "2.9")
        wait(app, lambda: "low_cut" in w.settings.staged)
        dlg = open_review(app, w, monkeypatch)
        assert [c.key for c in dlg.plan.changes] == ["low_cut"]
        dlg.ack.setChecked(True)
        dlg.write_btn.click()
        wait(app, lambda: dlg.result is not None)
        assert dlg.result.ok and dlg.result.results[0].status == CONFIRMED
    finally:
        w.close()
```

- [ ] **Step 4: Run them**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`
Expected: all pass. If the boundary test fails, the listed `file:line` imports are the ones to route through `eg4tool.drivers`.

- [ ] **Step 5: Remove the legacy names**
  - Delete `_LEGACY` and `__getattr__` from `settings.py`.
  - In `tests/test_engine.py`, `from eg4tool.settings import ORDER, SETTINGS, Rule, SettingError, f_to_c_exact` becomes `from eg4tool.settings import Rule, SettingError, f_to_c_exact` plus `SETTINGS, ORDER = EG4_LL.table, EG4_LL.table.order` after the `EG4_LL` import.
  - Run `grep -rn "settings import.*\(SETTINGS\|ORDER\|RANK\|LINKED_PAIRS\|TABLE\|REG_\|is_threshold\)" src tests`. Expected: nothing.

- [ ] **Step 6: README**. Add a section after the CLI usage section:

```markdown
## Adding a BMS

Everything specific to one BMS family lives in a driver package under
`src/eg4tool/drivers/` (see `eg4_ll/` for the complete example). A driver
subclasses `drivers.base.Driver` and provides:

- a `SettingTable`: registers, scaling, safe ranges, the order rules
  between settings, and any settings that must move together;
- the settings block range and which registers may be written;
- identity (model, firmware, clock) and live-value decoding;
- the Settings tab pages, built-in profiles, connection texts;
- a simulator, so the whole flow can be tested without hardware.

Register it in `drivers/__init__.py`. The engine, CLI (`--bms`) and app
(the BMS selector) pick it up from there. `tests/toy_driver.py` is a
minimal driver used to keep the engine honest.

A new driver should only ever be used for `info`, `backup`, `diff` and
`live` until its register map has been checked against a real unit.
```

In the README intro, change "EG4-LL battery packs" to "EG4-LL battery packs (other BMS types can be added as drivers)".

- [ ] **Step 7: Spec status**: in the spec, change `Status: design, awaiting review.` to `Status: implemented on branch bms-drivers.`

- [ ] **Step 8: Run everything one last time**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests && .venv/bin/python packaging/build.py`
Expected: all tests pass, ruff is clean, and the build produces `dist/EG4 BMS Tool.app` and the CLI binary. Check the CLI excludes Qt with `dist/eg4tool keys --bms eg4-ll | head`.

- [ ] **Step 9: Commit**

```bash
git add -A src tests README.md docs
git commit -m "Toy driver and import-boundary tests; drop the transitional EG4 names"
```

---

### Task 7: Push and CI

- [ ] **Step 1:** `git push -u origin bms-drivers`, then watch the build with `gh run watch --exit-status $(gh run list --branch bms-drivers --limit 1 --json databaseId -q '.[0].databaseId')`. Expected: all four platform builds succeed.
- [ ] **Step 2:** Report to the user: the branch, the CI result, the wording changes (Live temperatures line, linked-pair messages, copy prompts, backup default names), and that `main` is untouched for the real-pack test. Do not merge until the user says so.
