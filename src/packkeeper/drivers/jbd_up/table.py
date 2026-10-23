"""JBD-UP settings: the 0x1800 protection groups, pack and capacity settings,
and the unsafe ones. Ranges are this tool's proposals for 16S LiFePO4 with a
100 A BMS; compare them with the pack and the stock app before the first write.
"""

from __future__ import annotations

from ...settings import SettingTable
from .registers import STRIDE

DELAY = ("ms", 1, 0, 0, (100, 60000, 100))  # unit, multiplier, offset, decimals, limits
TEMP = ("°C", 0.1, -50, 1)
SIX = ("alarm", "alarm_recovery", "alarm_delay", "protection", "protection_recovery", "protection_delay")

# name: (base, (unit, mult, offset, decimals), high limit?, (lo, hi, step) for alarm/protection, (lo, hi, step) for recoveries, label)
GROUPS = {
    "cell_ov": (0x1800, ("V", 0.001, 0, 3), True, (3.4, 3.65, 0.001), (3.3, 3.6, 0.001), "Cell OV"),
    "cell_uv": (0x180C, ("V", 0.001, 0, 3), False, (2.5, 3.1, 0.001), (2.6, 3.2, 0.001), "Cell UV"),
    "pack_ov": (0x1818, ("V", 0.01, 0, 2), True, (54.4, 58.4, 0.01), (52.8, 57.6, 0.01), "Pack OV"),
    "pack_uv": (0x1824, ("V", 0.01, 0, 2), False, (40.0, 49.6, 0.01), (41.6, 51.2, 0.01), "Pack UV"),
    "charge_ot": (0x1858, TEMP, True, (30, 70, 0.1), (25, 65, 0.1), "Charge OT"),
    "charge_ut": (0x1864, TEMP, False, (-10, 10, 0.1), (-5, 15, 0.1), "Charge UT"),
    "discharge_ot": (0x1870, TEMP, True, (40, 80, 0.1), (35, 75, 0.1), "Discharge OT"),
    "discharge_ut": (0x187C, TEMP, False, (-30, 0, 0.1), (-25, 5, 0.1), "Discharge UT"),
    "mos_ot": (0x1888, TEMP, True, (60, 110, 0.1), (50, 100, 0.1), "MOSFET OT"),
    "ambient_ot": (0x1894, TEMP, True, (40, 80, 0.1), (35, 75, 0.1), "Ambient OT"),
    "ambient_ut": (0x18A0, TEMP, False, (-30, 0, 0.1), (-25, 5, 0.1), "Ambient UT"),
    "temp_diff": (0x18AC, ("Δ°C", 0.1, 0, 1), True, (2, 30, 0.1), (1, 29, 0.1), "Temperature diff"),
    "cell_diff": (0x18B8, ("V", 0.001, 0, 3), True, (0.05, 1.0, 0.001), (0.03, 0.99, 0.001), "Cell diff"),
    "soc_low": (0x18C4, ("%", 1, 0, 0), False, (0, 50, 1), (1, 60, 1), "SOC low"),
}
OC1 = ("alarm", "alarm_recovery", "alarm_delay", "protection", "protection_delay", "release_delay", "lockout_count")
OC2 = ("protection", "delay", "release_delay")
CURRENT = ("A", 0.1, 0, 1)
OC_GROUPS = {
    "charge_oc1": (0x1830, OC1, (1, 105, 0.1), "Charge OC 1"),
    "charge_oc2": (0x183E, OC2, (1, 150, 0.1), "Charge OC 2"),
    "discharge_oc1": (0x1844, OC1, (1, 105, 0.1), "Discharge OC 1"),
    "discharge_oc2": (0x1852, OC2, (1, 150, 0.1), "Discharge OC 2"),
}

