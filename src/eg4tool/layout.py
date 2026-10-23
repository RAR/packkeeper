"""How settings are arranged on screen: the shapes drivers fill in. No Qt
here, so they can be tested.

Threshold triples are rows of a Warning / Protection / Release matrix;
over-current settings are stage rows of trip current and delay.
"""

from __future__ import annotations

from dataclasses import dataclass

TEMP = "temp"  # unit placeholder: °C or °F, whichever is shown

WPR = ("Warning", "Protection", "Release")
COLUMN_HELP = {
    "Warning": "The pack raises a warning flag.",
    "Protection": "The BMS stops charging (high limits) or discharging (low limits).",
    "Release": "The protection clears once the value is back past this point.",
}


@dataclass(frozen=True)
class Section:
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, tuple[str | None, ...]], ...]  # (label, keys); None = empty cell
    unit: str | None = None  # shown after the title; TEMP follows the °F switch


@dataclass(frozen=True)
class Page:
    title: str
    sections: tuple[Section, ...]
    note: str = ""

    @property
    def keys(self) -> list[str]:
        return [k for s in self.sections for _label, keys in s.rows for k in keys if k]
