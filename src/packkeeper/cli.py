"""Command line: packkeeper ports | info | backup | diff | set | restore | copy | clock | find | probe | listen | live | keys | gui."""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, backup, drivers
from .block import BlankBlockError, Block, DriverMismatch
from .controls import control_frames
from .device import BROADCAST_WARNING, ModbusDevice, PackError
from .errors import FrameError
from .find import scan
from .probe import run as run_probes
from .modbus import ModbusError
from .planner import Plan, identity_warnings, make_plan, targets_from_block
from .service import NOT_ATTEMPTED, UNVERIFIED, StaleError, apply_plan, diff_blocks, run_control, set_clock
from .session import SessionLog, default_log_path
from .settings import SettingError, SettingTable
from .transport import NoReply, guess_port, list_ports


def driver_of(args):
    return drivers.get(getattr(args, "bms", None) or drivers.DEFAULT)


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


class CliError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        try:
            from .gui import main as gui_main
        except ImportError:
            pass
        else:
            return gui_main()
    args = build_parser().parse_args(argv)
    if sys.platform == "win32":
        os.system("")  # turn on ANSI escapes in the Windows console
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(errors="replace")
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except NoReply as e:
        print(f"error: {e}\n\n{driver_of(args).connection_help}", file=sys.stderr)
        return 3
    except (CliError, BlankBlockError, backup.BackupError, SettingError, PackError, ModbusError, FrameError, StaleError, DriverMismatch, drivers.UnknownDriver) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except OSError as e:  # includes serial.SerialException: adapter unplugged, folder not writable
        print(f"error: {e}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="packkeeper",
        description="Back up, compare and restore BMS protection settings, one pack at a time. Supported: "
        + ", ".join(d.label for d in drivers.DRIVERS.values()) + ".",
    )
    p.add_argument("--version", action="version", version=f"packkeeper {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bms", choices=sorted(drivers.DRIVERS), default=drivers.DEFAULT,
                        help="BMS type (default: %(default)s)")
    common.add_argument("-p", "--port", help="serial port (default: the only USB serial port, or $PACKKEEPER_PORT)")
    common.add_argument("--simulate", action="store_true", help="use a built-in fake pack instead of a serial port")
    common.add_argument("--sim-state", type=Path, metavar="FILE", help="with --simulate: keep the fake pack's registers in FILE")
    common.add_argument("--address", type=int, metavar="N", help="Modbus address, for BMS types that have one to choose")
    common.add_argument("-F", "--fahrenheit", action="store_true", help="show temperatures in °F (input too)")
    common.add_argument("--log-dir", help="where to write the session log (default: next to the file, else here)")
    common.add_argument("--unsafe", action="store_true",
                        help="allow changing unsafe settings (each is confirmed by typing 'unsafe')")
    write = argparse.ArgumentParser(add_help=False)
    write.add_argument("-n", "--dry-run", action="store_true", help="show the diff and the frames, write nothing")

    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    s = sub.add_parser("ports", help="list serial ports")
    s.set_defaults(func=cmd_ports)

    s = sub.add_parser("info", parents=[common], help="model, firmware, clock and live readings")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("backup", parents=[common], help="save the pack's settings to a JSON file")
    s.add_argument("file", type=Path)
    s.add_argument("--force", action="store_true", help="overwrite FILE if it exists")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("diff", parents=[common], help="compare the pack with a backup or a profile")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("file", nargs="?", type=Path)
    g.add_argument("--profile", help="a built-in profile (see 'packkeeper keys --profiles')")
    s.set_defaults(func=cmd_diff)

    s = sub.add_parser("set", parents=[common, write], help="change one setting (KEY VALUE, repeatable)")
    s.add_argument("pairs", nargs="+", metavar="KEY VALUE")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("restore", parents=[common, write], help="write a backup or a profile to the pack")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("file", nargs="?", type=Path)
    g.add_argument("--profile", help="a built-in profile (see 'packkeeper keys --profiles')")
    s.add_argument("--skip", default="", help="comma-separated setting keys to leave alone")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("copy", parents=[common, write], help="copy one pack's settings to another, one at a time")
    s.add_argument("--save", type=Path, help="where to save the source pack's backup")
    s.add_argument("--skip", default="", help="comma-separated setting keys to leave alone")
    s.set_defaults(func=cmd_copy)

    s = sub.add_parser("clock", parents=[common, write], help="show or set the BMS clock")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--now", action="store_true", help="set it to this computer's time")
    g.add_argument("--set", metavar="'YYYY-MM-DD HH:MM:SS'", help="set it to this time")
    s.add_argument("--utc", action="store_true", help="with --now: use UTC instead of local time")
    s.set_defaults(func=cmd_clock)

    s = sub.add_parser("control", parents=[common, write],
                       help="list or run a live control (restart, standby, MOSFET test)")
    s.add_argument("name", nargs="?", help="the control to run; leave out to list them")
    s.set_defaults(func=cmd_control)

    s = sub.add_parser("find", parents=[common], help="list the addresses that answer (reads only)")
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("probe", parents=[common], help="try a list of reads and report each (reads only)")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("listen", parents=[common],
                       help="decode every frame on the line without sending anything (to capture another program)")
    s.add_argument("--baud", type=int, help="line speed (default: the BMS type's)")
    s.add_argument("--seconds", type=float, help="stop after this long (default: until Ctrl-C)")
    s.set_defaults(func=cmd_listen)

    s = sub.add_parser("live", parents=[common], help="read-only live view of the pack")
    s.add_argument("--interval", type=float, default=2.0)
    s.add_argument("--once", action="store_true")
    s.set_defaults(func=cmd_live)

    s = sub.add_parser("keys", help="list setting keys, units and safe ranges")
    s.add_argument("--bms", choices=sorted(drivers.DRIVERS), default=drivers.DEFAULT,
                   help="BMS type (default: %(default)s)")
    s.add_argument("-F", "--fahrenheit", action="store_true")
    s.add_argument("--profiles", action="store_true", help="list the built-in profiles instead")
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("gui", help="open the desktop app")
    s.set_defaults(func=lambda a: __import__("packkeeper.gui", fromlist=["main"]).main())
    return p


