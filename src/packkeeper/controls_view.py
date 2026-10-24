"""The Controls tab: a BMS's live controls, sent by service.run_control on the worker thread."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .controls import CONFIRMED, Control, StepResult, control_frames
from .qtcommon import BAD, GOOD, WARN, error_text
from .service import run_control


class ControlsTab(QWidget):
    step_done = Signal(object)  # a StepResult, emitted on the worker thread

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.stop = threading.Event()
        self.running: Control | None = None
        self.buttons: dict[str, QPushButton] = {}
        self.lines: list[str] = []
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Each control sends frames captured from the BMS maker's own app, then reads the pack to confirm "
            "what happened. The pack is read first, and nothing is sent while writes are off."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(intro)
        self.rows = QVBoxLayout()
        lay.addLayout(self.rows)
        row = QHBoxLayout()
        self.dry = QCheckBox("Dry run (show the frames only)")
        self.stop_btn = QPushButton("Stop (back to Normal)")
        self.stop_btn.hide()
        row.addWidget(self.dry)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.steps = QLabel()
        self.steps.setTextFormat(Qt.TextFormat.RichText)
        self.steps.setWordWrap(True)
        self.steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.steps)
        lay.addStretch(1)
        self.stop_btn.clicked.connect(self.on_stop)
        self.step_done.connect(self.show_step)

    def set_driver(self, driver) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.buttons.clear()
        for c in driver.controls():
            box = QFrame()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 4, 0, 4)
            btn = QPushButton(f"{c.label}…")
            btn.clicked.connect(lambda _=False, c=c: self.start(c))
            text = QLabel(c.summary + (f"<br><span style='color:{WARN.name()}'>{c.danger}</span>" if c.danger else ""))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            h.addWidget(btn)
            h.addWidget(text, 1)
            self.rows.addWidget(box)
            self.buttons[c.key] = btn
        self.lines = []
        self.steps.setText("")
        self.update_enabled()

    def update_enabled(self) -> None:
        on = self.main.pack is not None and not self.main.worker.busy and self.running is None
        for b in self.buttons.values():
            b.setEnabled(on)

    def start(self, control: Control) -> None:
        pack = self.main.pack
        if pack is None or self.running is not None:
            return
        if self.dry.isChecked():
            frames = control_frames(pack, control)
            for label, f in frames:
                pack.log.info(f"dry run frame {f.hex(' ').upper()}  {label}")
            listing = "".join(f"<br><code>{f.hex(' ').upper()}</code>  {label}" for label, f in frames)
            self.steps.setText(f"<b>Dry run: {control.label}.</b> Would send:{listing}<br>Nothing was sent.")
            return
        self.steps.setText(f"Reading the pack before {control.label.lower()}…")
        self.main.worker.submit(pack.read_block, lambda _block, err: self.confirm(control, pack, err))

    def confirm(self, control: Control, pack, err) -> None:
        if pack is not self.main.pack:  # disconnected meanwhile
            return
        if err is not None:
            self.steps.setText(f"<b style='color:{BAD.name()}'>Could not read the pack:</b> "
                               f"{error_text(err, pack.driver)}")
            return
        if pack.write_refusal:
            self.steps.setText(f"<b style='color:{BAD.name()}'>Writes are off:</b> {pack.write_refusal}. "
                               "Nothing was sent.")
            return
        frames = "\n".join(f"{f.hex(' ').upper()}  {label}" for label, f in control_frames(pack, control))
        text = f"{control.summary}\n\n" + (f"{control.danger}\n\n" if control.danger else "") + f"Frames:\n{frames}"
        if control.typed_word:
            word, ok = QInputDialog.getText(self, control.label, f"{text}\n\nType '{control.typed_word}' to send it:")
            confirmed = ok and word.strip().lower() == control.typed_word
        else:
            yes = QMessageBox.StandardButton.Yes
            confirmed = QMessageBox.question(
                self, control.label, f"{text}\n\nSend it?", yes | QMessageBox.StandardButton.No) == yes
        if not confirmed:
            self.steps.setText("Not confirmed. Nothing was sent.")
            pack.log.info(f"control {control.key}: not confirmed; nothing sent")
            return
        pack.log.info(f"control {control.key} confirmed by user")
        self.run(control, pack)

    def run(self, control: Control, pack) -> None:
        self.running = control
        self.stop.clear()
        self.lines = [f"<b>{control.label}</b>"]
        self.steps.setText(self.lines[0])
        self.stop_btn.setEnabled(True)
        self.stop_btn.setVisible(bool(control.cleanup))
        self.main.control_running_changed()
        self.main.worker.submit(lambda: run_control(pack, control, self.step_done.emit, self.stop), self.done)

    def show_step(self, r: StepResult) -> None:
        ok = r.status == CONFIRMED
        color = GOOD if ok else BAD
        self.lines.append(f"<span style='color:{color.name()}'>{'✓' if ok else '✗'}</span> "
                          f"{'cleanup: ' if r.cleanup else ''}{r.step.label}: {r.status}, {r.detail}")
        self.steps.setText("<br>".join(self.lines))

    def on_stop(self) -> None:
        self.stop.set()
        self.stop_btn.setEnabled(False)
        self.lines.append("Stopping: back to Normal after the current step…")
        self.steps.setText("<br>".join(self.lines))

    def done(self, res, err) -> None:
        self.running = None
        self.stop_btn.hide()
        if err is not None:
            self.lines.append(f"<b style='color:{BAD.name()}'>Failed:</b> {error_text(err, self.main.driver)}")
        elif res.ok:
            self.lines.append(f"<b style='color:{GOOD.name()}'>Confirmed.</b>")
        else:
            self.lines.append(f"<b style='color:{BAD.name()}'>NOT confirmed:</b> {res.stopped}")
        self.steps.setText("<br>".join(self.lines))
        self.main.control_running_changed()
        self.main.read_settings()
