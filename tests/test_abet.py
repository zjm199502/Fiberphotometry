import json

import numpy as np
import pandas as pd
import pytest

from fiberphoto.abet import (
    AbetConfig, align_abet, export_abet, read_abet, resample_calcium, CUE_ALL, CUE_SPECIAL,
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
    r = align_abet(session, AbetConfig(), np.linspace(10, 150, 1401))
    assert r.markers[CUE_RESPONDED].tolist() == [52]
    assert r.control_onsets_s.tolist() == [37.]
    assert np.array_equal(r.trials.cue_on_calcium_s, r.trials.cue_on_s)
    export_abet(r, tmp_path)
    report = json.loads((tmp_path / "abet_alignment.json").read_text(encoding="utf-8"))
    assert report["span_difference_s"] == pytest.approx(140 - 127.8)
    assert report["mode"] == "duration_resample"
    assert len(pd.read_csv(tmp_path / "abet_trials.csv")) == 4


def test_traces_and_events_keep_their_relative_positions(abet_xml):
    from fiberphoto.data import RawData
    t = np.arange(1001) / 10 + 2
    s = read_abet(abet_xml)
    result = align_abet(s, AbetConfig(), t)
    assert result.markers[CUE_SPECIAL].tolist() == [3.]
    assert result.markers[CUE_TIMEOUT].tolist() == [72.]
    assert result.markers[TRAY_FEEDER].tolist() == [60.]
    assert result.markers[TRAY_OUTSIDE].tolist() == [37.]
    assert result.marker_offsets[CUE_TIMEOUT].tolist() == [78.]
    raw = RawData(t, t + 5, 2*t, 10., exported_dff=t/100, exported_f0=3*t,
                  markers={"TTL": np.array([2., 52., 102.])},
                  marker_offsets={"TTL": np.array([3., 53., np.nan])},
                  behavior=t, behavior_info={"method": "previous"},
                  behavior_native={"time": t, "values": t})
    aligned = resample_calcium(raw, result)
    assert aligned.time[[0, -1]].tolist() == [.2, 128.]
    assert aligned.fs == pytest.approx(10.)
    assert len(aligned.time) == 1279
    native_query = np.linspace(2, 102, 1279)
    for column, expected in (("signal", native_query+5), ("control", 2*native_query),
                             ("exported_f0", 3*native_query), ("exported_dff", native_query/100)):
        np.testing.assert_allclose(getattr(aligned, column), expected)
    np.testing.assert_allclose(aligned.markers["TTL"], [.2, 64.1, 128])
    assert np.isnan(aligned.marker_offsets["TTL"][-1])
    assert aligned.behavior.shape == aligned.time.shape and aligned.behavior[1] == 2
    assert raw.time is t and len(raw.exported_dff) == 1001


def test_downsampling_removes_aliases_and_preserves_acquisition_gaps(abet_xml):
    from dataclasses import replace
    from fiberphoto.data import RawData
    session = replace(read_abet(abet_xml), sync_pulses=(4., 14.))
    source = np.linspace(5, 25, 401)
    x = source - source[0]
    slow = np.sin(2*np.pi*x)
    # Under twofold compression 7 Hz exceeds the 5 Hz source-domain Nyquist.
    both = slow + .8*np.sin(2*np.pi*7*x)
    raw = RawData(source, both, None, 20)
    result = align_abet(session, AbetConfig(), source)
    output = resample_calcium(raw, result)
    assert len(output.time) == 201
    np.testing.assert_allclose(output.signal, slow[::2], atol=1e-11)
    mask = (source < 10) | (source > 11)
    y = both[mask].copy()
    y[(source[mask] > 13) & (source[mask] < 14)] = np.nan
    gapped = RawData(source[mask], y, None, 20)
    output = resample_calcium(gapped, align_abet(session, AbetConfig(), gapped.time))
    q = (output.time - 4)*2 + 5
    assert np.isnan(output.signal[(q > 10) & (q < 11)]).all()
    assert np.isnan(output.signal[(q > 13.1) & (q < 13.9)]).all()


@pytest.mark.parametrize("pulses", [(), (.1,), (0., 1., 2.), (1., 1.), (2., 1.), (0., np.inf)])
def test_requires_two_distinct_pulse_edges(abet_xml, pulses):
    from dataclasses import replace
    with pytest.raises(ValueError, match="start #1"):
        align_abet(replace(read_abet(abet_xml), sync_pulses=pulses), AbetConfig(), [0., 10.])


@pytest.mark.parametrize("times", [[], [1], [0, np.inf], [1, 1], [2, 1], [[0, 1]]])
def test_requires_a_monotonic_source_clock(abet_xml, times):
    with pytest.raises(ValueError):
        align_abet(read_abet(abet_xml), AbetConfig(), times)


def test_reject_other_xml_and_missing_abet_events(tmp_path):
    path = tmp_path / "other.xml"
    for text in ("<Data/>", "<AbetSession/>", "<AbetSession"):
        path.write_text(text)
        with pytest.raises(ValueError):
            read_abet(path)
