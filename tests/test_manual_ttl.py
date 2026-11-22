import json

import numpy as np
import pandas as pd
from matplotlib.backend_bases import KeyEvent, MouseEvent

from fiberphoto import PipelineConfig
from fiberphoto.analysis import peri_event
from fiberphoto.data import load_stage
from fiberphoto.gui.main_window import MainWindow
from fiberphoto.pipeline import pick_marker, read_with_behavior, run_batch, run_file
from fiberphoto.preprocess import dedrift


def recording(path):
    t = np.arange(320) / 4 + 10
    pd.DataFrame({"time": t, "RawF_1": 50 + np.sin(t), "R_1": .01 * np.sin(t)}).to_csv(path, index=False)
    return path


def test_manual_ttl_config_and_batch_exports(tmp_path):
    first = recording(tmp_path / "first.csv")
    second = recording(tmp_path / "second.csv")
    hardware = tmp_path / "ttl.csv"
    pd.DataFrame({"onset": [22., 44.], "label": ["Manual TTL"] * 2}).to_csv(hardware, index=False)
    cfg = PipelineConfig()
    cfg.channels.marker_file = str(hardware)
    cfg.deconv.enabled = False
    cfg.manual_ttl_by_recording[str(first)] = [48., 31.]
    cfg.peth.marker = "Manual TTL (2)"
    cfg.peth.align_to = "offset"
    cfg.plot.formats = ["png"]
    cfg.plot.dpi = 55
    cfg.save(tmp_path / "params.json")
    restored = PipelineConfig.load(tmp_path / "params.json")

    a = read_with_behavior(first, restored)
    np.testing.assert_array_equal(a.markers["Manual TTL"], [22., 44.])
    np.testing.assert_array_equal(pick_marker(a, cfg.peth.marker, "offset")[1], [31., 48.])
    assert a.manual_ttl_label == cfg.peth.marker
    assert not a.with_manual_ttl([200.]).manual_ttl_label
    assert "Manual TTL (2)" not in a.with_manual_ttl([200.]).markers
    runs = run_batch([first, second], restored, tmp_path / "out")
    for res in runs:
        assert not res.error, res.error
    annotated, other = runs
    np.testing.assert_array_equal(annotated.peth.onsets, [31., 48.])
    np.testing.assert_array_equal(other.peth.onsets, [22., 44.])
    loaded = load_stage(annotated.out_dir / "first.h5", "A")
    assert loaded.manual_ttl_label == "Manual TTL (2)"
    markers = pd.read_csv(annotated.out_dir / "first_dataA_markers.csv").groupby("label")
    np.testing.assert_array_equal(markers.get_group("Manual TTL (2)").onset_s, [31., 48.])
    config = json.loads((other.out_dir / "parameters.json").read_text(encoding="utf-8"))
    assert not config["manual_ttl_by_recording"]
    report = json.loads((annotated.out_dir / "analysis_methods.json").read_text(encoding="utf-8"))
    assert report["stages"][0]["parameters"]["manual_ttl"] == {"label": cfg.peth.marker, "onsets_s": [31., 48.]}


def test_click_add_undo_switch_and_reread(qtbot, tmp_path):
    first, second = [recording(tmp_path / n) for n in ("1.csv", "2.csv")]
    w = MainWindow(lang="en")
    qtbot.addWidget(w)
    w.show()
    w.add_paths([first, second])
    w.step_read()
    qtbot.waitUntil(lambda: w.A is not None)
    picker = w.baseline_picker
    canvas = picker.canvas
    ax = canvas.figure.axes[0]
    ax.set_xlim(30., 60.)
    canvas.draw()
    picker.ttl_button.click()
    assert canvas.widgetlock.isowner(picker) and not picker.signal_button.isEnabled()

    def click(start, end=None, button=1):
        for name, target in [("button_press_event", start), ("button_release_event", start if end is None else end)]:
            x, y = ax.transData.transform((target, np.mean(ax.get_ylim())))
            canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=button))

    click(48.)
    click(32.)
    click(32.)  # no duplicate for the same frame
    click(31., 35.)  # dragging is not a TTL
    click(40., button=3)
    np.testing.assert_array_equal(w.A.markers["Manual TTL"], [32., 48.])
    assert w.cb_marker_use.currentText() == "Manual TTL"
    assert picker.canvas is canvas
    np.testing.assert_allclose(ax.get_xlim(), [30., 60.])
    canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", canvas, key="escape"))
    assert picker.kind is None and not canvas.widgetlock.locked()
    assert not picker.ttl_button.isChecked()

    # Clear only results that used the series being edited.
    w.B = dedrift(w.A, w.cfg.dedrift)
    w.peth = peri_event(w.B, w.A.markers["Manual TTL"], w.cfg.peth, "Manual TTL")
    w.stim_events = pd.DataFrame()
    w.stim_events.attrs["marker"] = "Manual TTL"
    w.redraw_all()
    assert w.trial_table.rowCount() == 2
    pre_view = w.fig_dedrift.canvas
    picker.undo_ttl.click()
    assert w.fig_dedrift.canvas is pre_view
    overlay = next(c for c in pre_view.figure.axes[1].collections if c.get_gid() == "stimulus-times")
    assert [s[0][0] for s in overlay.get_segments()] == [48.]
    assert w.B is not None and w.peth is None and w.stim_events is None
    assert w.trial_table.rowCount() == 0 and w.fig_peth_dff.canvas is None
    np.testing.assert_array_equal(w.A.markers["Manual TTL"], [48.])

    # Independent hardware-series statistics remain intact.
    w.A.markers["Hardware"] = np.array([40.])
    w.cfg.peth.marker = "Hardware"
    w._refresh_marker_choices()
    saved_trials = w.peth = peri_event(w.B, w.A.markers["Hardware"], w.cfg.peth, "Hardware")
    saved_stats = w.stim_events = pd.DataFrame()
    saved_stats.attrs["marker"] = "Hardware"
    w.redraw_all()
    existing_figure = w.fig_peth.canvas
    w.edit_manual_ttl(31.)
    assert w.peth is saved_trials and w.stim_events is saved_stats
    assert w.fig_peth.canvas is existing_figure
    w.file_list.setCurrentRow(1)
    w.step_read()
    qtbot.waitUntil(lambda: w.A is not None)
    assert not w.A.manual_ttl_label
    w.file_list.setCurrentRow(0)
    np.testing.assert_array_equal(w.A.markers["Manual TTL"], [31., 48.])
    w.step_read()
    qtbot.waitUntil(lambda: not w._busy)
    np.testing.assert_array_equal(w.A.markers["Manual TTL"], [31., 48.])
    picker.undo_ttl.click()
    picker.undo_ttl.click()
    assert not w.A.manual_ttl_label and "Manual TTL" not in w.A.markers
    assert not w.cfg.manual_ttl_by_recording


