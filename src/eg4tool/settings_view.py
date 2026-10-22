"""The Settings tab: pages of threshold matrices, edited in place, with the
pending changes and any problems listed beside them.

Editing only stages values. Nothing reaches the pack until "Review and
write…", which re-reads the pack and goes through the planner and the
confirmation in the review dialog.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import backup, qtcommon
from .block import Block
from .layout import COLUMN_HELP, ORDERED_KEYS, PAGE_OF, PAGES, TEMP, Page
from .planner import Plan, identity_warnings, make_plan, targets_from_block
from .profiles import PROFILES
from .qtcommon import ACCENT, BAD, WARN
from .settings import LINKED_PAIRS, SETTINGS, SettingError

if TYPE_CHECKING:
    from .gui import MainWindow

FIELD_WIDTH = 104
PARTNER = {a: b for a, b in LINKED_PAIRS} | {b: a for a, b in LINKED_PAIRS}

STYLE = f"""
QLineEdit#value {{
    padding: 3px 6px; border: 1px solid palette(mid); border-radius: 4px; background: palette(base);
}}
QLineEdit#value:focus {{ border: 1px solid {ACCENT.name()}; }}
QLineEdit#value:disabled {{ background: palette(window); color: palette(placeholder-text); }}
QLineEdit#value[state="staged"] {{
    border: 2px solid {ACCENT.name()}; background: rgba(56, 132, 255, 40); font-weight: bold;
}}
QLineEdit#value[state="invalid"] {{ border: 2px solid {BAD.name()}; background: rgba(218, 54, 51, 30); }}
QLineEdit#value[state="problem"] {{ border: 2px solid {BAD.name()}; }}
QLineEdit#value[outside="true"] {{ color: {BAD.name()}; }}
QLabel#sectionTitle {{ font-weight: bold; font-size: 14px; padding-top: 6px; }}
QLabel#columnHead {{ color: palette(placeholder-text); font-size: 11px; }}
QLabel#hint {{ color: palette(placeholder-text); font-size: 11px; }}
QLabel#pill {{ border-radius: 8px; padding: 0px 6px; font-size: 11px; font-weight: bold; color: white; }}
QFrame#panel {{ border-left: 1px solid palette(mid); }}
"""


@dataclass
class Source:
    """Something to compare the pack with: a profile, a backup file or a pack read."""

    name: str
    block: Block
    detail: str = ""


def _unit(setting_unit: str | None, fahrenheit: bool) -> str:
    if setting_unit == TEMP:
        return "°F" if fahrenheit else "°C"
    return setting_unit or ""


class ValueEdit(QLineEdit):
    escaped = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(e)


class SettingField(QWidget):
    """One editable value with a one-line note under it."""

    committed = Signal(str, str)  # key, text
    link = Signal(str, str)  # key, "undo" | "ref"

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self._shown = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self.edit = ValueEdit()
        self.edit.setObjectName("value")
        self.edit.setFixedWidth(FIELD_WIDTH)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sub = QLabel()
        self.sub.setObjectName("hint")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.sub.setFixedWidth(FIELD_WIDTH)
        self.sub.setFixedHeight(self.sub.fontMetrics().height() + 2)
        self.sub.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.edit)
        lay.addWidget(self.sub)
        self.edit.editingFinished.connect(self._finish)
        self.edit.escaped.connect(self._revert)
        self.sub.linkActivated.connect(lambda href: self.link.emit(self.key, href))

    def _finish(self):
        text = self.edit.text().strip()
        if text != self._shown:
            self.committed.emit(self.key, text)

    def _revert(self):
        self.edit.setText(self._shown)
        self.edit.clearFocus()

    def show_value(self, text: str, state: str, sub: str, tooltip: str, outside: bool, enabled: bool):
        self._shown = text
        if not (self.edit.hasFocus() and self.edit.isModified()):
            self.edit.setText(text)
        self.edit.setEnabled(enabled)
        self.edit.setToolTip(tooltip)
        self.sub.setText(sub)
        changed = self.edit.property("state") != state or self.edit.property("outside") != outside
        self.edit.setProperty("state", state)
        self.edit.setProperty("outside", outside)
        if changed:
            self.edit.style().unpolish(self.edit)
            self.edit.style().polish(self.edit)


class SettingsPage(QScrollArea):
    """One page: its sections laid out on a single grid so columns line up."""

    def __init__(self, page: Page, fields: dict[str, SettingField]):
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(16, 4, 16, 12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        grid.setColumnMinimumWidth(0, 120)
        self.ncols = max(len(s.columns) for s in page.sections)
        self.range_col = self.ncols + 1
        self.unit_labels: list[tuple[QLabel, str, str | None]] = []
        self.range_labels: list[tuple[QLabel, list[str]]] = []
        line = ValueEdit().sizeHint().height()  # labels line up with the middle of the inputs
        row = 0
        for section in page.sections:
            title = QLabel()
            title.setObjectName("sectionTitle")
            self.unit_labels.append((title, section.title, section.unit))
            grid.addWidget(title, row, 0, 1, self.range_col + 1)
            row += 1
            for c, name in enumerate(section.columns):
                head = QLabel(name)
                head.setObjectName("columnHead")
                head.setAlignment(Qt.AlignmentFlag.AlignRight)
                head.setToolTip(COLUMN_HELP.get(name, ""))
                grid.addWidget(head, row, c + 1)
            head = QLabel("Safe range")
            head.setObjectName("columnHead")
            grid.addWidget(head, row, self.range_col)
            row += 1
            for label, keys in section.rows:
                lbl = QLabel(label)
                lbl.setFixedHeight(line)
                lbl.setToolTip("\n".join(f"{SETTINGS[k].label}: {k} (0x{SETTINGS[k].register:02X})" for k in keys if k))
                grid.addWidget(lbl, row, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                for c, key in enumerate(keys):
                    if key is None:
                        dash = QLabel("—")
                        dash.setObjectName("hint")
                        dash.setFixedHeight(line)
                        dash.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                        grid.addWidget(dash, row, c + 1, Qt.AlignmentFlag.AlignTop)
                        continue
                    grid.addWidget(fields[key], row, c + 1, Qt.AlignmentFlag.AlignTop)
                rng = QLabel()
                rng.setObjectName("hint")
                rng.setFixedHeight(line)
                self.range_labels.append((rng, [k for k in keys if k]))
                grid.addWidget(rng, row, self.range_col, Qt.AlignmentFlag.AlignTop)
                row += 1
            grid.setRowMinimumHeight(row, 10)
            row += 1
        grid.setColumnStretch(self.range_col + 1, 1)
        outer.addLayout(grid)
        if page.note:
            note = QLabel(page.note)
            note.setObjectName("hint")
            note.setWordWrap(True)
            outer.addSpacing(6)
            outer.addWidget(note)
        outer.addStretch(1)
        self.setWidget(body)

    def update_units(self, fahrenheit: bool):
        for label, title, unit in self.unit_labels:
            u = _unit(unit, fahrenheit)
            label.setText(f"{title} <span style='font-weight:normal'>({u})</span>" if u else title)
        for label, keys in self.range_labels:
            parts = []
            for k in keys:
                s = SETTINGS[k]
                text = f"{s.format(s.raw_min, fahrenheit)} – {s.format(s.raw_max, fahrenheit)}"
                if text not in parts:
                    parts.append(text)
            label.setText(" · ".join(parts))


def _pill(text: str, color, tip: str) -> QLabel:
    p = QLabel(text)
    p.setObjectName("pill")
    p.setStyleSheet(f"background: {color.name()};")
    p.setToolTip(tip)
    return p


def _drop(w: QWidget | None) -> None:
    """Remove a widget from its layout now, not at the next event loop pass."""
    if w is not None:
        w.hide()
        w.setParent(None)
        w.deleteLater()


class SettingsTab(QWidget):
    review_requested = Signal()

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        self.block: Block | None = None
        self.read_at: datetime | None = None
        self.sources: list[Source] = [Source(p.short, p.block, p.label) for p in PROFILES.values()]
        self.staged: dict[str, int] = {}
        self.bad: dict[str, tuple[str, str]] = {}  # key -> (text typed, why it was refused)
        self.plan: Plan | None = None
        self.setStyleSheet(STYLE)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.addLayout(self._top_bar())

        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(200)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { padding: 2px; }")
        self.stack = QStackedWidget()
        self.fields: dict[str, SettingField] = {}
        for key in ORDERED_KEYS:
            f = SettingField(key)
            f.committed.connect(self.on_commit)
            f.link.connect(self.on_field_link)
            self.fields[key] = f
        self.pages = [SettingsPage(p, self.fields) for p in PAGES]
        self.nav_widgets: list[tuple[QLabel, QHBoxLayout]] = []
        for page in self.pages:
            self.stack.addWidget(page)
            it = QListWidgetItem()
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 6, 8, 6)
            name = QLabel(page.page.title)
            h.addWidget(name)
            h.addStretch(1)
            self.nav.addItem(it)
            self.nav.setItemWidget(it, w)
            it.setSizeHint(QSize(180, 36))
            self.nav_widgets.append((name, h))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        body.addWidget(self._panel())
        lay.addLayout(body, 1)
        self.refresh()

    # --- layout ------------------------------------------------------------

    def _top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 0, 8, 6)
        self.read_label = QLabel()
        bar.addWidget(self.read_label)
        self.read_btn = QPushButton("Read again")
        self.backup_btn = QPushButton("Save backup…")
        self.copy_btn = QPushButton("Copy to another pack…")
        self.copy_btn.setToolTip("Keep this pack's settings as the comparison, to write them to the next pack you connect.")
        for b in (self.read_btn, self.backup_btn, self.copy_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Compare with:"))
        self.compare = QComboBox()
        self.compare.setMinimumWidth(240)
        self.compare.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        bar.addWidget(self.compare)
        self.read_btn.clicked.connect(self.main.read_settings)
        self.backup_btn.clicked.connect(self.save_backup)
        self.copy_btn.clicked.connect(self.keep_for_copy)
        self.compare.activated.connect(self.on_compare)
        self._compare_index = 0
        self.rebuild_sources(None)
        return bar

    def _panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(310)
        v = QVBoxLayout(panel)
        v.setContentsMargins(14, 4, 12, 0)
        self.panel_title = QLabel()
        self.panel_title.setObjectName("sectionTitle")
        v.addWidget(self.panel_title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        self.panel_body = QLabel()
        self.panel_body.setWordWrap(True)
        self.panel_body.setTextFormat(Qt.TextFormat.RichText)
        self.panel_body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.panel_body.setStyleSheet("background: transparent;")
        self.panel_body.linkActivated.connect(self.on_panel_link)
        scroll.setWidget(self.panel_body)
        v.addWidget(scroll, 1)

        self.ref_box = QFrame()
        self.ref_box.setFrameShape(QFrame.Shape.StyledPanel)
        rb = QVBoxLayout(self.ref_box)
        self.ref_text = QLabel()
        self.ref_text.setWordWrap(True)
        self.ref_text.setTextFormat(Qt.TextFormat.RichText)
        self.stage_btn = QPushButton()
        self.stage_btn.clicked.connect(self.stage_all)
        rb.addWidget(self.ref_text)
        rb.addWidget(self.stage_btn)
        v.addWidget(self.ref_box)

        buttons = QHBoxLayout()
        self.clear_btn = QPushButton("Discard all")
        self.review_btn = QPushButton("Review and write…")
        self.review_btn.setDefault(True)
        f = self.review_btn.font()
        f.setBold(True)
        self.review_btn.setFont(f)
        buttons.addWidget(self.clear_btn)
        buttons.addWidget(self.review_btn, 1)
        v.addLayout(buttons)
        self.clear_btn.clicked.connect(self.clear_staged)
        self.review_btn.clicked.connect(self.review_requested)
        return panel

    # --- state -------------------------------------------------------------

    @property
    def fahrenheit(self) -> bool:
        return self.main.fahrenheit

    def current_source(self) -> Source | None:
        i = self.compare.currentIndex()
        return self.sources[i - 1] if 1 <= i <= len(self.sources) else None

    def rebuild_sources(self, select: int | None):
        self.compare.clear()
        self.compare.addItem("Nothing")
        for s in self.sources:
            self.compare.addItem(s.name)
            if s.detail:
                self.compare.setItemData(self.compare.count() - 1, s.detail, Qt.ItemDataRole.ToolTipRole)
        self.compare.insertSeparator(self.compare.count())
        self.compare.addItem("Open backup file…")
        index = 0 if select is None else select + 1
        self.compare.setCurrentIndex(index)
        self._compare_index = index

    def on_compare(self, index: int):
        if index == self.compare.count() - 1:  # "Open backup file…"
            if not self.open_backup():
                self.compare.setCurrentIndex(self._compare_index)
            return
        self._compare_index = index
        self.refresh()

    def set_block(self, block: Block | None):
        self.block = block
        self.read_at = datetime.now() if block is not None else None
        if block is not None:
            self.staged = {k: v for k, v in self.staged.items() if block.raw(k) != v}
        self.refresh()

    def stage(self, key: str, raw: int):
        """Stage `raw` for `key`; the linked register follows."""
        for k in (key, PARTNER.get(key)):
            if k is None:
                continue
            self.bad.pop(k, None)
            if raw == self.block.raw(k):
                self.staged.pop(k, None)
            else:
                self.staged[k] = raw

    def unstage(self, key: str):
        for k in (key, PARTNER.get(key)):
            if k is not None:
                self.staged.pop(k, None)
                self.bad.pop(k, None)

    def on_commit(self, key: str, text: str):
        if self.block is None:
            return
        s = SETTINGS[key]
        if not text:
            self.unstage(key)
        else:
            try:
                raw = s.parse(text, self.fahrenheit)
                err = s.limit_error(raw)
                if err:
                    raise SettingError(err)
            except SettingError as e:
                self.unstage(key)
                self.bad[key] = (text, str(e))
                self.main.status(str(e), error=True)
            else:
                self.stage(key, raw)
                if key in PARTNER and key in self.staged:
                    self.main.status(
                        f"{SETTINGS[key].label} and {SETTINGS[PARTNER[key]].label} are set together "
                        "(which of 0x52 / 0x54 is which is not settled yet)."
                    )
        self.refresh()

    def on_field_link(self, key: str, action: str):
        src = self.current_source()
        if action == "undo":
            self.unstage(key)
        elif action == "ref" and src is not None and self.block is not None:
            self.stage(key, src.block.raw(key))
        self.refresh()

    def on_panel_link(self, href: str):
        action, _, key = href.partition(":")
        if action == "go":
            self.show_field(key)
        else:
            self.on_field_link(key, action)

    def show_field(self, key: str):
        self.nav.setCurrentRow(PAGE_OF[key])
        self.pages[PAGE_OF[key]].ensureWidgetVisible(self.fields[key])
        self.fields[key].edit.setFocus()
        self.fields[key].edit.selectAll()

    def stage_all(self):
        src = self.current_source()
        if src is None or self.block is None:
            return
        self.bad.clear()
        self.staged = {k: v for k, v in targets_from_block(src.block).items() if v != self.block.raw(k)}
        self.refresh()
        if not self.staged:
            self.main.status("The pack already matches the comparison.")

    def clear_staged(self):
        self.staged.clear()
        self.bad.clear()
        self.refresh()

    # --- rendering ---------------------------------------------------------

    def refresh(self):
        f = self.fahrenheit
        src = self.current_source()
        block = self.block
        self.plan = make_plan(block, self.staged, fahrenheit=f) if block is not None else None
        problems = self.plan.problem_keys if self.plan else set()

        for key, field in self.fields.items():
            s = SETTINGS[key]
            if block is None:
                field.show_value("", "", "", s.label, False, False)
                continue
            pack_raw = block.raw(key)
            ref_raw = src.block.raw(key) if src is not None else None
            tip = [
                f"{s.label}  (0x{s.register:02X}, {key})",
                f"Pack: {s.format(pack_raw, f)}",
                f"Safe range: {s.format(s.raw_min, f)} – {s.format(s.raw_max, f)}, step {s.step}",
            ]
            if ref_raw is not None:
                tip.append(f"{src.name}: {s.format(ref_raw, f)}")
            outside = s.limit_error(pack_raw)
            if outside:
                tip.append("The pack's value is outside the safe range.")
            if key in self.bad:
                text, why = self.bad[key]
                short = "out of range" if "range" in why else "not valid"
                field.show_value(text, "invalid", f"<span style='color:{BAD.name()}'>{short}</span>",
                                 "\n".join([why, *tip]), False, True)
                continue
            if key in self.staged:
                state = "invalid" if key in problems else "staged"
                sub = f"was {s.format(pack_raw, f, unit=False)} · <a href='undo'>undo</a>"
                field.show_value(s.format(self.staged[key], f, unit=False), state, sub, "\n".join(tip), False, True)
                continue
            sub = ""
            if ref_raw is not None and ref_raw != pack_raw:
                sub = (f"<a href='ref' style='color:{WARN.name()}'>ref {s.format(ref_raw, f, unit=False)}</a>")
                tip.append("Click the note under the field to use the comparison value.")
            state = "problem" if key in problems else ""
            field.show_value(s.format(pack_raw, f, unit=False), state, sub, "\n".join(tip), bool(outside), True)

        for page in self.pages:
            page.update_units(f)
        self._render_nav(src, problems)
        self._render_panel(src)
        self._render_top(src)

    def _render_top(self, src: Source | None):
        has = self.block is not None
        if has:
            self.read_label.setText(f"Read from the pack at {self.read_at:%H:%M:%S}")
        else:
            self.read_label.setText("<span style='color:palette(placeholder-text)'>Connect a pack to read its settings.</span>")
        self.read_btn.setEnabled(has and self.main.pack is not None and not self.main.worker.busy)
        self.backup_btn.setEnabled(has)
        self.copy_btn.setEnabled(has)

    def _render_nav(self, src: Source | None, problems: set[str]):
        for page, (_name, h) in zip(PAGES, self.nav_widgets):
            while h.count() > 2:  # name, stretch, then pills
                _drop(h.takeAt(2).widget())
            keys = page.keys
            bad = sum(k in problems or k in self.bad for k in keys)
            staged = sum(k in self.staged for k in keys)
            differs = 0
            if src is not None and self.block is not None:
                differs = sum(src.block.raw(k) != self.staged.get(k, self.block.raw(k)) for k in keys)
            pills = []
            if bad:
                pills.append(_pill("!", BAD, "Has a problem to fix before writing"))
            if staged:
                pills.append(_pill(str(staged), ACCENT, f"{staged} change(s) staged"))
            if differs:
                pills.append(_pill(str(differs), WARN, f"{differs} value(s) differ from the comparison"))
            for p in pills:
                h.addWidget(p)
                p.show()  # a widget added to a visible layout stays hidden otherwise

    def _render_panel(self, src: Source | None):
        f = self.fahrenheit
        text_color = self.palette().color(QPalette.ColorRole.WindowText).name()
        hint_color = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        n = len(self.staged)
        self.panel_title.setText(f"Changes to write ({n})" if n else "Changes to write")
        problems = self.plan.problem_keys if self.plan else set()
        html = []
        if self.block is None:
            html.append(f"<p style='color:{hint_color}'>Nothing to show until a pack is read.</p>")
        elif not self.staged and not self.bad:
            html.append(
                f"<p style='color:{hint_color}'>No changes. Type a new value into any field, or stage the "
                "differences from a comparison.</p>"
            )
        for key in ORDERED_KEYS:
            if key not in self.staged:
                continue
            s = SETTINGS[key]
            mark = f"<b style='color:{BAD.name()}'>!&nbsp;</b>" if key in problems else ""
            html.append(
                f"<p>{mark}<a href='go:{key}' style='color:{text_color}; text-decoration:none'><b>{s.label}</b></a>"
                f"<br>{s.format(self.block.raw(key), f)} → <b>{s.format(self.staged[key], f)}</b>"
                f" &nbsp;<a href='undo:{key}'>undo</a></p>"
            )

        issues = [
            f"<a href='go:{k}' style='color:{BAD.name()}'>{SETTINGS[k].label}</a>: {why}"
            for k, (_t, why) in self.bad.items()
        ]
        if self.plan is not None and self.staged:
            issues += self.plan.errors
        if issues:
            html.append(f"<p style='color:{BAD.name()}'><b>Fix before writing</b></p>")
            html += [f"<p style='color:{BAD.name()}'>• {i}</p>" for i in issues]
        if self.plan is not None and self.plan.warnings:
            html.append(f"<p style='color:{WARN.name()}'><b>Note</b></p>")
            html += [f"<p style='color:{WARN.name()}'>• {w}</p>" for w in self.plan.warnings]
        self.panel_body.setText("".join(html))

        # Comparison box.
        self.ref_box.setVisible(src is not None and self.block is not None)
        if src is not None and self.block is not None:
            diff = [k for k in ORDERED_KEYS if src.block.raw(k) != self.block.raw(k)]
            pending = [k for k in diff if self.staged.get(k) != src.block.raw(k)]
            lines = [f"<b>{src.name}</b>"]
            if not diff:
                lines.append("The pack matches it.")
            else:
                lines.append(f"{len(diff)} setting(s) on the pack differ" +
                             (f", {len(diff) - len(pending)} staged." if len(pending) < len(diff) else "."))
            lines += [f"<span style='color:{WARN.name()}'>{w}</span>"
                      for w in identity_warnings(self.block, src.block, "It")]
            self.ref_text.setText("<br>".join(lines))
            if pending:
                self.stage_btn.setText(f"Stage all {len(pending)} difference(s)")
            else:
                self.stage_btn.setText("All differences staged" if diff else "Nothing to stage")
            self.stage_btn.setEnabled(bool(pending))

        ok = self.block is not None and bool(self.staged) and not self.bad and self.plan is not None and self.plan.ok
        self.review_btn.setEnabled(ok)
        self.review_btn.setToolTip("" if ok or not self.staged else "Fix the problems listed above first.")
        self.clear_btn.setEnabled(bool(self.staged or self.bad))

    # --- files and copy ----------------------------------------------------

    def save_backup(self):
        if self.block is None:
            return
        default = qtcommon.data_dir() / f"eg4-backup-{datetime.now():%Y%m%d-%H%M%S}.json"
        path, _ = QFileDialog.getSaveFileName(self, "Save backup", str(default), "Backup (*.json)")
        if not path:
            return
        try:
            backup.save(Path(path), self.block, port=self.main.port_name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Backup failed", qtcommon.error_text(e))
            return
        self.main.log_result(f"backup saved to {path}")
        self.main.status(f"Backup saved to {path}")

    def open_backup(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open backup", str(qtcommon.data_dir()), "Backup (*.json);;All files (*)"
        )
        if not path:
            return False
        try:
            b = backup.load(Path(path))
        except backup.BackupError as e:
            QMessageBox.critical(self, "Cannot use this file", str(e))
            return False
        self.add_source(Source(Path(path).name, b.block, f"{path}\nsaved {b.created}, {b.block.model} {b.block.firmware}"))
        return True

    def add_source(self, source: Source):
        self.sources.append(source)
        self.rebuild_sources(len(self.sources) - 1)
        self.refresh()

    def keep_for_copy(self):
        if self.block is None:
            return
        self.add_source(Source(f"Pack read at {datetime.now():%H:%M:%S}", self.block,
                               f"{self.block.model} {self.block.firmware}, kept for copying"))
        QMessageBox.information(
            self, "Copy to another pack",
            "This pack's settings are kept as the comparison.\n\n"
            "1. Disconnect and put this pack's link cables and DIP ID back.\n"
            "2. On the target pack: DIP ID 1, link cables unplugged, adapter in its right-most "
            "paralleling port. Connect.\n"
            "3. Here, click \"Stage all differences\", then \"Review and write…\".",
        )
