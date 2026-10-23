"""The BMS drivers this tool knows, by name. Generic code reaches drivers only through here."""

from __future__ import annotations

from .base import Driver, Profile
from .eg4_ll import DRIVER as _EG4_LL
from .jk_pb import DRIVER as _JK_PB

DEFAULT = "eg4-ll"
DRIVERS: dict[str, Driver] = {d.name: d for d in (_EG4_LL, _JK_PB)}

__all__ = ["DEFAULT", "DRIVERS", "Driver", "Profile", "UnknownDriver", "get"]


class UnknownDriver(Exception):
    pass


def get(name: str) -> Driver:
    try:
        return DRIVERS[name]
    except KeyError:
        raise UnknownDriver(f"unknown BMS type '{name}' (known: {', '.join(sorted(DRIVERS))})") from None
