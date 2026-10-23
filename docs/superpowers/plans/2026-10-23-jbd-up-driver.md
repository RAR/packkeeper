# ECO-WORTHY / JBD-UP Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an ECO-WORTHY / JBD-UP driver (48 V rack, 16S LiFePO4, 100 A BMS, RS485, custom 0x78/0x79 framing). To support it, add three things to the core: a pluggable framing layer, an "unsafe settings" tier, and settings with named choices and bits, plus a read-only Find scan.

**Architecture:**
- **Framing:** a `Framing` object, kept per device, builds and checks every frame. `ModbusFraming` wraps today's `modbus.py`, and `JbdFraming` adds JBD's framing and write key byte.
- **Setting fields:** `Setting` gains `unsafe`, `choices` and `bits`.
- **Planner and CLI:** they gate unsafe changes behind an explicit switch and a typed confirmation.
- **The driver:** a package built from data tables, with a simulator that speaks the real framing.

**Tech Stack:** Python 3.11+, PySide6, pyserial, pytest. Lint with `uvx ruff check --select F,E9,B --ignore B905 src tests packaging`.

**Spec:** `docs/superpowers/specs/2026-10-23-jbd-up-driver-design.md`

## Global Constraints

- Work only in the worktree `/Users/rar/.config/superpowers/worktrees/eg4_tool/jbd-up` (branch `jbd-up`). Never touch `/Users/rar/eg4_tool`, which must stay on `main`.
- Tests: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen /Users/rar/eg4_tool/.venv/bin/pytest -q` (about 41 s, 143 tests at the start). They must pass at the end of every task. Lint must be clean.
- `src/packkeeper/drivers/eg4_ll/settings_table.py` stays byte-for-byte unchanged.
- No module outside `src/packkeeper/drivers/` imports `eg4_ll`, `jk_pb` or `jbd_up`.
- EG4-LL and JK-PB behaviour must not change: frames, write order, refusals, exit codes, sim state and CLI text. Every existing test keeps its assertions. The only allowed change to an existing test is a mechanical rename named in a task.
- JBD sources:
  - the user's notes, `/Users/rar/eco-worthy-app/JBD-ES-UP-V1.22.35/PROTOCOL.md`;
  - syssi/esphome-jbd-bms `jbd_bms_up_pack`;
  - the PhracturedBlue ECO-WORTHY gist.
  Simulator values are invented test data, never JBD defaults.
- Never in this tool:
  - restore defaults (0x290C) and factory reset (0x290E);
  - clear history (0x2910);
  - calibration (0x2914, 0x2100–0x2126);
  - writing the text strings;
  - the live controls, and writing RS485 baud (0x1C82) or the BMS address (0x1C88). These two items are piece 2.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq
  ```
- Match the surrounding code: short docstrings, sparse comments, the same naming.

## File structure

| File | Change |
|---|---|
| `src/packkeeper/errors.py` | `FrameError` base |
| `src/packkeeper/modbus.py` | `ModbusError(FrameError)`, `MAX_READ` |
| `src/packkeeper/framing.py` | new: `Framing` protocol, `ModbusFraming`, `MODBUS` |
| `src/packkeeper/transport.py` | `SerialTransport(..., framing=MODBUS)`; `resync` delegates to the framing |
| `src/packkeeper/device.py` | per-device `framing`; generic error classes; area length check |
| `src/packkeeper/drivers/base.py` | `framing`, `new_framing()`, `broadcast_address`, `find_addresses`, `find_probe`, `serial_is_unique`; frames take a framing |
| `src/packkeeper/settings.py` | `Setting.unsafe/choices/bits`; `build(..., unsafe=, choices=, bits=)`; `SettingTable.unsafe_keys` |
| `src/packkeeper/planner.py` | `allow_unsafe`; `Plan.unsafe`; serial warning |
| `src/packkeeper/cli.py` | `--unsafe`, typed `unsafe` confirmation, `keys` marks, `find`, framing to the transport |
| `src/packkeeper/layout.py` | `Page.unsafe` |
| `src/packkeeper/settings_view.py`, `gui.py` | unsafe checkbox/page/review, choice dropdown, Find button, framing to the transport |
| `src/packkeeper/find.py` | new: `Found`, `scan` |
| `src/packkeeper/drivers/jbd_up/` | new: `__init__`, `framing`, `registers`, `table`, `layout`, `live`, `sim`, `driver` |
| `tests/test_framing.py`, `tests/test_unsafe.py`, `tests/unsafe_toy_driver.py`, `tests/test_find.py`, `tests/test_jbd_up.py` | new |

---

### Task 1: Pluggable framing

**Files:**
- Create: `src/packkeeper/framing.py`, `tests/test_framing.py`
- Modify: `src/packkeeper/errors.py`, `src/packkeeper/modbus.py`, `src/packkeeper/transport.py`, `src/packkeeper/device.py`, `src/packkeeper/drivers/base.py`, `src/packkeeper/planner.py`, `src/packkeeper/cli.py`, `src/packkeeper/gui.py`

**Interfaces:**
- Produces:
  - `errors.FrameError`, `modbus.MAX_READ = 125`.
  - `framing.Framing` (Protocol), `framing.ModbusFraming`, `framing.MODBUS`.
  - `ModbusDevice.framing`, and `ModbusDevice.read(start, count, retries)`, which now uses the framing.
  - `Driver.framing`, `Driver.new_framing()` and `Driver.broadcast_address: int | None = 0`.
  - `Driver.write_frame(setting, raw, address=None, framing=None)` and `Driver.clock_frame(t, address=None, framing=None)`.
  - `Plan.frames(address=None, framing=None)`.
  - `SerialTransport(port, timeout=..., params=..., framing=MODBUS)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_framing.py`:

```python
import pytest

from packkeeper import modbus
from packkeeper.errors import FrameError
from packkeeper.framing import MODBUS, ModbusFraming
from packkeeper.session import SessionLog


def test_modbus_framing_builds_todays_frames():
    assert MODBUS.read_request(1, 0x2D, 0x5B) == modbus.read_request(1, 0x2D, 0x5B)
    assert MODBUS.write_request(1, 0x43, [3650]) == modbus.write_request(1, 0x43, [3650])
    assert MODBUS.max_read == modbus.MAX_READ == 125 and MODBUS.head_len == 3


def test_modbus_framing_parses_and_measures_replies():
    req = MODBUS.read_request(1, 0x2D, 2)
    reply = modbus.with_crc(bytes([1, 3, 4, 0x0E, 0x42, 0x00, 0x01]))
    assert MODBUS.reply_length(reply[:3]) == len(reply)
    assert MODBUS.parse_read(req, reply) == [0x0E42, 0x0001]
    with pytest.raises(FrameError):
        MODBUS.parse_read(req, reply[:-1] + b"\x00")


def test_modbus_error_is_a_frame_error():
    assert issubclass(modbus.ModbusError, FrameError)


def test_resync_uses_the_framing():
    from packkeeper.transport import resync

    good = modbus.with_crc(bytes([1, 3, 2, 0, 5]))
    req = MODBUS.read_request(1, 0, 1)
    assert MODBUS.resync(b"\x00\xff" + good, req) == (good, b"\x00\xff")
    assert resync(b"\x00\xff" + good, 1, 3) == (good, b"\x00\xff")  # the old helper still works


def test_each_device_gets_its_own_framing():
    from toy_driver import TOY

    class Counting(type(TOY)):
        made = 0

        def new_framing(self):
            Counting.made += 1
            return ModbusFraming()

    d = Counting()
    a = d.open_device(d.simulator(), SessionLog())
    b = d.open_device(d.simulator(), SessionLog())
    assert a.framing is not b.framing and Counting.made == 2
    a.read_block()  # still reads through the framing


def test_plan_frames_use_the_given_framing():
    from packkeeper.planner import make_plan
    from toy_driver import TOY

    class Tagged(ModbusFraming):
        def write_request(self, address, start, values):
            return b"T" + super().write_request(address, start, values)

    dev = TOY.open_device(TOY.simulator(), SessionLog())
    plan = make_plan(dev.read_block(), {"low_cut": 290})
    assert plan.frames(framing=Tagged())[0][:1] == b"T"
    assert plan.frames() == [TOY.write_frame(TOY.table["low_cut"], 290)]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen /Users/rar/eg4_tool/.venv/bin/pytest tests/test_framing.py -q`
Expected: `ModuleNotFoundError: packkeeper.framing`.

- [ ] **Step 3: Errors and Modbus.**

In `src/packkeeper/errors.py` add:

```python
class FrameError(Exception):
    """A reply that is malformed or does not answer the request."""
```

In `src/packkeeper/modbus.py`:
- import `FrameError` alongside `DeviceRefused`;
- change `class ModbusError(Exception)` to `class ModbusError(FrameError)`;
- add `MAX_READ = 125  # FC03 limit` after `EXCEPTION_FLAG`;
- in `read_request`, use `MAX_READ` in place of the literal 125.

- [ ] **Step 4: Create `src/packkeeper/framing.py`:**

```python
"""How frames are built and checked, one class per wire protocol.

A driver names its framing; each device gets its own instance, so per-session
state (such as JBD's write key) never leaks between connections.
"""

from __future__ import annotations

from typing import Protocol

from . import modbus


class Framing(Protocol):
    name: str
    max_read: int  # registers per read request
    head_len: int  # bytes needed before reply_length can answer

    def read_request(self, address: int, start: int, count: int) -> bytes: ...
    def parse_read(self, request: bytes, reply: bytes) -> list[int]: ...
    def write_request(self, address: int, start: int, values: list[int]) -> bytes: ...
    def parse_write(self, request: bytes, reply: bytes) -> None: ...
    def reply_length(self, head: bytes) -> int | None: ...
    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]: ...


class ModbusFraming:
    name = "Modbus RTU"
    max_read = modbus.MAX_READ
    head_len = 3

    def read_request(self, address: int, start: int, count: int) -> bytes:
        return modbus.read_request(address, start, count)

    def parse_read(self, request: bytes, reply: bytes) -> list[int]:
        return modbus.parse_read_reply(request, reply)

    def write_request(self, address: int, start: int, values: list[int]) -> bytes:
        return modbus.write_request(address, start, values)

    def parse_write(self, request: bytes, reply: bytes) -> None:
        modbus.parse_write_reply(request, reply)

    def reply_length(self, head: bytes) -> int | None:
        return modbus.expected_reply_length(head)

    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]:
        """Drop leading bytes that cannot start a reply to this request."""
        address, function = request[0], request[1]
        ok_functions = (function, function | modbus.EXCEPTION_FLAG)
        i = 0
        while i < len(buf):
            if buf[i] == address and (i + 1 >= len(buf) or buf[i + 1] in ok_functions):
                break
            i += 1
        return buf[i:], buf[:i]


MODBUS = ModbusFraming()
```

- [ ] **Step 5: Transport.**

In `src/packkeeper/transport.py`, replace the body of `resync(buf, address, function)` with a delegation that keeps its docstring and signature:

```python
    from .framing import MODBUS

    return MODBUS.resync(buf, bytes([address, function]))
```

`SerialTransport.__init__` gains `framing=None` as its last parameter and stores `self.framing = framing or MODBUS`. Import `MODBUS` lazily inside `__init__`, the same way, to avoid an import cycle.

In `transact`, replace the loop body with:

```python
            reply, dropped = self.framing.resync(reply, request)
            self.noise += dropped
            want = self.framing.reply_length(reply)
            n = (want - len(reply)) if want is not None else self.framing.head_len - len(reply)
            if want is not None and n <= 0:
                return reply
            reply += s.read(n)
```

- [ ] **Step 6: Device.**

In `src/packkeeper/device.py`:
- Import `DeviceRefused, FrameError` from `.errors`.
- Set `MAX_READ = modbus.MAX_READ  # FC03 limit`, keeping the name.
- In `__init__`, set `self.framing = driver.new_framing()`.
- In `_transact`, catch `FrameError` in place of `modbus.ModbusError`.
- In `read`:
  - `request = self.framing.read_request(self.address, start, count)`;
  - catch `(NoReply, FrameError)` around `_transact`;
  - return `self.framing.parse_read(request, reply)`;
  - `except DeviceRefused: raise`;
  - `except FrameError as e:` to retry.
- In `read_area`, chunk at `self.framing.max_read` in place of `MAX_READ`. Then check the length:

```python
        if len(out) != area.count:
            raise FrameError(f"0x{area.start:02X}: the device sent {len(out)} registers, expected {area.count}")
```

- In `read_block`, catch `DeviceRefused` in place of `modbus.ModbusException`.
- `_write`:

