import numpy as np
import pytest

from fiberphoto.config import DedriftConfig, PipelineConfig
from fiberphoto.data import RawData
from fiberphoto.preprocess import dedrift, lowpass, signal_interval_baseline


def recording():
    time = 30.0 + np.arange(2000) / 100.0
    signal = np.full(time.shape, 100.0)
    signal[:100] = 10.
    signal[100:250] = 25.
    raw = RawData(time, signal, np.linspace(-4, 3, len(time)), 100.)
    raw.exported_dff = np.full(time.shape, .02)
    raw.exported_f0 = np.full(time.shape, 90.)
    raw.channel_names = {"signal": "RawF_1", "exported_dff": "R_1"}
    return raw


def test_interval_uses_raw_not_preview_control_or_device_ratio():
    raw = recording()
    raw.channel_names["preview_signal"] = "R_1"
    cfg = DedriftConfig(method="signal_interval", baseline_start_s=30.0, baseline_end_s=30.99,
                        auto_trim=True, lowpass_hz=None, downsample_hz=None)
    b = dedrift(raw, cfg)
    assert (b.method, b.unit) == ("signal_interval", "%")
    np.testing.assert_array_equal(b.time, raw.time)
    np.testing.assert_array_equal(b.reference, np.full(len(raw.time), 10.0))
    expected = (raw.signal - 10.) / 10. * 100
    np.testing.assert_array_equal(b.dff, expected)
    np.testing.assert_array_equal(b.dff_unfiltered, expected)
    np.testing.assert_array_equal(raw.exported_dff, .02)
    assert dedrift(raw, DedriftConfig()).method == "Plexon R_1"


def test_interval_closed_median_finite_and_clocks():
    a = recording()
    a.signal[:5] = [12, 5, np.nan, 1500, 27]
    f0, n = signal_interval_baseline(a, a.time[0], a.time[4])
    assert (f0, n) == ((12 + 27) / 2, 4)
    with pytest.raises(ValueError, match="inside the recording"):
        signal_interval_baseline(a, 0., .5)
    with pytest.raises(ValueError, match="two finite"):
        signal_interval_baseline(a, a.time[1], a.time[2])


@pytest.mark.parametrize("start,end", [(31., 30.), (31., 31.), (np.nan, 43.), (30., np.inf), (30., 50.)])
def test_invalid_interval(start, end):
    with pytest.raises(ValueError, match="interval"):
        signal_interval_baseline(recording(), start, end)


def test_reject_zero_or_negative_f0():
    raw = recording()
    raw.signal[:50] = -2.
    with pytest.raises(ValueError, match="positive"):
        dedrift(raw, DedriftConfig(method="signal_interval", baseline_start_s=30, baseline_end_s=30.49))


@pytest.mark.parametrize("output", ["dff", "zscore"])
def test_filters_trim_bin_average_and_unfiltered_deconvolution(output):
    raw = recording()
    raw.signal += np.sin(np.arange(len(raw.time)) * .75)
    start, end = raw.time[10], raw.time[70]
    f0 = np.median(raw.signal[10:71])
    cfg = DedriftConfig(method="signal_interval", baseline_start_s=start, baseline_end_s=end,
                        trim_start_s=1., trim_end_s=.5, downsample_hz=25, lowpass_hz=6., output=output)
    # Deliberate trim removes baseline segment from output but leaves its original F0.
    result = dedrift(raw, cfg)
    clipped = raw.signal[100:-50]
    expected = lowpass(clipped, 100., 6.)[:1848].reshape(-1, 4).mean(axis=1)
    unfiltered = clipped[:1848].reshape(-1, 4).mean(axis=1)
    expected = 100. * (expected - f0) / f0
    unfiltered = 100. * (unfiltered - f0) / f0
    if output == "zscore":
        centre, scale = np.median(expected), np.std(expected)
        expected, unfiltered = (expected - centre) / scale, (unfiltered - centre) / scale
        assert result.unit == "z"
    np.testing.assert_allclose(result.reference, f0)
    np.testing.assert_allclose(result.dff, expected)
    np.testing.assert_allclose(result.dff_unfiltered, unfiltered)
    assert not np.allclose(result.dff, result.dff_unfiltered)
    assert result.time[0] > end
    assert result.fs == 25.


