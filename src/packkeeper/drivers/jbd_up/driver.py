"""The JBD-UP driver: ECO-WORTHY rack packs with JBD "ES/UP" BMS boards, RS485
at 9600 8N1 with JBD's 0x78/0x79 framing, one pack at a time."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...block import Block, Identity, ascii_text
from ...device import READ_RETRIES
from ...errors import DeviceRefused, FrameError
from ...transport import NoReply, SerialParams
from ..base import Driver
from . import controls as C, live, registers as R
from .framing import DEFAULT_KEY, JbdFraming
from .layout import PAGES
from .table import TABLE


class JbdUp(Driver):
    name = "jbd-up"
    label = "ECO-WORTHY / JBD-UP"
    table = TABLE
    areas = R.AREAS
    serial = SerialParams(baud=9600)
    address = 0
    address_choices = range(0, 64)
    framing = JbdFraming()
    broadcast_address = None  # 0 is an ordinary address in this protocol
    find_addresses = range(0, 16)
    find_probe = R.VERSION
    serial_is_unique = True
    pages = PAGES
    live_reads = (R.LIVE,)
    supports_clock = True
    connect_note_html = R.CONNECT_NOTE_HTML
    connection_help = R.CONNECTION_HELP
    no_reply_hint = R.NO_REPLY_HINT
    setup_hint = R.SETUP_HINT
    clock_note = R.CLOCK_NOTE
    control_registers = C.CONTROL_REGISTERS

    _writable = frozenset(TABLE.covering)

    def new_framing(self):
        return JbdFraming(request_ends=R.REQUEST_ENDS)

    def is_writable(self, register: int) -> bool:
        return register in self._writable

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    @staticmethod
    def key_of(block: Block) -> int:
        return block.reg(R.REG_PROTOCOL) & 0xFF or DEFAULT_KEY

    def identity(self, block: Block) -> Identity:
        model = ascii_text(block.regs(R.REG_HW_NAME, 8)) or ascii_text(block.regs(R.REG_PROJECT, 8))
        p = block.reg(R.REG_PROTOCOL) & 0xFF
        firmware = (f"app {block.reg(R.REG_APP_MAJOR)}.{block.reg(R.REG_APP_MINOR)} "
                    f"(hw {block.reg(R.REG_HW_VER)}, protocol {p >> 4}.{p & 0xF})")
        try:
            clock = datetime(*(block.reg(R.CLOCK.start + 2 * i) for i in range(6)))
        except ValueError:
            clock = None
        return Identity(model=model, firmware=firmware, serial=ascii_text(block.regs(R.REG_PACK_SN, 15)), clock=clock)

    def blank_reason(self, block: Block) -> str | None:
        # Replaces the generic all-zero check: the protection area is what matters here.
        if not any(block.areas[0]):
            return "the pack answered with every protection threshold zero; refusing to use this read"
        ov = block.reg(R.REG_CELL_OV_PROTECT)
        if not 3000 <= ov <= 4500:
            return f"cell OV protection reads as {ov} mV, outside 3000-4500 mV; refusing to use this read"
        cells = block.reg(R.REG_CELLS_IN_SERIES)
        if not 1 <= cells <= 32:
            return f"cells in series reads as {cells}, outside 1-32; refusing to use this read"
        return None

    def check_device(self, device, block: Block) -> str | None:
        device.framing.key = self.key_of(block)
        try:
            # The whole live block, as the stock app reads it: a pack answers block reads.
            signals = device.read_area(R.LIVE, retries=READ_RETRIES)[(R.REG_SIGNALS - R.LIVE.start) // 2]
        except (NoReply, FrameError, DeviceRefused) as e:
            return f"could not read the pack's capacity mode ({e})"
        if signals & R.LARGE_CAPACITY:
            return "this pack reports large-capacity mode (capacities in 0.1 Ah); this build models 0.01 Ah"
        return None

    def identity_warnings(self, block: Block) -> list[str]:
        version = block.areas[R.AREAS.index(R.VERSION)]
        if not any(version) or block.reg(R.REG_PROTOCOL) & 0xFF == 0:
            return [f"the pack reports no protocol version; writes use the default key 0x{DEFAULT_KEY:02X}"]
        return []

    def controls(self):
        return C.CONTROLS

    def clock_write(self, t: datetime) -> tuple[int, list[int]]:
        return R.CLOCK.start, [t.year, t.month, t.day, t.hour, t.minute, t.second]

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        return [
            ("RS485", f"baud code {block.reg(0x1C82)}"),
            ("SOC/SOH", f"{block.reg(0x2006) / 100:.0f} % / {block.reg(0x2008)} %, {block.reg(0x200A)} cycles"),
        ]

    def simulator(self, state_file: Path | None = None):
        from .sim import FakeJbd, FakeJbdBus

        packs = [FakeJbd(address=0, serial="SIM-PACK-1", state_file=state_file),
                 FakeJbd(address=2, serial="SIM-PACK-2"), FakeJbd(address=5, serial="SIM-PACK-3")]
        return FakeJbdBus(packs)
