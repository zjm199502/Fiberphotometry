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


def test_full_walkthrough(qtbot, window):
    w = window
    assert w.cb_signal.currentText() == "470nm" and w.cb_control.currentData() == "405nm"
    run(qtbot, w, w.step_read)
    assert w.A is not None and "DI1_Stim" in w.A.markers
    run(qtbot, w, w.step_pre)
    assert w.B is not None and w.C is not None
    run(qtbot, w, w.step_peth)
    assert w.peth is not None and w.trial_table.rowCount() == len(w.peth.trials) > 0
    run(qtbot, w, w.step_events)
    assert w.events is not None and w.events.summary["n_events"] > 0
    assert w.fig_peth.canvas is not None and w.fig_rate.canvas is not None
    w.set_lang("en")
    assert w.tabs.tabText(0).endswith("Load data")
    assert w.fig_peth.canvas is not None
    assert not w.shown


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
