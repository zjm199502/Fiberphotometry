from dataclasses import replace

import numpy as np
import pytest

from fiberphoto.config import DedriftConfig, PipelineConfig
from fiberphoto.data import RawData, load_stage, save_stage
from fiberphoto.method_report import _preprocess
from fiberphoto.preprocess import dedrift, format_dff_formula


def test_device_centering_uses_actual_timestamps_and_selected_interval(tmp_path):
    t = np.array([8., 9., 10., 11., 12., 13., 14., 25., 26., 27.])
    ratio = np.array([90., 90., .02, .04, .62, .06, .08, -.5, -.4, -.3])
    a = RawData(t, np.ones(t.size), None, 1., source="recording.csv", exported_dff=ratio.copy(),
                channel_names={"exported_dff": "R_1"})
    cfg = DedriftConfig(signal_start_s=10., signal_end_s=26., center_window_s=4.)
    b = dedrift(a, cfg)
    selected = (t >= 10) & (t <= 26)
    expected = [np.median(ratio[selected & (np.abs(t - ti) <= 2.)]) for ti in t[selected]]
    np.testing.assert_allclose(b.dff_center_baseline, expected)
    np.testing.assert_allclose(b.dff, ratio[selected] - expected)
    np.testing.assert_array_equal(a.exported_dff, ratio)
    assert b.unit == "ratio" and b.method == "Plexon R_1"
    assert b.dff_unfiltered is None
    restored = load_stage(save_stage(tmp_path / "session.h5", "B", b), "B")
    np.testing.assert_array_equal(restored.dff_center_baseline, b.dff_center_baseline)
    formula = format_dff_formula(a, restored, "en")
    assert "R_1(t)" in formula and "median(X(u)" in formula and "2 s" in formula
    report = _preprocess(a, b, cfg, "en")
    assert report["parameters"]["formula_info"]["center_window_s"] == 4.
    assert "pandas · Series.rolling" in report["references"]
    assert PipelineConfig.from_dict({}).dedrift.center_window_s == 0.
    np.testing.assert_array_equal(dedrift(a, DedriftConfig()).dff, ratio)


def test_filtered_and_oasis_input_share_baseline_before_z_scoring():
    t = np.arange(200) / 10.
    sig = 20. + .5*t + np.sin(2*t) + np.sin(20*t)
    a = RawData(t, sig, None, 10., channel_names={"signal": "green"})
    cfg = DedriftConfig(method="signal_interval", baseline_start_s=0., baseline_end_s=2.,
                        lowpass_hz=1., downsample_hz=None)
    original = dedrift(a, cfg)
    centered = dedrift(a, replace(cfg, center_window_s=4.))
    baseline = [np.median(original.dff[np.abs(t-ti) <= 2 + 1e-9]) for ti in t]
    np.testing.assert_allclose(centered.dff_center_baseline, baseline)
    np.testing.assert_allclose(centered.reference, original.reference)
    np.testing.assert_allclose(centered.dff, original.dff - baseline)
    np.testing.assert_allclose(centered.dff_unfiltered, original.dff_unfiltered - baseline)
    z = dedrift(a, replace(cfg, center_window_s=4., output="zscore"))
    expected = (centered.dff - np.median(centered.dff)) / centered.dff.std()
    np.testing.assert_allclose(z.dff, expected)
    assert "z(t) = (Y(t)" in format_dff_formula(a, z, "en")


def test_slow_drift_and_short_response_in_preprocessing_figure():
    from matplotlib import pyplot as plt
    from fiberphoto.plotting import fig_dedrift

    t = np.arange(1200) / 10.
    ratio = .2 + .0002*t
    response = (t >= 54) & (t < 55)
    ratio[response] += .08
    a = RawData(t, np.ones(t.size), None, 10., exported_dff=ratio, channel_names={"exported_dff": "R_1"})
    b = dedrift(a, DedriftConfig(center_window_s=20.))
    np.testing.assert_allclose(b.dff[(t > 15) & (t < 40)], 0., atol=1e-12)
    np.testing.assert_allclose(b.dff[response], .08, atol=.001)
    fig = fig_dedrift(b, lang="en")
    try:
        assert "20 s rolling median correction" in fig.axes[0].get_title(loc="left")
    finally:
        plt.close(fig)


@pytest.mark.parametrize("window", [-1., np.nan, .01])
def test_invalid_center_windows(window):
    t = np.arange(20) / 10.
    a = RawData(t, t + 1, None, 10., exported_dff=t.copy(), channel_names={"exported_dff": "R_1"})
    with pytest.raises(ValueError, match="window"):
        dedrift(a, DedriftConfig(center_window_s=window))