```python
    def _write(self, register: int, values: list[int]) -> bool:
        request = self.framing.write_request(self.address, register, values)
        try:
            self.framing.parse_write(request, self._transact(request))
        except NoReply:
            return False
        except DeviceRefused:
            raise
        except FrameError as e:
            self.log.warn(f"bad ack: {e}")
            return False
        return True
```

- Change the module docstring's first line to "Talking to one device: reads, writes, every frame logged."

- [ ] **Step 7: Driver base.**

In `src/packkeeper/drivers/base.py`:
- Import `from ..framing import MODBUS`.
- Add these attributes after `address_choices`:

```python
    framing = MODBUS  # the wire protocol; new_framing() gives each device its own state
    broadcast_address: int | None = 0  # an address every device on the bus obeys, or None
```

- Add to the provided methods:

```python
    def new_framing(self):
        """The framing for one device session (the shared one when it holds no state)."""
        return self.framing
```

- Change `write_frame` and `clock_frame` to take `framing=None`:

```python
    def write_frame(self, setting, raw: int, address: int | None = None, framing=None) -> bytes:
        """The frame that writes `setting` (all of its registers) to `raw`."""
        f = framing or self.framing
        return f.write_request(self.address if address is None else address, setting.register, setting.encode(raw))

    def clock_frame(self, t: datetime, address: int | None = None, framing=None) -> bytes:
        register, values = self.clock_write(t)
        return (framing or self.framing).write_request(self.address if address is None else address, register, values)
```

- Drop the `from .. import modbus` import if it is now unused.
- Update the class docstring. Replace "The defaults assume Modbus RTU … for dry-run output." with: "Frames come from `framing` (Modbus RTU by default). A BMS with another wire protocol sets `framing` and, if the framing holds session state, overrides `new_framing`."

- [ ] **Step 8: Planner, CLI and GUI call sites.**
  - `planner.Plan.frames`:

```python
    def frames(self, address: int | None = None, framing=None) -> list[bytes]:
        d = self.base.driver
        return [d.write_frame(c.setting, c.new, address, framing) for c in self.changes]
```

  - `cli.py`:
    - `plan.frames(pack.address)` becomes `plan.frames(pack.address, pack.framing)`;
    - `driver.clock_frame(target, pack.address)` becomes `driver.clock_frame(target, pack.address, pack.framing)`;
    - `SerialTransport(port, params=driver.serial)` becomes `SerialTransport(port, params=driver.serial, framing=driver.framing)`;
    - `warn_broadcast` checks `if pack.address == pack.driver.broadcast_address:`;
    - add `FrameError` (from `.errors`) to the exception tuple in `main`.
  - `gui.py`:
    - the ReviewDialog frames call passes `main.pack.framing if main.pack else None`;
    - `ClockTab.set_clock` passes `self.main.pack.framing`;
    - `SerialTransport(device, params=driver.serial, framing=driver.framing)`;
    - both broadcast checks compare against `pack.driver.broadcast_address` instead of `0`.

- [ ] **Step 9: Run the new tests, then the full suite and lint.** Expected: all pass, with no existing assertion changed.

- [ ] **Step 10: Commit**

```bash
git add -A src tests
git commit -m "Frames come from a per-device Framing; Modbus RTU is the first"
```

---

### Task 2: Unsafe settings, choices and bits (core and CLI)

**Files:**
- Modify: `src/packkeeper/settings.py`, `src/packkeeper/planner.py`, `src/packkeeper/cli.py`, `src/packkeeper/layout.py`
- Create: `tests/unsafe_toy_driver.py`, `tests/test_unsafe.py`

**Interfaces:**
- Produces:
  - On `Setting`: `unsafe: str | None = None`, `choices: tuple[tuple[int, str], ...] | None = None`, `bits: tuple[tuple[int, str], ...] | None = None`.
  - `SettingTable.build(..., unsafe=None, choices=None, bits=None)`, which take dicts keyed by setting.
  - `SettingTable.unsafe_keys -> list[str]`.
  - `make_plan(..., allow_unsafe=False)`, and `Plan.unsafe -> list[Change]`.
  - `targets_from_block(source, skip=frozenset(), allow_unsafe=False)`.
  - The CLI common option `--unsafe`.
  - `layout.Page.unsafe: bool = False`.

- [ ] **Step 1: Create the toy driver** `tests/unsafe_toy_driver.py`:

```python
"""A made-up Modbus BMS with unsafe settings, choices and bits. Test-only."""

from packkeeper.block import Area, Identity, ascii_text
from packkeeper.drivers.base import Driver
from packkeeper.layout import Page, Section
from packkeeper.live import Live
from packkeeper.settings import SettingTable
from packkeeper.sim import FakeModbusDevice

START, COUNT = 0x300, 6  # 0x300 name, 0x301 volts, 0x302 cells, 0x303 chem, 0x304 flags, 0x305 mode

SPEC = {
    "volts": (0x301, 0.01, 0, "V", None, 2),
    "cells": (0x302, 1, 0, None, None, 0),
    "chem": (0x303, 1, 0, None, None, 0),
    "flags": (0x304, 1, 0, None, None, 0),
    "mode": (0x305, 1, 0, None, None, 0),
}
LIMITS = {"volts": (3.0, 3.6, 0.01), "cells": (1, 32, 1), "chem": (0, 1, 1), "flags": (0, 0xFFFF, 1), "mode": (0, 1, 1)}
UNSAFE = {
    "cells": "A wrong count breaks every cell protection.",
    "chem": "Changes the chemistry model.",
    "flags": "Anti-theft can lock the pack out.",
}
CHOICES = {"chem": {0: "LFP", 1: "NCM"}, "mode": {0: "charge only", 1: "static"}}
BITS = {"flags": {0x0001: "heater", 0x0004: "buzzer", 0x0010: "anti-theft"}}

IMAGE = [0] * (START + COUNT)
IMAGE[0:2] = [5120, 80]
IMAGE[START:] = [0x5546, 340, 16, 0, 0x0001, 0]  # "UF", 3.40 V, 16 cells, LFP, heater, charge only


class UnsafeToy(Driver):
    name = "unsafe-toy"
    label = "Unsafe toy"
    table = SettingTable.build(SPEC, LIMITS, [], unsafe=UNSAFE, choices=CHOICES, bits=BITS)
    areas = (Area(START, COUNT),)
    address = 4
    live_reads = (Area(0x00, 2),)
    pages = (
        Page("Main", (Section("Main", ("Value",), (("Volts", ("volts",)), ("Mode", ("mode",)))),)),
        Page("Unsafe", (Section("Unsafe", ("Value",), (("Cells", ("cells",)), ("Chemistry", ("chem",)), ("Flags", ("flags",)))),), unsafe=True),
    )

    def is_writable(self, register):
        return START + 1 <= register < START + COUNT

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(pack_voltage=r[0] / 100, current=0.0, soc=r[1], soh=100, cells=[3.3], status_text="Idle")

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, state_file=state_file, state_from=START)


UNSAFE_TOY = UnsafeToy()
```

- [ ] **Step 2: Write the failing tests** in `tests/test_unsafe.py`:

```python
import pytest

from packkeeper.planner import make_plan, targets_from_block
from packkeeper.session import SessionLog
from packkeeper.settings import SettingError

from unsafe_toy_driver import UNSAFE_TOY


def dev():
    sim = UNSAFE_TOY.simulator()
    return sim, UNSAFE_TOY.open_device(sim, SessionLog())


def test_settings_carry_unsafe_choices_and_bits():
    t = UNSAFE_TOY.table
    assert t["cells"].unsafe.startswith("A wrong count") and t["volts"].unsafe is None
    assert t.unsafe_keys == ["cells", "chem", "flags"]
    assert t["chem"].format(1) == "NCM" and t["chem"].format(7) == "unknown (7)"
    assert t["chem"].parse("ncm") == 1 and t["chem"].parse("0") == 0
    assert t["chem"].limit_error(1) is None and "LFP" in t["chem"].limit_error(5)
    assert t["flags"].format(0x0015) == "0x0015 (heater, buzzer, anti-theft)"
    assert t["flags"].format(0) == "0x0000 (none)"
    assert t["flags"].parse("0x0011") == 0x11 and t["flags"].parse("0x0015 (heater, buzzer, anti-theft)") == 0x15
    with pytest.raises(SettingError):
        t["chem"].parse("diesel")


def test_unsafe_changes_need_the_switch():
    _sim, d = dev()
    b = d.read_block()
    p = make_plan(b, {"cells": 15})
    assert not p.ok and "unsafe setting" in p.errors[0] and "cells" in p.problem_keys
    p = make_plan(b, {"cells": 15}, allow_unsafe=True)
    assert p.ok and [c.key for c in p.unsafe] == ["cells"]
    assert make_plan(b, {"volts": 350}).unsafe == []
    assert make_plan(b, {"cells": 16}).ok  # unchanged: nothing to gate


def test_restore_targets_leave_unsafe_settings_out_without_the_switch():
    _sim, d = dev()
    b = d.read_block()
    assert set(targets_from_block(b)) == {"volts", "mode"}
    assert set(targets_from_block(b, allow_unsafe=True)) == set(UNSAFE_TOY.table)


def run_cli(monkeypatch, tmp_path, capsys, argv, answer):
    from packkeeper import drivers
    from packkeeper.cli import main

    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    monkeypatch.setattr("builtins.input", lambda prompt="": (print(prompt), answer)[1])
    code = main([*argv, "--bms", "unsafe-toy", "--simulate", "--log-dir", str(tmp_path)])
    return code, capsys.readouterr()


def test_cli_refuses_unsafe_without_the_switch(monkeypatch, tmp_path, capsys):
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "cells", "15"], "yes")
    assert code == 2 and "unsafe setting" in out.out


def test_cli_unsafe_needs_typed_unsafe(monkeypatch, tmp_path, capsys):
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "--unsafe", "cells", "15"], "yes")
    assert code == 1 and "Type 'unsafe'" in out.out and "A wrong count" in out.out and "Nothing was written" in out.out
    code, out = run_cli(monkeypatch, tmp_path, capsys, ["set", "--unsafe", "cells", "15"], "unsafe")
    assert code == 0 and "confirmed" in out.out


def test_cli_keys_marks_unsafe_and_lists_choices(monkeypatch, tmp_path, capsys):
    from packkeeper import drivers
    from packkeeper.cli import main

    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    assert main(["keys", "--bms", "unsafe-toy"]) == 0
    out = capsys.readouterr().out
    assert "[unsafe]" in out and "LFP, NCM" in out
```

- [ ] **Step 3: Run them and confirm they fail.** Expected: TypeError (unexpected keyword `unsafe`, or `unsafe=` on Page).

- [ ] **Step 4: Implement `settings.py`.**

Add these fields after `word_order` on `Setting`:

```python
    unsafe: str | None = None  # why changing it is risky; None for an ordinary setting
    choices: tuple[tuple[int, str], ...] | None = None  # named values
    bits: tuple[tuple[int, str], ...] | None = None  # named bit masks
```

In `limit_error`, at the top:

```python
        if self.choices is not None:
            names = dict(self.choices)
            if raw not in names:
                return f"{self.label} {raw} is not one of: {', '.join(names.values())}"
            return None
```

In `format`, at the top:

```python
        if self.choices is not None:
            return dict(self.choices).get(raw, f"unknown ({raw})")
        if self.bits is not None:
            names = [name for mask, name in self.bits if raw & mask == mask]
            return f"0x{raw:04X} ({', '.join(names) or 'none'})"
```

In `parse`, at the top:

```python
        if self.choices is not None:
            s = text.strip()
            for raw, name in self.choices:
                if s.lower() == name.lower():
                    return raw
            try:
                return int(s, 0)
            except ValueError:
                raise SettingError(f"{self.label}: '{text}' is not one of: {', '.join(n for _r, n in self.choices)}") from None
        if self.bits is not None:
            s = text.strip().split(" (", 1)[0]
            try:
                raw = int(s, 0)
            except ValueError:
                raise SettingError(f"{self.label}: '{text}' is not a number") from None
            lo, hi = self.raw_bounds
            if not lo <= raw <= hi:
                raise SettingError(f"{self.label}: {raw} does not fit the register")
            return raw
```

`SettingTable`:
- add the property `unsafe_keys`:

```python
    @property
    def unsafe_keys(self) -> list[str]:
        return [k for k, s in self.settings.items() if s.unsafe]
```

- `build` gains `unsafe: dict[str, str] | None = None, choices: dict[str, dict[int, str]] | None = None, bits: dict[str, dict[int, str]] | None = None`. Pass each into `Setting`:

