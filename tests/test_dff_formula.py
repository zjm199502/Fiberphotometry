import numpy as np
import pandas as pd
import pytest

from fiberphoto.config import DedriftConfig, PipelineConfig
from fiberphoto.data import RawData, load_stage, save_stage
from fiberphoto.pipeline import run_file
from fiberphoto.preprocess import dedrift, format_dff_formula


@pytest.fixture
def raw():
    t = np.arange(300) / 10.
    c = 10 + np.sin(t)
    return RawData(t, 2 * c + 3, c, 10., source="mouse7.csv",
                   channel_names={"signal": "green", "control": "violet"})


@pytest.mark.parametrize("method", ["signal_interval", "polynomial", "highpass", "isosbestic"])
def test_formula_describes_fitted_result(raw, method, tmp_path):
    cfg = DedriftConfig(method=method, lowpass_hz=None, auto_trim=False, downsample_hz=None,
                        baseline_start_s=0., baseline_end_s=5., output="zscore")
    b = dedrift(raw, cfg)
    d = b.formula_info
    if method == "signal_interval":
        reference = np.median(raw.signal[:51])
    elif method == "polynomial":
        reference = np.polyval(d["coefficients"], (b.time - b.time[0]) / d["span"])
    elif method == "highpass":
        from fiberphoto.preprocess import highpass
        reference = b.signal - highpass(b.signal, b.fs, d["cutoff_hz"])
    else:
        reference = d["slope"] * raw.control + d["intercept"]
    np.testing.assert_allclose(b.reference, reference)
    pct = 100 * (b.signal - reference) / reference
    np.testing.assert_allclose(b.dff, (pct - d["centre"]) / d["scale"])
    zh = format_dff_formula(raw, b, "zh")
    assert "[mouse7.csv] ΔF/F 公式" in zh and "z(t)" in zh and "green" in zh
    required = {"isosbestic": "violet", "signal_interval": "median(green[0–5 s])",
                "highpass": "HP(F(t)", "polynomial": "polyval"}
    assert required[method] in zh
    reloaded = load_stage(save_stage(tmp_path / "data.h5", "B", b), "B")
    assert format_dff_formula(raw, reloaded, "zh") == zh


def test_formula_with_failed_bleaching_fit_uses_actual_cubic(raw, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("did not converge")
    monkeypatch.setattr("fiberphoto.preprocess.optimize.curve_fit", fail)
    raw.control = None
    with pytest.warns(UserWarning, match="biexponential"):
        b = dedrift(raw, DedriftConfig(lowpass_hz=None, downsample_hz=None, auto_trim=False))
    formula = format_dff_formula(raw, b, "en")
    assert "cubic fallback" in formula and "polyval" in formula
    assert "violet" not in formula
    np.testing.assert_allclose(b.reference, np.polyval(b.formula_info["coefficients"], b.time - b.time[0]))


def test_biexponential_curve_parameters_reported(raw):
    t = raw.time
    raw.signal = 10 + 3 * np.exp(-t / 25) + 5 * np.exp(-t / 2.4)
    b = dedrift(raw, DedriftConfig(method="biexponential", lowpass_hz=None, downsample_hz=None, auto_trim=False))
    a, tau1, c, tau2, d = b.formula_info["coefficients"]
    np.testing.assert_allclose(b.reference, a * np.exp(-t / tau1) + c * np.exp(-t / tau2) + d)
    assert f"exp(−u/{tau1:.12g})" in format_dff_formula(raw, b, "en")


@pytest.mark.parametrize("lang, label", [("zh", "设备导出比值"), ("en", "device-exported ratio")])
def test_pipeline_logs_filename_and_device_source(tmp_path, lang, label):
    t = np.arange(600) / 10.
    path = tmp_path / "export.csv"
    pd.DataFrame({"time": t, "RawF_1": 20 + np.cos(t), "R_1": .01 * np.sin(t)}).to_csv(path, index=False)
    cfg = PipelineConfig()
    cfg.deconv.enabled = False
    cfg.plot.language = lang
    log = []
    result = run_file(path, cfg, tmp_path / "out", log.append, make_figures=False)
    assert not result.error
    line = next(line for line in log if f"[{path.name}]" in line)
    assert line == format_dff_formula(result.A, result.B, lang)
    assert "ΔF/F(t) = R_1(t)" in line and label in line
    assert "100 ×" not in line
