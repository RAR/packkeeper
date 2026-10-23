from packkeeper import drivers
from packkeeper.block import Area
from packkeeper.cli import main
from packkeeper.modbus import with_crc
from packkeeper.probe import run
from packkeeper.session import SessionLog


def test_default_probe_reads_every_area_and_live_read():
    d = drivers.get("jbd-up")
    assert [a for _label, a in d.probe_reads()] == [*d.areas, *(Area(*a) for a in d.live_reads)]


class RefusesLongReads:
    """The simulated JK, except that it answers exception 2 to reads of more than 64 registers,
    the way the real pack refused 125."""

    def __init__(self, sim):
        self.sim = sim

    def transact(self, request: bytes) -> bytes:
        if request[1] == 0x03 and int.from_bytes(request[4:6], "big") > 64:
            return with_crc(bytes([request[0], 0x83, 0x02]))
        return self.sim.transact(request)


def test_probe_keeps_going_after_a_refusal():
    d = drivers.get("jk-pb")
    probes = run(d.open_device(RefusesLongReads(d.simulator()), SessionLog(), 1))
    by_label = {p.label: p for p in probes}
    assert [p.label for p in probes][:3] == ["settings start", "cell OV, numbered by byte", "cell OV, numbered by word"]
    assert by_label["cell OV, numbered by byte"].words == (0, 3650)  # high word first
    assert by_label["settings, 100 registers"].outcome.startswith("refused")
    assert by_label["model text"].ok and probes[-1].label == "clock, numbered by word"


def test_cli_probe_on_the_simulated_jbd(tmp_path, capsys):
    assert main(["probe", "--bms", "jbd-up", "--simulate", "--log-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "0x1800 x104  ok, 104 registers" in out and "Every frame is in" in out
