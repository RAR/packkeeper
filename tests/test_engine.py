import dataclasses
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from packkeeper import backup, modbus
from packkeeper.block import BlankBlockError
from packkeeper.drivers.eg4_ll import DRIVER as EG4_LL
from packkeeper.drivers.eg4_ll.live import decode as decode_live
from packkeeper.drivers.eg4_ll.profiles import EG4_SHIPPED_FRAME, REALTIME_FRAMES
from packkeeper.drivers.eg4_ll.registers import pack_clock, unpack_clock
from packkeeper.drivers.eg4_ll.sim import FakePack
from packkeeper.modbus import registers_of
from packkeeper.planner import make_plan, targets_from_block
from packkeeper.service import CONFIRMED, NOT_APPLIED, NOT_ATTEMPTED, StaleError, apply_plan, diff_blocks, set_clock
from packkeeper.session import SessionLog
from packkeeper.settings import Rule, SettingError, f_to_c_exact

SETTINGS, ORDER = EG4_LL.table, EG4_LL.table.order
FRAMES = Path(__file__).parent / "rs232_service_frames.txt"
REF = EG4_LL.profiles["eg4-shipped"].block

# What the vendor BMS tool shows for the reference pack (settings_table_test.py).
EXPECTED = {
    "balance_start_voltage": 3.4, "balance_delta_voltage": 0.02, "low_capacity_warning": 10,
    "cell_undervoltage_warning": 2.5, "cell_undervoltage_protection": 2.3, "cell_undervoltage_release": 3.1,
    "pack_undervoltage_warning": 46.5, "pack_undervoltage_protection": 44.8, "pack_undervoltage_release": 48.0,
    "cell_overvoltage_warning": 3.7, "cell_overvoltage_protection": 3.8, "cell_overvoltage_release": 3.45,
    "pack_overvoltage_warning": 59.2, "pack_overvoltage_protection": 60.0, "pack_overvoltage_release": 55.2,
    "charge_overcurrent_count": 5, "discharge_overcurrent_count": 5,
    "charge_overcurrent_release_time": 180, "discharge_overcurrent_release_time": 60,
    "charge_overcurrent_1_protection": 205.0, "discharge_overcurrent_1_protection": 205.0,
    "short_circuit_current": 300.0, "charge_overcurrent_2_protection": 225.0,
    "discharge_overcurrent_2_protection": 300.0, "charge_overcurrent_1_delay": 10,
    "charge_overcurrent_2_delay": 3, "discharge_overcurrent_1_delay": 10, "discharge_overcurrent_2_delay": 3,
    "charge_undertemp_warning": -2, "charge_undertemp_protection": -5, "charge_undertemp_release": 0,
    "charge_overtemp_warning": 65, "charge_overtemp_protection": 70, "charge_overtemp_release": 60,
    "discharge_undertemp_warning": -15, "discharge_undertemp_protection": -20, "discharge_undertemp_release": -10,
    "discharge_overtemp_warning": 70, "discharge_overtemp_protection": 75, "discharge_overtemp_release": 65,
    "mos_overtemp_warning": 100, "mos_overtemp_protection": 110, "mos_overtemp_release": 80,
    "heater_on_temperature": 0, "heater_off_temperature": 3,
    "ambient_undertemp_warning": -20, "ambient_undertemp_protection": -25, "ambient_undertemp_release": -15,
    "ambient_overtemp_warning": 70, "ambient_overtemp_protection": 80, "ambient_overtemp_release": 60,
}


def raw(key, text):
    return SETTINGS[key].parse(text)


def fake_pack(**kw):
    sim = FakePack(**kw)
    return sim, EG4_LL.open_device(sim, SessionLog())


# --- framing ---------------------------------------------------------------

def test_crc_matches_known_request():
    assert modbus.read_request(1, 0x2D, 0x5B) == bytes.fromhex("0103002D005B9438")


def test_captured_frames_have_valid_crc():
    for line in FRAMES.read_text().splitlines():
        frame = bytes.fromhex(line.split()[2])
        assert modbus.crc_ok(frame), line


def test_write_frame_layout():
    f = modbus.write_request(1, 0x39, [20])
    assert f[:9] == bytes.fromhex("01100039000102 0014".replace(" ", ""))
    assert modbus.crc_ok(f)


