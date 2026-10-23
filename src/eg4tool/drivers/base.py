"""What a BMS driver provides. Subclass Driver, set the class attributes and
override the methods marked as required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from .. import modbus
from ..block import Block, Identity
from ..layout import Page
from ..live import Live
from ..settings import SettingTable
from ..transport import SerialParams


@dataclass(frozen=True)
class Profile:
    """A built-in reference block, e.g. the values a pack ships with."""

    name: str
    label: str
    short: str
    block: Block


class Driver:
    name: str  # CLI --bms value and backup "bms" field, e.g. "eg4-ll"
    label: str  # shown to people, e.g. "EG4-LL"
    table: SettingTable
    block_start: int
    block_count: int
    serial: SerialParams = SerialParams()
    address: int = 1
    pages: tuple[Page, ...] = ()
    live_reads: tuple[tuple[int, int], ...] = ()  # (start, count) reads, concatenated for decode_live
    supports_clock: bool = False
    connect_note_html: str = ""  # banner above the connect bar
    connection_help: str = ""  # shown when the device does not answer
    no_reply_hint: str = ""  # one sentence for the status bar
    setup_hint: str = ""  # how to connect a pack, e.g. for the copy steps
    clock_note: str = ""

    # --- required -----------------------------------------------------------

    def is_writable(self, register: int) -> bool:
        raise NotImplementedError

    def decode_live(self, registers: list[int]) -> Live:
        raise NotImplementedError

    def simulator(self, state_file: Path | None = None):
        """A transport that behaves like a device of this kind."""
        raise NotImplementedError

    # --- optional -----------------------------------------------------------

    @cached_property
    def profiles(self) -> dict[str, Profile]:
        return {}

    def identity(self, block: Block) -> Identity:
        return Identity()

    def blank_reason(self, block: Block) -> str | None:
        """Why this read must not be used, or None."""
        if block.thresholds_blank:
            return f"the {self.label} answered with every writable register zero; refusing to use this read"
        return None

    def identity_warnings(self, block: Block) -> list[str]:
        """Soft checks that the device really is this kind of BMS."""
        return []

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        raise NotImplementedError(f"{self.label} has no clock")

    # --- provided -----------------------------------------------------------

    def block(self, registers) -> Block:
        return Block(self, tuple(registers))

    def write_frame(self, register: int, raw: int) -> bytes:
        return modbus.write_request(self.address, register, [raw])

    def clock_frame(self, t: datetime) -> bytes:
        register, values = self.clock_write(t)
        return modbus.write_request(self.address, register, values)

    def open_device(self, transport, log):
        from ..device import ModbusDevice

        return ModbusDevice(self, transport, log)

    @cached_property
    def ordered_keys(self) -> list[str]:
        return [k for page in self.pages for k in page.keys]

    @cached_property
    def page_of(self) -> dict[str, int]:
        return {k: i for i, page in enumerate(self.pages) for k in page.keys}

    def __repr__(self) -> str:
        return f"<{self.label} driver>"
