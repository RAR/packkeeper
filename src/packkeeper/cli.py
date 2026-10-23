"""Command line: packkeeper ports | info | backup | diff | set | restore | copy | clock | live | keys | gui."""

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
from .device import BROADCAST_WARNING, ModbusDevice, PackError
from .modbus import ModbusError
from .planner import Plan, identity_warnings, make_plan, targets_from_block
from .service import NOT_ATTEMPTED, UNVERIFIED, StaleError, apply_plan, diff_blocks, set_clock
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
    except (CliError, BlankBlockError, backup.BackupError, SettingError, PackError, ModbusError, StaleError, DriverMismatch, drivers.UnknownDriver) as e:
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
    if args.simulate:
        transport = driver.simulator(state_file=args.sim_state)
        name = "simulated pack"
    else:
        from .transport import SerialTransport

        port = args.port or os.environ.get("PACKKEEPER_PORT") or os.environ.get("EG4TOOL_PORT") or guess_port()
        if not port:
            raise CliError("no port given and no single USB serial port found; use --port (see 'packkeeper ports')")
        try:
            transport = SerialTransport(port, params=driver.serial)
        except Exception as e:  # serial.SerialException and OS errors
            raise CliError(f"cannot open {port}: {e}") from None
        name = port
    log.info(f"port {name}")
    print(f"Port: {name}   Log: {log.path}")
    if address is not None:
        log.info(f"address {address}")
    return driver.open_device(transport, log, address)


# --- commands --------------------------------------------------------------

def cmd_ports(args) -> int:
    ports = list_ports()
    if not ports:
        print("No serial ports found. Is the USB-RS232 adapter plugged in (and its driver installed)?")
        return 1
    for p in ports:
        print(p.label)
    return 0


def print_identity(block: Block) -> None:
    ident = block.identity
    print(f"BMS       {block.driver.label}")
    print(f"Model     {ident.model}")
    print(f"Firmware  {ident.firmware}")
    if ident.serial:
        print(f"Serial    {ident.serial}  (the same on every pack; does not identify it)")
    if block.driver.supports_clock:
        bad = f"invalid (0x{ident.clock_raw:08X})" if ident.clock_raw is not None else "invalid"
        print(f"BMS clock {ident.clock or bad}")
    for label, text in block.driver.info_lines(block):
        print(f"{label:9s} {text}")
    for w in block.driver.identity_warnings(block):
        print(f"warning: {w}")


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
    print(f"\nSaved {len(block.registers)} registers ({len(block.driver.table)} settings, "
          f"{len(block.unknown_registers())} unknown kept) to {args.file}")
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
        print(f"{s.label:{width}}  {s.format(r.a, f):>12}  {s.format(r.b, f):>12}")
        pack.log.result(f"differs: {s.label} pack {s.format(r.a)} other {s.format(r.b)}")
    for reg in unknown:
        print(f"unknown register 0x{reg:02X}: pack {block.reg(reg)}, other {source.reg(reg)} (never written)")
    print(f"\n{len(rows)} setting(s) differ.")
    return 1


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
    return run_write(pack, targets_from_block(source, skip_set(args.skip, driver_of(args).table)), name, args, source=source)


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
    return run_write(pack, targets_from_block(source, skip), f"source pack ({save})", args, source=source)


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
    plan = make_plan(block, targets, fahrenheit=f, source=source)
    if source is not None:
        plan.warnings[:0] = identity_warnings(block, source, "the source")
    print(f"\nSource: {source_name}")
    print_plan(plan, f)
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
        for c, frame in zip(plan.changes, plan.frames(pack.address)):
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
    try:
        answer = input(f"\nType 'yes' to write {len(plan.changes)} setting(s) to this pack: ")
    except EOFError:
        answer = ""
    if answer.strip().lower() != "yes":
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
    if pack.address == 0:
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
    frame = driver.clock_frame(target, pack.address)
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
        print(f"  {key:38s} 0x{s.register:02X}  {rng:24s} step {s.step}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
