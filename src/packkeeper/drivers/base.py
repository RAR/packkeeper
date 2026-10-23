"""What a BMS driver provides. Subclass Driver, set the class attributes and
override the methods marked as required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from .. import modbus
from ..block import Area, Block, Identity
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
    """One BMS family: its settings table, block layout, texts and simulator.

    The defaults assume Modbus RTU: write_frame, clock_frame, open_device
    (which returns ModbusDevice) and live_reads. A non-Modbus BMS overrides
    open_device to return its own Device. That device's read_block() must call
    block.check_not_blank() before returning, and its write_setting must
    refuse registers where driver.is_writable is false. It also overrides
    write_frame and clock_frame for dry-run output.
    """

    name: str  # CLI --bms value and backup "bms" field, e.g. "eg4-ll"
    label: str  # shown to people, e.g. "EG4-LL"
    table: SettingTable
    areas: tuple[Area, ...]  # what one block read covers, in order
    serial: SerialParams = SerialParams()
    address: int = 1
    pages: tuple[Page, ...] = ()
    live_reads: tuple[Area | tuple[int, int], ...] = ()  # Area (or (start, count)) reads, concatenated for decode_live
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

    def block(self, *areas) -> Block:
        """A block from one register list per area (a single list for one area)."""
        return Block(self, tuple(tuple(a) for a in areas))

    def write_frame(self, setting, raw: int, address: int | None = None) -> bytes:
        """The FC16 frame that writes `setting` (all of its registers) to `raw`."""
        return modbus.write_request(self.address if address is None else address, setting.register, setting.encode(raw))

    def clock_frame(self, t: datetime, address: int | None = None) -> bytes:
        register, values = self.clock_write(t)
        return modbus.write_request(self.address if address is None else address, register, values)

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
