"""EG4-LL live registers 0x00-0x26 (EG4-LL Modbus protocol V01.06)."""

from __future__ import annotations

from ...live import Live, decode_bits

STATUS_BITS = {0x01: "Charging", 0x02: "Discharging", 0x04: "Protect", 0x08: "Charge limited"}
ALARM_BITS = {
    0x0001: "Pack OV",
    0x0002: "Cell OV",
    0x0004: "Pack UV",
    0x0008: "Cell UV",
    0x0010: "Charge OC",
    0x0020: "Discharge OC",
    0x0040: "Abnormal temp",
    0x0080: "MOSFET overheat",
    0x0100: "Charge OT",
    0x0200: "Discharge OT",
    0x0400: "Charge UT",
    0x0800: "Discharge UT",
    0x1000: "Low capacity",
}
WARNING_BITS = {**ALARM_BITS, 0x2000: "Float stopped"}
PROTECTION_BITS = {**ALARM_BITS, 0x2000: "Discharge short circuit"}
ERROR_BITS = {0x0001: "Voltage error", 0x0002: "Temperature error", 0x0004: "Current flow error", 0x0010: "Cell unbalance"}
CHARGE_BLOCKERS = 0x0001 | 0x0002 | 0x0010 | 0x0100 | 0x0400
DISCHARGE_BLOCKERS = 0x0004 | 0x0008 | 0x0020 | 0x0200 | 0x0800 | 0x2000


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
