"""Bits shared by the Qt windows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor

from . import backup
from .block import BlankBlockError
from .pack import CONNECTION_HELP
from .service import StaleError
from .settings import SettingError
from .transport import NoReply

APP_NAME = "EG4 BMS Tool"

GOOD = QColor(46, 160, 67)
BAD = QColor(218, 54, 51)
WARN = QColor(210, 153, 34)
ACCENT = QColor(56, 132, 255)


def data_dir() -> Path:
    """Where session logs, pre-write backups and saved backups go."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def error_text(e: Exception) -> str:
    if isinstance(e, NoReply):
        return f"{e}\n\n{CONNECTION_HELP}"
    if isinstance(e, (BlankBlockError, StaleError, SettingError, backup.BackupError)):
        return str(e)
    return f"{type(e).__name__}: {e}"
