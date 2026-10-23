# JK-PB Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a JK PB2A16S20P driver (JK-PB, 16S LiFePO4) with the full read/write/clock/live feature set. To support it, teach the generic core 32-bit and signed settings, multi-area blocks with an address stride, chunked reads, per-session addresses, and a driver check that can refuse writes.

**Architecture:**
- `Setting` gains `width`, `signed`, `stride` and `word_order`, so it can encode and decode its own registers.
- A `Block` holds one tuple of registers per `Area(start, count, stride)` that the driver lists.
- Writes become one FC16 frame per setting.
- `ModbusDevice` gets a session address and a `write_refusal` set by `Driver.check_device`.
- The JK driver is a new package that builds its table from byte offsets through one `STRIDE` constant.

**Tech Stack:** Python 3.11+, PySide6, pyserial, pytest. Lint with `uvx ruff check --select F,E9,B --ignore B905 src tests packaging`.

**Spec:** `docs/superpowers/specs/2026-10-23-jk-pb-driver-design.md`

## Global Constraints

- Branch `jk-pb` (already created from `rename-packkeeper`). The package is `packkeeper` and the repo is `/Users/rar/eg4_tool`.
- Run tests with `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`. The full suite takes about 40 s and must pass at the end of every task.
- Lint: `uvx ruff check --select F,E9,B --ignore B905 src tests packaging` must be clean at the end of every task.
- `src/packkeeper/drivers/eg4_ll/settings_table.py` stays byte-for-byte unchanged. Its sha256 is pinned by `tests/test_drivers.py`.
- No module outside `src/packkeeper/drivers/` may import `eg4_ll` or `jk_pb`, as enforced by `test_generic_code_never_imports_the_eg4_driver`. Extend that test to cover `jk_pb` in Task 6.
- EG4-LL behaviour must not change. That covers frames, write order, refusals, exit codes, sim state and CLI text. The only allowed differences are:
  - the backup format (version 3, with `areas`);
  - the GUI review text "one setting at a time".
- Never decompile vendor software. The JK sources are JK's RS485 Modbus V1.0/V1.1 PDFs (in syssi/esphome-jk-bms `docs/pb2a16s20p/`) and esphome-jk-bms discussion #501.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq
  ```
- Match the surrounding code: short docstrings, sparse comments, the same naming style.

## File structure

| File | Change |
|---|---|
| `src/packkeeper/settings.py` | `Setting.width/signed/stride/word_order`, `registers`, `encode`, `decode`. `SettingTable.covering`. The extended spec tuple in `build`. |
| `src/packkeeper/block.py` | `Area`. Multi-area `Block`. |
| `src/packkeeper/drivers/base.py` | `areas`, `block(*areas)`, `write_frame(setting, raw, address)`, `clock_frame(t, address)`, `address_choices`, `check_device`, `info_lines`, `open_device(..., address)`. |
| `src/packkeeper/device.py` | `read_area` (chunked, stride-aware), `write_setting(setting, raw)`, session `address`, `write_refusal`. |
| `src/packkeeper/sim.py` | Stride, `readable`, `answer_to`. |
| `src/packkeeper/planner.py`, `service.py` | Setting-level writes. Writable guard over every register. `describe_register(block, reg)`. |
| `src/packkeeper/backup.py` | Format version 3 with `areas`. Versions 1 and 2 still load. |
| `src/packkeeper/cli.py` | `--address`, write-refusal check, `info_lines`, the session address in frames. |
| `src/packkeeper/gui.py` | Address box, write-refusal display, the session address in frames, info lines on the Live tab. |
| `src/packkeeper/drivers/eg4_ll/driver.py`, `tests/toy_driver.py` | `areas` instead of `block_start` / `block_count`. |
| `src/packkeeper/drivers/jk_pb/` | New: `__init__.py`, `registers.py`, `table.py`, `layout.py`, `live.py`, `sim.py`, `driver.py`. |
| `src/packkeeper/drivers/__init__.py` | Register JK-PB. |
| `tests/test_wide.py`, `tests/wide_toy_driver.py` | New: core tests on a stride-2 toy. |
| `tests/test_jk_pb.py` | New: JK driver tests. |
| `README.md` | The JK-PB section and `--address`. |

---

### Task 1: Wide and signed settings

**Files:**
- Modify: `src/packkeeper/settings.py`
- Create: `tests/test_wide.py`

**Interfaces:**
- Produces:
  - `Setting.width: int = 1`, `Setting.signed: bool = False`, `Setting.stride: int = 1`, `Setting.word_order: str = "high_first"`.
  - `Setting.registers -> tuple[int, ...]`, `Setting.raw_bounds -> tuple[int, int]`, `Setting.encode(raw: int) -> list[int]`, `Setting.decode(words) -> int`.
  - `SettingTable.covering: dict[int, Setting]`, which maps every register a setting occupies.
  - `SettingTable.build(..., stride: int = 1, word_order: str = "high_first")`. A spec tuple may have 6 fields, or 8 with `(..., decimals, width, signed)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_wide.py`:

```python
import pytest

from packkeeper.settings import SettingError, SettingTable

SPEC = {
    "amps": (0x202, 0.001, 0, "A", None, 3, 2, False),
    "cold": (0x206, 0.1, 0, "°C", None, 1, 2, True),
    "small": (0x20A, 1, 0, "s", None, 0),
}
LIMITS = {"amps": (1, 300, 0.001), "cold": (-20, 10, 0.1), "small": (1, 600, 1)}


def table(word_order="high_first"):
    return SettingTable.build(SPEC, LIMITS, [], stride=2, word_order=word_order)


def test_wide_setting_registers_follow_the_stride():
    t = table()
    assert t["amps"].registers == (0x202, 0x204)
    assert t["small"].registers == (0x20A,)
    assert t.covering[0x204] is t["amps"] and 0x204 not in t.by_register


def test_encode_decode_high_and_low_first():
    s = table()["amps"]
    assert s.encode(150000) == [0x0002, 0x49F0]
    assert s.decode([0x0002, 0x49F0]) == 150000
    lo = table("low_first")["amps"]
    assert lo.encode(150000) == [0x49F0, 0x0002]
    assert lo.decode([0x49F0, 0x0002]) == 150000


def test_signed_values():
    s = table()["cold"]
    assert s.encode(-50) == [0xFFFF, 0xFFCE]
    assert s.decode([0xFFFF, 0xFFCE]) == -50
    assert s.parse("-5") == -50 and s.format(-50) == "-5.0 °C"
    assert s.limit_error(-50) is None and s.limit_error(-201) is not None


def test_raw_range_uses_the_width():
    s = table()["amps"]
    assert s.raw_bounds == (0, 0xFFFFFFFF)
    assert s.parse("300") == 300000  # far above 16 bits, still fits
    with pytest.raises(SettingError, match="does not fit"):
        s.encode(1 << 32)
    assert table()["cold"].raw_bounds == (-(1 << 31), (1 << 31) - 1)


def test_six_field_spec_is_one_unsigned_register():
    s = table()["small"]
    assert (s.width, s.signed, s.stride) == (1, False, 2)
    with pytest.raises(SettingError, match="does not fit"):
        s.raw(70000)


def test_overlapping_settings_are_refused():
    spec = {"a": (0x10, 1, 0, None, None, 0, 2, False), "b": (0x11, 1, 0, None, None, 0)}
    with pytest.raises(ValueError, match="0x11"):
        SettingTable.build(spec, {"a": (0, 9, 1), "b": (0, 9, 1)}, [])
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_wide.py -q`
Expected: FAIL, with a `ValueError` about too many values to unpack, or a TypeError on `stride=`.

- [ ] **Step 3: Implement in `src/packkeeper/settings.py`**

Add these fields at the end of `Setting`, after `label`:

```python
    width: int = 1  # 16-bit registers it occupies
    signed: bool = False
    stride: int = 1  # address step between its registers
    word_order: str = "high_first"  # or "low_first"
```

Add these methods to `Setting`, after `value`:

```python
    @property
    def registers(self) -> tuple[int, ...]:
        return tuple(self.register + i * self.stride for i in range(self.width))

    @property
    def raw_bounds(self) -> tuple[int, int]:
        bits = 16 * self.width
        if self.signed:
            return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        return 0, (1 << bits) - 1

    def encode(self, raw: int) -> list[int]:
        """The register words for `raw`, in the order they sit in the device."""
        lo, hi = self.raw_bounds
        if not lo <= raw <= hi:
            raise SettingError(f"{self.label}: {raw} does not fit the register")
        v = raw & ((1 << (16 * self.width)) - 1)
        words = [(v >> (16 * (self.width - 1 - i))) & 0xFFFF for i in range(self.width)]
        return words[::-1] if self.word_order == "low_first" else words

    def decode(self, words) -> int:
        words = list(words)
        if self.word_order == "low_first":
            words.reverse()
        v = 0
        for w in words:
            v = v << 16 | w
        if self.signed and v >= 1 << (16 * self.width - 1):
            v -= 1 << (16 * self.width)
        return v
```

In `Setting.raw`, replace the range check

```python
        if not 0 <= r <= 0xFFFF:
            raise SettingError(f"{self.label}: {value} does not fit the register")
```

with

```python
        lo, hi = self.raw_bounds
        if not lo <= r <= hi:
            raise SettingError(f"{self.label}: {value} does not fit the register")
```

In `SettingTable.__init__`, after building `self.by_register`, add the `covering` map with an overlap check:

```python
        self.covering: dict[int, Setting] = {}
        for s in settings.values():
            for r in s.registers:
                if r in self.covering:
                    raise ValueError(f"{s.key} and {self.covering[r].key} both use register 0x{r:02X}")
                self.covering[r] = s
```

In `SettingTable.build`, add `stride: int = 1, word_order: str = "high_first"` to the keyword parameters, after `label`. Update the docstring to say a spec tuple may end with `width, signed`. Replace the loop header and `Setting(...)` call with:

```python
        for key, spec in table.items():
            reg, mult, offset, unit, _cls, decimals, *wide = spec
            width = wide[0] if wide else 1
            signed = wide[1] if len(wide) > 1 else False
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
                width=width,
                signed=signed,
                stride=stride,
                word_order=word_order,
            )
```

- [ ] **Step 4: Run the new tests and the full suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_wide.py -q`, then the full suite, then lint.
Expected: all pass. The vendored-table hash test still passes because the table file is untouched.

- [ ] **Step 5: Commit**

```bash
git add src/packkeeper/settings.py tests/test_wide.py
git commit -m "Settings can span two registers and be signed"
```

