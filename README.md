# PackKeeper

Back up, compare and restore the protection settings of an EG4-LL battery pack
(other BMS types can be added as drivers) over RS232, through the right-most of its paralleling ports. It runs on Windows, macOS and Linux, as a desktop
app (Qt) or from the command line. It is an unofficial, open-source tool, not
made or supported by EG4.

It exists because the vendor's Windows BMS tool writes without checking. On
2026-10-22 its "restore defaults" wrote charge OC 62 A, cell OVP 3.90 V and
pack UVP 43.2 V to a live pack. This tool never writes blind:

1. It reads the pack's parameter block now.
2. It shows only the registers that change, old → new, in units.
3. It checks every new value against safe limits, and the whole resulting
   block against the warning / protection / release ordering rules.
4. It asks for one explicit confirmation. A copy of the pack's current
   settings is saved first.
5. It writes one register at a time, in an order where every intermediate
   block is still valid (moving a triple up, the top goes first; moving it
   down, the bottom goes first).
6. It re-reads the whole block after **every** write and reports each change
   as **confirmed** or **NOT applied**. The pack acks values below its own
   floor and silently keeps the old ones, so the ack proves nothing. A write
   that didn't stick stops the sequence.

There is no "factory reset" button. Restoring EG4's shipped settings goes
through the same diff and confirmation as any backup.

## Connecting: one pack at a time

There is no dedicated service port: the USB-RS232 adapter plugs into the
pack's **right-most paralleling port**, the one EG4's own cable uses. It
answers there at Modbus address 1 whatever its DIP ID, so the DIP switches
stay as they are. It only answers when **no other pack is linked to it**:
unplug the link cables from its paralleling ports first.

So: unplug the link cables, plug in the adapter, service the pack, then plug
the link cables back in.

Hardware: a USB-RS232 adapter at real RS232 levels (not TTL), with an RJ45
plug for the right-most paralleling port. EG4's cable is an FT232R lead; CH340, Prolific and CP210x
adapters work too. Wire adapter TX → pack RXD, adapter RX → pack TXD, plus
GND. If nothing decodes, swap TX and RX. The link is Modbus RTU at 115200 8N1,
address 1.

Only RS232 works. Reading the same block over the RS485 paralleling bus (as a
pack linked to others does) returns every threshold zero. The tool refuses such a block and says why.

## Desktop app

Open **PackKeeper**, pick the adapter's port and click **Connect**.

- **Live:** read-only pack voltage, current, SOC/SOH, cells, temperatures,
  and decoded status / warning / protection bits.
- **Settings:** four pages in a sidebar: Voltage, Current, Temperature, and
  Balancing & capacity.
  - Thresholds are laid out as rows of Warning / Protection / Release, and
    over-current as stages of trip current and delay. Each row shows its
    safe range.
  - Type a new value into any field to stage it. Units are optional, and
    temperatures accept °C or °F (`140F`). A staged field turns blue and
    shows "was … · undo"; Esc cancels typing.
  - The **Changes to write** panel lists everything staged. Problems are
    flagged as you type, before you review: out-of-range values, and
    ordering rules such as a warning above its protection, with the fields
    involved outlined in red.
  - **Compare with** EG4's shipped values or a backup file. Fields that
    differ show the reference value underneath (click it to use it), the
    sidebar counts the differences per page, and **Stage all differences**
    turns the comparison into a restore.
  - **Copy to another pack…** keeps this pack's settings as the comparison
    for the next pack you connect.
  - **Review and write…** re-reads the pack and opens the plan: the ordered
    changes, a dry run that shows the exact frames, then one confirmation
    and a per-write result.
- **Clock:** sets the BMS clock to the computer's time, confirmed by
  reading it back.
- **Controls** (ECO-WORTHY / JBD-UP): restart, standby sleep, a MOSFET
  test and Normal mode, each checked by reading the pack. The MOSFET test has
  a **Stop** button that goes back to Normal mode.
