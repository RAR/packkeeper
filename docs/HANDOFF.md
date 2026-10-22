# Handoff: a cross-platform EG4-LL pack settings tool

## Why

EG4's Windows BMS tool is the only way to read or change an EG4-LL pack's protection settings, and it handles them badly. On 2026-10-22 its "restore defaults" wrote a generic table to a live pack: charge overcurrent at 62 A, cell OVP at 3.90 V, pack UVP at 43.2 V. None of those are what EG4 ships. The pack then had to be restored by hand, one setting at a time, from an earlier read of its parameter block. The tool also showed a discharge OC of 8.79 A for a pack that had just been set to 205 A.

Build a small tool that runs on Windows, macOS and Linux and does this job safely:

- **backup:** read a pack's settings to a file
- **restore / copy:** write a backup (or another pack's settings) to a pack
- **diff:** compare a pack against a file or against EG4's shipped values
- **set:** change one setting
- **verify:** read every write back and report it
- **clock:** set the BMS clock

The protocol, the register map, the safe ranges and the ordering rules are all worked out and proven on real packs. They live in the open-source ESPHome component this tool should borrow from (see "Sources").

## Hardware and link

- A USB-RS232 adapter on the pack's **RS232 service port**: the RJ45 the vendor tool's cable uses. EG4's cable is an FT232R USB-RS232 lead. Any real RS232-level adapter works (not TTL).
  - Wiring at RS232 levels: adapter TX → the pack's RXD, adapter RX → the pack's TXD (idles about −5 V against ground), plus GND.
  - If nothing decodes, swap TX and RX.
- Modbus RTU, **115200 8N1**, slave address **1** whatever the pack's DIP ID.
- The port only answers when:
  - the pack's DIP ID is **1**, and
  - **no other pack is plugged into its paralleling ports.**

  So the workflow is one pack at a time: set DIP 1, unplug the link cables, service the pack, put both back. The tool's UI must say this plainly, because it's the first thing that goes wrong.
- The paralleling bus (RS485, 19200) answers the same parameter read with every threshold zero, so **only RS232 works**. If a read returns an all-zero threshold block, the tool must refuse to use it and say why.

## Protocol (all proven against a pack running main BMS fw S02T11)

| Read | Request | Content |
|---|---|---|
| Realtime | FC03 `0x0000` x18 | pack V (10 mV), current (10 mA signed, + = charge), cells 1-16 (mV) |
| Realtime | FC03 `0x0012` x21 | temps, remaining Ah, SOH, SOC, status/warning/protection bits, cycles, capacity |
| Model | FC03 `0x0069` x11 | ASCII, e.g. `ZTR-16S200A-10KW-STD2` |
| Firmware | FC03 `0x0075` x3 | ASCII, e.g. `S02T11` |
| Serial | FC03 `0x0078` x8 | ASCII. **The same on every pack**, so it can't identify a pack. |
| Parameter block | FC03 `0x002D` x91 (`01 03 00 2D 00 5B 94 38`) | clock + every threshold, 0x2D-0x87 |

- **Writes:** FC16 (0x10), one register per write (start = the register, count 1). The pack echoes the usual FC16 ack.
- **The ack proves nothing.** A value below the pack's own floor is acked and silently not stored. Seen: balance delta 0.010 V acked, the pack kept 0.039; 0.020 held. So **every write must be confirmed by re-reading the whole parameter block**, and the tool must report "NOT applied" when the value didn't stick.
- **Clock:** 0x2D-0x2E, a 32-bit value, high word first: `(year-2000)<<26 | month<<22 | day<<17 | hour<<12 | minute<<6 | second`. Write it as FC16 0x2D x2. Not Unix time.
  - The older "Writing Configuration → RTC" example in REGISTER_MAP.md (a 4-register byte-mangled form) is stale. Ignore it and use the packed form above, which the component uses and which was confirmed on a pack.
- Modbus CRC-16 (poly 0xA001, init 0xFFFF, low byte first).
- Encoding: value = raw × multiplier + offset. Temperatures are stored as °C + 50.

## Settings: tables to reuse, not retype

Take these three tables from `components/eg4_bms/settings_table.py`. They are pure Python literals, so the file can be vendored as-is:

- **`SETTINGS`:** 51 settings, `key: (register, multiplier, offset, unit, device_class, decimals)`.
- **`LIMITS`:** `key: (min, max, step)` per setting.
  - **Tighten, never loosen.** No limit lets a protection go past what the pack ships with: cell OV 3.80 V, pack OV 60.80 V, cell UV 2.30 V, currents 300 A, charge OT 70 °C, discharge OT 75 °C, MOSFET OT 110 °C, ambient OT 80 °C, charge UT no lower than −10 °C.
  - Raise `balance_delta_voltage`'s minimum from 0.005 to **0.020**. Values below that are dropped by the pack (see above).
- **`ORDER`:** `(lower, upper, strict)` rules.
  - Over-limit triples: release < warning < protection. Under-limit triples: protection < warning < release.
  - Overcurrent stages may be equal (discharge OC 2 == short circuit == 300 A ships that way).
  - Two cross-group rules (UV release < OV release, balance start < OV warning).
  - Every write is checked against the values the pack reports **right now** plus any writes in flight.

Writable registers are 0x2F-0x68 and 0x80-0x87 (`is_threshold_register` in `eg4_bms_settings.h`). Never write the clock range except through the clock command, and never write 0x69-0x7F (identity).

**One register-map discrepancy to settle with a test write:** `settings_table.py` has 0x52 = short circuit and 0x54 = discharge OC 2. REGISTER_MAP.md's overcurrent section says the reverse. Both ship as 30000 (300 A), so no read can tell them apart. Until a deliberate test resolves it (for example lower one by 1 A, read back, then look at the BMS tool or the display), treat the two as a linked pair and refuse to set them to different values.

## Reference values (EG4's shipped settings)

The reference is a captured parameter block from a pack (ZTR-16S200A, S02T11) on 2026-10-20, before any changes. It's in `tests/rs232_service_frames.txt` (the frame starting `0103B6…`); use it as the test vector and as the tool's built-in "EG4 shipped" profile. Decoded, the values that matter most:

- balance start 3.400 V, delta 0.020 V
- cell UV 2.50 / 2.30 / 3.10 V (warn / prot / release)
- pack UV 46.50 / 44.80 / 48.00 V
- cell OV 3.70 / 3.80 / 3.45 V
- pack OV 59.20 / 60.00 / 55.20 V
- charge OC 1 205 A (10 s), charge OC 2 225 A (3 s), release 180 s, count 5
- discharge OC 1 205 A (10 s), discharge OC 2 300 A (3 s), short circuit 300 A, release 60 s, count 5
- charge UT −2 / −5 / 0 °C, charge OT 65 / 70 / 60 °C
- discharge UT −15 / −20 / −10 °C, discharge OT 70 / 75 / 65 °C
- MOSFET OT 100 / 110 / 80 °C
- ambient UT −20 / −25 / −15 °C, ambient OT 70 / 80 / 60 °C
- heater on 0 °C, off 3 °C

Ship this as a profile labelled with the model and BMS firmware it came from. Warn when the connected pack's model/firmware differ.

## Requirements

1. **Never a blind write.** Every write path (set, restore, copy, profile) goes through this sequence:
   1. Read the parameter block now.
   2. Show a diff of only the registers that change: old → new, in units.
   3. Check every value against LIMITS and the whole resulting block against ORDER.
   4. Get one explicit confirmation.
   5. Write in a safe order.
   6. Re-read the block and report each change as confirmed or NOT applied.
2. **Safe write order:** for a multi-setting restore, compute an order in which every intermediate state still satisfies ORDER. When moving a triple up, write the top first; when moving it down, the bottom first. If no order exists, refuse and say which rule blocks it. Don't just retry.
3. **Backup format:** JSON (or TOML). It holds:
   - the raw 91 registers **including the unknown ones**
   - the decoded values with units
   - model, firmware, serial and timestamp
   - the tool version

   Restore writes only known threshold registers, never the unknowns (0x2F-0x37, 0x3B-0x3C, 0x49-0x4B, 0x59 and so on).
4. **No "factory reset" button.** Restoring EG4's shipped profile through the same diff/confirm path replaces it.
5. **Units:** show °C, with an optional °F display. Store and compare in pack units. Convert °F input exactly and refuse values that don't land on the 1 °C step.
6. **Live view:** read-only realtime readout (pack V, A, SOC, cells, temps, status/warning/protection decoded) so the user can see the pack is healthy before and after.
7. **Dry-run flag** for every write command. It prints the frames it would send.
8. **Logging:** a session log with every frame TX/RX (hex) and every result, saved next to the backup. That's how the 0.010 V drop was spotted.

## Shape and stack (recommendation)

- **Python 3.11+, `pyserial`,** with the Modbus framing written by hand (a few dozen lines; the component's frame splitter is the model). This vendors `settings_table.py` unchanged, so one source of truth stays shared with the ESPHome component.
- **A CLI first:**
  - `eg4tool ports`
  - `eg4tool info`
  - `eg4tool backup out.json`
  - `eg4tool diff [file|--profile eg4-shipped]`
  - `eg4tool set KEY VALUE`
  - `eg4tool restore FILE`
  - `eg4tool clock [--now]`
  - `eg4tool live`
- **Then a small UI:** either a Textual TUI or a local web page served by the same process. Both avoid a native GUI toolkit and run the same on all three OSes.
- **Packaging:** PyInstaller one-file builds for Windows, macOS (arm64 + x86_64) and Linux, from GitHub Actions. On macOS, check that FTDI and CH340 adapters enumerate as `/dev/cu.*`.

## Testing

- **Host tests with no hardware:** CRC, frame building, a decode of the reference frame, LIMITS/ORDER checks, and the write-order planner, including a case with no valid order.
- **A fake pack:** a serial loopback or in-process simulator that serves a block and applies FC16 writes. It should also be able to **ack but drop** a write below a floor, so the "NOT applied" path is tested.
- Test frames already exist in `tests/rs232_service_frames.txt`, and the C++ tests in `tests/rs232_service_test.cpp` show the expected parsing.
- **On a real pack:** only `info`, `backup`, `diff` and `live` until the user says otherwise. Any write test needs the user present and agreeing to that specific write.

## Boundaries

- Use only the public EG4-LL Modbus protocol PDF, the open-source component, and reads/captures of the user's own packs. **Don't decompile or disassemble EG4's Windows tool or pack firmware.** The vendor's terms forbid reverse engineering, and nothing here needs it.
- The tool writes protection limits on a battery. Keep the guard rails above even if they feel slow. They exist because the vendor tool has none.

## Sources

- Repo: `github.com/RAR/esphome-eg4-bms`, branch `feat/rs232-service` (pushed; not merged to main yet).
  - `components/eg4_bms/settings_table.py`: SETTINGS, LIMITS, ORDER
  - `components/eg4_bms/eg4_bms_settings.h`: register constants, `is_threshold_register`, clock packing
  - `components/eg4_bms/eg4_bms.cpp`: read-before-write, ordering check, write confirmation (`write_setting`, `apply_staged_writes_`)
  - `REGISTER_MAP.md`: register notes (see the two stale or contested spots above)
  - `tests/rs232_service_frames.txt`, `tests/rs232_service_test.cpp`, `tests/settings_table_test.py`
  - `atoms3-rs232-service-example.yaml`: the ESP32 device that does this job today through Home Assistant
- `docs/` in that repo: the EG4-LL Modbus protocol PDF (realtime registers 0x00-0x26).
