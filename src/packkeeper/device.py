"""Talking to one device: reads, single-register writes, every frame logged."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from . import modbus
from .block import Block
from .live import Live
from .session import SessionLog
from .transport import NoReply

if TYPE_CHECKING:
    from .drivers.base import Driver

READ_RETRIES = 3


class Transport(Protocol):
    def transact(self, request: bytes) -> bytes: ...
    def close(self) -> None: ...


class PackError(Exception):
    pass


class Device(Protocol):
    driver: "Driver"
    log: SessionLog

    def read_block(self) -> Block: ...
    def read_live(self, retries: int = READ_RETRIES) -> Live: ...
    def write_setting(self, register: int, raw: int) -> bool: ...
    def write_clock(self, t: datetime) -> bool: ...
    def close(self) -> None: ...


class ModbusDevice:
    def __init__(self, driver: "Driver", transport: Transport, log: SessionLog):
        self.driver = driver
        self.transport = transport
        self.log = log
        self.address = driver.address

    def _transact(self, request: bytes) -> bytes:
        self.log.tx(request)
        try:
            reply = self.transport.transact(request)
        except NoReply as e:
            self.log.warn(str(e))
            raise
        except modbus.ModbusError as e:
            self.log.warn(f"bad reply: {e}")
            raise
        noise = getattr(self.transport, "noise", b"")
        if noise:
            self.log.warn(f"discarded {len(noise)} byte(s) of line noise before the reply: {noise.hex(' ').upper()}")
        self.log.rx(reply)
        return reply

    def read(self, start: int, count: int, retries: int = READ_RETRIES) -> list[int]:
        request = modbus.read_request(self.address, start, count)
        last: Exception | None = None
        for _ in range(retries):
            try:
                reply = self._transact(request)  # logs its own failures
            except (NoReply, modbus.ModbusError) as e:
                last = e
                continue
            try:
                return modbus.parse_read_reply(request, reply)
            except modbus.ModbusException:
                raise
            except modbus.ModbusError as e:
                last = e
                self.log.warn(f"bad reply: {e}")
        assert last is not None
        raise last

    def read_block(self) -> Block:
        """The settings block; refuses a read the driver calls blank."""
        d = self.driver
        block = d.block(self.read(d.block_start, d.block_count))
        reason = d.blank_reason(block)
        if reason:
            self.log.error(f"refusing this read: {reason}")
        return block.check_not_blank()

    def read_live(self, retries: int = READ_RETRIES) -> Live:
        regs: list[int] = []
        for start, count in self.driver.live_reads:
            regs += self.read(start, count, retries)
        return self.driver.decode_live(regs)

    def write_setting(self, register: int, raw: int) -> bool:
        """One FC16 single-register write. True if acked, False if no ack.

        The ack proves nothing: callers must re-read the block to confirm.
        """
        if not self.driver.is_writable(register):
            raise PackError(f"refusing to write 0x{register:02X}: not a writable settings register")
        return self._write(register, [raw])

    def write_clock(self, t: datetime) -> bool:
        if not self.driver.supports_clock:
            raise PackError(f"the {self.driver.label} has no clock to set")
        register, values = self.driver.clock_write(t)
        return self._write(register, values)

    def _write(self, register: int, values: list[int]) -> bool:
        request = modbus.write_request(self.address, register, values)
        try:
            modbus.parse_write_reply(request, self._transact(request))
        except NoReply:
            return False
        except modbus.ModbusException:
            raise
        except modbus.ModbusError as e:
            self.log.warn(f"bad ack: {e}")
            return False
        return True

    def close(self) -> None:
        self.transport.close()