- **Log:** every frame sent and received (hex) and every result. Session logs
  and the pre-write backups go to `Documents/PackKeeper/`. (Before the app was
  renamed they went to `Documents/EG4 BMS Tool/`, which is left as it was.)

Choose **Simulated pack** in the port list to try everything without
hardware.

## Command line

```
packkeeper ports                              list serial ports
packkeeper info                               model, firmware, clock, live readings
packkeeper backup pack1.json                  save the settings (raw block + decoded)
packkeeper diff pack1.json                    compare the pack with a backup
packkeeper diff --profile eg4-shipped         ... or with EG4's shipped values
packkeeper set cell_overvoltage_warning 3.65  change a setting (KEY VALUE, repeatable)
packkeeper restore pack1.json [--skip k1,k2]  write a backup back
packkeeper restore --profile eg4-shipped      restore EG4's shipped values
packkeeper copy                               read one pack, swap, write it to another
packkeeper clock [--now | --set '...']        show or set the BMS clock
packkeeper control [NAME]                     list or run a live control (ECO-WORTHY / JBD-UP)
packkeeper find                               list the addresses that answer (reads only)
packkeeper probe                              try a list of reads, report each (reads only)
packkeeper listen [--baud N] [--seconds S]    decode every frame on the line; never sends
packkeeper live [--once]                      read-only live view
packkeeper keys                               setting keys, registers, safe ranges
packkeeper gui                                open the desktop app
```

Common options:

- `--port` / `-p` picks the port. The default is the only USB serial port,
  or `$PACKKEEPER_PORT` (the old `$EG4TOOL_PORT` still works).
- `-F` shows (and accepts) °F.
- `--dry-run` / `-n` on any write command prints the frames it would send.

`listen` only hears. Put a second adapter on the same A/B pair as another
program (the stock app, a master pack) and it logs and decodes every frame:
JBD-UP, Modbus RTU, JK's `55 AA EB 90` frames and `A5 5A` display frames.
- `--simulate` uses a fake pack. Add `--sim-state FILE` to keep its state
  between runs.
- `--address N` picks the Modbus address, for BMS types that have one to choose (JK-PB, ECO-WORTHY / JBD-UP).
- `--unsafe` allows changing unsafe settings (BMS types that have them); each is confirmed by typing `unsafe`.

Every run writes a session log next to the backup file, or in the current
directory, or in `--log-dir`. `diff` exits 1 when the pack differs.

On Windows, `PackKeeper.exe` is windowed and has no console. Use
`packkeeper-cli.exe` for the command line.

## Adding a BMS

Everything specific to one BMS family lives in a driver package under
`src/packkeeper/drivers/` (see `eg4_ll/` for the complete example). A driver
subclasses `drivers.base.Driver` and provides:

- a `SettingTable`: registers, scaling, safe ranges, the order rules
  between settings, and any settings that must move together;
- the settings block range and which registers may be written;
- identity (model, firmware, clock) and live-value decoding;
- the Settings tab pages, built-in profiles, connection texts;
- a simulator, so the whole flow can be tested without hardware.

Register it in `drivers/__init__.py`. The engine, CLI (`--bms`) and app
(the BMS selector) pick it up from there. `tests/toy_driver.py` is a
minimal driver used to keep the engine honest.

The defaults assume Modbus RTU: `write_frame`, `clock_frame`, `open_device`
(which returns a `ModbusDevice`) and `live_reads`. A non-Modbus BMS overrides
`open_device` to return its own `Device`. That device's `read_block()` must
call `block.check_not_blank()` before returning, and its `write_setting` must
refuse registers where `driver.is_writable` is false. It also overrides
`write_frame` and `clock_frame` so `--dry-run` output makes sense.

A new driver should only ever be used for `info`, `backup`, `diff` and
`live` until its register map has been checked against a real unit.

## JK-PB (PB2A16S20P)