```python
                unsafe=(unsafe or {}).get(key),
                choices=tuple(sorted((choices or {})[key].items())) if key in (choices or {}) else None,
                bits=tuple(sorted((bits or {})[key].items())) if key in (bits or {}) else None,
```

- In `build`, validate the keys: any key in `unsafe`, `choices` or `bits` that is not in `table` raises `ValueError(f"unknown setting '{k}'")`.

- [ ] **Step 5: `layout.py`.** Add `unsafe: bool = False  # shown only while unsafe settings are on` to `Page`, after `note`.

- [ ] **Step 6: Implement `planner.py`.**

`Plan` gains:

```python
    @property
    def unsafe(self) -> list[Change]:
        return [c for c in self.changes if c.setting.unsafe]
```

`make_plan` gains `allow_unsafe: bool = False`, after `source`. In the targets loop, right after `if raw == current[key]: continue`:

```python
        if s.unsafe and not allow_unsafe:
            plan.errors.append(f"{s.label} is an unsafe setting; turn on unsafe settings to change it")
            plan.problem_keys.add(key)
            continue
```

`targets_from_block`:

```python
def targets_from_block(source: Block, skip: set[str] = frozenset(), allow_unsafe: bool = False) -> dict[str, int]:
    """Every known setting of `source`; unknown registers are never included,
    and unsafe settings only when asked for."""
    source.check_not_blank()
    table = source.driver.table
    return {k: source.raw(k) for k in table if k not in skip and (allow_unsafe or not table[k].unsafe)}
```

- [ ] **Step 7: Implement `cli.py`.**
- Add the common option `--unsafe` with `action="store_true"` and help text "allow changing unsafe settings (each is confirmed by typing 'unsafe')".
- `run_write` passes `allow_unsafe=args.unsafe` to `make_plan`.
- `cmd_restore` and `cmd_copy` pass `allow_unsafe=args.unsafe` to `targets_from_block`.
- In `run_write`, after `print_plan`, when `source is not None and not args.unsafe`:

```python
        left = [k for k in block.driver.table.unsafe_keys if source.raw(k) != block.raw(k)]
        if left:
            print(f"note: {len(left)} unsafe setting(s) differ and were left alone (use --unsafe to include them)")
```

- Replace the confirmation `input(...)` block with:

```python
    word = "unsafe" if plan.unsafe else "yes"
    if plan.unsafe:
        print("\nUNSAFE changes in this plan:")
        for c in plan.unsafe:
            print(f"  {c.describe(f)}\n    {c.setting.unsafe}")
    try:
        answer = input(f"\nType '{word}' to write {len(plan.changes)} setting(s) to this pack: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != word:
```

  The body under the `if` stays as it is: "Nothing was written.", log, return 1.
- In `cmd_keys`, append `" [unsafe]"` to the line of an unsafe setting. For a setting with choices, print `choices: <names, comma-separated>` on the next line, indented 4 spaces. For a setting with bits, print `bits: 0x0001 heater, ...` the same way.
- EG4 and JK tables have no unsafe settings, choices or bits, so their output is unchanged.

- [ ] **Step 8: Run the new tests, then the full suite and lint.** Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "Unsafe settings behind a switch and a typed confirmation; named choices and bits"
```

---

### Task 3: Unsafe settings, choices and bits in the GUI

**Files:**
- Modify: `src/packkeeper/settings_view.py`, `src/packkeeper/gui.py`
- Test: `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: `Setting.unsafe/choices/bits`, `Page.unsafe`, `make_plan(..., allow_unsafe=)`, `targets_from_block(..., allow_unsafe=)` and `Plan.unsafe` (Task 2).
- Produces:
  - `SettingsTab.unsafe_box` (a QCheckBox) and `SettingsTab.unsafe_on -> bool`;
  - `ReviewDialog.unsafe_confirm` (a QLineEdit, present only when the plan has unsafe changes);
  - `settings_view.ChoiceEdit` (a QComboBox used in place of `ValueEdit` for settings with choices).

Behaviour:
1. `unsafe_box` ("Show unsafe settings") sits in the settings top bar, shown only when `driver.table.unsafe_keys` is non-empty. It starts unticked.
   - The first tick in a session shows `QMessageBox.warning(self, "Unsafe settings", <text>)`. The text is: "These settings can make the pack mis-measure, lock itself out or stop talking to the inverter. Change one only if you know why, and keep a backup."
   - Ticking shows the nav rows of pages with `page.unsafe` (`self.nav.setRowHidden(i, not on)`). Those rows are hidden otherwise. If the current row becomes hidden, select row 0.
   - Unticking removes every unsafe key from `staged` and `bad`, then calls `refresh()`.
2. `stage_all`, the "copy everything from the comparison source" action, skips unsafe settings unless `unsafe_on`.
3. `MainWindow.review` passes `allow_unsafe=self.settings.unsafe_on` to `make_plan`.
4. `ReviewDialog`, when `plan.unsafe` is non-empty:
   - add a red block listing each unsafe change (`c.describe(f)`) with `c.setting.unsafe` under it;
   - add a QLineEdit `unsafe_confirm` with the placeholder "type unsafe to confirm";
   - enable `write_btn` only when `ack` is checked and `unsafe_confirm.text().strip().lower() == "unsafe"`. Re-evaluate on both signals.
   - Without unsafe changes, the dialog is unchanged.
5. Settings with choices use `ChoiceEdit(QComboBox)` instead of `ValueEdit`. It must offer the same API that `SettingField` uses on its edit widget. Read `SettingField` (around settings_view.py:93-145) and give `ChoiceEdit` the same methods and signals:
   - `text()` returns the current item's text, and `setText(name)` selects the matching item;
   - `editingFinished` is emitted on `activated`;
   - support whatever property and enable calls the field makes.
   `SettingField(key)` needs the setting to choose. Pass it as `SettingField(key, setting)` from `SettingsTab.__init__` (`self.table[key]`). The parameter is optional for other callers.
6. Settings with bits keep `ValueEdit`. The field's sub-label, or tooltip if there is no room, shows the bit names, as `format()` already gives them. Typing "0x0011" commits.
7. Any driver with no unsafe settings, choices or bits gets exactly today's widgets.

- [ ] **Step 1: Write the failing GUI tests.** Append to `tests/test_gui.py`:

```python
def toy_window(tmp_path, monkeypatch):
    from packkeeper import drivers, qtcommon
    from unsafe_toy_driver import UNSAFE_TOY
    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    w.bms.setCurrentIndex(w.bms.findData("unsafe-toy"))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    return app, w


def test_unsafe_page_hidden_until_switched_on(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        s = w.settings
        assert not s.unsafe_box.isHidden() and not s.unsafe_box.isChecked()
        unsafe_row = [i for i, p in enumerate(s.driver.pages) if p.unsafe][0]
        assert s.nav.isRowHidden(unsafe_row)
        warned = []
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
        s.unsafe_box.setChecked(True)
        assert warned and not s.nav.isRowHidden(unsafe_row)
        edit(w, "cells", "15")
        wait(app, lambda: "cells" in s.staged)
        s.unsafe_box.setChecked(False)
        assert "cells" not in s.staged and s.nav.isRowHidden(unsafe_row)
    finally:
        w.close()


def test_unsafe_review_needs_typed_unsafe(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: None)
        w.settings.unsafe_box.setChecked(True)
        edit(w, "cells", "15")
        wait(app, lambda: "cells" in w.settings.staged)
        dlg = open_review(app, w, monkeypatch)
        assert [c.key for c in dlg.plan.unsafe] == ["cells"]
        dlg.ack.setChecked(True)
        assert not dlg.write_btn.isEnabled()
        dlg.unsafe_confirm.setText("unsafe")
        assert dlg.write_btn.isEnabled()
        dlg.write_btn.click()
        wait(app, lambda: dlg.result is not None)
        assert dlg.result.ok
    finally:
        w.close()


def test_choice_setting_is_a_dropdown(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        field = w.settings.fields["mode"]
        assert isinstance(field.edit, gui.settings_view.ChoiceEdit)
        assert [field.edit.itemText(i) for i in range(field.edit.count())] == ["charge only", "static"]
        assert field.edit.text() == "charge only"
        field.edit.setText("static")
        field.edit.editingFinished.emit()
        wait(app, lambda: w.settings.staged.get("mode") == 1)
    finally:
        w.close()
```

The existing `edit(w, key, text)` helper calls `field.edit.setText` and then `editingFinished.emit()`, so it works for both widgets.

- [ ] **Step 2: Run them and confirm they fail.** Expected: AttributeError (`unsafe_box`).

- [ ] **Step 3: Implement** the seven behaviours above, in `settings_view.py` and `gui.py`.

- [ ] **Step 4: Run the full suite and lint.** Expected: all pass. The EG4, JK and toy GUI tests are unchanged.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "GUI: unsafe settings page and typed confirmation; choice dropdowns"
```

---

### Task 4: Find, and per-pack serial warnings

**Files:**
- Create: `src/packkeeper/find.py`, `tests/test_find.py`
- Modify: `src/packkeeper/drivers/base.py`, `src/packkeeper/planner.py`, `src/packkeeper/cli.py`, `src/packkeeper/gui.py`

**Interfaces:**
- Produces:
  - `Driver.find_addresses: range | None = None`, `Driver.find_probe: Area | None = None` and `Driver.serial_is_unique: bool = False`.
  - `find.Found(address: int, model: str, serial: str, note: str = "")`.
  - `find.scan(driver, transport, addresses, log, timeout=0.3) -> list[Found]`.
  - The CLI subcommand `find`.
  - The GUI `MainWindow.find_btn`.
  - `identity_warnings` adds a serial warning when `driver.serial_is_unique` is set and the serials differ.

- [ ] **Step 1: Write the failing tests** in `tests/test_find.py`:

```python
from packkeeper.block import Area
from packkeeper.find import scan
from packkeeper.planner import identity_warnings
from packkeeper.session import SessionLog
from packkeeper.transport import NoReply

from unsafe_toy_driver import IMAGE, START, UNSAFE_TOY


class Bus:
    """Several fake packs on one line, each answering only its own address."""

    def __init__(self, packs):
        self.packs = packs
        self.timeout = 1.0

    def transact(self, request):
        pack = self.packs.get(request[0])
        if pack is None:
            raise NoReply("nobody at that address")
        return pack.transact(request)

    def close(self):
        pass


class Findable(type(UNSAFE_TOY)):
    address_choices = range(0, 16)
    find_addresses = range(0, 8)
    find_probe = Area(START, 1)
    serial_is_unique = True


def pack(name):
    sim = UNSAFE_TOY.simulator()
    sim.image[START] = int.from_bytes(name.encode(), "big")
    return sim


def test_scan_lists_who_answers():
    d = Findable()
    bus = Bus({0: pack("AA"), 2: pack("BB"), 5: pack("CC")})
    found = scan(d, bus, d.find_addresses, SessionLog())
    assert [(f.address, f.model) for f in found] == [(0, "AA"), (2, "BB"), (5, "CC")]
    assert bus.timeout == 1.0  # restored


def test_cli_find(monkeypatch, tmp_path, capsys):
    from packkeeper import drivers
    from packkeeper.cli import main

    d = Findable()
    d.name = "findable"
    monkeypatch.setitem(drivers.DRIVERS, "findable", d)
    monkeypatch.setattr(type(d), "simulator", lambda self, state_file=None: Bus({3: pack("DD")}))
    assert main(["find", "--bms", "findable", "--simulate", "--log-dir", str(tmp_path)]) == 0
    assert "3" in capsys.readouterr().out
    assert main(["find", "--simulate", "--log-dir", str(tmp_path)]) == 2  # EG4-LL has no find


def test_serial_warning_only_for_unique_serials():
    from packkeeper.block import Identity

    class Serial(Findable):
        def identity(self, block):
            return Identity(model="M", serial=f"S{block.reg(START + 1)}")

    s = Serial()
    a = s.block(IMAGE[START:])
    b = a.with_raws({START + 1: 341})
    assert any("S340" in w and "S341" in w for w in identity_warnings(b, a, "the backup"))
    s.serial_is_unique = False
    assert identity_warnings(b, a, "the backup") == []
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: `ModuleNotFoundError: packkeeper.find`.

- [ ] **Step 3: Implement.**

In `drivers/base.py`, add these attributes after `broadcast_address`:

```python
    find_addresses: range | None = None  # addresses Find tries; within address_choices
    find_probe: Area | None = None  # the quick read Find uses to see who answers
    serial_is_unique: bool = False  # True when the serial tells packs apart
```

Create `src/packkeeper/find.py`:

