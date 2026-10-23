# ECO-WORTHY / JBD-UP driver (piece 1 of 2)

Date: 2026-10-23. Status: implemented on branch jbd-up.
Branch: `jbd-up`, from `rename-packkeeper`.

## Goal

Support the user's six ECO-WORTHY 48 V rack packs. They have JBD "ES/UP" BMS
boards rated 100 A, 16S LiFePO4, and connect over RS485.

Using them needs a second wire protocol. JBD-UP is not Modbus: it uses
function 0x78 for reads and 0x79 for writes, byte addresses, and a `JBD`
write key.

This piece adds:

- a pluggable framing layer in the core;
- an "unsafe settings" tier that any driver can use;
- settings with named choices and named bits;
- a read-only Find scan of addresses;
- the `jbd-up` driver itself, with settings, clock, live view and backups.

The EG4-LL and JK-PB paths behave exactly as they do on `rename-packkeeper`.

**Piece 2**, a separate spec, will cover two things:

- live controls: MOSFET on/off, sleep, restart, debug mode and anti-theft
  unlock;
- writing the RS485 baud (0x1C82) and the BMS address (0x1C88), with
  reconnect-and-verify.

Out of scope for both pieces:

- restore defaults (0x290C) and factory reset (0x290E), following the
  handoff's "no factory reset" rule;
- clear history (0x2910);
- calibration (0x2914, 0x2100–0x2126);
- writing the text strings (serial numbers, barcode, dates);
- parallel/"DK" functions 0x43–0x45;
- the YD/T 1363, Vertiv and Leoch protocols;
- firmware update;
- the V2 handshake (`FE AA …`).

## Sources

1. `/Users/rar/eco-worthy-app/JBD-ES-UP-V1.22.35/PROTOCOL.md`, the user's
   notes, reverse engineered from JBD's ES-UP Windows apps V1.22.35 and
   V2.00.40. The user approved using it. It is the only source for the
   protection map at 0x1800.
2. syssi/esphome-jbd-bms `components/jbd_bms_up_pack`. It covers the 0x78/0x79
   framing and the 0x1000 realtime block, with byte offsets checked against
   an UP16S020 capture.
3. PhracturedBlue's ECO-WORTHY JBD-UP gist. It covers the framing, the
   `11 'J' 'B' 'D'` write prefix, and the 0x1C00, 0x2000, 0x2800 and 0x2900
   blocks.

All four example frames in source 1 have valid CRCs. The example write
(cell OV protection = 3650 mV at 0x1806) matches the 0x1800 layout.

## Part 1: core changes

### Framing

New module `src/packkeeper/framing.py`, with a `Framing` interface:

```python
class Framing(Protocol):
    max_read: int                      # registers per read request
    def read_request(self, address, start, count, stride) -> bytes: ...
    def parse_read(self, request, reply) -> list[int]: ...
    def write_request(self, address, start, values) -> bytes: ...
    def parse_write(self, request, reply) -> None: ...
    def reply_length(self, head: bytes) -> int | None: ...   # for the serial layer
    def reply_start(self, request: bytes) -> tuple[int, ...]: ...  # bytes that may start a reply
```

- `ModbusFraming` wraps today's `modbus.py` unchanged. `max_read` is 125.
- Errors:
  - `FrameError`, in `errors.py`, is the common base for a malformed or
    mismatched reply. `ModbusError` becomes a subclass of it.
  - A refusal raises `DeviceRefused`, as today.
- `Driver.framing` defaults to a `ModbusFraming()` instance. A driver that
  needs per-session state, like JBD's key byte, provides a factory:
  `Driver.new_framing() -> Framing`, which defaults to `self.framing`.
- `ModbusDevice` (its name kept for compatibility) takes the framing from
  the driver and uses it for every request and reply:
  - reads are chunked at `framing.max_read`;
  - writes are one frame per setting;
  - logging, retries, `write_refusal` and the writable checks are
    unchanged.
