"""Desktop app (PySide6). Same engine as the CLI: every write goes through
read -> diff -> LIMITS/ORDER check -> one confirmation -> ordered writes ->
read-back after each write.

All pack I/O runs on one worker thread, so reads and writes never interleave
and the window never freezes on a slow or silent port.
"""

from __future__ import annotations

import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__, backup, controls_view, drivers, qtcommon, settings_view
from .block import Block
from .device import BROADCAST_WARNING, ModbusDevice
from .find import scan
from .live import Live, format_temp
from .planner import Plan, identity_warnings, make_plan
from .qtcommon import APP_NAME, BAD, GOOD, WARN, error_text
from .service import CONFIRMED, NOT_APPLIED, UNVERIFIED, ApplyResult, apply_plan, set_clock
from .session import SessionLog, default_log_path
from .transport import NoReply, list_ports

SIM_PORT = "Simulated pack (no hardware)"


# --- worker ----------------------------------------------------------------

class Worker(QObject):
    """Runs pack jobs one at a time off the GUI thread."""

    finished = Signal(object, object, object)  # callback, result, exception
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._pending = 0
        self._closed = False
        self.finished.connect(self._deliver)

    @property
    def busy(self) -> bool:
        return self._pending > 0

    def submit(self, fn: Callable, done: Callable[[object, Exception | None], None]) -> None:
        if self._closed:
            return
        self._pending += 1
        if self._pending == 1:
            self.busy_changed.emit(True)

        def run():
            try:
                self.finished.emit(done, fn(), None)
            except Exception as e:  # noqa: BLE001 - delivered to the GUI thread
                e.__traceback_text__ = traceback.format_exc()
                self.finished.emit(done, None, e)

        self._pool.submit(run)

    def _deliver(self, done, result, error):
        self._pending -= 1
        if self._pending == 0:
            self.busy_changed.emit(False)
        done(result, error)

    def shutdown(self):
        self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)


# --- small widgets ---------------------------------------------------------