```python
"""Find: which addresses answer, for BMS types whose address can be chosen. Reads only."""

from __future__ import annotations

from dataclasses import dataclass

from .block import BlankBlockError
from .errors import DeviceRefused, FrameError
from .transport import NoReply


@dataclass(frozen=True)
class Found:
    address: int
    model: str
    serial: str
    note: str = ""


def scan(driver, transport, addresses, log, timeout: float = 0.3) -> list[Found]:
    old = getattr(transport, "timeout", None)
    if old is not None:
        transport.timeout = timeout
    found = []
    try:
        for a in addresses:
            dev = driver.open_device(transport, log, a)
            try:
                dev.read_area(driver.find_probe, retries=1)
            except (NoReply, FrameError, DeviceRefused):
                continue
            try:
                block = dev.read_block()
                found.append(Found(a, block.model, block.serial))
            except (NoReply, FrameError, DeviceRefused, BlankBlockError) as e:
                found.append(Found(a, "?", "?", note=str(e)))
    finally:
        if old is not None:
            transport.timeout = old
    log.result(f"find: {len(found)} answered at {', '.join(str(f.address) for f in found) or 'none'}")
    return found
```

Where the serial is shown, use `serial_is_unique`:
- **CLI `print_identity`:** print the serial line as `f"Serial    {ident.serial}"`. Add the existing suffix `"  (the same on every pack; does not identify it)"` only when `not block.driver.serial_is_unique`, so EG4 output is unchanged.
- **`backup.to_dict`:** `serial_note` is that same text only when the serial is non-empty and the driver's serial is not unique; otherwise it is None.
- **GUI `got_block`:** the identity text adds `f" · {block.serial}"` when `block.driver.serial_is_unique and block.serial`.

In `planner.identity_warnings`, after the firmware check:

```python
    if pack.driver.serial_is_unique and source.serial and pack.serial and source.serial != pack.serial:
        out.append(f"{source_name} is from pack '{source.serial}', the connected pack is '{pack.serial}'")
```

CLI:
- Add a subcommand `find` (common args; help "list the addresses that answer (reads only)"), handled by `cmd_find`.
- If `driver.find_addresses is None`, raise `CliError(f"the {driver.label} has no address to find")`.
- Otherwise:
  - open the transport the same way `open_pack` does, but without making a device;
  - call `scan`;
  - print one line per `Found`: `f"address {f.address:3d}  {f.model}  {f.serial}  {f.note}".rstrip()`;
  - return 0 if anything was found, else 1.
- Move the transport opening into a helper `open_transport(args, driver, log)` used by both `open_pack` and `cmd_find`, so there is no duplicated port logic.

GUI:
- Add `find_btn = QPushButton("Find…")` after the address box. It is visible when `driver.find_addresses is not None`, and enabled only while disconnected.
- Clicking it:
  - opens the selected port's transport, or the simulator;
  - runs `scan` on the worker, with the session log;
  - shows a `QInputDialog.getItem` list of `"address N — model — serial"`;
  - on a choice, sets the address box;
  - finally closes the transport;
  - if nothing answered, shows a status message instead.
- It is untested here; Task 7 adds the GUI test with the JBD simulator.

- [ ] **Step 4: Run the tests, then the full suite and lint.** Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A src tests
git commit -m "Find: a read-only scan of addresses; serial warnings for drivers with unique serials"
```

---

### Task 5: JBD-UP framing and simulator

**Files:**
- Create: `src/packkeeper/drivers/jbd_up/__init__.py` (empty for now), `src/packkeeper/drivers/jbd_up/framing.py`, `src/packkeeper/drivers/jbd_up/registers.py`, `src/packkeeper/drivers/jbd_up/sim.py`
- Test: `tests/test_jbd_up.py`

**Interfaces:**
- Consumes: `errors.FrameError`, `framing.Framing` (Task 1).
- Produces:
  - `jbd_up.framing.JbdFraming(key=DEFAULT_KEY)` with `.key`, `FN_READ = 0x78`, `FN_WRITE = 0x79`, `DEFAULT_KEY = 0x11`, `JbdError(FrameError)`;
  - `jbd_up.registers`: area constants and helpers (below);
  - `jbd_up.sim.FakeJbd` and `jbd_up.sim.FakeJbdBus`.

- [ ] **Step 1: Write the failing tests** in `tests/test_jbd_up.py`:

```python
import pytest

from packkeeper.drivers.jbd_up.framing import DEFAULT_KEY, JbdError, JbdFraming
from packkeeper.transport import NoReply

F = JbdFraming()


def test_frames_match_the_captured_examples():
    assert F.read_request(0, 0x1000, 0x50).hex(" ").upper() == "00 78 10 00 10 A0 00 00 BE 7E"
    assert F.read_request(0, 0x1800, 0x80).hex(" ").upper() == "00 78 18 00 19 00 00 00 BC 88"
    assert F.write_request(0, 0x1806, [3650]).hex(" ").upper() == "00 79 18 06 18 08 00 06 11 4A 42 44 0E 42 FC 5C"
    assert DEFAULT_KEY == 0x11 and F.max_read == 245 and F.head_len == 8


def test_key_byte_is_per_instance():
    f = JbdFraming(key=0x12)
    assert f.write_request(0, 0x1806, [3650])[8] == 0x12 and JbdFraming().key == 0x11


def reply(addr, fn, start, end, payload):
    from packkeeper.modbus import with_crc
    import struct
    return with_crc(struct.pack(">BBHHH", addr, fn, start, end, len(payload)) + payload)


def test_parse_read_and_reply_length():
    req = F.read_request(2, 0x1800, 2)
    r = reply(2, 0x78, 0x1800, 0x1804, bytes.fromhex("0E10 0DAC"))
    assert F.reply_length(r[:8]) == len(r) and F.reply_length(r[:7]) is None
    assert F.parse_read(req, r) == [0x0E10, 0x0DAC]
    for bad in (
        r[:-1] + bytes([r[-1] ^ 1]),  # CRC
        reply(3, 0x78, 0x1800, 0x1804, bytes(4)),  # address
        reply(2, 0x78, 0x1802, 0x1806, bytes(4)),  # range
        reply(2, 0x78, 0x1800, 0x1804, bytes(2)),  # short
    ):
        with pytest.raises(JbdError):
            F.parse_read(req, bad)


def test_parse_write_ack_and_resync():
    req = F.write_request(0, 0x1806, [3600])
    ack = reply(0, 0x79, 0x1806, 0x1808, b"")
    F.parse_write(req, ack)
    with pytest.raises(JbdError):
        F.parse_write(req, reply(0, 0x79, 0x1800, 0x1802, b""))
    buf, dropped = F.resync(b"\xAA\x00\x13" + ack, req)
    assert buf == ack and dropped == b"\xAA\x00\x13"


def test_fake_pack_serves_reads_and_checks_the_key():
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim = FakeJbd(address=0, serial="SIM-PACK-1")
    r = sim.transact(F.read_request(0, 0x1800, 6))
    assert F.parse_read(F.read_request(0, 0x1800, 6), r)[3] == 3650  # cell OV protection
    F.parse_write(F.write_request(0, 0x1806, [3600]), sim.transact(F.write_request(0, 0x1806, [3600])))
    assert sim.word(0x1806) == 3600 and sim.writes == [(0x1806, [3600])]
    with pytest.raises(NoReply):
        sim.transact(JbdFraming(key=0x42).write_request(0, 0x1806, [3500]))  # wrong key: ignored
    assert sim.word(0x1806) == 3600
    with pytest.raises(NoReply):
        sim.transact(F.read_request(1, 0x1800, 6))  # not its address


def test_fake_bus_routes_by_address():
    from packkeeper.drivers.jbd_up.sim import FakeJbd, FakeJbdBus

    bus = FakeJbdBus([FakeJbd(address=0, serial="A"), FakeJbd(address=5, serial="B")])
    assert bus.transact(F.read_request(5, 0x1800, 1))
    with pytest.raises(NoReply):
        bus.transact(F.read_request(3, 0x1800, 1))
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: `ModuleNotFoundError: packkeeper.drivers.jbd_up`.

- [ ] **Step 3: Create `src/packkeeper/drivers/jbd_up/framing.py`:**

```python
"""JBD-UP ("ES") wire format: Modbus-like frames with their own function codes.

    addr | func | start (BE) | end (BE) | len (BE) | data[len] | CRC16-Modbus (LE)

Registers are byte addresses (end = start + bytes). Reads are 0x78 with no
data; writes are 0x79 with data = key byte, 'JBD', then the values. Sources:
the user's PROTOCOL.md (from JBD's ES-UP apps), syssi/esphome-jbd-bms
jbd_bms_up, and the PhracturedBlue ECO-WORTHY gist.
"""

from __future__ import annotations

import struct

from ...errors import FrameError
from ...modbus import crc_ok, with_crc

FN_READ = 0x78
FN_WRITE = 0x79
DEFAULT_KEY = 0x11  # the app's key when the BMS reports protocol version 0
MAX_REPLY = 490  # data bytes per reply


class JbdError(FrameError):
    pass


def _head(frame: bytes) -> tuple[int, int, int, int, int]:
    return struct.unpack(">BBHHH", frame[:8])


class JbdFraming:
    name = "JBD-UP"
    max_read = MAX_REPLY // 2
    head_len = 8

    def __init__(self, key: int = DEFAULT_KEY):
        self.key = key

    def read_request(self, address: int, start: int, count: int) -> bytes:
        return with_crc(struct.pack(">BBHHH", address, FN_READ, start, start + 2 * count, 0))

    def write_request(self, address: int, start: int, values: list[int]) -> bytes:
        data = b"".join(struct.pack(">H", v) for v in values)
        head = struct.pack(">BBHHH", address, FN_WRITE, start, start + len(data), len(data) + 4)
        return with_crc(head + bytes([self.key]) + b"JBD" + data)

    def reply_length(self, head: bytes) -> int | None:
        if len(head) < self.head_len:
            return None
        return 10 + struct.unpack(">H", head[6:8])[0]

    def _check(self, request: bytes, reply: bytes) -> bytes:
        addr, fn, start, end, _n = _head(request)
        if len(reply) < 10 or not crc_ok(reply):
            raise JbdError("reply CRC mismatch or too short")
        r_addr, r_fn, r_start, r_end, r_len = _head(reply)
        if (r_addr, r_fn) != (addr, fn):
            raise JbdError(f"reply from {r_addr:02X}/{r_fn:02X}, expected {addr:02X}/{fn:02X}")
        if (r_start, r_end) != (start, end):
            raise JbdError(f"reply for 0x{r_start:04X}-0x{r_end:04X}, expected 0x{start:04X}-0x{end:04X}")
        if len(reply) != 10 + r_len:
            raise JbdError(f"reply length field {r_len} does not match its {len(reply) - 10} data bytes")
        return reply[8:-2]

    def parse_read(self, request: bytes, reply: bytes) -> list[int]:
        _a, _f, start, end, _n = _head(request)
        data = self._check(request, reply)
        if len(data) != end - start:
            raise JbdError(f"reply carries {len(data)} bytes, expected {end - start}")
        return [struct.unpack(">H", data[i : i + 2])[0] for i in range(0, len(data), 2)]

    def parse_write(self, request: bytes, reply: bytes) -> None:
        self._check(request, reply)

    def resync(self, buf: bytes, request: bytes) -> tuple[bytes, bytes]:
        i = 0
        while i < len(buf):
            if buf[i] == request[0] and (i + 1 >= len(buf) or buf[i + 1] == request[1]):
                break
            i += 1
        return buf[i:], buf[:i]
```

- [ ] **Step 4: Create `src/packkeeper/drivers/jbd_up/registers.py`:**