---

### Task 2: Areas, multi-area blocks, chunked stride-aware reads

**Files:**
- Modify: `src/packkeeper/block.py`, `src/packkeeper/drivers/base.py`, `src/packkeeper/device.py`, `src/packkeeper/sim.py`, `src/packkeeper/backup.py` (interim single-area access only), `src/packkeeper/drivers/eg4_ll/driver.py`, `tests/toy_driver.py`
- Create: `tests/wide_toy_driver.py`
- Test: `tests/test_wide.py` (append), plus mechanical updates in `tests/test_drivers.py` and `tests/test_gui.py`

**Interfaces:**
- Consumes: `Setting.registers`, `Setting.decode`, `Setting.encode`, `SettingTable.covering` (Task 1).
- Produces:
  - `block.Area(start: int, count: int, stride: int = 1)`, a NamedTuple with `addresses() -> range`.
  - `Block(driver, areas: tuple[tuple[int, ...], ...])`.
  - On `Block`: `registers` (a flat tuple, all areas concatenated), `reg(address)`, `regs(start, count)`, `addresses() -> list[tuple[int, int]]`, `raw(key)`, `raws()`, and `with_raws({address: raw})`. In `with_raws`, a setting's start address takes the setting's full raw value.
  - `Driver.areas: tuple[Area, ...]`, replacing `block_start` and `block_count`. `Driver.block(*areas) -> Block`.
  - `ModbusDevice.read_area(area, retries=READ_RETRIES) -> list[int]`, and the module constant `device.MAX_READ = 125`.
  - `FakeModbusDevice(..., stride=1, readable=None, answer_to=None)`.

- [ ] **Step 1: Create the stride-2 toy driver** `tests/wide_toy_driver.py`:

```python
"""A made-up BMS with 32-bit and signed settings in a byte-addressed map
(stride 2), like JK's. Test-only; never registered in the shipped app."""

from packkeeper.block import Area, Identity, ascii_text
from packkeeper.drivers.base import Driver
from packkeeper.layout import TEMP, Page, Section
from packkeeper.live import Live
from packkeeper.settings import SettingTable
from packkeeper.sim import FakeModbusDevice

START, COUNT, STRIDE = 0x200, 6, 2  # 0x200 name, 0x202/0x204 amps, 0x206/0x208 cold, 0x20A unknown

SPEC = {
    "amps": (0x202, 0.001, 0, "A", None, 3, 2, False),
    "cold": (0x206, 0.1, 0, "°C", None, 1, 2, True),
}
LIMITS = {"amps": (1, 300, 0.001), "cold": (-20, 10, 0.1)}

IMAGE = [0] * (START + COUNT * STRIDE)
IMAGE[0:2] = [5120, 80]  # 51.20 V, 80 %
for i, v in enumerate([0x5754, 0x0002, 0x49F0, 0xFFFF, 0xFFCE, 9]):  # "WT", 150 A, -5.0 °C, unknown 9
    IMAGE[START + i * STRIDE] = v


class WideToy(Driver):
    name = "wide"
    label = "Wide BMS"
    table = SettingTable.build(SPEC, LIMITS, [], stride=STRIDE)
    areas = (Area(START, COUNT, STRIDE),)
    address = 9
    live_reads = (Area(0x00, 2),)
    pages = (
        Page(
            "All",
            (
                Section("Current", ("Trip",), (("Amps", ("amps",)),), unit="A"),
                Section("Cold", ("Cut",), (("Cold", ("cold",)),), unit=TEMP),
            ),
        ),
    )

    def is_writable(self, register):
        return register in self.table.covering

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(pack_voltage=r[0] / 100, current=0.0, soc=r[1], soh=100, cells=[3.3], status_text="Idle")

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, stride=STRIDE, state_file=state_file, state_from=START)


WIDE = WideToy()
```

- [ ] **Step 2: Write the failing tests.** Append to `tests/test_wide.py`:

```python
from packkeeper.block import Area  # noqa: E402
from packkeeper.session import SessionLog  # noqa: E402

from wide_toy_driver import WIDE  # noqa: E402


def wide_device(**kw):
    sim = WIDE.simulator()
    for k, v in kw.items():
        setattr(sim, k, v)
    return sim, WIDE.open_device(sim, SessionLog())


def test_area_addresses_follow_the_stride():
    assert list(Area(0x200, 3, 2).addresses()) == [0x200, 0x202, 0x204]
    assert Area(0x2D, 91) == (0x2D, 91, 1)


def test_block_reads_wide_and_signed_values():
    _sim, dev = wide_device()
    b = dev.read_block()
    assert b.raw("amps") == 150000 and b.raw("cold") == -50
    assert b.reg(0x20A) == 9 and b.unknown_registers() == {}  # 0x20A is not writable here
    assert b.regs(0x202, 2) == [0x0002, 0x49F0]
    with pytest.raises(KeyError):
        b.reg(0x203)  # between two registers of a stride-2 area


def test_with_raws_splits_a_wide_value():
    _sim, dev = wide_device()
    b = dev.read_block().with_raws({0x206: -100})
    assert b.regs(0x206, 2) == [0xFFFF, 0xFF9C] and b.raw("cold") == -100


def test_long_areas_are_read_in_chunks_at_stride_addresses():
    from packkeeper import device

    class Long(type(WIDE)):
        areas = (Area(0x1000, 141, 2),)

    image = [7] * 0x1200
    sim = WIDE.simulator()
    sim.image, sim.stride = image, 2
    dev = Long().open_device(sim, SessionLog())
    regs = dev.read_area(Long.areas[0])
    assert len(regs) == 141 and device.MAX_READ == 125
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert [t[:17] for t in tx] == ["09 03 10 00 00 7D", "09 03 10 FA 00 10"]


def test_eg4_still_reads_its_block_in_one_frame():
    from packkeeper.drivers.eg4_ll import DRIVER
    from packkeeper.drivers.eg4_ll.sim import FakePack

    dev = DRIVER.open_device(FakePack(), SessionLog())
    dev.read_block()
    tx = [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]
    assert tx == ["01 03 00 2D 00 5B 94 38"]
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_wide.py -q`
Expected: FAIL with `ImportError: cannot import name 'Area'`.

- [ ] **Step 4: Implement `Area` and the multi-area `Block`** in `src/packkeeper/block.py`.

Add `from typing import TYPE_CHECKING, NamedTuple`, then add before `Identity`:

```python
class Area(NamedTuple):
    """Registers read in one go: register i is at start + i * stride."""

    start: int
    count: int
    stride: int = 1

    def addresses(self) -> range:
        return range(self.start, self.start + self.count * self.stride, self.stride)
```

Replace the `Block` class body, from its fields through `regs`, and `raw`, `raws`, `with_raws`, `threshold_registers` and `unknown_registers`, with:

```python
@dataclass(frozen=True)
class Block:
    """The registers of every area the driver reads, unknown ones included."""

    driver: "Driver" = field(repr=False)
    areas: tuple[tuple[int, ...], ...]

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
                if not 0 <= v <= 0xFFFF:
                    raise ValueError(f"register value {v} does not fit 16 bits")

    @property
    def registers(self) -> tuple[int, ...]:
        return tuple(v for values in self.areas for v in values)

    def _locate(self, address: int) -> tuple[int, int]:
        for n, area in enumerate(self.driver.areas):
            i, rem = divmod(address - area.start, area.stride)
            if rem == 0 and 0 <= i < area.count:
                return n, i
        raise KeyError(f"0x{address:02X} is not in a {self.driver.label} block")

    def reg(self, register: int) -> int:
        n, i = self._locate(register)
        return self.areas[n][i]

    def regs(self, start: int, count: int) -> list[int]:
        n, i = self._locate(start)
        if i + count > len(self.areas[n]):
            raise KeyError(f"0x{start:02X} x{count} runs past the end of its area")
        return list(self.areas[n][i : i + count])

    def addresses(self) -> list[tuple[int, int]]:
        """(address, value) for every register, area by area."""
        return [(a, v) for area, values in zip(self.driver.areas, self.areas) for a, v in zip(area.addresses(), values)]

    def raw(self, key: str) -> int:
        s = self.driver.table[key]
        return s.decode(self.reg(r) for r in s.registers)

    def raws(self) -> dict[str, int]:
        return {k: self.raw(k) for k in self.driver.table}
```

Keep `identity`, `model`, `firmware`, `serial`, `clock`, `clock_raw`, `thresholds_blank`, `check_not_blank` and `same_driver` as they are. Remove the `start` property. Then:

```python
    def with_raws(self, changes: dict[int, int]) -> "Block":
        """A copy with these values: a setting's start address takes its whole raw value."""
        areas = [list(values) for values in self.areas]
        by_register = self.driver.table.by_register
        for register, raw in changes.items():
            s = by_register.get(register)
            pairs = zip(s.registers, s.encode(raw)) if s else ((register, raw),)
            for address, word in pairs:
                n, i = self._locate(address)
                areas[n][i] = word
        return Block(self.driver, tuple(tuple(a) for a in areas))

    def threshold_registers(self) -> dict[int, int]:
        """Every register the driver allows writing, known or not."""
        return {a: v for a, v in self.addresses() if self.driver.is_writable(a)}

    def unknown_registers(self) -> dict[int, int]:
        """Writable registers no setting covers; never written."""
        covering = self.driver.table.covering
        return {r: v for r, v in self.threshold_registers().items() if r not in covering}
```

- [ ] **Step 5: Update the driver base** in `src/packkeeper/drivers/base.py`:
  - Import: `from ..block import Area, Block, Identity`.
  - Replace the `block_start: int` and `block_count: int` attributes with
    ```python
    areas: tuple[Area, ...]  # what one block read covers, in order
    ```
  - Change the `live_reads` comment to `# Area (or (start, count)) reads, concatenated for decode_live`.
  - Replace `block`:

```python
    def block(self, *areas) -> Block:
        """A block from one register list per area (a single list for one area)."""
        return Block(self, tuple(tuple(a) for a in areas))
```

- [ ] **Step 6: Make device reads chunked and stride-aware** in `src/packkeeper/device.py`.

Import `Area` from `.block`, and add `MAX_READ = 125  # FC03 limit` next to `READ_RETRIES`. Add after `read`:

```python
    def read_area(self, area, retries: int = READ_RETRIES) -> list[int]:
        """One area, in FC03 reads of at most MAX_READ registers."""
        area = Area(*area)
        out: list[int] = []
        for i in range(0, area.count, MAX_READ):
            out += self.read(area.start + i * area.stride, min(MAX_READ, area.count - i), retries)
        return out
```

