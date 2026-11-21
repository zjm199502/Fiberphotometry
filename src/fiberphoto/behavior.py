"""Import a single numeric behavior channel and sample it on a photometry clock."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import numpy as np


@dataclass
class BehaviorConfig:
    path: str = ""
    value_column: str = ""
    time_column: str | None = None
    time_unit: str = "s"
    sampling_rate_hz: float | None = None
    offset_s: float = 0.0
    method: str = "linear"
    align_starts: bool = False


@dataclass
class BehaviorTrace:
    time: np.ndarray  # native timestamps converted to seconds
    values: np.ndarray
    value_column: str
    source: str

    def aligned_time(self, calcium_start, *, offset_s=0.0, align_starts=False):
        """Map the original timestamps onto the calcium clock. Positive offset delays behavior."""
        native = np.asarray(self.time, dtype=float)
        if not np.isfinite(offset_s) or not np.isfinite(calcium_start):
            raise ValueError("时间偏移和起点必须为有限数值 / Expected finite clock offset and start")
        if align_starts:
            return (native - native[0]) + (calcium_start + offset_s)
        return native + offset_s


_MISSING = {"", "na", "n/a", "nan", "null"}


def _number(text):
    return np.nan if text.strip().lower() in _MISSING else float(text)


def _layout(reader):
    first = next((row for row in reader if row), None)
    if first is None:
        raise ValueError("行为 CSV 为空 / Empty behavior CSV")
    try:
        for value in first:
            _number(value)
    except ValueError:
        names = [name.strip() or f"column_{i}" for i, name in enumerate(first, 1)]
        if len(set(names)) != len(names):
            raise ValueError("行为 CSV 列名重复 / Duplicate behavior column names")
        return names, None
    return [f"column_{i + 1}" for i in range(len(first))], first


def behavior_columns(path):
    """Read the header; numeric headerless CSVs use column_1, column_2, etc."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        names, _ = _layout(csv.reader(f, skipinitialspace=True))
    return names


def _validate_clock(time, name):
    if (time.ndim != 1 or not time.size or not np.isfinite(time).all()
            or np.any(time[1:] <= time[:-1])):
        raise ValueError(f"{name} 时间戳须有限且严格递增 / Expected finite, strictly increasing {name} timestamps")


def load_behavior_csv(path, value_column="", *, time_column=None, time_unit="s", sampling_rate_hz=None):
    """Read timestamp/value pairs or regularly sampled values with an explicit Hz rate.

    Empty values and NA are retained as missing samples. In a value-only file, a
    blank row after the header or first value occupies one sample on the regular grid.
    """
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, skipinitialspace=True)
        names, first = _layout(reader)
        candidates = [name for name in names if name != time_column]
        if not value_column and len(candidates) == 1:
            value_column = candidates[0]
        if value_column not in candidates or (time_column and time_column not in names):
            raise ValueError("请选择独立的行为数值列和时间来源 / Select a behavior column and a distinct time source")
        vi = names.index(value_column)
        ti = names.index(time_column) if time_column else None
        scale = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "μs": 1e-6}.get(time_unit)
        if ti is not None and scale is None:
            raise ValueError("行为时间单位须为 s、ms 或 us / Expected time unit s, ms or us")
        if ti is None and (sampling_rate_hz is None or not np.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0):
            raise ValueError("无时间列时须指定正采样率 / Enter a finite positive sampling rate in Hz")
        times, values = [], []
        rows = reader if first is None else chain([first], reader)
        for row in rows:
            if not row:
                if ti is None:
                    values.append(np.nan)
                continue
            if len(row) != len(names):
                raise ValueError(f"行为 CSV 第 {reader.line_num} 行列数不符 / Inconsistent row width")
            try:
                values.append(_number(row[vi]))
                if ti is not None:
                    times.append(_number(row[ti]))
            except ValueError as exc:
                raise ValueError(f"行为 CSV 第 {reader.line_num} 行需数值 / Non-numeric time or behavior value") from exc
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).any() or np.isinf(values).any():
        raise ValueError("行为数据需包含有效数值且不能含无穷值 / Expected finite behavior samples or missing values")
    time = np.asarray(times) * scale if ti is not None else np.arange(len(values), dtype=float) / sampling_rate_hz
    _validate_clock(time, "behavior")
    return BehaviorTrace(time, values, value_column, str(path))


def align_behavior(trace, calcium_time, *, offset_s=0.0, method="linear", align_starts=False, require_overlap=True):
    """Return one value per calcium timestamp; outside the behavior recording return NaN.

    Linear interpolation preserves gaps containing missing observations. Previous
    holds the last observed state until the next timestamp (including a missing
    state). Nearest chooses the earlier sample on ties. No method extends the
    signal beyond its last timestamp.
    """
    if method not in ("linear", "previous", "nearest"):
        raise ValueError(f"未知的行为对齐方法 / Unknown resampling method: {method}")
    target = np.asarray(calcium_time, dtype=float)
    if target.ndim == 1 and not target.size and not require_overlap:
        return np.empty(0, dtype=float)
    _validate_clock(target, "calcium")
    values = np.asarray(trace.values, dtype=float)
    if not values.size or values.shape != np.shape(trace.time) or np.isinf(values).any():
        raise ValueError("行为样本和时间长度不匹配或数值无效 / Invalid behavior samples")
    time = trace.aligned_time(target[0], offset_s=offset_s, align_starts=align_starts)
    _validate_clock(time, "behavior")

    right = np.clip(np.searchsorted(time, target), 0, len(time) - 1)
    left = np.maximum(0, right - 1)
    nearest = np.where(np.abs(target - time[left]) <= np.abs(time[right] - target), left, right)
    # Treat a one-ULP unit conversion / clock addition discrepancy as an exact sample.
    exact = np.abs(target - time[nearest]) <= np.spacing(np.maximum(np.abs(target), np.abs(time[nearest])))
    query = np.where(exact, time[nearest], target)
    if method == "linear":
        out = np.interp(query, time, values, left=np.nan, right=np.nan)
    else:
        indices = nearest if method == "nearest" else np.maximum(0, np.searchsorted(time, query, side="right") - 1)
        out = np.where((query >= time[0]) & (query <= time[-1]), values[indices], np.nan)
    if require_overlap and not np.isfinite(out).any():
        raise ValueError("对齐后无有效重叠样本，请核对时间单位、起点和偏移 / No valid overlapping samples")
    return out


def load_aligned_behavior(config, calcium_time):
    trace = load_behavior_csv(config.path, config.value_column, time_column=config.time_column,
                              time_unit=config.time_unit, sampling_rate_hz=config.sampling_rate_hz)
    return align_behavior(trace, calcium_time, offset_s=config.offset_s,
                          method=config.method, align_starts=config.align_starts)
