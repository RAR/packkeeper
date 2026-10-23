"""A made-up Modbus BMS with unsafe settings, choices and bits. Test-only."""

from packkeeper.block import Area, Identity, ascii_text
from packkeeper.drivers.base import Driver
from packkeeper.layout import Page, Section
from packkeeper.live import Live
from packkeeper.settings import SettingTable
from packkeeper.sim import FakeModbusDevice

START, COUNT = 0x300, 6  # 0x300 name, 0x301 volts, 0x302 cells, 0x303 chem, 0x304 flags, 0x305 mode

SPEC = {
    "volts": (0x301, 0.01, 0, "V", None, 2),
    "cells": (0x302, 1, 0, None, None, 0),
    "chem": (0x303, 1, 0, None, None, 0),
    "flags": (0x304, 1, 0, None, None, 0),
    "mode": (0x305, 1, 0, None, None, 0),
}
LIMITS = {"volts": (3.0, 3.6, 0.01), "cells": (1, 32, 1), "chem": (0, 1, 1), "flags": (0, 0xFFFF, 1), "mode": (0, 1, 1)}
UNSAFE = {
    "cells": "A wrong count breaks every cell protection.",
    "chem": "Changes the chemistry model.",
    "flags": "Anti-theft can lock the pack out.",
}
CHOICES = {"chem": {0: "LFP", 1: "NCM"}, "mode": {0: "charge only", 1: "static"}}
BITS = {"flags": {0x0001: "heater", 0x0004: "buzzer", 0x0010: "anti-theft"}}

IMAGE = [0] * (START + COUNT)
IMAGE[0:2] = [5120, 80]
IMAGE[START:] = [0x5546, 340, 16, 0, 0x0001, 0]  # "UF", 3.40 V, 16 cells, LFP, heater, charge only


class UnsafeToy(Driver):
    name = "unsafe-toy"
    label = "Unsafe toy"
    table = SettingTable.build(SPEC, LIMITS, [], unsafe=UNSAFE, choices=CHOICES, bits=BITS)
    areas = (Area(START, COUNT),)
    address = 4
    live_reads = (Area(0x00, 2),)
    pages = (
        Page("Main", (Section("Main", ("Value",), (("Volts", ("volts",)), ("Mode", ("mode",)))),)),
        Page("Unsafe", (Section("Unsafe", ("Value",), (("Cells", ("cells",)), ("Chemistry", ("chem",)), ("Flags", ("flags",)))),), unsafe=True),
    )

    def is_writable(self, register):
        return START + 1 <= register < START + COUNT

    def identity(self, block):
        return Identity(model=ascii_text(block.regs(START, 1)))

    def decode_live(self, r):
        return Live(pack_voltage=r[0] / 100, current=0.0, soc=r[1], soh=100, cells=[3.3], status_text="Idle")

    def simulator(self, state_file=None):
        return FakeModbusDevice(IMAGE, self.is_writable, state_file=state_file, state_from=START)


UNSAFE_TOY = UnsafeToy()