```python
"""JBD-UP register map: areas, text fields and texts. Byte addresses, u16 big-endian."""

from __future__ import annotations

from ...block import Area

STRIDE = 2
LIVE = Area(0x1000, 0x50, STRIDE)  # 0x1000-0x10A0
PROTECTION = Area(0x1800, 0x80, STRIDE)  # 0x1800-0x1900
PACK = Area(0x1C00, 0x50, STRIDE)  # 0x1C00-0x1CA0
CAPACITY = Area(0x2000, 0x28, STRIDE)  # 0x2000-0x2050 (the V1 app's range)
CLOCK = Area(0x2800, 8, STRIDE)  # Y M D h m s, then 2 spare
VERSION = Area(0x2810, 0x18, STRIDE)  # 0x2810-0x2840
AREAS = (PROTECTION, PACK, CAPACITY, CLOCK, VERSION)

REG_SIGNALS = 0x1022  # live signal word; bit 15 = large-capacity mode
LARGE_CAPACITY = 1 << 15
REG_HW_VER, REG_APP_MAJOR, REG_APP_MINOR, REG_PROTOCOL = 0x2812, 0x2814, 0x2816, 0x281A
REG_HW_NAME, REG_PROJECT = 0x281C, 0x282C  # 16 B ASCII each
REG_PACK_SN = 0x1C34  # 30 B ASCII
REG_CELL_OV_PROTECT = 0x1806
REG_CELLS_IN_SERIES = 0x2032

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables, plug the USB-RS485 adapter into the pack's RS485 "
    "port, and set <b>Address</b> to its DIP value, or use <b>Find</b>."
)
CONNECTION_HELP = """\
The pack answers on RS485 when it is alone on the line: unplug the link
cables between packs. Also check:
  - A and B are not swapped (if nothing answers, swap them),
  - the address: use Find to see which addresses answer,
  - the baud (9600 unless it was changed in the stock app)."""
NO_REPLY_HINT = "Check the link cables are unplugged, A/B wiring, and the address (use Find)."
SETUP_HINT = "link cables unplugged, adapter in its RS485 port"
CLOCK_NOTE = "The BMS clock is local time, stored as year, month, day, hour, minute, second. Setting it is read back to confirm."
```

- [ ] **Step 5: Create `src/packkeeper/drivers/jbd_up/sim.py`.**

The simulated pack's register values are invented, as are the live data and the version block. The plausible 16S 100 Ah values are:

```python
"""A simulated JBD-UP pack speaking the real 0x78/0x79 framing. The values are
invented test data for a 16S 100 Ah LiFePO4 pack, not JBD defaults."""

from __future__ import annotations

import json
import struct
from datetime import datetime
from pathlib import Path

from ...modbus import with_crc
from ...transport import NoReply
from . import registers as R
from .framing import FN_READ, FN_WRITE


def _t(c: float) -> int:
    return round((c + 50) * 10)


# Six-register groups: alarm, alarm recovery, alarm delay, protection, protection recovery, protection delay.
GROUPS = {
    0x1800: (3600, 3500, 2000, 3650, 3550, 2000),  # cell OV, mV
    0x180C: (2900, 3000, 2000, 2800, 2950, 2000),  # cell UV
    0x1818: (5760, 5600, 2000, 5840, 5680, 2000),  # pack OV, 0.01 V
    0x1824: (4640, 4800, 2000, 4480, 4720, 2000),  # pack UV
    0x1858: (_t(50), _t(45), 2000, _t(55), _t(50), 2000),  # charge OT
    0x1864: (_t(2), _t(5), 2000, _t(0), _t(5), 2000),  # charge UT
    0x1870: (_t(55), _t(50), 2000, _t(60), _t(55), 2000),  # discharge OT
    0x187C: (_t(-15), _t(-10), 2000, _t(-20), _t(-15), 2000),  # discharge UT
    0x1888: (_t(85), _t(75), 2000, _t(95), _t(80), 2000),  # MOSFET OT
    0x1894: (_t(55), _t(50), 2000, _t(60), _t(55), 2000),  # ambient OT
    0x18A0: (_t(-15), _t(-10), 2000, _t(-20), _t(-15), 2000),  # ambient UT
    0x18AC: (80, 60, 2000, 100, 80, 2000),  # temperature diff, 0.1 °C
    0x18B8: (300, 200, 2000, 500, 300, 2000),  # cell diff, mV
    0x18C4: (10, 15, 2000, 5, 10, 2000),  # SOC low, %
}
WORDS = {
    # charge OC 1: alarm, alarm recovery, alarm delay, protection, protection delay, release delay, lockouts
    0x1830: (1000, 950, 2000, 1050, 2000, 30000, 3),
    0x183E: (1500, 1000, 30000),  # charge OC 2: protection, delay, release delay
    0x1844: (1000, 950, 2000, 1050, 2000, 30000, 3),  # discharge OC 1
    0x1852: (1500, 1000, 30000),  # discharge OC 2
    0x1C00: (0, 0),  # pack type LFP, cell maker Firm1
    0x1C04: (3400, 30, _t(0), _t(5), 5680, 2000),  # balance start V, delta, heater on/off, full-charge V/I
    0x1C76: (0,),  # CAN protocol
    0x1C78: (2900, 60, 0),  # sleep voltage, delay, balance mode
    0x1C7E: (0, 0, 0),  # RS485 protocol, CAN baud, RS485 baud
    0x1C84: (10, 120),  # standby sleep current, static sleep delay
    0x1C8A: (_t(45), _t(40)),  # fan on/off
    0x2000: (10000, 10000, 7600, 7600, 100, 37),  # design, full, remaining, SOC, SOH, cycles
    0x201A: (3000, 5, 3200, 20, 3300, 60, 3400, 95),  # SOC table (mV, %) x4
    0x202A: (560, 500, 460, 1000, 16, 250, 4, 3000, 2, 0, 0x0001),  # requests, cells, shunt, sensors, SC, SOC cfg, function bits
    0x2810: (1, 2, 2, 6, 0, 0x0011),  # boot, hw, app major, app minor, test, protocol
}
TEXTS = {0x281C: ("JBD-UP16S010", 16), 0x282C: ("ECOWORTHY48V", 16)}
CLOCK = datetime(2026, 10, 23, 8, 0, 0)
DEFAULT_FLOORS = {0x1C06: 20}  # keeps the old balance delta below 0.020 V, like a pack might


def _text(s: str, nbytes: int) -> list[int]:
    b = s.encode().ljust(nbytes, b"\0")
    return [b[i] << 8 | b[i + 1] for i in range(0, nbytes, 2)]


def _live(serial_cells: int = 16) -> bytes:
    cells = [3300 + i for i in range(serial_cells)]
    temps = [_t(24.0), _t(25.1), _t(24.8), _t(25.0)]
    head = struct.pack(
        ">HHI" + "H" * 8 + "II" + "H" * 18,  # 0x44 bytes: everything up to and including the cell count
        5283, 0, 300000 - 320,  # 52.83 V, (pad), -3.20 A
        7600, 7600, 10000, 10000,  # SOC 76.00 %, remaining, full, nominal (0.01 Ah)
        _t(31.2), _t(24.0), 2, 100,  # MOSFET, ambient, discharging, SOH
        0, 0,  # protection, alarm bits
        0b11, 0b10, 37,  # MOSFET bits (dsg+chg), signals (load), cycles
        16, 3315, 1, 3300, 3307,  # max cell idx/mV, min cell idx/mV, average
        2, _t(25.1), 1, _t(24.0), _t(24.8),  # max temp idx/val, min temp idx/val, average
        576, 500, 448, 1000,  # inverter limits: 57.6 V, 50.0 A, 44.8 V, 100.0 A
        serial_cells,
    )
    body = head + b"".join(struct.pack(">H", c) for c in cells)
    body += struct.pack(">H", len(temps)) + b"".join(struct.pack(">H", t) for t in temps)
    body += struct.pack(">I", 0)  # balancing bitmap
    return body.ljust(2 * R.LIVE.count, b"\0")
```

Check the `_live` packing format against the spec's live offsets before relying on it:
- pack voltage at 0x00, current u32 at 0x04, SOC 0x08, remaining 0x0A, full 0x0C, nominal 0x0E;
- MOSFET temperature 0x10, ambient 0x12, state 0x14, SOH 0x16;
- protection u32 at 0x18, alarm u32 at 0x1C, MOSFET bits 0x20, signals 0x22, cycles 0x24;
- 0x26–0x2E cells, 0x30–0x38 temperatures, 0x3A–0x40 inverter limits, cell count at 0x42.

The format string above has been checked: it is 0x44 bytes, so the cell count lands at 0x42. 0x02 is the pad word between voltage and current. If a test disagrees, the offsets above are the requirement.

Continue `sim.py` with the pack:

```python
class FakeJbd:
    def __init__(
        self,
        *,
        address: int = 0,
        serial: str = "SIM-PACK-1",
        large_capacity: bool = False,
        floors: dict[int, int] | None = None,
        key: int | None = None,
        protocol: int = 0x0011,
        state_file: Path | None = None,
    ):
        self.address = address
        self.port = "simulated pack"
        self.mem: dict[int, int] = {}
        for base, words in {**GROUPS, **WORDS}.items():
            for i, w in enumerate(words):
                self.mem[base + 2 * i] = w
        for reg, (text, n) in {**TEXTS, R.REG_PACK_SN: (serial, 30)}.items():
            for i, w in enumerate(_text(text, n)):
                self.mem[reg + 2 * i] = w
        self.mem[0x1C88] = address  # BMS software address (read-only here)
        self.mem[R.REG_PROTOCOL] = protocol
        self.key = (protocol & 0xFF or 0x11) if key is None else key  # the key this pack accepts
        self._set_clock(CLOCK)
        live = _live()
        for i in range(0, len(live), 2):
            self.mem[R.LIVE.start + i] = int.from_bytes(live[i : i + 2], "big")
        if large_capacity:
            self.mem[R.REG_SIGNALS] |= R.LARGE_CAPACITY
        self.floors = DEFAULT_FLOORS if floors is None else floors
        self.writes: list[tuple[int, list[int]]] = []
        self.blank = False
        self.state_file = state_file
        self._clock_base: tuple[datetime, datetime] | None = None
        if state_file and state_file.exists():
            for reg, v in json.loads(state_file.read_text()).get("registers", {}).items():
                self.mem[int(reg, 0)] = v

    def word(self, reg: int) -> int:
        return self.mem.get(reg, 0)

    def _set_clock(self, t: datetime) -> None:
        for i, v in enumerate((t.year, t.month, t.day, t.hour, t.minute, t.second)):
            self.mem[R.CLOCK.start + 2 * i] = v

    def _clock_now(self) -> None:
        if self._clock_base is not None:
            set_at, base = self._clock_base
            self._set_clock(base + (datetime.now() - set_at))

    def transact(self, request: bytes) -> bytes:
        from ...modbus import crc_ok

        if not crc_ok(request) or request[0] != self.address:
            raise NoReply("simulated pack did not answer")
        addr, fn, start, end, n = struct.unpack(">BBHHH", request[:8])
        if fn == FN_READ:
            self._clock_now()
            words = [0 if self.blank and R.PROTECTION.start <= a < 0x1900 else self.word(a) for a in range(start, end, 2)]
            data = b"".join(struct.pack(">H", w) for w in words)
            return with_crc(struct.pack(">BBHHH", addr, fn, start, end, len(data)) + data)
        if fn == FN_WRITE:
            body = request[8:-2]
            if len(body) != n or body[0] != self.key or body[1:4] != b"JBD":
                raise NoReply("simulated pack ignored a write with the wrong key")
            values = [struct.unpack(">H", body[4 + i : 6 + i])[0] for i in range(0, n - 4, 2)]
            self.writes.append((start, values))
            for i, v in enumerate(values):
                reg = start + 2 * i
                if reg in self.floors and v < self.floors[reg]:
                    continue  # acked, not stored
                self.mem[reg] = v
            if start == R.CLOCK.start:
                self._clock_base = (datetime.now(), datetime(*values[:6]))
            self._save()
            return with_crc(struct.pack(">BBHHH", addr, fn, start, end, 0))
        raise NoReply("simulated pack ignored an unknown function")

    def _save(self) -> None:
        if self.state_file:
            regs = {f"0x{r:04X}": v for r, v in sorted(self.mem.items()) if r >= 0x1800}
            self.state_file.write_text(json.dumps({"registers": regs}, indent=1))

    def close(self) -> None:
        pass


class FakeJbdBus:
    """Several simulated packs on one line; each answers only its own address."""

    def __init__(self, packs: list[FakeJbd]):
        self.packs = {p.address: p for p in packs}
        self.port = "simulated packs"
        self.timeout = 1.0

    def transact(self, request: bytes) -> bytes:
        pack = self.packs.get(request[0])
        if pack is None:
            raise NoReply("no simulated pack at that address")
        return pack.transact(request)

    def close(self) -> None:
        pass
```

- [ ] **Step 6: Run the tests.**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen /Users/rar/eg4_tool/.venv/bin/pytest tests/test_jbd_up.py -q`
Expected: PASS. Then run the full suite and lint.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "JBD-UP framing (0x78/0x79, JBD write key) and a simulated pack"
```

---

### Task 6: The JBD-UP driver

**Files:**
- Create: `src/packkeeper/drivers/jbd_up/table.py`, `layout.py`, `live.py`, `driver.py`; fill in `__init__.py`
- Modify: `src/packkeeper/drivers/__init__.py`, `tests/test_drivers.py` (the boundary test covers `jbd_up`), `tests/test_layout.py` (already parametrized over every registered driver)
- Test: `tests/test_jbd_up.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `packkeeper.drivers.jbd_up.DRIVER`, a `JbdUp()` with `name = "jbd-up"` and `label = "ECO-WORTHY / JBD-UP"`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_jbd_up.py`:

