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


def test_jk_pb_at_address_0_warns_about_broadcast(window, monkeypatch):
    from PySide6.QtWidgets import QLabel

    from packkeeper.device import BROADCAST_WARNING

    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    w.address.setValue(0)
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None)
    assert w.pack.address == 0
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    assert any(BROADCAST_WARNING in lbl.text() for lbl in dlg.findChildren(QLabel))
    asked = []
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a: (asked.append(a[2]), gui.QMessageBox.StandardButton.No)[1])
    w.clock.set_clock()
    assert asked and BROADCAST_WARNING in asked[0]
    dlg.reject()


def test_review_at_the_default_address_has_no_broadcast_warning(window, monkeypatch):
    from PySide6.QtWidgets import QLabel

    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None)
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in w.settings.staged)
    dlg = open_review(app, w, monkeypatch)
    assert not any("broadcast" in lbl.text() for lbl in dlg.findChildren(QLabel))
    asked = []
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *a: (asked.append(a[2]), gui.QMessageBox.StandardButton.No)[1])
    w.clock.set_clock()
    assert asked and "broadcast" not in asked[0]
    dlg.reject()


def toy_window(tmp_path, monkeypatch):
    from packkeeper import drivers, qtcommon
    from unsafe_toy_driver import UNSAFE_TOY
    monkeypatch.setitem(drivers.DRIVERS, "unsafe-toy", UNSAFE_TOY)
    monkeypatch.setattr(qtcommon, "data_dir", lambda: tmp_path)
    app = QApplication.instance() or QApplication([])
    w = gui.MainWindow()
    w.show()
    w.bms.setCurrentIndex(w.bms.findData("unsafe-toy"))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    return app, w