def test_parse_rejects_bad_crc_and_exceptions():
    req = modbus.read_request(1, 0x2D, 0x5B)
    bad = bytearray(EG4_SHIPPED_FRAME)
    bad[10] ^= 1
    with pytest.raises(modbus.ModbusError):
        modbus.parse_read_reply(req, bytes(bad))
    with pytest.raises(modbus.ModbusException):
        modbus.parse_read_reply(req, modbus.with_crc(bytes([1, 0x83, 2])))
    assert len(modbus.parse_read_reply(req, EG4_SHIPPED_FRAME)) == 91


# --- decoding --------------------------------------------------------------

def test_reference_block_decodes_to_vendor_values():
    for key, want in EXPECTED.items():
        s = SETTINGS[key]
        assert s.value(REF.raw(key)) == Decimal(str(want)), key
    assert set(EXPECTED) == set(SETTINGS)


def test_reference_identity_and_clock():
    assert REF.model == "ZTR-16S200A-10KW-STD2"
    assert REF.firmware == "S02T11"
    assert REF.serial == "1648100200200001"
    assert REF.clock is not None and REF.clock.year >= 2020


def test_reference_block_satisfies_rules():
    raws = REF.raws()
    for rule in ORDER:
        assert rule.holds(raws), rule
    for key, s in SETTINGS.items():
        assert s.limit_error(raws[key]) is None, key


def test_realtime_decode():
    rt = decode_live(registers_of(REALTIME_FRAMES[0]) + registers_of(REALTIME_FRAMES[1]))
    assert rt.pack_voltage == pytest.approx(53.30)
    assert rt.current == pytest.approx(26.30)
    assert len(rt.cells) == 16 and rt.cells[0] == pytest.approx(3.330)
    assert rt.soc == 67 and rt.soh == 100
    assert rt.status_text == "Charging"
    assert rt.cell_count == 16
    assert rt.design_capacity_ah == pytest.approx(280.0)
    assert rt.full_capacity_ah == pytest.approx(280.0, abs=0.5)
    assert rt.warnings == [] and rt.protections == [] and rt.charge_allowed


def test_clock_packing_roundtrip():
    t = datetime(2026, 10, 22, 18, 5, 9)
    assert unpack_clock(pack_clock(t)) == t


def test_blank_block_refused():
    blank = EG4_LL.block(0 if 0x2F <= 0x2D + i <= 0x68 or 0x80 <= 0x2D + i else v
                         for i, v in enumerate(REF.registers))
    with pytest.raises(BlankBlockError):
        blank.check_not_blank()
    sim, pack = fake_pack(blank_thresholds=True)
    with pytest.raises(BlankBlockError):
        pack.read_block()


# --- values and limits -----------------------------------------------------

def test_parse_units_and_steps():
    assert raw("cell_overvoltage_protection", "3.65") == 3650
    assert raw("cell_overvoltage_protection", "3.65 V") == 3650
    assert raw("charge_overcurrent_1_protection", "200A") == 20000
    assert raw("charge_overtemp_protection", "60") == 110
    assert raw("charge_overtemp_protection", "140F") == 110
    assert raw("charge_overtemp_protection", "-4°F") == 30  # -20 C
    with pytest.raises(SettingError):
        raw("cell_overvoltage_protection", "3.6505")
    with pytest.raises(SettingError, match="not a whole"):
        raw("charge_overtemp_protection", "141F")
    with pytest.raises(SettingError):
        raw("charge_overtemp_protection", "abc")


def test_fahrenheit_flag_on_bare_numbers():
    assert SETTINGS["charge_overtemp_protection"].parse("140", fahrenheit=True) == 110
    assert SETTINGS["cell_overvoltage_protection"].parse("3.5", fahrenheit=True) == 3500
    assert f_to_c_exact(Decimal("32")) == 0


def test_limits_never_loosen():
    s = SETTINGS
    assert s["cell_overvoltage_protection"].limit_error(3810)
    assert s["cell_overvoltage_protection"].limit_error(3800) is None
    assert s["charge_overcurrent_1_protection"].limit_error(30010)
    assert s["charge_undertemp_protection"].limit_error(39)  # -11 C
    assert s["balance_delta_voltage"].limit_error(10)  # 0.010 V: below the pack's floor
    assert s["balance_delta_voltage"].limit_error(19)
    assert s["balance_delta_voltage"].limit_error(20) is None
    assert s["charge_overcurrent_1_protection"].limit_error(20005)  # not on the 0.1 A step


