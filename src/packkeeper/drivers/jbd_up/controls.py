"""JBD-UP live controls, as JBD's stock app (JBD-ES-UP V1.22.35) sent them on
2026-10-24. Every value is the data the stock app wrote; the controls spec
holds the capture."""

from __future__ import annotations

import time

from ...controls import Check, Control, Step, pause
from ...errors import FrameError
from ...transport import NoReply
from . import registers as R

REG_MODE = 0x2900  # debug mode
REG_MOS = 0x2902  # MOS control, Tooling mode only: bit 0 charge, bit 1 discharge
REG_SLEEP = 0x2908
REG_RESTART = 0x2912
CONTROL_REGISTERS = frozenset({REG_MODE, REG_MOS, REG_SLEEP, REG_RESTART})
NORMAL, TOOLING = 0x0000, 0x5A01
MOS_CHARGE, MOS_BOTH = 0x0001, 0x0003
STANDBY = 0xA501
RESTART = 0x4A4A

MOS_WAIT_S = 3.0  # for the live MOSFET bits to show a step
QUIET_WAIT_S = 5.0  # for a restarting pack to stop answering
BACK_WAIT_S = 30.0  # for it to answer again
SLEEP_WAIT_S = 10.0  # for a pack that fell asleep to answer again
NORMAL_WAIT_S = 10.0  # for both MOSFETs after Normal: charge came on first, discharge a few s later (2026-11-26)
POLL_S = 0.25


def _onoff(charge: bool, discharge: bool) -> str:
    return f"charge {'on' if charge else 'off'}, discharge {'on' if discharge else 'off'}"


def _wait_for_mosfets(device, charge: bool, discharge: bool, seconds: float) -> Check:
    deadline = time.monotonic() + seconds
    while True:
        live = device.read_live(retries=1)
        seen = (live.charge_allowed, live.discharge_allowed)
        if seen == (charge, discharge):
            return Check(True, f"MOSFETs read {_onoff(*seen)}")
        if time.monotonic() >= deadline:
            return Check(False, f"MOSFETs read {_onoff(*seen)}, expected {_onoff(charge, discharge)}")
        pause(POLL_S)


def mosfets(charge: bool, discharge: bool):
    """A check: the live MOSFET bits show this state within MOS_WAIT_S."""

    def check(device, acked: bool) -> Check:
        return _wait_for_mosfets(device, charge, discharge, MOS_WAIT_S)

    return check


def normal_mode(device, acked: bool) -> Check:
    c = _wait_for_mosfets(device, True, True, NORMAL_WAIT_S)
    if not c.ok:
        return c
    mode = device.read_area(R.DEBUG, retries=1)[0]
    if mode != NORMAL:
        return Check(False, f"{c.detail}; debug mode reads 0x{mode:04X}, expected normal (0)")
    return Check(True, f"{c.detail}; debug mode reads normal")


def mode_reads_normal(device, acked: bool) -> Check:
    """Normal mode on its own: the mode reads 0. The BMS drives the MOSFETs again, and a protection may
    hold one off, so their state is only reported, after waiting up to NORMAL_WAIT_S for both."""
    mode = device.read_area(R.DEBUG, retries=1)[0]
    live = device.read_live(retries=1)
    if mode != NORMAL:
        seen = _onoff(live.charge_allowed, live.discharge_allowed)
        return Check(False, f"debug mode reads 0x{mode:04X}, expected normal (0); MOSFETs read {seen}")
    start, waited = time.monotonic(), False
    while not (live.charge_allowed and live.discharge_allowed):
        waited = True
        if time.monotonic() - start >= NORMAL_WAIT_S:
            seen = _onoff(live.charge_allowed, live.discharge_allowed)
            return Check(True, f"debug mode reads normal; MOSFETs still read {seen} after {NORMAL_WAIT_S:.1f} s "
                               "(the BMS may be holding one off)")
        pause(POLL_S)
        live = device.read_live(retries=1)
    after = f" after {time.monotonic() - start:.1f} s" if waited else ""
    return Check(True, f"debug mode reads normal; MOSFETs read {_onoff(True, True)}{after}")


