"""The JK-PB driver: JK PB-series inverter BMS (PB2A16S20P), RS485 Modbus RTU
at 115200 8N1, one pack at a time, address from its DIP switches."""

from __future__ import annotations

from pathlib import Path

from ...block import Area, Block, Identity, ascii_text
from ...device import READ_RETRIES
from ...errors import DeviceRefused
from ...framing import ModbusFraming
from ...modbus import ModbusError
from ...transport import NoReply, SerialParams
from ..base import Driver
from . import live, registers as R
from .layout import PAGES
from .table import build_table

# Bits 10-15 as esphome-jk-bms names them (jk_bms_ble.cpp, "JK-PB2A16S-20P v14"). A real PB2A16S20P
# (v15) read 0x3000 here (2026-11-26): bits 12 and 13, the two extra discharge OCP stages.
FEATURES = {
    1 << 0: "heating", 1 << 1: "temperature sensor disabled", 1 << 2: "GPS heartbeat", 1 << 3: "port RS485",
    1 << 4: "LCD always on", 1 << 5: "special charger", 1 << 6: "smart sleep", 1 << 7: "parallel limit off",
    1 << 8: "timed data storage", 1 << 9: "charging float mode", 1 << 10: "button triggers emergency",
    1 << 11: "dry contact alarm intermittent", 1 << 12: "discharge OCP II", 1 << 13: "discharge OCP III",
    1 << 14: "GPS locked charging", 1 << 15: "GPS locked discharging",
}


class JkFraming(ModbusFraming):
    max_read = R.MAX_READ
    # A real PB2A16S20P (v15, 2026-11-26) ignored most requests sent the moment its last reply ended,
    # and answered every one sent 50 ms or more after it.
    request_gap = 0.1


