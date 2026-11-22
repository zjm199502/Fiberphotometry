import json

import numpy as np
import pandas as pd
import pytest

from fiberphoto.abet import (
    AbetConfig, align_abet, export_abet, read_abet, CUE_ALL, CUE_SPECIAL,
    CUE_TIMEOUT, CUE_RESPONDED, TRAY_FEEDER, TRAY_OUTSIDE,
)


def test_native_trials_and_controls(abet_xml, tmp_path):
    session = read_abet(abet_xml)
    assert session.animal_id == "sample"
    assert session.sync_pulses == (.2, 128.)
    assert list(session.trials.outcome) == ["timeout", "responded", "timeout", "incomplete"]
    assert list(session.trials.regular)[:3] == [False, True, True]
    trial = session.trials.iloc[1]
    assert trial.tray_input_on_s == 60
    assert trial.tray_latency_from_window_open_s == 2
    assert trial.tray_xml_auto < trial.feeder_xml_auto
    r = align_abet(session, AbetConfig(), [10, 150])
    assert not r.markers and not r.marker_offsets
    assert r.info["offset_s"] is None
    assert r.control_onsets_s.tolist() == [37.]
    assert not any("calcium_s" in c for c in r.trials)
    export_abet(r, tmp_path)
    report = json.loads((tmp_path / "abet_alignment.json").read_text(encoding="utf-8"))
    assert report["span_difference_s"] == pytest.approx(140-127.8)
    assert len(pd.read_csv(tmp_path / "abet_trials.csv")) == 4


def test_clock_modes_and_bounds(abet_xml):
    s = read_abet(abet_xml)
    r = align_abet(s, AbetConfig(mode="assumed_trigger", latency_s=.4), [3, 160])
    assert r.info["scale"] == 1.
    assert r.info["first_frame_minus_mapped_start_s"] is None
    assert r.info["offset_s"] == pytest.approx(3 - .4 - .2)
    assert r.markers[CUE_SPECIAL] == pytest.approx([3 + 2.4])
    assert r.markers[CUE_RESPONDED] == pytest.approx([52 + 2.4])
    assert r.markers[CUE_TIMEOUT] == pytest.approx([72 + 2.4])
    assert r.markers[TRAY_FEEDER] == pytest.approx([60 + 2.4])
    assert r.markers[TRAY_OUTSIDE] == pytest.approx([37 + 2.4])
    assert r.marker_offsets[CUE_TIMEOUT] == pytest.approx([78 + 2.4])
    assert r.trials.loc[2, "cue_on_calcium_s"] == pytest.approx(74.4)
    assert len(r.markers[CUE_ALL]) == 4
    r = align_abet(s, AbetConfig(mode="affine", anchors=[[5, 20], [85, 100.8]]), [10, 104])
    assert r.info["scale"] == pytest.approx(1.01)
    assert r.info["first_frame_minus_mapped_start_s"] == pytest.approx(10 - (20 + (.2-5)*1.01))
    assert r.info["last_frame_minus_mapped_end_s"] == pytest.approx(104 - (20 + (128-5)*1.01))
    assert r.markers[CUE_ALL] == pytest.approx([20+(t-5)*1.01 for t in (3, 52, 72)])
    assert r.info["marker_counts_outside_calcium"][CUE_ALL] == 1
    r = align_abet(s, AbetConfig(mode="offset", anchors=[[3, 100]]), [100,102])
    assert r.info["scale"] == 1
    assert r.markers[CUE_ALL].tolist() == [100]
    assert np.isnan(r.marker_offsets[CUE_ALL][0])


@pytest.mark.parametrize("options", [
    {"mode": "affine", "anchors": [[1, 9], [2, 8]]},
    {"mode": "affine", "anchors": [[1, 3], [1, 8]]},
    {"mode": "affine", "anchors": [[1, 2], [129, 9]]},
    {"mode": "offset", "anchors": [[4, 11]]},
    {"mode": "offset", "anchors": [[4, np.nan]]},
    {"mode": "offset", "anchors": []},
    {"mode": "assumed_trigger", "latency_s": -1},
    {"mode": "behavior_only", "anchors": [[1, 5]]},
    {"control_gap_s": -1},
])
def test_invalid_anchors(abet_xml, options):
    with pytest.raises(ValueError):
        align_abet(read_abet(abet_xml), AbetConfig(**options), [0, 10])


def test_reject_other_xml_and_missing_abet_events(tmp_path):
    path = tmp_path / "other.xml"
    for text in ("<Data/>", "<AbetSession/>", "<AbetSession"):
        path.write_text(text)
        with pytest.raises(ValueError):
            read_abet(path)