def test_unsafe_page_hidden_until_switched_on(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        s = w.settings
        assert not s.unsafe_box.isHidden() and not s.unsafe_box.isChecked()
        unsafe_row = [i for i, p in enumerate(s.driver.pages) if p.unsafe][0]
        assert s.nav.isRowHidden(unsafe_row)
        warned = []
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
        s.unsafe_box.setChecked(True)
        assert warned and not s.nav.isRowHidden(unsafe_row)
        edit(w, "cells", "15")
        wait(app, lambda: "cells" in s.staged)
        s.unsafe_box.setChecked(False)
        assert "cells" not in s.staged and s.nav.isRowHidden(unsafe_row)
    finally:
        w.close()


def test_unsafe_review_needs_typed_unsafe(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: None)
        w.settings.unsafe_box.setChecked(True)
        edit(w, "cells", "15")
        wait(app, lambda: "cells" in w.settings.staged)
        dlg = open_review(app, w, monkeypatch)
        assert [c.key for c in dlg.plan.unsafe] == ["cells"]
        dlg.ack.setChecked(True)
        assert not dlg.write_btn.isEnabled()
        dlg.unsafe_confirm.setText("unsafe")
        assert dlg.write_btn.isEnabled()
        dlg.write_btn.click()
        wait(app, lambda: dlg.result is not None)
        assert dlg.result.ok
    finally:
        w.close()


def test_choice_setting_is_a_dropdown(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        field = w.settings.fields["mode"]
        assert isinstance(field.edit, gui.settings_view.ChoiceEdit)
        assert [field.edit.itemText(i) for i in range(field.edit.count())] == ["charge only", "static"]
        assert field.edit.text() == "charge only"
        field.edit.setText("static")
        field.edit.editingFinished.emit()
        wait(app, lambda: w.settings.staged.get("mode") == 1)
    finally:
        w.close()


def test_bits_setting_commits_hex(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: None)
        w.settings.unsafe_box.setChecked(True)
        assert w.settings.fields["flags"].edit.text() == "0x0001"
        edit(w, "flags", "0x0011")
        wait(app, lambda: w.settings.staged.get("flags") == 0x11)
    finally:
        w.close()


def _stage_all_with_source(app, w):
    s = w.settings
    other = s.block.with_raws({0x302: 20, 0x301: 350})
    s.add_source(gui.settings_view.Source("other", other))
    s.stage_all()
    return s


def test_stage_all_skips_unsafe_when_box_off(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        s = _stage_all_with_source(app, w)
        assert "cells" not in s.staged and "volts" in s.staged
    finally:
        w.close()


def test_stage_all_includes_unsafe_when_box_on(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: None)
        w.settings.unsafe_box.setChecked(True)
        s = _stage_all_with_source(app, w)
        assert "cells" in s.staged and "volts" in s.staged
    finally:
        w.close()


def test_review_refuses_unsafe_when_box_off(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        w.settings.staged["cells"] = 15
        dlg = open_review(app, w, monkeypatch)
        assert not dlg.plan.ok and any("unsafe" in e for e in dlg.plan.errors)
        dlg.ack.setChecked(True)
        assert not dlg.write_btn.isEnabled()
    finally:
        w.close()


def test_gui_runs_jbd_up_with_find(window, monkeypatch):
    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    assert not w.address.isHidden() and w.address.value() == 0 and not w.find_btn.isHidden()
    assert not w.settings.unsafe_box.isHidden()
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    monkeypatch.setattr(gui.QInputDialog, "getItem", lambda *a, **k: (a[3][1], True))  # the second pack found
    w.find_btn.click()
    wait(app, lambda: w.address.value() == 2)
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)
    assert "SIM-PACK-2" in w.identity.text()
    tab = w.settings
    w.tabs.setCurrentWidget(tab)
    for i in range(tab.nav.count()):
        if not tab.nav.isRowHidden(i):
            tab.nav.setCurrentRow(i)
            app.processEvents()
    edit(w, "cell_ov_protection", "3.6")
    wait(app, lambda: "cell_ov_protection" in tab.staged)
    dlg = open_review(app, w, monkeypatch)
    dlg.ack.setChecked(True)
    dlg.write_btn.click()
    wait(app, lambda: dlg.result is not None)
    assert dlg.result.ok


def test_hidden_unsafe_differences_are_not_counted(tmp_path, monkeypatch):
    app, w = toy_window(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(gui.settings_view.QMessageBox, "warning", lambda *a, **k: None)
        s = w.settings
        cells_reg = s.table["cells"].register
        s.add_source(gui.settings_view.Source("other", s.block.with_raws({cells_reg: s.block.raw("cells") - 1})))
        s.refresh()
        assert "Nothing to stage" in s.stage_btn.text() and not s.stage_btn.isEnabled()
        assert "left alone" in s.ref_text.text() and "differ" in s.ref_text.text()
        assert "setting(s) on the pack differ" not in s.ref_text.text()
        s.unsafe_box.setChecked(True)
        assert "1 setting(s) on the pack differ" in s.ref_text.text()
        assert s.stage_btn.isEnabled() and "left alone" not in s.ref_text.text()
    finally:
        w.close()


def test_choice_edit_ignores_wheel_without_focus(app_instance=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    box = gui.settings_view.ChoiceEdit([(0, "a"), (1, "b"), (2, "c")])
    box.show()
    assert box.focusPolicy() == Qt.FocusPolicy.StrongFocus and not box.hasFocus()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    box.wheelEvent(ev)
    assert box.currentText() == "a" and not ev.isAccepted()
    box.close()


def test_a_failed_settings_read_is_written_to_the_log(window, monkeypatch):
    from packkeeper.errors import FrameError

    app, w = window
    connect_sim(app, w)
    shown = []
    monkeypatch.setattr(gui.QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))

    def fail():
        raise FrameError("0x1C00: the device sent 68 registers, expected 71")

    monkeypatch.setattr(w.pack, "read_block", fail)
    w.read_settings()
    wait(app, lambda: shown)
    assert any("ERROR" in line and "the device sent 68 registers" in line for line in w.pack.log.lines)


def test_a_setting_the_pack_did_not_send_is_greyed_out_and_never_staged(window):
    from packkeeper.drivers.jbd_up import DRIVER
    from packkeeper.drivers.jbd_up.sim import FakeJbd
    from packkeeper.session import SessionLog

    app, w = window
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    sim = FakeJbd()
    full = DRIVER.open_device(sim, SessionLog(), 0).read_block()
    sim.sends[0x1C00] = 0x1C86  # static sleep delay not sent
    short = DRIVER.open_device(sim, SessionLog(), 0).read_block()
    tab = w.settings
    tab.set_block(short)
    field = tab.fields["static_sleep_delay"]
    assert not field.edit.isEnabled() and field.edit.text() == "" and "not sent" in field.sub.text()
    assert tab.fields["standby_sleep_current"].edit.isEnabled()
    assert "1 setting(s) not sent" in tab.read_label.text()
    changed = full.with_raws({DRIVER.table["static_sleep_delay"].register: 60,
                              DRIVER.table["standby_sleep_current"].register: 20})
    tab.add_source(gui.settings_view.Source("other", changed))
    assert "60" in field.edit.toolTip()
    tab.stage_all()
    assert tab.staged == {"standby_sleep_current": 20}
    assert "1 setting(s) were not sent by one side" in tab.ref_text.text()


def connect_jbd(app, w):
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    w.ports.setCurrentIndex(w.ports.findData(gui.SIM_PORT))
    w.toggle_connect()
    wait(app, lambda: w.settings.block is not None and w._last_rt is not None)


def click_control(app, w, key):
    """Click a control's button once nothing else holds the worker (a live poll disables the buttons)."""
    wait(app, lambda: w.controls.buttons[key].isEnabled())
    w.controls.buttons[key].click()


def slow_jbd_bus(monkeypatch, delay=0.03):
    """Make every simulated request take a little time, so Stop and close land mid-control."""
    from packkeeper.drivers.jbd_up import sim

    orig = sim.FakeJbdBus.transact

    def slow(self, request):
        time.sleep(delay)
        return orig(self, request)

    monkeypatch.setattr(sim.FakeJbdBus, "transact", slow)


def test_controls_tab_only_for_a_bms_with_controls(window):
    app, w = window
    i = w.tabs.indexOf(w.controls)
    assert not w.tabs.isTabVisible(i)  # EG4-LL
    w.bms.setCurrentIndex(w.bms.findData("jk-pb"))
    assert not w.tabs.isTabVisible(i)
    w.bms.setCurrentIndex(w.bms.findData("jbd-up"))
    assert w.tabs.isTabVisible(i) and set(w.controls.buttons) == {"restart", "standby", "mosfet-test", "normal"}
    assert not any(b.isEnabled() for b in w.controls.buttons.values())  # not connected


def test_controls_dry_run_sends_nothing(window):
    app, w = window
    connect_jbd(app, w)
    w.controls.dry.setChecked(True)
    click_control(app, w, "mosfet-test")
    text = w.controls.steps.text()
    assert "5A 01" in text and "Nothing was sent" in text
    assert not [line for line in w.pack.log.lines if " TX " in line and " 79 29 " in line]


def test_mosfet_test_runs_and_locks_disconnect(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    click_control(app, w, "mosfet-test")
    wait(app, lambda: w.controls.running is not None)
    assert not w.connect_btn.isEnabled() and not w.controls.stop_btn.isHidden()
    w.disconnect_pack()
    assert w.pack is not None  # refused while a control runs
    wait(app, lambda: w.controls.running is None, timeout=15)
    assert "Confirmed." in w.controls.steps.text() and w.connect_btn.isEnabled()
    assert w.controls.stop_btn.isHidden()


def test_stop_goes_back_to_normal(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    w.controls.step_done.connect(lambda r: w.controls.stop_btn.click() if not r.cleanup else None)
    click_control(app, w, "mosfet-test")
    wait(app, lambda: w.controls.running is not None)
    wait(app, lambda: w.controls.running is None, timeout=15)
    text = w.controls.steps.text()
    assert "stopped by the user" in text and "cleanup: Normal mode" in text
    assert w.pack.transport.packs[0].mem[0x2900] == 0


def test_closing_mid_control_goes_back_to_normal_first(window, monkeypatch):
    import packkeeper.drivers.jbd_up.controls as JC

    app, w = window
    monkeypatch.setattr(JC, "POLL_S", 0.01)
    monkeypatch.setattr(gui.controls_view.QInputDialog, "getText", lambda *a, **k: ("test", True))
    slow_jbd_bus(monkeypatch)
    connect_jbd(app, w)
    sim = w.pack.transport.packs[0]  # the GUI's simulated port is the FakeJbdBus itself
    click_control(app, w, "mosfet-test")
    wait(app, lambda: sim.writes and sim.writes[-1] == (0x2900, [0x5A01]))
    w.close()
    assert sim.writes[-1] == (0x2900, [0]) and sim.mem[0x2900] == 0