- `SerialTransport(port, timeout, params, framing)` uses
  `framing.reply_length` and `framing.reply_start` to resynchronise and to
  know when a reply is complete. The CLI and GUI pass the driver's framing.
- Dry-run frames come from the device's framing (`Plan.frames(device)`), so
  they carry the session's address and key byte. `Driver.write_frame` and
  `clock_frame` take an optional framing argument and default to the
  driver's.

### Unsafe settings

- `Setting.unsafe: str | None = None` is the note shown to the user, for
  example "a wrong shunt value scales every current reading and over-current
  protection by the same factor".
- `SettingTable.build(..., unsafe={key: note})`.
- `make_plan(..., allow_unsafe=False)`:
  - Without the switch, an unsafe target is an error: "<label> is an unsafe
    setting; turn on unsafe settings to change it".
  - With it, the plan lists unsafe changes in `plan.unsafe`.
- `targets_from_block(source, skip, allow_unsafe=False)` leaves unsafe
  settings out. Restore and copy report "N unsafe setting(s) left alone (use
  --unsafe to include them)".
- CLI:
  - A common `--unsafe` option.
  - When a plan has unsafe changes, the confirmation lists them with their
    notes, and you must type `unsafe` instead of `yes`.
  - `keys` marks unsafe settings.
- GUI:
  - A **Show unsafe settings** checkbox, shown only when the driver has
    unsafe settings. Ticking it shows a warning dialog the first time in a
    session.
  - Unsafe settings live on their own page, hidden until the box is ticked.
  - Review lists unsafe changes separately, in red, with their notes. The
    Write button stays disabled until `unsafe` is typed into a field.
  - Unticking the box drops any staged unsafe changes.
- EG4-LL and JK-PB have no unsafe settings, so nothing about them changes.

### Choices and bits

- `Setting.choices: dict[int, str] | None`:
  - `format` shows the name, or `"unknown (n)"`;
  - `parse` accepts the name (case-insensitive) or the number;
  - the GUI shows a dropdown;
  - the limit check is "the value is one of the choices".
- `Setting.bits: dict[int, str] | None`:
  - `format` shows `0x0015 (heater, buzzer, anti-theft)`;
  - `parse` accepts a number (hex or decimal);
  - the GUI shows the bit names next to the value.
- Both pass through the backup's raw/value check unchanged, since the stored
  value is the raw number.

### Find

- A driver can set `find_addresses: range | None`. When set, it must lie
  within `address_choices`.
- `scan(driver, transport, addresses, log) -> list[Found]`:
  - for each address, read the driver's identity area once with no retries
    and a short timeout (0.3 s);
  - return `Found(address, model, serial)` for each one that answers;
  - it only reads; every frame is logged.
- CLI: `packkeeper find --bms jbd-up [--port P]`.
- GUI: a **Find** button next to the address box. It opens a list of the
  packs that answered, and choosing one sets the address.

## Part 2: the `jbd-up` driver

Package `src/packkeeper/drivers/jbd_up/`. Name `jbd-up`, label
`ECO-WORTHY / JBD-UP`.

### Wire format (`framing.py` in the package, `JbdFraming`)

```
addr | func | start (BE16) | end (BE16) | len (BE16) | data[len] | CRC16-Modbus (LE)
```

- Read request: `addr 78 start end 0000`, with `end = start + 2·count`.
  - The reply echoes addr, func, start and end, and carries `len` data
    bytes.
  - `max_read` is 245 registers (490 bytes).
- Write request:
  `addr 79 reg reg+n (n+4) KK 4A 42 44 <n data bytes>`, with n = 2·count.
  - `KK` is the key byte. The reply is checked by addr, func, start, end
    and CRC.
- `reply_length(head)` returns `10 + len` once the first 8 bytes are known.
- No exception frame is documented, so a mismatch or bad CRC is a
  `FrameError`, retried like any bad reply.