def item(text: str, align=Qt.AlignmentFlag.AlignLeft, editable=False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    flags = it.flags() & ~Qt.ItemFlag.ItemIsEditable
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    it.setFlags(flags)
    return it


RIGHT = Qt.AlignmentFlag.AlignRight


class Stat(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        t = QLabel(title)
        t.setStyleSheet("color: palette(placeholder-text); font-size: 11px;")
        self.value = QLabel("—")
        f = self.value.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        self.value.setFont(f)
        lay.addWidget(t)
        lay.addWidget(self.value)

    def set(self, text: str, color: QColor | None = None):
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color.name()};" if color else "")


# --- live tab --------------------------------------------------------------

class LiveTab(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.voltage, self.current, self.soc, self.soh = Stat("Pack voltage"), Stat("Current"), Stat("SOC"), Stat("SOH")
        self.delta, self.temp = Stat("Cell delta"), Stat("Max temp")
        for w in (self.voltage, self.current, self.soc, self.soh, self.delta, self.temp):
            row.addWidget(w)
        lay.addLayout(row)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)
        self.alarms = QLabel()
        self.alarms.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.alarms)

        cells = QGroupBox("Cells")
        self.cell_grid = QGridLayout(cells)
        self.cell_labels: list[QLabel] = []
        for i in range(16):
            lbl = QLabel("—")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFrameShape(QFrame.Shape.StyledPanel)
            lbl.setMinimumHeight(44)
            self.cell_labels.append(lbl)
            self.cell_grid.addWidget(lbl, i // 8, i % 8)
        lay.addWidget(cells)

        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.details)
        self.temps = QLabel()
        self.temps.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.temps)
        lay.addStretch(1)
        self.updated = QLabel("Not connected.")
        self.updated.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.updated)

    def show_realtime(self, rt: Live, fahrenheit: bool):
        def t(c: float) -> str:
            return format_temp(c, fahrenheit)

        self.voltage.set(f"{rt.pack_voltage:.2f} V")
        self.current.set(f"{rt.current:+.2f} A")
        self.soc.set(f"{rt.soc} %")
        self.soh.set(f"{rt.soh} %")
        self.delta.set(f"{rt.cell_delta * 1000:.0f} mV", WARN if rt.cell_delta > 0.05 else None)
        self.temp.set(t(rt.max_temp) if rt.max_temp is not None else "—")

        def tags(items: list[str], color: QColor, none: str) -> str:
            if not items:
                return f"<span style='color:{GOOD.name()}'>{none}</span>"
            return ", ".join(f"<b style='color:{color.name()}'>{x}</b>" for x in items)

        allowed = ""
        if rt.charge_allowed is not None and rt.discharge_allowed is not None:
            charge = "allowed" if rt.charge_allowed else f"<b style='color:{BAD.name()}'>BLOCKED</b>"
            discharge = "allowed" if rt.discharge_allowed else f"<b style='color:{BAD.name()}'>BLOCKED</b>"
            allowed = f" &nbsp;·&nbsp; charge {charge}, discharge {discharge}"
        self.status.setText(f"<b>Status:</b> {rt.status_text}{allowed}")
        self.alarms.setText(
            f"<b>Protection:</b> {tags(rt.protections, BAD, 'none')} &nbsp;·&nbsp; "
            f"<b>Warnings:</b> {tags(rt.warnings, WARN, 'none')} &nbsp;·&nbsp; "
            f"<b>Errors:</b> {tags(rt.errors, BAD, 'none')}"
        )
        balancing = set(rt.balancing_cells)
        for i, lbl in enumerate(self.cell_labels):
            if i >= len(rt.cells):
                lbl.setText("—")
                lbl.setStyleSheet("")
                continue
            v = rt.cells[i]
            mark = " ⇄" if i + 1 in balancing else ""
            lbl.setText(f"<small>Cell {i + 1}{mark}</small><br><b>{v:.3f} V</b>")
            if v == rt.cell_max and rt.cell_delta > 0:
                lbl.setStyleSheet(f"border: 2px solid {WARN.name()};")
            elif v == rt.cell_min and rt.cell_delta > 0:
                lbl.setStyleSheet(f"border: 2px solid {QColor(56, 132, 255).name()};")
            else:
                lbl.setStyleSheet("")
        extra = "".join(f"<br><b>{label}:</b> {text}" for label, text in rt.extra)
        if rt.full_capacity_ah is not None:
            design = f" (design {rt.design_capacity_ah:.1f} Ah)" if rt.design_capacity_ah is not None else ""
            cycles = f" &nbsp;·&nbsp; <b>Cycles:</b> {rt.cycles}" if rt.cycles is not None else ""
            self.details.setText(
                f"<b>Capacity:</b> {rt.remaining_ah} Ah remaining of {rt.full_capacity_ah:.1f} Ah full"
                f"{design}{cycles}" + extra
            )
        else:
            self.details.setText(extra[4:])
        self.temps.setText(f"<b>Temperatures:</b> {', '.join(f'{label} {t(v)}' for label, v in rt.temps)}")
        self.updated.setText(f"Updated {datetime.now():%H:%M:%S}. Read-only: nothing here writes to the pack.")


# --- review / write dialog -------------------------------------------------

