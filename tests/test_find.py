from packkeeper.block import Area
from packkeeper.find import scan
from packkeeper.planner import identity_warnings
from packkeeper.session import SessionLog
from packkeeper.transport import NoReply

from unsafe_toy_driver import IMAGE, START, UNSAFE_TOY


class Bus:
    """Several fake packs on one line, each answering only its own address."""

    def __init__(self, packs):
        self.packs = packs
        self.timeout = 1.0

    def transact(self, request):
        pack = self.packs.get(request[0])
        if pack is None:
            raise NoReply("nobody at that address")
        return pack.transact(request)

    def close(self):
        pass


class Findable(type(UNSAFE_TOY)):
    address_choices = range(0, 16)
    find_addresses = range(0, 8)
    find_probe = Area(START, 1)
    serial_is_unique = True


def pack(name):
    sim = UNSAFE_TOY.simulator()
    sim.image[START] = int.from_bytes(name.encode(), "big")
    return sim


def test_scan_lists_who_answers():
    d = Findable()
    bus = Bus({0: pack("AA"), 2: pack("BB"), 5: pack("CC")})
    found = scan(d, bus, d.find_addresses, SessionLog())
    assert [(f.address, f.model) for f in found] == [(0, "AA"), (2, "BB"), (5, "CC")]
    assert bus.timeout == 1.0  # restored


def test_cli_find(monkeypatch, tmp_path, capsys):
    from packkeeper import drivers
    from packkeeper.cli import main

    d = Findable()
    d.name = "findable"
    monkeypatch.setitem(drivers.DRIVERS, "findable", d)
    monkeypatch.setattr(type(d), "simulator", lambda self, state_file=None: Bus({3: pack("DD")}))
    assert main(["find", "--bms", "findable", "--simulate", "--log-dir", str(tmp_path)]) == 0
    assert "3" in capsys.readouterr().out
    assert main(["find", "--simulate", "--log-dir", str(tmp_path)]) == 2  # EG4-LL has no find


def test_serial_warning_only_for_unique_serials():
    from packkeeper.block import Identity

    class Serial(Findable):
        def identity(self, block):
            return Identity(model="M", serial=f"S{block.reg(START + 1)}")

    s = Serial()
    a = s.block(IMAGE[START:])
    b = a.with_raws({START + 1: 341})
    assert any("S340" in w and "S341" in w for w in identity_warnings(b, a, "the backup"))
    s.serial_is_unique = False
    assert identity_warnings(b, a, "the backup") == []
