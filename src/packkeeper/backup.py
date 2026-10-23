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

from . import __version__, drivers
from .block import Block, BlankBlockError

FORMAT = "eg4tool-backup"
FORMAT_VERSION = 3
READABLE_VERSIONS = (1, 2, 3)  # 1: before drivers, always EG4-LL; 1-2: one area, no "areas"


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
    d = block.driver
    clock = block.clock
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "bms": d.name,
        "tool_version": __version__,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "port": port,
        "pack": {
            "model": block.model,
            "firmware": block.firmware,
            "serial": block.serial,
            "serial_note": "the same on every pack; cannot identify a pack" if block.serial else None,
            "bms_clock": clock.isoformat(sep=" ") if clock else None,
        },
        "areas": [
            {"start": a.start, "stride": a.stride, "registers": list(values)}
            for a, values in zip(d.areas, block.areas)
        ],
        "settings": {
            key: {
                "register": f"0x{s.register:02X}",
                "width": s.width,
                "raw": block.raw(key),
                "value": float(s.value(block.raw(key))),
                "unit": s.display_unit or None,
            }
            for key, s in d.table.items()
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


def _ints(regs, where: str) -> list[int]:
    if not isinstance(regs, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in regs):
        raise BackupError(f"{where}: 'registers' must be a list of integers")
    return regs


def _areas(data: dict, version: int, driver, where: str) -> list[list[int]]:
    layout = driver.areas
    if version < 3:
        area = layout[0]
        if len(layout) != 1 or data.get("start_register") != area.start or data.get("register_count") != area.count:
            raise BackupError(f"{where}: not a {driver.label} settings block (0x{area.start:02X} x{area.count})")
        return [_ints(data.get("registers"), where)]
    saved = data.get("areas")
    if not isinstance(saved, list) or len(saved) != len(layout):
        raise BackupError(f"{where}: 'areas' must list the {driver.label}'s {len(layout)} area(s)")
    out = []
    for a, entry in zip(layout, saved):
        if not isinstance(entry, dict) or (entry.get("start"), entry.get("stride")) != (a.start, a.stride):
            raise BackupError(
                f"{where}: not a {driver.label} block (expected an area at 0x{a.start:02X}, stride {a.stride})"
            )
        out.append(_ints(entry.get("registers"), where))
    return out


def from_dict(data: dict, where: str = "backup") -> Backup:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BackupError(f"{where} is not an packkeeper backup")
    version = data.get("format_version")
    if version not in READABLE_VERSIONS:
        raise BackupError(f"{where}: unsupported format version {version}")
    name = data.get("bms") if version >= 2 else drivers.DEFAULT
    if not isinstance(name, str):
        raise BackupError(f"{where}: 'bms' must name a BMS type")
    try:
        driver = drivers.get(name)
    except drivers.UnknownDriver as e:
        raise BackupError(f"{where}: {e}") from None
    areas = _areas(data, version, driver, where)
    try:
        block = driver.block(*areas)
        block.check_not_blank()
    except (ValueError, BlankBlockError) as e:
        raise BackupError(f"{where}: {e}") from None

    for key, entry in (data.get("settings") or {}).items():
        s = driver.table.get(key)
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
