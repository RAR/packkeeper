# JBD-UP Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live controls (restart, standby sleep, a guided MOSFET test)
for the ECO-WORTHY / JBD-UP driver. Each control is a fixed frame captured
from JBD's stock app, sent one step at a time and confirmed by reading the
pack. It is available from the CLI and from a new GUI tab.

**Architecture:** A generic `controls.py` holds the data types (`Step`,
`Control`, `Check`, results) and `control_frames()`. Drivers declare their
controls as data, along with the only registers those controls may write.
`ModbusDevice.send_control()` writes one step, and `service.run_control()`
sends the steps, runs the checks and sends the cleanup steps on failure,
Stop or Ctrl-C. The JBD driver declares three controls. The CLI gets
`packkeeper control`, and the GUI gets a Controls tab (`controls_view.py`)
modelled on the Clock tab.

**Tech Stack:** Python 3.12, PySide6, pytest. JBD frames go through the
existing `JbdFraming`, and tests run on the existing JBD simulator
(`FakeJbd`).

**Spec:** `docs/superpowers/specs/2026-10-23-jbd-up-controls-design.md`.
Read it first. It also has the capture table these frames come from.

## Global Constraints

- Every control step writes **one register** with a **constant captured from
  the stock app**:
  - restart 0x2912 ← 0x4A4A
  - standby 0x2908 ← 0xA501
  - Tooling 0x2900 ← 0x5A01
  - Normal 0x2900 ← 0x0000
  - MOS control 0x2902 ← 0x0001, then 0x0003

  Never compute, combine or bulk-write control values. The stock app's
  22-register "Settings" write is never copied.
- `send_control` writes only `driver.control_registers`, and never while
  `write_refusal` is set.
- Once a step's frame may be on the wire (sent, whether acked or not), a
  failed step, a Stop or Ctrl-C runs the control's `cleanup` steps.
- EG4-LL and JK-PB have no controls. Their behaviour must not change.
- The vendored `src/packkeeper/drivers/eg4_ll/settings_table.py` stays
  unchanged.
- Match the surrounding code: short docstrings, few comments, plain names.
  Text people read uses plain words.
- Commands, run from the repo root `/Users/rar/eg4_tool`:
  - Tests: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`
  - Lint: `uvx ruff check --select F,E9,B --ignore B905 src tests packaging`
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq
  ```
- Never send anything to a real pack. All tests use simulators.

---

## File Structure

- Create `src/packkeeper/controls.py`: data types (`Check`, `Step`,
  `Control`, `StepResult`, `ControlResult`), status strings and
  `control_frames()`.
- Modify `src/packkeeper/drivers/base.py`: add `control_registers` and
  `controls()`.
- Modify `src/packkeeper/device.py`: add `send_control()`, plus a
  `Device` protocol entry.
- Modify `src/packkeeper/service.py`: add `run_control()` and
  `_send_step()`.
- Create `src/packkeeper/drivers/jbd_up/controls.py`: the JBD constants,
  checks and the `CONTROLS` tuple.
- Modify `src/packkeeper/drivers/jbd_up/registers.py`: add `DEBUG`,
  `REG_IO`, `REG_STATE` and the 0x2900 request end.
- Modify `src/packkeeper/drivers/jbd_up/driver.py`: expose the controls.
- Modify `src/packkeeper/drivers/jbd_up/sim.py`: model what the captured
  controls did.
- Create `tests/jbd_up_control_frames.txt`: the captured frames.
- Modify `src/packkeeper/cli.py`: add the `control` subcommand.
- Create `src/packkeeper/controls_view.py`: the GUI Controls tab.
- Modify `src/packkeeper/gui.py`: add the tab, lock Disconnect while a
  control runs, and stop safely on close.
- Modify `README.md`: the command list, the Desktop app section and the
  JBD section.
- Tests:
  - `tests/test_controls.py` (generic, toy driver)
  - `tests/test_jbd_controls.py` (JBD and CLI)
  - `tests/test_gui.py` (GUI)

---

### Task 1: Generic controls: model, `send_control`, `run_control`

**Files:**
- Create: `src/packkeeper/controls.py`
- Modify: `src/packkeeper/drivers/base.py` (class attributes and the "optional" methods)
- Modify: `src/packkeeper/device.py` (the `Device` protocol and `ModbusDevice`)
- Modify: `src/packkeeper/service.py` (imports, new functions at the end)
- Test: `tests/test_controls.py`

**Interfaces:**
- Produces:
  - `packkeeper.controls`:
    - status strings `CONFIRMED`, `NOT_CONFIRMED`, `UNVERIFIED`, `NOT_SENT`
    - `Check(ok: bool | None, detail: str)`
    - `Step(label: str, register: int, value: int, check: Callable[[device, bool], Check])`
    - `Control(key, label, summary, steps, danger="", typed_word=None, cleanup=())`
    - `StepResult(step, status, detail, cleanup=False)`, with `.describe() -> str`
    - `ControlResult(control, results=[], stopped=None)`, with `.ok`
    - `control_frames(device, control) -> list[tuple[str, bytes]]`
  - `Driver.control_registers: frozenset[int]` (default empty)
  - `Driver.controls() -> tuple[Control, ...]` (default `()`)
  - `ModbusDevice.send_control(step: Step) -> bool` (True if acked; raises
    `PackError` when refused before sending)
  - `packkeeper.service.run_control(pack, control, progress=None, stop=None) -> ControlResult`.
    `progress` is called with each `StepResult`, and `stop` is a
    `threading.Event`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_controls.py`:

```python
"""Generic live controls: fixed one-register steps, each checked, with cleanup on failure or stop."""

import threading

import pytest

from packkeeper import drivers, modbus
from packkeeper.controls import (
    CONFIRMED, NOT_CONFIRMED, NOT_SENT, UNVERIFIED, Check, Control, Step, control_frames,
)
from packkeeper.device import PackError
from packkeeper.service import run_control
from packkeeper.session import SessionLog
from packkeeper.transport import NoReply
from toy_driver import Toy

REG_A, REG_B, REG_C = 0x50, 0x51, 0x52


class ToyWithControls(Toy):
    control_registers = frozenset({REG_A, REG_B})


class ToyWritesOff(ToyWithControls):
    def check_device(self, device, block):
        return "a test refusal"


def toy(driver=None):
    driver = driver or ToyWithControls()
    sim = driver.simulator()
    return sim, driver.open_device(sim, SessionLog())


def holds(register, value):
    def check(dev, acked):
        v = dev.transport.image[register]
        return Check(v == value, f"0x{register:02X} reads {v}")
    return check


def control(*steps, cleanup=()):
    return Control("demo", "Demo", "a test control", steps=tuple(steps), cleanup=tuple(cleanup))


def test_frames_are_the_fixed_steps_then_the_cleanup():
    sim, dev = toy()
    c = control(Step("A on", REG_A, 1, holds(REG_A, 1)), cleanup=[Step("A off", REG_A, 0, holds(REG_A, 0))])
    assert control_frames(dev, c) == [
        ("A on", modbus.write_request(7, REG_A, [1])),
        ("only if stopped or a step fails: A off", modbus.write_request(7, REG_A, [0])),
    ]


