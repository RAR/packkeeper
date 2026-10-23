"""Backup files: JSON with the raw block (unknowns included) and decoded values.

The raw registers are the source of truth. A file whose decoded values do not
match its raw registers is refused, so a hand-edited value cannot silently be
ignored or half-applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import __version__
from .block import Block, BlankBlockError
from .settings import BLOCK_COUNT, REG_BLOCK, SETTINGS

FORMAT = "eg4tool-backup"
FORMAT_VERSION = 1


class BackupError(Exception):
    pass


@dataclass
class Backup:
    block: Block
    created: str
    tool_version: str
    port: str | None = None

    @property
    def name(self) -> str:
        return f"backup of {self.created}"


def to_dict(block: Block, port: str | None = None) -> dict:
    clock = block.clock
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "tool_version": __version__,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "port": port,
        "pack": {
            "model": block.model,
            "firmware": block.firmware,
            "serial": block.serial,
            "serial_note": "the same on every pack; cannot identify a pack",
            "bms_clock": clock.isoformat(sep=" ") if clock else None,
        },
        "start_register": REG_BLOCK,
        "register_count": BLOCK_COUNT,
        "registers": list(block.registers),
        "settings": {
            key: {
                "register": f"0x{s.register:02X}",
                "raw": block.raw(key),
                "value": float(s.value(block.raw(key))),
                "unit": s.display_unit or None,
            }
            for key, s in SETTINGS.items()
        },
        "unknown_registers": {f"0x{r:02X}": v for r, v in block.unknown_registers().items()},
    }


def save(path: Path, block: Block, port: str | None = None) -> None:
    block.check_not_blank()
    path.write_text(json.dumps(to_dict(block, port), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path: Path) -> Backup:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BackupError(f"cannot read {path}: {e}") from None
    return from_dict(data, str(path))


def from_dict(data: dict, where: str = "backup") -> Backup:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError(f"{where} is not an eg4tool backup")
    if data.get("format_version") != FORMAT_VERSION:
        raise BackupError(f"{where}: unsupported format version {data.get('format_version')}")
    if data.get("start_register") != REG_BLOCK or data.get("register_count") != BLOCK_COUNT:
        raise BackupError(f"{where}: not a 0x{REG_BLOCK:02X} x{BLOCK_COUNT} parameter block")
    regs = data.get("registers")
    if not isinstance(regs, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in regs):
        raise BackupError(f"{where}: 'registers' must be a list of integers")
    try:
        from .drivers.eg4_ll import DRIVER  # transitional: Task 3 reads the driver from the backup

        block = DRIVER.block(regs)
        block.check_not_blank()
    except (ValueError, BlankBlockError) as e:
        raise BackupError(f"{where}: {e}") from None

    for key, entry in (data.get("settings") or {}).items():
        s = SETTINGS.get(key)
        if s is None:
            continue
        raw = block.raw(key)
        if not isinstance(entry, dict) or entry.get("raw") != raw:
            raise BackupError(
                f"{where}: {key} raw value {entry.get('raw') if isinstance(entry, dict) else entry!r} "
                f"does not match register 0x{s.register:02X} ({raw}); the file was edited"
            )
        value = entry.get("value")
        if not isinstance(value, (int, float)) or abs(value - float(s.value(raw))) > 1e-9:
            raise BackupError(
                f"{where}: {key} value {value!r} does not match its raw register ({s.format(raw)}); the file was edited"
            )
    return Backup(
        block=block,
        created=str(data.get("created", "?")),
        tool_version=str(data.get("tool_version", "?")),
        port=data.get("port"),
    )
