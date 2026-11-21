"""Excluded channels stay local to one recording and are ignored by readers."""
import numpy as np
import pandas as pd
import pytest

from fiberphoto.config import ChannelConfig, PipelineConfig
from fiberphoto.recordings import read_recording


def plexon(path):
    t = np.arange(100) / 10.0
    pd.DataFrame({"time": t, "RawF_1": 10 + np.sin(t), "F0_1": 10.,
                  "R_1": np.sin(t) / 10., "Stim": (np.arange(100) % 20) < 5}).to_csv(path, index=False)
    return path


def test_exclusion_ui_roundtrip(qtbot, tmp_path):
    from fiberphoto.gui.main_window import MainWindow

    path = plexon(tmp_path / "a.csv")
    other = plexon(tmp_path / "b.csv")
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    window.add_paths([path, other])
    window.cb_signal.setCurrentText("R_1")
    window.cb_control.setCurrentText("F0_1")
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.exported_dff is not None
    original = path.read_bytes()

    def exclude(name):
        table = window.chan_table
        table.selectRow(next(i for i in range(table.rowCount()) if table.item(i, 1).text() == name))
        window.b_exclude.click()

    for name in ("R_1", "F0_1", "Stim"):
        exclude(name)
    assert window.A is None and window.cb_signal.currentText() == "RawF_1"
    assert window.cb_control.currentData() == "" and not window.lst_markers.count()
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.exported_dff is None and window.A.exported_f0 is None
    assert window.A.control is None and not window.A.markers
    settings = tmp_path / "params.json"
    window.forms_store().save(settings)
    cfg = PipelineConfig.load(settings)
    assert read_recording(path, cfg.channels).exported_dff is None
    another = read_recording(other, cfg.channels)
    assert another.exported_dff is not None and another.control is not None and "Stim" in another.markers
    window.file_list.setCurrentRow(1)
    assert window.cb_signal.findText("R_1") >= 0
    window.file_list.setCurrentRow(0)
    assert window.cb_signal.findText("R_1") < 0
    window.b_restore.click()
    assert window.cb_signal.findText("R_1") >= 0 and window.cb_control.findText("F0_1") >= 0
    exclude("RawF_1")
    assert window.cb_signal.count() == 0
    with pytest.raises(ValueError, match="fluorescence"):
        read_recording(path, window.forms_store().channels)
    assert path.read_bytes() == original


def test_recording_intervals_switch_save_and_batch(qtbot, tmp_path):
    from fiberphoto.gui.main_window import MainWindow
    from fiberphoto.pipeline import pick_marker, run_batch

    a_path = plexon(tmp_path / "a.csv")
    b_path = plexon(tmp_path / "b.csv")
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    window.add_paths([a_path, b_path])
    fields = window.form_dedrift.editors
    fields["method"].setCurrentIndex(fields["method"].findData("signal_interval"))
    fields["baseline_start_s"].setValue(0.5)
    fields["baseline_end_s"].setValue(1.5)
    fields["signal_start_s"].setValue(2)
    fields["signal_end_s"].setValue(6)
    window.file_list.setCurrentRow(1)
    assert fields["method"].currentData() != "signal_interval"
    assert fields["signal_start_s"].value() is None
    window.file_list.setCurrentRow(0)
    assert fields["baseline_start_s"].value() == 0.5
    assert fields["signal_end_s"].value() == 6
    settings = tmp_path / "session.json"
    window.forms_store().save(settings)
    cfg = PipelineConfig.load(settings)
    cfg.dedrift.auto_trim = False
    cfg.dedrift.lowpass_hz = None
    cfg.dedrift.downsample_hz = None
    cfg.deconv.enabled = False
    cfg.peth.pre_s = cfg.peth.post_s = 0.5
    cfg.plot.formats = []
    first, second = run_batch([a_path, b_path], cfg, tmp_path / "out", mapping_source=a_path)
    assert not first.error and not second.error
    assert (first.B.time[0], first.B.time[-1]) == (2, 6)
    assert first.B.method == "signal_interval"
    assert second.B.method == "Plexon R_1" and len(second.B.time) == 100
    np.testing.assert_allclose(first.A.markers["Stim"], [0, 2, 4, 6, 8])
    assert first.summary["n_markers"] == 3 and second.summary["n_markers"] == 5
    np.testing.assert_allclose(pick_marker(first.A, "Stim", processed=first.B)[1], [2, 4, 6])
    np.testing.assert_allclose(pick_marker(first.A, "Stim", "offset", .5, processed=first.B)[1], [3, 5])
    assert not PipelineConfig.load(second.out_dir / "parameters.json").intervals_by_recording


def test_tdms_excluded_group_not_picked_for_marker(tmp_path):
    from nptdms import ChannelObject, TdmsWriter

    path = tmp_path / "groups.tdms"
    ttl = np.array([0, 0, 1, 1, 0])
    props = {"wf_increment": .1}
    with TdmsWriter(path) as writer:
        writer.write_segment([ChannelObject("A", "470nm", np.arange(5), props),
                              ChannelObject("A", "405nm", np.arange(5) + 8, props),
                              ChannelObject("A", "DI1", ttl, props),
                              ChannelObject("B", "DI1", ttl[::-1], props)])
    cfg = ChannelConfig(excluded_channels={str(path.resolve()): {"A": ["405nm", "DI1"]}})
    raw = read_recording(path, cfg)
    assert raw.control is None
    np.testing.assert_allclose(raw.markers["DI1"], [.1])
    cfg.excluded_channels[str(path.resolve())]["B"] = ["DI1"]
    assert not read_recording(path, cfg).markers