def test_every_step_confirmed():
    sim, dev = toy()
    seen = []
    res = run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1)), Step("B", REG_B, 2, holds(REG_B, 2))),
                      progress=seen.append)
    assert res.ok and res.stopped is None
    assert [r.status for r in res.results] == [CONFIRMED, CONFIRMED] and seen == res.results
    assert sim.writes == [(REG_A, [1]), (REG_B, [2])]
    assert any("control demo confirmed" in line for line in dev.log.lines)


def test_stops_at_the_first_unconfirmed_step_and_runs_cleanup():
    sim, dev = toy()
    c = control(Step("A", REG_A, 1, holds(REG_A, 9)), Step("B", REG_B, 2, holds(REG_B, 2)),
                cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert not res.ok and res.stopped.startswith("A: NOT confirmed")
    assert [(r.step.label, r.status, r.cleanup) for r in res.results] == [
        ("A", NOT_CONFIRMED, False), ("undo A", CONFIRMED, True)]
    assert sim.writes == [(REG_A, [1]), (REG_A, [0])]


def test_nothing_is_sent_while_writes_are_off():
    sim, dev = toy(ToyWritesOff())
    with pytest.raises(PackError, match="a test refusal"):
        run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1))))
    assert sim.writes == []


def test_send_control_refuses_a_register_the_driver_does_not_list():
    sim, dev = toy()
    with pytest.raises(PackError, match="not a control register"):
        dev.send_control(Step("C", REG_C, 1, holds(REG_C, 1)))
    assert sim.writes == []


def test_a_refused_step_is_not_sent_and_needs_no_cleanup():
    sim, dev = toy()
    c = control(Step("C", REG_C, 1, holds(REG_C, 1)), cleanup=[Step("undo", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert [r.status for r in res.results] == [NOT_SENT] and sim.writes == []


def test_stop_after_a_step_runs_cleanup():
    sim, dev = toy()
    stop = threading.Event()

    def check_and_stop(dev, acked):
        stop.set()  # the user clicks Stop while step A is being checked
        return holds(REG_A, 1)(dev, acked)

    c = control(Step("A", REG_A, 1, check_and_stop), Step("B", REG_B, 2, holds(REG_B, 2)),
                cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c, stop=stop)
    assert res.stopped == "stopped by the user" and not res.ok
    assert sim.writes == [(REG_A, [1]), (REG_A, [0])]


def test_stop_before_anything_was_sent_sends_nothing():
    sim, dev = toy()
    stop = threading.Event()
    stop.set()
    res = run_control(dev, control(Step("A", REG_A, 1, holds(REG_A, 1)),
                                   cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))]), stop=stop)
    assert res.stopped == "stopped by the user" and res.results == [] and sim.writes == []


def test_no_ack_and_no_read_back_is_unverified():
    sim, dev = toy()
    sim.drop_acks = 1

    def no_read_back(dev, acked):
        raise NoReply("silent")

    res = run_control(dev, control(Step("A", REG_A, 1, no_read_back)))
    (r,) = res.results
    assert r.status == UNVERIFIED and "(no ack)" in r.detail and "read-back failed" in r.detail
    assert sim.writes == [(REG_A, [1])]


def test_ctrl_c_runs_cleanup():
    sim, dev = toy()

    def interrupted(dev, acked):
        raise KeyboardInterrupt

    c = control(Step("A", REG_A, 1, interrupted), cleanup=[Step("undo A", REG_A, 0, holds(REG_A, 0))])
    res = run_control(dev, c)
    assert res.stopped == "interrupted" and sim.writes == [(REG_A, [1]), (REG_A, [0])]
    assert res.results[-1].cleanup and res.results[-1].status == CONFIRMED


def test_eg4_and_jk_have_no_controls():
    for name in ("eg4-ll", "jk-pb"):
        d = drivers.get(name)
        assert d.controls() == () and d.control_registers == frozenset()
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_controls.py`
Expected: an ImportError for `packkeeper.controls`, so the module fails to collect.

- [ ] **Step 3: Create `src/packkeeper/controls.py`**

```python
"""Live controls a driver declares, such as a restart.

Each step writes one register with data captured from the BMS maker's own
tool; nothing is computed. A step's check reads the pack to confirm what the
write did. service.run_control sends the steps and runs the checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

CONFIRMED = "confirmed"
NOT_CONFIRMED = "NOT confirmed"
UNVERIFIED = "UNVERIFIED"  # sent, but the read-back could not tell
NOT_SENT = "not sent"


@dataclass(frozen=True)
class Check:
    ok: bool | None  # True: confirmed; False: the pack shows otherwise; None: could not tell
    detail: str


@dataclass(frozen=True)
class Step:
    label: str
    register: int
    value: int  # one register, exactly as captured
    check: Callable[[object, bool], Check] = field(repr=False, compare=False)  # (device, acked) -> Check


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    summary: str
    steps: tuple[Step, ...]
    danger: str = ""
    typed_word: str | None = None  # None: a plain yes
    cleanup: tuple[Step, ...] = ()  # sent when a step fails or the user stops, once a frame may be on the wire


@dataclass(frozen=True)
class StepResult:
    step: Step
    status: str
    detail: str
    cleanup: bool = False

    def describe(self) -> str:
        return f"{'cleanup: ' if self.cleanup else ''}{self.status:13s} {self.step.label}: {self.detail}"


@dataclass
class ControlResult:
    control: Control
    results: list[StepResult] = field(default_factory=list)
    stopped: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.stopped is None
            and len(self.results) == len(self.control.steps)
            and all(r.status == CONFIRMED for r in self.results)
        )


def control_frames(device, control: Control) -> list[tuple[str, bytes]]:
    """(label, frame) for every step, then every cleanup step, as `device` would send them."""

    def frame(s: Step) -> bytes:
        return device.framing.write_request(device.address, s.register, [s.value])

    return [(s.label, frame(s)) for s in control.steps] + [
        (f"only if stopped or a step fails: {s.label}", frame(s)) for s in control.cleanup
    ]
```

- [ ] **Step 4: Add the driver hooks to `src/packkeeper/drivers/base.py`**

Add to the imports block:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..controls import Control
```

Add this class attribute after `clock_note: str = ""`:

```python
    control_registers: frozenset[int] = frozenset()  # the only registers send_control writes
```

Add this method in the "optional" section, after `clock_write`:

```python
    def controls(self) -> tuple["Control", ...]:
        """Live controls (restart, sleep, ...), as captured from the maker's own tool."""
        return ()
```

- [ ] **Step 5: Add `send_control` to `src/packkeeper/device.py`**

In `class Device(Protocol)`, after `def write_clock(...)`, add:

```python
    def send_control(self, step) -> bool: ...
```

In `class ModbusDevice`, after `write_clock`, add:

```python
    def send_control(self, step) -> bool:
        """One control step's write; True if acked. Only the driver's control registers."""
        if self.write_refusal:
            raise PackError(f"writes are off: {self.write_refusal}")
        if step.register not in self.driver.control_registers:
            raise PackError(
                f"refusing to write 0x{step.register:02X}: not a control register of the {self.driver.label}"
            )
        return self._write(step.register, [step.value])
```

- [ ] **Step 6: Add `run_control` to `src/packkeeper/service.py`**

Add to the imports:

```python
import threading

