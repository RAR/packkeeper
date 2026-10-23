"""A made-up BMS that shares nothing with EG4-LL: other registers, address,
baud, rules and layout. It proves the engine has no hidden EG4 assumptions.
Test-only; never registered in the shipped app."""

from functools import cached_property

from eg4tool.block import Identity, ascii_text
from eg4tool.drivers.base import Driver, Profile
from eg4tool.layout import TEMP, Page, Section
from eg4tool.live import Live
from eg4tool.settings import LinkedPair, SettingTable
from eg4tool.sim import FakeModbusDevice
from eg4tool.transport import SerialParams

START, COUNT = 0x100, 8  # 0x100 name, 0x101-0x106 settings, 0x107 unknown

SPEC = {
    "low_cut": (0x101, 0.01, 0, "V", None, 2),
    "high_cut": (0x102, 0.01, 0, "V", None, 2),
    "top_cut": (0x103, 0.01, 0, "V", None, 2),
    "trip_a": (0x104, 1, 0, "A", None, 0),
    "trip_b": (0x105, 1, 0, "A", None, 0),
    "hot_cut": (0x106, 1, -40, "°C", None, 0),
}
LIMITS = {
    "low_cut": (2.5, 3.0, 0.01),
    "high_cut": (3.3, 3.7, 0.01),
    "top_cut": (3.4, 3.8, 0.01),
    "trip_a": (10, 200, 1),
    "trip_b": (10, 200, 1),
    "hot_cut": (40, 80, 1),
}
ORDER = [("low_cut", "high_cut", True), ("high_cut", "top_cut", True)]

IMAGE = [0] * (START + COUNT)
IMAGE[0:6] = [5120, 150, 80, 3200, 3210, 3205]  # 51.20 V, +1.50 A, 80 %, three cells
IMAGE[START:] = [0x5459, 280, 350, 365, 100, 100, 100, 7]  # "TY", ..., hot_cut 60 °C, unknown 7


class Toy(Driver):
    name = "toy"
    label = "Toy BMS"
    table = SettingTable.build(SPEC, LIMITS, ORDER, linked=[LinkedPair("trip_a", "trip_b", "toy pair")])
    block_start = START
    block_count = COUNT
    serial = SerialParams(baud=9600)
    address = 7
    live_reads = ((0x00, 6),)
    pages = (
        Page(
            "All",
            (
                Section("Voltage", ("Low", "High", "Top"), (("Cell", ("low_cut", "high_cut", "top_cut")),), unit="V"),
                Section("Current", ("A", "B"), (("Trip", ("trip_a", "trip_b")),), unit="A"),
                Section("Heat", ("Cut",), (("Hot", ("hot_cut",)),), unit=TEMP),
            ),
        ),
    )
    connection_help = "toy: check the toy cable"

    def is_writable(self, register):
        return START + 1 <= register < START + COUNT

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(
            pack_voltage=r[0] / 100, current=r[1] / 100, soc=r[2], soh=100,
            cells=[v / 1000 for v in r[3:6]], status_text="Idle",
        )

    @cached_property
    def profiles(self):
        return {"toy-default": Profile("toy-default", "Toy defaults", "Toy defaults", self.block(IMAGE[START:]))}

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, state_file=state_file, state_from=START)


TOY = Toy()