def test_signal_interval_parameters_roundtrip_and_old_config():
    cfg = PipelineConfig.from_dict({"dedrift": {"method": "signal_interval",
                                                "baseline_start_s": 102., "baseline_end_s": 113.}})
    assert cfg.to_dict()["dedrift"]["baseline_start_s"] == 102.
    assert cfg.to_dict()["dedrift"]["baseline_end_s"] == 113.
    assert PipelineConfig.from_dict({"dedrift": {"method": "isosbestic"}}).dedrift.method == "isosbestic"


def test_signal_window_plexon_native_clock_behavior_and_bounds():
    raw = recording()
    # R may contain bad frames outside the selected acquisition interval.
    raw.exported_dff[:30] = np.nan
    raw.behavior = 2 * raw.time
    raw.behavior_native = {"time": raw.time.copy(), "values": raw.behavior.copy()}
    raw.behavior_info = {"value_column": "speed", "path": "speed.csv", "method": "linear"}
    cfg = DedriftConfig(signal_start_s=raw.time[40], signal_end_s=raw.time[210],
                        trim_start_s=19., trim_end_s=14., auto_trim=True)
    b = dedrift(raw, cfg)
    assert b.method == "Plexon R_1"
    assert (b.signal_start_s, b.signal_end_s) == (raw.time[40], raw.time[210])
    np.testing.assert_array_equal(b.time, raw.time[40:211])
    np.testing.assert_array_equal(b.dff, raw.exported_dff[40:211])
    np.testing.assert_array_equal(b.reference, raw.exported_f0[40:211])
    np.testing.assert_array_equal(b.signal, raw.signal[40:211])
    np.testing.assert_allclose(b.behavior, 2 * b.time)
    assert len(raw.time) == 2000


def test_disjoint_signal_and_baseline_and_no_independent_reference():
    raw = recording()
    raw.control = None
    cfg = DedriftConfig(method="signal_interval", baseline_start_s=raw.time[0], baseline_end_s=raw.time[50],
                        signal_start_s=raw.time[175], signal_end_s=raw.time[300], trim_end_s=19,
                        lowpass_hz=None, downsample_hz=None)
    b = dedrift(raw, cfg)
    np.testing.assert_array_equal(b.time, raw.time[175:301])
    np.testing.assert_array_equal(b.reference, np.full(126, 10.0))
    np.testing.assert_array_equal(b.dff, 100 * (raw.signal[175:301] / 10.0 - 1))


def test_signal_window_raw_fit_ignores_unselected_baseline_and_saves_bounds(tmp_path):
    from fiberphoto.data import save_stage, load_stage
    raw = recording()
    raw.control = None
    raw.exported_dff = None
    cfg = DedriftConfig(method="polynomial", signal_start_s=raw.time[500], signal_end_s=raw.time[700],
                        baseline_start_s=0, baseline_end_s=0, poly_order=1, lowpass_hz=None,
                        downsample_hz=50.)
    b = dedrift(raw, cfg)
    assert b.method == "polynomial" and b.fs == 50
    np.testing.assert_allclose(b.time, raw.time[500:700].reshape(-1, 2).mean(axis=1))
    np.testing.assert_allclose(b.dff, 0., atol=1e-12)
    restored = load_stage(save_stage(tmp_path / "b.h5", "B", b), "B")
    assert (restored.signal_start_s, restored.signal_end_s) == (raw.time[500], raw.time[700])


@pytest.mark.parametrize("start,end", [(30., None), (None, 31.), (30., 30.), (29., 31.),
                                       (30., 80.), (30.002, 30.009)])
def test_signal_window_requires_two_bounds_and_covered_samples(start, end):
    with pytest.raises(ValueError, match="interval|samples"):
        dedrift(recording(), DedriftConfig(signal_start_s=start, signal_end_s=end))
