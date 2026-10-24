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

### Restart and standby seen from the pack (2026-10-24)

The user clicked restart and standby in the stock app on pack 2, and both
worked. The app saves no frame log. Its real-time CSV (one row a second)
shows the effect:

- **Restart** (09:15:29 and 09:17:21): the pack stopped answering for
  4-5 s, then reported "Idle" for a second before going back to
  "Discharge".
- **Standby** (09:23:30-48): the pack kept answering, but both MOSFETs were
  off and the current was 0 A for about 18 s. Then both came back on and it
  went back to discharging. Standby cuts the pack's output.

The frames are still unknown. These observations give the read-back
checks: restart means the pack goes silent and then answers again;
standby means the I/O bits (0x1020 bits 0 and 1) both read off while the
pack keeps answering.

### Captured from the stock app (2026-10-24)

Captured on pack 2 (protocol 2.6). The listener was on the eco-charger bridge B's RS485 passthrough, wired in parallel to the stock app's adapter. The capture is
`~/eco-charger/captures/jbd-controls-20261024-094951.log`, with a mark before and after each click. Each control is a single 0x79 write, and the pack acked every one with an empty reply, except the clock write, whose reply echoes the data.
The stock app used key 0x11 until it had read the version block, then 0x26. The pack accepted both.

| Stock app control | Write | Seen in live data |
|---|---|---|
| Restart | 0x2912 ← `4A 4A` | silent ~1.5 s, then answers |
| Standby sleep | 0x2908 ← `A5 01` | "Standby" ~2 s; MOSFETs stayed on (0.8 A load). The app then wrote the clock (0x2800). |
| Normal mode | 0x2900 ← `00 00` | normal |
| Tooling mode | 0x2900 ← `5A 01` | both MOSFETs off within 1 s, 0 A, "Standby" |
| MOS control (in Tooling mode; written when the box is ticked) | 0x2902 ← bits in screen order: bit 0 CHG MOS, bit 1 DSG MOS (`00 01`, then `00 03`) | CHG on, then both on |
| Dry contact 1 on / off | 0x2904 ← `00 01` / `00 00` | live I/O bit 5 set / clear |
| "Settings" button | one write of 0x2900-0x292B (22 registers), with whatever the page holds, including 0x2908 = `A5 01` | **never copy this** |
| Debug "Reading" | read 0x2900-0x2910: 7 registers, all 0 | |
| History | 0x78 to 0x4000-0x4001 *with* 2 data bytes: `00 00` first, `00 01` next; each reply is a 101-byte record, newest first | |
| WiFi / module pages | read 0x2400-0x2450: 7 registers, all 0 | |
| Unknown | 0x2914 ← `66 88` (key 0x26), sent when the session ended | the pack answered once more ("Standby"); unexplained |

The live I/O word (0x1020) puts discharge at bit 0 and charge at bit 1. The MOS control register (0x2902) puts charge at bit 0 and discharge at bit 1.
Still unknown: DV/DP mode, Deep sleep, the other 0x2902 bits (precharge, heater, fan, charge limit), the LEDs, and 0x2914.

## Finding the missing frames without decompiling

The stock app already knows every frame. Capture its traffic instead of
reading its code:

The stock app does not save its frames (checked 2026-10-24). Use either a
second adapter running `listen` (below) or a Windows serial-port monitor on
the stock app's COM port.

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
