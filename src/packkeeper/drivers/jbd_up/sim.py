"""A simulated JBD-UP pack speaking the real 0x78/0x79 framing. The values are
invented test data for a 16S 100 Ah LiFePO4 pack, not JBD defaults."""

from __future__ import annotations

import json
import struct
import time
from datetime import datetime
from pathlib import Path

from ...modbus import crc_ok, with_crc
from ...transport import NoReply
from . import controls as C, registers as R
from .framing import FN_READ, FN_WRITE


def _t(c: float) -> int:
    return round((c + 50) * 10)


# Six-register groups: alarm, alarm recovery, alarm delay, protection, protection recovery, protection delay.
GROUPS = {
    0x1800: (3600, 3500, 2000, 3650, 3550, 2000),  # cell OV, mV
    0x180C: (2900, 3000, 2000, 2800, 2950, 2000),  # cell UV
    0x1818: (5760, 5600, 2000, 5840, 5680, 2000),  # pack OV, 0.01 V
    0x1824: (4640, 4800, 2000, 4480, 4720, 2000),  # pack UV
    0x1858: (_t(50), _t(45), 2000, _t(55), _t(50), 2000),  # charge OT
    0x1864: (_t(2), _t(5), 2000, _t(0), _t(5), 2000),  # charge UT
    0x1870: (_t(55), _t(50), 2000, _t(60), _t(55), 2000),  # discharge OT
    0x187C: (_t(-15), _t(-10), 2000, _t(-20), _t(-15), 2000),  # discharge UT
    0x1888: (_t(85), _t(75), 2000, _t(95), _t(80), 2000),  # MOSFET OT
    0x1894: (_t(55), _t(50), 2000, _t(60), _t(55), 2000),  # ambient OT
    0x18A0: (_t(-15), _t(-10), 2000, _t(-20), _t(-15), 2000),  # ambient UT
    0x18AC: (80, 60, 2000, 100, 80, 2000),  # temperature diff, 0.1 °C
    0x18B8: (300, 200, 2000, 500, 300, 2000),  # cell diff, mV
    0x18C4: (10, 15, 2000, 5, 10, 2000),  # SOC low, %
}
WORDS = {
    # charge OC 1: alarm, alarm recovery, alarm delay, protection, protection delay, release delay, lockouts
    0x1830: (1000, 950, 2000, 1050, 2000, 30000, 3),
    0x183E: (1500, 1000, 30000),  # charge OC 2: protection, delay, release delay
    0x1844: (1000, 950, 2000, 1050, 2000, 30000, 3),  # discharge OC 1
    0x1852: (1500, 1000, 30000),  # discharge OC 2
    0x1C00: (0, 0),  # pack type LFP, cell maker Firm1
    0x1C04: (3400, 30, _t(0), _t(5), 5680, 2000),  # balance start V, delta, heater on/off, full-charge V/I
    0x1C76: (0,),  # CAN protocol
    0x1C78: (2900, 60, 0),  # sleep voltage, delay, balance mode
    0x1C7E: (0, 0, 0),  # RS485 protocol, CAN baud, RS485 baud
    0x1C84: (10, 120),  # standby sleep current, static sleep delay
    0x2000: (10000, 10000, 7600, 7600, 100, 37),  # design, full, remaining, SOC, SOH, cycles
    0x201A: (3000, 5, 3200, 20, 3300, 60, 3400, 95),  # SOC table (mV, %) x4
    0x202A: (560, 500, 460, 1000, 16, 250, 4, 3000, 2, 0, 0x0001),  # requests, cells, shunt, sensors, SC, SOC cfg, function bits
    0x2810: (1, 2, 2, 6, 0, 0x0011),  # boot, hw, app major, app minor, test, protocol
}
TEXTS = {0x281C: ("JBD-UP16S010", 16), 0x282C: ("ECOWORTHY48V", 16)}
CLOCK = datetime(2026, 10, 23, 8, 0, 0)
DEFAULT_FLOORS = {0x1C06: 20}  # keeps the old balance delta below 0.020 V, like a pack might
# Where a block read ends, whatever end the request names: the lengths a real pack sent.
SENDS = {0x1000: 0x10A2, 0x1800: 0x18D0, 0x1C00: 0x1C88, 0x2810: 0x283C, 0x2900: 0x290E}


def _text(s: str, nbytes: int) -> list[int]:
    b = s.encode().ljust(nbytes, b"\0")
    return [b[i] << 8 | b[i + 1] for i in range(0, nbytes, 2)]