from . import controls as C
from .block import MissingRegister
from .errors import FrameError
from .transport import NoReply
```

`threading` goes with the other stdlib imports at the top. `from .errors
import DeviceRefused` already exists, so add `FrameError` to that line:
`from .errors import DeviceRefused, FrameError`. `from .block import ...`
already exists, so add `MissingRegister` to it.

Then append at the end of the file:

```python
def run_control(
    pack: Device,
    control: C.Control,
    progress: Callable[[C.StepResult], None] | None = None,
    stop: threading.Event | None = None,
) -> C.ControlResult:
    """Send a control's steps one at a time, checking each, and stop at the first not confirmed.

    The block is read first, which sets the write key and whether writes are
    allowed. Once a frame may be on the wire, a failed step, a stop or Ctrl-C
    sends the cleanup steps.
    """
    log = pack.log
    pack.read_block()
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}; nothing was sent")
    out = C.ControlResult(control)
    log.info(f"control {control.key}: {control.label}")
    on_wire = False

    def record(r: C.StepResult) -> None:
        out.results.append(r)
        log.result(r.describe())
        if progress:
            progress(r)

    try:
        for step in control.steps:
            if stop is not None and stop.is_set():
                out.stopped = "stopped by the user"
                break
            r = _send_step(pack, step)
            on_wire = on_wire or r.status != C.NOT_SENT
            record(r)
            if r.status != C.CONFIRMED:
                out.stopped = f"{step.label}: {r.status} ({r.detail})"
                break
    except KeyboardInterrupt:
        out.stopped = "interrupted"
        on_wire = True  # a frame may have gone out when Ctrl-C came
    if out.stopped and on_wire:
        for step in control.cleanup:
            record(_send_step(pack, step, cleanup=True))
    log.result(f"control {control.key} " + ("confirmed" if out.ok else f"NOT confirmed ({out.stopped})"))
    return out


def _send_step(pack: Device, step: C.Step, cleanup: bool = False) -> C.StepResult:
    try:
        acked = pack.send_control(step)
    except PackError as e:  # refused before sending
        return C.StepResult(step, C.NOT_SENT, str(e), cleanup)
    except DeviceRefused as e:
        acked = False
        pack.log.warn(f"the pack refused {step.label}: {e}")
    try:
        check = step.check(pack, acked)
    except (NoReply, FrameError, DeviceRefused, MissingRegister) as e:
        check = C.Check(None, f"the read-back failed: {e}")
    status = C.CONFIRMED if check.ok else C.UNVERIFIED if check.ok is None else C.NOT_CONFIRMED
    return C.StepResult(step, status, check.detail + ("" if acked else " (no ack)"), cleanup)
```

`Callable` is already imported in `service.py` (`from typing import
Callable`).

- [ ] **Step 7: Run the tests until they pass**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_controls.py`
Expected: 11 passed.

- [ ] **Step 8: Run the full suite and lint**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests packaging`
Expected: all tests pass, and ruff prints "All checks passed!"

- [ ] **Step 9: Commit**

```bash
git add src/packkeeper/controls.py src/packkeeper/drivers/base.py src/packkeeper/device.py src/packkeeper/service.py tests/test_controls.py
git commit -m "Live controls: one-register steps a driver declares, each checked, with cleanup on failure or stop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq"
```

---

### Task 2: JBD-UP controls and the simulator

**Files:**
- Create: `src/packkeeper/drivers/jbd_up/controls.py`
- Modify: `src/packkeeper/drivers/jbd_up/registers.py`
- Modify: `src/packkeeper/drivers/jbd_up/driver.py`
- Modify: `src/packkeeper/drivers/jbd_up/sim.py`
- Create: `tests/jbd_up_control_frames.txt`
- Test: `tests/test_jbd_controls.py`

**Interfaces:**
- Consumes (Task 1): `Check`, `Step`, `Control`, `control_frames`,
  `run_control`, the status strings, and the `Driver.control_registers` /
  `Driver.controls()` hooks.
- Produces:
  - `packkeeper.drivers.jbd_up.controls`:
    - registers `REG_MODE`, `REG_MOS`, `REG_SLEEP`, `REG_RESTART`
    - values `NORMAL`, `TOOLING`, `MOS_CHARGE`, `MOS_BOTH`, `STANDBY`, `RESTART`
    - `CONTROL_REGISTERS`
    - timing knobs, which tests patch: `MOS_WAIT_S`, `QUIET_WAIT_S`,
      `BACK_WAIT_S`, `POLL_S`
    - `CONTROLS`, keyed `restart`, `standby` and `mosfet-test`
  - `registers.DEBUG = Area(0x2900, 7, 2)`, `registers.REG_IO = 0x1020`,
    `registers.REG_STATE = 0x1014`
  - `FakeJbd` attributes: `restart_s` (float or None; 0 means it never goes
    quiet), `ignore: set[int]`, `stuck_mosfets: bool`

- [ ] **Step 1: Save the captured frames as a fixture**

Create `tests/jbd_up_control_frames.txt`, exactly as follows:

```
# JBD stock app (JBD-ES-UP V1.22.35) control frames to pack 2 (address 2, protocol 2.6),
# captured 2026-10-24 over the eco-charger bridge: ~/eco-charger/captures/jbd-controls-20261024-094951.log
# The stock app used key 0x11 for these. label|request|reply
restart|02 79 29 12 29 14 00 06 11 4A 42 44 4A 4A F8 D1|02 79 29 12 29 14 00 00 DE B7
standby|02 79 29 08 29 0A 00 06 11 4A 42 44 A5 01 00 6E|02 79 29 08 29 0A 00 00 E7 73
tooling|02 79 29 00 29 02 00 06 11 4A 42 44 5A 01 0C 3E|02 79 29 00 29 02 00 00 87 70
mos charge|02 79 29 02 29 04 00 06 11 4A 42 44 00 01 16 86|02 79 29 02 29 04 00 00 1E B1
mos both|02 79 29 02 29 04 00 06 11 4A 42 44 00 03 97 47|02 79 29 02 29 04 00 00 1E B1
normal|02 79 29 00 29 02 00 06 11 4A 42 44 00 00 F7 5E|02 79 29 00 29 02 00 00 87 70
debug read|02 78 29 00 29 10 00 00 37 B5|02 78 29 00 29 10 00 0E 00 00 00 00 00 00 00 00 00 00 00 00 00 00 0A C7
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_jbd_controls.py`:

```python
"""JBD-UP live controls: frames byte for byte as captured, checked against the simulator."""

import threading
from pathlib import Path

import pytest

import packkeeper.drivers.jbd_up.controls as JC
from packkeeper.controls import CONFIRMED, NOT_CONFIRMED, UNVERIFIED, control_frames
from packkeeper.device import PackError
from packkeeper.drivers.jbd_up import DRIVER, registers as R
from packkeeper.drivers.jbd_up.framing import JbdFraming
from packkeeper.drivers.jbd_up.sim import FakeJbd
from packkeeper.service import run_control
from packkeeper.session import SessionLog

FRAMES = Path(__file__).parent / "jbd_up_control_frames.txt"


def captured() -> dict[str, tuple[bytes, bytes]]:
    out = {}
    for line in FRAMES.read_text().splitlines():
        if line and not line.startswith("#"):
            label, req, rep = line.split("|")
            out[label] = (bytes.fromhex(req), bytes.fromhex(rep))
    return out


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(JC, "MOS_WAIT_S", 0.2)
    monkeypatch.setattr(JC, "QUIET_WAIT_S", 0.3)
    monkeypatch.setattr(JC, "BACK_WAIT_S", 0.5)