# --- planner ---------------------------------------------------------------

def test_no_change_gives_empty_plan():
    p = make_plan(REF, targets_from_block(REF))
    assert p.ok and p.empty


def test_triple_moving_up_writes_top_first():
    p = make_plan(REF, {
        "cell_overvoltage_release": raw("cell_overvoltage_release", "3.50"),
        "cell_overvoltage_warning": raw("cell_overvoltage_warning", "3.75"),
        "cell_overvoltage_protection": raw("cell_overvoltage_protection", "3.80"),  # unchanged
        "cell_undervoltage_release": raw("cell_undervoltage_release", "3.15"),
    })
    assert p.ok, p.errors
    keys = [c.key for c in p.changes]
    assert keys.index("cell_overvoltage_warning") < keys.index("cell_overvoltage_release")


def test_triple_moving_down_writes_bottom_first():
    p = make_plan(REF, {
        "cell_overvoltage_release": raw("cell_overvoltage_release", "3.40"),
        "cell_overvoltage_warning": raw("cell_overvoltage_warning", "3.42"),
        "cell_overvoltage_protection": raw("cell_overvoltage_protection", "3.44"),
        "balance_start_voltage": raw("balance_start_voltage", "3.35"),
        "cell_undervoltage_release": raw("cell_undervoltage_release", "3.05"),
    })
    assert p.ok, p.errors
    keys = [c.key for c in p.changes]
    assert keys.index("cell_overvoltage_release") < keys.index("cell_overvoltage_warning") < keys.index(
        "cell_overvoltage_protection")
    assert keys.index("balance_start_voltage") < keys.index("cell_overvoltage_warning")
    _assert_every_step_valid(REF, p)


def _assert_every_step_valid(base, plan):
    state = base.raws()
    for c in plan.changes:
        before = {r for r in ORDER if r.holds(state)}
        state[c.key] = c.new
        for r in before:
            assert r.holds(state), f"{c.key} broke {r}"
    assert all(r.holds(state) for r in ORDER)


def test_restore_shipped_over_vendor_reset():
    # What the vendor tool's "restore defaults" wrote: charge OC 62 A, cell OVP
    # 3.90 V, pack UVP 43.2 V. Restoring the shipped profile must repair it.
    broken = REF.with_raws({0x50: 6200, 0x44: 3900, 0x41: 4320})
    p = make_plan(broken, targets_from_block(REF))
    assert p.ok, p.errors
    assert {c.key for c in p.changes} == {
        "charge_overcurrent_1_protection", "cell_overvoltage_protection", "pack_undervoltage_protection"}
    assert any("outside the safe range" in w for w in make_plan(broken, {}).warnings)


def test_limit_violation_refused():
    p = make_plan(REF, {"cell_overvoltage_protection": 3900})
    assert not p.ok and "safe range" in p.errors[0]


def test_order_violation_refused():
    # Warning above protection: no order can get there.
    p = make_plan(REF, {"cell_overvoltage_warning": raw("cell_overvoltage_warning", "3.79"),
                        "cell_overvoltage_protection": raw("cell_overvoltage_protection", "3.75")})
    assert not p.ok
    assert any("must stay below" in e for e in p.errors)
    assert p.changes == []


def test_final_state_breaking_a_rule_refused():
    rules = [Rule("cell_overvoltage_release", "cell_overvoltage_warning", True),
             Rule("cell_overvoltage_warning", "cell_overvoltage_release", False)]
    p = make_plan(REF, {"cell_overvoltage_release": 3400, "cell_overvoltage_warning": 3600}, rules=rules)
    assert not p.ok and p.changes == []


def test_no_valid_order_refused_with_blockers(monkeypatch):
    # With the real ORDER table and a valid final block a deadlock cannot
    # happen (a write only waits on a neighbour moving the same way, toward
    # the end of an acyclic chain), so force one: the planner must refuse and
    # name the blocking rules, never pick an order anyway.
    from packkeeper import planner
    rules = [Rule("cell_overvoltage_release", "cell_overvoltage_warning", True),
             Rule("cell_overvoltage_warning", "cell_overvoltage_protection", True)]
    targets = {"cell_overvoltage_release": 3750, "cell_overvoltage_warning": 3790}
    monkeypatch.setattr(planner, "_order", lambda _table, cur, want, _rules: ([], {"cell_overvoltage_warning": [rules[1]]}))
    p = make_plan(REF, targets, rules=rules)
    assert not p.ok and p.changes == []
    assert "no write order" in p.errors[0]
    assert "is blocked by" in p.errors[0]