- `JbdFraming.key` is set from the last block read: the low byte of 0x281A,
  or 0x11 if that is 0. The driver's `new_framing()` gives each device its
  own `JbdFraming`.

### Connection

- Serial: 9600 8N1.
- Address: the DIP value, wire byte as is. `address_choices = range(0, 64)`,
  default 0, `find_addresses = range(0, 16)`.
- Texts:
  - Connect note: "One pack at a time: unplug the link cables, plug the
    USB-RS485 adapter into the pack's RS485 port, and set Address to its
    DIP value, or use Find."
  - No-reply help: check the A/B wiring, the address (use Find), the baud
    (9600 unless it was changed), and that the link cables are unplugged.
  - Address 0 is a normal address in this protocol, so the broadcast
    warning must not appear for this driver. The warning is generic today;
    it becomes `Driver.broadcast_address: int | None`, set to 0 for Modbus
    drivers and None here.

### Areas (all stride 2)

| Area | Start | Registers | Request names | Notes |
|---|---|---|---|---|
| protection | 0x1800 | 104 | 0x1800–0x1900 | a real pack sends 208 bytes (to 0x18D0) |
| pack | 0x1C00 | 68 | 0x1C00–0x1CA0 | through 0x1C86; a real pack (2026-10-23) sends no 0x1C88 address or V2 fan registers, so they are left out |
| capacity | 0x2000 | 32 | 0x2000–0x2050 | through the function bits at 0x203E |
| clock | 0x2800 | 6 | 0x2800–0x280F | Y, M, D, h, m, s as u16, full year |
| version | 0x2810 | 22 | 0x2810–0x2840 | a real pack sends 44 bytes (to 0x283C) |

Revised 2026-10-23 after the first real-pack session: the pack answers a
block read with its own block length, whatever end the request names. It
sent 208 bytes for 0x1800–0x1900, 44 for 0x2810–0x2840 and 162 for
0x1000–0x10A0. Requests name the stock app's ranges; replies of any length
are accepted, as the app does; an area is refused only when the reply is
shorter than the registers it holds, and extra registers are dropped.

Live data: one read of 0x1000–0x10A0 (80 registers).

### Normal settings

All are u16 and width 1 unless noted. The ranges are this tool's proposals
for 16S LiFePO4 with a 100 A BMS. The first read-only session checks them
against the pack and the stock app.

Protection area. Most groups have six registers: alarm, alarm recovery,
alarm delay (ms), protection, protection recovery, protection delay (ms).

| Group | Base | Scale | Alarm/protection range | Recovery range |
|---|---|---|---|---|
| Cell OV | 0x1800 | 1 mV | 3.400–3.650 V | 3.300–3.600 V |
| Cell UV | 0x180C | 1 mV | 2.500–3.100 V | 2.600–3.200 V |
| Pack OV | 0x1818 | 0.01 V | 54.40–58.40 V | 52.80–57.60 V |
| Pack UV | 0x1824 | 0.01 V | 40.00–49.60 V | 41.60–51.20 V |
| Charge OT | 0x1858 | 0.1 °C, −50 °C offset | 30–70 °C | 25–65 °C |
| Charge UT | 0x1864 | same | −20–10 °C | −15–15 °C |
| Discharge OT | 0x1870 | same | 40–80 °C | 35–75 °C |
| Discharge UT | 0x187C | same | −30–0 °C | −25–5 °C |
| MOSFET OT | 0x1888 | same | 60–110 °C | 50–100 °C |
| Ambient OT | 0x1894 | same | 40–80 °C | 35–75 °C |
| Ambient UT | 0x18A0 | same | −30–0 °C | −25–5 °C |
| Temperature diff | 0x18AC | 0.1 °C | 2–30 °C | 1–29 °C |
| Cell diff | 0x18B8 | 1 mV | 0.050–1.000 V | 0.030–0.990 V |
| SOC low | 0x18C4 | 1 % | 0–50 % | 1–60 % |

Current groups, scale 0.1 A:

