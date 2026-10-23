"""How the JK-PB settings are arranged on screen."""

from __future__ import annotations

from ...layout import TEMP, Page, Section

PR = ("Protection", "Release")

PAGES = (
    Page(
        "Voltage",
        (
            Section(
                "Cell protection",
                PR,
                (
                    ("Under-voltage", ("cell_uv_protection", "cell_uv_release")),
                    ("Over-voltage", ("cell_ov_protection", "cell_ov_release")),
                ),
                unit="V",
            ),
            Section(
                "Charging",
                ("Voltage",),
                (
                    ("Charge", ("charge_voltage",)),
                    ("Float", ("float_voltage",)),
                    ("SOC 100%", ("soc_100_voltage",)),
                    ("SOC 0%", ("soc_0_voltage",)),
                ),
                unit="V",
            ),
            Section(
                "Shutdown",
                ("Voltage",),
                (("Power off", ("power_off_voltage",)), ("Smart sleep", ("smart_sleep_voltage",))),
                unit="V",
            ),
        ),
        "Power-off < under-voltage protection < release; over-voltage release < protection; "
        "float ≤ charge voltage < over-voltage protection.",
    ),
    Page(
        "Current",
        (
            Section(
                "Over-current",
                ("Trip at (A)", "After (s)", "Release (s)"),
                (
                    ("Charge", ("charge_oc_current", "charge_oc_delay", "charge_oc_release_time")),
                    ("Discharge", ("discharge_oc_current", "discharge_oc_delay", "discharge_oc_release_time")),
                ),
            ),
            Section(
                "Short circuit",
                ("After (µs)", "Release (s)"),
                (("Short circuit", ("short_circuit_delay", "short_circuit_release_time")),),
            ),
        ),
    ),
    Page(
        "Temperature",
        (
            Section(
                "Temperature",
                PR,
                (
                    ("Charge hot", ("charge_ot_protection", "charge_ot_release")),
                    ("Discharge hot", ("discharge_ot_protection", "discharge_ot_release")),
                    ("Charge cold", ("charge_ut_protection", "charge_ut_release")),
                    ("MOSFET hot", ("mos_ot_protection", "mos_ot_release")),
                ),
                unit=TEMP,
            ),
        ),
        "Each release sits on the safe side of its protection value.",
    ),
    Page(
        "Balancing & capacity",
        (
            Section(
                "Balancing",
                ("Value",),
                (
                    ("Trigger delta (V)", ("balance_trigger_delta",)),
                    ("Start voltage (V)", ("balance_start_voltage",)),
                    ("Max current (A)", ("max_balance_current",)),
                ),
            ),
            Section("Capacity", ("Value",), (("Design (Ah)", ("design_capacity",)),)),
        ),
    ),
)