def test_deadlock_path_names_rules():
    from packkeeper import planner
    rules = [Rule("cell_overvoltage_release", "cell_overvoltage_warning", True),
             Rule("cell_overvoltage_warning", "cell_overvoltage_protection", True)]
    order, stuck = planner._order(REF.driver.table, REF.raws(), {"cell_overvoltage_release": 3750, "cell_overvoltage_warning": 3800}, rules)
    assert set(stuck) == {"cell_overvoltage_release", "cell_overvoltage_warning"}
    assert stuck["cell_overvoltage_warning"] == [rules[1]]


def test_linked_pair_must_match():
    sc = raw("short_circuit_current", "250")
    p = make_plan(REF, {"short_circuit_current": sc})
    assert not p.ok and "same value" in p.errors[0]
    p = make_plan(REF, {"short_circuit_current": sc, "discharge_overcurrent_2_protection": sc})
    assert p.ok, p.errors
    keys = [c.key for c in p.changes]
    # Moving down: discharge OC 2 (lower) before short circuit.
    assert keys == ["discharge_overcurrent_2_protection", "short_circuit_current"]


def test_unknown_registers_never_written():
    p = make_plan(REF.with_raws({0x30: 1234}), targets_from_block(REF))
    assert p.empty
    for f in make_plan(REF, {"cell_overvoltage_warning": 3650}).frames():
        assert 0x2F <= int.from_bytes(f[2:4], "big") <= 0x87


# --- applying to the fake pack ---------------------------------------------

def test_apply_confirms_each_write():
    sim, pack = fake_pack()
    p = make_plan(pack.read_block(), {"cell_overvoltage_warning": 3650, "charge_overcurrent_1_protection": 20000})
    res = apply_plan(pack, p)
    assert res.ok
    assert [r.status for r in res.results] == [CONFIRMED, CONFIRMED]
    assert sim.image[0x43] == 3650 and sim.image[0x50] == 20000


def test_ack_but_drop_reports_not_applied_and_stops():
    sim, pack = fake_pack(floors={0x39: 25, 0x43: 0})
    p = make_plan(pack.read_block(), {"balance_delta_voltage": 22, "balance_start_voltage": 3450})
    assert p.ok
    res = apply_plan(pack, p)
    assert not res.ok
    statuses = [r.status for r in res.results]
    i = [r.change.key for r in res.results].index("balance_delta_voltage")
    assert statuses[i] == NOT_APPLIED
    assert statuses[:i] == [CONFIRMED] * i and statuses[i + 1:] == [NOT_ATTEMPTED] * (len(statuses) - i - 1)
    dropped = next(r for r in res.results if r.change.key == "balance_delta_voltage")
    assert dropped.reported == 20


def test_missing_ack_but_stored_is_confirmed():
    sim, pack = fake_pack()
    sim.drop_acks = 1
    p = make_plan(pack.read_block(), {"cell_overvoltage_warning": 3650})
    res = apply_plan(pack, p)
    assert res.ok and res.results[0].acked is False


def test_stale_plan_refused():
    sim, pack = fake_pack()
    p = make_plan(pack.read_block(), {"cell_overvoltage_warning": 3650})
    sim.image[0x44] = 3750  # a different pack, or someone else wrote meanwhile
    with pytest.raises(StaleError):
        apply_plan(pack, p)
    assert sim.writes == []


def test_read_retries_after_dropped_reply():
    sim, pack = fake_pack()
    sim.drop_replies = 2
    assert pack.read_block().model.startswith("ZTR")


def test_clock_set_and_confirmed():
    sim, pack = fake_pack()
    res = set_clock(pack, datetime(2026, 10, 22, 12, 0, 0))
    assert res.ok, res
    assert sim.writes[-1][0] == 0x2D and len(sim.writes[-1][1]) == 2


