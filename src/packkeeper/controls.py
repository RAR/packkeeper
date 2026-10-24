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
