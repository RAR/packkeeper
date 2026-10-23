"""Applying a plan to a pack, with a read-back after every write.

Shared by the CLI and the GUI. Each write is confirmed by re-reading the whole
parameter block before the next one goes out. A write the pack acks but does
not store, or any other register changing, stops the sequence: the block is
still valid at that point (the planner guarantees every intermediate block
is), so stopping is safe and guessing is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from .block import Block, DriverMismatch
from .device import Device, PackError
from .errors import DeviceRefused
from .planner import Change, Plan
from .settings import Setting, SettingTable

CONFIRMED = "confirmed"
NOT_APPLIED = "NOT applied"
NOT_ATTEMPTED = "not attempted"
UNVERIFIED = "UNVERIFIED"  # sent, but no read-back: the pack may or may not hold it
CLOCK_TOLERANCE = timedelta(seconds=5)


class StaleError(Exception):
    """The pack no longer holds the block the plan was made from."""


@dataclass
class WriteResult:
    change: Change
    status: str = NOT_ATTEMPTED
    acked: bool | None = None
    reported: int | None = None

    def describe(self, fahrenheit: bool = False) -> str:
        s = self.change.setting
        text = f"{self.status:13s} {self.change.describe(fahrenheit)}"
        if self.status == NOT_APPLIED and self.reported is not None:
            text += f" (pack still reports {s.format(self.reported, fahrenheit)})"
        if self.status == UNVERIFIED:
            text += " (sent, but the read-back failed: read the pack and diff before anything else)"
        if self.acked is False:
            text += " [no ack]"
        return text


@dataclass
class ApplyResult:
    results: list[WriteResult]
    final: Block | None
    stopped: str | None = None
    unexpected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stopped is None and all(r.status == CONFIRMED for r in self.results)


def register_diff(a: Block, b: Block) -> list[int]:
    """Writable registers (known or not) that differ between two blocks."""
    a.same_driver(b)
    ra, rb = a.threshold_registers(), b.threshold_registers()
    return [r for r in ra if ra[r] != rb[r]]


def describe_register(table: SettingTable, reg: int, raw: int) -> str:
    s = table.by_register.get(reg)
    return f"{s.label} = {s.format(raw)}" if s else f"unknown register 0x{reg:02X} = {raw}"


def apply_plan(
    pack: Device, plan: Plan, progress: Callable[[int, int, WriteResult], None] | None = None
) -> ApplyResult:
    log = pack.log
    results = [WriteResult(c) for c in plan.changes]
    if not plan.ok:
        raise ValueError("refusing to apply a plan with errors")
    if pack.driver is not plan.base.driver:
        raise DriverMismatch(
            f"this plan is for a {plan.base.driver.label} BMS and the connected device is a {pack.driver.label}"
        )
    table = plan.base.driver.table

    now = pack.read_block()
    changed = register_diff(plan.base, now)
    if changed:
        what = ", ".join(describe_register(table, r, now.reg(r)) for r in changed)
        log.error(f"pack changed since the diff was made: {what}")
        raise StaleError(
            f"the pack's settings changed since the diff was made ({what}). "
            "Was a different pack connected? Nothing was written; make the diff again."
        )

    expected = now
    final: Block | None = now
    log.info(f"applying {len(plan.changes)} write(s)")
    for i, (change, result) in enumerate(zip(plan.changes, results)):
        # From here on the frame may be on the wire: never report it as not sent.
        result.status = UNVERIFIED
        try:
            try:
                result.acked = pack.write_setting(change.register, change.new)
            except DeviceRefused as e:
                result.acked = False
                log.warn(f"the pack refused {change.describe()}: {e}; reading back to see what it holds")
            else:
                if not result.acked:
                    log.warn(f"no ack for {change.describe()}; reading back to see what the pack holds")
            block = pack.read_block()
        except Exception as e:  # noqa: BLE001 - report and stop, never continue blind
            log.error(f"stopped at {change.describe()}: {e}")
            log.result(result.describe())
            out = ApplyResult(
                results,
                None,
                stopped=f"stopped at {change.setting.label}: {e}. That write was sent but could not be "
                "read back; read the pack and diff it before doing anything else",
            )
            _log_summary(log, out, table)
            return out

        final = block
        result.reported = block.reg(change.register)
        result.status = CONFIRMED if result.reported == change.new else NOT_APPLIED
        expected = expected.with_raws({change.register: change.new})
        others = [r for r in register_diff(expected, block) if r != change.register]
        if progress:
            progress(i, len(results), result)
        log.result(result.describe())
        if others:
            unexpected = [
                f"{describe_register(table, r, block.reg(r))} (expected {expected.reg(r)} raw)" for r in others
            ]
            out = ApplyResult(
                results, block, stopped="other registers changed unexpectedly", unexpected=unexpected
            )
            for u in unexpected:
                log.error(f"unexpected change: {u}")
            _log_summary(log, out, table)
            return out
        if result.status != CONFIRMED:
            rest = len(results) - i - 1
            out = ApplyResult(
                results,
                block,
                stopped=f"the pack did not store {change.setting.label}"
                + (f"; the remaining {rest} write(s) were not sent" if rest else ""),
            )
            _log_summary(log, out, table)
            return out

    out = ApplyResult(results, final)
    _log_summary(log, out, table)
    return out


def _log_summary(log, out: ApplyResult, table: SettingTable) -> None:
    if out.stopped and out.final is not None:
        for a, b in table.linked:
            if out.final.raw(a) != out.final.raw(b):
                sa, sb = table[a], table[b]
                out.warnings.append(
                    f"the pack is left with {sa.label} {sa.format(out.final.raw(a))} and {sb.label} "
                    f"{sb.format(out.final.raw(b))}: they should be equal; fix before using the pack"
                )
    for w in out.warnings:
        log.warn(w)
    n = sum(r.status == CONFIRMED for r in out.results)
    log.result(f"{n} of {len(out.results)} write(s) confirmed" + (f"; {out.stopped}" if out.stopped else ""))


@dataclass
class ClockResult:
    before: datetime | None
    requested: datetime
    after: datetime | None
    acked: bool

    @property
    def ok(self) -> bool:
        return self.after is not None and abs(self.after - self.requested) <= CLOCK_TOLERANCE


def set_clock(pack: Device, t: datetime) -> ClockResult:
    if not pack.driver.supports_clock:
        raise PackError(f"the {pack.driver.label} has no clock to set")
    before = pack.read_block().clock
    t = t.replace(microsecond=0, tzinfo=None)
    pack.log.info(f"setting the BMS clock to {t}")
    acked = pack.write_clock(t)
    after = pack.read_block().clock
    res = ClockResult(before, t, after, acked)
    pack.log.result(f"clock {'confirmed' if res.ok else 'NOT applied'}: pack reports {after}")
    return res


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
