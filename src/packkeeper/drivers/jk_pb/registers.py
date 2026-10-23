"""JK-PB register map, from JK's RS485 Modbus V1.1 document.

Fields are given there as byte offsets into four areas. Community configs
for the PB2A16S20P (esphome-jk-bms discussion #501) read cell 2 at 0x1202
and pack voltage as a 32-bit value at 0x1290, high word first: the pack
addresses by byte. STRIDE is that one assumption; check_device confirms it
on every connect, reads only.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ...block import Area
from ...settings import SettingError

STRIDE = 2  # address step between 16-bit registers: 2 = byte addresses
WORD_ORDER = "high_first"
SETTINGS, LIVE, DEVICE, COMMANDS = 0x1000, 0x1200, 0x1400, 0x1600
SETTINGS_BYTES, LIVE_BYTES, DEVICE_BYTES = 0x11A, 0x10E, 0x28
OV_OFFSET = 0x0C  # cell over-voltage protection: the field check_device reads alone
CLOCK_OFFSET = 0x100  # RTC ticks in the live area, seconds since 2020-01-01
CLOCK_SET_OFFSET = 0x12  # "time calibration" in the command area
EPOCH = datetime(2020, 1, 1)


def addr(area: int, offset: int, stride: int = STRIDE) -> int:
    """The address of the register holding byte `offset` of `area`."""
    return area + (offset // 2) * stride


def areas(stride: int) -> tuple[Area, ...]:
    return (
        Area(SETTINGS, SETTINGS_BYTES // 2, stride),
        Area(DEVICE, DEVICE_BYTES // 2, stride),
        Area(addr(LIVE, CLOCK_OFFSET, stride), 2, stride),
    )


def live_reads(stride: int) -> tuple[Area, ...]:
    return (Area(LIVE, LIVE_BYTES // 2, stride),)


def pack_clock(t: datetime) -> int:
    secs = int((t - EPOCH).total_seconds())
    if not 0 <= secs <= 0xFFFFFFFF:
        raise SettingError(f"{t} does not fit the BMS clock (seconds since {EPOCH:%Y-%m-%d})")
    return secs


def unpack_clock(secs: int) -> datetime:
    return EPOCH + timedelta(seconds=secs)


BLANK_MESSAGE = "the JK-PB answered with every setting zero; refusing to use this read"

CONNECTION_HELP = """\
The pack answers on RS485 when it is the only one on the line: unplug
the link cables between packs. Also check:
  - the adapter is USB-RS485 and plugged into one of the pack's RS485 ports,
  - A and B are not swapped (if nothing answers, swap them),
  - the address: the pack answers at its DIP-switch address; try 1, then 0,
  - the right port is selected (115200 8N1)."""

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables between packs, then plug the USB-RS485 adapter "
    "into one of the pack's RS485 ports. Set <b>Address</b> to the pack's DIP-switch address."
)
READ_ERROR_HINT = (
    "The JK-PB refused this read. 'packkeeper probe --bms jk-pb' (reads only) shows which addresses "
    "and read lengths it accepts; see the README's JK-PB section."
)
NO_REPLY_HINT = "Check the link cables are unplugged, A/B wiring, and the address (try 1, then 0)."
SETUP_HINT = "link cables unplugged, adapter in one of its RS485 ports"
CLOCK_NOTE = (
    "The BMS clock counts seconds since 2020-01-01 (local time). It is set with JK's time-calibration "
    "command and read back to confirm."
)
