"""Regenerate the README screenshots from the simulated packs (no hardware).

    python packaging/screenshots.py            # writes docs/images/*.png

Uses the light colour scheme and a fixed window size so the images stay comparable.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from packkeeper import gui, qtcommon

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
SIZE = (1280, 820)


def wait(app: QApplication, cond, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise TimeoutError("the simulated pack did not answer")


def settle(app: QApplication, seconds: float = 0.4) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def connect(app: QApplication, w: gui.MainWindow, bms: str) -> None:
    if w.pack is not None:
        w.toggle_connect()
        wait(app, lambda: w.pack is None)
    w.bms.setCurrentIndex(w.bms.findData(bms))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    old = w._last_rt
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None and w._last_rt is not old)


def shot(app: QApplication, w: gui.MainWindow, name: str) -> None:
    settle(app)
    path = OUT / name
    w.grab().save(str(path))
    print(path)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="packkeeper-shots-"))
    qtcommon.data_dir = lambda: tmp  # keep session logs out of Documents
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle("Fusion")
    OUT.mkdir(parents=True, exist_ok=True)
    w = gui.MainWindow()
    w.resize(*SIZE)
    w.show()

    # Settings: compared with EG4's shipped values, with two changes staged, one of them
    # breaking an ordering rule (release above warning).
    connect(app, w, "eg4-ll")
    w.tabs.setCurrentWidget(w.settings)
    s = w.settings
    s.compare.setCurrentIndex(1)
    s.on_compare(1)
    s.on_commit("cell_overvoltage_warning", "3.650")
    s.on_commit("cell_overvoltage_release", "3.680")
    s.refresh()
    s.nav.setCurrentRow(s.driver.page_of["cell_overvoltage_warning"])
    shot(app, w, "settings.png")

    # Live: a JK-PB pack's cells, temperatures and run time.
    connect(app, w, "jk-pb")
    w.tabs.setCurrentWidget(w.live)
    shot(app, w, "live.png")

    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