Replace the first line of `read_block`'s body that reads the block with

```python
        block = d.block(*(self.read_area(a) for a in d.areas))
```

and replace `read_live`'s loop with

```python
        for area in self.driver.live_reads:
            regs += self.read_area(area, retries)
```

- [ ] **Step 7: Make the generic fake stride-aware** in `src/packkeeper/sim.py`.

Add keyword parameters `stride: int = 1`, `readable: Callable[[int], bool] | None = None` and `answer_to: int | None = None`. Store them as `self.stride`, `self.readable` and `self.answer_to`. Add to the module docstring: "`stride` is the address step between registers (2 for a byte-addressed map); `answer_to` makes it ignore other addresses." In `transact`, after the `drop_replies` check, add:

```python
        if self.answer_to is not None and address != self.answer_to:
            raise NoReply(f"simulated pack only answers at address {self.answer_to}")
```

Move that check after `address, function = ...` is unpacked. Then replace the address handling for FC_READ and FC_WRITE:

```python
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
            ...rest unchanged (on_write, _save, drop_replies, drop_acks, ack)
```

In `_save`, iterate `range(self.state_from, len(self.image), self.stride)`. When restoring from `state_file`, nothing else changes.

- [ ] **Step 8: Move EG4 and the toy to `areas`, and keep backup working with one area for now.**
  - `src/packkeeper/drivers/eg4_ll/driver.py`: import `Area` from `...block`. Replace `block_start = ...` and `block_count = ...` with `areas = (Area(registers.REG_BLOCK, registers.BLOCK_COUNT),)`.
  - `tests/toy_driver.py`: import `Area`, and replace `block_start = START` and `block_count = COUNT` with `areas = (Area(START, COUNT),)`.
  - `src/packkeeper/backup.py` is interim; Task 5 replaces this. In `to_dict` use `area = d.areas[0]`, with `"start_register": area.start` and `"register_count": area.count`. In `from_dict`, replace the start/count check with:

```python
    area = driver.areas[0]
    if len(driver.areas) != 1 or data.get("start_register") != area.start or data.get("register_count") != area.count:
        raise BackupError(f"{where}: not a {driver.label} settings block (0x{area.start:02X} x{area.count})")
```

- [ ] **Step 9: Update the tests that named the old attributes. These are mechanical changes; the assertions keep their meaning.**
  - In `tests/test_drivers.py` `test_registry_has_eg4_ll_by_default`, change to `d.label == "EG4-LL" and d.areas == ((0x2D, 91, 1),)`.
  - In `tests/test_gui.py` `test_backup_from_another_bms_is_not_offered`, change the attribute to `object.__setattr__(foreign, "areas", w.settings.block.areas)`.
  - Grep with `grep -rn "block_start\|block_count\|\.start\b" src tests` and fix any remaining uses in the same way.

- [ ] **Step 10: Run the full suite and lint**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q` and the lint command.
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add -A src tests
git commit -m "Blocks are a set of areas with an address stride; long areas read in chunks"
```

---

### Task 3: One write per setting, guards over every register

**Files:**
- Modify: `src/packkeeper/drivers/base.py`, `src/packkeeper/device.py`, `src/packkeeper/planner.py`, `src/packkeeper/service.py`, `src/packkeeper/cli.py`, `src/packkeeper/gui.py`
- Test: `tests/test_wide.py` (append). Make mechanical updates in `tests/test_drivers.py` and `tests/test_engine.py`.

**Interfaces:**
- Consumes: Task 1 and Task 2 APIs.
- Produces:
  - `Driver.write_frame(setting: Setting, raw: int, address: int | None = None) -> bytes` and `Driver.clock_frame(t, address: int | None = None) -> bytes`.
  - `Device.write_setting(setting: Setting, raw: int) -> bool`, and `Device.address: int` in the protocol.
  - `Plan.frames(address: int | None = None) -> list[bytes]`.
  - `service.describe_register(block: Block, reg: int) -> str`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_wide.py`:

```python
from packkeeper.planner import make_plan  # noqa: E402
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan  # noqa: E402


def test_a_wide_setting_is_one_fc16_frame():
    _sim, dev = wide_device()
    p = make_plan(dev.read_block(), {"amps": 120000})
    assert p.ok, p.errors
    (frame,) = p.frames()
    assert frame[:7] == bytes([9, 0x10, 0x02, 0x02, 0x00, 0x02, 0x04])
    assert frame[7:11] == bytes([0x00, 0x01, 0xD4, 0xC0])  # 120000 = 0x0001D4C0, high word first
    assert p.frames(address=3)[0][0] == 3


def test_wide_write_is_confirmed_by_read_back():
    sim, dev = wide_device()
    res = apply_plan(dev, make_plan(dev.read_block(), {"cold": -100}))
    assert res.ok and res.results[0].status == CONFIRMED and res.results[0].reported == -100
    assert sim.writes == [(0x206, [0xFFFF, 0xFF9C])]


def test_wide_write_dropped_on_the_low_word_is_not_applied():
    sim, dev = wide_device(floors={0x204: 0xF000})
    res = apply_plan(dev, make_plan(dev.read_block(), {"amps": 120000}))
    assert res.results[0].status == NOT_APPLIED and res.stopped


def test_planner_and_device_check_every_register_of_a_setting():
    import dataclasses

    from packkeeper.device import PackError
    from packkeeper.planner import Change

    sim, dev = wide_device()
    s = WIDE.table["amps"]
    forged = dataclasses.replace(s, register=0x208)  # covers 0x208 (writable) and 0x20A (not)
    with pytest.raises(PackError, match="0x20A"):
        dev.write_setting(forged, 1000)
    plan = make_plan(dev.read_block(), {"amps": 120000})
    plan.changes.append(Change(forged, 0, 1000))
    with pytest.raises(ValueError, match="0x20A"):
        apply_plan(dev, plan)
    assert sim.writes == []
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_wide.py -q`
Expected: FAIL, because `write_frame` takes a register and `frames()` takes no address.

- [ ] **Step 3: Implement the driver frames** in `src/packkeeper/drivers/base.py`:

```python
    def write_frame(self, setting, raw: int, address: int | None = None) -> bytes:
        """The FC16 frame that writes `setting` (all of its registers) to `raw`."""
        return modbus.write_request(self.address if address is None else address, setting.register, setting.encode(raw))

    def clock_frame(self, t: datetime, address: int | None = None) -> bytes:
        register, values = self.clock_write(t)
        return modbus.write_request(self.address if address is None else address, register, values)
```

- [ ] **Step 4: Implement device writes** in `src/packkeeper/device.py`.

In the `Device` protocol, add `address: int` and change `write_setting(self, setting: "Setting", raw: int) -> bool`. Import `Setting` under `TYPE_CHECKING` from `.settings`. In `ModbusDevice`:

```python
    def write_setting(self, setting: "Setting", raw: int) -> bool:
        """One FC16 write covering the whole setting. True if acked, False if no ack.

        The ack proves nothing: callers must re-read the block to confirm.
        """
        for r in setting.registers:
            if not self.driver.is_writable(r):
                raise PackError(f"refusing to write 0x{r:02X}: not a writable settings register")
        return self._write(setting.register, setting.encode(raw))
```

- [ ] **Step 5: Implement the planner** in `src/packkeeper/planner.py`.

```python
    def frames(self, address: int | None = None) -> list[bytes]:
        d = self.base.driver
        return [d.write_frame(c.setting, c.new, address) for c in self.changes]
```

In `make_plan`, replace the writable check with:

```python
        blocked = [r for r in s.registers if not base.driver.is_writable(r)]
        if blocked:
            plan.errors.append(f"{s.label}: register 0x{blocked[0]:02X} is not writable")
            continue
```

Update the module docstring's first line to "Turn target values into a checked, safely ordered list of setting writes."

- [ ] **Step 6: Implement the service** in `src/packkeeper/service.py`.

```python
def describe_register(block: Block, reg: int) -> str:
    s = block.driver.table.covering.get(reg)
    return f"{s.label} = {s.format(block.raw(s.key))}" if s else f"unknown register 0x{reg:02X} = {block.reg(reg)}"
```

In `apply_plan`:
- The guard loop becomes
  ```python
      for change in plan.changes:
          for r in change.setting.registers:
              if not pack.driver.is_writable(r):
                  raise ValueError(
                      f"refusing to apply the plan: 0x{r:02X} is not a writable register "
                      f"on the {pack.driver.label}; nothing was written"
                  )
  ```
- The stale message becomes `what = ", ".join(dict.fromkeys(describe_register(now, r) for r in changed))`.
- `result.acked = pack.write_setting(change.setting, change.new)`.
- `result.reported = block.raw(change.key)`.
- `others = [r for r in register_diff(expected, block) if r not in change.setting.registers]`.
- The unexpected list keeps today's EG4 text exactly. Today it is `f"{describe_register(table, r, block.reg(r))} (expected {expected.reg(r)} raw)"`. Only wide settings get a formatted expected value:

```python
            def expected_text(r: int) -> str:
                s = table.covering.get(r)
                return f"{s.format(expected.raw(s.key))}" if s and s.width > 1 else f"{expected.reg(r)} raw"

            unexpected = list(dict.fromkeys(f"{describe_register(block, r)} (expected {expected_text(r)})" for r in others))
