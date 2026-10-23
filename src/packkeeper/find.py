"""Find: which addresses answer, for BMS types whose address can be chosen. Reads only."""

from __future__ import annotations

from dataclasses import dataclass

from .block import BlankBlockError
from .errors import DeviceRefused, FrameError
from .transport import NoReply


@dataclass(frozen=True)
class Found:
    address: int
    model: str
    serial: str
    note: str = ""


def scan(driver, transport, addresses, log, timeout: float = 0.3) -> list[Found]:
    old = getattr(transport, "timeout", None)
    if old is not None:
        transport.timeout = timeout
    found = []
    try:
        for a in addresses:
            dev = driver.open_device(transport, log, a)
            try:
                dev.read_area(driver.find_probe, retries=1)
            except (NoReply, FrameError, DeviceRefused):
                continue
            try:
                block = dev.read_block()
                found.append(Found(a, block.model, block.serial))
            except (NoReply, FrameError, DeviceRefused, BlankBlockError) as e:
                found.append(Found(a, "?", "?", note=str(e)))
    finally:
        if old is not None:
            transport.timeout = old
    log.result(f"find: {len(found)} answered at {', '.join(str(f.address) for f in found) or 'none'}")
    return found