```python
from datetime import datetime  # noqa: E402

from packkeeper import backup, drivers  # noqa: E402
from packkeeper.block import BlankBlockError  # noqa: E402
from packkeeper.device import PackError  # noqa: E402
from packkeeper.planner import identity_warnings, make_plan  # noqa: E402
from packkeeper.service import CONFIRMED, NOT_APPLIED, apply_plan, set_clock  # noqa: E402
from packkeeper.session import SessionLog  # noqa: E402


def jbd(**kw):
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd

    sim = FakeJbd(**kw)
    return sim, DRIVER.open_device(sim, SessionLog(), kw.get("address", 0))


def test_registered_with_its_tables():
    from packkeeper.drivers.jbd_up import DRIVER as D

    assert drivers.get("jbd-up") is D and D.serial.baud == 9600 and D.broadcast_address is None
    t = D.table
    assert t["cell_ov_protection"].register == 0x1806 and t["cell_ov_protection"].unit == "V"
    assert t["charge_ot_protection"].format(t["charge_ot_protection"].parse("55")) == "55.0 °C"
    assert t["charge_ot_protection"].parse("55") == 1050
    assert set(t.unsafe_keys) == {
        "cells_in_series", "shunt_resistance", "short_circuit_current", "short_circuit_delay_step",
        "pack_type", "cell_maker", "soc_config_bits", "function_bits", "can_protocol", "rs485_protocol", "can_baud",
    }
    for reg in (0x290C, 0x290E, 0x2910, 0x2914, 0x2100, 0x1C82, 0x1C88, 0x2004, 0x2006, 0x200A, 0x1C34, 0x2800):
        assert not D.is_writable(reg), hex(reg)


def test_read_decodes_identity_and_settings():
    _sim, dev = jbd()
    b = dev.read_block()
    assert dev.write_refusal is None and dev.framing.key == 0x11
    assert b.model == "JBD-UP16S010" and b.serial == "SIM-PACK-1"
    assert b.firmware == "app 2.6 (hw 2, protocol 1.1)"
    assert b.clock == datetime(2026, 10, 23, 8, 0, 0)
    assert b.raw("cell_ov_protection") == 3650 and b.raw("charge_oc1_protection") == 1050
    assert b.raw("pack_type") == 0 and b.driver.table["pack_type"].format(0) == "LFP"


def test_live_decode():
    _sim, dev = jbd()
    rt = dev.read_live()
    assert rt.cell_count == 16 and rt.cells[0] == 3.3 and rt.pack_voltage == 52.83
    assert rt.current == -3.2 and rt.soc == 76 and rt.soh == 100 and rt.cycles == 37
    assert ("MOSFET", 31) in rt.temps and rt.max_temp == 25
    assert rt.status_text == "Discharging" and rt.charge_allowed and rt.discharge_allowed
    assert rt.remaining_ah == 76.0 and rt.full_capacity_ah == 100.0


def test_write_confirmed_with_the_key_byte():
    sim, dev = jbd()
    p = make_plan(dev.read_block(), {"cell_ov_protection": 3600})
    assert p.ok, p.errors
    assert p.frames(dev.address, dev.framing)[0][:12].hex(" ").upper() == "00 79 18 06 18 08 00 06 11 4A 42 44"
    res = apply_plan(dev, p)
    assert res.ok and res.results[0].status == CONFIRMED and sim.writes == [(0x1806, [3600])]


def test_wrong_key_is_not_applied():
    sim, dev = jbd(key=0x13)  # the pack wants 0x13 but reports protocol 0x11
    res = apply_plan(dev, make_plan(dev.read_block(), {"cell_ov_protection": 3600}))
    assert res.results[0].status == NOT_APPLIED and sim.word(0x1806) == 3650


def test_dropped_write_is_not_applied():
    _sim, dev = jbd()
    res = apply_plan(dev, make_plan(dev.read_block(), {"balance_start_delta": 10}))
    assert res.results[0].status == NOT_APPLIED


def test_rules_and_unsafe_gate():
    _sim, dev = jbd()
    b = dev.read_block()
    assert not make_plan(b, {"cell_ov_alarm": 3700}).ok  # above its range and above protection 3650
    assert not make_plan(b, {"cell_ov_protection_recovery": 3650}).ok  # must stay below protection
    assert not make_plan(b, {"cells_in_series": 15}).ok
    assert make_plan(b, {"cells_in_series": 15}, allow_unsafe=True).ok


def test_large_capacity_turns_writes_off():
    _sim, dev = jbd(large_capacity=True)
    dev.read_block()
    assert dev.write_refusal and "large-capacity" in dev.write_refusal
    with pytest.raises(PackError):
        dev.write_setting(dev.driver.table["cell_ov_protection"], 3600)


def test_blank_and_implausible_reads_are_refused():
    sim, dev = jbd()
    sim.blank = True
    with pytest.raises(BlankBlockError):
        dev.read_block()
    sim, dev = jbd()
    sim.mem[0x2032] = 0
    with pytest.raises(BlankBlockError, match="cells in series"):
        dev.read_block()


def test_protocol_zero_warns_and_uses_the_default_key():
    _sim, dev = jbd(protocol=0)
    b = dev.read_block()
    assert dev.framing.key == 0x11 and dev.driver.identity_warnings(b)


def test_clock_set_and_read_back():
    sim, dev = jbd()
    res = set_clock(dev, datetime(2026, 10, 24, 9, 30, 0))
    assert res.ok and sim.writes[-1][0] == 0x2800 and len(sim.writes[-1][1]) == 6


def test_backup_round_trip_and_serial_warning(tmp_path):
    _sim, dev_a = jbd(serial="PACK-A")
    _sim, dev_b = jbd(serial="PACK-B")
    a, b = dev_a.read_block(), dev_b.read_block()
    f = tmp_path / "a.json"
    backup.save(f, a)
    assert backup.load(f).block == a
    assert any("PACK-A" in w and "PACK-B" in w for w in identity_warnings(b, a, "the backup"))


def test_find_on_a_bus():
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd, FakeJbdBus
    from packkeeper.find import scan

    bus = FakeJbdBus([FakeJbd(address=a, serial=f"P{a}") for a in (0, 2, 5)])
    found = scan(DRIVER, bus, DRIVER.find_addresses, SessionLog())
    assert [(f.address, f.serial) for f in found] == [(0, "P0"), (2, "P2"), (5, "P5")]


def test_cli_info_and_dry_run(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["info", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "JBD-UP16S010" in out and "SIM-PACK-1" in out
    assert main(["set", "--bms", "jbd-up", "--simulate", "--dry-run", "--address", "0", "--log-dir", str(tmp_path),
                 "cell_ov_protection", "3.6"]) == 0
    out = capsys.readouterr().out
    assert "00 79 18 06 18 08 00 06 11 4A 42 44 0E 10" in out and "broadcast" not in out
```

- [ ] **Step 2: Run them and confirm they fail.** Expected: ImportError (`DRIVER`).

- [ ] **Step 3: Create `src/packkeeper/drivers/jbd_up/table.py`.** It builds the settings from compact group data:

