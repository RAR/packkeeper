"""Read-only diagnostics: try the driver's probe reads one by one and report
what each got. Nothing is written; the session log keeps every frame."""

from __future__ import annotations

from dataclasses import dataclass

from .block import Area
from .errors import DeviceRefused, FrameError
from .transport import NoReply


@dataclass(frozen=True)
class Probe:
    label: str
    area: Area
    outcome: str
    words: tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.words)


def run(device) -> list[Probe]:
    out = []
    for label, area in device.driver.probe_reads():
        words: tuple[int, ...] = ()
        try:
            words = tuple(device.read_area(area, retries=1))
            outcome = f"ok, {len(words)} registers"
        except DeviceRefused as e:
            outcome = f"refused: {e}"
        except NoReply:
            outcome = "no reply"
        except FrameError as e:
            outcome = f"bad reply: {e}"
        device.log.result(f"probe {label} (0x{area.start:04X} x{area.count}): {outcome}")
        out.append(Probe(label, area, outcome, words))
    return out