`--bms jk-pb` (or **BMS: JK-PB** in the app) talks to a JK PB-series inverter
BMS over RS485 with a USB-RS485 adapter, one pack at a time with the link
cables unplugged. Pick the pack's DIP-switch address with `--address N`
(or the **Address** box); a pack that does not answer at 1 may answer at 0.

The register map is JK's RS485 Modbus V1.1 document. The settings ranges are
this tool's proposal for 16S LiFePO4, not JK's limits: compare them with JK's
app before the first write. On every connect the tool reads cell
over-voltage at two candidate addresses to confirm how the pack numbers its
registers; if that does not come out cleanly, writes and the clock are turned
off for that session and the reason is shown. If the pack refuses the
settings read, run `packkeeper probe --bms jk-pb --address N` (reads only; on
Windows `packkeeper-cli`). It tries short and long reads at the byte- and
word-numbered addresses and prints which ones the pack accepts; the session
log it names has every frame.

Never written: cell count, the charge/discharge/balance switches, the device
address, calibration, shutdown, emergency start and the one-key presets.

## ECO-WORTHY / JBD-UP (48 V rack)

`--bms jbd-up` (or **BMS: ECO-WORTHY / JBD-UP**) talks to ECO-WORTHY rack packs
with JBD "ES/UP" BMS boards over RS485 at 9600 baud, one pack at a time with the
link cables unplugged. The address is the pack's DIP value; `packkeeper find
--bms jbd-up` (or **Find…**) lists the addresses that answer, with each pack's
serial number.

The protection map comes from notes reverse engineered from JBD's ES-UP app,
cross-checked against syssi/esphome-jbd-bms and the PhracturedBlue ECO-WORTHY
notes. The ranges are this tool's proposals for 16S LiFePO4 with a 100 A BMS:
compare them with the stock app before the first write.

Unsafe settings (cells in series, shunt, short circuit, pack type, function and
anti-theft bits, inverter protocols, CAN baud) are hidden unless you tick
**Show unsafe settings** or pass `--unsafe`, and each write of one is confirmed
by typing `unsafe`. Restore defaults, factory reset and calibration are not in
this tool.

**Controls** (`packkeeper control --bms jbd-up`, or the **Controls** tab):
restart, standby sleep, a MOSFET test and Normal mode. Each one sends frames captured from
JBD's own app, never computed ones, and checks the result by reading the pack:

- **Restart**: the pack must go quiet and then answer again within 30 s.
- **Standby sleep** (type `standby`): the pack stops answering while it
  sleeps, under load too, so the check waits up to 10 s for it to answer
  again and says how long it took (2-3 s under load). Confirmed by its ack, or
  without one by the pack going quiet and coming back, as for a restart; the
  pack's status is shown after. It read 0 A when it woke, even under load, so
  it may stop supplying power for those seconds.
- **MOSFET test** (type `test`): Tooling mode (both MOSFETs off), charge on,
  both on, then back to Normal, each step checked against the live MOSFET bits.
  If a step fails, or you stop it or press Ctrl-C, it goes back to Normal mode
  and checks that. **Stop** (or closing the window) ends the check in progress
  at once. The pack's output is cut for a few seconds, so run it with little or
  no load.
- **Normal mode**: takes the BMS out of Tooling mode (for example if the stock
  app left it there); confirmed when the mode reads back as normal. It then
  waits up to 10 s for both MOSFETs and shows their state: leaving Tooling, a
  real pack turned charge on first and discharge a few seconds later, and in
  Normal mode the BMS drives them, so a protection may hold one off.

The live status JBD calls 0 is shown as **Idle**, as the stock app does: no
current is flowing. It is not the standby sleep.

`--dry-run` shows the frames and sends nothing. Nothing is sent while writes
are off.

## Safe ranges and rules

The settings, their encodings, LIMITS and ORDER rules are
[`src/packkeeper/drivers/eg4_ll/settings_table.py`](src/packkeeper/drivers/eg4_ll/settings_table.py). It is
vendored unchanged from
[esphome-eg4-bms](https://github.com/RAR/esphome-eg4-bms) (`feat/rs232-service`,
`cb105bf`), so the ESPHome component and this tool share one source of truth. This
tool's own tightening (`LIMIT_OVERRIDES` and the linked 0x52/0x54 pair) lives
in [`src/packkeeper/drivers/eg4_ll/table.py`](src/packkeeper/drivers/eg4_ll/table.py).

- **Tighten, never loosen.** No limit lets a protection go past what the pack
  ships with (cell OV 3.80 V, pack OV 60.80 V, cell UV 2.30 V, currents
  300 A, and so on).
- The balance delta minimum is raised to **0.020 V** in
  [`table.py`](src/packkeeper/drivers/eg4_ll/table.py) (`LIMIT_OVERRIDES`). The pack
  drops anything lower.
- **Short circuit (0x52) and discharge OC 2 (0x54) are linked.** The
  component and REGISTER_MAP.md disagree on which register is which, and
  both ship at 300 A. Until a deliberate test settles it, the tool only sets
  them together, to the same value.
- Only known threshold registers are ever written. The unknown registers
  (0x2F-0x37, 0x3B-0x3C, 0x49-0x4B, 0x59, …) are kept in backups but never
  restored. The clock (0x2D-0x2E) is written only by the clock command;
  identity (0x69-0x7F) never.
- A backup's raw registers are the source of truth. A file whose decoded
  values were edited so they no longer match is refused.
- A register the pack does not send (a reply shorter than the block, or a
  refused read of an identity or clock area) is never guessed. It shows as
  **not sent**, is saved as `null`, and is never written. Neither is a
  setting whose rule partner was not sent, since the rule cannot be checked.
  Restore and copy leave such settings alone and say which. A refused read
  in a settings area still fails the whole read: that usually means wrong
  addressing.

The built-in **eg4-shipped** profile is a parameter block captured from a
ZTR-16S200A-10KW-STD2 on BMS firmware S02T11 (2026-10-20). Restoring it, or
any backup, onto a pack whose model or firmware differs shows a warning in the
diff.

## Install from source

```
python -m pip install ".[gui]"     # Python 3.11+
packkeeper gui
```

- **Linux:** add yourself to the `dialout` group (or `uucp` on Arch) to open
  serial ports.
- **macOS:** FTDI and CH340 adapters appear as `/dev/cu.*`. The app isn't
  signed, so right-click → Open the first time.

## Development

```
python -m pip install -e ".[gui,dev]"
pytest                                  # engine + GUI (offscreen) tests, no hardware
python packaging/build.py               # PyInstaller builds into dist/
```

The tests decode the captured reference frames, and cover CRC, framing,
LIMITS/ORDER, the write-order planner (including refusals and a randomized
check that every valid target gets a safe order), backups, and a fake pack.
The fake pack applies writes and can ack a write but drop it, exercising the
"NOT applied" path.

`.github/workflows/build.yml` runs the tests on Linux for every push and
pull request. Builds cost more CI minutes (macOS counts 10x and Windows 2x
on a private repo), so they run only when asked for or on a `v*` tag, which
builds all four and drafts a release:

```
gh workflow run build --ref main                     # Windows build only
gh workflow run build --ref main -f platforms=all    # Windows, macOS arm64 and x86_64, Linux
```

To build on your own machine instead (no CI minutes): `python
packaging/build.py` builds for the platform it runs on. On Windows,
`packaging\build-windows.bat` does it all: it installs uv if needed, runs the
tests, builds both programs and zips them. Copy the source to the PC's disk
first.

On a real pack, start with `info`, `backup`, `diff` and `live`. Any write
test needs the owner present and agreeing to that specific write.

The protocol comes only from the public EG4-LL Modbus PDF, the open-source
component, and reads of the owner's own packs. Nothing here comes from
decompiling the vendor tool or the pack firmware.
