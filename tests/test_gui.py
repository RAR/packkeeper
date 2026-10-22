"""Drives the real window offscreen against the fake pack."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from eg4tool import gui  # noqa: E402
from eg4tool.service import CONFIRMED, NOT_APPLIED  # noqa: E402

SHOTS = os.environ.get("EG4TOOL_SHOTS")


@pytest.fixture
def window(tmp_path, monkeypatch):
    from eg4tool import qtcommon
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    yield app, w
    w.close()


def wait(app, cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def connect_sim(app, w, **sim_kw):
    from eg4tool import sim

    if sim_kw:
        orig = sim.FakePack
        sim.FakePack = lambda: orig(**sim_kw)
    try:
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
    finally:
        if sim_kw:
            sim.FakePack = orig
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)


def edit(w, key, text):
    field = w.settings.fields[key]
    field.edit.setText(text)
    field.edit.editingFinished.emit()


def open_review(app, w, monkeypatch):
    dialogs = []
    monkeypatch.setattr(gui.ReviewDialog, "exec", lambda self: (dialogs.append(self), self.show()))
    w.review()
    wait(app, lambda: dialogs)
    return dialogs[0]


def shot(widget, name):
    if SHOTS:
        widget.grab().save(os.path.join(SHOTS, name))


def test_connect_reads_block_and_live(window):
    app, w = window
    connect_sim(app, w)
    assert "ZTR-16S200A" in w.identity.text()
    assert w.live.soc.value.text() == "67 %"
    w.tabs.setCurrentIndex(0)
    shot(w, "live.png")


def test_edit_review_write_confirmed(window, monkeypatch):
    app, w = window
    connect_sim(app, w)
    w.tabs.setCurrentWidget(w.settings)
    edit(w, "charge_overcurrent_1_protection", "150 A")
    wait(app, lambda: "charge_overcurrent_1_protection" in w.settings.staged)
    edit(w, "charge_overtemp_protection", "149F")  # 65 C == warning: must be refused by ORDER
    wait(app, lambda: "charge_overtemp_protection" in w.settings.staged)
    shot(w, "settings-staged.png")

    dlg = open_review(app, w, monkeypatch)
    assert not dlg.plan.ok
    assert not dlg.ack.isEnabled()
    shot(dlg, "review-refused.png")
    dlg.close()

    edit(w, "charge_overtemp_protection", "")
    wait(app, lambda: "charge_overtemp_protection" not in w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    assert dlg.plan.ok and len(dlg.plan.changes) == 1
    assert not dlg.write_btn.isEnabled()
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.ok and dlg.result.results[0].status == CONFIRMED
    shot(dlg, "review-done.png")
    wait(app, lambda: w.settings.block.raw("charge_overcurrent_1_protection") == 15000)
    assert w.settings.staged == {}
    assert any(p.suffix == ".json" for p in w.log.path.parent.iterdir())  # pre-write backup


def test_restore_profile_and_dropped_write(window, monkeypatch):
    app, w = window
    connect_sim(app, w, floors={0x39: 25})
    w.tabs.setCurrentWidget(w.settings)
    edit(w, "balance_delta_voltage", "0.022")
    wait(app, lambda: w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.results[0].status == NOT_APPLIED
    assert "pack kept 0.020 V" in dlg.table.item(0, 4).text()
    shot(dlg, "review-not-applied.png")
    dlg.close()


def test_linked_pair_and_fahrenheit(window):
    app, w = window
    connect_sim(app, w)
    edit(w, "short_circuit_current", "250")
    wait(app, lambda: len(w.settings.staged) == 2)
    assert w.settings.staged["discharge_overcurrent_2_protection"] == 25000
    w.fahrenheit_box.setChecked(True)
    assert w.settings.fields["charge_overtemp_protection"].edit.text() == "158"
    edit(w, "charge_overtemp_protection", "141")
    wait(app, lambda: "charge_overtemp_protection" in w.settings.bad)


def test_compare_with_profile_and_stage(window):
    app, w = window
    connect_sim(app, w)
    w.tabs.setCurrentWidget(w.settings)
    w.settings.compare.setCurrentIndex(1)  # EG4 shipped
    w.settings.on_compare(1)
    assert not w.settings.stage_btn.isEnabled()  # the fake pack ships with the reference block
    shot(w, "settings-compare.png")


def test_restore_over_vendor_reset(window):
    app, w = window
    from eg4tool import sim
    orig = sim.FakePack

    def broken():
        p = orig()
        p.image[0x50], p.image[0x44], p.image[0x41] = 6200, 3900, 4320
        return p

    sim.FakePack = broken
    try:
        connect_sim(app, w)
    finally:
        sim.FakePack = orig
    w.tabs.setCurrentWidget(w.settings)
    w.settings.compare.setCurrentIndex(1)
    w.settings.on_compare(1)
    field = w.settings.fields["cell_overvoltage_protection"]
    assert field.edit.property("outside") is True
    assert "ref 3.800" in field.sub.text()
    assert w.settings.stage_btn.text() == "Stage all 3 difference(s)"
    w.settings.stage_all()
    assert w.settings.staged == {"charge_overcurrent_1_protection": 20500, "cell_overvoltage_protection": 3800,
                                 "pack_undervoltage_protection": 4480}
    assert w.settings.review_btn.isEnabled()
    shot(w, "settings-restore.png")


def test_order_problem_flagged_before_review(window):
    app, w = window
    connect_sim(app, w)
    w.tabs.setCurrentWidget(w.settings)
    edit(w, "cell_overvoltage_protection", "3.65")  # below the 3.70 warning
    f = w.settings.fields
    assert f["cell_overvoltage_protection"].edit.property("state") == "invalid"
    assert f["cell_overvoltage_warning"].edit.property("state") == "problem"
    assert not w.settings.review_btn.isEnabled()
    edit(w, "cell_overvoltage_warning", "3.60")
    assert f["cell_overvoltage_warning"].edit.property("state") == "staged"
    assert w.settings.review_btn.isEnabled()
    shot(w, "settings-edit.png")


def test_ref_link_and_undo(window):
    app, w = window
    connect_sim(app, w)
    edit(w, "charge_overcurrent_1_protection", "150")
    w.settings.on_field_link("charge_overcurrent_1_protection", "undo")
    assert w.settings.staged == {}


def test_every_page_renders(window):
    app, w = window
    connect_sim(app, w)
    w.tabs.setCurrentWidget(w.settings)
    for i in range(w.settings.nav.count()):
        w.settings.nav.setCurrentRow(i)
        app.processEvents()
        shot(w, f"settings-page{i}.png")


def test_disconnect_drops_staged_edits(window):
    app, w = window
    connect_sim(app, w)
    edit(w, "cell_overvoltage_protection", "3.75")
    wait(app, lambda: w.settings.staged)
    w.toggle_connect()  # disconnect
    assert w.settings.staged == {} and not w.settings.review_btn.isEnabled()


def test_linked_pair_revert_unstages_partner(window):
    app, w = window
    connect_sim(app, w)
    edit(w, "short_circuit_current", "250")
    wait(app, lambda: len(w.settings.staged) == 2)
    edit(w, "short_circuit_current", "300")  # back to the pack's value
    wait(app, lambda: w.settings.staged == {})