def _live(serial_cells: int = 16) -> bytes:
    cells = [3300 + i for i in range(serial_cells)]
    temps = [_t(24.0), _t(25.1), _t(24.8), _t(25.0)]
    head = struct.pack(
        ">HHI" + "H" * 8 + "II" + "H" * 18,  # 0x44 bytes: everything up to and including the cell count
        5283, 0, 300000 - 320,  # 52.83 V, (pad), -3.20 A
        7600, 7600, 10000, 10000,  # SOC 76.00 %, remaining, full, nominal (0.01 Ah)
        _t(31.2), _t(24.0), 2, 100,  # MOSFET, ambient, discharging, SOH
        0, 0,  # protection, alarm bits
        0b11, 0b10, 37,  # MOSFET bits (dsg+chg), signals (load), cycles
        16, 3315, 1, 3300, 3307,  # max cell idx/mV, min cell idx/mV, average
        2, _t(25.1), 1, _t(24.0), _t(24.8),  # max temp idx/val, min temp idx/val, average
        576, 500, 448, 1000,  # inverter limits: 57.6 V, 50.0 A, 44.8 V, 100.0 A
        serial_cells,
    )
    body = head + b"".join(struct.pack(">H", c) for c in cells)
    body += struct.pack(">H", len(temps)) + b"".join(struct.pack(">H", t) for t in temps)
    body += struct.pack(">I", 0)  # balancing bitmap
    return body.ljust(2 * R.LIVE.count, b"\0")