| Group | Base | Registers |
|---|---|---|
| Charge OC 1 | 0x1830 | alarm, alarm recovery, alarm delay, protection, protection delay, release delay, lockout count |
| Charge OC 2 | 0x183E | protection, delay, release delay |
| Discharge OC 1 | 0x1844 | as charge OC 1 |
| Discharge OC 2 | 0x1852 | as charge OC 2 |

- Over-current ranges: stage 1 alarm, recovery and protection are 1–150 A
  (real packs ship stage 1 protection at 120 A; the stage 1 < stage 2 rule
  bounds it). Stage 2 protection is 1–150 A.
- Every delay is 100–60 000 ms, in steps of 100 ms, except the over-current
  release delays: those are seconds (the stock app shows 60 s and 600 s),
  0–3600 s. Lockout count is 0–10.

Pack area:

| Key | Reg | Scale | Range |
|---|---|---|---|
| balance start voltage | 0x1C04 | 1 mV | 3.000–3.500 V |
| balance start delta | 0x1C06 | 1 mV | 0.005–0.100 V |
| heater on / off | 0x1C08 / 0x1C0A | 0.1 °C −50 | −20–15 °C / −15–20 °C |
| full-charge calibration voltage | 0x1C0C | 0.01 V | 52.00–58.40 V |
| full-charge calibration current | 0x1C0E | 1 mA | 100–20 000 mA |
| sleep voltage | 0x1C78 | 1 mV | 2.500–3.200 V |
| sleep delay | 0x1C7A | 1 min | 1–1440 |
| balance mode | 0x1C7C | choice | 0 charge only, 1 static |
| standby sleep current | 0x1C84 | 0.1 A | 0.1–5.0 A |
| static sleep delay | 0x1C86 | 1 min | 0–1440 (ships at 0) |
| fan on / off | 0x1C8A / 0x1C8C | 0.1 °C −50 | 30–70 °C / 25–65 °C |

The fan registers exist only on V2 firmware. If they read 0, they are shown
as "—" and planning them is refused.

Capacity area:

| Key | Reg | Scale | Range |
|---|---|---|---|
| design capacity | 0x2000 | 0.01 Ah | 10–600 Ah |
| full capacity | 0x2002 | 0.01 Ah | 10–600 Ah |
| SOC table voltage 1–4 | 0x201A, 0x201E, 0x2022, 0x2026 | 1 mV | 2.500–3.650 V |
| SOC table percent 1–4 | 0x201C, 0x2020, 0x2024, 0x2028 | 1 % | 0–100 % |
| charge request voltage / current | 0x202A / 0x202C | 0.1 V / 0.1 A | 48.0–58.4 V / 1–100 A |
| discharge request voltage / current | 0x202E / 0x2030 | 0.1 V / 0.1 A | 40.0–52.0 V / 1–100 A |

The SOC table's (mV, %) pairs follow the user's notes, which list them as
interleaved. The first session confirms the order.

### Order rules

Every pair below shares a scale.

- High-limit groups (cell/pack OV, charge/discharge OT, MOSFET OT,
  ambient OT, temperature diff, cell diff):
  - alarm recovery < alarm ≤ protection;
  - protection recovery < protection.
  - Temperature diff only: recovery ≤ trigger, since a real pack ships with
    8.0 / 8.0.
- Low-limit groups (cell/pack UV, charge/discharge UT, ambient UT, SOC low):
  - protection ≤ alarm < alarm recovery;
  - protection < protection recovery.
- Cell UV protection < cell OV protection. Pack UV protection < pack OV
  protection.
- Stage 1 OC: alarm recovery < alarm ≤ protection, charge and discharge
  separately. Stage 1 protection < stage 2 protection.
- Heater on < heater off. Fan off < fan on.
- SOC table voltages increase from point 1 to 4, and so do the percents.
- Full capacity ≤ design capacity.

There are no linked pairs.

### Unsafe settings, each with its note

