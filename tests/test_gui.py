"""Smoke test of the desktop interface: run every step on the sample data."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from fiberphoto.gui.main_window import MainWindow  # noqa: E402
from fiberphoto.sample_data import make_sample_dataset  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    w = MainWindow(lang="zh")
    qtbot.addWidget(w)
    w.shown = shown
    paths = make_sample_dataset(tmp_path / "data", n_animals=2, duration_s=180)
    w.add_paths(paths)
    w.out_dir.setText(str(tmp_path / "out"))
    return w


def run(qtbot, w, fn):
    fn()
    qtbot.waitUntil(lambda: not w._busy, timeout=60000)


def test_compact_recording_switch_and_preview(qtbot, window):
    w = window
    w.resize(671, 420)
    w.show()
    qtbot.waitUntil(w.file_combo.isVisible)
    assert w.file_combo.count() == 2
    assert not w.file_sidebar.isVisible() and not w.log_dock.isVisible()
    run(qtbot, w, w.step_read)
    first = w.current
    raw = w.A
    assert w.read_panels.compact.currentWidget() is w.fig_raw
    w.file_combo.activated.emit(1)
    assert w.current != first and w.A is None and w.read_panels.compact.currentIndex() == 0
    w.file_combo.activated.emit(0)
    assert w.current == first and w.A is raw and w.read_panels.compact.currentWidget() is w.fig_raw
    w.resize(1400, 900)
    qtbot.waitUntil(w.file_sidebar.isVisible)
    assert w.A is raw and w.fig_raw.isVisible()

    # At 1400 × 900 the sidebar fits, but larger platform fonts can still
    # require separate tabs for the settings and preview. Give both panes
    # enough height to verify that widening the layout preserves live data.
    w.setFixedSize(1600, max(1400, 85 * w.fontMetrics().lineSpacing()))
    qtbot.waitUntil(lambda: w.read_panels.compact.count() == 0)
    assert w.b_read.isVisible() and w.fig_raw.isVisible() and w.A is raw


def test_full_walkthrough(qtbot, window, tmp_path, monkeypatch):
    w = window
    assert w.cb_signal.currentText() == "470nm" and w.cb_control.currentData() == "405nm"
    run(qtbot, w, w.step_read)
    assert w.A is not None and "DI1_Stim" in w.A.markers
    run(qtbot, w, w.step_pre)
    assert w.B is not None and w.C is not None
    assert "ΔF/F 公式" in w.log_view.toPlainText() and "405nm" in w.log_view.toPlainText()
    run(qtbot, w, w.step_peth)
    assert w.peth is not None and w.trial_table.rowCount() == len(w.peth.trials) > 0
    peak_choice = w.form_peth.editors["peak_direction"]
    peak_choice.setCurrentIndex(peak_choice.findData("negative"))
    run(qtbot, w, w.step_peth)
    assert w.peth.peak_direction == "negative"
    for tab, unit in [(1, "%"), (0, "z")]:
        w.peth_views.setCurrentIndex(tab)
        auc, peak = w.fig_auc.canvas.figure.axes
        assert "AUC" in auc.get_ylabel() and unit in auc.get_ylabel()
        assert "负峰" in peak.get_ylabel() and unit in peak.get_ylabel()
    run(qtbot, w, w.step_events)
    assert w.events is not None and w.events.summary["n_events"] > 0
    from PySide6.QtWidgets import QFileDialog
    import pandas as pd

    assert w.event_model.rowCount() == len(w.events.events)
    assert float(w.event_model.data(w.event_model.index(0, 2))) == pytest.approx(w.events.events.auc.iloc[0])
    path = tmp_path / "events-preview.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(path), "CSV"))
    w.form_events.editors["spike_threshold"].setValue(100)
    w.b_export_events.click()
    exported = pd.read_csv(path)
    assert exported.auc.tolist() == pytest.approx(w.events.events.auc.tolist())
    assert exported.amplitude_unit.unique().tolist() == [w.B.unit]
    assert exported.auc_unit.unique().tolist() == [w.B.unit + "·s"]
    assert w.fig_peth.canvas is not None and w.fig_rate.canvas is not None
    w.set_lang("en")
    assert w.tabs.tabText(0).endswith("Load data")
    assert w.fig_peth.canvas is not None
    assert not w.shown


def test_clear_page_preserves_other_results_and_parameters(qtbot, window, tmp_path):
    w = window
    assert not any(button.isEnabled() for button in w.clear_buttons.values())
    for step in (w.step_read, w.step_pre, w.step_peth, w.step_events):
        run(qtbot, w, step)
    w._set_busy(True)
    assert not any(button.isEnabled() for button in w.clear_buttons.values())
    w.clear_page_results("read")
    assert w.A is not None and w.peth is not None
    w._set_busy(False)
    peth, peth_canvas = w.peth, w.fig_peth.canvas
    w.clear_buttons["events"].click()
    assert w.events is w.stim_events is w.fig_events.canvas is w.fig_dist.canvas is w.fig_rate.canvas is None
    assert not w.lbl_events.text() and not w.clear_buttons["events"].isEnabled()
    assert w.event_model.rowCount() == 0 and not w.b_export_events.isEnabled()
    assert w.peth is peth and w.fig_peth.canvas is peth_canvas
    run(qtbot, w, w.step_events)
    events, event_canvas = w.events, w.fig_events.canvas
    w.clear_buttons["peth"].click()
    assert w.peth is w.fig_peth.canvas is w.fig_peth_dff.canvas is w.fig_auc.canvas is None
    assert w.trial_table.rowCount() == 0 and not w.clear_buttons["peth"].isEnabled()
    assert w.events is events and w.fig_events.canvas is event_canvas
    run(qtbot, w, w.step_peth)

    w.chk_fmt["pdf"].setChecked(False)
    w.spin_dpi.setValue(72)
    run(qtbot, w, w.export_current)
    report = tmp_path / "out" / w.current.stem / "analysis_methods.json"
    assert report.is_file() and w.lbl_export.text() and w.clear_buttons["export"].isEnabled()
    w.clear_buttons["export"].click()
    assert report.is_file() and not w.lbl_export.text() and w.progress.value() < 0
    assert w.peth is not None and w.events is events

    raw, raw_canvas = w.A, w.fig_raw.canvas
    w.clear_buttons["pre"].click()
    assert w.A is raw and w.fig_raw.canvas is raw_canvas
    assert w.B is w.C is w.peth is w.events is w.stim_events is None
    assert w.fig_dedrift.canvas is w.fig_deconv.canvas is None
    assert not w.lbl_pre.text() and not w.lbl_events.text() and not w.trial_table.rowCount()
    w.cb_marker_use.setCurrentText("DI2_Lick")
    w.form_dedrift.editors["signal_start_s"].setValue(10.0)
    w.form_dedrift.editors["signal_end_s"].setValue(120.0)
    w.form_peth.editors["pre_s"].setValue(3.0)
    w.clear_buttons["read"].click()
    assert w.current.is_file() and w.A is None and w.fig_raw.canvas is None
    assert w.chan_table.rowCount() > 0 and not w.cb_marker_use.count()
    w.file_list.setCurrentRow(1)
    run(qtbot, w, w.step_read)
    other_raw = w.A
    w.file_list.setCurrentRow(0)
    assert w.A is w.B is w.peth is w.events is None and not w.clear_buttons["read"].isEnabled()
    run(qtbot, w, w.step_read)
    assert w.cb_marker_use.currentText() == "DI2_Lick"
    assert w.form_dedrift.editors["signal_start_s"].value() == 10
    assert w.form_dedrift.editors["signal_end_s"].value() == 120
    assert w.form_peth.editors["pre_s"].value() == 3
    run(qtbot, w, w.step_pre)
    assert w.B is not None and w.clear_buttons["pre"].isEnabled()
    w.file_list.setCurrentRow(1)
    assert w.A is other_raw


def test_close_during_analysis(qtbot, window):
    from threading import Event

    release = Event()
    applied = []
    window.show()
    window._run(lambda: release.wait(10), applied.append)
    try:
        assert not window.file_list.isEnabled()
        assert not window.close()
        assert window._stop and window.isVisible()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert not applied and not window._workers


def test_switch_recordings(qtbot, window):
    run(qtbot, window, window.step_read)
    run(qtbot, window, window.step_pre)
    run(qtbot, window, window.step_peth)
    run(qtbot, window, window.step_events)
    window.tabs.setCurrentIndex(3)
    previous = window.A, window.B, window.C, window.peth, window.events, window.stim_events
    note = window.lbl_pre.text()
    window.file_list.setCurrentRow(1)
    assert window.A is None and window.events is None and not window.cb_marker_use.count()
    run(qtbot, window, window.step_read)
    other = window.A
    window.file_list.setCurrentRow(0)
    assert all(a is b for a, b in zip(previous, (window.A, window.B, window.C, window.peth, window.events, window.stim_events)))
    assert window.lbl_pre.text() == note and window.tabs.currentIndex() == 3
    assert window.fig_events.canvas is not None and window.trial_table.rowCount() > 0
    path = window.current
    window.remove_file()
    assert path not in window.recording_states and window.A is other
    path.rename(path.with_suffix(".moved"))


def test_parameters_reach_pipeline(qtbot, window):
    w = window
    w.form_dedrift.editors["method"].setCurrentIndex(w.form_dedrift.editors["method"].findData("polynomial"))
    w.form_peth.editors["pre_s"].setValue(3.0)
    cfg = w.forms_store()
    assert cfg.dedrift.method == "polynomial" and cfg.peth.pre_s == 3.0
    assert w.form_dedrift.editors["poly_order"].isEnabled()
    assert not w.form_dedrift.editors["highpass_hz"].isEnabled()


def test_batch(qtbot, window, tmp_path):
    w = window
    w.chk_fmt["pdf"].setChecked(False)
    w.spin_dpi.setValue(72)
    run(qtbot, w, w.run_batch)
    out = tmp_path / "out"
    assert (out / "batch_summary.csv").exists()
    assert len(list(out.glob("*/figures/04_peth_heatmap_z.png"))) == 2
    assert not w.shown


def test_batch_uses_each_analyzed_recordings_settings(qtbot, window, tmp_path):
    from fiberphoto.config import PipelineConfig
    w = window
    roots = []
    for row, method in enumerate(("polynomial", "isosbestic")):
        w.file_list.setCurrentRow(row)
        roots.append(tmp_path / "out" / w.current.stem)
        editor = w.form_dedrift.editors["method"]
        editor.setCurrentIndex(editor.findData(method))
        run(qtbot, w, w.step_read)
        run(qtbot, w, w.step_pre)
    w.chk_fmt["pdf"].setChecked(False)
    w.spin_dpi.setValue(72)
    run(qtbot, w, w.run_batch)
    for root, method in zip(roots, ("polynomial", "isosbestic")):
        cfg = PipelineConfig.load(root / "parameters.json")
        assert cfg.dedrift.method == method
        assert method in (root / "analysis_methods.json").read_text(encoding="utf-8")


def test_needs_previous_step(qtbot, window):
    w = window
    w.step_peth()
    assert w.shown  # told to load data first


def test_paired_event_file(qtbot, tmp_path, monkeypatch):
    import numpy as np
    from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

    from fiberphoto.sample_data import simulate

    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    sim = simulate(duration_s=120, n_stim=5, seed=5)
    props = {"wf_increment": 1.0 / sim["fs"]}
    main = tmp_path / "rec 1.tdms"
    with TdmsWriter(main) as w:
        w.write_segment([RootObject(), GroupObject("G"),
                         ChannelObject("G", "470nm", sim["signal"], properties=props),
                         ChannelObject("G", "405nm", sim["control"], properties=props)])
    with TdmsWriter(tmp_path / "rec 1-Event.tdms") as w:
        w.write_segment([RootObject(), GroupObject("G"),
                         ChannelObject("G", "Event1", sim["ttl_stim"], properties=props)])

    win = MainWindow(lang="en")
    qtbot.addWidget(win)
    win.add_paths([main, tmp_path / "rec 1-Event.tdms"])
    assert win.file_list.count() == 1
    assert "rec 1-Event.tdms" in win.marker_file.placeholderText()
    run(qtbot, win, win.step_read)
    (onsets,) = win.A.markers.values()
    stim = [s for s in sim["stim"] if s < 120]
    np.testing.assert_allclose(onsets, stim, atol=0.011)


def test_unnamed_channels_without_rate(qtbot, tmp_path, monkeypatch):
    """Like the real rig: Untitled channels, one flat, no sampling-rate property."""
    import numpy as np
    from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter

    from fiberphoto.sample_data import simulate

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    sim = simulate(duration_s=120, fs=50.0, seed=9)
    path = tmp_path / "dSub 1.tdms"
    with TdmsWriter(path) as w:
        w.write_segment([RootObject(), GroupObject("Untitled"),
                         ChannelObject("Untitled", "Untitled", np.zeros(sim["t"].size)),
                         ChannelObject("Untitled", "Untitled 1", sim["signal"])])
    win = MainWindow(lang="en")
    qtbot.addWidget(win)
    win.add_paths([path])
    assert win.cb_signal.currentText() == "Untitled 1"   # flat channel skipped
    assert not win.lbl_fs_missing.isHidden()
    win.step_read()
    assert shown and "sampling rate" in shown[-1]       # refuses until the rate is entered
    win.fs_edit.spin.setValue(50.0)
    run(qtbot, win, win.step_read)
    assert win.A is not None and win.A.fs == 50.0
    run(qtbot, win, win.step_pre)
    assert win.B.method == "biexponential" and "No control channel" in win.lbl_pre.text()


def test_manual_is_bundled(window, monkeypatch):
    from PySide6.QtGui import QDesktopServices
    from fiberphoto.gui.main_window import manual_path
    for lang in ("zh", "en"):
        assert manual_path(lang).name == f"manual_{lang}.pdf"
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window.a_manual.trigger()
    assert opened and opened[0].endswith("manual_zh.pdf")
