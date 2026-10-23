"""JBD-UP register map: areas, text fields and texts. Byte addresses, u16 big-endian."""

from __future__ import annotations

from ...block import Area

STRIDE = 2
LIVE = Area(0x1000, 0x50, STRIDE)  # 0x1000-0x10A0
PROTECTION = Area(0x1800, 0x80, STRIDE)  # 0x1800-0x1900
PACK = Area(0x1C00, 0x50, STRIDE)  # 0x1C00-0x1CA0
CAPACITY = Area(0x2000, 0x28, STRIDE)  # 0x2000-0x2050 (the V1 app's range)
CLOCK = Area(0x2800, 8, STRIDE)  # Y M D h m s, then 2 spare
VERSION = Area(0x2810, 0x18, STRIDE)  # 0x2810-0x2840
AREAS = (PROTECTION, PACK, CAPACITY, CLOCK, VERSION)

REG_SIGNALS = 0x1022  # live signal word; bit 15 = large-capacity mode
LARGE_CAPACITY = 1 << 15
REG_HW_VER, REG_APP_MAJOR, REG_APP_MINOR, REG_PROTOCOL = 0x2812, 0x2814, 0x2816, 0x281A
REG_HW_NAME, REG_PROJECT = 0x281C, 0x282C  # 16 B ASCII each
REG_PACK_SN = 0x1C34  # 30 B ASCII
REG_CELL_OV_PROTECT = 0x1806
REG_CELLS_IN_SERIES = 0x2032

CONNECT_NOTE_HTML = (
    "<b>One pack at a time.</b> Unplug the link cables, plug the USB-RS485 adapter into the pack's RS485 "
    "port, and set <b>Address</b> to its DIP value, or use <b>Find</b>."
)
CONNECTION_HELP = """\
The pack answers on RS485 when it is alone on the line: unplug the link
cables between packs. Also check:
  - A and B are not swapped (if nothing answers, swap them),
  - the address: use Find to see which addresses answer,
  - the baud (9600 unless it was changed in the stock app)."""
NO_REPLY_HINT = "Check the link cables are unplugged, A/B wiring, and the address (use Find)."
SETUP_HINT = "link cables unplugged, adapter in its RS485 port"
CLOCK_NOTE = "The BMS clock is local time, stored as year, month, day, hour, minute, second. Setting it is read back to confirm."
