"""Decoding the JK-PB live area (0x1200, bytes 0x000-0x10D)."""

from __future__ import annotations

from ...live import Live, decode_bits

PROTECTIONS = {
    1 << 1: "MOSFET OT", 1 << 4: "cell OV", 1 << 5: "pack OV", 1 << 6: "charge OC", 1 << 7: "charge short",
    1 << 8: "charge OT", 1 << 9: "charge UT", 1 << 11: "cell UV", 1 << 12: "pack UV", 1 << 13: "discharge OC",
    1 << 14: "discharge short", 1 << 15: "discharge OT",
}
ERRORS = {
    1 << 0: "balance wire resistance", 1 << 2: "cell count mismatch", 1 << 3: "current sensor",
    1 << 10: "internal communication", 1 << 16: "charge MOSFET", 1 << 17: "discharge MOSFET",
    1 << 22: "temperature sensor", 1 << 23: "parallel module",
}
WARNINGS = {
    1 << 18: "GPS disconnected", 1 << 19: "change the password", 1 << 20: "discharge on failed",
    1 << 21: "battery over-temperature",
}


def decode(regs: list[int]) -> Live:
    def u16(off: int) -> int:
        return regs[off // 2]

    def s16(off: int) -> int:
        v = u16(off)
        return v - 0x10000 if v & 0x8000 else v

    def u32(off: int) -> int:
        return u16(off) << 16 | u16(off + 2)

    def s32(off: int) -> int:
        v = u32(off)
        return v - (1 << 32) if v & (1 << 31) else v

    present = u32(0x40)
    n = present.bit_length()
    cells = [u16(2 * i) / 1000 for i in range(min(n, 32))]
    current = s32(0x98) / 1000
    sensors = u16(0xD0) >> 8  # bit 0 MOSFET, bits 1-5 battery sensors 1-5
    temps = [("MOSFET", round(s16(0x8A) / 10))]
    for i, off in enumerate((0x9C, 0x9E, 0xF8, 0xFA, 0xFC), 1):
        if sensors & (1 << i):
            temps.append((f"sensor {i}", round(s16(off) / 10)))
    battery = [t for label, t in temps if label != "MOSFET"]
    alarms = u32(0xA0)
    balance = {0: "off", 1: "charging", 2: "discharging"}.get(u16(0xA6) >> 8, "?")
    status = "Charging" if current > 0.05 else "Discharging" if current < -0.05 else "Idle"
    return Live(
        pack_voltage=u32(0x90) / 1000,
        current=current,
        soc=u16(0xA6) & 0xFF,
        soh=u16(0xB8) >> 8,
        cells=cells,
        status_text=status,
        temps=temps,
        max_temp=max(battery) if battery else None,
        warnings=decode_bits(alarms & sum(WARNINGS), WARNINGS),
        protections=decode_bits(alarms & sum(PROTECTIONS), PROTECTIONS),
        errors=decode_bits(alarms & ~(sum(WARNINGS) | sum(PROTECTIONS)), ERRORS),
        charge_allowed=bool(u16(0xC0) >> 8),
        discharge_allowed=bool(u16(0xC0) & 0xFF),
        remaining_ah=s32(0xA8) / 1000,
        full_capacity_ah=u32(0xAC) / 1000,
        cycles=u32(0xB0),
        extra=[("Balancer", f"{balance}, {s16(0xA4) / 1000:.3f} A")],
        registers=list(regs),
    )