```

In `diff_blocks`, use `unknown = [r for r in register_diff(a, b) if r not in table.covering]`.

- [ ] **Step 7: Pass the session address at the CLI and GUI call sites.**
  - `src/packkeeper/cli.py`: `plan.frames()` becomes `plan.frames(pack.address)`, and `driver.clock_frame(target)` becomes `driver.clock_frame(target, pack.address)`.
  - `src/packkeeper/gui.py`:
    - In `ReviewDialog`, `plan.frames()` becomes `plan.frames(main.pack.address if main.pack else None)`.
    - In `ClockTab.set_clock`, use `self.main.pack.driver.clock_frame(now, self.main.pack.address)`.
    - In the review header, "Writes go out one register at a time" becomes "Writes go out one setting at a time".

- [ ] **Step 8: Update the tests that called the old signatures. These are mechanical changes.**
  - In `tests/test_drivers.py`:
    - `test_dry_run_frames_use_the_driver` uses `EG4_LL.write_frame(EG4_LL.table["cell_overvoltage_warning"], 3650)`.
    - In `test_toy_device_refuses_to_write_a_non_writable_register`, replace `dev.write_setting(0x100, 1)` with `dev.write_setting(dataclasses.replace(TOY.table["low_cut"], register=0x100), 1)`, and add `import dataclasses` to that test.
  - In `tests/test_engine.py`, at the call near line 354 (`pack.write_setting(reg, 1)`), pass a forged setting the same way: `dataclasses.replace(EG4_LL.table["cell_overvoltage_warning"], register=reg)`.
  - Grep `write_setting(\|write_frame(\|describe_register(` across tests and update every call.

- [ ] **Step 9: Run the full suite and lint.** Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add -A src tests
git commit -m "Write one frame per setting and guard every register it covers"
```

---

### Task 4: Session address, write refusal and info lines

**Files:**
- Modify: `src/packkeeper/drivers/base.py`, `src/packkeeper/device.py`, `src/packkeeper/service.py`, `src/packkeeper/cli.py`, `src/packkeeper/gui.py`
- Test: `tests/test_wide.py` (append), `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `Driver.address_choices: range | None = None`.
  - `Driver.check_device(device, block) -> str | None`, which returns None by default.
  - `Driver.info_lines(block) -> list[tuple[str, str]]`, which returns [] by default.
  - `Driver.open_device(transport, log, address: int | None = None)`.
  - `ModbusDevice(driver, transport, log, address=None)`, with `.write_refusal: str | None`.
  - The CLI common option `--address N`.
  - GUI: `MainWindow.address` (a `QSpinBox`) and `MainWindow.address_label`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_wide.py`:

```python
from packkeeper.device import PackError  # noqa: E402
from packkeeper.transport import NoReply  # noqa: E402


class Addressed(type(WIDE)):
    address_choices = range(0, 248)
    refusal = None

    def check_device(self, device, block):
        return self.refusal


def test_session_address_reaches_every_frame():
    d = Addressed()
    sim = d.simulator()
    sim.answer_to = 5
    dev = d.open_device(sim, SessionLog(), address=5)
    block = dev.read_block()
    assert dev.address == 5 and make_plan(block, {"amps": 120000}).frames(dev.address)[0][0] == 5
    with pytest.raises(NoReply):
        d.open_device(sim, SessionLog()).read_block()  # default address 9: no answer


def test_address_outside_the_choices_is_refused():
    with pytest.raises(ValueError, match="address"):
        Addressed().open_device(WIDE.simulator(), SessionLog(), address=300)
    with pytest.raises(ValueError, match="address"):
        WIDE.open_device(WIDE.simulator(), SessionLog(), address=5)  # no choices: fixed at 9


def test_a_write_refusal_stops_every_write_before_any_frame():
    from packkeeper.service import set_clock

    d = Addressed()
    d.refusal = "numbering not confirmed"
    sim = d.simulator()
    dev = d.open_device(sim, SessionLog())
    block = dev.read_block()  # reads still work
    assert dev.write_refusal == "numbering not confirmed"
    with pytest.raises(PackError, match="numbering not confirmed"):
        dev.write_setting(d.table["amps"], 120000)
    with pytest.raises(PackError, match="numbering not confirmed"):
        apply_plan(dev, make_plan(block, {"amps": 120000}))
    assert sim.writes == []
    d.supports_clock = True
    with pytest.raises(PackError, match="numbering not confirmed"):
        set_clock(dev, __import__("datetime").datetime(2026, 1, 1))
    assert any("writes are off" in line for line in dev.log.lines)


def test_cli_address_option(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--simulate", "--address", "5", "--log-dir", str(tmp_path)]) == 2
    assert "--address" in capsys.readouterr().err  # EG4-LL is fixed at address 1
```

Append to `tests/test_gui.py`:

```python
def test_address_box_only_for_drivers_with_choices(tmp_path, monkeypatch):
    from packkeeper import drivers, qtcommon
    from test_wide import Addressed
    d = Addressed()
    d.name = "addressed"
    monkeypatch.setitem(drivers.DRIVERS, "addressed", d)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    try:
        assert w.address.isHidden()  # EG4-LL
        w.bms.setCurrentIndex(w.bms.findData("addressed"))
        assert not w.address.isHidden() and w.address.value() == 9
        d.refusal = "numbering not confirmed"
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
        wait(app, lambda: w.settings.block is not None)
        assert not w.address.isEnabled()
        assert "writes off" in w.identity.text() and "numbering not confirmed" in w.identity.toolTip()
        warned = []
        monkeypatch.setattr(gui.QMessageBox, "warning", lambda *a: warned.append(a[2]))
        w.settings.staged["amps"] = 120000
        w.review()
        assert warned and "numbering not confirmed" in warned[0]
    finally:
        w.close()
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: TypeError (unexpected `address` keyword) or AttributeError (`write_refusal`, `address`).

- [ ] **Step 3: Implement the driver hooks** in `src/packkeeper/drivers/base.py`. Add the attribute `address_choices: range | None = None  # None: always at `address`` after `address`. Add these to the optional methods:

```python
    def check_device(self, device, block: Block) -> str | None:
        """Runs after every block read (reads only). A reason here turns writes off."""
        return None

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        """Read-only values worth showing next to the identity, as (label, text)."""
        return []
```

Change `open_device`:

```python
    def open_device(self, transport, log, address: int | None = None):
        from ..device import ModbusDevice

        return ModbusDevice(self, transport, log, address)
```

- [ ] **Step 4: Implement the device** in `src/packkeeper/device.py`. Add `write_refusal: str | None` to the `Device` protocol.

```python
class ModbusDevice:
    def __init__(self, driver: "Driver", transport: Transport, log: SessionLog, address: int | None = None):
        self.driver = driver
        self.transport = transport
        self.log = log
        if address is None:
            address = driver.address
        elif address != driver.address and (driver.address_choices is None or address not in driver.address_choices):
            raise ValueError(f"the {driver.label} cannot be reached at address {address}")
        self.address = address
        self.write_refusal: str | None = None
```

At the end of `read_block`, before `return block.check_not_blank()`, keep the blank check first, then add:

```python
        block.check_not_blank()
        refusal = d.check_device(self, block)
        if refusal and refusal != self.write_refusal:
            self.log.error(f"writes are off: {refusal}")
        elif self.write_refusal and not refusal:
            self.log.info("writes are allowed again")
        self.write_refusal = refusal
        return block
```

At the top of `write_setting` and `write_clock`:

```python
        if self.write_refusal:
            raise PackError(f"writes are off: {self.write_refusal}")
```

- [ ] **Step 5: Implement the service.** In `apply_plan`, after the driver-mismatch check:

```python
    refusal = getattr(pack, "write_refusal", None)
    if refusal:
        raise PackError(f"writes are off: {refusal}; nothing was written")
```

Add the same check again right after `now = pack.read_block()`, because that read can set it. In `set_clock`, check it after `before = pack.read_block().clock` and before writing.

- [ ] **Step 6: Implement the CLI** in `src/packkeeper/cli.py`.

Add a common option:

```python
    common.add_argument("--address", type=int, metavar="N", help="Modbus address, for BMS types that have one to choose")
```

In `open_pack`, right after `driver = driver_of(args)` (before the log is created, so a bad `--address` writes no log file):

```python
    address = args.address
    if address is not None:
        if driver.address_choices is None:
            raise CliError(f"the {driver.label} always answers at address {driver.address}; --address does not apply")
        if address not in driver.address_choices:
            c = driver.address_choices
            raise CliError(f"--address must be {c.start}..{c.stop - 1} for the {driver.label}")
```

End `open_pack` with:

```python
    if address is not None:
        log.info(f"address {address}")
    return driver.open_device(transport, log, address)
```

In `print_identity`, after the clock line:

```python
    for label, text in block.driver.info_lines(block):
        print(f"{label:9s} {text}")
```

In `run_write`, right after the dry-run `return 0` block and before the pre-write backup:

```python
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}. Nothing was written.")
```

In `cmd_clock`, add the same check after the dry-run return and before the `input(...)` prompt.

- [ ] **Step 7: Implement the GUI** in `src/packkeeper/gui.py`.
  - Import `QSpinBox` with the other widgets.
  - In the connect bar, after `bar.addWidget(self.bms)`:
    ```python
            self.address_label = QLabel("Address:")
            bar.addWidget(self.address_label)
            self.address = QSpinBox()
            self.address.setToolTip("The pack's Modbus address (its DIP switch setting)")
            bar.addWidget(self.address)
    ```
  - In `driver_changed`:
    ```python
            choices = d.address_choices
            self.address_label.setVisible(choices is not None)
            self.address.setVisible(choices is not None)
            if choices is not None:
                self.address.setRange(choices.start, choices.stop - 1)
                self.address.setValue(d.address)
    ```
  - In `set_connected`: `self.address.setEnabled(not on)`.
  - In `toggle_connect`, create the device with `address = self.address.value() if driver.address_choices is not None else None` and `self.pack = driver.open_device(transport, self.log, address)`. When an address is given, log `self.log.info(f"address {address}")`.
  - In `got_block`, after the warnings tag:
    ```python
            refusal = self.pack.write_refusal
            if refusal:
                text += f" · <b style='color:{BAD.name()}'>writes off</b>"
                warnings = [*warnings, f"Writes are off: {refusal}"]
            ...
            self.identity.setToolTip("\n".join(warnings))
    ```
    Then, in place of the final `self.status("Settings read from the pack.")`, use `self.status(f"Writes are off: {refusal}", error=True) if refusal else self.status("Settings read from the pack.")`.
  - In `review`, at the top after the `staged` check:
    ```python
            if self.pack.write_refusal:
                QMessageBox.warning(self, "Writes are off", self.pack.write_refusal)
                return
    ```
  - In `ClockTab.show_block`, enable the button with `self.set_btn.setEnabled(has_clock and not (self.main.pack and self.main.pack.write_refusal))`.
  - Show info lines on the Live tab:
    - In `got_live`, before `self._last_rt = rt`:
      ```python
              block = self.settings.block
              if block is not None:
                  rt.extra = [*rt.extra, *block.driver.info_lines(block)]
      ```
    - In `LiveTab.show_realtime`, build `extra = "".join(f"<br><b>{label}:</b> {text}" for label, text in rt.extra)` and append it to `self.details` text in both branches (`setText(... + extra)`, and `setText(extra[4:])` when there is no capacity line).

- [ ] **Step 8: Run the full suite and lint.** Expected: all pass. EG4 tests are unaffected: no address box, and `write_refusal` is None.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "Per-session address, a driver check that can turn writes off, info lines"
```

---

### Task 5: Backup format version 3

**Files:**
- Modify: `src/packkeeper/backup.py`
- Test: `tests/test_drivers.py` (update the v2 assertions, add v1/v2/v3 tests), `tests/test_wide.py` (append)

**Interfaces:**
- Consumes: `Driver.areas`, `Block.areas`, `Setting.width`.
- Produces: `FORMAT_VERSION = 3` and `READABLE_VERSIONS = (1, 2, 3)`. Backups carry `"areas": [{"start", "stride", "registers"}]`.

- [ ] **Step 1: Write the failing tests.**

In `tests/test_drivers.py`:
- Change `test_backup_records_the_bms_type`'s first assertion to `data["format_version"] == 3 and data["bms"] == "eg4-ll"`, and add `assert data["areas"] == [{"start": 0x2D, "stride": 1, "registers": list(REF.registers)}]`.
- Replace `test_version_1_backup_loads_as_eg4_ll` with:

```python
def _old(version):
    data = backup.to_dict(REF)
    data["format_version"] = version
    del data["areas"]
    data.update(start_register=0x2D, register_count=91, registers=list(REF.registers))
    if version == 1:
        del data["bms"]
    return data


def test_version_1_and_2_backups_still_load():
    assert backup.from_dict(_old(1)).block == REF
    assert backup.from_dict(_old(2)).block == REF


def test_a_backup_area_that_does_not_match_the_driver_is_refused():
    data = backup.to_dict(REF)
    data["areas"][0]["start"] = 0x2E
    with pytest.raises(backup.BackupError, match="0x2D"):
        backup.from_dict(data)
```

Append to `tests/test_wide.py`:

```python
def test_wide_backup_round_trip(tmp_path, monkeypatch):
    import json

    from packkeeper import backup, drivers

    monkeypatch.setitem(drivers.DRIVERS, "wide", WIDE)
    _sim, dev = wide_device()
    b = dev.read_block()
    f = tmp_path / "w.json"
    backup.save(f, b)
    data = json.loads(f.read_text())
    assert data["areas"][0]["stride"] == 2 and data["settings"]["cold"]["raw"] == -50
    assert data["settings"]["amps"]["width"] == 2 and data["settings"]["amps"]["register"] == "0x202"
    assert backup.load(f).block == b
    data["settings"]["cold"]["raw"] = -60
    with pytest.raises(backup.BackupError, match="edited"):
        backup.from_dict(data)
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: KeyError `areas`, or `format_version == 2`.

- [ ] **Step 3: Implement** in `src/packkeeper/backup.py`.

```python
FORMAT_VERSION = 3
READABLE_VERSIONS = (1, 2, 3)  # 1: before drivers, always EG4-LL; 1-2: one area, no "areas"
```

In `to_dict`, replace the `start_register`, `register_count` and `registers` entries with

```python
        "areas": [
            {"start": a.start, "stride": a.stride, "registers": list(values)}
            for a, values in zip(d.areas, block.areas)
        ],
```

Add `"width": s.width,` to each settings entry, after `"register"`. Make `serial_note` appear only when there is a serial number:
`"serial_note": "the same on every pack; cannot identify a pack" if block.serial else None,`.

In `from_dict`, replace the start/count check and the `regs` handling with:

```python
    areas = _areas(data, version, driver, where)
    try:
        block = driver.block(*areas)
        block.check_not_blank()
    except (ValueError, BlankBlockError) as e:
        raise BackupError(f"{where}: {e}") from None
```

Add this helper:

```python
def _ints(regs, where: str) -> list[int]:
    if not isinstance(regs, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in regs):
        raise BackupError(f"{where}: 'registers' must be a list of integers")
    return regs


def _areas(data: dict, version: int, driver, where: str) -> list[list[int]]:
    layout = driver.areas
    if version < 3:
        area = layout[0]
        if len(layout) != 1 or data.get("start_register") != area.start or data.get("register_count") != area.count:
            raise BackupError(f"{where}: not a {driver.label} settings block (0x{area.start:02X} x{area.count})")
        return [_ints(data.get("registers"), where)]
    saved = data.get("areas")
    if not isinstance(saved, list) or len(saved) != len(layout):
        raise BackupError(f"{where}: 'areas' must list the {driver.label}'s {len(layout)} area(s)")
    out = []
    for a, entry in zip(layout, saved):
        if not isinstance(entry, dict) or (entry.get("start"), entry.get("stride")) != (a.start, a.stride):
            raise BackupError(
                f"{where}: not a {driver.label} block (expected an area at 0x{a.start:02X}, stride {a.stride})"
            )
        out.append(_ints(entry.get("registers"), where))
    return out
```

The raw/value consistency check below is unchanged. It already uses `block.raw(key)`, which now decodes wide and signed values.

- [ ] **Step 4: Fix any remaining test that read `data["registers"]` or `start_register`.** Grep with `grep -rn "start_register\|\"registers\"\]" tests` and switch those to `data["areas"][0]["registers"]`.

- [ ] **Step 5: Run the full suite and lint.** Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "Backup format 3: areas with start and stride; versions 1 and 2 still load"
```

---

### Task 6: The JK-PB driver

**Files:**
- Create: `src/packkeeper/drivers/jk_pb/__init__.py`, `registers.py`, `table.py`, `layout.py`, `live.py`, `sim.py`, `driver.py`
- Modify: `src/packkeeper/drivers/__init__.py`
- Create: `tests/test_jk_pb.py`
- Modify: `tests/test_drivers.py` (the boundary test now also covers `jk_pb`), `tests/test_layout.py` (run for every driver)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces:
  - `packkeeper.drivers.jk_pb.DRIVER`, a `JkPB()` instance with `name = "jk-pb"` and `label = "JK-PB"`.
  - `JkPB(stride: int = registers.STRIDE)`, so tests can build the word-addressed variant.
  - `jk_pb.sim.FakeJK(driver, *, stride=None, word_order="high_first", floors=None, blank_thresholds=False, state_file=None)`.
  - `jk_pb.registers.addr(area, offset, stride)`.

- [ ] **Step 1: Write the failing tests** `tests/test_jk_pb.py`:

```python
import json
from datetime import datetime

import pytest

from packkeeper import backup, drivers
from packkeeper.device import PackError
from packkeeper.drivers.jk_pb import DRIVER as JK
from packkeeper.drivers.jk_pb import registers
from packkeeper.drivers.jk_pb.driver import JkPB
from packkeeper.drivers.jk_pb.sim import FakeJK
from packkeeper.planner import make_plan
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan, set_clock
from packkeeper.session import SessionLog


def jk(driver=JK, **kw):
    sim = FakeJK(driver, **kw)
    return sim, driver.open_device(sim, SessionLog())


def tx(dev):
    return [line.split(None, 2)[2] for line in dev.log.lines if line.split(None, 2)[1] == "TX"]


def test_registered_with_30_settings():
    assert drivers.get("jk-pb") is JK and JK.label == "JK-PB"
    assert len(JK.table) == 30
    assert JK.table["cell_ov_protection"].registers == (0x100C, 0x100E)
    assert JK.table["charge_ut_protection"].signed and JK.table["short_circuit_delay"].unit == "µs"
    assert not JK.is_writable(0x106C) and not JK.is_writable(0x1108)  # cell count, device address
    assert not JK.is_writable(registers.addr(registers.COMMANDS, 0x12, 2))


def test_read_decodes_settings_identity_clock_and_info():
    _sim, dev = jk()
    b = dev.read_block()
    assert dev.write_refusal is None
    assert b.raw("cell_ov_protection") == 3650 and b.raw("charge_ut_protection") == 20
    assert b.model == "JK_PB2A16S20P" and b.firmware == "19.08 (hw 19A)" and b.serial == ""
    assert b.clock == datetime(2026, 10, 23, 8, 0, 0)
    assert JK.identity_warnings(b) == []
    info = dict(JK.info_lines(b))
    assert info["Cells"] == "16" and info["Address"] == "1" and "balancer on" in info["Switches"]


def test_live_decode():
    _sim, dev = jk()
    rt = dev.read_live()
    assert rt.cell_count == 16 and rt.cells[0] == 3.300
    assert rt.pack_voltage == 52.83 and rt.current == -3.2 and rt.soc == 76 and rt.soh == 100
    assert ("MOSFET", 31) in rt.temps and rt.max_temp == 25
    assert rt.charge_allowed and rt.discharge_allowed and rt.status_text == "Discharging"
    assert rt.remaining_ah == 152.0 and rt.full_capacity_ah == 200.0 and rt.cycles == 37


def test_plan_orders_and_rules():
    _sim, dev = jk()
    b = dev.read_block()
    p = make_plan(b, {"cell_ov_protection": 3600, "cell_ov_release": 3550})
    assert p.ok, p.errors
    assert [c.key for c in p.changes] == ["cell_ov_release", "cell_ov_protection"]  # moving down: bottom first
    bad = make_plan(b, {"charge_voltage": 3650})  # must stay below OV protection 3.650
    assert not bad.ok and any("Charge voltage" in e for e in bad.errors)
    assert not make_plan(b, {"cell_ov_protection": 3700}).ok  # above 3.650 V


def test_write_confirmed_with_one_frame_at_the_byte_address():
    sim, dev = jk()
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert res.ok and res.results[0].status == CONFIRMED
    assert sim.writes == [(0x100C, [0x0000, 0x0E10])]
    assert any(t.startswith("01 10 10 0C 00 02 04 00 00 0E 10") for t in tx(dev))


def test_dropped_write_is_not_applied():
    _sim, dev = jk()
    res = apply_plan(dev, make_plan(dev.read_block(), {"max_balance_current": 200}))  # 0.2 A, below the sim floor
    assert res.results[0].status == NOT_APPLIED


def test_wrong_numbering_turns_writes_off():
    sim, dev = jk(stride=1)  # the pack addresses by word, this build expects bytes
    dev.read_block()
    assert dev.write_refusal and "0x100C" in dev.write_refusal and "0x1006" in dev.write_refusal
    n = len(sim.writes)
    with pytest.raises(PackError, match="writes are off"):
        dev.write_setting(JK.table["cell_ov_protection"], 3600)
    assert len(sim.writes) == n == 0


def test_word_addressed_build_works_on_a_word_addressed_pack():
    d = JkPB(stride=1)
    assert d.table["cell_ov_protection"].registers == (0x1006, 0x1007)
    sim, dev = jk(d, stride=1)
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert dev.write_refusal is None and res.ok and sim.writes == [(0x1006, [0, 3600])]


def test_wrong_word_order_is_refused():
    from packkeeper.block import BlankBlockError

    _sim, dev = jk(word_order="low_first")
    with pytest.raises(BlankBlockError, match="word order"):
        dev.read_block()


def test_all_zero_settings_are_refused():
    from packkeeper.block import BlankBlockError

    _sim, dev = jk(blank_thresholds=True)
    with pytest.raises(BlankBlockError):
        dev.read_block()


def test_clock_set_and_read_back():
    sim, dev = jk()
    t = datetime(2026, 10, 23, 9, 30, 0)
    res = set_clock(dev, t)
    assert res.ok and res.after is not None
    assert sim.writes[-1][0] == registers.addr(registers.COMMANDS, 0x12, 2)


def test_backup_round_trip_keeps_read_only_registers(tmp_path):
    _sim, dev = jk()
    b = dev.read_block()
    f = tmp_path / "jk.json"
    backup.save(f, b)
    data = json.loads(f.read_text())
    assert data["bms"] == "jk-pb" and [a["start"] for a in data["areas"]] == [0x1000, 0x1400, 0x1300]
    assert backup.load(f).block == b
    assert b.reg(0x106C + 2) == 16  # cell count low word kept


def test_cli_with_address(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--bms", "jk-pb", "--simulate", "--address", "1", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "JK_PB2A16S20P" in out and "Cells     16" in out
    assert main(["set", "--bms", "jk-pb", "--simulate", "--dry-run", "--log-dir", str(tmp_path),
                 "cell_ov_protection", "3.6"]) == 0
    assert "01 10 10 0C 00 02 04 00 00 0E 10" in capsys.readouterr().out
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: `ModuleNotFoundError: packkeeper.drivers.jk_pb`.

- [ ] **Step 3: Create `src/packkeeper/drivers/jk_pb/registers.py`:**

```python
"""JK-PB register map, from JK's RS485 Modbus V1.1 document.

Fields are given there as byte offsets into four areas. Community configs
for the PB2A16S20P (esphome-jk-bms discussion #501) read cell 2 at 0x1202
and pack voltage as a 32-bit value at 0x1290, high word first: the pack
addresses by byte. STRIDE is that one assumption; check_device confirms it
on every connect, reads only.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...block import Area

STRIDE = 2  # address step between 16-bit registers: 2 = byte addresses
WORD_ORDER = "high_first"
SETTINGS, LIVE, DEVICE, COMMANDS = 0x1000, 0x1200, 0x1400, 0x1600
SETTINGS_BYTES, LIVE_BYTES, DEVICE_BYTES = 0x11A, 0x10E, 0x28
OV_OFFSET = 0x0C  # cell over-voltage protection: the field check_device reads alone
CLOCK_OFFSET = 0x100  # RTC ticks in the live area, seconds since 2020-01-01
CLOCK_SET_OFFSET = 0x12  # "time calibration" in the command area
EPOCH = datetime(2020, 1, 1)


def addr(area: int, offset: int, stride: int = STRIDE) -> int:
    """The address of the register holding byte `offset` of `area`."""
    return area + (offset // 2) * stride


def areas(stride: int) -> tuple[Area, ...]:
    return (
        Area(SETTINGS, SETTINGS_BYTES // 2, stride),
        Area(DEVICE, DEVICE_BYTES // 2, stride),
        Area(addr(LIVE, CLOCK_OFFSET, stride), 2, stride),
    )


def live_reads(stride: int) -> tuple[Area, ...]:
    return (Area(LIVE, LIVE_BYTES // 2, stride),)


def pack_clock(t: datetime) -> int:
    secs = int((t - EPOCH).total_seconds())
    if not 0 <= secs <= 0xFFFFFFFF:
        raise ValueError(f"{t} does not fit the BMS clock (seconds since {EPOCH:%Y-%m-%d})")
    return secs


def unpack_clock(secs: int) -> datetime:
    return EPOCH + timedelta(seconds=secs)


BLANK_MESSAGE = "the JK-PB answered with every setting zero; refusing to use this read"

CONNECTION_HELP = """\
The pack answers on RS485 when it is the only one on the line: unplug
the link cables between packs. Also check:
  - the adapter is USB-RS485 and plugged into one of the pack's RS485 ports,
  - A and B are not swapped (if nothing answers, swap them),
  - the address: the pack answers at its DIP-switch address; try 1, then 0,
  - the right port is selected (115200 8N1)."""

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables between packs, then plug the USB-RS485 adapter "
    "into one of the pack's RS485 ports. Set <b>Address</b> to the pack's DIP-switch address."
)
NO_REPLY_HINT = "Check the link cables are unplugged, A/B wiring, and the address (try 1, then 0)."
SETUP_HINT = "link cables unplugged, adapter in one of its RS485 ports"
CLOCK_NOTE = (
    "The BMS clock counts seconds since 2020-01-01 (local time). It is set with JK's time-calibration "
    "command and read back to confirm."
)
```

- [ ] **Step 4: Create `src/packkeeper/drivers/jk_pb/table.py`:**

```python
"""JK-PB settings: 30 writable settings from the settings area, 16S LiFePO4 ranges.

The ranges are this tool's, not JK's: compare them with the pack and JK's
app before the first write.
"""

from __future__ import annotations

from ...settings import SettingTable
from .registers import SETTINGS, WORD_ORDER, addr

V = ("V", 0.001, 3)
A = ("A", 0.001, 3)
S = ("s", 1, 0)
C = ("°C", 0.1, 1)
US = ("µs", 1, 0)
AH = ("Ah", 0.001, 3)

# key: (byte offset, (unit, multiplier, decimals), signed, (min, max, step), group, label)
FIELDS = {
    "smart_sleep_voltage": (0x00, V, False, (2.5, 3.4, 0.001), "Shutdown", "Smart sleep voltage"),
    "cell_uv_protection": (0x04, V, False, (2.5, 3.1, 0.001), "Cell voltage", "Cell UV protection"),
    "cell_uv_release": (0x08, V, False, (2.6, 3.2, 0.001), "Cell voltage", "Cell UV release"),
    "cell_ov_protection": (0x0C, V, False, (3.45, 3.65, 0.001), "Cell voltage", "Cell OV protection"),
    "cell_ov_release": (0x10, V, False, (3.3, 3.6, 0.001), "Cell voltage", "Cell OV release"),
    "balance_trigger_delta": (0x14, V, False, (0.005, 0.1, 0.001), "Balancing", "Balance trigger delta"),
    "soc_100_voltage": (0x18, V, False, (3.3, 3.6, 0.001), "Charging", "SOC 100% voltage"),
    "soc_0_voltage": (0x1C, V, False, (2.5, 3.2, 0.001), "Charging", "SOC 0% voltage"),
    "charge_voltage": (0x20, V, False, (3.3, 3.65, 0.001), "Charging", "Charge voltage"),
    "float_voltage": (0x24, V, False, (3.2, 3.5, 0.001), "Charging", "Float voltage"),
    "power_off_voltage": (0x28, V, False, (2.5, 3.0, 0.001), "Shutdown", "Power-off voltage"),
    "charge_oc_current": (0x2C, A, False, (1, 200, 0.001), "Charge current", "Charge OC current"),
    "charge_oc_delay": (0x30, S, False, (1, 600, 1), "Charge current", "Charge OC delay"),
    "charge_oc_release_time": (0x34, S, False, (1, 600, 1), "Charge current", "Charge OC release time"),
    "discharge_oc_current": (0x38, A, False, (1, 200, 0.001), "Discharge current", "Discharge OC current"),
    "discharge_oc_delay": (0x3C, S, False, (1, 600, 1), "Discharge current", "Discharge OC delay"),
    "discharge_oc_release_time": (0x40, S, False, (1, 600, 1), "Discharge current", "Discharge OC release time"),
    "short_circuit_release_time": (0x44, S, False, (1, 600, 1), "Discharge current", "Short circuit release time"),
    "max_balance_current": (0x48, A, False, (0.1, 2.0, 0.001), "Balancing", "Max balance current"),
    "charge_ot_protection": (0x4C, C, True, (30, 70, 0.1), "Temperature", "Charge OT protection"),
    "charge_ot_release": (0x50, C, True, (25, 65, 0.1), "Temperature", "Charge OT release"),
    "discharge_ot_protection": (0x54, C, True, (40, 80, 0.1), "Temperature", "Discharge OT protection"),
    "discharge_ot_release": (0x58, C, True, (35, 75, 0.1), "Temperature", "Discharge OT release"),
    "charge_ut_protection": (0x5C, C, True, (-10, 10, 0.1), "Temperature", "Charge UT protection"),
    "charge_ut_release": (0x60, C, True, (-5, 15, 0.1), "Temperature", "Charge UT release"),
    "mos_ot_protection": (0x64, C, True, (60, 100, 0.1), "Temperature", "MOSFET OT protection"),
    "mos_ot_release": (0x68, C, True, (50, 90, 0.1), "Temperature", "MOSFET OT release"),
    "design_capacity": (0x7C, AH, False, (1, 1000, 0.001), "Balancing", "Design capacity"),
    "short_circuit_delay": (0x80, US, False, (50, 1500, 1), "Discharge current", "Short circuit delay"),
    "balance_start_voltage": (0x84, V, False, (3.0, 3.5, 0.001), "Balancing", "Balance start voltage"),
}

ORDER = [
    ("power_off_voltage", "cell_uv_protection", True),
    ("cell_uv_protection", "cell_uv_release", True),
    ("cell_ov_release", "cell_ov_protection", True),
    ("float_voltage", "charge_voltage", False),
    ("charge_voltage", "cell_ov_protection", True),
    ("balance_start_voltage", "cell_ov_protection", True),
    ("charge_ot_release", "charge_ot_protection", True),
    ("discharge_ot_release", "discharge_ot_protection", True),
    ("mos_ot_release", "mos_ot_protection", True),
    ("charge_ut_protection", "charge_ut_release", True),
]


def build_table(stride: int) -> SettingTable:
    spec = {
        key: (addr(SETTINGS, off, stride), mult, 0, unit, None, decimals, 2, signed)
        for key, (off, (unit, mult, decimals), signed, _lim, _g, _l) in FIELDS.items()
    }
    limits = {key: f[3] for key, f in FIELDS.items()}
    return SettingTable.build(
        spec,
        limits,
        ORDER,
        group=lambda k: FIELDS[k][4],
        label=lambda k: FIELDS[k][5],
        stride=stride,
        word_order=WORD_ORDER,
    )
```

- [ ] **Step 5: Create `src/packkeeper/drivers/jk_pb/layout.py`:**

```python
"""How the JK-PB settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, Page, Section

PR = ("Protection", "Release")

PAGES = (
    Page(
        "Voltage",
        (
            Section(
                "Cell protection",
                PR,
                (
                    ("Under-voltage", ("cell_uv_protection", "cell_uv_release")),
                    ("Over-voltage", ("cell_ov_protection", "cell_ov_release")),
                ),
                unit="V",
            ),
            Section(
                "Charging",
                ("Voltage",),
                (
                    ("Charge", ("charge_voltage",)),
                    ("Float", ("float_voltage",)),
                    ("SOC 100%", ("soc_100_voltage",)),
                    ("SOC 0%", ("soc_0_voltage",)),
                ),
                unit="V",
            ),
            Section(
                "Shutdown",
                ("Voltage",),
                (("Power off", ("power_off_voltage",)), ("Smart sleep", ("smart_sleep_voltage",))),
                unit="V",
            ),
        ),
        "Power-off < under-voltage protection < release; over-voltage release < protection; "
        "float ≤ charge voltage < over-voltage protection.",
    ),
    Page(
        "Current",
        (
            Section(
                "Over-current",
                ("Trip at (A)", "After (s)", "Release (s)"),
                (
                    ("Charge", ("charge_oc_current", "charge_oc_delay", "charge_oc_release_time")),
                    ("Discharge", ("discharge_oc_current", "discharge_oc_delay", "discharge_oc_release_time")),
                ),
            ),
            Section(
                "Short circuit",
                ("After (µs)", "Release (s)"),
                (("Short circuit", ("short_circuit_delay", "short_circuit_release_time")),),
            ),
        ),
    ),
    Page(
        "Temperature",
        (
            Section(
                "Temperature",
                PR,
                (
                    ("Charge hot", ("charge_ot_protection", "charge_ot_release")),
                    ("Discharge hot", ("discharge_ot_protection", "discharge_ot_release")),
                    ("Charge cold", ("charge_ut_protection", "charge_ut_release")),
                    ("MOSFET hot", ("mos_ot_protection", "mos_ot_release")),
                ),
                unit=TEMP,
            ),
        ),
        "Each release sits on the safe side of its protection value.",
    ),
    Page(
        "Balancing & capacity",
        (
            Section(
                "Balancing",
                ("Value",),
                (
                    ("Trigger delta (V)", ("balance_trigger_delta",)),
                    ("Start voltage (V)", ("balance_start_voltage",)),
                    ("Max current (A)", ("max_balance_current",)),
                ),
            ),
            Section("Capacity", ("Value",), (("Design (Ah)", ("design_capacity",)),)),
        ),
    ),
)
```

- [ ] **Step 6: Create `src/packkeeper/drivers/jk_pb/live.py`:**

```python
"""Decoding the JK-PB live area (0x1200, bytes 0x000-0x10D)."""

from __future__ import annotations

from ...live import Live, decode_bits

PROTECTIONS = {
    1 << 1: "MOSFET OT", 1 << 4: "cell OV", 1 << 5: "pack OV", 1 << 6: "charge OC", 1 << 7: "charge short",
    1 << 8: "charge OT", 1 << 9: "charge UT", 1 << 11: "cell UV", 1 << 12: "pack UV", 1 << 13: "discharge OC",
    1 << 14: "discharge short", 1 << 15: "discharge OT",
}
ERRORS = {
    1 << 0: "balance wire resistance", 1 << 2: "cell count mismatch", 1 << 3: "current sensor",
    1 << 10: "internal communication", 1 << 16: "charge MOSFET", 1 << 17: "discharge MOSFET",
    1 << 22: "temperature sensor", 1 << 23: "parallel module",
}
WARNINGS = {
    1 << 18: "GPS disconnected", 1 << 19: "change the password", 1 << 20: "discharge on failed",
    1 << 21: "battery over-temperature",
}


def decode(regs: list[int]) -> Live:
    def u16(off: int) -> int:
        return regs[off // 2]

    def s16(off: int) -> int:
        v = u16(off)
        return v - 0x10000 if v & 0x8000 else v

    def u32(off: int) -> int:
        return u16(off) << 16 | u16(off + 2)

    def s32(off: int) -> int:
        v = u32(off)
        return v - (1 << 32) if v & (1 << 31) else v

    present = u32(0x40)
    n = present.bit_length()
    cells = [u16(2 * i) / 1000 for i in range(min(n, 32))]
    current = s32(0x98) / 1000
    sensors = u16(0xD0) >> 8  # bit 0 MOSFET, bits 1-5 battery sensors 1-5
    temps = [("MOSFET", round(s16(0x8A) / 10))]
    for i, off in enumerate((0x9C, 0x9E, 0xF8, 0xFA, 0xFC), 1):
        if sensors & (1 << i):
            temps.append((f"sensor {i}", round(s16(off) / 10)))
    battery = [t for label, t in temps if label != "MOSFET"]
    alarms = u32(0xA0)
    balance = {0: "off", 1: "charging", 2: "discharging"}.get(u16(0xA6) >> 8, "?")
    status = "Charging" if current > 0.05 else "Discharging" if current < -0.05 else "Idle"
    return Live(
        pack_voltage=u32(0x90) / 1000,
        current=current,
        soc=u16(0xA6) & 0xFF,
        soh=u16(0xB8) >> 8,
        cells=cells,
        status_text=status,
        temps=temps,
        max_temp=max(battery) if battery else None,
        warnings=decode_bits(alarms & sum(WARNINGS), WARNINGS),
        protections=decode_bits(alarms & sum(PROTECTIONS), PROTECTIONS),
        errors=decode_bits(alarms & ~(sum(WARNINGS) | sum(PROTECTIONS)), ERRORS),
        charge_allowed=bool(u16(0xC0) >> 8),
        discharge_allowed=bool(u16(0xC0) & 0xFF),
        remaining_ah=s32(0xA8) / 1000,
        full_capacity_ah=u32(0xAC) / 1000,
        cycles=u32(0xB0),
        extra=[("Balancer", f"{balance}, {s16(0xA4) / 1000:.3f} A")],
        registers=list(regs),
    )
```

`decode_bits` reports unknown bits as `unknown 0x....`, formatted with 4 hex digits. That's acceptable for a 32-bit value.

- [ ] **Step 7: Create `src/packkeeper/drivers/jk_pb/sim.py`:**

```python
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
    "cell_ov_protection": 3650, "cell_ov_release": 3600, "balance_trigger_delta": 10,
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
```

`self._writable` is set before `super().__init__`, so the `is_writable` lambda always has it.

- [ ] **Step 8: Create `src/packkeeper/drivers/jk_pb/driver.py`:**

```python
"""The JK-PB driver: JK PB-series inverter BMS (PB2A16S20P), RS485 Modbus RTU
at 115200 8N1, one pack at a time, address from its DIP switches."""

from __future__ import annotations

from pathlib import Path

from ...block import Block, Identity, ascii_text
from ...errors import DeviceRefused
from ...modbus import ModbusError
from ...transport import NoReply, SerialParams
from ..base import Driver
from . import live, registers as R
from .layout import PAGES
from .table import build_table

FEATURES = {
    1 << 0: "heating", 1 << 1: "temperature sensor disabled", 1 << 2: "GPS heartbeat", 1 << 3: "port RS485",
    1 << 4: "LCD always on", 1 << 5: "special charger", 1 << 6: "smart sleep", 1 << 7: "parallel limit off",
    1 << 8: "timed data storage", 1 << 9: "charging float mode",
}


class JkPB(Driver):
    name = "jk-pb"
    label = "JK-PB"
    serial = SerialParams(baud=115200)
    address = 1
    address_choices = range(0, 248)
    pages = PAGES
    supports_clock = True
    connect_note_html = R.CONNECT_NOTE_HTML
    connection_help = R.CONNECTION_HELP
    no_reply_hint = R.NO_REPLY_HINT
    setup_hint = R.SETUP_HINT
    clock_note = R.CLOCK_NOTE

    def __init__(self, stride: int = R.STRIDE):
        self.stride = stride
        self.table = build_table(stride)
        self.areas = R.areas(stride)
        self.live_reads = R.live_reads(stride)
        self._writable = frozenset(self.table.covering)

    def _a(self, area: int, offset: int) -> int:
        return R.addr(area, offset, self.stride)

    def _u32(self, block: Block, area: int, offset: int) -> int:
        hi, lo = block.reg(self._a(area, offset)), block.reg(self._a(area, offset + 2))
        return hi << 16 | lo if R.WORD_ORDER == "high_first" else lo << 16 | hi

    def is_writable(self, register: int) -> bool:
        return register in self._writable

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    def identity(self, block: Block) -> Identity:
        secs = self._u32(block, R.LIVE, R.CLOCK_OFFSET)
        hw = ascii_text(block.regs(self._a(R.DEVICE, 0x10), 4))
        sw = ascii_text(block.regs(self._a(R.DEVICE, 0x18), 4))
        return Identity(
            model=ascii_text(block.regs(self._a(R.DEVICE, 0x00), 8)),
            firmware=f"{sw} (hw {hw})",
            clock=R.unpack_clock(secs),
            clock_raw=secs,
        )

    def blank_reason(self, block: Block) -> str | None:
        if not any(block.areas[0]):
            return R.BLANK_MESSAGE
        s = self.table["cell_ov_protection"]
        ov = block.raw(s.key)
        if 2000 <= ov <= 4500:
            return None
        swapped = s.decode([block.reg(r) for r in reversed(s.registers)])
        if 2000 <= swapped <= 4500:
            return (
                f"cell over-voltage reads as {ov} mV, but {swapped} mV with its two registers swapped: "
                "the 32-bit word order looks wrong. Refusing to use this read."
            )
        return (
            f"cell over-voltage reads as {ov} mV, outside 2000-4500 mV: this does not look like a "
            f"{self.label} settings block. Refusing to use this read."
        )

    def check_device(self, device, block: Block) -> str | None:
        s = self.table["cell_ov_protection"]
        want = [block.reg(r) for r in s.registers]
        other = R.SETTINGS + (R.OV_OFFSET if self.stride == 1 else R.OV_OFFSET // 2)

        def probe(address: int) -> list[int] | None:
            try:
                return device.read(address, 2, retries=1)
            except (DeviceRefused, NoReply, ModbusError):
                return None

        mine, theirs = probe(s.register), probe(other)
        if mine == want and theirs != want:
            return None

        def show(v: list[int] | None) -> str:
            return "no answer" if v is None else " ".join(f"{w:04X}" for w in v)

        expect = "byte" if self.stride == 2 else "word"
        return (
            f"the register numbering could not be confirmed: cell over-voltage read alone at 0x{s.register:04X} "
            f"gave {show(mine)}, at 0x{other:04X} gave {show(theirs)}, and the full read has {show(want)}. "
            f"This build expects {expect} addressing"
        )

    def identity_warnings(self, block: Block) -> list[str]:
        model = block.model
        if model.startswith(("JK_PB", "JK-PB")):
            return []
        return [f"the model text '{model}' is not the JK_PB... of a PB-series BMS; is the right BMS type selected?"]

    def clock_write(self, t):
        secs = R.pack_clock(t)
        return self._a(R.COMMANDS, R.CLOCK_SET_OFFSET), [secs >> 16, secs & 0xFFFF]

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        def u32(off: int) -> int:
            return self._u32(block, R.SETTINGS, off)

        def on(v: int) -> str:
            return "on" if v else "off"

        bits = block.reg(self._a(R.SETTINGS, 0x114))
        features = [name for bit, name in FEATURES.items() if bits & bit]
        return [
            ("Cells", str(u32(0x6C))),
            ("Switches", f"charge {on(u32(0x70))}, discharge {on(u32(0x74))}, balancer {on(u32(0x78))}"),
            ("Address", str(u32(0x108))),
            ("Pre-charge", f"{u32(0x10C)} s"),
            ("Features", ", ".join(features) or "none"),
            ("Sleep time", f"{block.reg(self._a(R.SETTINGS, 0x118)) >> 8} h"),
        ]

    def simulator(self, state_file: Path | None = None):
        from . import sim

        return sim.FakeJK(self, state_file=state_file)
```

`clock_write` sends high word first, which matches `WORD_ORDER`. Because `WORD_ORDER` is a constant, if it ever changes, `clock_write` must change with it. Add a one-line comment saying so.

- [ ] **Step 9: Create `src/packkeeper/drivers/jk_pb/__init__.py`:**

```python
from .driver import JkPB

DRIVER = JkPB()
```

Register it in `src/packkeeper/drivers/__init__.py`:

```python
from .eg4_ll import DRIVER as _EG4_LL
from .jk_pb import DRIVER as _JK_PB

DEFAULT = "eg4-ll"
DRIVERS: dict[str, Driver] = {d.name: d for d in (_EG4_LL, _JK_PB)}
```

- [ ] **Step 10: Extend the shared tests.**
  - `tests/test_drivers.py` `test_generic_code_never_imports_the_eg4_driver`: change the condition to `if any(name in n for name in ("eg4_ll", "jk_pb") for n in names)`.
  - `tests/test_layout.py`: parametrize the first two tests over `drivers.DRIVERS.values()`. Import `from packkeeper import drivers`, use `@pytest.mark.parametrize("d", list(drivers.DRIVERS.values()), ids=lambda d: d.name)`, and use `d.ordered_keys`, `d.table` and `d.pages` in place of the module constants. Keep `test_matrix_rows_share_a_scale` EG4-only, as it is.

- [ ] **Step 11: Run the JK tests, then the full suite and lint**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_jk_pb.py -q`, then the full suite, then lint.
Expected: all pass. If `test_live_decode`'s `("MOSFET", 31)` fails on rounding, the sim value 312 rounds to 31; keep it.

- [ ] **Step 12: Commit**

```bash
git add -A src tests
git commit -m "JK-PB driver: settings, identity, clock, live, checks and simulator"
```

---

### Task 7: JK in the GUI, README, EG4 parity, CI

**Files:**
- Modify: `tests/test_gui.py` (append), `README.md`, `docs/superpowers/specs/2026-10-23-jk-pb-driver-design.md` (status line)
- Create (scratch, not committed): `/private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity.py`

- [ ] **Step 1: Write the GUI test.** Append to `tests/test_gui.py`:

```python
def test_gui_runs_jk_pb(window, monkeypatch):
    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    assert not w.address.isHidden() and w.address.value() == 1
    assert w.tabs.isTabVisible(w.tabs.indexOf(w.clock))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    assert w.identity.text().startswith("<b>JK_PB2A16S20P</b>") and "writes off" not in w.identity.text()
    assert "Cells:</b> 16" in w.live.details.text()
    w.tabs.setCurrentWidget(w.settings)
    for i in range(w.settings.nav.count()):
        w.settings.nav.setCurrentRow(i)
        app.processEvents()
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    assert [c.key for c in dlg.plan.changes] == ["cell_ov_protection"]
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.ok and dlg.result.results[0].status == CONFIRMED
```

- [ ] **Step 2: Run it**

Run: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/test_gui.py -q -k jk`
Expected: PASS. If it fails, fix the cause in the source, not the test.

- [ ] **Step 3: README.** Add a section "JK-PB (PB2A16S20P)" after "Adding a BMS", using this text:

```markdown
## JK-PB (PB2A16S20P)

`--bms jk-pb` (or **BMS: JK-PB** in the app) talks to a JK PB-series inverter
BMS over RS485 with a USB-RS485 adapter, one pack at a time with the link
cables unplugged. Pick the pack's DIP-switch address with `--address N`
(or the **Address** box); a pack that does not answer at 1 may answer at 0.

The register map is JK's RS485 Modbus V1.1 document. The settings ranges are
this tool's proposal for 16S LiFePO4, not JK's limits: compare them with JK's
app before the first write. On every connect the tool reads cell
over-voltage at two candidate addresses to confirm how the pack numbers its
registers; if that does not come out cleanly, writes and the clock are turned
off for that session and the reason is shown.

Never written: cell count, the charge/discharge/balance switches, the device
address, calibration, shutdown, emergency start and the one-key presets.
```

Add `--address` to the "Common options" list: "`--address N` picks the Modbus address, for BMS types that have one to choose (JK-PB)." Also fix the stale `settings.py` link for `LIMIT_OVERRIDES`. It should point to `src/packkeeper/drivers/eg4_ll/table.py`.

- [ ] **Step 4: Write and run the EG4 parity script** at `/private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity.py`:

```python
"""Run the same simulated EG4-LL CLI sessions on main and on this branch and
compare exit codes, normalised output, TX frames and final sim state."""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path("/Users/rar/eg4_tool")
PY = REPO / ".venv/bin/python"
SCENARIOS = [
    (["info"], ""),
    (["backup", "{d}/b.json"], ""),
    (["diff", "--profile", "eg4-shipped"], ""),
    (["diff", "{d}/b.json"], ""),
    (["set", "-n", "cell_overvoltage_warning", "3.60"], ""),
    (["set", "cell_overvoltage_warning", "3.60"], "yes\n"),
    (["set", "cell_overvoltage_protection", "3.70", "cell_overvoltage_release", "3.45"], "yes\n"),
    (["set", "cell_overvoltage_warning", "3.95"], ""),
    (["set", "short_circuit_current", "250"], "yes\n"),
    (["set", "balance_delta_voltage", "0.010"], "yes\n"),
    (["restore", "-n", "--profile", "eg4-shipped"], ""),
    (["restore", "{d}/b.json"], "yes\n"),
    (["clock", "--set", "2026-01-02 03:04:05", "-n"], ""),
    (["clock", "--set", "2026-01-02 03:04:05"], "yes\n"),
    (["keys"], ""),
    (["keys", "--profiles"], ""),
    (["live", "--once"], ""),
]


def run(tree: Path, module: str, work: Path) -> list[dict]:
    out = []
    state = work / "state.json"
    for i, (args, stdin) in enumerate(SCENARIOS):
        logs = work / f"logs{i}"
        logs.mkdir()
        argv = [a.replace("{d}", str(work)) for a in args]
        sim = [] if argv[0] == "keys" else ["--simulate", "--sim-state", str(state), "--log-dir", str(logs)]
        p = subprocess.run([str(PY), "-m", module, *argv, *sim], input=stdin, capture_output=True, text=True,
                           env={"PYTHONPATH": str(tree / "src"), "PATH": "/usr/bin:/bin"})
        text = (p.stdout + p.stderr).replace(str(work), "<work>").replace("eg4tool", "packkeeper")
        text = "\n".join(l for l in text.splitlines() if not l.startswith(("Port:", "This computer", "The pack's current settings were saved")))
        tx = []
        for log in logs.glob("*.log"):
            tx += [l.split(None, 2)[2] for l in log.read_text().splitlines() if len(l.split(None, 2)) == 3 and l.split(None, 2)[1] == "TX"]
        out.append({"args": args, "code": p.returncode, "text": text, "tx": tx,
                    "state": json.loads(state.read_text()) if state.exists() else None})
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(tmp / "main"), "main"], check=True,
                       capture_output=True)
        try:
            (tmp / "a").mkdir()
            (tmp / "b").mkdir()
            a = run(tmp / "main", "eg4tool", tmp / "a")
            b = run(REPO, "packkeeper", tmp / "b")
        finally:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tmp / "main")], check=True)
    bad = 0
    for x, y in zip(a, b):
        for k in ("code", "text", "tx", "state"):
            if x[k] != y[k]:
                bad += 1
                print(f"DIFF {' '.join(x['args'])} [{k}]\n  main: {x[k]!r}\n  here: {y[k]!r}")
    print(f"{len(SCENARIOS)} scenarios, {bad} difference(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `.venv/bin/python /private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity.py`
Expected: `17 scenarios, 0 difference(s)`, or only differences explained by the allowed list in Global Constraints. The backup files themselves aren't compared here. Separately confirm that `b.json` from each tree differs only in `format_version`, `areas`/`start_register`/`register_count`/`registers`, `settings.*.width`, `created` and `tool_version`. Each real difference is either fixed, or reported in the task report with its cause.

Session-log lines are `<iso time> <kind padded to 6> <text>`, so splitting on whitespace into three parts gives the kind and the frame.

- [ ] **Step 5: Spec status.** Change the spec's status line to `Status: implemented on branch jk-pb.`

- [ ] **Step 6: Full suite, lint, commit, push and CI**

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
uvx ruff check --select F,E9,B --ignore B905 src tests packaging
git add -A src tests README.md docs
git commit -m "JK-PB in the app and README; spec marked implemented"
git push -u origin jk-pb
gh workflow run build --ref jk-pb --repo RAR/packkeeper
```

Watch the run with `gh run watch <id> --repo RAR/packkeeper --exit-status`. All four platforms must pass.
