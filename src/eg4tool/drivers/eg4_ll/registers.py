"""EG4-LL register layout (RS232 on the right-most paralleling port)."""

from __future__ import annotations

REG_BLOCK = 0x2D  # parameter block FC03 0x2D x91, 0x2D-0x87
BLOCK_COUNT = 0x5B
REG_BLOCK_LAST = REG_BLOCK + BLOCK_COUNT - 1
REG_CLOCK = 0x2D  # packed clock, 0x2D-0x2E, high word first


def is_writable(reg: int) -> bool:
    """Writable thresholds: not the clock (0x2D-0x2E), not identity (0x69-0x7F)."""
    return 0x2F <= reg <= 0x68 or 0x80 <= reg <= REG_BLOCK_LAST
