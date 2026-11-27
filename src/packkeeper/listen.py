"""Hearing a line without talking on it: split the bytes into frames and say
what each one is. Used to capture what another program (the stock app, a
master pack) sends, so nothing has to be guessed. Never transmits."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable

from .modbus import crc_ok

JK_MAGIC = bytes([0x55, 0xAA, 0xEB, 0x90])
JK_LEN = 300
JK_KINDS = {1: "settings", 2: "status", 3: "about"}
DISPLAY_MAGIC = bytes([0xA5, 0x5A])
JBD_READ, JBD_WRITE = 0x78, 0x79
JBD_MAX_DATA = 494  # 490 data bytes plus the write key and 'JBD'
JBD_HISTORY = 0x4000  # the stock app reads history with a 0x78 frame here carrying the record index
QUIET_S = 0.5  # a frame still unfinished after this long without a byte is given up as noise
NEED_MORE = -1  # a matcher's answer when the frame may continue past the bytes so far


@dataclass(frozen=True)
class Heard:
    kind: str  # "JBD", "Modbus", "JK", "display" or "noise"
    raw: bytes
    text: str


def _jk(buf: bytes, i: int) -> int:
    head = buf[i : i + 4]
    if head != JK_MAGIC[: len(head)]:
        return 0
    if len(buf) - i < JK_LEN:
        return NEED_MORE
    return JK_LEN if sum(buf[i : i + JK_LEN - 1]) & 0xFF == buf[i + JK_LEN - 1] else 0


def _display(buf: bytes, i: int) -> int:
    head = buf[i : i + 2]
    if head != DISPLAY_MAGIC[: len(head)]:
        return 0
    if len(buf) - i < 3:
        return NEED_MORE
    n = 3 + buf[i + 2]
    return n if len(buf) - i >= n else NEED_MORE


def _crc_frame(buf: bytes, i: int, lengths: list[int | None]) -> int:
    """The first candidate length whose frame has a good CRC; None means the length is not known yet."""
    waiting = False
    for n in lengths:
        if n is None or len(buf) - i < n:
            waiting = True
        elif crc_ok(buf[i : i + n]):
            return n
    return NEED_MORE if waiting else 0


def _jbd(buf: bytes, i: int) -> int:
    if len(buf) - i < 2 or buf[i + 1] not in (JBD_READ, JBD_WRITE):
        return 0
    if len(buf) - i < 8:
        return NEED_MORE
    n = struct.unpack(">H", buf[i + 6 : i + 8])[0]
    return _crc_frame(buf, i, [10 + n]) if n <= JBD_MAX_DATA else 0


def _modbus(buf: bytes, i: int) -> int:
    if len(buf) - i < 2 or buf[i] > 247:
        return 0

    def at(k: int) -> int | None:
        return buf[i + k] if len(buf) - i > k else None

    fn = buf[i + 1]
    if fn == 0x03:
        bc = at(2)
        return _crc_frame(buf, i, [8, None if bc is None else 5 + bc])
    if fn == 0x10:
        bc = at(6)
        return _crc_frame(buf, i, [8, None if bc is None else 9 + bc])
    if fn in (0x83, 0x90):
        return _crc_frame(buf, i, [5])
    return 0


MATCHERS: tuple[tuple[str, Callable[[bytes, int], int]], ...] = (
    ("JK", _jk), ("display", _display), ("JBD", _jbd), ("Modbus", _modbus),
)


def _words(data: bytes) -> str:
    return " ".join(data[k : k + 2].hex().upper() for k in range(0, len(data), 2))


def describe(kind: str, f: bytes) -> str:
    if kind == "JK":
        return f"JK frame type {f[4]} ({JK_KINDS.get(f[4], 'unknown')})"
    if kind == "display":
        return f"display {f[3]:02X}: {f[4:].hex(' ').upper()}".rstrip(": ")
    if kind == "JBD":
        addr, fn, start, end, n = struct.unpack(">BBHHH", f[:8])
        data = f[8:-2]
        if fn == JBD_READ and start == JBD_HISTORY and n == 2:
            return f"JBD addr {addr} history request 0x{start:04X}: record {struct.unpack('>H', data)[0]}"
        if fn == JBD_READ:
            return f"JBD addr {addr} reply 0x{start:04X}: {n} bytes" if n else f"JBD addr {addr} read 0x{start:04X}-0x{end:04X}"
        if data[1:4] == b"JBD":
            return f"JBD addr {addr} write 0x{start:04X} key {data[0]:02X}: {data[4:].hex(' ').upper()}"
        return f"JBD addr {addr} write reply 0x{start:04X}" + (f": {data.hex(' ').upper()}" if data else "")
    if kind == "Modbus":
        unit, fn = f[0], f[1]
        if fn & 0x80:
            return f"Modbus unit {unit} exception {f[2]} to FC{fn & 0x7F:02X}"
        if len(f) == 8:
            start, count = struct.unpack(">HH", f[2:6])
            what = "read" if fn == 0x03 else "write ack"
            return f"Modbus unit {unit} {what} 0x{start:04X} x{count}"
        if fn == 0x03:
            return f"Modbus unit {unit} reply: {f[2] // 2} registers"
        start, count = struct.unpack(">HH", f[2:6])
        return f"Modbus unit {unit} write 0x{start:04X} x{count}: {_words(f[7:-2])}"
    return f"{len(f)} unparsed byte(s): {f.hex(' ').upper()}"


def split(buf: bytes, final: bool = False) -> tuple[list[Heard], bytes]:
    """The frames in `buf`, and the bytes left over that may start a frame still arriving.
    With `final` (the line went quiet), nothing is kept back."""
    heard: list[Heard] = []
    noise = bytearray()

    def flush_noise() -> None:
        if noise:
            heard.append(Heard("noise", bytes(noise), describe("noise", bytes(noise))))
            noise.clear()

    i = 0
    while i < len(buf):
        found = None
        waiting = False
        for kind, match in MATCHERS:
            n = match(buf, i)
            if n == NEED_MORE:
                waiting = True
            elif n:
                found = (kind, n)
                break
        if found:
            kind, n = found
            flush_noise()
            heard.append(Heard(kind, buf[i : i + n], describe(kind, buf[i : i + n])))
            i += n
        elif waiting and not final:
            break
        else:
            noise.append(buf[i])
            i += 1
    flush_noise()
    return heard, buf[i:]


def run(
    read: Callable[[int], bytes],
    on_heard: Callable[[Heard], None],
    seconds: float | None = None,
    quiet_s: float = QUIET_S,
) -> None:
    """Read until `seconds` pass (or forever). An unfinished frame is kept until the line has been
    quiet for `quiet_s`: a frame relayed over Wi-Fi can arrive in pieces with empty reads between."""
    deadline = None if seconds is None else time.monotonic() + seconds
    buf = b""
    last = time.monotonic()
    while deadline is None or time.monotonic() < deadline:
        chunk = read(512)
        if chunk:
            last = time.monotonic()
            heard, buf = split(buf + chunk)
        elif buf and time.monotonic() - last >= quiet_s:
            heard, buf = split(buf, final=True)
        else:
            heard = []
        for h in heard:
            on_heard(h)
    for h in split(buf, final=True)[0]:
        on_heard(h)