def jbd(**kw):
    sim = FakeJbd(**kw)
    sim.restart_s = 0.1
    return sim, DRIVER.open_device(sim, SessionLog(), kw.get("address", 0))


def by_key():
    return {c.key: c for c in DRIVER.controls()}


def test_the_driver_lists_three_controls_and_their_registers():
    c = by_key()
    assert list(c) == ["restart", "standby", "mosfet-test"]
    assert [c[k].typed_word for k in c] == [None, "standby", "test"]
    assert DRIVER.control_registers == {0x2900, 0x2902, 0x2908, 0x2912}
    assert [(s.register, s.value) for s in c["mosfet-test"].steps] == [
        (0x2900, 0x5A01), (0x2902, 0x0001), (0x2902, 0x0003), (0x2900, 0x0000)]
    assert [(s.register, s.value) for s in c["mosfet-test"].cleanup] == [(0x2900, 0x0000)]


def test_frames_are_byte_for_byte_the_captured_ones():
    cap = captured()
    framing = JbdFraming(key=0x11, request_ends=R.REQUEST_ENDS)  # the key the stock app used

    class At2:
        address = 2

    At2.framing = framing
    frames = {label: f for c in DRIVER.controls() for label, f in control_frames(At2, c)}
    assert frames["Restart"] == cap["restart"][0]
    assert frames["Standby sleep"] == cap["standby"][0]
    assert frames["Tooling mode (both MOSFETs off)"] == cap["tooling"][0]
    assert frames["Charge MOSFET on"] == cap["mos charge"][0]
    assert frames["Both MOSFETs on"] == cap["mos both"][0]
    assert frames["Normal mode (the BMS drives the MOSFETs)"] == cap["normal"][0]
    assert framing.read_request(2, R.DEBUG.start, R.DEBUG.count) == cap["debug read"][0]
    for label in ("restart", "standby", "tooling", "mos charge", "mos both", "normal"):
        req, rep = cap[label]
        framing.parse_write(req, rep)  # the pack's acks parse
    req, rep = cap["debug read"]
    assert framing.parse_read(req, rep) == [0] * 7


def test_mosfet_test_confirmed_step_by_step():
    sim, dev = jbd()
    res = run_control(dev, by_key()["mosfet-test"])
    assert res.ok, res.stopped
    assert [r.status for r in res.results] == [CONFIRMED] * 4
    assert "charge off, discharge off" in res.results[0].detail
    assert "charge on, discharge off" in res.results[1].detail
    assert "debug mode reads normal" in res.results[3].detail
    assert [(s, v) for s, v in sim.writes] == [(0x2900, [0x5A01]), (0x2902, [1]), (0x2902, [3]), (0x2900, [0])]


def test_mosfets_that_stay_on_fail_the_test_and_it_returns_to_normal():
    sim, dev = jbd()
    sim.stuck_mosfets = True
    res = run_control(dev, by_key()["mosfet-test"])
    assert not res.ok and res.results[0].status == NOT_CONFIRMED
    assert res.results[-1].cleanup and res.results[-1].status == CONFIRMED
    assert sim.writes == [(0x2900, [0x5A01]), (0x2900, [0])] and sim.mem[0x2900] == 0


def test_an_ignored_mos_write_fails_and_returns_to_normal():
    sim, dev = jbd()
    sim.ignore = {0x2902}
    res = run_control(dev, by_key()["mosfet-test"])
    assert [r.status for r in res.results[:2]] == [CONFIRMED, NOT_CONFIRMED]
    assert res.results[-1].cleanup and sim.writes[-1] == (0x2900, [0])


def test_stop_returns_to_normal():
    sim, dev = jbd()
    stop = threading.Event()
    res = run_control(dev, by_key()["mosfet-test"], progress=lambda r: stop.set(), stop=stop)
    assert res.stopped == "stopped by the user"
    assert sim.writes == [(0x2900, [0x5A01]), (0x2900, [0])]
    assert dev.read_live().charge_allowed and dev.read_live().discharge_allowed


def test_restart_confirmed_when_the_pack_goes_quiet_then_answers():
    sim, dev = jbd()
    res = run_control(dev, by_key()["restart"])
    assert res.ok and "went quiet, then answered again" in res.results[0].detail
    assert sim.writes == [(0x2912, [0x4A4A])]


def test_a_restart_never_seen_is_unverified():
    sim, dev = jbd()
    sim.restart_s = 0
    res = run_control(dev, by_key()["restart"])
    assert not res.ok and res.results[0].status == UNVERIFIED and "never went quiet" in res.results[0].detail


def test_a_pack_that_never_comes_back_is_not_confirmed():
    sim, dev = jbd()
    sim.restart_s = None
    res = run_control(dev, by_key()["restart"])
    assert res.results[0].status == NOT_CONFIRMED and "has not answered" in res.results[0].detail


def test_standby_is_confirmed_by_its_ack_and_reports_the_status():
    sim, dev = jbd()
    res = run_control(dev, by_key()["standby"])
    assert res.ok and "Standby" in res.results[0].detail
    assert sim.writes == [(0x2908, [0xA501])]


def test_writes_off_sends_no_control():
    sim, dev = jbd(large_capacity=True)  # turns writes off on every read
    with pytest.raises(PackError, match="writes are off"):
        run_control(dev, by_key()["restart"])
    assert sim.writes == []
```

- [ ] **Step 3: Run them to see them fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_jbd_controls.py`
Expected: an ImportError for `packkeeper.drivers.jbd_up.controls`.

- [ ] **Step 4: Add registers to `src/packkeeper/drivers/jbd_up/registers.py`**

After `VERSION = Area(0x2810, 0x16, STRIDE)  # ...`, add:

```python
DEBUG = Area(0x2900, 7, STRIDE)  # 0x2900-0x290E: the stock app's debug "Reading"; 0x2900 is the debug mode
```

Change `REQUEST_ENDS` to include the stock app's debug read:

```python
REQUEST_ENDS = {0x1000: 0x10A0, 0x1800: 0x1900, 0x1C00: 0x1CA0, 0x2000: 0x2050, 0x2800: 0x280F, 0x2810: 0x2840,
                0x2900: 0x2910}
```

After `REG_SIGNALS = 0x1022 ...`, add:

```python
REG_STATE = 0x1014  # live status: 0 standby, 1 charging, 2 discharging
REG_IO = 0x1020  # live I/O: bit 0 discharge MOSFET, bit 1 charge MOSFET
```

- [ ] **Step 5: Create `src/packkeeper/drivers/jbd_up/controls.py`**

