"""JK-PB settings: 30 writable settings from the settings area, 16S LiFePO4 ranges.

The ranges are this tool's, not JK's: compare them with the pack and JK's
app before the first write.
"""

from __future__ import annotations

from ...settings import SettingTable
from .registers import SETTINGS, WORD_ORDER, addr

V = ("V", 0.001, 3)
A = ("A", 0.001, 3)
S = ("s", 1, 0)
C = ("°C", 0.1, 1)
US = ("µs", 1, 0)
AH = ("Ah", 0.001, 3)

# key: (byte offset, (unit, multiplier, decimals), signed, (min, max, step), group, label)
FIELDS = {
    "smart_sleep_voltage": (0x00, V, False, (2.5, 3.4, 0.001), "Shutdown", "Smart sleep voltage"),
    "cell_uv_protection": (0x04, V, False, (2.5, 3.1, 0.001), "Cell voltage", "Cell UV protection"),
    "cell_uv_release": (0x08, V, False, (2.6, 3.2, 0.001), "Cell voltage", "Cell UV release"),
    "cell_ov_protection": (0x0C, V, False, (3.45, 3.65, 0.001), "Cell voltage", "Cell OV protection"),
    "cell_ov_release": (0x10, V, False, (3.3, 3.6, 0.001), "Cell voltage", "Cell OV release"),
    "balance_trigger_delta": (0x14, V, False, (0.005, 0.1, 0.001), "Balancing", "Balance trigger delta"),
    "soc_100_voltage": (0x18, V, False, (3.3, 3.6, 0.001), "Charging", "SOC 100% voltage"),
    "soc_0_voltage": (0x1C, V, False, (2.5, 3.2, 0.001), "Charging", "SOC 0% voltage"),
    "charge_voltage": (0x20, V, False, (3.3, 3.65, 0.001), "Charging", "Charge voltage"),
    "float_voltage": (0x24, V, False, (3.2, 3.5, 0.001), "Charging", "Float voltage"),
    "power_off_voltage": (0x28, V, False, (2.5, 3.0, 0.001), "Shutdown", "Power-off voltage"),
    "charge_oc_current": (0x2C, A, False, (1, 200, 0.001), "Charge current", "Charge OC current"),
    "charge_oc_delay": (0x30, S, False, (1, 600, 1), "Charge current", "Charge OC delay"),
    "charge_oc_release_time": (0x34, S, False, (1, 600, 1), "Charge current", "Charge OC release time"),
    "discharge_oc_current": (0x38, A, False, (1, 200, 0.001), "Discharge current", "Discharge OC current"),
    "discharge_oc_delay": (0x3C, S, False, (1, 600, 1), "Discharge current", "Discharge OC delay"),
    "discharge_oc_release_time": (0x40, S, False, (1, 600, 1), "Discharge current", "Discharge OC release time"),
    "short_circuit_release_time": (0x44, S, False, (1, 600, 1), "Discharge current", "Short circuit release time"),
    "max_balance_current": (0x48, A, False, (0.1, 2.0, 0.001), "Balancing", "Max balance current"),
    "charge_ot_protection": (0x4C, C, True, (30, 70, 0.1), "Temperature", "Charge OT protection"),
    "charge_ot_release": (0x50, C, True, (25, 65, 0.1), "Temperature", "Charge OT release"),
    "discharge_ot_protection": (0x54, C, True, (40, 80, 0.1), "Temperature", "Discharge OT protection"),
    "discharge_ot_release": (0x58, C, True, (35, 75, 0.1), "Temperature", "Discharge OT release"),
    "charge_ut_protection": (0x5C, C, True, (-10, 10, 0.1), "Temperature", "Charge UT protection"),
    "charge_ut_release": (0x60, C, True, (-5, 15, 0.1), "Temperature", "Charge UT release"),
    "mos_ot_protection": (0x64, C, True, (60, 100, 0.1), "Temperature", "MOSFET OT protection"),
    "mos_ot_release": (0x68, C, True, (50, 90, 0.1), "Temperature", "MOSFET OT release"),
    "design_capacity": (0x7C, AH, False, (1, 1000, 0.001), "Balancing", "Design capacity"),
    "short_circuit_delay": (0x80, US, False, (50, 1500, 1), "Discharge current", "Short circuit delay"),
    "balance_start_voltage": (0x84, V, False, (3.0, 3.5, 0.001), "Balancing", "Balance start voltage"),
}

ORDER = [
    ("power_off_voltage", "cell_uv_protection", True),
    ("cell_uv_protection", "cell_uv_release", True),
    ("cell_ov_release", "cell_ov_protection", True),
    ("float_voltage", "charge_voltage", False),
    ("charge_voltage", "cell_ov_protection", True),
    ("balance_start_voltage", "cell_ov_protection", True),
    ("charge_ot_release", "charge_ot_protection", True),
    ("discharge_ot_release", "discharge_ot_protection", True),
    ("mos_ot_release", "mos_ot_protection", True),
    ("charge_ut_protection", "charge_ut_release", True),
]


def build_table(stride: int) -> SettingTable:
    spec = {
        key: (addr(SETTINGS, off, stride), mult, 0, unit, None, decimals, 2, signed)
        for key, (off, (unit, mult, decimals), signed, _lim, _g, _l) in FIELDS.items()
    }
    limits = {key: f[3] for key, f in FIELDS.items()}
    return SettingTable.build(
        spec,
        limits,
        ORDER,
        group=lambda k: FIELDS[k][4],
        label=lambda k: FIELDS[k][5],
        stride=stride,
        word_order=WORD_ORDER,
    )
