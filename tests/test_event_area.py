import numpy as np
import pandas as pd
import pytest

from fiberphoto.analysis import detect_events
from fiberphoto.config import EventConfig
from fiberphoto.data import DffData, DeconvData


def traces(signal, spikes=None, time=None):
    y = np.asarray(signal, dtype=float)
    t = np.arange(len(y), dtype=float) if time is None else np.array(time, dtype=float)
    b = DffData(t, y, 1., y, None, "device", "ratio")
    c = DeconvData(t, y, np.zeros_like(y) if spikes is None else np.array(spikes, dtype=float),
                   1., 0., (.95,), 0., sn=1.)
    return b, c


def test_oasis_area_uses_delayed_peak_and_ends_at_next_group():
    # First peak lags its spike. The next group reuses the final boundary sample.
    b, c = traces([0, 1, 4, 2, 1, 3, 2, 0], [0, 1, 0, 0, 0, 2, 0, 0])
    events = detect_events(c, b, EventConfig()).events
    np.testing.assert_allclose(events.peak_time_s, [2, 5])
    np.testing.assert_allclose(events.amplitude, [4, 2])
    np.testing.assert_allclose(events.baseline, [0, 1])
    np.testing.assert_allclose(events.auc_start_s, [0, 4])
    np.testing.assert_allclose(events.auc_end_s, [4, 6.5])
    np.testing.assert_allclose(events.auc, [7.5, 2.75])


def test_peak_area_interpolates_crossings_on_acquisition_clock():
    b, c = traces([0, -1, 1, 3, 1, -1, 0, 0, 0], time=[0, 1, 2, 3, 4, 7, 8, 9, 10])
    event, = detect_events(c, b, EventConfig(method="peak", threshold_mad=1, min_prominence_mad=1)).events.itertuples()
    assert event.baseline == 0 and event.amplitude == 3
    assert event.auc_start_s == 1.5 and event.auc_end_s == 5.5
    assert event.auc == pytest.approx(5.0)


def test_adjacent_peaks_split_at_valley_and_clip_at_recording_edge():
    b, c = traces([0, 0, 0, 0, 0, 0, 0, 2, 4, 2, 5, 1])
    cfg = EventConfig(method="peak", threshold_mad=1, min_prominence_mad=1)
    result = detect_events(c, b, cfg)
    e = result.events
    np.testing.assert_allclose(e.auc_start_s, [6, 9])
    np.testing.assert_allclose(e.auc_end_s, [9, 11])
    assert e.auc.sum() == pytest.approx(13.5)


@pytest.mark.parametrize("method", ["oasis", "peak"])
def test_empty_event_export_keeps_measurement_headers(method, tmp_path):
    b, c = traces([0, 0, 0, 0])
    result = detect_events(c, b, EventConfig(method=method))
    path = tmp_path / "events.csv"
    result.to_csv(path, b.unit)
    table = pd.read_csv(path)
    assert len(table) == 0
    assert {"amplitude", "auc", "baseline", "auc_start_s", "auc_end_s", "amplitude_unit", "auc_unit"} <= set(table)
