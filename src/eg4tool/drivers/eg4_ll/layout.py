"""How the EG4-LL settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, WPR, Page, Section

PAGES = (
    Page(
        "Voltage",
        (
            Section(
                "Cell voltage",
                WPR,
                (
                    ("Over-voltage", ("cell_overvoltage_warning", "cell_overvoltage_protection", "cell_overvoltage_release")),
                    ("Under-voltage", ("cell_undervoltage_warning", "cell_undervoltage_protection", "cell_undervoltage_release")),
                ),
                unit="V",
            ),
            Section(
                "Pack voltage",
                WPR,
                (
                    ("Over-voltage", ("pack_overvoltage_warning", "pack_overvoltage_protection", "pack_overvoltage_release")),
                    ("Under-voltage", ("pack_undervoltage_warning", "pack_undervoltage_protection", "pack_undervoltage_release")),
                ),
                unit="V",
            ),
        ),
        "High limits need release < warning < protection; low limits need protection < warning < release. "
        "Cell under-voltage release must stay below cell over-voltage release.",
    ),
    Page(
        "Current",
        (
            Section(
                "Charge over-current",
                ("Trip at (A)", "After (s)"),
                (
                    ("Stage 1", ("charge_overcurrent_1_protection", "charge_overcurrent_1_delay")),
                    ("Stage 2", ("charge_overcurrent_2_protection", "charge_overcurrent_2_delay")),
                ),
            ),
            Section(
                "Discharge over-current",
                ("Trip at (A)", "After (s)"),
                (
                    ("Stage 1", ("discharge_overcurrent_1_protection", "discharge_overcurrent_1_delay")),
                    ("Stage 2", ("discharge_overcurrent_2_protection", "discharge_overcurrent_2_delay")),
                    ("Short circuit", ("short_circuit_current", None)),
                ),
            ),
            Section(
                "Recovery",
                ("Release after (s)", "Count"),
                (
                    ("Charge", ("charge_overcurrent_release_time", "charge_overcurrent_count")),
                    ("Discharge", ("discharge_overcurrent_release_time", "discharge_overcurrent_count")),
                ),
            ),
        ),
        "Stage 2 must trip at or above stage 1, and short circuit at or above discharge stage 2. "
        "Short circuit and discharge stage 2 are always set to the same value: which of registers "
        "0x52 / 0x54 is which is not settled yet.",
    ),
    Page(
        "Temperature",
        (
            Section(
                "Charging",
                WPR,
                (
                    ("Over-temp", ("charge_overtemp_warning", "charge_overtemp_protection", "charge_overtemp_release")),
                    ("Under-temp", ("charge_undertemp_warning", "charge_undertemp_protection", "charge_undertemp_release")),
                ),
                unit=TEMP,
            ),
            Section(
                "Discharging",
                WPR,
                (
                    ("Over-temp", ("discharge_overtemp_warning", "discharge_overtemp_protection", "discharge_overtemp_release")),
                    ("Under-temp", ("discharge_undertemp_warning", "discharge_undertemp_protection", "discharge_undertemp_release")),
                ),
                unit=TEMP,
            ),
            Section(
                "MOSFET",
                WPR,
                (("Over-temp", ("mos_overtemp_warning", "mos_overtemp_protection", "mos_overtemp_release")),),
                unit=TEMP,
            ),
            Section(
                "Ambient",
                WPR,
                (
                    ("Over-temp", ("ambient_overtemp_warning", "ambient_overtemp_protection", "ambient_overtemp_release")),
                    ("Under-temp", ("ambient_undertemp_warning", "ambient_undertemp_protection", "ambient_undertemp_release")),
                ),
                unit=TEMP,
            ),
            Section(
                "Heater",
                ("Turn on at", "Turn off at"),
                (("", ("heater_on_temperature", "heater_off_temperature")),),
                unit=TEMP,
            ),
        ),
        "High limits need release < warning < protection; low limits need protection < warning < release. "
        "The heater must turn on below the temperature it turns off at. Temperatures are whole °C.",
    ),
    Page(
        "Balancing & capacity",
        (
            Section(
                "Cell balancing",
                ("Start above (V)", "Cell difference (V)"),
                (("", ("balance_start_voltage", "balance_delta_voltage")),),
            ),
            Section("Capacity", ("Low capacity warning (%)",), (("", ("low_capacity_warning",)),)),
        ),
        "Balancing runs when a cell is above the start voltage and the cells differ by more than the "
        "difference. Balancing must start below the cell over-voltage warning. The pack ignores a "
        "difference below 0.020 V.",
    ),
)