def test_pack_refuses_non_threshold_writes():
    sim, pack = fake_pack()
    from packkeeper.device import PackError
    for reg in (0x2D, 0x2E, 0x69, 0x7F, 0x88):
        with pytest.raises(PackError):
            pack.write_setting(dataclasses.replace(EG4_LL.table["cell_overvoltage_warning"], register=reg), 1)
    assert sim.writes == []


def test_session_log_records_frames(tmp_path):
    log = SessionLog(tmp_path / "s.log")
    pack = EG4_LL.open_device(FakePack(), log)
    pack.read_block()
    text = (tmp_path / "s.log").read_text()
    assert "TX     01 03 00 2D 00 5B 94 38" in text
    assert "RX     01 03 B6" in text


# --- backups ---------------------------------------------------------------

def test_backup_roundtrip(tmp_path):
    f = tmp_path / "b.json"
    backup.save(f, REF, port="COM3")
    b = backup.load(f)
    assert b.block == REF
    rows, unknown = diff_blocks(REF, b.block)
    assert rows == [] and unknown == []


def test_backup_keeps_unknown_registers(tmp_path):
    f = tmp_path / "b.json"
    backup.save(f, REF)
    import json
    data = json.loads(f.read_text())
    assert data["registers"][0x2F - 0x2D] == 8  # unknown 0x2F
    assert "0x2F" in data["unknown_registers"]


def test_edited_backup_refused(tmp_path):
    import json
    f = tmp_path / "b.json"
    backup.save(f, REF)
    data = json.loads(f.read_text())
    data["settings"]["cell_overvoltage_protection"]["value"] = 3.9
    f.write_text(json.dumps(data))
    with pytest.raises(backup.BackupError, match="edited"):
        backup.load(f)


def test_blank_backup_refused(tmp_path):
    with pytest.raises(BlankBlockError):
        backup.save(tmp_path / "b.json", EG4_LL.block([0] * 91))