```python
"""JBD-UP live controls, as JBD's stock app (JBD-ES-UP V1.22.35) sent them on
2026-10-24. Every value is the data the stock app wrote; the controls spec
holds the capture."""

from __future__ import annotations

import time

from ...controls import Check, Control, Step
from ...errors import FrameError
from ...transport import NoReply
from . import registers as R

REG_MODE = 0x2900  # debug mode
REG_MOS = 0x2902  # MOS control, Tooling mode only: bit 0 charge, bit 1 discharge
REG_SLEEP = 0x2908
REG_RESTART = 0x2912
CONTROL_REGISTERS = frozenset({REG_MODE, REG_MOS, REG_SLEEP, REG_RESTART})
NORMAL, TOOLING = 0x0000, 0x5A01
MOS_CHARGE, MOS_BOTH = 0x0001, 0x0003
STANDBY = 0xA501
RESTART = 0x4A4A

MOS_WAIT_S = 3.0  # for the live MOSFET bits to show a step
QUIET_WAIT_S = 5.0  # for a restarting pack to stop answering
BACK_WAIT_S = 30.0  # for it to answer again
POLL_S = 0.25


def _onoff(charge: bool, discharge: bool) -> str:
    return f"charge {'on' if charge else 'off'}, discharge {'on' if discharge else 'off'}"


def mosfets(charge: bool, discharge: bool):
    """A check: the live MOSFET bits show this state within MOS_WAIT_S."""

    def check(device, acked: bool) -> Check:
        deadline = time.monotonic() + MOS_WAIT_S
        while True:
            live = device.read_live(retries=1)
            seen = (live.charge_allowed, live.discharge_allowed)
            if seen == (charge, discharge):
                return Check(True, f"MOSFETs read {_onoff(*seen)}")
            if time.monotonic() >= deadline:
                return Check(False, f"MOSFETs read {_onoff(*seen)}, expected {_onoff(charge, discharge)}")
            time.sleep(POLL_S)

    return check


def normal_mode(device, acked: bool) -> Check:
    c = mosfets(True, True)(device, acked)
    if not c.ok:
        return c
    mode = device.read_area(R.DEBUG, retries=1)[0]
    if mode != NORMAL:
        return Check(False, f"{c.detail}; debug mode reads 0x{mode:04X}, expected normal (0)")
    return Check(True, f"{c.detail}; debug mode reads normal")


def restarted(device, acked: bool) -> Check:
    """The pack stops answering, then answers the version read again."""
    end = time.monotonic() + QUIET_WAIT_S
    while True:
        try:
            device.read_area(R.VERSION, retries=1)
        except (NoReply, FrameError):
            break
        if time.monotonic() >= end:
            return Check(None, f"{'acked' if acked else 'no ack'}, but the pack never went quiet: restart not seen")
        time.sleep(POLL_S)
    end = time.monotonic() + BACK_WAIT_S
    while time.monotonic() < end:
        time.sleep(POLL_S)
        try:
            device.read_area(R.VERSION, retries=1)
        except (NoReply, FrameError):
            continue
        return Check(True, "the pack went quiet, then answered again")
    return Check(False, f"the pack went quiet and has not answered for {BACK_WAIT_S:.0f} s")


def standby(device, acked: bool) -> Check:
    status = device.read_live(retries=1).status_text
    return Check(acked, f"{'acked' if acked else 'no ack'}; the pack reports {status}")


NORMAL_STEP = Step("Normal mode (the BMS drives the MOSFETs)", REG_MODE, NORMAL, normal_mode)

CONTROLS = (
    Control(
        "restart", "Restart",
        "Restarts the BMS. It stops answering for a few seconds, then answers again.",
        steps=(Step("Restart", REG_RESTART, RESTART, restarted),),
    ),
    Control(
        "standby", "Standby sleep",
        "Puts the BMS into standby sleep. Under load it reported Standby for about 2 s and kept its MOSFETs on.",
        steps=(Step("Standby sleep", REG_SLEEP, STANDBY, standby),),
        danger="Without a load the pack may stop supplying power until it wakes.",
        typed_word="standby",
    ),
    Control(
        "mosfet-test", "MOSFET test",
        "Tooling mode (both MOSFETs off), charge on, both on, then back to Normal; "
        "each step is checked in the live data.",
        steps=(
            Step("Tooling mode (both MOSFETs off)", REG_MODE, TOOLING, mosfets(False, False)),
            Step("Charge MOSFET on", REG_MOS, MOS_CHARGE, mosfets(True, False)),
            Step("Both MOSFETs on", REG_MOS, MOS_BOTH, mosfets(True, True)),
            NORMAL_STEP,
        ),
        danger="The pack's output is cut for a few seconds in the first two steps: an inverter running off "
        "the stack loses this pack briefly. Best with little or no load.",
        typed_word="test",
        cleanup=(NORMAL_STEP,),
    ),
)
```

- [ ] **Step 6: Expose the controls in `src/packkeeper/drivers/jbd_up/driver.py`**

Change `from . import live, registers as R` to:

```python
from . import controls as C, live, registers as R
```

Add this class attribute after `clock_note = R.CLOCK_NOTE`:

```python
    control_registers = C.CONTROL_REGISTERS
```

Add this method after `clock_write`:

```python
    def controls(self):
        return C.CONTROLS
```

- [ ] **Step 7: Teach `FakeJbd` the captured behaviour (`src/packkeeper/drivers/jbd_up/sim.py`)**

Add `import time` to the stdlib imports. Change `from . import registers as R`
to `from . import controls as C, registers as R`.

Change `SENDS` to:

```python
SENDS = {0x1000: 0x10A2, 0x1800: 0x18D0, 0x1C00: 0x1C88, 0x2810: 0x283C, 0x2900: 0x290E}
```

In `FakeJbd.__init__`, after `self.sends = dict(SENDS)`, add:

```python
        self.restart_s: float | None = 1.5  # silent this long after a restart; None: never answers again; 0: never quiet
        self.ignore: set[int] = set()  # registers whose writes are acked but not applied
        self.stuck_mosfets = False  # Tooling mode leaves the MOSFETs on
        self._silent_until: float | None = 0.0
        self._standby_reads = 0
        self._status = self.mem[R.REG_STATE]
```

At the top of `transact`, before the CRC check, add:

```python
        if self._silent_until is None or time.monotonic() < self._silent_until:
            raise NoReply("simulated pack is restarting")
```

In the `fn == FN_READ` branch, before `end = self.sends.get(start, end)`, add:

```python
            if start == R.LIVE.start:
                self._live_state()
```

In the `fn == FN_WRITE` branch, replace everything from
`self.writes.append((start, values))` up to the existing `self._save()` with:

```python
            self.writes.append((start, values))
            if start in self.ignore:
                return with_crc(struct.pack(">BBHHH", addr, fn, start, end, 0))  # acked, not applied
            for i, v in enumerate(values):
                reg = start + 2 * i
                if reg in self.floors and v < self.floors[reg]:
                    continue  # acked, not stored
                self.mem[reg] = v
            if start == R.CLOCK.start:
                self._clock_base = (datetime.now(), datetime(*values[:6]))
            self._control(start, values[0])
```

Leave the existing `self._save()` and the ack `return` after it unchanged.

Add these methods after `_clock_now`:

```python
    def _control(self, register: int, value: int) -> None:
        """What the captured controls did to a real pack (2026-10-24)."""
        if register == C.REG_MODE and value == C.TOOLING:
            self.mem[C.REG_MOS] = 0  # entering Tooling switched both MOSFETs off
        elif register == C.REG_SLEEP and value == C.STANDBY:
            self.mem[register] = 0
            self._standby_reads = 2
        elif register == C.REG_RESTART and value == C.RESTART:
            self.mem[register] = 0
            self._silent_until = None if self.restart_s is None else time.monotonic() + self.restart_s

    def _live_state(self) -> None:
        """The live I/O and status words, from the mode the controls left the pack in."""
        io = self.mem.get(R.REG_IO, 0) & ~0b11
        if self.mem.get(C.REG_MODE, 0) == C.TOOLING and not self.stuck_mosfets:
            mos = self.mem.get(C.REG_MOS, 0)
            io |= (0b10 if mos & 1 else 0) | (0b01 if mos & 2 else 0)
        else:
            io |= 0b11
        self.mem[R.REG_IO] = io
        self.mem[R.REG_STATE] = 0 if not io & 0b01 or self._standby_reads else self._status
        self._standby_reads = max(0, self._standby_reads - 1)
```

