"""A live reading, the same shape for every BMS. Drivers decode into it."""

from __future__ import annotations

from dataclasses import dataclass, field


def decode_bits(value: int, names: dict[int, str]) -> list[str]:
    out = [name for bit, name in names.items() if value & bit]
    unknown = value & ~sum(names)
    if unknown:
        out.append(f"unknown 0x{unknown:04X}")
    return out


def format_temp(c: float, fahrenheit: bool = False) -> str:
    """A temperature for people: tenths when the BMS sends them (a float), whole degrees otherwise."""
    shown = c * 9 / 5 + 32 if fahrenheit else c
    return f"{shown:.1f} °{'F' if fahrenheit else 'C'}" if isinstance(c, float) else (
        f"{shown:.0f} °F" if fahrenheit else f"{c} °C")


@dataclass
class Live:
    pack_voltage: float
    current: float
    soc: int
    soh: int
    cells: list[float]
    status_text: str
    temps: list[tuple[str, float]] = field(default_factory=list)  # (label, °C); float when in tenths
    max_temp: float | None = None
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
            lines.append("Temps      " + "  ".join(f"{label} {format_temp(t)}" for label, t in self.temps))
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