def test_random_valid_targets_always_get_a_safe_order():
    import random
    rng = random.Random(1234)
    keys = list(SETTINGS)
    planned = 0

    def nudge(s, raw):
        # Small moves around a value keep most random blocks valid.
        span = (s.raw_max - s.raw_min) // s.raw_step
        steps = rng.randint(-max(1, span // 8), max(1, span // 8))
        return min(s.raw_max, max(s.raw_min, raw + steps * s.raw_step))

    for _ in range(3000):
        start = REF.raws()
        # Random walk of a few settings within LIMITS, from the reference or a
        # previously shuffled start.
        base = dict(start)
        for k in rng.sample(keys, 6):
            s = SETTINGS[k]
            base[k] = nudge(s, base[k])
        if not all(r.holds(base) for r in ORDER):
            continue
        targets = dict(base)
        for k in rng.sample(keys, rng.randrange(1, 12)):
            s = SETTINGS[k]
            targets[k] = nudge(s, targets[k])
        for a, b in [("short_circuit_current", "discharge_overcurrent_2_protection")]:
            targets[b] = targets[a]
            base[b] = base[a]
        if not all(r.holds(targets) for r in ORDER) or not all(r.holds(base) for r in ORDER):
            continue
        block = REF.with_raws({SETTINGS[k].register: v for k, v in base.items()})
        p = make_plan(block, targets)
        assert p.ok, p.errors
        _assert_every_step_valid(block, p)
        planned += 1
    assert planned > 500


def test_write_without_read_back_is_unverified_not_unsent():
    from packkeeper.service import UNVERIFIED
    sim, pack = fake_pack()
    p = make_plan(pack.read_block(), {"cell_overvoltage_warning": 3650, "charge_overcurrent_1_protection": 20000})
    sim.silent_after_write = 3  # applies and acks the write, then goes quiet for every retry
    res = apply_plan(pack, p)
    assert not res.ok and res.final is None
    assert [r.status for r in res.results] == [UNVERIFIED, NOT_ATTEMPTED]
    assert "read the pack" in res.results[0].describe()
    assert sim.image[p.changes[0].register] == p.changes[0].new  # it really was applied


def test_stopped_write_flags_split_linked_pair():
    sim, pack = fake_pack(floors={0x52: 30000})  # short circuit refuses to move
    p = make_plan(pack.read_block(), {"short_circuit_current": 25000, "discharge_overcurrent_2_protection": 25000})
    res = apply_plan(pack, p)
    assert not res.ok
    assert any("should be equal" in w for w in res.warnings)


def test_long_decimal_input_is_not_rounded_onto_a_step():
    with pytest.raises(SettingError):
        raw("cell_overvoltage_protection", "3.80000000000000000000000000049")


def test_resync_drops_line_noise():
    from packkeeper.transport import resync
    good = EG4_SHIPPED_FRAME
    assert resync(b"\x00\xff" + good, 1, 3) == (good, b"\x00\xff")
    assert resync(b"\x01\x10" + good, 1, 3) == (good, b"\x01\x10")  # 01 followed by the wrong function
    assert resync(b"\x01", 1, 3) == (b"\x01", b"")  # might still be a frame start


def test_serial_transport_survives_leading_noise(monkeypatch):
    from packkeeper import transport

    class FakeSerial:
        def __init__(self, *a, **k):
            self.buf = b""

        def reset_input_buffer(self):
            pass

        def write(self, req):
            self.buf = b"\x00" + FakePack().transact(req)

        def flush(self):
            pass

        def read(self, n):
            out, self.buf = self.buf[:n], self.buf[n:]
            return out

        def close(self):
            pass

    import serial
    monkeypatch.setattr(serial, "Serial", FakeSerial)
    log = SessionLog()
    pack = EG4_LL.open_device(transport.SerialTransport("FAKE"), log)
    assert pack.read_block().model.startswith("ZTR")
    assert any("line noise" in line for line in log.lines)


def test_each_pre_write_backup_gets_its_own_file(tmp_path):
    log = SessionLog(tmp_path / "s.log")
    a = log.before_path()
    a.write_text("{}")
    b = log.before_path()
    assert a != b and not b.exists()


def test_write_refused_with_exception_is_read_back_not_unverified():
    sim, pack = fake_pack()
    sim.refuse = {0x43: 3}
    p = make_plan(pack.read_block(), {"cell_overvoltage_warning": 3650})
    res = apply_plan(pack, p)
    assert res.results[0].status == NOT_APPLIED and res.results[0].acked is False
    assert res.final is not None


def test_late_reply_is_not_taken_for_the_next_request(monkeypatch):
    # The pack answers the first request after the reply deadline; the retry
    # must not take that late reply as its own.
    from packkeeper import transport

    class SlowSerial:
        def __init__(self, *a, **k):
            self.pending = []  # (time it arrives, bytes)
            self.calls = 0

        def reset_input_buffer(self):
            now = time.monotonic()
            self.pending = [(t, b) for t, b in self.pending if t > now]

        def write(self, req):
            self.calls += 1
            reply = FakePack().transact(req)
            if self.calls == 1:  # late, and a different block from what the retry will get
                reply = FakePack().transact(req)[:-2]
                reply = modbus.with_crc(reply[:3] + bytes([0xAA, 0xAA]) + reply[5:])
                self.pending.append((time.monotonic() + 1.2, reply))
            else:
                self.pending.append((time.monotonic() + 0.01, reply))

        def flush(self):
            pass

        def read(self, n):
            time.sleep(0.05)
            now = time.monotonic()
            out = b""
            while self.pending and self.pending[0][0] <= now and len(out) < n:
                t, b = self.pending[0]
                take = b[: n - len(out)]
                out += take
                rest = b[len(take):]
                self.pending[0:1] = [(t, rest)] if rest else []
            return out

        def close(self):
            pass

    import serial
    monkeypatch.setattr(serial, "Serial", SlowSerial)
    pack = EG4_LL.open_device(transport.SerialTransport("FAKE"), SessionLog())
    block = pack.read_block()
    assert block.registers[0] == 0x6AA8  # the retry's own reply, not the late 0xAAAA one


def test_step_error_shows_the_typed_value():
    s = SETTINGS["charge_overcurrent_1_protection"]
    assert "205.05 A is not on the 0.1 step" in s.limit_error(s.parse("205.05"))


def test_cli_backup_refuses_to_overwrite(tmp_path, capsys):
    from packkeeper.cli import main
    f = tmp_path / "b.json"
    assert main(["backup", str(f), "--simulate", "--log-dir", str(tmp_path)]) == 0
    assert main(["backup", str(f), "--simulate", "--log-dir", str(tmp_path)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert main(["backup", str(f), "--force", "--simulate", "--log-dir", str(tmp_path)]) == 0
