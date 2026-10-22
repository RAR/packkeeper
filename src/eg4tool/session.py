"""Session log: every frame sent and received (hex) and every result."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import __version__


def default_log_path(directory: Path | str | None = None) -> Path:
    d = Path(directory) if directory else Path.cwd()
    stem = f"eg4tool-{datetime.now():%Y%m%d-%H%M%S}"
    path, n = d / f"{stem}.log", 1
    # Never share a log with another run.
    while path.exists():
        n += 1
        path = d / f"{stem}-{n}.log"
    return path


class SessionLog:
    def __init__(self, path: Path | None = None, echo: Callable[[str], None] | None = None):
        self.path = path
        self.echo = echo
        self.lines: list[str] = []
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self.info(f"eg4tool {__version__} on {sys.platform}, Python {sys.version.split()[0]}")

    def _write(self, kind: str, text: str) -> None:
        line = f"{datetime.now().isoformat(timespec='milliseconds')} {kind:6s} {text}"
        self.lines.append(line)
        if self._fh:
            self._fh.write(line + "\n")
        if self.echo and kind not in ("TX", "RX"):
            self.echo(line)

    def before_path(self) -> Path | None:
        """A new file for the pack's settings as they were before a write."""
        if self.path is None:
            return None
        stem, n = f"{self.path.stem}.before-{datetime.now():%H%M%S}", 1
        path = self.path.with_name(f"{stem}.json")
        while path.exists():  # several writes in one session each keep their own copy
            n += 1
            path = self.path.with_name(f"{stem}-{n}.json")
        return path

    def tx(self, frame: bytes) -> None:
        self._write("TX", frame.hex(" ").upper())

    def rx(self, frame: bytes) -> None:
        self._write("RX", frame.hex(" ").upper())

    def info(self, text: str) -> None:
        self._write("INFO", text)

    def result(self, text: str) -> None:
        self._write("RESULT", text)

    def warn(self, text: str) -> None:
        self._write("WARN", text)

    def error(self, text: str) -> None:
        self._write("ERROR", text)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