class JkPB(Driver):
    name = "jk-pb"
    label = "JK-PB"
    serial = SerialParams(baud=115200)
    framing = JkFraming()
    address = 1
    address_choices = range(0, 248)
    pages = PAGES
    supports_clock = True
    connect_note_html = R.CONNECT_NOTE_HTML
    connection_help = R.CONNECTION_HELP
    no_reply_hint = R.NO_REPLY_HINT
    read_error_hint = R.READ_ERROR_HINT
    setup_hint = R.SETUP_HINT
    clock_note = R.CLOCK_NOTE

    def __init__(self, stride: int = R.STRIDE):
        self.stride = stride
        self.table = build_table(stride)
        self.areas = R.areas(stride)
        self.live_reads = R.live_reads(stride)
        self._writable = frozenset(self.table.covering)

    def _a(self, area: int, offset: int) -> int:
        return R.addr(area, offset, self.stride)

    def _u32(self, block: Block, area: int, offset: int) -> int:
        hi, lo = block.reg(self._a(area, offset)), block.reg(self._a(area, offset + 2))
        return hi << 16 | lo if R.WORD_ORDER == "high_first" else lo << 16 | hi

    def is_writable(self, register: int) -> bool:
        return register in self._writable

    def decode_live(self, regs: list[int]):
        return live.decode(regs)

    def identity(self, block: Block) -> Identity:
        secs = self._u32(block, R.LIVE, R.CLOCK_OFFSET)
        hw = ascii_text(block.regs(self._a(R.DEVICE, 0x10), 4))
        sw = ascii_text(block.regs(self._a(R.DEVICE, 0x18), 4))
        return Identity(
            model=ascii_text(block.regs(self._a(R.DEVICE, 0x00), 8)),
            firmware=f"{sw} (hw {hw})",
            clock=R.unpack_clock(secs),
            clock_raw=secs,
        )

    def blank_reason(self, block: Block) -> str | None:
        # Replaces the all-zero check on writable registers: the settings area holds every one of them.
        if not any(block.areas[0]):
            return R.BLANK_MESSAGE
        s = self.table["cell_ov_protection"]
        ov = block.raw(s.key)
        if 2000 <= ov <= 4500:
            return None
        swapped = s.decode([block.reg(r) for r in reversed(s.registers)])
        if 2000 <= swapped <= 4500:
            return (
                f"cell over-voltage reads as {ov} mV, but {swapped} mV with its two registers swapped: "
                "the 32-bit word order looks wrong. Refusing to use this read."
            )
        return (
            f"cell over-voltage reads as {ov} mV, outside 2000-4500 mV: this does not look like a "
            f"{self.label} settings block. Refusing to use this read."
        )

    def check_device(self, device, block: Block) -> str | None:
        s = self.table["cell_ov_protection"]
        want = [block.reg(r) for r in s.registers]
        other = R.SETTINGS + (R.OV_OFFSET if self.stride == 1 else R.OV_OFFSET // 2)

        def probe(address: int) -> list[int] | None:
            try:
                return device.read(address, 2, retries=READ_RETRIES)
            except (DeviceRefused, NoReply, ModbusError):
                return None

        mine, theirs = probe(s.register), probe(other)
        if mine == want and theirs != want:
            return None

        def show(v: list[int] | None) -> str:
            return "no answer" if v is None else " ".join(f"{w:04X}" for w in v)

        expect = "byte" if self.stride == 2 else "word"
        return (
            f"the register numbering could not be confirmed: cell over-voltage read alone at 0x{s.register:04X} "
            f"gave {show(mine)}, at 0x{other:04X} gave {show(theirs)}, and the full read has {show(want)}. "
            f"This build expects {expect} addressing"
        )

    def identity_warnings(self, block: Block) -> list[str]:
        model = block.model
        if model.startswith(("JK_PB", "JK-PB")):
            return []
        return [f"the model text '{model}' is not the JK_PB... of a PB-series BMS; is the right BMS type selected?"]

    def probe_reads(self):
        # Small reads first, then longer ones: which addresses and lengths the pack accepts
        # shows its register numbering (cell OV at 0x100C by byte, 0x1006 by word) and its read limit.
        S, L, D = R.SETTINGS, R.LIVE, R.DEVICE
        reads = [
            ("settings start", Area(S, 2)),
            ("cell OV, numbered by byte", Area(S + R.OV_OFFSET, 2)),
            ("cell OV, numbered by word", Area(S + R.OV_OFFSET // 2, 2)),
            *((f"settings, {n} registers", Area(S, n)) for n in (8, 16, 32, 64, 100, 125)),
            ("live start", Area(L, 2)),
            *((f"live, {n} registers", Area(L, n)) for n in (16, 64)),
            ("model text", Area(D, 8)),
            ("device info, 20 registers", Area(D, 20)),
            ("clock, numbered by byte", Area(L + R.CLOCK_OFFSET, 2)),
            ("clock, numbered by word", Area(L + R.CLOCK_OFFSET // 2, 2)),
        ]
        return tuple(reads)

    def clock_write(self, t):
        secs = R.pack_clock(t)
        # High word first, matching R.WORD_ORDER; change both together.
        return self._a(R.COMMANDS, R.CLOCK_SET_OFFSET), [secs >> 16, secs & 0xFFFF]

    def info_lines(self, block: Block) -> list[tuple[str, str]]:
        def u32(off: int) -> int:
            return self._u32(block, R.SETTINGS, off)

        def on(v: int) -> str:
            return "on" if v else "off"

        bits = block.reg(self._a(R.SETTINGS, 0x114))
        features = [name for bit, name in FEATURES.items() if bits & bit]
        return [
            ("Cells", str(u32(0x6C))),
            ("Switches", f"charge {on(u32(0x70))}, discharge {on(u32(0x74))}, balancer {on(u32(0x78))}"),
            ("Address", str(u32(0x108))),
            ("Pre-charge", f"{u32(0x10C)} s"),
            ("Features", f"{', '.join(features) or 'none'} (0x{bits:04X})"),
            ("Sleep time", f"{block.reg(self._a(R.SETTINGS, 0x118)) >> 8} h"),
        ]

    def simulator(self, state_file: Path | None = None):
        from . import sim

        return sim.FakeJK(self, state_file=state_file)
