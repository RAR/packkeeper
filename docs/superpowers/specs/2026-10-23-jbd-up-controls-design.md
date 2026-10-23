# ECO-WORTHY / JBD-UP controls (piece 2): draft

Date: 2026-10-23. Status: **draft for review**. Only `packkeeper listen`
is built. No control is sent to a pack until the user approves this design
and the frames are captured.
Branch: `jbd-controls`, from `realpack-fixes`.

## What piece 1 promised

Piece 2 was to add:

- live controls: MOSFET on/off, sleep, restart, debug mode and anti-theft
  unlock;
- writing the RS485 baud (0x1C82) and the BMS address (0x1C88), with
  reconnect-and-verify.

## What the real packs and the protocol notes show

Sources: the first real-pack session (two packs, 2026-10-23) and the
user's PROTOCOL.md.

| Item | Known? | Notes |
|---|---|---|
| Restart | yes | 0x2912 ← `4A 4A` |
| RS485 baud | yes | 0x1C82, codes 0 9600, 1 19200, 2 38400, 3 115200. Both packs read 0 (9600). |
| BMS address | **no** | Neither pack sends 0x1C88: their pack block ends at 0x1C86. The DIP switches set the address. |
| Anti-theft unlock | partly | A 6-byte password to 0x243C with "write key/type 3". That frame shape is not documented. Neither pack has anti-theft on (function bits 0x1000). |
| MOSFET on/off | **no** | No register in the notes. |
| Sleep | **no** | No register in the notes. |
| Debug mode | **no** | No register in the notes. |

Pack 1 (protocol 3.5) reports inverter limits of 600 A where pack 2 reports
100 A. That is six packs × 100 A, so pack 1 is probably the stack's master.
Changing anything on it may affect the whole stack's link to the inverter.

## Finding the missing frames without decompiling

The stock app already knows every frame. Capture its traffic instead of
reading its code:

1. Connect the PC running the stock app to one pack as usual.
2. Connect a second USB-RS485 adapter to the same A/B pair, on any computer.
3. Run `packkeeper listen --bms jbd-up --port <second adapter>`. It never
   transmits. It logs and decodes every frame on the line.
4. In the stock app, click each control once, with a note of the time:
   charge MOSFET off/on, discharge MOSFET off/on, sleep, restart, debug mode.
5. Copy the log to the stick. Each write shows up as one decoded line, for
   example `addr 1 write 0x2912 key 35: 4A 4A`.

Each captured frame becomes a test fixture before any code sends it.

`listen` also decodes Modbus RTU, JK's `55 AA EB 90` frames and `A5 5A`
display frames. That shows what else talks on a JK line, such as the
master's polls seen today.

## Proposed design for the controls, once captured

- A driver declares `controls()`: each one has a key, label, register,
  fixed data, a danger note, an optional typed word, and a check that reads
  the result back.
- Controls are sent only through `Device.send_control`. It refuses when
  writes are off and only sends a register the driver lists as a control.
  It never sends a computed value.
- **Dry run** shows the exact frame, as every other write does.
- **Confirmation:** each control shows its frame and note. Controls that
  cut power to the inverter (discharge MOSFET off, sleep) also need the
  action typed: `off` or `sleep`.
- **Verification:**
  - MOSFET: the live I/O bits (0x1020 bits 0 and 1) must show the new state.
  - Restart: the pack stops answering, then answers the version read again
    within 30 s.
  - Sleep: the pack stops answering; the tool says how to wake it.
- **GUI:** a Controls box on the Live tab, enabled only while connected,
  with writes allowed and the settings read.
- **CLI:** `packkeeper control <name> [--dry-run]`.

## RS485 baud

Proposal: leave it out unless the user needs it.

- Changing it on one pack breaks the stack's RS485 link until the
  master, every pack and the inverter match.
- 9600 is what both packs and the stock app use.

If it is wanted, the flow is:

1. Write 0x1C82 at the old baud.
2. Reopen the port at the new baud and read the version block.
3. If the pack answers, the change is confirmed. If not, try the old baud
   and report NOT applied or unknown.

## Questions for the user

1. Is capturing the stock app's frames with a second adapter possible?
   It needs two USB-RS485 adapters, or one adapter plus the stock app on
   another PC.
2. Which controls matter most? MOSFET on/off, sleep, restart, debug.
3. Drop BMS-address writing? These packs do not expose it.
4. Drop baud writing, or keep it behind the unsafe gate?