def test_last_manual_ttl_archives_only_owned_obsolete_outputs_after_success(tmp_path, monkeypatch):
    from fiberphoto import marker_exports

    src = recording(tmp_path / "annotated.csv")
    cfg = PipelineConfig()
    cfg.plot.formats = ["png"]
    cfg.plot.dpi = 45
    cfg.manual_ttl_by_recording[str(src)] = [30., 60.]
    out = tmp_path / "out"
    result = run_file(src, cfg, out)
    assert not result.error, result.error
    dest = result.out_dir
    table = dest / "peth_trials_dff.csv"
    metrics = dest / "peth_metrics.csv"
    metrics.write_text("edited by user", encoding="utf-8")
    (dest / "notes.csv").write_text("notes", encoding="utf-8")
    previous_report = (dest / "analysis_methods.json").read_bytes()
    cfg.manual_ttl_by_recording.clear()

    def locked(*_):
        raise PermissionError("locked archived path")

    with monkeypatch.context() as m:
        m.setattr(marker_exports, "archive_superseded", locked)
        failed = run_file(src, cfg, out, make_figures=False)
        assert "locked archived path" in failed.error
    assert table.exists() and (dest / "analysis_methods.json").read_bytes() == previous_report

    result = run_file(src, cfg, out, make_figures=False)
    assert not result.error, result.error
    archive, = dest.glob("previous-manual-ttl-*")
    assert not table.exists() and (archive / table.name).is_file()
    for name in ("annotated_dataA_markers.csv", "peth_paired_stats.csv", "events_vs_stimulus.csv",
                 "figures/04_peth_heatmap_dff.png", "figures/05_peak_baseline_target.png", "figures/07_event_rate_pre_post.png"):
        assert (archive / name).is_file() and not (dest / name).exists()
    assert metrics.read_text(encoding="utf-8") == "edited by user"
    assert (dest / "notes.csv").read_text(encoding="utf-8") == "notes"
    assert (dest / "figures/06_event_distributions.png").is_file()
    assert src.is_file()


def test_last_manual_ttl_keeps_new_hardware_comparisons(tmp_path):
    src = recording(tmp_path / "hardware.csv")
    markers = tmp_path / "markers.csv"
    markers.write_text("onset,label\n40,stim\n", encoding="utf-8")
    cfg = PipelineConfig()
    cfg.channels.marker_file = str(markers)
    cfg.manual_ttl_by_recording[str(src)] = [30.]
    cfg.peth.marker = "Manual TTL"
    cfg.deconv.enabled = False
    out = tmp_path / "out"
    one = run_file(src, cfg, out, make_figures=False)
    assert not one.error and one.peth.marker == "Manual TTL"
    cfg.manual_ttl_by_recording.clear()
    two = run_file(src, cfg, out, make_figures=False)
    assert not two.error and two.peth.marker == "stim"
    m = pd.read_csv(two.out_dir / "hardware_dataA_markers.csv")
    assert list(m["label"]) == ["stim"]
    assert list(pd.read_csv(two.out_dir / "peth_metrics.csv").onset_s) == [40.]
    assert not list(two.out_dir.glob("previous-manual-ttl-*"))
