"""Talking to one device: reads, writes, every frame logged."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from . import modbus
from .block import Area, Block
from .errors import DeviceRefused, FrameError
from .live import Live
from .session import SessionLog
from .transport import NoReply

if TYPE_CHECKING:
    from .drivers.base import Driver
    from .settings import Setting

READ_RETRIES = 3
MAX_READ = modbus.MAX_READ  # FC03 limit
BROADCAST_WARNING = (
    "Address 0 is the Modbus broadcast address: every pack on the bus takes this write. "
    "Make sure the link cables are unplugged."
)


class Transport(Protocol):
    def transact(self, request: bytes) -> bytes: ...
    def close(self) -> None: ...


class PackError(Exception):
    pass


class Device(Protocol):
    driver: "Driver"
    log: SessionLog
    address: int
    write_refusal: str | None

    def read_area(self, area, retries: int = READ_RETRIES) -> list[int]: ...
    def read_block(self) -> Block: ...
    def read_live(self, retries: int = READ_RETRIES) -> Live: ...
    def write_setting(self, setting: "Setting", raw: int) -> bool: ...
    def write_clock(self, t: datetime) -> bool: ...
    def close(self) -> None: ...


class ModbusDevice:
    def __init__(self, driver: "Driver", transport: Transport, log: SessionLog, address: int | None = None):
        self.driver = driver
        self.transport = transport
        self.log = log
        if address is None:
            address = driver.address
        elif address != driver.address and (driver.address_choices is None or address not in driver.address_choices):
            raise ValueError(f"the {driver.label} cannot be reached at address {address}")
        self.address = address
        self.framing = driver.new_framing()
        self.write_refusal: str | None = None

    def _transact(self, request: bytes) -> bytes:
        self.log.tx(request)
        try:
            reply = self.transport.transact(request)
        except NoReply as e:
            self.log.warn(str(e))
            raise
        except FrameError as e:
            self.log.warn(f"bad reply: {e}")
            raise
        noise = getattr(self.transport, "noise", b"")
        if noise:
            self.log.warn(f"discarded {len(noise)} byte(s) of line noise before the reply: {noise.hex(' ').upper()}")
        self.log.rx(reply)
        return reply

    def read(self, start: int, count: int, retries: int = READ_RETRIES) -> list[int]:
        request = self.framing.read_request(self.address, start, count)
        last: Exception | None = None
        for _ in range(retries):
            try:
                reply = self._transact(request)  # logs its own failures
            except (NoReply, FrameError) as e:
                last = e
                continue
            try:
                return self.framing.parse_read(request, reply)[:count]
            except DeviceRefused:
                raise
            except FrameError as e:
                last = e
                self.log.warn(f"bad reply: {e}")
        assert last is not None
        raise last

    def read_area(self, area, retries: int = READ_RETRIES) -> list[int]:
        """One area, in reads of at most framing.max_read registers."""
        area = Area(*area)
        out: list[int] = []
        step = self.framing.max_read
        for i in range(0, area.count, step):
            n = min(step, area.count - i)
            got = self.read(area.start + i * area.stride, n, retries)
            if len(got) < n:
                raise FrameError(
                    f"0x{area.start:02X}: the device sent {i + len(got)} registers, expected {area.count}"
                )
            out += got
        return out

    def read_block(self) -> Block:
        """The settings block; refuses a read the driver calls blank."""
        d = self.driver
        try:
            block = d.block(*(self.read_area(a) for a in d.areas))
        except DeviceRefused as e:
            if not d.read_error_hint:
                raise
            self.log.warn(d.read_error_hint)
            raise PackError(f"{e}. {d.read_error_hint}") from e
        reason = d.blank_reason(block)
        if reason:
            self.log.error(f"refusing this read: {reason}")
        block.check_not_blank()
        refusal = d.check_device(self, block)
        if refusal and refusal != self.write_refusal:
            self.log.error(f"writes are off: {refusal}")
        elif self.write_refusal and not refusal:
            self.log.info("writes are allowed again")
        self.write_refusal = refusal
        return block

    def read_live(self, retries: int = READ_RETRIES) -> Live:
        regs: list[int] = []
        for area in self.driver.live_reads:
            regs += self.read_area(area, retries)
        return self.driver.decode_live(regs)

    def write_setting(self, setting: "Setting", raw: int) -> bool:
        """One FC16 write covering the whole setting. True if acked, False if no ack.

        The ack proves nothing: callers must re-read the block to confirm.
        """
        if self.write_refusal:
            raise PackError(f"writes are off: {self.write_refusal}")
        for r in setting.registers:
            if not self.driver.is_writable(r):
                raise PackError(f"refusing to write 0x{r:02X}: not a writable settings register")
        return self._write(setting.register, setting.encode(raw))

    def write_clock(self, t: datetime) -> bool:
        if self.write_refusal:
            raise PackError(f"writes are off: {self.write_refusal}")
        if not self.driver.supports_clock:
            raise PackError(f"the {self.driver.label} has no clock to set")
        register, values = self.driver.clock_write(t)
        return self._write(register, values)

    def _write(self, register: int, values: list[int]) -> bool:
        request = self.framing.write_request(self.address, register, values)
        try:
            self.framing.parse_write(request, self._transact(request))
        except NoReply:
            return False
        except DeviceRefused:
            raise
        except FrameError as e:
            self.log.warn(f"bad ack: {e}")
            return False
        return True

    def close(self) -> None:
        self.transport.close()
