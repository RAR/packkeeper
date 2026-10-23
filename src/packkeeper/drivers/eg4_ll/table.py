"""EG4-LL settings: the vendored table plus this tool's tightening.

settings_table.py is vendored unchanged from esphome-eg4-bms
(components/eg4_bms/settings_table.py, branch feat/rs232-service cb105bf),
so the component and this tool share one source of truth.
"""

from __future__ import annotations

from ...settings import LinkedPair, SettingTable
from . import settings_table

# Tightened on top of the vendored LIMITS. The pack acks a balance delta below
# its floor and keeps the old value (0.010 V acked, 0.039 V kept; 0.020 V held).
LIMIT_OVERRIDES = {
    "balance_delta_voltage": (0.020, 0.1, 0.001),
}

# settings_table.py has 0x52 = short circuit and 0x54 = discharge OC 2;
# REGISTER_MAP.md says the reverse. Both ship as 300 A, so no read tells them
# apart. Until a deliberate test settles it, they are only set together, to
# the same value.
LINKED = [
    LinkedPair(
        "short_circuit_current",
        "discharge_overcurrent_2_protection",
        "which of 0x52 / 0x54 is which is not settled yet",
    )
]

GROUPS = [
    ("Balancing", ("balance_", "low_capacity")),
    ("Cell voltage", ("cell_",)),
    ("Pack voltage", ("pack_",)),
    ("Charge current", ("charge_overcurrent",)),
    ("Discharge current", ("discharge_overcurrent", "short_circuit")),
    ("Charge temperature", ("charge_under", "charge_over")),
    ("Discharge temperature", ("discharge_under", "discharge_over")),
    ("MOSFET temperature", ("mos_",)),
    ("Ambient temperature", ("ambient_",)),
    ("Heater", ("heater_",)),
]

_WORDS = {
    "overvoltage": "OV",
    "undervoltage": "UV",
    "overcurrent": "OC",
    "overtemp": "OT",
    "undertemp": "UT",
    "mos": "MOSFET",
}


def _group(key: str) -> str:
    for name, prefixes in GROUPS:
        if key.startswith(prefixes):
            return name
    return "Other"


def _label(key: str) -> str:
    words = [_WORDS.get(w, w) for w in key.split("_")]
    text = " ".join(words)
    return text[0].upper() + text[1:]


TABLE = SettingTable.build(
    settings_table.SETTINGS,
    settings_table.LIMITS,
    settings_table.ORDER,
    linked=LINKED,
    overrides=LIMIT_OVERRIDES,
    group=_group,
    label=_label,
)
