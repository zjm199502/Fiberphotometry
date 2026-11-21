import numpy as np
import pytest

from fiberphoto.behavior import (BehaviorConfig, BehaviorTrace, align_behavior,
                                behavior_columns, load_aligned_behavior, load_behavior_csv)


def test_timestamp_units_csv_selection_offset_and_overlap(tmp_path):
    path = tmp_path / "speed.csv"
    path.write_text("time ms,track,Speed\n500,mouse,0\n750,mouse,2\n1250,mouse,4\n", encoding="utf-8-sig")
    assert behavior_columns(path) == ["time ms", "track", "Speed"]
    cfg = BehaviorConfig(str(path), "Speed", time_column="time ms", time_unit="ms", offset_s=.25)
    # Transformed behavioral clock: [0.75, 1.0, 1.5]. Nonuniform original timestamps.
    target = np.array([.5, .75, .875, 1., 1.25, 1.5, 1.6])
    np.testing.assert_allclose(load_aligned_behavior(cfg, target), [np.nan, 0, 1, 2, 3, 4, np.nan])
    cfg.method = "previous"
    np.testing.assert_allclose(load_aligned_behavior(cfg, target), [np.nan, 0, 0, 2, 2, 4, np.nan])
    cfg.method = "nearest"
    np.testing.assert_allclose(load_aligned_behavior(cfg, target), [np.nan, 0, 0, 2, 2, 4, np.nan])


def test_align_starts_negative_offset_and_processed_clock():
    trace = BehaviorTrace(np.array([197.4, 197.9, 198.4]), np.array([2, 4, 8]), "speed", "b.csv")
    calcium = np.array([10., 10.25, 10.75, 11.])
    corrected = trace.aligned_time(calcium[0], align_starts=True, offset_s=-.25)
    np.testing.assert_allclose(corrected, [9.75, 10.25, 10.75])
    raw = align_behavior(trace, calcium, offset_s=-.25, align_starts=True)
    np.testing.assert_allclose(raw, [3., 4., 8., np.nan])
    on_clock = BehaviorTrace(corrected, trace.values, trace.value_column, trace.source)
    np.testing.assert_allclose(align_behavior(on_clock, calcium[1:]), raw[1:])
    np.testing.assert_allclose(trace.time, [197.4, 197.9, 198.4])


def test_value_only_and_headerless_preserve_first_sample_and_missing(tmp_path):
    path = tmp_path / "behavior.csv"
    path.write_text("1\n\n3\n4\n", encoding="utf-8")
    assert behavior_columns(path) == ["column_1"]
    trace = load_behavior_csv(path, sampling_rate_hz=10)
    np.testing.assert_equal(trace.time, [0, .1, .2, .3])
    np.testing.assert_equal(trace.values, [1, np.nan, 3, 4])
    np.testing.assert_allclose(align_behavior(trace, np.arange(0, .31, .05)),
                               [1, np.nan, np.nan, np.nan, 3, 3.5, 4])
    path.write_text("-1000,2\n1000,4\n", encoding="utf-8")
    assert behavior_columns(path) == ["column_1", "column_2"]
    trace = load_behavior_csv(path, "column_2", time_column="column_1", time_unit="us")
    np.testing.assert_allclose(trace.time, [-.001, .001])
    np.testing.assert_array_equal(trace.values, [2, 4])


@pytest.mark.parametrize("method", ["linear", "previous", "nearest"])
def test_single_native_sample_and_no_overlap(method):
    trace = BehaviorTrace(np.array([13.]), np.array([6.]), "speed", "example.csv")
    np.testing.assert_equal(align_behavior(trace, [12., 13., 14.], method=method), [np.nan, 6., np.nan])
    with pytest.raises(ValueError, match="overlap"):
        align_behavior(trace, [1., 2.], method=method)
    np.testing.assert_equal(align_behavior(trace, [1., 2.], method=method, require_overlap=False),
                            [np.nan, np.nan])


@pytest.mark.parametrize("data,kwargs,reason", [
    ("time,value\n0,1\n0,2\n", {"time_column": "time"}, "strictly increasing"),
    ("time,value\n2,1\n1,2\n", {"time_column": "time"}, "strictly increasing"),
    ("time,value\n,1\n", {"time_column": "time"}, "timestamps"),
    ("time,value\n2,1\n", {"time_column": "time", "time_unit": "min"}, "time unit"),
    ("time,value\n2,1\n", {"time_column": "value"}, "distinct"),
    ("time,value\n2,bad\n", {"time_column": "time"}, "Non-numeric"),
    ("time,value\n2,2,3\n", {"time_column": "time"}, "row width"),
    ("time,time\n2,2\n", {"time_column": "time"}, "Duplicate"),
    ("value\n3\n", {}, "sampling rate"),
    ("value\n3\n", {"sampling_rate_hz": 0}, "sampling rate"),
    ("value\ninf\n", {"sampling_rate_hz": 10}, "finite"),
    ("value\nNA\n", {"sampling_rate_hz": 1}, "finite"),
])
def test_csv_errors(tmp_path, data, kwargs, reason):
    path = tmp_path / "behavior.csv"
    path.write_text(data, encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        load_behavior_csv(path, "value", **kwargs)


def test_resampling_errors():
    trace = BehaviorTrace(np.arange(2.), np.array([np.nan, 2.]), "speed", "example.csv")
    with pytest.raises(ValueError, match="overlap"):
        align_behavior(trace, [.25, .5])
    with pytest.raises(ValueError, match="resampling method"):
        align_behavior(trace, [0, 1], method="last")
    with pytest.raises(ValueError, match="offset"):
        align_behavior(trace, [0, 1], offset_s=np.inf)
    with pytest.raises(ValueError, match="calcium"):
        align_behavior(trace, [1., 0.])
