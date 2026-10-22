import json

import numpy as np
import pytest

from fiberphoto.config import ChannelConfig, PipelineConfig
from fiberphoto.data import load_stage
from fiberphoto.io_tdms import auto_map, inspect_tdms, read_tdms
from fiberphoto.pipeline import run_batch, run_file
from fiberphoto.preprocess import dedrift, deconvolve
from fiberphoto.sample_data import make_sample_dataset, simulate, write_tdms


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sample")
    paths = make_sample_dataset(folder, n_animals=2, duration_s=300)
    return folder, paths


def truth(path):
    return json.loads(path.with_name(path.stem + "_ground_truth.json").read_text())


def test_inspect_guesses_roles(dataset):
    _, paths = dataset
    roles = {c.name: c.guess for c in inspect_tdms(paths[0])}
    assert roles == {"470nm": "signal", "405nm": "control", "DI1_Stim": "marker", "DI2_Lick": "marker"}
    cfg = auto_map(inspect_tdms(paths[0]), ChannelConfig())
    assert (cfg.signal, cfg.control) == ("470nm", "405nm")


def test_read_markers_match_truth(dataset):
    _, paths = dataset
    a = read_tdms(paths[0], ChannelConfig())
    assert a.fs == pytest.approx(100.0)
    t = truth(paths[0])
    # TTL edges are sample-quantised, so allow one sample of error
    np.testing.assert_allclose(a.markers["DI1_Stim"], t["stimulus_onsets_s"], atol=0.011)
    assert len(a.markers["DI2_Lick"]) == len(t["lick_onsets_s"])


def test_marker_csv_route(dataset):
    folder, paths = dataset
    cfg = ChannelConfig(markers=["none"], marker_file=str(folder / "mouse01_session1_markers.csv"))
    a = read_tdms(paths[0], cfg)
    assert list(a.markers) == ["tone"]
    np.testing.assert_allclose(a.markers["tone"], truth(paths[0])["stimulus_onsets_s"], atol=1e-3)


@pytest.mark.parametrize("method", ["isosbestic", "biexponential", "polynomial", "highpass"])
def test_dedrift_removes_bleaching(dataset, method):
    _, paths = dataset
    a = read_tdms(paths[0], ChannelConfig())
    cfg = PipelineConfig().dedrift
    cfg.method = method
    b = dedrift(a, cfg)
    assert b.fs == pytest.approx(20.0)
    # median ΔF/F in the first and last minute should agree within a few %
    n = int(60 * b.fs)
    assert abs(np.median(b.dff[:n]) - np.median(b.dff[-n:])) < 3.0


def test_no_control_falls_back(tmp_path):
    sim = simulate(duration_s=120, seed=3)
    sim["control"] = sim["control"][:0]
    p = write_tdms(tmp_path / "noctl.tdms", sim)
    a = read_tdms(p, ChannelConfig())
    assert a.control is None and a.signal.size == 12000
    b = dedrift(a, PipelineConfig().dedrift)
    assert b.method == "biexponential"


@pytest.mark.parametrize("method,min_precision", [("oasis", 0.75), ("peak", 0.9)])
def test_event_detection_recovers_truth(dataset, tmp_path, method, min_precision):
    _, paths = dataset
    cfg = PipelineConfig()
    cfg.events.method = method
    res = run_file(paths[0], cfg, tmp_path, make_figures=False)
    assert not res.error
    t = truth(paths[0])
    true_times = np.sort(np.r_[t["spontaneous_event_times_s"], t["stimulus_onsets_s"]])
    found = res.events.events["time_s"].to_numpy()
    # precision: detections lying within 1.5 s after a true event
    # (OASIS spikes at the default 0.8 σ threshold trade some precision for sensitivity)
    hits = [np.any((found_t - true_times >= -0.3) & (found_t - true_times < 1.5)) for found_t in found]
    assert np.mean(hits) > min_precision
    # every stimulus-evoked response should be detected
    stim_hit = [np.any((found >= s - 0.3) & (found < s + 2.0)) for s in t["stimulus_onsets_s"]]
    assert np.mean(stim_hit) > 0.9


def test_peth_shows_response(dataset, tmp_path):
    _, paths = dataset
    res = run_file(paths[0], PipelineConfig(), tmp_path, make_figures=False)
    p = res.peth
    assert p.trials.shape[0] == len(truth(paths[0])["stimulus_onsets_s"])
    for kind in ("z", "dff"):
        assert (p.metrics[f"auc_target_{kind}"] > p.metrics[f"auc_baseline_{kind}"]).mean() > 0.9
    assert p.trials_dff.shape == p.trials_z.shape
    # z-scores are relative to each trial's own baseline window
    np.testing.assert_allclose(p.metrics["baseline_mean_z"], 0, atol=1e-9)
    assert 0.2 < p.metrics["peak_latency_s"].median() < 2.0


def test_outputs_and_backups(dataset, tmp_path):
    folder, _ = dataset
    cfg = PipelineConfig()
    cfg.plot.formats = ["png"]
    cfg.plot.dpi = 72
    results = run_batch([folder], cfg, tmp_path)
    assert len(results) == 2 and not any(r.error for r in results)
    out = results[0].out_dir
    for f in ["summary.json", "parameters.json", "events.csv", "peth_metrics.csv",
              "peth_trials_z.csv", "peth_trials_dff.csv", f"{out.name}_dataB.csv",
              "figures/04_peth_heatmap_z.png", "figures/04_peth_heatmap_dff.png", "figures/05_auc_baseline_target.png"]:
        assert (out / f).exists(), f
    assert (tmp_path / "batch_summary.csv").exists()
    h5 = out / f"{out.name}.h5"
    a, b, c = (load_stage(h5, s) for s in "ABC")
    np.testing.assert_allclose(b.dff, results[0].B.dff)
    np.testing.assert_allclose(c.denoised, results[0].C.denoised)
    assert set(a.markers) == {"DI1_Stim", "DI2_Lick"}


def test_config_roundtrip(tmp_path):
    cfg = PipelineConfig()
    cfg.dedrift.method = "polynomial"
    cfg.peth.pre_s = 3
    cfg.channels.markers = ["DI1"]
    cfg.save(tmp_path / "p.json")
    back = PipelineConfig.load(tmp_path / "p.json")
    assert back == cfg


def test_deconv_ar2(dataset):
    _, paths = dataset
    a = read_tdms(paths[0], ChannelConfig())
    b = dedrift(a, PipelineConfig().dedrift)
    cfg = PipelineConfig().deconv
    cfg.model = "ar2"
    c = deconvolve(b, cfg)
    assert len(c.g) == 2 and np.all(c.spikes >= 0)