class FakeJbd:
    def __init__(
        self,
        *,
        address: int = 0,
        serial: str = "SIM-PACK-1",
        large_capacity: bool = False,
        floors: dict[int, int] | None = None,
        key: int | None = None,
        protocol: int = 0x0011,
        state_file: Path | None = None,
    ):
        self.address = address
        self.port = "simulated pack"
        self.mem: dict[int, int] = {}
        for base, words in {**GROUPS, **WORDS}.items():
            for i, w in enumerate(words):
                self.mem[base + 2 * i] = w
        for reg, (text, n) in {**TEXTS, R.REG_PACK_SN: (serial, 30)}.items():
            for i, w in enumerate(_text(text, n)):
                self.mem[reg + 2 * i] = w
        self.mem[R.REG_PROTOCOL] = protocol
        self.key = (protocol & 0xFF or 0x11) if key is None else key  # the key this pack accepts
        self._set_clock(CLOCK)
        live = _live()
        for i in range(0, len(live), 2):
            self.mem[R.LIVE.start + i] = int.from_bytes(live[i : i + 2], "big")
        if large_capacity:
            self.mem[R.REG_SIGNALS] |= R.LARGE_CAPACITY
        self.floors = DEFAULT_FLOORS if floors is None else floors
        self.writes: list[tuple[int, list[int]]] = []
        self.sends = dict(SENDS)
        self.restart_s: float | None = 1.5  # silent this long after a restart; None: never answers again; 0: never quiet
        self.sleep_s: float | None = 0.0  # silent this long after standby; None: never wakes
        self.standby_acks = True  # False: falls asleep before acking standby (the real pack under load, 2026-11-26)
        self.ignore: set[int] = set()  # registers whose writes are acked but not applied
        self.stuck_mosfets = False  # Tooling mode leaves the MOSFETs on
        # Leaving Tooling, discharge comes on this long after charge (a real pack: a few s, 2026-11-26);
        # None: the BMS holds it off.
        self.discharge_lag_s: float | None = 0.0
        self._discharge_at: float | None = 0.0
        self._silent_until: float | None = 0.0
        self._standby_reads = 0
        self._status = self.mem[R.REG_STATE]
        self.blank = False
        self.state_file = state_file
        self._clock_base: tuple[datetime, datetime] | None = None
        if state_file and state_file.exists():
            for reg, v in json.loads(state_file.read_text()).get("registers", {}).items():
                self.mem[int(reg, 0)] = v

    def word(self, reg: int) -> int:
        return self.mem.get(reg, 0)

    def _set_clock(self, t: datetime) -> None:
        for i, v in enumerate((t.year, t.month, t.day, t.hour, t.minute, t.second)):
            self.mem[R.CLOCK.start + 2 * i] = v

    def _clock_now(self) -> None:
        if self._clock_base is not None:
            set_at, base = self._clock_base
            self._set_clock(base + (datetime.now() - set_at))

    def _control(self, register: int, value: int, mode_before: int) -> None:
        """What the captured controls did to a real pack (2026-10-24)."""
        if register == C.REG_MODE and value == C.TOOLING:
            self.mem[C.REG_MOS] = 0  # entering Tooling switched both MOSFETs off
        elif register == C.REG_MODE and value == C.NORMAL and mode_before == C.TOOLING:
            lag = self.discharge_lag_s
            self._discharge_at = None if lag is None else time.monotonic() + lag
        elif register == C.REG_SLEEP and value == C.STANDBY:
            self.mem[register] = 0
            self._standby_reads = 2
            if self.sleep_s != 0:
                self._silent_until = None if self.sleep_s is None else time.monotonic() + self.sleep_s
        elif register == C.REG_RESTART and value == C.RESTART:
            self.mem[register] = 0
            self._silent_until = None if self.restart_s is None else time.monotonic() + self.restart_s

    def _live_state(self) -> None:
        """The live I/O and status words, from the mode the controls left the pack in."""
        io = self.mem.get(R.REG_IO, 0) & ~0b11
        if self.mem.get(C.REG_MODE, 0) == C.TOOLING and not self.stuck_mosfets:
            mos = self.mem.get(C.REG_MOS, 0)
            io |= (0b10 if mos & 1 else 0) | (0b01 if mos & 2 else 0)
        else:
            on = self._discharge_at is not None and time.monotonic() >= self._discharge_at
            io |= 0b10 | (0b01 if on else 0)
        self.mem[R.REG_IO] = io
        self.mem[R.REG_STATE] = 0 if not io & 0b01 or self._standby_reads else self._status
        self._standby_reads = max(0, self._standby_reads - 1)

    def transact(self, request: bytes) -> bytes:
        if self._silent_until is None or time.monotonic() < self._silent_until:
            raise NoReply("simulated pack is restarting or asleep")
        if not crc_ok(request) or request[0] != self.address:
            raise NoReply("simulated pack did not answer")
        addr, fn, start, end, n = struct.unpack(">BBHHH", request[:8])
        if fn == FN_READ:
            self._clock_now()
            if start == R.LIVE.start:
                self._live_state()
            end = self.sends.get(start, end)
            words = [0 if self.blank and R.PROTECTION.start <= a < 0x1900 else self.word(a) for a in range(start, end, 2)]
            data = b"".join(struct.pack(">H", w) for w in words)
            return with_crc(struct.pack(">BBHHH", addr, fn, start, end, len(data)) + data)
        if fn == FN_WRITE:
            body = request[8:-2]
            if len(body) != n or body[0] != self.key or body[1:4] != b"JBD":
                raise NoReply("simulated pack ignored a write with the wrong key")
            values = [struct.unpack(">H", body[4 + i : 6 + i])[0] for i in range(0, n - 4, 2)]
            self.writes.append((start, values))
            if start in self.ignore:
                return with_crc(struct.pack(">BBHHH", addr, fn, start, end, 0))  # acked, not applied
            mode_before = self.mem.get(C.REG_MODE, 0)
            for i, v in enumerate(values):
                reg = start + 2 * i
                if reg in self.floors and v < self.floors[reg]:
                    continue  # acked, not stored
                self.mem[reg] = v
            if start == R.CLOCK.start:
                self._clock_base = (datetime.now(), datetime(*values[:6]))
            self._control(start, values[0], mode_before)
            self._save()
            if start == C.REG_SLEEP and not self.standby_acks:
                raise NoReply("simulated pack fell asleep before acking")
            return with_crc(struct.pack(">BBHHH", addr, fn, start, end, 0))
        raise NoReply("simulated pack ignored an unknown function")

    def _save(self) -> None:
        if self.state_file:
            regs = {f"0x{r:04X}": v for r, v in sorted(self.mem.items()) if r >= 0x1800}
            self.state_file.write_text(json.dumps({"registers": regs}, indent=1))

    def close(self) -> None:
        pass


class FakeJbdBus:
    """Several simulated packs on one line; each answers only its own address."""

    def __init__(self, packs: list[FakeJbd]):
        self.packs = {p.address: p for p in packs}
        self.port = "simulated packs"
        self.timeout = 1.0

    def transact(self, request: bytes) -> bytes:
        pack = self.packs.get(request[0])
        if pack is None:
            raise NoReply("no simulated pack at that address")
        return pack.transact(request)

    def close(self) -> None:
        pass
