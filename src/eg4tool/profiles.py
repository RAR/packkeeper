"""Built-in profiles: parameter blocks captured from real packs.

EG4_SHIPPED is the 0x2D x91 reply of a ZTR-16S200A (BMS fw S02T11) captured
on 2026-10-20 before any changes (tests/rs232_service_frames.txt in
esphome-eg4-bms). Restoring it goes through the same diff/confirm path as any
backup; there is no separate factory reset.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import modbus
from .block import Block

# Raw replies, CRC included.
EG4_SHIPPED_FRAME = bytes.fromhex(
    "0103B66AA8B66D0008119400000000000000000000000000010D480014000A0000000009C408FC0C1C122A"
    "118012C00E740ED80D7A172017701590022B01F400000005000500B4003C50145014753057E47530000A00"
    "03000A000300000030002D003200730078006E0023001E00280078007D0073009600A000825A54522D3136"
    "53323030412D31304B572D535444322020205330325431313136343831303032303032303030303100320035"
    "001E0019002300780082006E5161"
)
# Realtime replies captured from the same pack (FC03 0x00 x18, FC03 0x12 x21).
REALTIME_FRAMES = (
    bytes.fromhex(
        "01032414D20A460D020D040D030D040D030D040D040D050D040D040D030D030D040D040D030D0314F1"
    ),
    bytes.fromhex(
        "01032A00170018001800BB00C8006400430001000000000000000001173C14DC0018171718000000100AF000004219"
    ),
)


def registers_of(frame: bytes) -> list[int]:
    if not modbus.crc_ok(frame) or frame[1] != modbus.FC_READ:
        raise ValueError("not a valid FC03 reply")
    data = frame[3:-2]
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]


@dataclass(frozen=True)
class Profile:
    name: str
    label: str
    short: str
    block: Block


EG4_SHIPPED = Profile(
    name="eg4-shipped",
    label="EG4 shipped settings (ZTR-16S200A-10KW-STD2, BMS fw S02T11, captured 2026-10-20)",
    short="EG4 shipped values",
    block=Block(tuple(registers_of(EG4_SHIPPED_FRAME))),
)

PROFILES = {EG4_SHIPPED.name: EG4_SHIPPED}
