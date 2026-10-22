"""Quirks seen in real recordings: LED switched on late, no control, unnamed channels."""
import warnings

import numpy as np
import pytest

from fiberphoto.config import ChannelConfig, PipelineConfig
from fiberphoto.data import RawData
from fiberphoto.preprocess import DataWarning, dedrift, detect_led_onset
from fiberphoto.sample_data import simulate


def led_late(onset_s=25.0, fs=50.0):
    sim = simulate(duration_s=300, fs=fs, seed=7)
    sig = sim["signal"].copy()
    n0 = int(onset_s * fs)
    sig[:n0] = 0.3 * sig[:n0].mean() + np.random.default_rng(0).normal(0, 0.005, n0)
    return sim, sig


def test_detect_led_onset():
    sim, sig = led_late()
    assert detect_led_onset(sig, 50.0) == pytest.approx(26.0, abs=0.5)
    assert detect_led_onset(sim["signal"], 50.0) == 0.0


def test_dedrift_warns_and_trims():
    sim, sig = led_late()
    raw = RawData(time=sim["t"], signal=sig, control=None, fs=50.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DataWarning)
        b = dedrift(raw, PipelineConfig().dedrift)
    msgs = [str(w.message) for w in caught]
    assert b.time[0] >= 25.0
    assert b.method == "biexponential"
    assert any("Light source off" in m for m in msgs) and any("No control channel" in m for m in msgs)


def test_negative_baseline_warns():
    sim = simulate(duration_s=120, fs=50.0, seed=8)
    raw = RawData(time=sim["t"], signal=-sim["signal"], control=None, fs=50.0)
    cfg = PipelineConfig().dedrift
    cfg.method = "polynomial"
    with pytest.warns(DataWarning, match="negative"):
        dedrift(raw, cfg)


def test_align_to_ttl_offset(tmp_path):
    """Stimulus delivered when the 1 s TTL ends: align to the falling edge."""
    from fiberphoto.io_tdms import read_tdms
    from fiberphoto.pipeline import pick_marker
    from fiberphoto.sample_data import write_tdms

    sim = simulate(duration_s=120, n_stim=5, seed=4)
    a = read_tdms(write_tdms(tmp_path / "r.tdms", sim), ChannelConfig())
    _, on = pick_marker(a, "DI1_Stim")
    _, off = pick_marker(a, "DI1_Stim", "offset")
    _, shifted = pick_marker(a, "DI1_Stim", "offset", 0.5)
    np.testing.assert_allclose(off - on, 1.0, atol=0.011)
    np.testing.assert_allclose(shifted - off, 0.5)


def test_oasis_spike_events_threshold_modes():
    from fiberphoto.analysis import detect_events
    from fiberphoto.config import EventConfig
    from fiberphoto.data import DeconvData

    t = np.arange(200) / 20.0
    s = np.zeros(200)
    s[[20, 21, 80, 150]] = [2.0, 1.0, 0.5, 4.0]
    c = DeconvData(time=t, denoised=np.zeros(200), spikes=s, fs=20.0, baseline=0.0, g=(0.95,), lam=0.0, sn=1.0)
    ev = detect_events(c, None, EventConfig(spike_threshold=0.8))           # 0.8 × σ = 0.8
    assert list(ev.events["time_s"]) == [1.0, 7.5]
    assert ev.events["spike_sum"].tolist() == [3.0, 4.0]                    # adjacent frames merged
    ev = detect_events(c, None, EventConfig(spike_threshold=0.4, spike_threshold_mode="absolute"))
    assert len(ev.events) == 3
    ev = detect_events(c, None, EventConfig(spike_threshold=0.8, spike_threshold_mode="max"))
    assert list(ev.events["time_s"]) == [7.5]


def test_spike_threshold_is_monotonic(tmp_path):
    """Raising the threshold must never create events (plateaus used to split)."""
    from fiberphoto.analysis import detect_events
    from fiberphoto.io_tdms import read_tdms
    from fiberphoto.preprocess import deconvolve
    from fiberphoto.sample_data import write_tdms

    cfg = PipelineConfig()
    a = read_tdms(write_tdms(tmp_path / "m.tdms", simulate(duration_s=300, seed=11)), cfg.channels)
    b = dedrift(a, cfg.dedrift)
    c = deconvolve(b, cfg.deconv)
    counts = []
    for thr in (0.5, 0.8, 1.5, 3, 5, 8):
        cfg.events.spike_threshold = thr
        ev = detect_events(c, b, cfg.events)
        counts.append(len(ev.events))
        assert (ev.events["amplitude"] <= b.dff.max() - b.dff.min()).all()
    assert counts == sorted(counts, reverse=True)
    # noise is estimated on the unfiltered trace, so it is not shrunk by the low-pass
    assert c.sn > 0.5 * np.std(np.diff(b.dff_unfiltered)) / np.sqrt(2)


def test_windows_are_user_set_in_seconds():
    from fiberphoto.analysis import peri_event
    from fiberphoto.config import PethConfig
    from fiberphoto.data import DffData

    fs = 10.0
    t = np.arange(0, 100, 1 / fs)
    dff = np.where((t % 20 >= 10) & (t % 20 < 12), 5.0, 0.0) + np.random.default_rng(1).normal(0, 0.1, t.size)
    b = DffData(time=t, dff=dff, fs=fs, signal=dff, reference=dff, method="x", unit="%")
    cfg = PethConfig(pre_s=5, post_s=5, baseline_start_s=-4, baseline_end_s=-1,
                     response_start_s=0, response_end_s=2)
    p = peri_event(b, np.arange(10, 100, 20.0), cfg)
    m = p.metrics
    np.testing.assert_allclose(m["target_mean_dff"], 5.0, atol=0.3)
    np.testing.assert_allclose(m["auc_target_dff"], 10.0, atol=1.0)   # 5 %·2 s
    np.testing.assert_allclose(m["auc_baseline_dff"], 0.0, atol=0.5)
    assert (m["peak_z"] > 20).all()
