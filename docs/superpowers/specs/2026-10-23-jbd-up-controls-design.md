# ECO-WORTHY / JBD-UP controls (piece 2)

Date: 2026-10-24 (draft 2026-10-23). Status: **approved design, written up
for review**. Nothing is built yet beyond `packkeeper listen`.

Three controls for the JBD-UP driver: **restart**, **standby sleep** and a
guided **MOSFET test**. They are built as driver-declared controls that one
generic runner sends, checks and reports on. EG4-LL and JK-PB declare
none.

## Where the frames come from

Every frame here was captured from JBD's stock app (JBD-ES-UP V1.22.35) on
pack 2 (app 13.5, protocol 2.6). No code was decompiled. The listener was on
the eco-charger bridge B's RS485 passthrough, wired in parallel with the
stock app's adapter. The capture is
`~/eco-charger/captures/jbd-controls-20261024-094951.log`, with a mark before
and after each click.

Each control is a single 0x79 write. The pack acked each one with an empty
reply (len 0). The stock app used key 0x11 until it had read the version
block, then 0x26. The pack accepted both.

| Stock app control | Write | Seen in the live data |
|---|---|---|
| Restart | 0x2912 ← `4A 4A` | silent ~1.5 s, then answered |
| Standby sleep | 0x2908 ← `A5 01` | "Standby" for ~2 s; MOSFETs stayed on (0.8 A load). The app then wrote the clock (0x2800). |
| Normal mode | 0x2900 ← `00 00` | normal; the BMS drives the MOSFETs |
| Tooling mode | 0x2900 ← `5A 01` | both MOSFETs off within 1 s, 0 A, "Standby" |
| MOS control (Tooling mode only; sent when a box is ticked) | 0x2902 ← bits in screen order: bit 0 CHG MOS, bit 1 DSG MOS (`00 01`, then `00 03`) | charge on, then both on |
| Dry contact 1 on / off | 0x2904 ← `00 01` / `00 00` | live I/O bit 5 set / clear |
| "Settings" button | one write of 0x2900-0x292B (22 registers), with whatever the page holds, including 0x2908 = `A5 01` | **never copy this** |
| Debug "Reading" | read 0x2900-0x2910: 7 registers, all 0 | |
| History | 0x78 to 0x4000-0x4001 *with* 2 data bytes: `00 00` first, `00 01` next; each reply is a 101-byte record, newest first | |
| WiFi / module pages | read 0x2400-0x2450: 7 registers, all 0 | |
| Unknown | 0x2914 ← `66 88` (key 0x26), sent as the session ended | the pack answered once more ("Standby"); unexplained |

The live I/O word (0x1020) puts discharge at bit 0 and charge at bit 1. The
MOS control register (0x2902) puts charge at bit 0 and discharge at bit 1.

Earlier the same day, the stock app's real-time CSV showed one click (taken
then for standby) turning both MOSFETs off for ~18 s at 0 A. That matches
Tooling mode, not standby.

## Scope

In:

- **Restart.**
- **Standby sleep.**
- **MOSFET test:** Tooling → charge on → both on → Normal, each step
  checked against the live MOSFET bits. The user wants MOSFET switching for
  testing, so the test always ends in Normal mode. A pack is never left in
  Tooling mode.

Out:

- Deep sleep, DV/DP mode, the LEDs and 0x2914: their frames or effects are
  unknown.
- Dry contacts: not needed.
- Holding a MOSFET off.
- RS485 baud and BMS address writing. These packs do not send the address
  (0x1C88), and baud was not chosen.
- Anything like the stock app's bulk "Settings" write.

Unknown and treated as a risk: whether the BMS still enforces its
protections in Tooling mode. That is why the MOSFET test is short,
attended, and always returns to Normal.

## Model

A new generic module, `controls.py`. Drivers only declare data.

- `Step`: one fixed write, plus a check.
  - `label`: e.g. "Tooling mode (both MOSFETs off)".
  - `register`, `values`: the exact captured data. Nothing is computed or
    combined.
  - `check(device) -> Check`: reads the pack and returns confirmed or a
    reason. It may poll until its own deadline (3 s for MOSFET bits, 30 s for
    restart).
- `Control`: `key`, `label`, `summary`, `danger` (shown in the
  confirmation), `typed_word` (or None), `steps`, and `cleanup` steps.
- `Driver.controls() -> tuple[Control, ...]`: empty by default. Also
  `Driver.control_registers`: the only registers `send_control` will write.
- `Device.send_control(step) -> bool` (acked).
  - It refuses while writes are off (`write_refusal`).
  - It refuses a register outside `control_registers`.
  - It uses the same framing and key as settings writes: the key from the
    pack's protocol register, set on every block read.