| Key | Reg | Shown as | Note |
|---|---|---|---|
| cells in series | 0x2032 | 1–32 | A wrong count breaks every cell and pack protection. |
| shunt resistance | 0x2034 | 0.001 mΩ, 0.050–5.000 | Scales every current reading, and over-current protection with it. |
| short-circuit current | 0x2038 | raw, 0–65535 | The last line of protection; the unit is not published. |
| short-circuit delay step | 0x203A | raw, 0–65535 | Same. |
| pack type | 0x1C00 | choice: LFP, NCM | Changes the chemistry model behind every curve. |
| cell maker | 0x1C02 | choice: Firm1–3 | Changes the cell model. |
| SOC config bits | 0x203C | bits: 0x0005 no capacity learning, 0x0050 disable SOC curve | Changes how SOC is learned. |
| function bits | 0x203E | bits: 0x0001 heater, 0x0004 buzzer, 0x0010 anti-theft, 0x0040 GPS, 0x0100 4-digit SOC, 0x0400 LED board | Anti-theft can lock the pack out. |
| CAN inverter protocol | 0x1C76 | choice, 16 names from the notes | The wrong protocol cuts off the inverter. |
| RS485 inverter protocol | 0x1C7E | choice, 16 names | Same. |
| CAN baud | 0x1C80 | choice: 500K, 100K, 125K, 250K | Same. |

### Read-only

These are shown and kept in backups, never written:

- remaining capacity (0x2004), SOC (0x2006), SOH (0x2008), cycles (0x200A);
- the cycle factors (0x200C/0x200E);
- the cumulative charge and discharge, u32 (0x2010/0x2014);
- self-discharge (0x2018);
- the V2-only capacity fields;
- RS485 baud (0x1C82) and BMS address (0x1C88), until piece 2;
- the serial numbers, barcode and dates (0x1C10–0x1C74);
- the version block.

`is_writable` is true only for the registers of the settings above, normal
or unsafe.

### Checks

- `blank_reason` refuses a read when:
  - the whole protection area is zero;
  - cell OV protection (0x1806) is outside 3000–4500 mV;
  - or cells in series (0x2032) is outside 1–32.
- `check_device`, reads only: read the live signal word (0x1022). If bit 15
  (large capacity) is set, turn writes off with "this pack reports
  large-capacity mode (capacities in 0.1 Ah); this build models 0.01 Ah". A
  failed read also turns writes off.
- `identity_warnings`: warn when the version block is all zero, or the
  protocol version is 0, because the default key 0x11 is then a guess.

### Identity

- Model: the HW name at 0x281C (16 B ASCII), or the project name at 0x282C
  if that is empty.
- Firmware: `app <major>.<minor> (hw <0x2812>, protocol <0x281A as x.y>)`.
- Serial: the pack serial at 0x1C34 (30 B ASCII). It is shown in Info, the
  connect bar, backups and Find.
- Restore and copy add a warning when the source's serial differs from the
  connected pack's ("this backup is from pack <A>; the connected pack is
  <B>"). It is a warning, not a refusal, because copying between packs is
  the point.

### Clock

- Read from 0x2800 as Y, M, D, h, m, s, local time.
- Set by one write of 6 registers (n = 12) at 0x2800, confirmed by read-back
  within 5 s.
- The tool never sets the clock on its own.

### Live (`Live`)

The 0x1000 realtime block, at byte offsets from the start of the data:

- Pack voltage (0x00, ×0.01 V) and current (0x04, u32, raw × 0.01 − 3000 A).
- SOC (0x08, ×0.01 %) and SOH (0x16).
- Remaining, full and nominal capacity (0x0A/0x0C/0x0E, ×0.01 Ah).
- MOSFET and ambient temperature (0x10/0x12), then the sensor
  temperatures, all (raw − 500)/10 °C.
- State (0x14): standby, charging or discharging.
- Protection bits (0x18) and alarm bits (0x1C), u32 each, named as in the
  notes.