- [ ] **Step 8: Run the JBD controls tests until they pass**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_jbd_controls.py`
Expected: 11 passed.

- [ ] **Step 9: Run the full suite and lint**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests packaging`
Expected: all tests pass (including the existing `test_jbd_up.py`), and
ruff passes.

- [ ] **Step 10: Commit**

```bash
git add src/packkeeper/drivers/jbd_up tests/jbd_up_control_frames.txt tests/test_jbd_controls.py
git commit -m "JBD-UP: restart, standby and MOSFET test controls, as captured from the stock app

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq"
```

---

### Task 3: `packkeeper control` on the command line, plus the README

**Files:**
- Modify: `src/packkeeper/cli.py` (imports, `build_parser`, a new `cmd_control` after `cmd_clock`)
- Modify: `README.md` (the command list in "## Command line", and the "## ECO-WORTHY / JBD-UP (48 V rack)" section)
- Test: `tests/test_jbd_controls.py` (append)

**Interfaces:**
- Consumes: `control_frames` (Task 1), `run_control` (Task 1),
  `DRIVER.controls()` (Task 2), and the patchable timing knobs in
  `packkeeper.drivers.jbd_up.controls` (Task 2).
- Produces: the CLI subcommand `control [NAME]` with the common options and
  `-n/--dry-run`. Exit codes: 0 confirmed or listed, 1 not confirmed, 2
  refused or nothing sent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_jbd_controls.py`:

```python
def control_tx(tmp_path) -> list[str]:
    return [line for log in tmp_path.glob("*.log") for line in log.read_text().splitlines()
            if " TX " in line and " 79 29 " in line]


def test_cli_lists_the_controls(capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up"]) == 0
    out = capsys.readouterr().out
    assert "restart" in out and "standby" in out and "mosfet-test" in out


def test_cli_dry_run_shows_the_frames_and_sends_nothing(tmp_path, capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up", "--simulate", "--dry-run", "--log-dir", str(tmp_path),
                 "mosfet-test"]) == 0
    out = capsys.readouterr().out
    assert "00 79 29 00 29 02 00 06 11 4A 42 44 5A 01" in out and "Dry run: nothing sent." in out
    assert "only if stopped or a step fails" in out
    assert control_tx(tmp_path) == []


def test_cli_needs_the_typed_word(tmp_path, capsys, monkeypatch):
    from packkeeper.cli import main

    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    assert main(["control", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path), "mosfet-test"]) == 2
    assert "Nothing was sent." in capsys.readouterr().out and control_tx(tmp_path) == []


def test_cli_runs_the_mosfet_test(tmp_path, capsys, monkeypatch):
    from packkeeper.cli import main

    monkeypatch.setattr("builtins.input", lambda prompt="": "test")
    assert main(["control", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path), "mosfet-test"]) == 0
    out = capsys.readouterr().out
    assert out.count("confirmed") >= 4 and "Control confirmed." in out
    assert len(control_tx(tmp_path)) == 4


def test_cli_unknown_control_and_bms_without_controls(capsys):
    from packkeeper.cli import main

    assert main(["control", "--bms", "jbd-up", "--simulate", "nope"]) == 2
    assert "unknown control 'nope'" in capsys.readouterr().err
    assert main(["control"]) == 2
    assert "has no controls" in capsys.readouterr().err
```

The CLI's `--simulate` JBD simulator is a `FakeJbdBus` of fresh `FakeJbd`s,
with `restart_s` at its default of 1.5 s. These tests run no restart, so the
default doesn't slow them down.

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_jbd_controls.py -k cli`
Expected: they FAIL, because argparse exits with "invalid choice: 'control'".

- [ ] **Step 3: Add the subcommand to `src/packkeeper/cli.py`**

Add to the imports:

```python
from .controls import control_frames
```

Change the `from .service import ...` line to also import `run_control`:

```python
from .service import NOT_ATTEMPTED, UNVERIFIED, StaleError, apply_plan, diff_blocks, run_control, set_clock
```

In `build_parser`, after the `clock` subparser's `s.set_defaults(func=cmd_clock)`, add:

```python
    s = sub.add_parser("control", parents=[common, write],
                       help="list or run a live control (restart, standby, MOSFET test)")
    s.add_argument("name", nargs="?", help="the control to run; leave out to list them")
    s.set_defaults(func=cmd_control)
```

After `cmd_clock`, add:

```python
def cmd_control(args) -> int:
    driver = driver_of(args)
    controls = {c.key: c for c in driver.controls()}
    if not controls:
        raise CliError(f"the {driver.label} has no controls")
    if args.name is None:
        for c in controls.values():
            print(f"{c.key:12s} {c.label}: {c.summary}")
            if c.danger:
                print(f"{'':12s} {c.danger}")
        return 0
    control = controls.get(args.name)
    if control is None:
        raise CliError(f"unknown control '{args.name}' (known: {', '.join(controls)})")
    pack = open_pack(args)
    pack.read_block()
    print(f"{control.label}: {control.summary}")
    if control.danger:
        print(f"WARNING: {control.danger}")
    frames = control_frames(pack, control)
    print("\nFrames, in order:")
    for label, frame in frames:
        print(f"  {frame.hex(' ').upper()}   {label}")
    if args.dry_run:
        for label, frame in frames:
            pack.log.info(f"dry run frame {frame.hex(' ').upper()}  {label}")
        print("\nDry run: nothing sent.")
        return 0
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}. Nothing was sent.")
    word = control.typed_word or "yes"
    try:
        answer = input(f"\nType '{word}' to send it: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != word:
        print("Nothing was sent.")
        pack.log.info(f"control {control.key}: not confirmed; nothing sent")
        return 2
    pack.log.info(f"control {control.key} confirmed by user")
    print()
    res = run_control(pack, control, lambda r: print(f"  {r.describe()}"))
    if res.ok:
        print("\nControl confirmed.")
        return 0
    print(f"\nNOT confirmed: {res.stopped}\nSee the log: {pack.log.path}")
    return 1
```

Ctrl-C during the steps is handled inside `run_control`, which sends the
cleanup step. `test_controls.py::test_ctrl_c_runs_cleanup` covers it.

- [ ] **Step 4: Run the CLI tests until they pass**

Run: `PYTHONPATH=src .venv/bin/pytest -q tests/test_jbd_controls.py`
Expected: 16 passed.

- [ ] **Step 5: Document it in `README.md`**

In the "## Command line" code block, add this line after the `packkeeper
clock ...` line, keeping the column alignment:

```
packkeeper control [NAME]                     list or run a live control (ECO-WORTHY / JBD-UP)
```

At the end of the "## ECO-WORTHY / JBD-UP (48 V rack)" section, just before
"## Safe ranges and rules", add:

```markdown
**Controls** (`packkeeper control --bms jbd-up`, or the **Controls** tab):
restart, standby sleep and a MOSFET test. Each one sends frames captured from
JBD's own app, never computed ones, and checks the result by reading the pack:

- **Restart**: the pack must go quiet and then answer again within 30 s.
- **Standby sleep** (type `standby`): confirmed by its ack; the pack's status
  is shown after.
- **MOSFET test** (type `test`): Tooling mode (both MOSFETs off), charge on,
  both on, then back to Normal, each step checked against the live MOSFET bits.
  If a step fails, or you stop it or press Ctrl-C, it goes back to Normal mode
  and checks that. The pack's output is cut for a few seconds, so run it with
  little or no load.

`--dry-run` shows the frames and sends nothing. Nothing is sent while writes
are off.
```

