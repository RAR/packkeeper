"""A made-up BMS with 32-bit and signed settings in a byte-addressed map
(stride 2), like JK's. Test-only; never registered in the shipped app."""

from packkeeper.block import Area, Identity, ascii_text
from packkeeper.drivers.base import Driver
from packkeeper.layout import TEMP, Page, Section
from packkeeper.live import Live
from packkeeper.settings import SettingTable
from packkeeper.sim import FakeModbusDevice

START, COUNT, STRIDE = 0x200, 6, 2  # 0x200 name, 0x202/0x204 amps, 0x206/0x208 cold, 0x20A unknown

SPEC = {
    "amps": (0x202, 0.001, 0, "A", None, 3, 2, False),
    "cold": (0x206, 0.1, 0, "°C", None, 1, 2, True),
}
LIMITS = {"amps": (1, 300, 0.001), "cold": (-20, 10, 0.1)}

IMAGE = [0] * (START + COUNT * STRIDE)
IMAGE[0:2] = [5120, 80]  # 51.20 V, 80 %
for i, v in enumerate([0x5754, 0x0002, 0x49F0, 0xFFFF, 0xFFCE, 9]):  # "WT", 150 A, -5.0 °C, unknown 9
    IMAGE[START + i * STRIDE] = v


class WideToy(Driver):
    name = "wide"
    label = "Wide BMS"
    table = SettingTable.build(SPEC, LIMITS, [], stride=STRIDE)
    areas = (Area(START, COUNT, STRIDE),)
    address = 9
    live_reads = (Area(0x00, 2),)
    pages = (
        Page(
            "All",
            (
                Section("Current", ("Trip",), (("Amps", ("amps",)),), unit="A"),
                Section("Cold", ("Cut",), (("Cold", ("cold",)),), unit=TEMP),
            ),
        ),
    )

    def is_writable(self, register):
        return register in self.table.covering

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(pack_voltage=r[0] / 100, current=0.0, soc=r[1], soh=100, cells=[3.3], status_text="Idle")

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, stride=STRIDE, state_file=state_file, state_from=START)


WIDE = WideToy()