# --- connection ------------------------------------------------------------

def open_pack(args, near: Path | None = None) -> ModbusDevice:
    driver = driver_of(args)
    address = args.address
    if address is not None:
        if driver.address_choices is None:
            raise CliError(f"the {driver.label} always answers at address {driver.address}; --address does not apply")
        if address not in driver.address_choices:
            c = driver.address_choices
            raise CliError(f"--address must be {c.start}..{c.stop - 1} for the {driver.label}")
    log_dir = Path(args.log_dir) if args.log_dir else (near.resolve().parent if near else None)
    log = SessionLog(default_log_path(log_dir))
    log.info(f"BMS type {driver.label}")
    transport = open_transport(args, driver, log)
    if address is not None:
        log.info(f"address {address}")
    return driver.open_device(transport, log, address)


def open_transport(args, driver, log):
    """The serial port (or the simulated pack) the arguments name; logs and prints where."""
    if args.simulate:
        transport = driver.simulator(state_file=args.sim_state)
        name = "simulated pack"
    else:
        from .transport import SerialTransport

        port = args.port or os.environ.get("PACKKEEPER_PORT") or os.environ.get("EG4TOOL_PORT") or guess_port()
        if not port:
            raise CliError("no port given and no single USB serial port found; use --port (see 'packkeeper ports')")
        try:
            transport = SerialTransport(port, params=driver.serial, framing=driver.framing)
        except Exception as e:  # serial.SerialException and OS errors
            raise CliError(f"cannot open {port}: {e}") from None
        name = port
    log.info(f"port {name}")
    print(f"Port: {name}   Log: {log.path}")
    return transport


# --- commands --------------------------------------------------------------

def cmd_ports(args) -> int:
    ports = list_ports()
    if not ports:
        print("No serial ports found. Is the USB-RS232 adapter plugged in (and its driver installed)?")
        return 1
    for p in ports:
        print(p.label)
    return 0


def cmd_find(args) -> int:
    driver = driver_of(args)
    if driver.find_addresses is None:
        raise CliError(f"the {driver.label} has no address to find")
    log_dir = Path(args.log_dir) if args.log_dir else None
    log = SessionLog(default_log_path(log_dir))
    log.info(f"BMS type {driver.label}")
    transport = open_transport(args, driver, log)
    try:
        found = scan(driver, transport, driver.find_addresses, log)
    finally:
        transport.close()
    for f in found:
        print(f"address {f.address:3d}  {f.model}  {f.serial}  {f.note}".rstrip())
    if not found:
        print("No pack answered.")
    return 0 if found else 1