- MOSFET and I/O bits (0x20): charge and discharge allowed, and extras for
  heater, fan and dry contacts.
- Signal bits (0x22): charger and load present.
- Cycles (0x24), and max/min/average cell (0x26–0x2E).
- The inverter request limits (0x3A–0x40), as an extra line.
- Cells: count N at 0x42, then N × u16 mV. Temperature sensors: count M,
  then M values. Then the balancing bitmap, u32 with bit 0 = cell 1.
- The decode stops cleanly at the end of the data. A short block gives
  fewer cells, not an error.

### Simulator (`jbd_up/sim.py`)

`FakeJbd(driver, *, address=0, serial="SIM-PACK-1", large_capacity=False,
floors=None, key=0x11, state_file=None)`. It:

- speaks `JbdFraming`, ignoring frames for other addresses;
- ignores a write whose key or `JBD` prefix is wrong (no reply), which is
  this tool's guess at real behaviour;
- holds plausible invented data for a 16S 100 Ah pack, not JBD defaults;
- keeps a floor on one setting so a low write is dropped, for the
  NOT_APPLIED path;
- runs a clock after it is set;
- can serve several packs on one transport (`FakeJbdBus(packs)`) for Find
  and copy tests.

### Pages

- **Voltage:** a Cell and Pack OV/UV matrix with Alarm / Protection /
  Recovery columns, plus delays.
- **Current:** OC stage 1 and 2 for charge and discharge, with delays and
  lockout count.
- **Temperature:** a matrix of charge/discharge OT/UT, MOSFET OT and ambient
  OT/UT, plus temperature diff.
- **Balancing & sleep:** cell diff, balance start voltage and delta, mode,
  heater, fan, the sleep settings, full-charge calibration.
- **Capacity & inverter:** design and full capacity, the SOC table, the
  request voltages and currents, SOC low.
- **Unsafe:** every unsafe setting, shown only when unsafe settings are on.

The layout test covers this driver: every setting appears exactly once,
including unsafe ones on their page.

## Testing

1. Framing:
   - `JbdFraming` builds the read and write frames from source 1
     byte-for-byte (`00 78 10 00 10 A0 00 00 BE 7E`,
     `00 78 18 00 19 00 00 00 BC 88`, and the cell OV write).
   - It parses replies, rejects bad CRC, address, function and range,
     and handles the key byte.
   - `ModbusFraming` gives exactly today's frames.
2. Core:
   - unsafe refused without the switch;
   - restore and copy skip and count unsafe settings;
   - the CLI's typed `unsafe` confirmation, and `keys` marking;
   - choices and bits parse and format;
   - Find on a fake bus of 3 packs at addresses 0, 2 and 5;
   - `broadcast_address` gives no warning for JBD.
3. JBD on the simulator:
   - every setting decodes;
   - plan order and rules;
   - write CONFIRMED and NOT_APPLIED, with a wrong key dropping the write;
   - the large-capacity flag turns writes off;
   - the plausibility refusals;
   - clock set and read-back;
   - live decode, including a short block;
   - a backup round-trip keeping the read-only registers;
   - copy between two fake packs warning on the serial mismatch.
4. GUI:
   - connect, and Find choosing a pack;
   - the unsafe checkbox shows the page, and Review needs the typed
     `unsafe`;
   - unticking drops staged unsafe changes.
5. Parity: the 17 EG4 scenarios against `main` show no differences, and
   every JK and toy test passes unchanged.
6. The import boundary covers `jbd_up`, the vendored EG4 table is unchanged,
   `ruff` passes, and CI is green on four platforms.

## First real-pack session (ECO-WORTHY)

Read-only:

1. `find`, `info`, `backup`, `live` on one pack.
2. Compare with the stock app: thresholds, delays, temperatures, capacity,
   SOC table order, clock, serial and the protocol version (key byte).

Writes come only after that, with the user present and agreeing to each
specific write. Unsafe settings come later still, one at a time.
