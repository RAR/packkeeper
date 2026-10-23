"""How the JBD-UP settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, Page, Section
from .table import GROUPS, OC_GROUPS

APR = ("Alarm", "Protection", "Recovery")
COLS = ("Alarm", "Alarm recovery", "Alarm delay (ms)", "Protection", "Protection recovery", "Protection delay (ms)")


def _six(title, names, unit):
    return Section(title, COLS, tuple((GROUPS[n][5], tuple(f"{n}_{f}" for f in (
        "alarm", "alarm_recovery", "alarm_delay", "protection", "protection_recovery", "protection_delay"))) for n in names), unit=unit)


def _oc(name):
    _base, fields, _lim, label = OC_GROUPS[name]
    return Section(label, tuple(f.replace("_", " ").capitalize() for f in fields), ((label, tuple(f"{name}_{f}" for f in fields)),))


def _one(title, rows, unit=None):
    return Section(title, ("Value",), tuple((label, (key,)) for label, key in rows), unit=unit)


PAGES = (
    Page("Voltage", (_six("Cell", ("cell_ov", "cell_uv"), "V"), _six("Pack", ("pack_ov", "pack_uv"), "V")),
         "High limits: recovery < alarm ≤ protection. Low limits mirrored. Delays in milliseconds."),
    Page("Current", tuple(_oc(n) for n in OC_GROUPS)),
    Page("Temperature", (
        _six("Charge / discharge", ("charge_ot", "charge_ut", "discharge_ot", "discharge_ut"), TEMP),
        _six("MOSFET / ambient", ("mos_ot", "ambient_ot", "ambient_ut"), TEMP),
        _six("Difference", ("temp_diff",), None),
    )),
    Page("Balancing & sleep", (
        _six("Cell difference", ("cell_diff",), "V"),
        _one("Balancing", (("Start voltage (V)", "balance_start_voltage"), ("Start delta (V)", "balance_start_delta"),
                           ("Mode", "balance_mode"))),
        _one("Heater", (("Heater on", "heater_on"), ("Heater off", "heater_off")), TEMP),
        _one("Sleep", (("Sleep voltage (V)", "sleep_voltage"), ("Sleep delay (min)", "sleep_delay"),
                       ("Standby sleep current (A)", "standby_sleep_current"), ("Static sleep delay (min)", "static_sleep_delay"))),
        _one("Full-charge calibration", (("Voltage (V)", "full_charge_voltage"), ("Current (A)", "full_charge_current"))),
    )),
    Page("Capacity & inverter", (
        _six("SOC low", ("soc_low",), "%"),
        _one("Capacity (Ah)", (("Design", "design_capacity"), ("Full", "full_capacity"))),
        Section("SOC table", ("Voltage (V)", "SOC (%)"),
                tuple((f"Point {i}", (f"soc_table_voltage_{i}", f"soc_table_percent_{i}")) for i in range(1, 5))),
        _one("Inverter requests", (("Charge voltage (V)", "charge_request_voltage"), ("Charge current (A)", "charge_request_current"),
                                   ("Discharge voltage (V)", "discharge_request_voltage"), ("Discharge current (A)", "discharge_request_current"))),
    )),
    Page("Unsafe", (
        _one("Pack hardware", (("Cells in series", "cells_in_series"), ("Shunt (mΩ)", "shunt_resistance"),
                               ("Short-circuit current (raw)", "short_circuit_current"),
                               ("Short-circuit delay step (raw)", "short_circuit_delay_step"),
                               ("Pack type", "pack_type"), ("Cell maker", "cell_maker"))),
        _one("Functions", (("SOC config bits", "soc_config_bits"), ("Function bits", "function_bits"))),
        _one("Inverter link", (("CAN protocol", "can_protocol"), ("RS485 protocol", "rs485_protocol"), ("CAN baud", "can_baud"))),
    ), "Each of these can stop the pack measuring, protecting or talking correctly. Keep a backup.", unsafe=True),
)