```python
"""JBD-UP settings: the 0x1800 protection groups, pack and capacity settings,
and the unsafe ones. Ranges are this tool's proposals for 16S LiFePO4 with a
100 A BMS; compare them with the pack and the stock app before the first write.
"""

from __future__ import annotations

from ...settings import SettingTable
from .registers import STRIDE

DELAY = ("ms", 1, 0, 0, (100, 60000, 100))  # unit, multiplier, offset, decimals, limits
TEMP = ("°C", 0.1, -50, 1)
SIX = ("alarm", "alarm_recovery", "alarm_delay", "protection", "protection_recovery", "protection_delay")

# name: (base, (unit, mult, offset, decimals), high limit?, (lo, hi, step) for alarm/protection, (lo, hi, step) for recoveries, label)
GROUPS = {
    "cell_ov": (0x1800, ("V", 0.001, 0, 3), True, (3.4, 3.65, 0.001), (3.3, 3.6, 0.001), "Cell OV"),
    "cell_uv": (0x180C, ("V", 0.001, 0, 3), False, (2.5, 3.1, 0.001), (2.6, 3.2, 0.001), "Cell UV"),
    "pack_ov": (0x1818, ("V", 0.01, 0, 2), True, (54.4, 58.4, 0.01), (52.8, 57.6, 0.01), "Pack OV"),
    "pack_uv": (0x1824, ("V", 0.01, 0, 2), False, (40.0, 49.6, 0.01), (41.6, 51.2, 0.01), "Pack UV"),
    "charge_ot": (0x1858, TEMP, True, (30, 70, 0.1), (25, 65, 0.1), "Charge OT"),
    "charge_ut": (0x1864, TEMP, False, (-10, 10, 0.1), (-5, 15, 0.1), "Charge UT"),
    "discharge_ot": (0x1870, TEMP, True, (40, 80, 0.1), (35, 75, 0.1), "Discharge OT"),
    "discharge_ut": (0x187C, TEMP, False, (-30, 0, 0.1), (-25, 5, 0.1), "Discharge UT"),
    "mos_ot": (0x1888, TEMP, True, (60, 110, 0.1), (50, 100, 0.1), "MOSFET OT"),
    "ambient_ot": (0x1894, TEMP, True, (40, 80, 0.1), (35, 75, 0.1), "Ambient OT"),
    "ambient_ut": (0x18A0, TEMP, False, (-30, 0, 0.1), (-25, 5, 0.1), "Ambient UT"),
    "temp_diff": (0x18AC, ("Δ°C", 0.1, 0, 1), True, (2, 30, 0.1), (1, 29, 0.1), "Temperature diff"),
    "cell_diff": (0x18B8, ("V", 0.001, 0, 3), True, (0.05, 1.0, 0.001), (0.03, 0.99, 0.001), "Cell diff"),
    "soc_low": (0x18C4, ("%", 1, 0, 0), False, (0, 50, 1), (1, 60, 1), "SOC low"),
}
OC1 = ("alarm", "alarm_recovery", "alarm_delay", "protection", "protection_delay", "release_delay", "lockout_count")
OC2 = ("protection", "delay", "release_delay")
CURRENT = ("A", 0.1, 0, 1)
OC_GROUPS = {
    "charge_oc1": (0x1830, OC1, (1, 105, 0.1), "Charge OC 1"),
    "charge_oc2": (0x183E, OC2, (1, 150, 0.1), "Charge OC 2"),
    "discharge_oc1": (0x1844, OC1, (1, 105, 0.1), "Discharge OC 1"),
    "discharge_oc2": (0x1852, OC2, (1, 150, 0.1), "Discharge OC 2"),
}

# key: (register, (unit, mult, offset, decimals), (lo, hi, step), label)
SINGLE = {
    "balance_start_voltage": (0x1C04, ("V", 0.001, 0, 3), (3.0, 3.5, 0.001), "Balance start voltage"),
    "balance_start_delta": (0x1C06, ("V", 0.001, 0, 3), (0.005, 0.1, 0.001), "Balance start delta"),
    "heater_on": (0x1C08, TEMP, (-20, 15, 0.1), "Heater on"),
    "heater_off": (0x1C0A, TEMP, (-15, 20, 0.1), "Heater off"),
    "full_charge_voltage": (0x1C0C, ("V", 0.01, 0, 2), (52.0, 58.4, 0.01), "Full-charge calibration voltage"),
    "full_charge_current": (0x1C0E, ("A", 0.001, 0, 3), (0.1, 20.0, 0.001), "Full-charge calibration current"),
    "sleep_voltage": (0x1C78, ("V", 0.001, 0, 3), (2.5, 3.2, 0.001), "Sleep voltage"),
    "sleep_delay": (0x1C7A, ("min", 1, 0, 0), (1, 1440, 1), "Sleep delay"),
    "balance_mode": (0x1C7C, (None, 1, 0, 0), (0, 1, 1), "Balance mode"),
    "standby_sleep_current": (0x1C84, ("A", 0.1, 0, 1), (0.1, 5.0, 0.1), "Standby sleep current"),
    "static_sleep_delay": (0x1C86, ("min", 1, 0, 0), (1, 1440, 1), "Static sleep delay"),
    "fan_on": (0x1C8A, TEMP, (30, 70, 0.1), "Fan on"),
    "fan_off": (0x1C8C, TEMP, (25, 65, 0.1), "Fan off"),
    "design_capacity": (0x2000, ("Ah", 0.01, 0, 2), (10, 600, 0.01), "Design capacity"),
    "full_capacity": (0x2002, ("Ah", 0.01, 0, 2), (10, 600, 0.01), "Full capacity"),
    "charge_request_voltage": (0x202A, ("V", 0.1, 0, 1), (48.0, 58.4, 0.1), "Charge request voltage"),
    "charge_request_current": (0x202C, ("A", 0.1, 0, 1), (1, 100, 0.1), "Charge request current"),
    "discharge_request_voltage": (0x202E, ("V", 0.1, 0, 1), (40.0, 52.0, 0.1), "Discharge request voltage"),
    "discharge_request_current": (0x2030, ("A", 0.1, 0, 1), (1, 100, 0.1), "Discharge request current"),
    # unsafe (notes below)
    "cells_in_series": (0x2032, (None, 1, 0, 0), (1, 32, 1), "Cells in series"),
    "shunt_resistance": (0x2034, ("mΩ", 0.001, 0, 3), (0.05, 5.0, 0.001), "Shunt resistance"),
    "short_circuit_current": (0x2038, (None, 1, 0, 0), (0, 65535, 1), "Short-circuit current (raw)"),
    "short_circuit_delay_step": (0x203A, (None, 1, 0, 0), (0, 65535, 1), "Short-circuit delay step (raw)"),
    "pack_type": (0x1C00, (None, 1, 0, 0), (0, 1, 1), "Pack type"),
    "cell_maker": (0x1C02, (None, 1, 0, 0), (0, 2, 1), "Cell maker"),
    "soc_config_bits": (0x203C, (None, 1, 0, 0), (0, 0xFFFF, 1), "SOC config bits"),
    "function_bits": (0x203E, (None, 1, 0, 0), (0, 0xFFFF, 1), "Function bits"),
    "can_protocol": (0x1C76, (None, 1, 0, 0), (0, 15, 1), "CAN inverter protocol"),
    "rs485_protocol": (0x1C7E, (None, 1, 0, 0), (0, 15, 1), "RS485 inverter protocol"),
    "can_baud": (0x1C80, (None, 1, 0, 0), (0, 3, 1), "CAN baud"),
}
for i in range(4):
    SINGLE[f"soc_table_voltage_{i + 1}"] = (0x201A + 4 * i, ("V", 0.001, 0, 3), (2.5, 3.65, 0.001), f"SOC table voltage {i + 1}")
    SINGLE[f"soc_table_percent_{i + 1}"] = (0x201C + 4 * i, ("%", 1, 0, 0), (0, 100, 1), f"SOC table percent {i + 1}")

UNSAFE = {
    "cells_in_series": "A wrong count breaks every cell and pack protection.",
    "shunt_resistance": "Scales every current reading, and over-current protection with it.",
    "short_circuit_current": "The last line of protection; the unit is not published.",
    "short_circuit_delay_step": "The last line of protection; the unit is not published.",
    "pack_type": "Changes the chemistry model behind every curve.",
    "cell_maker": "Changes the cell model.",
    "soc_config_bits": "Changes how SOC is learned.",
    "function_bits": "Anti-theft can lock the pack out.",
    "can_protocol": "The wrong protocol cuts off the inverter.",
    "rs485_protocol": "The wrong protocol cuts off the inverter.",
    "can_baud": "The wrong baud cuts off the inverter.",
}
CHOICES = {
    "balance_mode": {0: "charge only", 1: "static"},
    "pack_type": {0: "LFP", 1: "NCM"},
    "cell_maker": {0: "Firm1", 1: "Firm2", 2: "Firm3"},
    "can_protocol": dict(enumerate([
        "Pylon", "Growatt", "GoodWe", "Sofar", "Victron", "Riyueyuan / Sanke", "Pengcheng", "Deye",
        "Solis", "SMA", "Nanjing Oulu", "SRNE", "INVT", "Sorotec", "MUST", "EASUN",
    ])),
    "rs485_protocol": dict(enumerate([
        "Pylon", "Growatt", "Riyueyuan", "Pengcheng", "Deye", "INVT", "SRNE", "IY-Power",
        "SMK", "Peicheng", "EPEver", "Sanke", "extension 6 / Nanjing Oulu", "extension 7", "extension 8", "extension 9",
    ])),
    "can_baud": {0: "500K", 1: "100K", 2: "125K", 3: "250K"},
}
BITS = {
    "soc_config_bits": {0x0005: "no capacity learning", 0x0050: "disable SOC curve"},
    "function_bits": {0x0001: "heater", 0x0004: "buzzer", 0x0010: "anti-theft", 0x0040: "GPS", 0x0100: "4-digit SOC", 0x0400: "LED board"},
}


def _build():
    spec, limits, labels, groups, order = {}, {}, {}, {}, []
    for name, (base, (unit, mult, off, dec), high, main, rec, label) in GROUPS.items():
        for i, field in enumerate(SIX):
            key = f"{name}_{field}"
            if field.endswith("delay"):
                u, m, o, d, lim = DELAY
                spec[key], limits[key] = (base + 2 * i, m, o, u, None, d), lim
            else:
                spec[key] = (base + 2 * i, mult, off, unit, None, dec)
                limits[key] = rec if field.endswith("recovery") else main
            labels[key], groups[key] = f"{label} {field.replace('_', ' ')}", label
        a, ar, p, pr = (f"{name}_{f}" for f in ("alarm", "alarm_recovery", "protection", "protection_recovery"))
        if high:
            order += [(ar, a, True), (a, p, False), (pr, p, True)]
        else:
            order += [(p, a, False), (a, ar, True), (p, pr, True)]
    for name, (base, fields, main, label) in OC_GROUPS.items():
        for i, field in enumerate(fields):
            key = f"{name}_{field}"
            if field.endswith("delay"):
                u, m, o, d, lim = DELAY
                spec[key], limits[key] = (base + 2 * i, m, o, u, None, d), lim
            elif field == "lockout_count":
                spec[key], limits[key] = (base + 2 * i, 1, 0, None, None, 0), (0, 10, 1)
            else:
                unit, mult, off, dec = CURRENT
                spec[key], limits[key] = (base + 2 * i, mult, off, unit, None, dec), main
            labels[key], groups[key] = f"{label} {field.replace('_', ' ')}", label
    for side in ("charge", "discharge"):
        order += [
            (f"{side}_oc1_alarm_recovery", f"{side}_oc1_alarm", True),
            (f"{side}_oc1_alarm", f"{side}_oc1_protection", False),
            (f"{side}_oc1_protection", f"{side}_oc2_protection", True),
        ]
    order += [
        ("cell_uv_protection", "cell_ov_protection", True),
        ("pack_uv_protection", "pack_ov_protection", True),
        ("heater_on", "heater_off", True),
        ("fan_off", "fan_on", True),
        ("full_capacity", "design_capacity", False),
    ]
    for i in range(1, 4):
        order += [
            (f"soc_table_voltage_{i}", f"soc_table_voltage_{i + 1}", True),
            (f"soc_table_percent_{i}", f"soc_table_percent_{i + 1}", True),
        ]
    for key, (reg, (unit, mult, off, dec), lim, label) in SINGLE.items():
        spec[key], limits[key], labels[key] = (reg, mult, off, unit, None, dec), lim, label
        groups[key] = "Unsafe" if key in UNSAFE else "Pack"
    return SettingTable.build(
        spec, limits, order, group=groups.__getitem__, label=labels.__getitem__,
        stride=STRIDE, unsafe=UNSAFE, choices=CHOICES, bits=BITS,
    )


TABLE = _build()
```

`Δ°C` is deliberately not `°C`. A temperature *difference* must not get the +32 of a °F conversion, and `Setting.is_temperature` is `unit == "°C"`.

- [ ] **Step 4: Create `src/packkeeper/drivers/jbd_up/layout.py`.** It holds six `Page`s. Every key in `TABLE` must appear exactly once, which `tests/test_layout.py` checks.

```python
"""How the JBD-UP settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, Page, Section
from .table import GROUPS, OC_GROUPS

APR = ("Alarm", "Protection", "Recovery")
COLS = ("Alarm", "Alarm recovery", "Alarm delay (ms)", "Protection", "Protection recovery", "Protection delay (ms)")


def _six(title, names, unit):
    return Section(title, COLS, tuple((GROUPS[n][5], tuple(f"{n}_{f}" for f in (
        "alarm", "alarm_recovery", "alarm_delay", "protection", "protection_recovery", "protection_delay"))) for n in names), unit=unit)


def _oc(name):
    _base, fields, _lim, label = OC_GROUPS[name]
    return Section(label, tuple(f.replace("_", " ").capitalize() for f in fields), ((label, tuple(f"{name}_{f}" for f in fields)),))


def _one(title, rows, unit=None):
    return Section(title, ("Value",), tuple((label, (key,)) for label, key in rows), unit=unit)


PAGES = (
    Page("Voltage", (_six("Cell", ("cell_ov", "cell_uv"), "V"), _six("Pack", ("pack_ov", "pack_uv"), "V")),
         "High limits: recovery < alarm ≤ protection. Low limits mirrored. Delays in milliseconds."),
    Page("Current", tuple(_oc(n) for n in OC_GROUPS)),
    Page("Temperature", (
        _six("Charge / discharge", ("charge_ot", "charge_ut", "discharge_ot", "discharge_ut"), TEMP),
        _six("MOSFET / ambient", ("mos_ot", "ambient_ot", "ambient_ut"), TEMP),
        _six("Difference", ("temp_diff",), None),
    )),
    Page("Balancing & sleep", (
        _six("Cell difference", ("cell_diff",), "V"),
        _one("Balancing", (("Start voltage (V)", "balance_start_voltage"), ("Start delta (V)", "balance_start_delta"),
                           ("Mode", "balance_mode"))),
        _one("Heater and fan", (("Heater on", "heater_on"), ("Heater off", "heater_off"),
                                ("Fan on", "fan_on"), ("Fan off", "fan_off")), TEMP),
        _one("Sleep", (("Sleep voltage (V)", "sleep_voltage"), ("Sleep delay (min)", "sleep_delay"),
                       ("Standby sleep current (A)", "standby_sleep_current"), ("Static sleep delay (min)", "static_sleep_delay"))),
        _one("Full-charge calibration", (("Voltage (V)", "full_charge_voltage"), ("Current (A)", "full_charge_current"))),
    )),
    Page("Capacity & inverter", (
        _six("SOC low", ("soc_low",), "%"),
        _one("Capacity (Ah)", (("Design", "design_capacity"), ("Full", "full_capacity"))),
        Section("SOC table", ("Voltage (V)", "SOC (%)"),
                tuple((f"Point {i}", (f"soc_table_voltage_{i}", f"soc_table_percent_{i}")) for i in range(1, 5))),
        _one("Inverter requests", (("Charge voltage (V)", "charge_request_voltage"), ("Charge current (A)", "charge_request_current"),
                                   ("Discharge voltage (V)", "discharge_request_voltage"), ("Discharge current (A)", "discharge_request_current"))),
    )),
    Page("Unsafe", (
        _one("Pack hardware", (("Cells in series", "cells_in_series"), ("Shunt (mΩ)", "shunt_resistance"),
                               ("Short-circuit current (raw)", "short_circuit_current"),
                               ("Short-circuit delay step (raw)", "short_circuit_delay_step"),
                               ("Pack type", "pack_type"), ("Cell maker", "cell_maker"))),
        _one("Functions", (("SOC config bits", "soc_config_bits"), ("Function bits", "function_bits"))),
        _one("Inverter link", (("CAN protocol", "can_protocol"), ("RS485 protocol", "rs485_protocol"), ("CAN baud", "can_baud"))),
    ), "Each of these can stop the pack measuring, protecting or talking correctly. Keep a backup.", unsafe=True),
)
```

If the settings view's grid cannot take a section with six columns, say so in the report as DONE_WITH_CONCERNS, and don't split the layout on your own. EG4's matrices already use three.

- [ ] **Step 5: Create `src/packkeeper/drivers/jbd_up/live.py`:**

