"""`packkeeper listen`: hears and decodes frames on a line, and never transmits."""

from pathlib import Path

import pytest

from packkeeper.listen import split
from packkeeper.modbus import with_crc

HERE = Path(__file__).parent


def real_jbd_pair() -> tuple[bytes, bytes]:
    for line in (HERE / "jbd_up_real_frames.txt").read_text().splitlines():
        if line.startswith("02 78 18 00"):
            req, rep = line.split("|")
            return bytes.fromhex(req), bytes.fromhex(rep)
    raise AssertionError("fixture line missing")


# Heard on a real JK line: a master's FC16 polls, then a pack's 55 AA EB 90 frame (cut short here).
POLLS = bytes.fromhex("06 10 16 20 00 01 02 00 00 F0 C1 01 10 16 20 00 01 02 00 00 D6 F1")
# Heard on another JK line: display frames.
DISPLAY = bytes.fromhex("A5 5A 05 82 21 63 00 00 A5 5A 03 80 01 00")


def jk_frame(kind: int) -> bytes:
    body = bytes([0x55, 0xAA, 0xEB, 0x90, kind, 0x07]) + bytes(293)
    return body + bytes([sum(body) & 0xFF])


def test_splits_a_real_jbd_read_and_reply():
    req, rep = real_jbd_pair()
    heard, rest = split(req + rep, final=True)
    assert rest == b""
    assert [h.raw for h in heard] == [req, rep]
    assert heard[0].text == "JBD addr 2 read 0x1800-0x1900"
    assert heard[1].text == "JBD addr 2 reply 0x1800: 208 bytes"


def test_decodes_a_jbd_write_with_its_key():
    data = bytes([0x35]) + b"JBD" + bytes([0x4A, 0x4A])
    frame = with_crc(bytes([1, 0x79, 0x29, 0x12, 0x29, 0x14, 0, len(data)]) + data)
    (h,), _ = split(frame, final=True)
    assert h.text == "JBD addr 1 write 0x2912 key 35: 4A 4A"


def test_splits_modbus_polls_jk_frames_display_frames_and_noise():
    stream = b"\x00\xff" + POLLS + jk_frame(2) + DISPLAY
    heard, rest = split(stream, final=True)
    assert rest == b""
    assert [h.kind for h in heard] == ["noise", "Modbus", "Modbus", "JK", "display", "display"]
    assert heard[1].text == "Modbus unit 6 write 0x1620 x1: 0000"
    assert heard[3].text == "JK frame type 2 (status)"
    assert heard[4].text == "display 82: 21 63 00 00"


def test_a_frame_cut_at_the_end_waits_for_more():
    req, rep = real_jbd_pair()
    heard, rest = split(req + rep[:50])
    assert [h.raw for h in heard] == [req] and rest == rep[:50]
    heard, rest = split(rest + rep[50:])
    assert [h.raw for h in heard] == [rep] and rest == b""


class FakeSerial:
    """A port that yields captured bytes and fails the test if anything is written."""

    chunks: list[bytes] = []

    def __init__(self, *a, **k):
        self.chunks = list(FakeSerial.chunks)

    def read(self, n):
        return self.chunks.pop(0) if self.chunks else b""

    def write(self, data):
        raise AssertionError("listen must never transmit")

    def close(self):
        pass


def test_cli_listen_prints_and_logs_without_transmitting(tmp_path, capsys, monkeypatch):
    serial = pytest.importorskip("serial")
    from packkeeper.cli import main

    req, rep = real_jbd_pair()
    FakeSerial.chunks = [req, rep[:100], rep[100:], *[b""] * 5]
    monkeypatch.setattr(serial, "Serial", FakeSerial)
    assert main(["listen", "--bms", "jbd-up", "--port", "FAKE", "--seconds", "0.5", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "JBD addr 2 read 0x1800-0x1900" in out and "JBD addr 2 reply 0x1800: 208 bytes" in out
    log = next(tmp_path.glob("*.log")).read_text()
    assert "TX" not in log and "heard JBD addr 2 reply 0x1800" in log
