"""Drives the real window offscreen against the fake pack."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from packkeeper import gui  # noqa: E402
from packkeeper.service import CONFIRMED, NOT_APPLIED  # noqa: E402

SHOTS = os.environ.get("PACKKEEPER_SHOTS")


@pytest.fixture
def window(tmp_path, monkeypatch):
    from packkeeper import qtcommon
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
    from packkeeper.drivers.eg4_ll import sim

    if sim_kw:
        orig = sim.FakePack
        sim.FakePack = lambda **kw: orig(**sim_kw, **kw)
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
    from packkeeper.drivers.eg4_ll import sim
    orig = sim.FakePack

    def broken(**kw):
        p = orig(**kw)
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


def test_silent_port_pauses_live_and_keeps_disconnect(window, monkeypatch):
    app, w = window
    from packkeeper.drivers.eg4_ll import sim
    monkeypatch.setattr(gui.QMessageBox, "warning", lambda *a, **k: None)
    orig = sim.FakePack

    def silent(**kw):
        p = orig(**kw)
        p.drop_replies = 10_000
        return p

    sim.FakePack = silent
    try:
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
    finally:
        sim.FakePack = orig
    wait(app, lambda: not w.worker.busy and not w.live_timer.isActive())
    assert "paused" in w.live.updated.text()
    assert w.settings.read_btn.isEnabled() and w.settings.read_btn.text() == "Read pack"
    assert w.connect_btn.isEnabled()
    w.toggle_connect()  # disconnect
    assert w.pack is None


def test_bms_selector_shows_the_driver_and_locks_while_connected(window):
    app, w = window
    assert w.bms.currentData() == "eg4-ll" and w.bms.currentText() == "EG4-LL"
    assert w.settings.driver.name == "eg4-ll"
    connect_sim(app, w)
    assert not w.bms.isEnabled()
    w.toggle_connect()
    assert w.bms.isEnabled()


def test_backup_from_another_bms_is_not_offered(window, monkeypatch, tmp_path):
    app, w = window
    connect_sim(app, w)
    from packkeeper import backup
    from packkeeper.block import Block

    class Other:
        label = "Other BMS"
    foreign = Block.__new__(Block)
    object.__setattr__(foreign, "driver", Other())
    object.__setattr__(foreign, "areas", w.settings.block.areas)
    monkeypatch.setattr(backup, "load", lambda p: backup.Backup(foreign, "now", "x"))
    monkeypatch.setattr(gui.settings_view.QFileDialog, "getOpenFileName", lambda *a, **k: (str(tmp_path / "x.json"), ""))
    errors = []
    monkeypatch.setattr(gui.settings_view.QMessageBox, "critical", lambda *a: errors.append(a[2]))
    n = len(w.settings.sources)
    assert w.settings.open_backup() is False
    assert len(w.settings.sources) == n and "Other BMS" in errors[0]


def test_gui_runs_a_toy_bms(tmp_path, monkeypatch):
    from packkeeper import drivers, qtcommon
    from toy_driver import TOY
    monkeypatch.setitem(drivers.DRIVERS, "toy", TOY)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    try:
        w.tabs.setCurrentWidget(w.settings)
        eg4_tab = w.settings
        w.bms.setCurrentIndex(w.bms.findData("toy"))
        assert w.settings is not eg4_tab and w.tabs.currentWidget() is w.settings
        assert w.note.isHidden()  # the toy has no connect note
        assert w.settings.staged == {}
        assert w.settings.driver is TOY and set(w.settings.fields) == set(TOY.table)
        assert not w.tabs.isTabVisible(w.tabs.indexOf(w.clock))
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
        wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
        assert w.identity.text().startswith("<b>TY</b>")
        assert not w.clock.set_btn.isEnabled()  # the toy has no clock
        assert w.live.soc.value.text() == "80 %"
        edit(w, "low_cut", "2.9")
        wait(app, lambda: "low_cut" in w.settings.staged)
        dlg = open_review(app, w, monkeypatch)
        assert [c.key for c in dlg.plan.changes] == ["low_cut"]
        dlg.ack.setChecked(True)
        dlg.write_btn.click()
        wait(app, lambda: dlg.result is not None)
        assert dlg.result.ok and dlg.result.results[0].status == CONFIRMED
    finally:
        w.close()


def test_backup_from_the_same_bms_is_offered(window, monkeypatch, tmp_path):
    app, w = window
    connect_sim(app, w)
    from packkeeper import backup
    path = tmp_path / "same.json"
    backup.save(path, w.settings.block)
    monkeypatch.setattr(gui.settings_view.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    n = len(w.settings.sources)
    assert w.settings.open_backup() is True
    assert len(w.settings.sources) == n + 1


def test_identity_warning_tags_the_pack(window):
    app, w = window
    from packkeeper.drivers.eg4_ll import sim
    orig = sim.FakePack

    def other_model(**kw):
        p = orig(**kw)
        p.image[0x69] = 0x4142  # "AB" where every EG4-LL says "ZT"
        return p

    sim.FakePack = other_model
    try:
        connect_sim(app, w)
    finally:
        sim.FakePack = orig
    assert "check BMS type" in w.identity.text()
    assert w.identity.toolTip() != ""


def test_bms_stays_locked_until_a_pending_job_after_disconnect_is_done(window, monkeypatch):
    app, w = window
    import threading
    connect_sim(app, w)
    edit(w, "cell_overvoltage_protection", "3.75")
    wait(app, lambda: w.settings.staged)
    gate = threading.Event()
    real_read = w.pack.read_block
    monkeypatch.setattr(w.pack, "read_block", lambda: (gate.wait(5), real_read())[1])
    shown = []
    monkeypatch.setattr(gui.ReviewDialog, "exec", lambda self: shown.append(self))
    w.review()  # its read now waits on the gate
    assert w.worker.busy
    w.toggle_connect()  # disconnect while the review read is pending
    assert w.pack is None and not w.bms.isEnabled()
    gate.set()
    wait(app, lambda: not w.worker.busy)
    assert w.bms.isEnabled()
    assert shown == [] and w.settings.block is None  # the late result was dropped


def test_address_box_only_for_drivers_with_choices(tmp_path, monkeypatch):
    from packkeeper import drivers, qtcommon
    from test_wide import Addressed
    d = Addressed()
    d.name = "addressed"
    monkeypatch.setitem(drivers.DRIVERS, "addressed", d)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    try:
        assert w.address.isHidden()  # EG4-LL
        w.bms.setCurrentIndex(w.bms.findData("addressed"))
        assert not w.address.isHidden() and w.address.value() == 9
        d.refusal = "numbering not confirmed"
        w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
        w.toggle_connect()
        wait(app, lambda: w.settings.block is not None)
        assert not w.address.isEnabled()
        assert "writes off" in w.identity.text() and "numbering not confirmed" in w.identity.toolTip()
        warned = []
        monkeypatch.setattr(gui.QMessageBox, "warning", lambda *a: warned.append(a[2]))
        w.settings.staged["amps"] = 120000
        w.review()
        assert warned and "numbering not confirmed" in warned[0]
    finally:
        w.close()


def test_gui_runs_jk_pb(window, monkeypatch):
    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    assert not w.address.isHidden() and w.address.value() == 1
    assert w.tabs.isTabVisible(w.tabs.indexOf(w.clock))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    assert w.identity.text().startswith("<b>JK_PB2A16S20P</b>") and "writes off" not in w.identity.text()
    assert "Cells:</b> 16" in w.live.details.text()
    w.tabs.setCurrentWidget(w.settings)
    for i in range(w.settings.nav.count()):
        w.settings.nav.setCurrentRow(i)
        app.processEvents()
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    assert [c.key for c in dlg.plan.changes] == ["cell_ov_protection"]
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.ok and dlg.result.results[0].status == CONFIRMED
