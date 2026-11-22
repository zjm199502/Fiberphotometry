import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pytest
from PySide6.QtWidgets import QFileDialog

from fiberphoto import analysis
from fiberphoto.gui.main_window import MainWindow
from fiberphoto.sample_data import make_sample_dataset
from fiberphoto.plot_data import export_plot_bundle


def read(bundle, name):
    return pd.read_csv(bundle.open(name + ".csv"), float_precision="round_trip")


def test_visible_results_export_without_reanalysis(qtbot, tmp_path, monkeypatch):
    src, = make_sample_dataset(tmp_path / "input", n_animals=1, duration_s=180)
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    monkeypatch.setattr(window, "_confirm_preprocessing", lambda *_: True)
    window.add_paths([src])
    for action in (window.step_read, window.step_pre, window.step_peth, window.step_events):
        action()
        qtbot.waitUntil(lambda: not window._busy, timeout=60000)
        assert not errors
    assert window.b_export_peth_plot_data.isEnabled()
    assert window.b_export_event_plot_data.isEnabled()
    p, ev = window.peth, window.events
    monkeypatch.setattr(analysis, "peri_event", lambda *_: pytest.fail("analysis called during export"))
    monkeypatch.setattr(analysis, "detect_events", lambda *_: pytest.fail("event detection called during export"))
    window.form_events.editors["spike_threshold"].setValue(12.)
    window.form_peth.editors["response_end_s"].setValue(19.)
    for page, button in (("peth", window.b_export_peth_plot_data), ("events", window.b_export_event_plot_data)):
        archive = tmp_path / (page + ".zip")
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(archive), ""))
        button.click()
        qtbot.waitUntil(lambda: not window._busy, timeout=60000)
        assert not errors
        with ZipFile(archive) as bundle:
            assert bundle.testzip() is None
            assert any(n.startswith("preview/") for n in bundle.namelist())
            meta = json.loads(bundle.read("metadata.json"))
            if page == "peth":
                assert meta["settings"]["response_end_s"] == 5.
                np.testing.assert_allclose(read(bundle, "trials_dff").iloc[:, 1:].T, p.trials_dff)
                np.testing.assert_allclose(read(bundle, "trials_z").iloc[:, 1:].T, p.trials_z)
                np.testing.assert_allclose(read(bundle, "time_summary").sem_z, p.sem("z"), equal_nan=True)
                np.testing.assert_allclose(read(bundle, "trial_metrics").peak_dff, p.metrics.peak_dff)
                assert list(meta["axes"]["peth_z"][0]["xlim"]) == [-5., 5.]
            else:
                assert meta["settings"]["spike_threshold"] == .8
                assert meta["threshold"]["axis"] == "activity"
                e, d = read(bundle, "events"), read(bundle, "deconvolution")
                np.testing.assert_allclose(d.denoised_native, window.C.denoised)
                np.testing.assert_allclose(d.activity_native, window.C.spikes)
                np.testing.assert_allclose(e.auc_dff, ev.events.auc)
                np.testing.assert_allclose(e.marker_y_native, window.C.denoised[np.searchsorted(window.C.time, e.peak_time_s)])
                assert np.isclose(read(bundle, "fluorescence").signal_z.mean(), 0)
                assert len(read(bundle, "stimulus_comparison")) == len(window.stim_events)
                bins = read(bundle, "distributions")
                assert bins.query("variable == 'amplitude'")["count"].sum() == len(e)
            folder = tmp_path / page
            bundle.extractall(folder)
        subprocess.run([sys.executable, str(folder / "replot.py")], check=True, cwd=tmp_path, timeout=60)
        assert len(list((folder / "redrawn").glob("*.png"))) >= 5
    window.clear_page_results("events")
    assert not window.b_export_event_plot_data.isEnabled()


def test_physical_units_from_standardized_preprocessing(tmp_path):
    from fiberphoto.config import PethConfig, EventConfig
    from fiberphoto.data import DffData, DeconvData, RawData

    time = np.arange(0., 21.)
    signal = np.sin(time)
    raw = RawData(time=time, signal=signal, control=None, markers={}, fs=1., source="z.csv")
    b = DffData(time, signal, 1., signal, None, "test", "z", formula_info={"centre": 4., "scale": 2.})
    c = DeconvData(time, signal, np.maximum(signal, 0), 1., 0., (.9,), .1)
    ev = analysis.detect_events(c, b, EventConfig(method="peak", threshold_mad=0, min_prominence_mad=0))
    archive = tmp_path / "events.zip"
    # A simulated legacy high-pass result has no stored filter details. The data conversion is unchanged.
    b.method = "isosbestic"
    b.formula_info.update(slope=1., intercept=0.)
    export_plot_bundle(archive, raw, b, ({}, {}), events=ev, deconv=c)
    with ZipFile(archive) as z:
        np.testing.assert_allclose(read(z, "fluorescence").signal_dff, signal * 2 + 4)
        e = read(z, "events")
        np.testing.assert_allclose(e.amplitude_dff, ev.events.amplitude * 2)
        np.testing.assert_allclose(e.baseline_dff, ev.events.baseline * 2 + 4)
        meta = json.loads(z.read("metadata.json"))
        assert meta["threshold"]["dff"] == pytest.approx(ev.threshold * 2 + 4)
    p = analysis.peri_event(b, [10.], PethConfig())
    export_plot_bundle(archive, raw, b, ({}, {}), peth=p)
    with ZipFile(archive) as z:
        np.testing.assert_allclose(read(z, "trials_dff").trial_1, p.trials_dff[0] * 2)
        metrics = read(z, "trial_metrics")
        assert metrics.prestimulus_mean_input_dff[0] == pytest.approx(p.metrics.prestimulus_mean_input_dff[0]*2+4)
