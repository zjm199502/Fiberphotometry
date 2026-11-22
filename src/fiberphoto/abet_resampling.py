"""Stretch acquisition time to the two ABET boundary pulses and sample on a uniform grid."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from scipy.fft import dst, idst

from .behavior import BehaviorTrace, align_behavior


DURATION_RESAMPLE = "duration_resample"


def grid_info(pulses, source_time):
    if len(pulses) != 2 or not np.isfinite(pulses).all() or pulses[1] <= pulses[0]:
        raise ValueError("须有两个依次递增的 start #1 上升沿 / Expected exactly two increasing start #1 rising edges")
    t = np.asarray(source_time, dtype=float)
    intervals = np.diff(t) if t.ndim == 1 else []
    if t.ndim != 1 or len(t) < 2 or not np.isfinite(t).all() or np.any(np.asarray(intervals) <= 0):
        raise ValueError("钙记录时间须有限且严格递增 / Calcium timestamps must be finite and strictly increasing")
    # Mean frame spacing avoids rounding errors in decimal timestamp exports.
    # Exclude acquisition gaps rather than using dropped frames to lower the estimated frame rate.
    adjacent = intervals[intervals <= 1.5 * np.median(intervals)]
    source_fs = 1.0 / adjacent.mean()
    start, end = map(float, pulses)
    span = end - start
    steps = max(1, round(span * source_fs))
    source_span = float(t[-1] - t[0])
    return {
        "source_first_s": float(t[0]), "source_last_s": float(t[-1]), "source_span_s": source_span,
        "source_samples": len(t), "source_fs_hz": float(source_fs),
        "aligned_first_s": start, "aligned_last_s": end, "aligned_span_s": span,
        "aligned_samples": steps + 1, "aligned_fs_hz": steps / span,
        "source_to_abet_scale": span / source_span,
        "span_difference_s": source_span - span,
        "formula": "t_ABET = start_pulse + (t_source - first_frame) * (end_pulse - start_pulse) / (last_frame - first_frame)",
        "grid_formula": "N = max(1, round(ABET_span * source_fs)) + 1; t = linspace(start_pulse, end_pulse, N)",
        "resampling": "Continuous fluorescence channels are linearly interpolated; downsampling first removes frequencies above the target Nyquist on each contiguous finite segment (linear detrending and a sine transform).",
        "gap_rule": "NaN samples and acquisition gaps > 1.5 source frame intervals are not bridged.",
    }


def source_to_behavior(value, info):
    v = np.asarray(value)
    mapped = info["aligned_first_s"] + (v - info["source_first_s"]) * info["source_to_abet_scale"]
    exact = np.where(v == info["source_last_s"], info["aligned_last_s"], mapped)
    return float(exact) if exact.ndim == 0 else exact


def behavior_to_source(value, info):
    v = np.asarray(value)
    mapped = info["source_first_s"] + (v - info["aligned_first_s"]) / info["source_to_abet_scale"]
    exact = np.where(v == info["aligned_last_s"], info["source_last_s"], mapped)
    return float(exact) if exact.ndim == 0 else exact


def _low_pass(t, y, step):
    """Band-limit a finite segment before compression while preserving its end values.

    The sine modes of a zero-ended signal have frequencies k / (2 * span).
    Subtracting the endpoint chord gives a continuous odd extension for the transform.
    """
    if len(t) < 3:
        return t, y
    span = t[-1] - t[0]
    keep = int(np.floor(span / step))
    if keep >= len(t) - 2:
        return t, y
    grid = np.linspace(t[0], t[-1], len(t))
    chord = np.linspace(y[0], y[-1], len(t))
    detrended = np.interp(grid[1:-1], t, y) - chord[1:-1]
    modes = dst(detrended, type=1)
    modes[keep:] = 0
    chord[1:-1] += idst(modes, type=1)
    return grid, chord


def resample_channel(t, y, query, source_fs):
    """Band-limit and interpolate onto a uniform grid within each finite source segment."""
    if y is None:
        return None
    y = np.asarray(y, dtype=float)
    if y.shape != t.shape:
        raise ValueError("钙通道长度与时间轴不符 / Calcium channel length differs from the source clock")
    if np.array_equal(t, query):
        return y.copy()
    result = np.full(len(query), np.nan)
    indices = np.flatnonzero(np.isfinite(y))
    if not len(indices):
        return result
    split = (np.diff(indices) > 1) | (np.diff(t[indices]) > 1.5 / source_fs)
    query_step = (query[-1] - query[0]) / (len(query) - 1)
    for group in np.split(indices, np.flatnonzero(split) + 1):
        lo, hi = t[group[0]], t[group[-1]]
        start, stop = np.searchsorted(query, lo), np.searchsorted(query, hi, side="right")
        if start < stop:
            x, values = _low_pass(t[group], y[group], query_step)
            result[start:stop] = np.interp(query[start:stop], x, values)
    return result


def resample_calcium(raw, result):
    """Return signals and previously attached acquisition events on the ABET clock.

    Call with source frames. The caller merges ABET events and retains the acquisition
    object for changing the association without stretching a trace a second time.
    """
    if raw.abet is not None:
        raise ValueError("ABET 重采样须使用原始采集帧 / Resampling requires the original acquisition frames")
    info = result.info
    t = np.asarray(raw.time, dtype=float)
    if (len(t) != info["source_samples"] or t[0] != info["source_first_s"]
            or t[-1] != info["source_last_s"]):
        raise ValueError("重采样时间轴与源记录不符 / Resampling grid does not match the acquisition")
    n = info["aligned_samples"]
    target = np.linspace(info["aligned_first_s"], info["aligned_last_s"], n)
    query = np.linspace(t[0], t[-1], n)
    channels = {k: resample_channel(t, getattr(raw, k), query, info["source_fs_hz"])
                for k in ("signal", "control", "exported_f0", "exported_dff")}
    native = dict(raw.behavior_native)
    behavior = None
    behavior_info = dict(raw.behavior_info)
    if raw.behavior is not None:
        observed = native.get("time", t)
        values = native.get("values", raw.behavior)
        native.update(time=source_to_behavior(observed, info), values=values)
        trace = BehaviorTrace(native["time"], values, behavior_info.get("value_column", ""), behavior_info.get("path", ""))
        behavior = align_behavior(trace, target, method=behavior_info.get("method", "linear"), require_overlap=False)
        behavior_info.update(valid_frames=int(np.isfinite(behavior).sum()), calcium_frames=n, clock=DURATION_RESAMPLE)
    return replace(
        raw, **channels, time=target, fs=info["aligned_fs_hz"],
        markers={k: source_to_behavior(v, info) for k, v in raw.markers.items()},
        marker_offsets={k: source_to_behavior(v, info) for k, v in raw.marker_offsets.items()},
        behavior=behavior, behavior_native=native, behavior_info=behavior_info,
    )
