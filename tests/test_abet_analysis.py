from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from fiberphoto.abet_analysis import analyze_abet, export_abet_analysis, figure_abet_analysis
from fiberphoto.data import DffData


def sample():
    t = np.arange(0, 40, .1)
    b = DffData(t, t.copy(), 10., t, None, "Plexon R_1", "ratio")
    rows = pd.DataFrame([
        (1, "timeout", False, 1., 1., np.nan),
        (2, "responded", True, 9., 15., 17.),
        (3, "timeout", True, 23., 29., np.nan),
        (4, "incomplete", False, 34., np.nan, np.nan),
    ], columns=["trial", "outcome", "regular", "cue_on_s", "traylight_on_s", "tray_input_on_s"])
    return b, SimpleNamespace(trials=rows, control_onsets_s=[34.],
                              info={"mode": "assumed_trigger", "offset_s": -1., "scale": 1., "latency_s": 1.})


def test_clock_trial_identity_and_metrics(tmp_path):
    b, aligned = sample()
    result = analyze_abet(b, aligned, peak_direction="negative")
    ledger = result.metrics
    cue = ledger.query("group == 'cue_responded'").iloc[0]
    assert cue.trial == 2 and cue.abet_onset_s == 9 and cue.calcium_onset_s == 8
    assert cue.target_mean_dff == pytest.approx(3.05)
    assert cue.auc_target_dff == pytest.approx(9.15)
    assert cue.peak_dff == pytest.approx(1.55) and cue.peak_latency_s == 0.
    assert ledger.exclusion.tolist().count("outside_analysis_window") == 1
    assert ledger.exclusion.tolist().count("incomplete_trial") == 1
    assert result.groups.n_used.sum() == 4
    for _, curve in result.traces.groupby("event_id"):
        assert curve.loc[curve.t_rel_s < 0, "dff_centered"].mean() == pytest.approx(0, abs=1e-12)
    export_abet_analysis(result, tmp_path)
    assert (tmp_path / "overview.png").stat().st_size > 1000
    assert pd.read_csv(tmp_path / "metrics.csv").shape == ledger.shape
    assert result.parameters["alignment"]["latency_s"] == 1.


def test_unmapped_nonfinite_gaps_and_sparse_sessions():
    b, aligned = sample()
    aligned.info["mode"] = "behavior_only"
    with pytest.raises(ValueError):
        analyze_abet(b, aligned)
    aligned.info["mode"] = "offset"
    b.dff[80] = np.nan
    b.time[210:] += 2.
    result = analyze_abet(b, aligned)
    assert result.metrics.query("group == 'cue_responded'").iloc[0].exclusion == "nonfinite_signal"
    assert result.metrics.query("group == 'cue_timeout'").iloc[0].exclusion == "acquisition_gap"
    b.dff[:] = np.nan
    result = analyze_abet(b, aligned)
    assert result.groups.n_used.sum() == 0
    assert figure_abet_analysis(result, "en").axes


def test_comparison_from_peri_event_button(qtbot, tmp_path, monkeypatch, abet_xml):
    from PySide6.QtWidgets import QDialog, QFileDialog, QPushButton
    from fiberphoto.abet import AbetConfig
    from fiberphoto.gui.main_window import MainWindow
    from fiberphoto.gui.widgets import FigureView
    from fiberphoto.preprocess import dedrift

    recording = abet_xml.with_suffix(".csv")
    t = np.arange(600) / 4
    pd.DataFrame({"time": t, "RawF_1": 50 + np.sin(t), "R_1": .03*np.sin(t)}).to_csv(recording, index=False)
    window = MainWindow("en")
    qtbot.addWidget(window)
    window.add_paths([recording])
    window.step_read()
    qtbot.waitUntil(lambda: window.A is not None)
    window.B = dedrift(window.A, window.cfg.dedrift)
    assert not window.b_abet_compare.isEnabled()
    window.set_abet(AbetConfig(str(abet_xml), mode="assumed_trigger"))
    qtbot.waitUntil(window.b_abet_compare.isEnabled)
    folder = tmp_path / (recording.stem + "_ABET_comparison")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_: str(tmp_path))

    def inspect(dialog):
        assert "Assumed trigger" in dialog.findChild(FigureView).canvas.figure._suptitle.get_text()
        save = next(button for button in dialog.findChildren(QPushButton) if "Export" in button.text())
        save.click()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", inspect)
    window.b_abet_compare.click()
    qtbot.waitUntil(lambda: (folder/"overview.png").exists(), timeout=12000)
    assert set(pd.read_csv(folder/"metrics.csv").sync_mode) == {"assumed_trigger"}
