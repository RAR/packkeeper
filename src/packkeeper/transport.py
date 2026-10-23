"""Serial transport and port discovery.

A transport sends one request frame and returns the raw reply frame. It does
no Modbus checking beyond knowing how long a reply is; device.py checks the rest.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SerialParams:
    baud: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1


REPLY_TIMEOUT_S = 1.0
LATE_REPLY_S = 1.5  # longest wait for a timed-out reply to finish arriving
QUIET_S = 0.3  # after a timeout, the line must be silent this long before the next request

# USB vendor IDs of common USB-RS232 bridges, for the port list.
KNOWN_BRIDGES = {
    0x0403: "FTDI",
    0x1A86: "CH340",
    0x067B: "Prolific",
    0x10C4: "Silicon Labs CP210x",
}


class NoReply(Exception):
    """The pack did not answer (or answered only part of a frame)."""


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str
    bridge: str | None

    @property
    def label(self) -> str:
        extra = f" [{self.bridge}]" if self.bridge else ""
        return f"{self.device} - {self.description}{extra}"


def list_ports() -> list[PortInfo]:
    from serial.tools import list_ports as lp

    ports = []
    for p in lp.comports():
        # On macOS use /dev/cu.* only: /dev/tty.* blocks waiting for carrier detect.
        if sys.platform == "darwin" and p.device.startswith("/dev/tty."):
            continue
        bridge = KNOWN_BRIDGES.get(p.vid) if p.vid else None
        ports.append(PortInfo(p.device, p.description or "", p.hwid or "", bridge))
    ports.sort(key=lambda p: (p.bridge is None, p.device))
    return ports


def guess_port() -> str | None:
    """The only USB serial port, if there is exactly one."""
    usb = [p for p in list_ports() if p.bridge or "USB" in p.hwid.upper()]
    return usb[0].device if len(usb) == 1 else None


def resync(buf: bytes, address: int, function: int) -> tuple[bytes, bytes]:
    """Drop leading bytes that cannot start a reply to (address, function).

    Returns (the buffer from a plausible frame start, the dropped bytes). A
    line settling or a stray byte must not make every retry fail the same way.
    """
    from .framing import MODBUS

    return MODBUS.resync(buf, bytes([address, function]))


class SerialTransport:
    def __init__(self, port: str, timeout: float = REPLY_TIMEOUT_S, params: SerialParams = SerialParams(), framing=None):  # noqa: B008 - frozen
        import serial

        from .framing import MODBUS

        self.port = port
        self.framing = framing or MODBUS
        self.timeout = timeout
        self.noise = b""  # bytes dropped before the last reply
        self._late = False  # the last request timed out: its reply may still arrive
        # POSIX ports are shared unless asked otherwise; Windows ports are always exclusive.
        extra = {"exclusive": True} if os.name == "posix" else {}
        self._serial = serial.Serial(
            port, params.baud, bytesize=params.bytesize, parity=params.parity, stopbits=params.stopbits,
            timeout=0.05, write_timeout=1.0, **extra,
        )

    def _drain(self) -> None:
        """Wait for the line to go quiet, so a late reply is not taken as the next one."""
        end = time.monotonic() + LATE_REPLY_S
        quiet_since = time.monotonic()
        while time.monotonic() < end and time.monotonic() - quiet_since < QUIET_S:
            if self._serial.read(256):
                quiet_since = time.monotonic()
        self._late = False

    def transact(self, request: bytes) -> bytes:
        s = self._serial
        if self._late:
            self._drain()
        s.reset_input_buffer()
        s.write(request)
        s.flush()
        self.noise = b""
        deadline = time.monotonic() + self.timeout
        reply = b""
        while time.monotonic() < deadline:
            reply, dropped = self.framing.resync(reply, request)
            self.noise += dropped
            want = self.framing.reply_length(reply)
            n = (want - len(reply)) if want is not None else self.framing.head_len - len(reply)
            if want is not None and n <= 0:
                return reply
            reply += s.read(n)
        self._late = True
        got = self.noise + reply
        self.noise = got  # everything heard, for spotting another master on the line
        raise NoReply(f"no reply from the pack on {self.port}" + (f" (got {got.hex(' ').upper()})" if got else ""))

    def close(self) -> None:
        self._serial.close()
