"""The EG4-LL driver: lithium rack packs with the ZTR BMS, RS232 on the
right-most paralleling port, Modbus RTU at 115200 8N1, address 1."""

from __future__ import annotations

from datetime import datetime
from functools import cached_property
from pathlib import Path

from ...block import Block, Identity, ascii_text
from ...transport import SerialParams
from ..base import Driver, Profile
from . import live, registers
from .layout import PAGES
from .table import TABLE


class Eg4LL(Driver):
    name = "eg4-ll"
    label = "EG4-LL"
    table = TABLE
    block_start = registers.REG_BLOCK
    block_count = registers.BLOCK_COUNT
    serial = SerialParams(baud=115200)
    address = registers.ADDRESS
    pages = PAGES
    live_reads = ((0x00, 0x12), (0x12, 0x15))
    supports_clock = True
    connect_note_html = registers.CONNECT_NOTE_HTML
    connection_help = registers.CONNECTION_HELP
    no_reply_hint = registers.NO_REPLY_HINT
    setup_hint = registers.SETUP_HINT
    clock_note = registers.CLOCK_NOTE

    def is_writable(self, register: int) -> bool:
        return registers.is_writable(register)

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    def identity(self, block: Block) -> Identity:
        packed = block.reg(registers.REG_CLOCK) << 16 | block.reg(registers.REG_CLOCK + 1)
        return Identity(
            model=ascii_text(block.regs(registers.REG_MODEL, registers.MODEL_REGS)),
            firmware=ascii_text(block.regs(registers.REG_FIRMWARE, registers.FIRMWARE_REGS)),
            serial=ascii_text(block.regs(registers.REG_SERIAL, registers.SERIAL_REGS)),
            clock=registers.unpack_clock(packed),
            clock_raw=packed,
        )

    def blank_reason(self, block: Block) -> str | None:
        return registers.BLANK_MESSAGE if block.thresholds_blank else None

    def identity_warnings(self, block: Block) -> list[str]:
        # Every EG4-LL seen so far reports a ZTR- model. Warn only: other variants may differ.
        model = block.model
        if model.startswith("ZTR-"):
            return []
        return [f"the model text '{model}' is not the ZTR-... seen on EG4-LL packs; is the right BMS type selected?"]

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        packed = registers.pack_clock(t)
        return registers.REG_CLOCK, [packed >> 16, packed & 0xFFFF]

    @cached_property
    def profiles(self) -> dict[str, Profile]:
        from .profiles import build

        return build(self)

    def simulator(self, state_file: Path | None = None):
        from . import sim  # looked up per call, so tests can patch sim.FakePack

        return sim.FakePack(state_file=state_file)


DRIVER = Eg4LL()