In the "## Desktop app" bullet list, after the **Clock:** bullet, add:

```markdown
- **Controls** (ECO-WORTHY / JBD-UP): restart, standby sleep and a MOSFET
  test, each checked by reading the pack. The MOSFET test has a **Stop**
  button that goes back to Normal mode.
```

- [ ] **Step 6: Run the full suite and lint**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests packaging`
Expected: all tests pass, and ruff passes.

- [ ] **Step 7: Commit**

```bash
git add src/packkeeper/cli.py README.md tests/test_jbd_controls.py
git commit -m "packkeeper control: list, dry-run and run a BMS's live controls

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq"
```

---

### Task 4: The GUI Controls tab

**Files:**
- Create: `src/packkeeper/controls_view.py`
- Modify: `src/packkeeper/gui.py`:
  - imports
  - `MainWindow.__init__`: tab creation and `addTab` calls
  - `driver_changed`, `on_busy`, `set_connected`, `disconnect_pack`, `closeEvent`
  - a new method `control_running_changed`
- Test: `tests/test_gui.py` (append)

**Interfaces:**
- Consumes: `control_frames`, `CONFIRMED`, `StepResult` and `Control`
  (Task 1), `run_control` (Task 1), the `DRIVER.controls()` keys `restart`,
  `standby` and `mosfet-test` (Task 2), `JC.POLL_S` (Task 2), and
  `FakeJbd.mem` (Task 2).
- Produces:
  - `controls_view.ControlsTab(main)`, with these attributes:
    - `buttons: dict[str, QPushButton]`, keyed by control key
    - `dry: QCheckBox`
    - `stop_btn: QPushButton`
    - `steps: QLabel`
    - `stop: threading.Event`
    - `running: Control | None`
    - `step_done: Signal(object)`
  - methods: `set_driver(driver)`, `update_enabled()`
  - `MainWindow.controls` (the tab) and `MainWindow.control_running_changed()`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui.py`:

```python
def connect_jbd(app, w):
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)


def click_control(app, w, key):
    """Click a control's button once nothing else holds the worker (a live poll disables the buttons)."""
    wait(app, lambda: w.controls.buttons[key].isEnabled())
    w.controls.buttons[key].click()


def slow_jbd_bus(monkeypatch, delay=0.03):
    """Make every simulated request take a little time, so Stop and close land mid-control."""
    from packkeeper.drivers.jbd_up import sim

    orig = sim.FakeJbdBus.transact

    def slow(self, request):
        time.sleep(delay)
        return orig(self, request)

    monkeypatch.setattr(sim.FakeJbdBus, "transact", slow)


def test_controls_tab_only_for_a_bms_with_controls(window):
    app, w = window
    i = w.tabs.indexOf(w.controls)
    assert not w.tabs.isTabVisible(i)  # EG4-LL
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    assert not w.tabs.isTabVisible(i)
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    assert w.tabs.isTabVisible(i) and set(w.controls.buttons) == {"restart", "standby", "mosfet-test"}
    assert not any(b.isEnabled() for b in w.controls.buttons.values())  # not connected


def test_controls_dry_run_sends_nothing(window):
    app, w = window
    connect_jbd(app, w)
    w.controls.dry.setChecked(True)
    click_control(app, w, "mosfet-test")
    text = w.controls.steps.text()
    assert "5A 01" in text and "Nothing was sent" in text
    assert not [line for line in w.pack.log.lines if " TX " in line and " 79 29 " in line]


def test_mosfet_test_runs_and_locks_disconnect(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    click_control(app, w, "mosfet-test")
    wait(app, lambda: w.controls.running is not None)
    assert not w.connect_btn.isEnabled() and not w.controls.stop_btn.isHidden()
    w.disconnect_pack()
    assert w.pack is not None  # refused while a control runs
    wait(app, lambda: w.controls.running is None, timeout=15)
    assert "Confirmed." in w.controls.steps.text() and w.connect_btn.isEnabled()
    assert w.controls.stop_btn.isHidden()


def test_stop_goes_back_to_normal(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    w.controls.step_done.connect(lambda r: w.controls.stop_btn.click() if not r.cleanup else None)
    click_control(app, w, "mosfet-test")
    wait(app, lambda: w.controls.running is not None)
    wait(app, lambda: w.controls.running is None, timeout=15)
    text = w.controls.steps.text()
    assert "stopped by the user" in text and "cleanup: Normal mode" in text
    assert w.pack.transport.packs[0].mem[0x2900] == 0


def test_closing_mid_control_goes_back_to_normal_first(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    sim = w.pack.transport.packs[0]  # the GUI's simulated port is the FakeJbdBus itself
    click_control(app, w, "mosfet-test")
    wait(app, lambda: sim.writes and sim.writes[-1] == (0x2900, [0x5A01]))
    w.close()
    assert sim.writes[-1] == (0x2900, [0]) and sim.mem[0x2900] == 0
```

