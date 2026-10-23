# EG4 BMS Tool

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

Open **EG4 BMS Tool**, pick the adapter's port and click **Connect**.

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
- **Log:** every frame sent and received (hex) and every result. Session logs
  and the pre-write backups go to `Documents/EG4 BMS Tool/`.

Choose **Simulated pack** in the port list to try everything without
hardware.

## Command line

```
eg4tool ports                              list serial ports
eg4tool info                               model, firmware, clock, live readings
eg4tool backup pack1.json                  save the settings (raw block + decoded)
eg4tool diff pack1.json                    compare the pack with a backup
eg4tool diff --profile eg4-shipped         ... or with EG4's shipped values
eg4tool set cell_overvoltage_warning 3.65  change a setting (KEY VALUE, repeatable)
eg4tool restore pack1.json [--skip k1,k2]  write a backup back
eg4tool restore --profile eg4-shipped      restore EG4's shipped values
eg4tool copy                               read one pack, swap, write it to another
eg4tool clock [--now | --set '...']        show or set the BMS clock
eg4tool live [--once]                      read-only live view
eg4tool keys                               setting keys, registers, safe ranges
eg4tool gui                                open the desktop app
```

Common options:

- `--port` / `-p` picks the port. The default is the only USB serial port,
  or `$EG4TOOL_PORT`.
- `-F` shows (and accepts) °F.
- `--dry-run` / `-n` on any write command prints the frames it would send.
- `--simulate` uses a fake pack. Add `--sim-state FILE` to keep its state
  between runs.

Every run writes a session log next to the backup file, or in the current
directory, or in `--log-dir`. `diff` exits 1 when the pack differs.

On Windows, `EG4 BMS Tool.exe` is windowed and has no console. Use
`eg4tool.exe` for the command line.

## Adding a BMS

Everything specific to one BMS family lives in a driver package under
`src/eg4tool/drivers/` (see `eg4_ll/` for the complete example). A driver
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

## Safe ranges and rules

The settings, their encodings, LIMITS and ORDER rules are
[`src/eg4tool/drivers/eg4_ll/settings_table.py`](src/eg4tool/drivers/eg4_ll/settings_table.py). It is
vendored unchanged from
[esphome-eg4-bms](https://github.com/RAR/esphome-eg4-bms) (`feat/rs232-service`,
`cb105bf`), so the ESPHome component and this tool share one source of truth. This
tool's own tightening (`LIMIT_OVERRIDES` and the linked 0x52/0x54 pair) lives
in [`src/eg4tool/drivers/eg4_ll/table.py`](src/eg4tool/drivers/eg4_ll/table.py).

- **Tighten, never loosen.** No limit lets a protection go past what the pack
  ships with (cell OV 3.80 V, pack OV 60.80 V, cell UV 2.30 V, currents
  300 A, and so on).
- The balance delta minimum is raised to **0.020 V** in
  [`settings.py`](src/eg4tool/settings.py) (`LIMIT_OVERRIDES`). The pack
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

The built-in **eg4-shipped** profile is a parameter block captured from a
ZTR-16S200A-10KW-STD2 on BMS firmware S02T11 (2026-10-20). Restoring it, or
any backup, onto a pack whose model or firmware differs shows a warning in the
diff.

## Install from source

```
python -m pip install ".[gui]"     # Python 3.11+
eg4tool gui
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
"NOT applied" path. `.github/workflows/build.yml` runs the tests and builds
Windows, macOS (arm64 and x86_64) and Linux binaries. A `v*` tag drafts a
release.

This tool has not been run against a real pack yet. Start there with `info`,
`backup`, `diff` and `live`. Any write test needs the owner present and
agreeing to that specific write.

The protocol comes only from the public EG4-LL Modbus PDF, the open-source
component, and reads of the owner's own packs. Nothing here comes from
decompiling the vendor tool or the pack firmware.