# key: (register, (unit, mult, offset, decimals), (lo, hi, step), label)
SINGLE = {
    "balance_start_voltage": (0x1C04, ("V", 0.001, 0, 3), (3.0, 3.5, 0.001), "Balance start voltage"),
    "balance_start_delta": (0x1C06, ("V", 0.001, 0, 3), (0.005, 0.1, 0.001), "Balance start delta"),
    "heater_on": (0x1C08, TEMP, (-20, 15, 0.1), "Heater on"),
    "heater_off": (0x1C0A, TEMP, (-15, 20, 0.1), "Heater off"),
    "full_charge_voltage": (0x1C0C, ("V", 0.01, 0, 2), (52.0, 58.4, 0.01), "Full-charge calibration voltage"),
    "full_charge_current": (0x1C0E, ("A", 0.001, 0, 3), (0.1, 20.0, 0.001), "Full-charge calibration current"),
    "sleep_voltage": (0x1C78, ("V", 0.001, 0, 3), (2.5, 3.2, 0.001), "Sleep voltage"),
    "sleep_delay": (0x1C7A, ("min", 1, 0, 0), (1, 1440, 1), "Sleep delay"),
    "balance_mode": (0x1C7C, (None, 1, 0, 0), (0, 1, 1), "Balance mode"),
    "standby_sleep_current": (0x1C84, ("A", 0.1, 0, 1), (0.1, 5.0, 0.1), "Standby sleep current"),
    "static_sleep_delay": (0x1C86, ("min", 1, 0, 0), (1, 1440, 1), "Static sleep delay"),
    "fan_on": (0x1C8A, TEMP, (30, 70, 0.1), "Fan on"),
    "fan_off": (0x1C8C, TEMP, (25, 65, 0.1), "Fan off"),
    "design_capacity": (0x2000, ("Ah", 0.01, 0, 2), (10, 600, 0.01), "Design capacity"),
    "full_capacity": (0x2002, ("Ah", 0.01, 0, 2), (10, 600, 0.01), "Full capacity"),
    "charge_request_voltage": (0x202A, ("V", 0.1, 0, 1), (48.0, 58.4, 0.1), "Charge request voltage"),
    "charge_request_current": (0x202C, ("A", 0.1, 0, 1), (1, 100, 0.1), "Charge request current"),
    "discharge_request_voltage": (0x202E, ("V", 0.1, 0, 1), (40.0, 52.0, 0.1), "Discharge request voltage"),
    "discharge_request_current": (0x2030, ("A", 0.1, 0, 1), (1, 100, 0.1), "Discharge request current"),
    # unsafe (notes below)
    "cells_in_series": (0x2032, (None, 1, 0, 0), (1, 32, 1), "Cells in series"),
    "shunt_resistance": (0x2034, ("mΩ", 0.001, 0, 3), (0.05, 5.0, 0.001), "Shunt resistance"),
    "short_circuit_current": (0x2038, (None, 1, 0, 0), (0, 65535, 1), "Short-circuit current (raw)"),
    "short_circuit_delay_step": (0x203A, (None, 1, 0, 0), (0, 65535, 1), "Short-circuit delay step (raw)"),
    "pack_type": (0x1C00, (None, 1, 0, 0), (0, 1, 1), "Pack type"),
    "cell_maker": (0x1C02, (None, 1, 0, 0), (0, 2, 1), "Cell maker"),
    "soc_config_bits": (0x203C, (None, 1, 0, 0), (0, 0xFFFF, 1), "SOC config bits"),
    "function_bits": (0x203E, (None, 1, 0, 0), (0, 0xFFFF, 1), "Function bits"),
    "can_protocol": (0x1C76, (None, 1, 0, 0), (0, 15, 1), "CAN inverter protocol"),
    "rs485_protocol": (0x1C7E, (None, 1, 0, 0), (0, 15, 1), "RS485 inverter protocol"),
    "can_baud": (0x1C80, (None, 1, 0, 0), (0, 3, 1), "CAN baud"),
}
for i in range(4):
    SINGLE[f"soc_table_voltage_{i + 1}"] = (0x201A + 4 * i, ("V", 0.001, 0, 3), (2.5, 3.65, 0.001), f"SOC table voltage {i + 1}")
    SINGLE[f"soc_table_percent_{i + 1}"] = (0x201C + 4 * i, ("%", 1, 0, 0), (0, 100, 1), f"SOC table percent {i + 1}")