def restarted(device, acked: bool) -> Check:
    """The pack stops answering, then answers the version read again."""
    end = time.monotonic() + QUIET_WAIT_S
    while True:
        try:
            device.read_area(R.VERSION, retries=1)
        except (NoReply, FrameError):
            break
        if time.monotonic() >= end:
            return Check(None, f"{'acked' if acked else 'no ack'}, but the pack never went quiet: restart not seen")
        pause(POLL_S)
    end = time.monotonic() + BACK_WAIT_S
    while time.monotonic() < end:
        pause(POLL_S)
        try:
            device.read_area(R.VERSION, retries=1)
        except (NoReply, FrameError):
            continue
        return Check(True, "the pack went quiet, then answered again")
    return Check(False, f"the pack went quiet and has not answered for {BACK_WAIT_S:.0f} s")


def standby(device, acked: bool) -> Check:
    """Reports the status. The pack stops answering while it sleeps, so wait up to SLEEP_WAIT_S for it
    to answer again. At 0 A it acked first (2026-10-24); under load it went quiet without an ack and
    answered again 2-3 s later (2026-11-26), so going quiet and coming back also confirms it, as for a
    restart."""
    ack = "acked" if acked else "no ack"
    start, quiet = time.monotonic(), False
    while True:
        try:
            status = device.read_live(retries=1).status_text
            break
        except (NoReply, FrameError):
            quiet = True
        if time.monotonic() - start >= SLEEP_WAIT_S:
            return Check(True if acked else None,
                         f"{ack}; the pack went quiet (asleep) and has not answered for {SLEEP_WAIT_S:.0f} s")
        pause(POLL_S)
    if quiet:
        waited = time.monotonic() - start
        return Check(True, f"{ack}; the pack went quiet (asleep), answered again after {waited:.1f} s and reports {status}")
    return Check(True if acked else None, f"{ack}; the pack reports {status}")


NORMAL_STEP = Step("Normal mode (the BMS drives the MOSFETs)", REG_MODE, NORMAL, normal_mode)

CONTROLS = (
    Control(
        "restart", "Restart",
        "Restarts the BMS. It stops answering for a few seconds, then answers again.",
        steps=(Step("Restart", REG_RESTART, RESTART, restarted),),
    ),
    Control(
        "standby", "Standby sleep",
        "Puts the BMS into standby sleep. It stops answering while it sleeps, under load too, "
        "then answers again by itself (after 2-3 s under load).",
        steps=(Step("Standby sleep", REG_SLEEP, STANDBY, standby),),
        danger="The pack read 0 A when it woke, even under load: it may stop supplying power until it wakes.",
        typed_word="standby",
    ),
    Control(
        "mosfet-test", "MOSFET test",
        "Tooling mode (both MOSFETs off), charge on, both on, then back to Normal; "
        "each step is checked in the live data.",
        steps=(
            Step("Tooling mode (both MOSFETs off)", REG_MODE, TOOLING, mosfets(False, False)),
            Step("Charge MOSFET on", REG_MOS, MOS_CHARGE, mosfets(True, False)),
            Step("Both MOSFETs on", REG_MOS, MOS_BOTH, mosfets(True, True)),
            NORMAL_STEP,
        ),
        danger="The pack's output is cut for a few seconds in the first two steps: an inverter running off "
        "the stack loses this pack briefly. Best with little or no load.",
        typed_word="test",
        cleanup=(NORMAL_STEP,),
    ),
    Control(
        "normal", "Normal mode",
        "Takes the BMS out of Tooling mode so it drives its MOSFETs again, e.g. if the stock app left it "
        "there. Harmless if it is already in Normal mode.",
        steps=(Step("Normal mode (the BMS drives the MOSFETs)", REG_MODE, NORMAL, mode_reads_normal),),
    ),
)
