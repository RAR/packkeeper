"""Talking to one pack: reads, single-register writes, every frame logged."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from . import modbus
from .block import Block, Realtime, pack_clock
from .session import SessionLog
from .settings import BLOCK_COUNT, REG_BLOCK, REG_CLOCK, is_threshold_register
from .transport import NoReply

ADDRESS = 1  # over RS232 the pack answers at address 1 whatever the DIP ID
READ_RETRIES = 3

CONNECTION_HELP = """\
The pack only answers over RS232 when:
  - its DIP ID is set to 1, and
  - no other pack is linked to it: unplug the link cables from its paralleling ports.
Also check:
  - the adapter is plugged into the pack's RIGHT-MOST paralleling port
    (there is no separate service port; EG4's own cable uses this one),
  - the adapter is RS232-level (not TTL),
  - TX and RX: adapter TX -> pack RXD, adapter RX -> pack TXD, plus GND
    (if nothing decodes, swap TX and RX),
  - the right port is selected (115200 8N1)."""


class Transport(Protocol):
    def transact(self, request: bytes) -> bytes: ...
    def close(self) -> None: ...


class PackError(Exception):
    pass


class Pack:
    def __init__(self, transport: Transport, log: SessionLog, address: int = ADDRESS):
        self.transport = transport
        self.log = log
        self.address = address

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

    def read(self, start: int, count: int) -> list[int]:
        request = modbus.read_request(self.address, start, count)
        last: Exception | None = None
        for _ in range(READ_RETRIES):
            try:
                return modbus.parse_read_reply(request, self._transact(request))
            except modbus.ModbusException:
                raise
            except (NoReply, modbus.ModbusError) as e:
                last = e
                if isinstance(e, modbus.ModbusError):
                    self.log.warn(f"bad reply: {e}")
        assert last is not None
        raise last

    def read_block(self) -> Block:
        """The parameter block; refuses an all-zero threshold block."""
        block = Block(tuple(self.read(REG_BLOCK, BLOCK_COUNT)))
        if block.thresholds_blank:
            self.log.error("parameter block has every threshold zero (read over the RS485 bus, not RS232?)")
        return block.check_not_blank()

    def read_realtime(self) -> Realtime:
        regs = self.read(0x00, 0x12) + self.read(0x12, 0x15)
        return Realtime.decode(regs)

    def write_register(self, register: int, raw: int) -> bool:
        """One FC16 single-register write. True if acked, False if no ack.

        The ack proves nothing: callers must re-read the block to confirm.
        """
        if not is_threshold_register(register):
            raise PackError(f"refusing to write 0x{register:02X}: not a threshold register")
        return self._write(register, [raw])

    def write_clock(self, t: datetime) -> bool:
        packed = pack_clock(t)
        return self._write(REG_CLOCK, [packed >> 16, packed & 0xFFFF])

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
