"""What a BMS driver provides. Subclass Driver, set the class attributes and
override the methods marked as required."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from ..block import Area, Block, Identity
from ..framing import MODBUS
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

    Frames come from `framing` (Modbus RTU by default). A BMS with another
    wire protocol sets `framing` and, if the framing holds session state,
    overrides `new_framing`.
    """

    name: str  # CLI --bms value and backup "bms" field, e.g. "eg4-ll"
    label: str  # shown to people, e.g. "EG4-LL"
    table: SettingTable
    areas: tuple[Area, ...]  # what one block read covers, in order
    serial: SerialParams = SerialParams()
    address: int = 1
    address_choices: range | None = None  # None: always at `address`
    framing = MODBUS  # the wire protocol; new_framing() gives each device its own state
    broadcast_address: int | None = 0  # an address every device on the bus obeys, or None
    find_addresses: range | None = None  # addresses Find tries; within address_choices
    find_probe: Area | None = None  # the quick read Find uses to see who answers
    serial_is_unique: bool = False  # True when the serial tells packs apart
    pages: tuple[Page, ...] = ()
    live_reads: tuple[Area | tuple[int, int], ...] = ()  # Area (or (start, count)) reads, concatenated for decode_live
    supports_clock: bool = False
    connect_note_html: str = ""  # banner above the connect bar
    connection_help: str = ""  # shown when the device does not answer
    no_reply_hint: str = ""  # one sentence for the status bar
    read_error_hint: str = ""  # added to the error when the device refuses a block read
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

    def check_device(self, device, block: Block) -> str | None:
        """Runs after every block read (reads only). A reason here turns writes off."""
        return None

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        """Read-only values worth showing next to the identity, as (label, text)."""
        return []

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        raise NotImplementedError(f"{self.label} has no clock")

    # --- provided -----------------------------------------------------------

    def block(self, *areas) -> Block:
        """A block from one register list per area (a single list for one area)."""
        return Block(self, tuple(tuple(a) for a in areas))

    def new_framing(self):
        """The framing for one device session (the shared one when it holds no state)."""
        return self.framing

    def write_frame(self, setting, raw: int, address: int | None = None, framing=None) -> bytes:
        """The frame that writes `setting` (all of its registers) to `raw`."""
        f = framing or self.framing
        return f.write_request(self.address if address is None else address, setting.register, setting.encode(raw))

    def clock_frame(self, t: datetime, address: int | None = None, framing=None) -> bytes:
        register, values = self.clock_write(t)
        return (framing or self.framing).write_request(self.address if address is None else address, register, values)

    def open_device(self, transport, log, address: int | None = None):
        from ..device import ModbusDevice

        return ModbusDevice(self, transport, log, address)

    @cached_property
    def ordered_keys(self) -> list[str]:
        return [k for page in self.pages for k in page.keys]

    @cached_property
    def page_of(self) -> dict[str, int]:
        return {k: i for i, page in enumerate(self.pages) for k in page.keys}

    def __repr__(self) -> str:
        return f"<{self.label} driver>"
