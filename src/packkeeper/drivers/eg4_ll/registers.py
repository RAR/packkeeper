"""EG4-LL register layout (RS232 on the right-most paralleling port)."""

from __future__ import annotations

from datetime import datetime

REG_BLOCK = 0x2D  # parameter block FC03 0x2D x91, 0x2D-0x87
BLOCK_COUNT = 0x5B
REG_BLOCK_LAST = REG_BLOCK + BLOCK_COUNT - 1
REG_CLOCK = 0x2D  # packed clock, 0x2D-0x2E, high word first


def is_writable(reg: int) -> bool:
    """Writable thresholds: not the clock (0x2D-0x2E), not identity (0x69-0x7F)."""
    return 0x2F <= reg <= 0x68 or 0x80 <= reg <= REG_BLOCK_LAST


REG_MODEL, MODEL_REGS = 0x69, 11
REG_FIRMWARE, FIRMWARE_REGS = 0x75, 3
REG_SERIAL, SERIAL_REGS = 0x78, 8
ADDRESS = 1  # over RS232 the pack answers at address 1 whatever the DIP ID


def pack_clock(t: datetime) -> int:
    """BMS clock: year-2000 << 26 | month << 22 | day << 17 | hour << 12 | min << 6 | sec."""
    if not 2000 <= t.year <= 2063:
        raise ValueError(f"year {t.year} does not fit the BMS clock")
    return (
        (t.year - 2000) << 26 | t.month << 22 | t.day << 17 | t.hour << 12 | t.minute << 6 | t.second
    )


def unpack_clock(packed: int) -> datetime | None:
    try:
        return datetime(
            2000 + (packed >> 26),
            (packed >> 22) & 0x0F,
            (packed >> 17) & 0x1F,
            (packed >> 12) & 0x1F,
            (packed >> 6) & 0x3F,
            packed & 0x3F,
        )
    except ValueError:
        return None


BLANK_MESSAGE = (
    "the pack answered with every threshold zero. That is what the RS485 "
    "paralleling bus returns: the parameter block only comes over RS232, with the "
    "adapter in the pack's right-most paralleling port. Refusing to use this read."
)

CONNECTION_HELP = """\
The pack only answers over RS232 when no other pack is linked to it:
unplug the link cables from its paralleling ports. (Its DIP ID does not
matter: it always answers at address 1 here.) Also check:
  - the adapter is plugged into the pack's RIGHT-MOST paralleling port
    (there is no separate service port; EG4's own cable uses this one),
  - the adapter is RS232-level (not TTL),
  - TX and RX: adapter TX -> pack RXD, adapter RX -> pack TXD, plus GND
    (if nothing decodes, swap TX and RX),
  - the right port is selected (115200 8N1)."""

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables from the pack's paralleling ports, then plug the "
    "USB-RS232 adapter into its <b>right-most</b> paralleling port. The DIP ID can stay as it is."
)
NO_REPLY_HINT = "Check the link cables are unplugged and the adapter is in the right-most paralleling port."
SETUP_HINT = "link cables unplugged, adapter in its right-most paralleling port"
CLOCK_NOTE = (
    "The BMS clock is local time packed as year-2000, month, day, hour, minute, second "
    "(registers 0x2D-0x2E). Setting it is read back to confirm."
)