UNSAFE = {
    "cells_in_series": "A wrong count breaks every cell and pack protection.",
    "shunt_resistance": "Scales every current reading, and over-current protection with it.",
    "short_circuit_current": "The last line of protection; the unit is not published.",
    "short_circuit_delay_step": "The last line of protection; the unit is not published.",
    "pack_type": "Changes the chemistry model behind every curve.",
    "cell_maker": "Changes the cell model.",
    "soc_config_bits": "Changes how SOC is learned.",
    "function_bits": "Anti-theft can lock the pack out.",
    "can_protocol": "The wrong protocol cuts off the inverter.",
    "rs485_protocol": "The wrong protocol cuts off the inverter.",
    "can_baud": "The wrong baud cuts off the inverter.",
}
CHOICES = {
    "balance_mode": {0: "charge only", 1: "static"},
    "pack_type": {0: "LFP", 1: "NCM"},
    "cell_maker": {0: "Firm1", 1: "Firm2", 2: "Firm3"},
    "can_protocol": dict(enumerate([
        "Pylon", "Growatt", "GoodWe", "Sofar", "Victron", "Riyueyuan / Sanke", "Pengcheng", "Deye",
        "Solis", "SMA", "Nanjing Oulu", "SRNE", "INVT", "Sorotec", "MUST", "EASUN",
    ])),
    "rs485_protocol": dict(enumerate([
        "Pylon", "Growatt", "Riyueyuan", "Pengcheng", "Deye", "INVT", "SRNE", "IY-Power",
        "SMK", "Peicheng", "EPEver", "Sanke", "extension 6 / Nanjing Oulu", "extension 7", "extension 8", "extension 9",
    ])),
    "can_baud": {0: "500K", 1: "100K", 2: "125K", 3: "250K"},
}
BITS = {
    "soc_config_bits": {0x0005: "no capacity learning", 0x0050: "disable SOC curve"},
    "function_bits": {0x0001: "heater", 0x0004: "buzzer", 0x0010: "anti-theft", 0x0040: "GPS", 0x0100: "4-digit SOC", 0x0400: "LED board"},
}


def _build():
    spec, limits, labels, groups, order = {}, {}, {}, {}, []
    for name, (base, (unit, mult, off, dec), high, main, rec, label) in GROUPS.items():
        for i, field in enumerate(SIX):
            key = f"{name}_{field}"
            if field.endswith("delay"):
                u, m, o, d, lim = DELAY
                spec[key], limits[key] = (base + 2 * i, m, o, u, None, d), lim
            else:
                spec[key] = (base + 2 * i, mult, off, unit, None, dec)
                limits[key] = rec if field.endswith("recovery") else main
            labels[key], groups[key] = f"{label} {field.replace('_', ' ')}", label
        a, ar, p, pr = (f"{name}_{f}" for f in ("alarm", "alarm_recovery", "protection", "protection_recovery"))
        if high:
            order += [(ar, a, True), (a, p, False), (pr, p, True)]
        else:
            order += [(p, a, False), (a, ar, True), (p, pr, True)]
    for name, (base, fields, main, label) in OC_GROUPS.items():
        for i, field in enumerate(fields):
            key = f"{name}_{field}"
            if field.endswith("delay"):
                u, m, o, d, lim = DELAY
                spec[key], limits[key] = (base + 2 * i, m, o, u, None, d), lim
            elif field == "lockout_count":
                spec[key], limits[key] = (base + 2 * i, 1, 0, None, None, 0), (0, 10, 1)
            else:
                unit, mult, off, dec = CURRENT
                spec[key], limits[key] = (base + 2 * i, mult, off, unit, None, dec), main
            labels[key], groups[key] = f"{label} {field.replace('_', ' ')}", label
    for side in ("charge", "discharge"):
        order += [
            (f"{side}_oc1_alarm_recovery", f"{side}_oc1_alarm", True),
            (f"{side}_oc1_alarm", f"{side}_oc1_protection", False),
            (f"{side}_oc1_protection", f"{side}_oc2_protection", True),
        ]
    order += [
        ("cell_uv_protection", "cell_ov_protection", True),
        ("pack_uv_protection", "pack_ov_protection", True),
        ("heater_on", "heater_off", True),
    ]
    for i in range(1, 4):
        order += [
            (f"soc_table_voltage_{i}", f"soc_table_voltage_{i + 1}", True),
            (f"soc_table_percent_{i}", f"soc_table_percent_{i + 1}", True),
        ]
    for key, (reg, (unit, mult, off, dec), lim, label) in SINGLE.items():
        spec[key], limits[key], labels[key] = (reg, mult, off, unit, None, dec), lim, label
        groups[key] = "Unsafe" if key in UNSAFE else "Pack"
    return SettingTable.build(
        spec, limits, order, group=groups.__getitem__, label=labels.__getitem__,
        stride=STRIDE, unsafe=UNSAFE, choices=CHOICES, bits=BITS,
    )


TABLE = _build()
