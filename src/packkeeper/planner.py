"""Turn target values into a checked, safely ordered list of setting writes.

A plan is made against a parameter block read from the pack moments before.
It is refused (plan.errors) if any new value breaks LIMITS, if the resulting
block breaks any ORDER rule, if a linked pair would end up different, or if
no write order keeps every intermediate block valid.

Intermediate blocks: a write may not break an ORDER rule that holds in the
block it is applied to. Rules the pack already breaks are allowed to stay
broken until a later write in the same plan fixes them; the final block must
satisfy every rule.

Settings the pack did not send are never written. Neither is a setting whose
rule or linked partner was not sent, since the rule cannot be checked. A
restore or copy leaves them alone, with a note.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .block import Block
from .settings import LinkedPair, Rule, Setting, SettingTable


@dataclass(frozen=True)
class Change:
    setting: Setting
    old: int
    new: int

    @property
    def key(self) -> str:
        return self.setting.key

    @property
    def register(self) -> int:
        return self.setting.register

    @property
    def direction(self) -> int:
        return 1 if self.new > self.old else -1

    def describe(self, fahrenheit: bool = False) -> str:
        s = self.setting
        return f"{s.label}: {s.format(self.old, fahrenheit)} -> {s.format(self.new, fahrenheit)}"


@dataclass
class Plan:
    base: Block
    changes: list[Change] = field(default_factory=list)  # in write order
    proposed: list[Change] = field(default_factory=list)  # every requested change, by register
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problem_keys: set[str] = field(default_factory=set)  # settings named by an error
    allow_unsafe: bool = False  # made with unsafe settings on

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def empty(self) -> bool:
        return not self.changes

    @property
    def unsafe(self) -> list[Change]:
        return [c for c in self.changes if c.setting.unsafe]

    @property
    def result(self) -> Block:
        return self.base.with_raws({c.register: c.new for c in self.changes})

    def frames(self, address: int | None = None, framing=None) -> list[bytes]:
        d = self.base.driver
        return [d.write_frame(c.setting, c.new, address, framing) for c in self.changes]


def make_plan(
    base: Block,
    targets: dict[str, int],
    rules: list[Rule] | None = None,
    linked: list[LinkedPair] | None = None,
    fahrenheit: bool = False,
    source: Block | None = None,
    allow_unsafe: bool = False,
) -> Plan:
    """Plan writes that take `base` to `targets` ({setting key: raw})."""
    if source is not None:
        base.same_driver(source, "the source")
    table = base.driver.table
    rules = table.order if rules is None else rules
    linked = table.linked if linked is None else linked
    plan = Plan(base=base, allow_unsafe=allow_unsafe)
    base.check_not_blank()
    current = base.raws()  # only what the pack sent
    left_alone: list[str] = []

    wanted: dict[str, int] = {}
    for key, raw in targets.items():
        s = table.get(key)
        if s is None:
            plan.errors.append(f"unknown setting '{key}'")
            continue
        blocked = [r for r in s.registers if not base.driver.is_writable(r)]
        if blocked:
            plan.errors.append(f"{s.label}: register 0x{blocked[0]:02X} is not writable")
            continue
        if key not in current:
            if source is not None:
                left_alone.append(key)
            else:
                plan.errors.append(f"{s.label}: the pack did not send it, so it cannot be written")
                plan.problem_keys.add(key)
            continue
        if raw == current[key]:
            continue
        if s.unsafe and not allow_unsafe:
            plan.errors.append(f"{s.label} is an unsafe setting; turn on unsafe settings to change it")
            plan.problem_keys.add(key)
            continue
        err = s.limit_error(raw)
        if err:
            plan.errors.append(err)
            plan.problem_keys.add(key)
            continue
        wanted[key] = raw

    final = {**current, **wanted}
    plan.proposed = sorted(
        (Change(table[k], current[k], r) for k, r in targets.items() if k in current and r != current[k]),
        key=lambda c: c.register,
    )

    def unchecked(a: str, b: str, why: str) -> bool:
        """True if a or b was not sent; an error if the other is being changed."""
        gone = [k for k in (a, b) if k not in current]
        if not gone:
            return False
        changing = [k for k in (a, b) if k in wanted]
        if changing:
            plan.problem_keys.update(changing)
            plan.errors.append(
                f"{table[changing[0]].label} cannot be checked against {table[gone[0]].label} ({why}): "
                "the pack did not send it"
            )
        return True

    for pair in linked:
        a, b = pair
        if unchecked(a, b, "they must be equal"):
            continue
        if a in wanted or b in wanted:
            if final[a] != final[b]:
                plan.problem_keys.update((a, b))
                sa, sb = table[a], table[b]
                plan.errors.append(
                    f"{sa.label} ({sa.format(final[a])}) and {sb.label} ({sb.format(final[b])}) "
                    f"must be set to the same value: {pair.reason}"
                )
        elif current[a] != current[b]:
            plan.warnings.append(
                f"the pack has {table[a].label} != {table[b].label}; they ship equal"
            )

    checkable = []
    for rule in rules:
        if unchecked(rule.lower, rule.upper, table.describe_rule(rule)):
            continue
        checkable.append(rule)
        if not rule.holds(final):
            plan.problem_keys.update((rule.lower, rule.upper))
            already = "" if (rule.lower in wanted or rule.upper in wanted) else " (the pack already breaks this rule; include a fix for it)"
            plan.errors.append(f"after these writes, {table.describe_rule(rule, final, fahrenheit)}{already}")

    for key, raw in current.items():
        if key not in wanted:
            err = table[key].limit_error(raw)
            if err:
                plan.warnings.append(f"the pack currently has {err} (not being changed)")

    if left_alone:
        plan.notes.append(
            f"left alone, the pack did not send them: {', '.join(table[k].label for k in left_alone)}"
        )
    if source is not None:
        unsent = [k for k in table if k in current and not source.has(k) and (allow_unsafe or not table[k].unsafe)]
        if unsent:
            plan.notes.append(
                f"left alone, the source has no value for them (not sent when it was read): "
                f"{', '.join(table[k].label for k in unsent)}"
            )

    if plan.errors:
        return plan

    order, stuck = _order(table, current, wanted, checkable)
    if stuck:
        blocked = "; ".join(
            f"{table[k].label} is blocked by: {', '.join(table.describe_rule(r, current, fahrenheit) for r in rs)}"
            for k, rs in stuck.items()
        )
        plan.errors.append(f"no write order keeps every step valid. {blocked}")
        return plan

    plan.changes = [Change(table[k], current[k], wanted[k]) for k in order]
    for pair in linked:
        a, b = pair
        if a in wanted:
            plan.notes.append(f"{table[a].label} and {table[b].label} are written together ({pair.reason})")
    return plan


def _order(
    table: SettingTable, current: dict[str, int], wanted: dict[str, int], rules: list[Rule]
) -> tuple[list[str], dict[str, list[Rule]]]:
    state = dict(current)
    pending = dict(wanted)
    order: list[str] = []
    by_key: dict[str, list[Rule]] = {}
    for r in rules:
        by_key.setdefault(r.lower, []).append(r)
        by_key.setdefault(r.upper, []).append(r)

    def blockers(key: str) -> list[Rule]:
        trial = {**state, key: pending[key]}
        return [r for r in by_key.get(key, []) if r.holds(state) and not r.holds(trial)]

    while pending:
        free = [k for k in pending if not blockers(k)]
        if not free:
            return order, {k: blockers(k) for k in pending}

        # Moving up: the top of a chain first. Moving down: the bottom first.
        def priority(k: str) -> tuple[int, int]:
            up = pending[k] > state[k]
            return (-table.rank[k] if up else table.rank[k], table[k].register)

        key = min(free, key=priority)
        state[key] = pending.pop(key)
        order.append(key)
    return order, {}


def targets_from_block(source: Block, skip: set[str] = frozenset(), allow_unsafe: bool = False) -> dict[str, int]:
    """Every known setting `source` holds; unknown registers are never included,
    and unsafe settings only when asked for."""
    source.check_not_blank()
    table = source.driver.table
    return {
        k: source.raw(k)
        for k in table
        if k not in skip and source.has(k) and (allow_unsafe or not table[k].unsafe)
    }


def identity_warnings(pack: Block, source: Block, source_name: str) -> list[str]:
    pack.same_driver(source, source_name)
    out = []
    if source.model != pack.model:
        out.append(f"{source_name} is from model '{source.model}', this pack is '{pack.model}'")
    if source.firmware != pack.firmware:
        out.append(
            f"{source_name} is from BMS firmware '{source.firmware}', this pack runs '{pack.firmware}'"
        )
    if pack.driver.serial_is_unique and source.serial and pack.serial and source.serial != pack.serial:
        out.append(f"{source_name} is from pack '{source.serial}', the connected pack is '{pack.serial}'")
    return out