`time` is already imported at the top of `tests/test_gui.py`.

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_gui.py -k "control or stop_goes or closing_mid"`
Expected: they FAIL with `AttributeError: 'MainWindow' object has no
attribute 'controls'`.

- [ ] **Step 3: Create `src/packkeeper/controls_view.py`**

```python
"""The Controls tab: a BMS's live controls, sent by service.run_control on the worker thread."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .controls import CONFIRMED, Control, StepResult, control_frames
from .qtcommon import BAD, GOOD, WARN, error_text
from .service import run_control


class ControlsTab(QWidget):
    step_done = Signal(object)  # a StepResult, emitted on the worker thread

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.stop = threading.Event()
        self.running: Control | None = None
        self.buttons: dict[str, QPushButton] = {}
        self.lines: list[str] = []
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Each control sends frames captured from the BMS maker's own app, then reads the pack to confirm "
            "what happened. The pack is read first, and nothing is sent while writes are off."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(intro)
        self.rows = QVBoxLayout()
        lay.addLayout(self.rows)
        row = QHBoxLayout()
        self.dry = QCheckBox("Dry run (show the frames only)")
        self.stop_btn = QPushButton("Stop (back to Normal)")
        self.stop_btn.hide()
        row.addWidget(self.dry)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.steps = QLabel()
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        self.steps.setWordWrap(True)
        self.steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.steps)
        lay.addStretch(1)
        self.stop_btn.clicked.connect(self.on_stop)
        self.step_done.connect(self.show_step)

    def set_driver(self, driver) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.buttons.clear()
        for c in driver.controls():
            box = QFrame()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 4, 0, 4)
            btn = QPushButton(f"{c.label}…")
            btn.clicked.connect(lambda _=False, c=c: self.start(c))
            text = QLabel(c.summary + (f"<br><span style='color:{WARN.name()}'>{c.danger}</span>" if c.danger else ""))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            h.addWidget(btn)
            h.addWidget(text, 1)
            self.rows.addWidget(box)
            self.buttons[c.key] = btn
        self.lines = []
        self.steps.setText("")
        self.update_enabled()

    def update_enabled(self) -> None:
        on = self.main.pack is not None and not self.main.worker.busy and self.running is None
        for b in self.buttons.values():
            b.setEnabled(on)

    def start(self, control: Control) -> None:
        pack = self.main.pack
        if pack is None or self.running is not None:
            return
        if self.dry.isChecked():
            frames = control_frames(pack, control)
            for label, f in frames:
                pack.log.info(f"dry run frame {f.hex(' ').upper()}  {label}")
            listing = "".join(f"<br><code>{f.hex(' ').upper()}</code>  {label}" for label, f in frames)
            self.steps.setText(f"<b>Dry run: {control.label}.</b> Would send:{listing}<br>Nothing was sent.")
            return
        self.steps.setText(f"Reading the pack before {control.label.lower()}…")
        self.main.worker.submit(pack.read_block, lambda _block, err: self.confirm(control, pack, err))

    def confirm(self, control: Control, pack, err) -> None:
        if pack is not self.main.pack:  # disconnected meanwhile
            return
        if err is not None:
            self.steps.setText(f"<b style='color:{BAD.name()}'>Could not read the pack:</b> "
                               f"{error_text(err, pack.driver)}")
            return
        if pack.write_refusal:
            self.steps.setText(f"<b style='color:{BAD.name()}'>Writes are off:</b> {pack.write_refusal}. "
                               "Nothing was sent.")
            return
        frames = "\n".join(f"{f.hex(' ').upper()}  {label}" for label, f in control_frames(pack, control))
        text = f"{control.summary}\n\n" + (f"{control.danger}\n\n" if control.danger else "") + f"Frames:\n{frames}"
        if control.typed_word:
            word, ok = QInputDialog.getText(self, control.label, f"{text}\n\nType '{control.typed_word}' to send it:")
            confirmed = ok and word.strip().lower() == control.typed_word
        else:
            yes = QMessageBox.StandardButton.Yes
            confirmed = QMessageBox.question(
                self, control.label, f"{text}\n\nSend it?", yes | QMessageBox.StandardButton.No) == yes
        if not confirmed:
            self.steps.setText("Not confirmed. Nothing was sent.")
            pack.log.info(f"control {control.key}: not confirmed; nothing sent")
            return
        pack.log.info(f"control {control.key} confirmed by user")
        self.run(control, pack)

    def run(self, control: Control, pack) -> None:
        self.running = control
        self.stop.clear()
        self.lines = [f"<b>{control.label}</b>"]
        self.steps.setText(self.lines[0])
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(bool(control.cleanup))
        self.main.control_running_changed()
        self.main.worker.submit(lambda: run_control(pack, control, self.step_done.emit, self.stop), self.done)

    def show_step(self, r: StepResult) -> None:
        ok = r.status == CONFIRMED
        color = GOOD if ok else BAD
        self.lines.append(f"<span style='color:{color.name()}'>{'✓' if ok else '✗'}</span> "
                          f"{'cleanup: ' if r.cleanup else ''}{r.step.label}: {r.status}, {r.detail}")
        self.steps.setText("<br>".join(self.lines))

    def on_stop(self) -> None:
        self.stop.set()
        self.stop_btn.setEnabled(False)
        self.lines.append("Stopping: back to Normal after the current step…")
        self.steps.setText("<br>".join(self.lines))

    def done(self, res, err) -> None:
        self.running = None
        self.stop_btn.hide()
        if err is not None:
            self.lines.append(f"<b style='color:{BAD.name()}'>Failed:</b> {error_text(err, self.main.driver)}")
        elif res.ok:
            self.lines.append(f"<b style='color:{GOOD.name()}'>Confirmed.</b>")
        else:
            self.lines.append(f"<b style='color:{BAD.name()}'>NOT confirmed:</b> {res.stopped}")
        self.steps.setText("<br>".join(self.lines))
        self.main.control_running_changed()
        self.main.read_settings()
```

- [ ] **Step 4: Wire the tab into `src/packkeeper/gui.py`**

Change the import `from . import __version__, backup, drivers, qtcommon,
settings_view` to:

```python
from . import __version__, backup, controls_view, drivers, qtcommon, settings_view
```

In `MainWindow.__init__`, after `self.clock = ClockTab(self)`, add:

```python
        self.controls = controls_view.ControlsTab(self)
```

After `self.tabs.addTab(self.clock, "Clock")`, add:

```python
        self.tabs.addTab(self.controls, "Controls")
```

In `driver_changed`, after the line
`self.tabs.setTabVisible(self.tabs.indexOf(self.clock), d.supports_clock)`, add:

```python
        self.tabs.setTabVisible(self.tabs.indexOf(self.controls), bool(d.controls()))
        self.controls.set_driver(d)
```

Replace `on_busy`'s first statement:

```python
        self.connect_btn.setEnabled(not busy or self.pack is not None)
```

with:

```python
        self.connect_btn.setEnabled((not busy or self.pack is not None) and self.controls.running is None)
```

and add this as the last line of `on_busy`, after the cursor
`if/else`:

```python
        self.controls.update_enabled()
```

In `set_connected`, after `self.clock.set_btn.setEnabled(False)`, add:

```python
        self.controls.update_enabled()
```

At the very start of `disconnect_pack`, before `pack = self.pack`, add:

```python
        if self.controls.running is not None:
            self.status("A control is running: wait for it to finish, or click Stop.", error=True)
            return
```

Add this method after `disconnect_pack`:

```python
    def control_running_changed(self):
        running = self.controls.running is not None
        self.connect_btn.setEnabled(not running and (not self.worker.busy or self.pack is not None))
        self.controls.update_enabled()
```

In `closeEvent`, before `self.worker.shutdown()`, add:

```python
        self.controls.stop.set()  # a running control goes back to Normal before the port closes
```

- [ ] **Step 5: Run the GUI tests until they pass**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q tests/test_gui.py`
Expected: every GUI test passes, including the 5 new ones.

- [ ] **Step 6: Run the full suite and lint**

Run: `PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q && uvx ruff check --select F,E9,B --ignore B905 src tests packaging`
Expected: all tests pass, and ruff passes.

- [ ] **Step 7: Commit**

```bash
git add src/packkeeper/controls_view.py src/packkeeper/gui.py tests/test_gui.py
git commit -m "GUI: a Controls tab for a BMS's live controls, with Stop and a locked Disconnect while one runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_014t3ZTvrMDULakWHJ3mSQMq"
```

---

### Task 5: Whole-branch check (controller)

**Files:** none (verification only)

- [ ] **Step 1: EG4 parity.** Run the parity script against the tested EG4
  build:
  `/Users/rar/eg4_tool/.venv/bin/python /private/tmp/claude-501/-Users-rar-eg4-tool/6550f5ba-d8f0-4606-8ea7-83179d8584a7/scratchpad/parity_eg4base.py`.
  Its `BRANCH` must point at the tree under test: edit the `BRANCH = ...`
  line if work happens in a worktree.
  Expected: `17 scenarios, 0 difference(s)`.
- [ ] **Step 2: Full suite and lint, one last time.**
  Expected: everything passes.
- [ ] **Step 3: Simulated smoke test of the CLI.**
  `PYTHONPATH=src .venv/bin/python -m packkeeper control --bms jbd-up --simulate --dry-run mosfet-test`.
  Expected: five frames listed (four steps plus "only if stopped or a step
  fails: Normal mode …") and "Dry run: nothing sent."
