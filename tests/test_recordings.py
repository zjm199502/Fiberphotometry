"""Device-layout regression tests for the normal pipeline, GUI and folder entry points."""
import h5py
import numpy as np
import pandas as pd
import pytest

from fiberphoto.config import ChannelConfig, PipelineConfig
from fiberphoto.pipeline import find_recordings, run_batch
from fiberphoto.recordings import inspect_recording, read_recording
from fiberphoto.sample_data import simulate


def plexon_file(folder):
    sim = simulate(duration_s=180, fs=50)
    t = sim["t"]
    path = folder / "Session_PhotoData.csv"
    pd.DataFrame({"Frame": np.arange(len(t)), "timestamp": t,
                  "RawF_465nm_F1_123pix": sim["signal"], "F0_465_F1": 4,
                  "RawF_410nm_F1_123pix": sim["control"],
                  "RawF_465nm_F2_215pix": sim["signal"] + 3,
                  "RawF_410nm_F2_215pix": sim["control"] + 1,
                  "Stim_465nm": sim["ttl_stim"]}).to_csv(path, index=False)
    return path, sim


def test_plexon_batch_discovery_and_mapping(tmp_path):
    path, sim = plexon_file(tmp_path)
    events = tmp_path / "events.csv"
    pd.DataFrame({"Frame": [1, 2, 3], "timestamp": [0, .02, .04],
                  "Input1": [0, 1, 0]}).to_csv(events, index=False)
    cfg = PipelineConfig(channels=ChannelConfig(group="F2", marker_file=str(events)))
    raw = read_recording(path, cfg.channels)
    np.testing.assert_allclose(raw.signal, sim["signal"] + 3)
    np.testing.assert_allclose(raw.control, sim["control"] + 1)
    assert raw.fs == pytest.approx(50)
    np.testing.assert_allclose(raw.markers["Input1"], [.02])
    np.testing.assert_allclose(raw.marker_offsets["Input1"], [.04])
    assert "F0_465_F1" not in [c.name for c in inspect_recording(path)]
    cfg.channels.marker_file = ""
    cfg.plot.formats, cfg.plot.dpi = ["png"], 72
    result, = run_batch([tmp_path], cfg, tmp_path / "results")
    assert not result.error and result.peth.trials.size and result.C is not None
    assert find_recordings(tmp_path) == [path]


def test_npm_demultiplex_preserves_clock(tmp_path):
    path = tmp_path / "NPM.csv"
    t = np.arange(11) / 100
    flags = [7, 1, 6, 1, 22, 17, 6, 1, 6, 1, 6]
    pd.DataFrame({"Timestamp": t, "Flags": flags, "Region0G": t + 5,
                  "Region1R": t + 7}).to_csv(path, index=False)
    raw = read_recording(path, ChannelConfig(group="Region0G"))
    assert raw.channel_names["signal"] == "470nm" and raw.fs == pytest.approx(50)
    np.testing.assert_allclose(raw.time, [.02, .04, .06, .08])
    np.testing.assert_allclose(raw.control, raw.time + 5)
    np.testing.assert_allclose(raw.markers["Flags bit 4"], [.04])
    red = read_recording(path, ChannelConfig(group="Region1R", auto_control=False))
    assert red.channel_names["signal"] == "560nm" and red.control is None
    np.testing.assert_allclose(red.signal, red.time + 7)


@pytest.mark.parametrize("store,channel", [("AIN01xAOUT02-LockIn", "Values"), ("LockInAOUT02", "AIN01")])
def test_doric_series_and_ttl_clock(tmp_path, store, channel):
    path = tmp_path / "recording.doric"
    with h5py.File(path, "w") as f:
        group = f.create_group("DataAcquisition/FPConsole/Signals/Series0002")
        signal = group.create_group(store)
        signal["Time"] = np.arange(10) * .1 + 4
        signal[channel] = np.arange(10)
        ref = group.create_group("LockInAOUT01")
        ref["Time"] = signal["Time"][()]
        ref["AIN01"] = np.arange(10) + 2
        ttl = group.create_group("DigitalIO")
        ttl["Time"] = 4 + np.arange(20) * .05
        ttl["DIO01"] = [0] * 3 + [1] * 3 + [0] * 14
    with pytest.raises(ValueError, match="wiring"):
        read_recording(path, ChannelConfig())
    raw = read_recording(path, ChannelConfig(signal=f"{store}:{channel}", control="LockInAOUT01:AIN01"))
    np.testing.assert_allclose(raw.control, np.arange(10) + 2)
    np.testing.assert_allclose(raw.markers["DigitalIO:DIO01"], [4.15])
    np.testing.assert_allclose(raw.marker_offsets["DigitalIO:DIO01"], [4.30])
    path.rename(tmp_path / "moved.doric")


def test_gui_import_plexon(qtbot, tmp_path):
    from fiberphoto.gui.main_window import MainWindow
    path, sim = plexon_file(tmp_path)
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    window.add_paths([path])
    window.cb_group.setCurrentText("F2")
    assert window.cb_control.currentData() == "RawF_410nm_F2_215pix"
    window.cb_control.setCurrentIndex(0)
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    np.testing.assert_allclose(window.A.signal, sim["signal"] + 3)
    assert window.A.control is None and window.fig_raw.canvas is not None