- `service.run_control(device, control, progress, stop) -> ControlResult`.
  - It reads the block first. If writes are off, nothing is sent.
  - It sends each step, then runs its check, and reports each step:
    confirmed / NOT confirmed / UNVERIFIED (no ack and no read-back) / not
    sent.
  - It stops at the first step that is not confirmed.
  - If any step may have reached the wire (sent, acked or not), any later
    failure, a Stop or Ctrl-C runs `cleanup`, and cleanup's checks are
    reported too.
  - The result is OK only if every step and every cleanup check is
    confirmed.

### JBD controls

| Key | Steps (exact captured data) | Check | Typed word | Cleanup |
|---|---|---|---|---|
| `restart` | 0x2912 ← `4A 4A` | The pack stops answering (at least one no-reply), then answers the version read again within 30 s. If it never goes quiet: UNVERIFIED, "acked, restart not seen". | none | none |
| `standby` | 0x2908 ← `A5 01` | Acked. Reports the live status afterwards ("Standby", or not, under load). | `standby` | none |
| `mosfet-test` | 1. 0x2900 ← `5A 01` (Tooling) | live bits: charge off, discharge off | `test` | 0x2900 ← `00 00` (Normal); check: both on, and 0x2900 reads 0 |
| | 2. 0x2902 ← `00 01` | charge on, discharge off | | |
| | 3. 0x2902 ← `00 03` | both on | | |
| | 4. 0x2900 ← `00 00` (Normal) | both on, and the debug read (0x2900 x7) shows 0x2900 = 0 | | |

The MOSFET test's danger note says the pack's output is cut for a few seconds
in steps 1-2, so an inverter running off the stack loses this pack briefly.
It is best run with little or no load.

## Using it

**GUI: a Controls tab.** It is built like the Clock tab and shown only for
a BMS with controls.

- Each control is a row with its summary, danger note and button. A shared
  **Dry run** box shows the exact frames and sends nothing.
- A click reads the pack first. If writes are off, the control stops there
  with the reason. Otherwise it shows the frames and danger note and asks
  for the typed word where there is one.
- While it runs, each step appears with ✓/✗ and the MOSFET bits read back.
  The MOSFET test has a **Stop** button, which runs cleanup.
- Live polling pauses while a control runs; the control makes its own
  reads. Disconnect is disabled until it finishes. Closing the window
  mid-control runs cleanup first.

**CLI:**

- `packkeeper control --bms jbd-up` lists the controls.
- `packkeeper control --bms jbd-up NAME [--dry-run]` shows the frames,
  asks for the typed word, and prints each step and result.
- Ctrl-C runs cleanup before exiting.
- Exit codes: 0 confirmed, 1 not confirmed, 2 refused or nothing sent.

**Log:** every frame as usual, a RESULT line per step, and a final
"control confirmed" or "NOT confirmed (…)".

## Testing

- **Simulator:** `FakeJbd` models the captured behaviour.
  - Tooling forces both live MOSFET bits off; 0x2902 then sets them;
    Normal gives them back to the BMS (both on); 0x2900 reads back the
    mode.
  - Restart ignores requests for ~1.5 s.
  - Standby shows "Standby" in the live status.
  - It has switches to misbehave: ignore a write, keep the MOSFETs on, never
    come back from a restart.
- **Fixture:** the capture's control frames are saved in
  `tests/jbd_up_control_frames.txt`. With the key set to the one the
  capture used, every frame PackKeeper builds is byte for byte the captured
  one.
- **`send_control`:** refuses while writes are off, refuses a non-control
  register, and a dry run sends nothing.
- **Checks:**
  - Each check reports confirmed or not correctly.
  - A restart that never goes quiet is UNVERIFIED.
  - If the MOSFET test fails at each step in turn, or is stopped or
    interrupted, Normal is still sent and checked.
- **GUI:** the Controls tab shows only for JBD; Stop works; Disconnect is
  locked while a control runs; the dry run sends nothing.
- **CLI:** the typed word is required, Ctrl-C runs cleanup, and the exit
  codes are as above.
- **EG4 and JK:** no controls, no tab, and EG4 output unchanged (the 17
  parity scenarios against d35201d).

## First real-pack run (user present)

1. Dry run each control on pack 2 and compare the frames with the capture.
2. `restart` on pack 2.
3. `mosfet-test` on pack 2, with little or no load.
4. `standby` on pack 2, if wanted.

Pack 1 looks like the stack master (it reports 600 A inverter limits), so it
is not used for these runs.