```python
"""Decoding the JBD-UP live block (0x1000-0x10A0)."""

from __future__ import annotations

import struct

from ...live import Live, decode_bits

PROTECTION_BITS = [
    "cell OV", "cell UV", "pack OV", "pack UV", "charge OC1", "charge OC2", "discharge OC1", "discharge OC2",
    "charge OT", "charge UT", "discharge OT", "discharge UT", "MOSFET OT", "ambient OT", "ambient UT", "cell diff",
    "temp diff", "SOC low", "short circuit", "cell disconnected", "temp sensor disconnected", "charge MOSFET fault",
    "discharge MOSFET fault", "current-limit fault", "aerosol fault", "full-charge stop", "AFE comm fault",
    "reverse polarity", "comm anti-theft lock", "gyro anti-theft lock", "charge OC lockout", "discharge OC lockout",
]
ALARM_BITS = [
    "cell OV", "cell UV", "pack OV", "pack UV", "charge OC", "discharge OC", "charge OT", "charge UT", "discharge OT",
    "discharge UT", "MOSFET OT", "ambient OT", "ambient UT", "cell diff", "temp diff", "SOC low", "EEPROM fault",
    "RTC fault", "battery full",
]
IO_BITS = ["discharge MOSFET", "charge MOSFET", "precharge", "heater", "fan", "dry contact 1", "dry contact 2", "current limiter"]
STATES = {0: "Standby", 1: "Charging", 2: "Discharging"}


def _names(names: list[str]) -> dict[int, str]:
    return {1 << i: n for i, n in enumerate(names)}


def decode(regs: list[int]) -> Live:
    data = b"".join(struct.pack(">H", r) for r in regs)

    def u16(off: int) -> int:
        return struct.unpack(">H", data[off : off + 2])[0] if off + 2 <= len(data) else 0

    def u32(off: int) -> int:
        return u16(off) << 16 | u16(off + 2)

    def temp(raw: int) -> int:
        return round((raw - 500) / 10)

    n = min(u16(0x42), 32)
    cells = [u16(0x44 + 2 * i) / 1000 for i in range(n) if 0x44 + 2 * i + 2 <= len(data)]
    off = 0x44 + 2 * u16(0x42)
    m = min(u16(off), 16)
    sensors = [temp(u16(off + 2 + 2 * i)) for i in range(m)]
    off += 2 + 2 * u16(off)
    balancing = u32(off)
    io = u16(0x20)
    signals = u16(0x22)
    temps = [("MOSFET", temp(u16(0x10))), ("ambient", temp(u16(0x12)))] + [(f"sensor {i + 1}", t) for i, t in enumerate(sensors)]
    extra = [
        ("I/O", ", ".join(name for bit, name in _names(IO_BITS).items() if io & bit and bit > 2) or "none"),
        ("Signals", ", ".join(n for b, n in ((1, "charger"), (2, "load"), (4, "button")) if signals & b) or "none"),
        ("Inverter", f"charge ≤ {u16(0x3A) / 10:.1f} V / {u16(0x3C) / 10:.1f} A, "
                     f"discharge ≥ {u16(0x3E) / 10:.1f} V / {u16(0x40) / 10:.1f} A"),
    ]
    return Live(
        pack_voltage=u16(0x00) / 100,
        current=round(u32(0x04) * 0.01 - 3000, 2),
        soc=round(u16(0x08) / 100),
        soh=u16(0x16),
        cells=cells,
        status_text=STATES.get(u16(0x14), f"state {u16(0x14)}"),
        temps=temps,
        max_temp=max(sensors) if sensors else None,
        warnings=decode_bits(u32(0x1C), _names(ALARM_BITS)),
        protections=decode_bits(u32(0x18), _names(PROTECTION_BITS)),
        charge_allowed=bool(io & 2),
        discharge_allowed=bool(io & 1),
        balancing_cells=[i + 1 for i in range(32) if balancing & (1 << i)],
        remaining_ah=u16(0x0A) / 100,
        full_capacity_ah=u16(0x0C) / 100,
        design_capacity_ah=u16(0x0E) / 100,
        cycles=u16(0x24),
        extra=extra,
        registers=list(regs),
    )
```

- [ ] **Step 6: Create `src/packkeeper/drivers/jbd_up/driver.py`:**

```python
"""The JBD-UP driver: ECO-WORTHY rack packs with JBD "ES/UP" BMS boards, RS485
at 9600 8N1 with JBD's 0x78/0x79 framing, one pack at a time."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...block import Block, Identity, ascii_text
from ...device import READ_RETRIES
from ...errors import DeviceRefused, FrameError
from ...transport import NoReply, SerialParams
from ..base import Driver
from . import live, registers as R
from .framing import DEFAULT_KEY, JbdFraming
from .layout import PAGES
from .table import TABLE


class JbdUp(Driver):
    name = "jbd-up"
    label = "ECO-WORTHY / JBD-UP"
    table = TABLE
    areas = R.AREAS
    serial = SerialParams(baud=9600)
    address = 0
    address_choices = range(0, 64)
    framing = JbdFraming()
    broadcast_address = None  # 0 is an ordinary address in this protocol
    find_addresses = range(0, 16)
    find_probe = R.VERSION
    serial_is_unique = True
    pages = PAGES
    live_reads = (R.LIVE,)
    supports_clock = True
    connect_note_html = R.CONNECT_NOTE_HTML
    connection_help = R.CONNECTION_HELP
    no_reply_hint = R.NO_REPLY_HINT
    setup_hint = R.SETUP_HINT
    clock_note = R.CLOCK_NOTE

    _writable = frozenset(TABLE.covering)

    def new_framing(self):
        return JbdFraming()

    def is_writable(self, register: int) -> bool:
        return register in self._writable

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    @staticmethod
    def key_of(block: Block) -> int:
        return block.reg(R.REG_PROTOCOL) & 0xFF or DEFAULT_KEY

    def identity(self, block: Block) -> Identity:
        model = ascii_text(block.regs(R.REG_HW_NAME, 8)) or ascii_text(block.regs(R.REG_PROJECT, 8))
        p = block.reg(R.REG_PROTOCOL) & 0xFF
        firmware = (f"app {block.reg(R.REG_APP_MAJOR)}.{block.reg(R.REG_APP_MINOR)} "
                    f"(hw {block.reg(R.REG_HW_VER)}, protocol {p >> 4}.{p & 0xF})")
        try:
            clock = datetime(*(block.reg(R.CLOCK.start + 2 * i) for i in range(6)))
        except ValueError:
            clock = None
        return Identity(model=model, firmware=firmware, serial=ascii_text(block.regs(R.REG_PACK_SN, 15)), clock=clock)

    def blank_reason(self, block: Block) -> str | None:
        # Replaces the generic all-zero check: the protection area is what matters here.
        if not any(block.areas[0]):
            return "the pack answered with every protection threshold zero; refusing to use this read"
        ov = block.reg(R.REG_CELL_OV_PROTECT)
        if not 3000 <= ov <= 4500:
            return f"cell OV protection reads as {ov} mV, outside 3000-4500 mV; refusing to use this read"
        cells = block.reg(R.REG_CELLS_IN_SERIES)
        if not 1 <= cells <= 32:
            return f"cells in series reads as {cells}, outside 1-32; refusing to use this read"
        return None

    def check_device(self, device, block: Block) -> str | None:
        device.framing.key = self.key_of(block)
        try:
            (signals,) = device.read(R.REG_SIGNALS, 1, retries=READ_RETRIES)
        except (NoReply, FrameError, DeviceRefused) as e:
            return f"could not read the pack's capacity mode ({e})"
        if signals & R.LARGE_CAPACITY:
            return "this pack reports large-capacity mode (capacities in 0.1 Ah); this build models 0.01 Ah"
        return None

    def identity_warnings(self, block: Block) -> list[str]:
        version = block.areas[R.AREAS.index(R.VERSION)]
        if not any(version) or block.reg(R.REG_PROTOCOL) & 0xFF == 0:
            return [f"the pack reports no protocol version; writes use the default key 0x{DEFAULT_KEY:02X}"]
        return []

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        return R.CLOCK.start, [t.year, t.month, t.day, t.hour, t.minute, t.second]

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        return [
            ("Address", f"{block.reg(0x1C88)} (software), RS485 baud code {block.reg(0x1C82)}"),
            ("SOC/SOH", f"{block.reg(0x2006) / 100:.0f} % / {block.reg(0x2008)} %, {block.reg(0x200A)} cycles"),
        ]

    def simulator(self, state_file: Path | None = None):
        from .sim import FakeJbd, FakeJbdBus

        packs = [FakeJbd(address=0, serial="SIM-PACK-1", state_file=state_file),
                 FakeJbd(address=2, serial="SIM-PACK-2"), FakeJbd(address=5, serial="SIM-PACK-3")]
        return FakeJbdBus(packs)
```

`check_device` sets the key on the device's own framing, so dry-run frames and writes always carry the key from the last read.

- [ ] **Step 7: Register it.**

`src/packkeeper/drivers/jbd_up/__init__.py`:

```python
from .driver import JbdUp

DRIVER = JbdUp()
```

`src/packkeeper/drivers/__init__.py`: import `from .jbd_up import DRIVER as _JBD_UP` and add it to the tuple in `DRIVERS`.

`tests/test_drivers.py`: the boundary test's tuple gains `"jbd_up"`.

- [ ] **Step 8: Run the JBD tests, then the full suite and lint.** If the live-decode test fails, read Task 5 Step 5's note on offsets. The offsets in the spec are the requirement.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "ECO-WORTHY / JBD-UP driver: protection, pack and capacity settings, unsafe tier, clock, live"
```

---

### Task 7: GUI test, README, parity, CI

**Files:**
- Modify: `tests/test_gui.py` (append), `README.md`, `docs/superpowers/specs/2026-10-23-jbd-up-driver-design.md` (status line)
- Scratch, not committed: `/private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity.py`, which already exists

- [ ] **Step 1: GUI test.** Append to `tests/test_gui.py`:

```python
def test_gui_runs_jbd_up_with_find(window, monkeypatch):
    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    assert not w.address.isHidden() and w.address.value() == 0 and not w.find_btn.isHidden()
    assert not w.settings.unsafe_box.isHidden()
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    monkeypatch.setattr(gui.QInputDialog, "getItem", lambda *a, **k: (a[3][1], True))  # the second pack found
    w.find_btn.click()
    wait(app, lambda: w.address.value() == 2)
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    assert "SIM-PACK-2" in w.identity.text() + w.identity.toolTip() or w.settings.block.serial == "SIM-PACK-2"
    tab = w.settings
    w.tabs.setCurrentWidget(tab)
    for i in range(tab.nav.count()):
        if not tab.nav.isRowHidden(i):
            tab.nav.setCurrentRow(i)
            app.processEvents()
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in tab.staged)
    dlg = open_review(app, w, monkeypatch)
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.ok
```

Before relying on `a[3][1]`, check how `MainWindow`'s Find handler calls `QInputDialog.getItem`, then index the items argument accordingly. The test picks the second pack found, which is address 2 in the simulator. If the connect bar does not show the serial, assert `w.settings.block.serial == "SIM-PACK-2"` alone, and drop the identity text check.

- [ ] **Step 2: README.** Add a section after the JK-PB one:

```markdown
## ECO-WORTHY / JBD-UP (48 V rack)

`--bms jbd-up` (or **BMS: ECO-WORTHY / JBD-UP**) talks to ECO-WORTHY rack packs
with JBD "ES/UP" BMS boards over RS485 at 9600 baud, one pack at a time with the
link cables unplugged. The address is the pack's DIP value; `packkeeper find
--bms jbd-up` (or **Find…**) lists the addresses that answer, with each pack's
serial number.

The protection map comes from notes reverse engineered from JBD's ES-UP app,
cross-checked against syssi/esphome-jbd-bms and the PhracturedBlue ECO-WORTHY
notes. The ranges are this tool's proposals for 16S LiFePO4 with a 100 A BMS:
compare them with the stock app before the first write.

Unsafe settings (cells in series, shunt, short circuit, pack type, function and
anti-theft bits, inverter protocols, CAN baud) are hidden unless you tick
**Show unsafe settings** or pass `--unsafe`, and each write of one is confirmed
by typing `unsafe`. Restore defaults, factory reset and calibration are not in
this tool.
```

Add `--unsafe` to the "Common options" list: "`--unsafe` allows changing unsafe settings (BMS types that have them); each is confirmed by typing `unsafe`."

- [ ] **Step 3: Parity.** Edit the scratch parity script so `BRANCH = Path("/Users/rar/.config/superpowers/worktrees/eg4_tool/jbd-up")`, then run it:
`/Users/rar/eg4_tool/.venv/bin/python /private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity.py`
Expected: `17 scenarios, 0 difference(s)`. Report the output in full. Any difference is a finding, unless it is purely the package name, a path, or the backup's `created` time, which the script already normalises.

- [ ] **Step 4: Spec status.** Change the spec's status line to `Status: implemented on branch jbd-up.`

- [ ] **Step 5: Full suite, lint, commit.** Don't push; the controller pushes and runs CI.

```bash
git add -A src tests README.md docs
git commit -m "JBD-UP in the app and README; spec marked implemented"
```
