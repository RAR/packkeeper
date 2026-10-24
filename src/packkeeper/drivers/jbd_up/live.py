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

    def temp(raw: int) -> float:
        return round((raw - 500) / 10, 1)  # the pack sends tenths, as the stock app shows them

    n = min(u16(0x42), 32)
    cells = [u16(0x44 + 2 * i) / 1000 for i in range(n) if 0x44 + 2 * i + 2 <= len(data)]
    off = 0x44 + 2 * u16(0x42)
    m = min(u16(off), 16)
    sensors = [temp(u16(off + 2 + 2 * i)) for i in range(m) if off + 2 + 2 * i + 2 <= len(data)]
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
