# JK-PB driver

Date: 2026-10-23. Status: implemented on branch jk-pb.
Branch: `jk-pb`, from `rename-packkeeper`.

## Goal

Add a driver for the JK PB2A16S20P inverter BMS (tested on hardware
v15 and v19 units), used on 16S LiFePO4 packs. It supports the full tool:
info, backup, diff, restore, copy, set, clock and live, with the same
guard rails as EG4-LL. To get there, the generic core learns 32-bit
(and signed) settings, multi-register writes and register maps split
across several areas.

The EG4-LL path behaves as it does on `main`. The one expected visible
difference is the backup format version (3).

Out of scope:

- Bluetooth.
- Parallel packs on a shared bus. One pack at a time, link cables unplugged.
- JBD / ECO-WORTHY.
- The charge, discharge and balance switches.
- One-key presets, calibration, shutdown and emergency start.
- The protocol and CAN settings at 0x1400.
- Li-ion and LTO limits.

## Sources

JK's own RS485 Modbus documents, `JK_BMS.RS485.Modbus.v1_0.pdf` and
`BMS RS485 Modbus V1.1.pdf`, as shipped in
[syssi/esphome-jk-bms](https://github.com/syssi/esphome-jk-bms) under
`docs/pb2a16s20p/`.

Community configs for the same model,
[esphome-jk-bms discussion #501](https://github.com/syssi/esphome-jk-bms/discussions/501),
fill in two details the documents leave out:

- Pack voltage is read as a 32-bit value at 0x1290, and cells at
  0x1200, 0x1202, and so on. So an address is the area start **plus the
  byte offset**, and 32-bit values are high word first.
- Devices are polled at Modbus address 0.

No vendor software is decompiled.

## Facts from the documents

- Modbus RTU over RS485, 115200 8N1. Supported function codes are 03
  (read) and 10 (write multiple). Addresses are 1 to 247 per the v1.0
  document; community configs poll address 0.
- Areas:
  - 0x1000: settings, read/write.
  - 0x1200: live data, read only.
  - 0x1400: device info and protocol settings.
  - 0x1600: write-only commands.
- Each field has a byte offset within its area. Most settings are 4-byte
  UINT32 or INT32 values. Temperatures are INT32 in 0.1 °C.

## Part 1: generic core changes

### Wide and signed settings

`Setting` gains two fields:

- `width: int = 1`: how many 16-bit registers it occupies (1 or 2).
- `signed: bool = False`.

`Setting.raw()` checks the value fits the register: 0 to 2^(16·width)−1
when unsigned, −2^(16·width−1) to 2^(16·width−1)−1 when signed.
Decimals, step checks and °F conversion are unchanged.

The driver sets `word_order`, `"high_first"` (default) or `"low_first"`,
for how a wide value's two registers are ordered.

`SettingTable.build` takes the width and signed flag from the spec tuple.
It gets them through an optional extension of the tuple, so the vendored
EG4 table format still builds unchanged.

### Areas instead of one block

The driver lists what a pack read covers:

```python
areas: tuple[Area, ...]   # Area(start, count, stride=1)
```

- `stride` is the address step between consecutive 16-bit registers in
  an area: 1 for normal Modbus, 2 for JK, which addresses by byte.
  Register *i* of an area is at address `start + i·stride`.
- Register numbers everywhere (settings, plan, backups, frames, logs) are
  the pack's own addresses, so they match the vendor's map and the
  session log shows the bytes actually sent.
- EG4-LL has `areas = (Area(0x2D, 91),)`. `block_start` and
  `block_count` are removed.

`Block(driver, areas)` holds one tuple of register values per area.

- `reg(address)` finds the area holding that address and checks the
  address is on its stride. An address outside every area is an error.
- `raw(key)` joins a wide setting's registers using `word_order` and
  applies the sign. `with_raws` splits them back.
- `threshold_registers()` is every address the driver calls writable.
  `unknown_registers()` is the writable addresses that no setting covers,
  counting every register of a wide setting.
- Two blocks are equal when their drivers and area values are equal.

### Reads

`ModbusDevice.read_block` reads each area. An area longer than 125
registers, the FC03 limit, is read in chunks of at most 125. Each chunk
starts at `start + i·stride`. EG4-LL's 91 registers are still one read,
so its frames are unchanged.

`live_reads` stays a list of `(start, count)` reads, now also chunked
and stride-aware.

### Writes

- `Driver.write_frame(setting, raw, address)` builds one FC16 frame
  covering `setting.width` registers. For a width-1 setting it is the
  same frame as today.
- `ModbusDevice.write_setting(setting, raw)` checks every register the
  setting covers with `is_writable` before sending.
- `apply_plan` keeps its generic writable guard over every register of
  every change, and its full re-read after each write.

### Device address per session

A driver can declare `address_choices: range | None`. It is None for
EG4-LL, which stays fixed at address 1. For JK it is `range(0, 248)`
with default 1.

- CLI: a common `--address N` option. It is refused for a driver with no
  choices.
- GUI: an address box in the connect bar, shown only when the driver has
  choices. It is locked while connected.
- The chosen address lives on the device. `Plan.frames(address)` and the
  dry run use the session's address.

### A driver check that can refuse writes

`Driver.check_device(device, block) -> str | None` runs after every
`read_block`, using reads only. It returns None for EG4-LL.

A non-None result is stored on the device as `write_refusal`. While it is
set:

- `write_setting` and `write_clock` raise `PackError(write_refusal)`.
- `apply_plan` refuses before any I/O and reports the reason.
- The GUI shows it in the connect bar and the Settings and Clock tabs.
- Reads, backup, diff and live still work.

### Backups: format version 3

```json
"areas": [{"start": 4096, "stride": 2, "registers": [...]}]
```

This replaces `start_register`, `register_count` and `registers`.
`format` stays `"eg4tool-backup"`.

- Versions 1 and 2 still load, as one EG4-LL area.
- The loader checks each area's start, stride and count against the
  named driver.
- The check that decoded values match the raw registers stays.
- Settings record their register as `0x....` and their width.

## Part 2: the JK-PB driver

Package `src/packkeeper/drivers/jk_pb/`, with name `jk-pb` and label
`JK-PB`.

### Connection

- Serial: 115200 8N1.
- Address: default 1, choices 0 to 247.
- Connect note and help: a USB–RS485 adapter on one of the pack's RS485
  ports, one pack at a time, link cables unplugged.
- No-reply hint: check A/B wiring, the DIP address (try 0 or the DIP
  value), and the baud.

### Areas

| Area | Start | Registers | Stride | Holds |
|---|---|---|---|---|
| settings | 0x1000 | 141 (bytes 0x000–0x119) | 2 | everything in the settings map |
| identity | 0x1400 | 20 (bytes 0x00–0x27) | 2 | model, hardware, software, run time, power-on count |
| clock | 0x1300 | 2 | 2 | RTC counter (0x1200 + 0x100), seconds since 2020-01-01 |

Addresses in this spec are given for `STRIDE = 2`. `STRIDE = 2` is one
constant. The table is built from byte offsets as
`address = AREA + (offset // 2) · STRIDE`. If the pack turns out to use
word addressing, changing that one constant to 1 moves every address,
backup layout and frame, and the tests run on both.

### Settings: 30 writable

All are LiFePO4 16S ranges and are proposals. The first read-only session
compares them with the pack's current values and the JK app's limits
before any write.

| Key | Offset | Unit (raw) | Range | Step |
|---|---|---|---|---|
| `smart_sleep_voltage` | 0x00 | V (mV) | 2.500–3.400 | 0.001 |
| `cell_uv_protection` | 0x04 | V (mV) | 2.500–3.100 | 0.001 |
| `cell_uv_release` | 0x08 | V (mV) | 2.600–3.200 | 0.001 |
| `cell_ov_protection` | 0x0C | V (mV) | 3.450–3.650 | 0.001 |
| `cell_ov_release` | 0x10 | V (mV) | 3.300–3.600 | 0.001 |
| `balance_trigger_delta` | 0x14 | V (mV) | 0.005–0.100 | 0.001 |
| `soc_100_voltage` | 0x18 | V (mV) | 3.300–3.600 | 0.001 |
| `soc_0_voltage` | 0x1C | V (mV) | 2.500–3.200 | 0.001 |
| `charge_voltage` | 0x20 | V (mV) | 3.300–3.650 | 0.001 |
| `float_voltage` | 0x24 | V (mV) | 3.200–3.500 | 0.001 |
| `power_off_voltage` | 0x28 | V (mV) | 2.500–3.000 | 0.001 |
| `charge_oc_current` | 0x2C | A (mA) | 1–200 | 0.001 |
| `charge_oc_delay` | 0x30 | s | 1–600 | 1 |
| `charge_oc_release_time` | 0x34 | s | 1–600 | 1 |
| `discharge_oc_current` | 0x38 | A (mA) | 1–200 | 0.001 |
| `discharge_oc_delay` | 0x3C | s | 1–600 | 1 |
| `discharge_oc_release_time` | 0x40 | s | 1–600 | 1 |
| `short_circuit_release_time` | 0x44 | s | 1–600 | 1 |
| `max_balance_current` | 0x48 | A (mA) | 0.1–2.0 | 0.001 |
| `charge_ot_protection` | 0x4C | °C (0.1, signed) | 30–70 | 0.1 |
| `charge_ot_release` | 0x50 | °C (0.1, signed) | 25–65 | 0.1 |
| `discharge_ot_protection` | 0x54 | °C (0.1, signed) | 40–80 | 0.1 |
| `discharge_ot_release` | 0x58 | °C (0.1, signed) | 35–75 | 0.1 |
| `charge_ut_protection` | 0x5C | °C (0.1, signed) | −10–10 | 0.1 |
| `charge_ut_release` | 0x60 | °C (0.1, signed) | −5–15 | 0.1 |
| `mos_ot_protection` | 0x64 | °C (0.1, signed) | 60–100 | 0.1 |
| `mos_ot_release` | 0x68 | °C (0.1, signed) | 50–90 | 0.1 |
| `design_capacity` | 0x7C | Ah (mAh) | 1–1000 | 0.001 |
| `short_circuit_delay` | 0x80 | µs | 50–1500 | 1 |
| `balance_start_voltage` | 0x84 | V (mV) | 3.000–3.500 | 0.001 |

- All are width 2. Units are set explicitly; none rely on the
  key-suffix guess. `short_circuit_delay` is µs, not s.
- `is_writable` is true only for the two registers of each of these 30
  settings. That is an explicit set, not a range.

Order rules. `<` is strict, `≤` is not; all pairs share a scale.

- `power_off_voltage < cell_uv_protection < cell_uv_release`
- `cell_ov_release < cell_ov_protection`
- `float_voltage ≤ charge_voltage < cell_ov_protection`
- `balance_start_voltage < cell_ov_protection`
- `charge_ot_release < charge_ot_protection`, `discharge_ot_release <
  discharge_ot_protection`, `mos_ot_release < mos_ot_protection`
- `charge_ut_protection < charge_ut_release`

There are no linked pairs.

### Read-only values: shown, backed up, never written

These are kept in the settings area and therefore in every backup. They
are shown as `extra` lines in Info and Live:

- cell count (0x6C)
- the charge, discharge and balance switches (0x70, 0x74, 0x78)
- device address (0x108)
- pre-charge time (0x10C)
- feature bits (0x114: heating, sensor disable, GPS heartbeat, port
  switch, LCD always on, special charger, smart sleep, parallel current
  limit, timed data storage, charging float mode)
- smart-sleep time (0x118)

The balance-wire resistances (0x88–0x107) stay raw in backups only.
None of these addresses are writable.

### Checks

- `blank_reason`:
  - Refuse a settings area that is all zeros.
  - Refuse when `cell_ov_protection` is outside 2.000–4.500 V. If
    swapping its two registers would make it plausible, the message
    says the word order looks wrong.
- `check_device`, reads only:
  - Read `cell_ov_protection` alone, 2 registers, at the configured
    address (0x100C when `STRIDE` is 2, 0x1006 when it is 1).
  - Also read 2 registers at the other numbering's address. That is
    0x1006 when `STRIDE` is 2, or 0x100C when `STRIDE` is 1.
  - Writes stay allowed only if the configured address matches the bulk
    read and the other one does not.
  - Anything else sets `write_refusal`, and the message says what each
    address returned. An exception reply counts as no match.
- `identity_warnings`: warn when the model text does not start with
  `JK_PB` or `JK-PB`.

### Identity and clock

- Model: ASCII at 0x1400 (16 bytes). Firmware: shown as
  `<software> (hw <hardware>)`, from 0x1410 and 0x1418. No serial number
  is in the map, so serial is blank.
- Clock read: the RTC counter at 0x1300, a UINT32 of seconds since
  2020-01-01 00:00. It is treated as local time, like EG4-LL; the first
  session confirms this.
- Clock write: 2 registers at 0x1612 (0x1600 + 0x12, "time
  calibration"), UINT32, same epoch. This is the only command-area
  address the driver ever writes, and only through `write_clock`. It is
  confirmed by reading 0x1300 back within 5 seconds.

### Live

Two chunked reads of 0x1200, bytes 0x000–0x10D. Decoded into `Live`:

- cells 1 to N, with N from the settings cell count, capped at 32
- pack voltage (0x90), power (0x94), current (0x98, signed)
- MOSFET temperature (0x8A) and battery temperatures 1–5 (0x9C, 0x9E,
  0xF8, 0xFA, 0xFC)
- SOC (low byte of 0xA6), SOH (0xB8)
- remaining and full capacity (0xA8, 0xAC), cycles (0xB0)
- balancing state (high byte of 0xA6) and balance current (0xA4)
- charge and discharge state (0xC0)
- alarm bits (0xA0), named from the V1.1 document

### Simulator

`jk_pb/sim.py` builds a `FakeModbusDevice` with:

- a plausible LiFePO4 16S image. This is invented test data, not JK
  defaults.
- configurable `stride`, `word_order` and address.
- one register that silently drops writes below a floor, for the
  NOT_APPLIED path.
- a clock.

The generic fake gains stride-aware addressing, FC16 writes of any
length, and an exception reply for addresses outside its areas.

### Pages

- Voltage:
  - Cell protection: a Protection / Release matrix with rows
    Under-voltage and Over-voltage.
  - Charging: charge, float, SOC 100 %, SOC 0 %.
  - Shutdown: power-off, smart sleep.
- Current: Charge and Discharge rows with Trip at (A) / After (s) /
  Release (s) columns. Short circuit: delay (µs) and release (s).
- Temperature: a Protection / Release matrix with rows Charge hot,
  Discharge hot, Charge cold, MOSFET hot.
- Balancing & capacity: trigger delta, start voltage, max balance
  current, design capacity.

The layout test covers JK: every setting exactly once, and rows fit
their columns.

### Profiles

None. There is no captured JK block, so `--profile` lists no choices for
JK, and the GUI's Compare-with offers backups only.

## Testing

1. All existing tests pass, with only mechanical changes:
   `block_start` / `block_count` become `areas`, and `write_frame` takes
   a setting.
2. Core tests:
   - wide and signed raw ranges;
   - `word_order` both ways;
   - multi-area `Block` and stride addressing;
   - chunked reads at 125 registers;
   - one FC16 frame per wide setting;
   - backup v3 round-trip, with v1 and v2 still loading.
3. JK on the simulator, run with `STRIDE` 2 and 1:
   - plan order and rules;
   - apply CONFIRMED and NOT_APPLIED;
   - refusals for an all-zero block, the wrong word order, and a failed
     address check. Every write is refused after a failed check, with
     no frames sent.
   - clock set and read-back;
   - live decode;
   - a backup round-trip that keeps the read-only and resistance
     registers;
   - the CLI with `--address`, refused for EG4-LL.
4. GUI with JK: connect, the address box shown, and pages drawn. A write
   refusal is shown, and the address box is hidden for EG4-LL.
5. The import boundary still passes, and the vendored EG4 table hash is
   unchanged.
6. EG4-LL parity: the 17 simulated CLI scenarios against `main`. Frames,
   write order, refusals, exit codes and sim state must be identical.
   Backups may differ only by format version 3 and the `areas` layout.
7. `ruff`, and CI green on all four platforms.

## First real-pack session (JK)

Read-only, under the standing rules:

1. `info`, `backup`, `live` on one pack.
2. Compare with JK's app: settings, cell count, temperatures, and the
   clock.

Writes come only after that, with the user present and agreeing to each
specific write. The session confirms:

- the baud;
- which address the pack answers at;
- the stride, through the `check_device` result;
- the word order;
- the clock epoch and time zone;
- the proposed ranges.