def cmd_probe(args) -> int:
    pack = open_pack(args)
    try:
        probes = run_probes(pack)
    finally:
        pack.close()
    for p in probes:
        head = " ".join(f"{w:04X}" for w in p.words[:8]) + (" ..." if len(p.words) > 8 else "")
        print(f"{p.label:<28} 0x{p.area.start:04X} x{p.area.count:<4} {p.outcome:<20} {head}".rstrip())
    if pack.bus_chatter:
        print(f"warning: {pack.bus_chatter}")
    if pack.log.path:
        print(f"Every frame is in {pack.log.path}")
    return 0 if any(p.ok for p in probes) else 1


def cmd_listen(args) -> int:
    import dataclasses

    import serial

    from . import listen

    if args.simulate:
        raise CliError("listen needs a real port: it only hears what other devices send")
    driver = driver_of(args)
    params = dataclasses.replace(driver.serial, baud=args.baud) if args.baud else driver.serial
    port = args.port or os.environ.get("PACKKEEPER_PORT") or os.environ.get("EG4TOOL_PORT") or guess_port()
    if not port:
        raise CliError("no port given and no single USB serial port found; use --port (see 'packkeeper ports')")
    log = SessionLog(default_log_path(Path(args.log_dir) if args.log_dir else None))
    log.info(f"listening on {port} at {params.baud} baud; nothing is sent")
    try:
        line = serial.Serial(port, params.baud, bytesize=params.bytesize, parity=params.parity,
                             stopbits=params.stopbits, timeout=0.05)
    except Exception as e:  # serial.SerialException and OS errors
        raise CliError(f"cannot open {port}: {e}") from None
    print(f"Listening on {port} at {params.baud} baud; nothing is sent. Ctrl-C to stop.   Log: {log.path}")

    def heard(h) -> None:
        log.rx(h.raw)
        log.info(f"heard {h.text}")
        print(f"{datetime.now():%H:%M:%S.%f}"[:-3] + f"  {h.text}")

    try:
        listen.run(line.read, heard, args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        line.close()
    return 0


def print_identity(block: Block) -> None:
    ident = block.identity
    print(f"BMS       {block.driver.label}")
    print(f"Model     {ident.model}")
    print(f"Firmware  {ident.firmware}")
    if ident.serial:
        note = "" if block.driver.serial_is_unique else "  (the same on every pack; does not identify it)"
        print(f"Serial    {ident.serial}{note}")
    if block.driver.supports_clock:
        bad = f"invalid (0x{ident.clock_raw:08X})" if ident.clock_raw is not None else "invalid"
        print(f"BMS clock {ident.clock or bad}")
    for label, text in block.info_lines():
        print(f"{label:9s} {text}")
    for w in block.identity_warnings():
        print(f"warning: {w}")
    missing = block.missing_keys
    if missing:
        print(f"warning: the pack did not send {len(missing)} setting(s), shown as not sent and never written: "
              f"{', '.join(block.driver.table[k].label for k in missing)}")


def cmd_info(args) -> int:
    pack = open_pack(args)
    block = pack.read_block()
    print_identity(block)
    print()
    for line in pack.read_live().summary_lines():
        print(line)
    return 0


def cmd_backup(args) -> int:
    if args.file.exists() and not args.force:
        raise CliError(f"{args.file} already exists (use --force to overwrite it)")
    pack = open_pack(args, near=args.file)
    block = pack.read_block()
    print_identity(block)
    backup.save(args.file, block, port=getattr(pack.transport, "port", None))
    pack.log.result(f"backup saved to {args.file}")
    not_sent = len(block.missing_registers)
    print(f"\nSaved {len(block.registers) - not_sent} registers ({len(block.driver.table)} settings, "
          f"{len(block.unknown_registers())} unknown kept"
          + (f", {not_sent} not sent by the pack and saved as null" if not_sent else "") + f") to {args.file}")
    return 0


def load_source(args) -> tuple[Block, str]:
    if getattr(args, "profile", None):
        profiles = driver_of(args).profiles
        if args.profile not in profiles:
            raise CliError(f"unknown profile '{args.profile}' (known: {', '.join(sorted(profiles))})")
        prof = profiles[args.profile]
        return prof.block, prof.label
    b = backup.load(args.file)
    return b.block, f"{args.file} ({b.created})"


def cmd_diff(args) -> int:
    source, name = load_source(args)
    pack = open_pack(args, near=args.file)
    block = pack.read_block()
    print(f"Pack: {block.model} {block.firmware}\nOther: {name}\n")
    for w in identity_warnings(block, source, "the other side"):
        print(f"warning: {w}")
    rows, unknown = diff_blocks(block, source)
    if not rows and not unknown:
        print("No differences.")
        pack.log.result("no differences")
        return 0
    f = args.fahrenheit
    width = max(len(r.setting.label) for r in rows) if rows else 10
    print(f"{'Setting':{width}}  {'Pack':>12}  {'Other':>12}")
    for r in rows:
        s = r.setting
        print(f"{s.label:{width}}  {_shown(s, r.a, f):>12}  {_shown(s, r.b, f):>12}")
        pack.log.result(f"differs: {s.label} pack {_shown(s, r.a)} other {_shown(s, r.b)}")
    for reg in unknown:
        pv, sv = (b.reg(reg) if b.sent(reg) else "not sent" for b in (block, source))
        print(f"unknown register 0x{reg:02X}: pack {pv}, other {sv} (never written)")
    print(f"\n{len(rows)} setting(s) differ.")
    return 1


def _shown(s, raw: int | None, fahrenheit: bool = False) -> str:
    return "not sent" if raw is None else s.format(raw, fahrenheit)


def parse_pairs(pairs: list[str], fahrenheit: bool, table: SettingTable) -> dict[str, int]:
    if len(pairs) % 2:
        raise CliError("give settings as KEY VALUE pairs (see 'packkeeper keys')")
    out: dict[str, int] = {}
    for key, value in zip(pairs[::2], pairs[1::2]):
        s = table.get(key)
        if s is None:
            near = [k for k in table if key.lower() in k]
            raise CliError(f"unknown setting '{key}'" + (f"; did you mean: {', '.join(near[:6])}" if near else ""))
        out[key] = s.parse(value, fahrenheit)
    # The two registers whose mapping is unsettled move together.
    for a, b in table.linked:
        if a in out and b not in out:
            out[b] = out[a]
        elif b in out and a not in out:
            out[a] = out[b]
    return out


def cmd_set(args) -> int:
    table = driver_of(args).table
    targets = parse_pairs(args.pairs, args.fahrenheit, table)
    pack = open_pack(args)
    return run_write(pack, targets, "command line", args)


def skip_set(text: str, table: SettingTable) -> set[str]:
    keys = {k.strip() for k in text.split(",") if k.strip()}
    bad = keys - set(table)
    if bad:
        raise CliError(f"unknown setting(s) in --skip: {', '.join(sorted(bad))}")
    return keys


def cmd_restore(args) -> int:
    source, name = load_source(args)
    pack = open_pack(args, near=args.file)
    return run_write(pack, targets_from_block(source, skip_set(args.skip, driver_of(args).table), args.unsafe), name, args, source=source)


def cmd_copy(args) -> int:
    driver = driver_of(args)
    skip = skip_set(args.skip, driver.table)
    pack = open_pack(args, near=args.save)
    print(f"\n{plain(driver.connect_note_html)}\n")
    confirm_enter(f"Set up the SOURCE pack ({driver.setup_hint}), then press Enter")
    source = pack.read_block()
    print_identity(source)
    save = args.save or Path(f"{driver.name}-source-{datetime.now():%Y%m%d-%H%M%S}.json")
    backup.save(save, source, port=getattr(pack.transport, "port", None))
    pack.log.result(f"source backup saved to {save}")
    print(f"Source saved to {save}\n")
    confirm_enter(f"Move the adapter to the TARGET pack ({driver.setup_hint}), then press Enter")
    return run_write(pack, targets_from_block(source, skip, args.unsafe), f"source pack ({save})", args, source=source)


def confirm_enter(prompt: str) -> None:
    try:
        input(f"{prompt}: ")
    except EOFError:
        raise CliError("no terminal to confirm on") from None


def print_plan(plan: Plan, fahrenheit: bool) -> None:
    for w in plan.warnings:
        print(f"warning: {w}")
    for e in plan.errors:
        print(f"REFUSED: {e}")
    if plan.errors and plan.proposed:
        print("\nRequested changes:")
        for c in plan.proposed:
            print(f"      0x{c.register:02X} {c.describe(fahrenheit)}")
    if plan.changes:
        print(f"\n{len(plan.changes)} change(s), in write order:")
        for i, c in enumerate(plan.changes, 1):
            print(f"  {i:2d}. 0x{c.register:02X} {c.describe(fahrenheit)}")
    for n in plan.notes:
        print(f"note: {n}")


def run_write(pack: ModbusDevice, targets: dict[str, int], source_name: str, args, source: Block | None = None) -> int:
    f = args.fahrenheit
    block = pack.read_block()
    print_identity(block)
    plan = make_plan(block, targets, fahrenheit=f, source=source, allow_unsafe=args.unsafe)
    if source is not None:
        plan.warnings[:0] = identity_warnings(block, source, "the source")
    print(f"\nSource: {source_name}")
    print_plan(plan, f)
    if source is not None and not args.unsafe:
        left = [k for k in block.driver.table.unsafe_keys
                if source.has(k) and block.has(k) and source.raw(k) != block.raw(k)]
        if left:
            print(f"note: {len(left)} unsafe setting(s) differ and were left alone (use --unsafe to include them)")
    for line in plan.warnings:
        pack.log.warn(line)
    for line in plan.errors:
        pack.log.error(f"refused: {line}")
    if not plan.ok:
        print("\nNothing was written.")
        return 2
    if plan.empty:
        print("\nThe pack already has these values. Nothing to write.")
        pack.log.result("nothing to write")
        return 0
    for c in plan.changes:
        pack.log.info(f"planned: 0x{c.register:02X} {c.describe()}")

    if args.dry_run:
        print("\nDry run. Frames that would be sent (each followed by a full read-back):")
        for c, frame in zip(plan.changes, plan.frames(pack.address, pack.framing)):
            print(f"  {frame.hex(' ').upper()}   {c.setting.label}")
            pack.log.info(f"dry run frame {frame.hex(' ').upper()}")
        return 0
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}. Nothing was written.")

    pre = pack.log.before_path()
    if pre:
        backup.save(pre, block, port=getattr(pack.transport, "port", None))
        print(f"\nThe pack's current settings were saved to {pre}")
    warn_broadcast(pack)
    word = "unsafe" if plan.unsafe else "yes"
    if plan.unsafe:
        print("\nUNSAFE changes in this plan:")
        for c in plan.unsafe:
            print(f"  {c.describe(f)}\n    {c.setting.unsafe}")
    try:
        answer = input(f"\nType '{word}' to write {len(plan.changes)} setting(s) to this pack: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != word:
        print("Nothing was written.")
        pack.log.info("not confirmed; nothing written")
        return 1
    pack.log.info("confirmed by user")

    def progress(i, n, r):
        print(f"  [{i + 1}/{n}] {r.describe(f)}")

    print()
    res = apply_plan(pack, plan, progress)
    not_sent = [r for r in res.results if r.status == NOT_ATTEMPTED]
    unverified = [r for r in res.results if r.status == UNVERIFIED]
    print()
    if res.ok:
        print(f"All {len(res.results)} write(s) confirmed by reading the block back.")
        return 0
    print(f"STOPPED: {res.stopped}")
    for u in res.unexpected:
        print(f"  unexpected: {u}")
    for r in unverified:
        print(f"  {r.describe(f)}")
    for w in res.warnings:
        print(f"  WARNING: {w}")
    for r in not_sent:
        print(f"  not sent: {r.change.describe(f)}")
    print(f"See the log: {pack.log.path}")
    return 1


def warn_broadcast(pack: ModbusDevice) -> None:
    if pack.address == pack.driver.broadcast_address:
        pack.log.warn(BROADCAST_WARNING)
        print(f"\nWARNING: {BROADCAST_WARNING}")


def cmd_clock(args) -> int:
    pack = open_pack(args)
    block = pack.read_block()
    now = datetime.now(timezone.utc).replace(tzinfo=None) if args.utc else datetime.now()
    driver = pack.driver
    if not driver.supports_clock:
        raise CliError(f"the {driver.label} has no clock")
    print(f"BMS clock: {block.identity.clock or 'invalid'}")
    print(f"This computer: {now.replace(microsecond=0)}{' UTC' if args.utc else ' (local)'}")
    if not (args.now or args.set):
        return 0
    if args.set:
        try:
            target = datetime.strptime(args.set, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise CliError("use --set 'YYYY-MM-DD HH:MM:SS'") from None
    else:
        target = now
    frame = driver.clock_frame(target, pack.address, pack.framing)
    print(f"\nSet the BMS clock to {target.replace(microsecond=0)}  (frame {frame.hex(' ').upper()})")
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}. Nothing was written.")
    warn_broadcast(pack)
    if input("Type 'yes' to set the clock: ").strip().lower() != "yes":
        print("Nothing was written.")
        return 1
    if args.now:
        target = datetime.now(timezone.utc).replace(tzinfo=None) if args.utc else datetime.now()
    res = set_clock(pack, target)
    print(f"Pack now reports {res.after}: {'confirmed' if res.ok else 'NOT applied'}")
    return 0 if res.ok else 1


def cmd_control(args) -> int:
    driver = driver_of(args)
    controls = {c.key: c for c in driver.controls()}
    if not controls:
        raise CliError(f"the {driver.label} has no controls")
    if args.name is None:
        for c in controls.values():
            print(f"{c.key:12s} {c.label}: {c.summary}")
            if c.danger:
                print(f"{'':12s} {c.danger}")
        return 0
    control = controls.get(args.name)
    if control is None:
        raise CliError(f"unknown control '{args.name}' (known: {', '.join(controls)})")
    pack = open_pack(args)
    pack.read_block()
    print(f"{control.label}: {control.summary}")
    if control.danger:
        print(f"WARNING: {control.danger}")
    frames = control_frames(pack, control)
    print("\nFrames, in order:")
    for label, frame in frames:
        print(f"  {frame.hex(' ').upper()}   {label}")
    if args.dry_run:
        for label, frame in frames:
            pack.log.info(f"dry run frame {frame.hex(' ').upper()}  {label}")
        print("\nDry run: nothing sent.")
        return 0
    if pack.write_refusal:
        raise PackError(f"writes are off: {pack.write_refusal}. Nothing was sent.")
    word = control.typed_word or "yes"
    try:
        answer = input(f"\nType '{word}' to send it: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != word:
        print("Nothing was sent.")
        pack.log.info(f"control {control.key}: not confirmed; nothing sent")
        return 2
    pack.log.info(f"control {control.key} confirmed by user")
    print()
    res = run_control(pack, control, lambda r: print(f"  {r.describe()}"))
    if res.ok:
        print("\nControl confirmed.")
        return 0
    print(f"\nNOT confirmed: {res.stopped}\nSee the log: {pack.log.path}")
    if not res.cleanup_ok:
        print(
            "WARNING: the cleanup was not confirmed, so the pack may not be back to normal. "
            "Check it with 'packkeeper live' and run the control again or restart the pack."
        )
    return 1


def cmd_live(args) -> int:
    pack = open_pack(args)
    block = pack.read_block() if not args.once else None
    while True:
        rt = pack.read_live()
        lines = rt.summary_lines()
        if args.once:
            print("\n".join(lines))
            return 0
        sys.stdout.write("\x1b[2J\x1b[H")
        print(f"{block.model} {block.firmware}   {datetime.now():%H:%M:%S}   (Ctrl+C to stop)\n")
        print("\n".join(lines))
        sys.stdout.flush()
        time.sleep(args.interval)


def cmd_keys(args) -> int:
    driver = driver_of(args)
    if args.profiles:
        for name, prof in driver.profiles.items():
            print(f"{name:20s} {prof.label}")
        return 0
    f = args.fahrenheit
    group = None
    for key, s in driver.table.items():
        if s.group != group:
            group = s.group
            print(f"\n{group}")
        rng = f"{s.format(s.raw_min, f)} .. {s.format(s.raw_max, f)}"
        print(f"  {key:38s} 0x{s.register:02X}  {rng:24s} step {s.step}" + (" [unsafe]" if s.unsafe else ""))
        if s.choices:
            print(f"    choices: {', '.join(n for _r, n in s.choices)}")
        if s.bits:
            print(f"    bits: {', '.join(f'0x{m:04X} {n}' for m, n in s.bits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
