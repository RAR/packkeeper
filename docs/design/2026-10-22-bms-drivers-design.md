# BMS drivers: making the tool pluggable

Date: 2026-10-22. Status: implemented on branch bms-drivers.

## Goal

Everything EG4-LL specific moves behind a driver interface, so a second BMS
can be added later as one new package. EG4-LL is the only driver for now.
No user-visible behaviour of the EG4-LL path changes.

Out of scope: a second real driver, auto-detection, data-file (JSON/YAML)
drivers, renaming the app ("EG4 BMS Tool") or the `eg4tool` package.

## What is generic and what is not

Generic (stays in `src/eg4tool/`, must not import any driver):

| Module | Notes |
|---|---|
| `modbus.py`, `transport.py` | Modbus RTU framing and serial I/O; baud rate becomes a parameter |
| `session.py` | frame log; header also records the driver |
| `settings.py` | `Setting`, `Rule`, `SettingError`, °F helpers, and a new `SettingTable` |
| `block.py` | `Block`, a register image bound to its driver |
| `live.py` | `Live`, the generic live reading (today's `Realtime`, without the decode) |
| `planner.py`, `service.py`, `backup.py` | take everything from the block's driver |
| `device.py` | `Device` protocol plus `ModbusDevice` (today's `Pack`) |
| `sim.py` | `FakeModbusDevice`, parameterised by an image and the driver's quirks |
| `layout.py` | `Section`, `Page`, `TEMP`, `COLUMN_HELP` (no page contents) |
| GUI, CLI | take a driver; never import EG4 names |

EG4-LL specific (moves to `src/eg4tool/drivers/eg4_ll/`):

- `settings_table.py`, vendored, byte-for-byte unchanged
  (sha256 `de30c596…ed78bd`, pinned by a test).
- Table build: `LIMIT_OVERRIDES` (balance delta ≥ 0.020 V), linked pair
  0x52/0x54, `GROUPS`, label words.
- Register layout: block 0x2D ×91, writable ranges 0x2F–0x68 and 0x80–0x87,
  clock at 0x2D–0x2E and its bit packing, model/firmware/serial text
  registers.
- The all-zero (RS485 bus) check and its message.
- Live-view decode: 0x00 ×18 + 0x12 ×21, status, alarm and error bits.
- `PAGES` (Voltage, Current, Temperature, Balancing & capacity).
- The "EG4 shipped values" profile and captured frames.
- `CONNECTION_HELP`, serial settings (115200 8N1, address 1).
- Simulator setup: shipped image, balance-delta floor at 0x39.

## The driver interface

`drivers/base.py` defines `Driver`. Each driver package exposes one
instance as `DRIVER`; `drivers/__init__.py` holds the registry:
`DRIVERS: dict[str, Driver]` and `get(name)`. Registration is explicit,
with no plugin discovery.

```python
class Driver:
    name: str                     # "eg4-ll": CLI --bms value, backup field
    label: str                    # "EG4-LL", shown in the BMS selector
    table: SettingTable           # settings, order rules, linked pairs, rank
    block_start: int              # first register of the settings block
    block_count: int
    serial: SerialParams          # baud, bytesize, parity, stopbits
    address: int                  # Modbus address
    pages: tuple[Page, ...]       # Settings tab layout; every setting exactly once
    profiles: dict[str, Profile]  # built-in reference blocks
    connection_help: str
    supports_clock: bool

    def is_writable(self, register: int) -> bool: ...
    def identity(self, block: Block) -> Identity: ...         # model, firmware, serial, clock
    def blank_reason(self, block: Block) -> str | None: ...    # refuse this read, and why
    def identity_warnings(self, block: Block) -> list[str]: ...
    def open_device(self, transport, log: SessionLog) -> Device: ...
    def simulator(self, state_file: Path | None = None) -> Transport: ...
```

`identity_warnings` is a soft check after every read. For EG4-LL it warns
when the model text does not start with `ZTR-`, which is the model prefix
on the one pack captured so far. It warns and never refuses, since other
EG4-LL variants may report something else.

### SettingTable

`settings.py` keeps `Setting` as it is today and adds:

```python
@dataclass(frozen=True)
class LinkedPair:
    a: str
    b: str
    reason: str   # EG4: "register map for 0x52/0x54 not settled"

class SettingTable:
    settings: dict[str, Setting]
    by_register: dict[int, Setting]
    order: list[Rule]
    linked: list[LinkedPair]
    rank: dict[str, int]          # computed from order
    def partner(self, key) -> str | None: ...

    @classmethod
    def build(cls, table, limits, order, overrides, linked, group, label) -> "SettingTable": ...
```

`Rule.describe` gets its labels from the table instead of the global
`SETTINGS`. `ORDER`, `RANK`, `SETTINGS`, `BY_REGISTER` and `LINKED_PAIRS`
stop being module globals in generic code.

### Block

`Block(driver, registers)`. Its length is checked against
`driver.block_count`, and its register addresses count from
`driver.block_start`. `model`, `firmware`, `serial` and `clock` go through
`driver.identity`, and `check_not_blank` uses `driver.blank_reason`.
`threshold_registers`, `unknown_registers` and `with_raws` use
`driver.is_writable` and `driver.table`. Two blocks are equal only if
their drivers and their registers are equal.

### Device

```python
class Device(Protocol):
    log: SessionLog
    def read_block(self) -> Block: ...
    def read_live(self, retries: int = ...) -> Live: ...
    def write_setting(self, register: int, raw: int) -> bool: ...  # True = acked
    def write_clock(self, t: datetime) -> bool: ...
    def close(self) -> None: ...
```

`ModbusDevice(driver, transport, log)` is today's `Pack`: FC03 reads with
retries, FC16 single-register writes, every frame logged. It refuses any
register the driver does not call writable. The live decode is a function
the driver hands to it. Errors stay as they are: `NoReply`, `ModbusError`,
and `ModbusException`, which becomes a subclass of a new generic
`DeviceRefused`. The service catches `DeviceRefused`, so a non-Modbus
device can raise it too.

### Live

`Live` holds the fields every pack has:

- Pack voltage and current, SOC and SOH.
- Cells and balancing cells.
- Labelled temperatures, and the temperature the summary shows.
- Remaining, full and design capacity, and cycles.
- Status text, warnings, protections and errors as lists of strings.
- `charge_allowed` / `discharge_allowed` (each may be None).
- `extra: list[tuple[str, str]]` for driver-only details.

The Live tab and `summary_lines` work only from these fields.

## Cross-driver safety

These are new refusals:

- `make_plan(base, targets)` uses `base.driver`. `targets_from_block`,
  `diff_blocks` and `identity_warnings` refuse two blocks from different
  drivers ("this backup is from a <label>, the connected pack is a
  <label>").
- `apply_plan` refuses if the device's driver is not the plan's driver.
- The GUI only offers profiles from the connected driver. When it opens a
  backup from another driver, it shows the refusal and does not add the
  backup as a comparison source.

## Backups

The format stays `eg4tool-backup`, with `format_version` 2, which adds
`"bms": "<driver name>"`. The loader also reads version 1, with no `bms`
field, as `eg4-ll`. A file naming an unknown driver is refused. The
start-register and count checks use the named driver.

## Choosing the driver

- **CLI:** a common option `--bms {eg4-ll}`, default `eg4-ll`. `keys`,
  `--profile` choices and `set` key lookup come from the chosen driver.
  `--simulate` uses `driver.simulator()`.
- **GUI:** a "BMS" combo in the connect bar, before the port, showing
  `driver.label`. It is shown even with one entry, so it is clear what
  the tool thinks it is talking to. It is locked while connected. The
  Settings tab rebuilds its pages from `driver.pages` on connect. The
  Clock tab is hidden when `supports_clock` is false.

## Errors and messages

Generic code stops mentioning EG4, RS485 or the right-most port. Those
texts come from `driver.blank_reason` and `driver.connection_help`, so
the EG4-LL messages read exactly as they do now.

## Testing

1. **The existing 62 tests.** Only their imports change (for example
   `eg4tool.settings.SETTINGS` becomes `eg4_ll.DRIVER.table.settings`).
   No assertion changes.
2. **A toy driver (`tests/toy_driver.py`).** It is never registered in the
   shipped app. It has:
   - A settings block at 0x100 ×8, with 5 settings and one unknown
     register.
   - A three-setting order chain, one linked pair, one °C setting, no
     clock and one page.
   - `FakeModbusDevice` as its simulator.

   Tests on it cover planning order, apply with read-back (CONFIRMED and
   NOT_APPLIED), backup round-trip with `"bms": "toy"`, and GUI connect
   and render with the toy driver injected into the registry.
3. **Cross-driver refusals:** a plan, diff, restore, or a backup load
   into the GUI across EG4-LL and toy.
4. **Old backups:** a version 1 file loads as `eg4-ll`.
5. **Import boundary:** no module outside `drivers/` imports
   `eg4tool.drivers.eg4_ll` (an AST check). The GUI and CLI reach drivers
   only through the registry.
6. **The vendored table:** its sha256 is pinned to the upstream file.
7. **Lint:** `ruff` as today; CI on all four platforms stays green.

## Order of work

1. `SettingTable`, `LinkedPair`, and moving the EG4 table build into the
   driver.
2. `Driver`, the registry, and a driver-bound `Block`.
3. `Device` / `ModbusDevice`, `Live`, and the generic simulator.
4. Planner, service and backup take the driver from the block (backup v2).
5. CLI `--bms`, then GUI selector and pages from the driver.
6. Toy driver tests, boundary test, README section "Adding a BMS".

Each step leaves the full test suite passing.