class ReviewDialog(QDialog):
    def __init__(self, main: "MainWindow", plan: Plan, source_name: str):
        super().__init__(main)
        self.main = main
        self.plan = plan
        self.result: ApplyResult | None = None
        self.writing = False
        self.pre: Path | None = None
        f = main.fahrenheit
        self.setWindowTitle("Review changes")
        self.resize(820, 560)
        lay = QVBoxLayout(self)

        head = QLabel(
            f"<b>Pack:</b> {plan.base.model} &nbsp; <b>Firmware:</b> {plan.base.firmware} &nbsp; "
            f"<b>Source:</b> {source_name}<br>"
            "These values were just read from the pack. Writes go out one setting at a time in the order "
            "below, and the whole parameter block is read back after each one."
        )
        head.setWordWrap(True)
        lay.addWidget(head)

        msgs = []
        for e in plan.errors:
            msgs.append(f"<p style='color:{BAD.name()}'><b>Refused:</b> {e}</p>")
        for w in plan.warnings:
            msgs.append(f"<p style='color:{WARN.name()}'><b>Warning:</b> {w}</p>")
        if main.pack and main.pack.address == main.pack.driver.broadcast_address and plan.ok and not plan.empty:
            msgs.append(f"<p style='color:{BAD.name()}'><b>Warning:</b> {BROADCAST_WARNING}</p>")
        for n in plan.notes:
            msgs.append(f"<p><b>Note:</b> {n}</p>")
        if msgs:
            m = QLabel("".join(msgs))
            m.setWordWrap(True)
            m.setTextFormat(Qt.TextFormat.RichText)
            lay.addWidget(m)

        rows = plan.changes if plan.ok else plan.proposed
        self.table = QTableWidget(len(rows), 5)
        self.table.setHorizontalHeaderLabels(["#", "Reg", "Setting", "Now → new", "Result"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for i, c in enumerate(rows):
            s = c.setting
            self.table.setItem(i, 0, item(str(i + 1) if plan.ok else "", RIGHT))
            self.table.setItem(i, 1, item(f"0x{c.register:02X}"))
            self.table.setItem(i, 2, item(s.label))
            self.table.setItem(i, 3, item(f"{s.format(c.old, f)}  →  {s.format(c.new, f)}"))
            self.table.setItem(i, 4, item(""))
        lay.addWidget(self.table, 1)

        self.frames = QPlainTextEdit()
        self.frames.setReadOnly(True)
        self.frames.setFont(QFont("Menlo" if sys.platform == "darwin" else "Consolas" if os.name == "nt" else "Monospace"))
        self.frames.setPlainText(
            "\n".join(f"{fr.hex(' ').upper()}   {c.setting.label}" for c, fr in zip(plan.changes, plan.frames(main.pack.address if main.pack else None, main.pack.framing if main.pack else None)))
        )
        self.frames.setVisible(False)
        self.frames.setMaximumHeight(140)
        lay.addWidget(self.frames)

        self.outcome = QLabel()
        self.outcome.setWordWrap(True)
        self.outcome.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.outcome)

        self.ack = QCheckBox(f"I have checked these {len(plan.changes)} change(s) and want to write them to this pack")
        lay.addWidget(self.ack)
        self.unsafe_confirm: QLineEdit | None = None
        if plan.unsafe:
            lines = "".join(f"<p><b>{c.describe(f)}</b><br>{c.setting.unsafe}</p>" for c in plan.unsafe)
            u = QLabel(f"<p><b>Unsafe settings in this write</b></p>{lines}")
            u.setWordWrap(True)
            u.setTextFormat(Qt.TextFormat.RichText)
            u.setStyleSheet(f"color: {BAD.name()};")
            lay.addWidget(u)
            self.unsafe_confirm = QLineEdit()
            self.unsafe_confirm.setPlaceholderText("type unsafe to confirm")
            lay.addWidget(self.unsafe_confirm)

        buttons = QDialogButtonBox()
        self.dry_btn = buttons.addButton("Dry run: show frames", QDialogButtonBox.ButtonRole.ActionRole)
        self.write_btn = buttons.addButton(f"Write {len(plan.changes)} setting(s)", QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        lay.addWidget(buttons)

        self.write_btn.setEnabled(False)
        self.ack.toggled.connect(self._update_write)
        if self.unsafe_confirm is not None:
            self.unsafe_confirm.textChanged.connect(self._update_write)
        self.ack.setEnabled(plan.ok and not plan.empty)
        self.dry_btn.setEnabled(plan.ok and not plan.empty)
        self.dry_btn.clicked.connect(self.dry_run)
        self.write_btn.clicked.connect(self.write)
        self.close_btn.clicked.connect(self.reject)
        if not plan.ok:
            self.outcome.setText(
                f"<b style='color:{BAD.name()}'>Refused: nothing can be written until the problems above are "
                "fixed.</b> The table lists the requested changes."
            )
        elif plan.empty:
            self.outcome.setText("The pack already has these values. Nothing to write.")

    def _update_write(self, *_):
        typed = self.unsafe_confirm is None or self.unsafe_confirm.text().strip().lower() == "unsafe"
        self.write_btn.setEnabled(self.ack.isChecked() and typed and self.plan.ok and not self.plan.empty)

    def dry_run(self):
        self.frames.setVisible(True)
        for line in self.frames.toPlainText().splitlines():
            self.main.log.info(f"dry run frame {line}")
        self.outcome.setText("Dry run: these are the frames that would be sent. Nothing was written.")

    def reject(self):
        if not self.writing:  # Esc or the close box must not hide a write in progress
            super().reject()

    def write(self):
        self.writing = True
        self.write_btn.setEnabled(False)
        self.ack.setEnabled(False)
        if self.unsafe_confirm is not None:
            self.unsafe_confirm.setEnabled(False)
        self.dry_btn.setEnabled(False)
        self.close_btn.setEnabled(False)
        self.outcome.setText("Writing… each write is confirmed by reading the block back.")
        pack, plan, log = self.main.pack, self.plan, self.main.log
        log.info("confirmed by user")
        pre = log.before_path()

        self.pre = pre

        def job():
            if pre:
                backup.save(pre, plan.base, port=self.main.port_name)
                log.info(f"pre-write backup saved to {pre}")
            return apply_plan(pack, plan)

        self.main.worker.submit(job, self.on_written)

    def on_written(self, res: ApplyResult | None, err: Exception | None):
        self.writing = False
        self.close_btn.setEnabled(True)
        self.close_btn.setText("Close")
        f = self.main.fahrenheit
        if err is not None:
            self.outcome.setText(f"<b style='color:{BAD.name()}'>Write failed:</b> {error_text(err, self.main.driver)}")
            self.main.after_write(None)
            return
        self.result = res
        for i, r in enumerate(res.results):
            text = r.status
            if r.status == NOT_APPLIED and r.reported is not None:
                text += f" (pack kept {r.change.setting.format(r.reported, f)})"
            if r.status == UNVERIFIED:
                text += " (sent; read-back failed)"
            if r.acked is False:
                text += " [no ack]"
            it = item(text)
            it.setForeground(QBrush(GOOD if r.status == CONFIRMED else BAD if r.status in (NOT_APPLIED, UNVERIFIED) else WARN))
            self.table.setItem(i, 4, it)
        n_ok = sum(r.status == CONFIRMED for r in res.results)
        if res.ok:
            self.outcome.setText(
                f"<b style='color:{GOOD.name()}'>All {n_ok} write(s) confirmed</b> by reading the block back."
                f"<br>The settings from before the write are saved in {self.pre}."
            )
        else:
            extra = "".join(f"<br>Unexpected: {u}" for u in res.unexpected)
            extra += "".join(f"<br><b style='color:{BAD.name()}'>Warning:</b> {w}" for w in res.warnings)
            self.outcome.setText(
                f"<b style='color:{BAD.name()}'>Stopped:</b> {res.stopped}. {n_ok} of {len(res.results)} confirmed."
                f"{extra}<br>Every write that was confirmed left the block valid. Read the pack and check the "
                f"Settings tab before using it. See the Log tab. The settings from before the write are saved "
                f"in {self.pre}."
            )
        self.main.after_write(res.final)


# --- clock tab -------------------------------------------------------------

class ClockTab(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        g = QGridLayout()
        self.bms = QLabel("—")
        self.local = QLabel("—")
        for lbl in (self.bms, self.local):
            f = lbl.font()
            f.setPointSize(f.pointSize() + 4)
            lbl.setFont(f)
        g.addWidget(QLabel("BMS clock (at last read):"), 0, 0)
        g.addWidget(self.bms, 0, 1)
        g.addWidget(QLabel("This computer:"), 1, 0)
        g.addWidget(self.local, 1, 1)
        lay.addLayout(g)
        self.set_btn = QPushButton("Set BMS clock to this computer's time…")
        self.dry = QCheckBox("Dry run (show the frame only)")
        row = QHBoxLayout()
        row.addWidget(self.set_btn)
        row.addWidget(self.dry)
        row.addStretch(1)
        lay.addLayout(row)
        self.note = QLabel()  # text set by MainWindow.driver_changed
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.note)
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.result)
        lay.addStretch(1)
        self.set_btn.clicked.connect(self.set_clock)
        t = QTimer(self)
        t.timeout.connect(lambda: self.local.setText(f"{datetime.now():%Y-%m-%d %H:%M:%S}"))
        t.start(500)

    def show_block(self, block: Block | None):
        has_clock = block is not None and block.driver.supports_clock
        if not has_clock:
            self.bms.setText("—")
        elif block.clock:
            self.bms.setText(str(block.clock))
        else:
            raw = block.clock_raw
            self.bms.setText("invalid" if raw is None else f"invalid (0x{raw:08X})")
        self.set_btn.setEnabled(has_clock and not (self.main.pack and self.main.pack.write_refusal))

    def set_clock(self):
        now = datetime.now().replace(microsecond=0)
        frame = self.main.pack.driver.clock_frame(now, self.main.pack.address, self.main.pack.framing).hex(" ").upper()
        if self.dry.isChecked():
            self.main.log.info(f"dry run frame {frame}")
            self.result.setText(f"Dry run. Would send <code>{frame}</code> (time {now}). Nothing was written.")
            return
        question = f"Set the pack's clock to this computer's time ({now})?"
        if self.main.pack.address == self.main.pack.driver.broadcast_address:
            question += f"\n\n{BROADCAST_WARNING}"
        ok = QMessageBox.question(
            self, "Set BMS clock", question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok != QMessageBox.StandardButton.Yes:
            return
        self.main.log.info("clock set confirmed by user")
        pack = self.main.pack
        self.main.worker.submit(lambda: set_clock(pack, datetime.now()), self.on_clock_set)

    def on_clock_set(self, res, err):
        if err is not None:
            self.result.setText(f"<b style='color:{BAD.name()}'>Failed:</b> {error_text(err, self.main.driver)}")
            return
        color = GOOD if res.ok else BAD
        self.result.setText(
            f"<b style='color:{color.name()}'>{'Confirmed' if res.ok else 'NOT applied'}</b>: "
            f"pack now reports {res.after} (was {res.before})."
        )
        self.main.read_settings()


# --- main window -----------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1240, 800)
        self.worker = Worker()
        self.pack: ModbusDevice | None = None
        self.port_name: str | None = None
        self.log: SessionLog | None = None
        self._log_shown = 0

        central = QWidget()
        lay = QVBoxLayout(central)

        note = QLabel()
        self.note = note
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)
        note.setStyleSheet(
            f"background: {QColor(210, 153, 34, 40).name(QColor.NameFormat.HexArgb)}; "
            f"border: 1px solid {WARN.name()}; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(note)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("BMS:"))
        self.bms = QComboBox()
        for d in drivers.DRIVERS.values():
            self.bms.addItem(d.label, d.name)
        self.bms.setCurrentIndex(self.bms.findData(drivers.DEFAULT))
        bar.addWidget(self.bms)
        self.address_label = QLabel("Address:")
        bar.addWidget(self.address_label)
        self.address = QSpinBox()
        self.address.setToolTip("The pack's Modbus address (its DIP switch setting)")
        bar.addWidget(self.address)
        self.find_btn = QPushButton("Find…")
        self.find_btn.setToolTip("Look for packs on the bus and pick the address of one (reads only)")
        bar.addWidget(self.find_btn)
        bar.addWidget(QLabel("Port:"))
        self.ports = QComboBox()
        self.ports.setMinimumWidth(320)
        bar.addWidget(self.ports)
        self.refresh_btn = QPushButton("↻")
        self.refresh_btn.setToolTip("Refresh the port list")
        bar.addWidget(self.refresh_btn)
        self.connect_btn = QPushButton("Connect")
        bar.addWidget(self.connect_btn)
        self.fahrenheit_box = QCheckBox("Show °F")
        bar.addWidget(self.fahrenheit_box)
        bar.addStretch(1)
        self.identity = QLabel("Not connected")
        bar.addWidget(self.identity)
        lay.addLayout(bar)

        self.tabs = QTabWidget()
        self.live = LiveTab()
        self.settings = settings_view.SettingsTab(self, self.driver)
        self.clock = ClockTab(self)
        self.controls = controls_view.ControlsTab(self)
        logw = QWidget()
        ll = QVBoxLayout(logw)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Menlo" if sys.platform == "darwin" else "Consolas" if os.name == "nt" else "Monospace"))
        self.log_view.setMaximumBlockCount(20000)
        ll.addWidget(self.log_view)
        lrow = QHBoxLayout()
        self.log_path = QLabel()
        self.log_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_dir = QPushButton("Open folder")
        open_dir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(qtcommon.data_dir()))))
        lrow.addWidget(self.log_path, 1)
        lrow.addWidget(open_dir)
        ll.addLayout(lrow)

        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(self.settings, "Settings")
        self.tabs.addTab(self.clock, "Clock")
        self.tabs.addTab(self.controls, "Controls")
        self.tabs.addTab(logw, "Log")
        lay.addWidget(self.tabs, 1)
        self.driver_changed()
        self.setCentralWidget(central)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.toggle_connect)
        self.find_btn.clicked.connect(self.find_packs)
        self.fahrenheit_box.toggled.connect(self.units_changed)
        self.settings.review_requested.connect(self.review)
        self.bms.currentIndexChanged.connect(self.driver_changed)
        self.worker.busy_changed.connect(self.on_busy)

        self.live_timer = QTimer(self)
        self.live_timer.timeout.connect(self.poll_live)
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.pump_log)
        self.log_timer.start(300)

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

        self.refresh_ports()
        self.set_connected(False)
        self.statusBar().showMessage("Choose the adapter's port and Connect.")
        self._last_rt: Live | None = None

    # helpers
    @property
    def driver(self):
        return drivers.get(self.bms.currentData())

    def driver_changed(self):
        d = self.driver
        self.note.setText(d.connect_note_html)
        self.note.setVisible(bool(d.connect_note_html))
        self.clock.note.setText(d.clock_note)
        choices = d.address_choices
        self.address_label.setVisible(choices is not None)
        self.address.setVisible(choices is not None)
        self.find_btn.setVisible(d.find_addresses is not None)
        if choices is not None:
            self.address.setRange(choices.start, choices.stop - 1)
            self.address.setValue(d.address)
        self.tabs.setTabVisible(self.tabs.indexOf(self.clock), d.supports_clock)
        self.tabs.setTabVisible(self.tabs.indexOf(self.controls), bool(d.controls()))
        self.controls.set_driver(d)
        if self.settings.driver is not d:
            self.install_settings(d)

    def install_settings(self, driver):
        old = self.settings
        i = self.tabs.indexOf(old)
        was_current = self.tabs.currentWidget() is old
        self.settings = settings_view.SettingsTab(self, driver)
        self.settings.review_requested.connect(self.review)
        self.tabs.removeTab(i)
        self.tabs.insertTab(i, self.settings, "Settings")
        if was_current:
            self.tabs.setCurrentWidget(self.settings)
        old.deleteLater()
        self.settings.set_block(None)

    @property
    def fahrenheit(self) -> bool:
        return self.fahrenheit_box.isChecked()

    def status(self, text: str, error: bool = False):
        self.statusBar().setStyleSheet(f"color: {BAD.name()};" if error else "")
        self.statusBar().showMessage(text, 15000)

    def log_result(self, text: str):
        if self.log:
            self.log.result(text)

    def pump_log(self):
        if self.log is None:
            return
        lines = self.log.lines
        if len(lines) > self._log_shown:
            self.log_view.appendPlainText("\n".join(lines[self._log_shown :]))
            self._log_shown = len(lines)

    def on_busy(self, busy: bool):
        # Disconnect stays available while a read waits on a silent port.
        self.connect_btn.setEnabled((not busy or self.pack is not None) and self.controls.running is None)
        # A result still in flight after a disconnect belongs to that BMS type.
        self.bms.setEnabled(not busy and self.pack is None)
        self.settings.read_btn.setEnabled(not busy and self.pack is not None)
        if busy:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        else:
            QGuiApplication.restoreOverrideCursor()
        self.controls.update_enabled()

    def refresh_ports(self):
        current = self.ports.currentText()
        self.ports.clear()
        try:
            ports = list_ports()
        except Exception:  # noqa: BLE001
            ports = []
        for p in ports:
            self.ports.addItem(p.label, p.device)
        self.ports.addItem(SIM_PORT, SIM_PORT)
        # Keep the selection; otherwise list_ports() puts known USB bridges first.
        self.ports.setCurrentIndex(max(0, self.ports.findText(current)))

    def set_connected(self, on: bool):
        self.connect_btn.setText("Disconnect" if on else "Connect")
        self.bms.setEnabled(not on and not self.worker.busy)
        self.address.setEnabled(not on)
        self.find_btn.setEnabled(not on)
        self.ports.setEnabled(not on)
        self.refresh_btn.setEnabled(not on)
        self.settings.read_btn.setEnabled(on)
        self.clock.set_btn.setEnabled(False)
        self.controls.update_enabled()
        if not on:
            self.identity.setText("Not connected")
            self.identity.setToolTip("")
            self.live_timer.stop()
            # Edits were made against this pack; never offer them to the next one.
            self.settings.staged.clear()
            self.settings.bad.clear()
            self.settings.set_block(None)
            self.clock.show_block(None)

    # connection
    def toggle_connect(self):
        if self.pack is not None:
            self.disconnect_pack()
            return
        device = self.ports.currentData()
        if not device:
            return
        driver = self.driver
        self.log = SessionLog(default_log_path(qtcommon.data_dir()))
        self._log_shown = 0
        self.log_view.clear()
        self.log_path.setText(f"Session log: {self.log.path}")
        try:
            if device == SIM_PORT:
                transport = driver.simulator()
            else:
                from .transport import SerialTransport

                transport = SerialTransport(device, params=driver.serial, framing=driver.framing)
        except Exception as e:  # noqa: BLE001
            self.log.error(f"cannot open {device}: {e}")
            QMessageBox.critical(self, "Cannot open port", f"Cannot open {device}:\n{e}")
            return
        self.port_name = device
        self.log.info(f"BMS type {driver.label}")
        self.log.info(f"port {device}")
        address = self.address.value() if driver.address_choices is not None else None
        if address is not None:
            self.log.info(f"address {address}")
        self.pack = driver.open_device(transport, self.log, address)
        self.set_connected(True)
        self.status(f"Connected to {device}. Reading the pack…")
        self.read_settings()
        self.poll_live()
        self.live_timer.start(2000)

    def find_packs(self):
        device = self.ports.currentData()
        if not device or self.pack is not None or self.worker.busy:
            return
        driver = self.driver
        if driver.find_addresses is None:
            return
        self.log = SessionLog(default_log_path(qtcommon.data_dir()))
        self._log_shown = 0
        self.log_view.clear()
        self.log_path.setText(f"Session log: {self.log.path}")
        try:
            if device == SIM_PORT:
                transport = driver.simulator()
            else:
                from .transport import SerialTransport

                transport = SerialTransport(device, params=driver.serial, framing=driver.framing)
        except Exception as e:  # noqa: BLE001
            self.log.error(f"cannot open {device}: {e}")
            QMessageBox.critical(self, "Cannot open port", f"Cannot open {device}:\n{e}")
            return
        log = self.log
        self.log.info(f"BMS type {driver.label}")
        self.log.info(f"port {device}")
        self.find_btn.setEnabled(False)
        self.status("Looking for packs…")

        def job():
            try:
                return scan(driver, transport, driver.find_addresses, log)
            finally:
                transport.close()

        self.worker.submit(job, self.got_found)

    def got_found(self, found, err: Exception | None):
        self.find_btn.setEnabled(self.pack is None)
        if err is not None:
            QMessageBox.warning(self, "Find failed", error_text(err, self.driver))
            return
        if not found:
            self.status("No pack answered. Check the cable, power and baud rate.", error=True)
            return
        items = [f"address {f.address} — {f.model} — {f.serial}" for f in found]
        choice, ok = QInputDialog.getItem(self, "Find", "Packs that answered:", items, 0, False)
        if ok and choice in items:
            self.address.setValue(found[items.index(choice)].address)
            self.status(f"Address set to {self.address.value()}.")

    def disconnect_pack(self):
        if self.controls.running is not None:
            self.status("A control is running: wait for it to finish, or click Stop.", error=True)
            return
        pack = self.pack
        self.pack = None
        if pack is not None:
            if self.worker.busy:  # close after the job holding the port
                self.worker.submit(pack.close, lambda *_: None)
            else:
                try:
                    pack.close()
                except Exception as e:  # noqa: BLE001 - always finish disconnecting
                    pack.log.warn(f"error while closing the port: {e}")
            pack.log.info("disconnected")
        self.set_connected(False)
        self.status("Disconnected.")

    def control_running_changed(self):
        running = self.controls.running is not None
        self.connect_btn.setEnabled(not running and (not self.worker.busy or self.pack is not None))
        self.controls.update_enabled()

    def read_settings(self):
        if self.pack is None:
            return
        pack = self.pack
        self.worker.submit(pack.read_block, self.got_block)

    def got_block(self, block: Block | None, err: Exception | None):
        if self.pack is None:  # disconnected while the read was waiting
            return
        if err is not None:
            self.pack.log.error(f"settings read failed: {err}")
            self.settings.set_block(None)
            self.clock.show_block(None)
            self.identity.setText("No valid read")
            self.identity.setToolTip("")
            QMessageBox.warning(self, "Could not read the pack's settings", error_text(err, self.driver))
            return
        self.settings.set_block(block)
        self.clock.show_block(block)
        warnings = block.identity_warnings()
        for warning in warnings:
            self.log.warn(warning)
        text = f"<b>{block.model}</b> · firmware {block.firmware}"
        if block.driver.serial_is_unique and block.serial:
            text += f" · {block.serial}"
        if warnings:
            text += f" · <b style='color:{WARN.name()}'>check BMS type</b>"
        refusal = self.pack.write_refusal
        if refusal:
            text += f" · <b style='color:{BAD.name()}'>writes off</b>"
            warnings = [*warnings, f"Writes are off: {refusal}"]
        self.identity.setText(text)
        self.identity.setToolTip("\n".join(warnings))
        if not self.live_timer.isActive() and QApplication.activeModalWidget() is None:
            self.poll_live()
            self.live_timer.start(2000)
        self.status(f"Writes are off: {refusal}", error=True) if refusal else self.status("Settings read from the pack.")

    def poll_live(self):
        if self.pack is None or self.worker.busy:
            return
        pack = self.pack
        self.worker.submit(lambda: pack.read_live(retries=1), self.got_live)

    def got_live(self, rt: Live | None, err: Exception | None):
        if self.pack is None:
            return
        if err is not None:
            # Stop polling a silent port: each poll would hold the worker for seconds.
            self.live_timer.stop()
            self.live.updated.setText(f"Live view paused: {err}. Click Read again on the Settings tab to retry.")
            if isinstance(err, NoReply):
                self.status(self.pack.bus_chatter or f"The pack is not answering. {self.pack.driver.no_reply_hint}",
                            error=True)
            return
        block = self.settings.block
        if block is not None:
            rt.extra = [*rt.extra, *block.info_lines()]
        self._last_rt = rt
        self.live.show_realtime(rt, self.fahrenheit)

    def units_changed(self):
        self.settings.refresh()
        if self._last_rt:
            self.live.show_realtime(self._last_rt, self.fahrenheit)

    # writing
    def review(self):
        if self.pack is None or not self.settings.staged:
            return
        if self.pack.write_refusal:
            QMessageBox.warning(self, "Writes are off", self.pack.write_refusal)
            return
        targets = dict(self.settings.staged)
        src = self.settings.current_source()
        # A restore or copy: every staged value comes from the comparison.
        from_source = src is not None and all(src.block.has(k) and src.block.raw(k) == v for k, v in targets.items())
        name = src.name if from_source else "values entered here"
        fahrenheit = self.fahrenheit
        allow_unsafe = self.settings.unsafe_on
        pack = self.pack
        self.status("Reading the pack before planning the writes…")

        def job():
            block = pack.read_block()
            plan = make_plan(block, targets, fahrenheit=fahrenheit, source=src.block if from_source else None,
                            allow_unsafe=allow_unsafe)
            if from_source:
                plan.warnings[:0] = identity_warnings(block, src.block, "the comparison source")
            for w in plan.warnings:
                pack.log.warn(w)
            for e in plan.errors:
                pack.log.error(f"refused: {e}")
            for c in plan.changes:
                pack.log.info(f"planned: 0x{c.register:02X} {c.describe()}")
            return block, plan

        def done(result, err):
            if self.pack is None:  # disconnected while the read was waiting
                return
            if err is not None:
                QMessageBox.warning(self, "Could not read the pack", error_text(err, self.driver))
                return
            block, plan = result
            self.settings.set_block(block)
            self.live_timer.stop()
            ReviewDialog(self, plan, name).exec()
            if self.pack is not None:
                self.live_timer.start(2000)

        self.worker.submit(job, done)

    def after_write(self, final: Block | None):
        if final is not None:
            self.settings.set_block(final)
            self.clock.show_block(final)
        else:
            self.read_settings()

    def closeEvent(self, event):
        self.live_timer.stop()
        self.log_timer.stop()
        self.controls.stop.set()  # a running control goes back to Normal before the port closes
        # Let a write in progress finish its read-back before the port closes.
        self.worker.shutdown()
        if self.pack is not None:
            self.pack.close()
        if self.log:
            self.log.close()
        super().closeEvent(event)


def main() -> int:
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
