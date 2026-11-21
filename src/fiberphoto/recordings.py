"""Device export readers. Time vectors are in seconds in the acquisition clock."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

from .config import ChannelConfig
from .data import RawData, indicator_annotation
from .io_tdms import (ChannelInfo, auto_map, detect_edges, inspect_tdms, is_event_file,
                      read_marker_csv, read_marker_tdms, read_tdms)

PLEXON_COLUMN = re.compile(r"(RawF|F0|R)_(?:(\d+)(?:nm)?_F)?(\d+)(?:_\d+pix)?", re.I)
ROI = re.compile(r"(?:Region|Channel)\d+[GR]?", re.I)
DIGITAL = re.compile(r"(?:Stim(?:ulation|_.*)?|TTL.*|DI\d+|DIO\d+|Event.*|Input\d+|Output\d+)", re.I)
CLOCK = re.compile(r"(?:time|timestamp|systemtimestamp)(?:\s*\((s|ms|us)\))?", re.I)


@dataclass
class Trace:
    group: str
    name: str
    role: str
    time: np.ndarray
    values: np.ndarray
    indicator: str = ""
    ratio_name: str = ""
    baseline_name: str = ""
    ratio: np.ndarray | None = None
    baseline: np.ndarray | None = None

    def info(self):
        steps = np.diff(self.time)
        if (self.time.ndim != 1 or self.time.shape != self.values.shape or not steps.size
                or not np.isfinite(self.time).all() or np.any(steps <= 0)):
            raise ValueError(f"{self.name}: invalid timestamps or sample count / 时间戳或样本数无效")
        return ChannelInfo(self.group, self.name, len(self.time), 1 / np.median(steps), "",
                           self.role, float(np.median(self.values[:20000])))


def _csv_layout(columns):
    if any(c.lower().startswith("rawf_") and PLEXON_COLUMN.fullmatch(c) for c in columns):
        return "plexon"
    if any(c.lower() in ("flags", "ledstate") for c in columns) and any(ROI.fullmatch(c) for c in columns):
        return "npm"
    return None


def recording_family(path):
    path = Path(path)
    if path.suffix.lower() != ".csv":
        return path.suffix.lower()
    with path.open(encoding="utf-8-sig", newline="") as f:
        columns = [c.strip() for c in next(csv.reader(f), [])]
    return _csv_layout(columns)


def is_recording(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".tdms":
        return not is_event_file(path)
    if suffix == ".doric":
        return True
    if suffix == ".csv":
        try:
            return recording_family(path) is not None and not path.stem.lower().endswith("_photo_coords")
        except (UnicodeError, OSError, csv.Error):
            return False
    return False


def _csv_traces(path):
    frame = pd.read_csv(path, encoding="utf-8-sig", skipinitialspace=True)
    frame.columns = frame.columns.str.strip()
    layout = _csv_layout(frame.columns)
    if not layout:
        raise ValueError("Expected Plexon RawF columns or Neurophotometrics LedState/Flags and ROI columns")
    if layout == "plexon" and (path.stem.lower().endswith("_photo_coords") or "X1" in frame.columns):
        raise ValueError("Export PhotoData without tracking coordinates / 请选择未合并行为坐标的 PhotoData")
    clocks = [c for c in frame if CLOCK.fullmatch(c)]
    if not clocks:
        raise ValueError("Missing Timestamp column / 缺少时间戳列")
    clock = next((c for c in clocks if not c.lower().startswith("system")), clocks[0])
    unit = (CLOCK.fullmatch(clock)[1] or "s").lower()
    time = frame[clock].to_numpy(float) * {"s": 1, "ms": 1e-3, "us": 1e-6}[unit]
    if not len(time) or not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
        raise ValueError(f"Invalid or non-increasing clock: {clock}")
    traces = []
    if layout == "plexon":
        columns = {}
        for name in frame:
            m = PLEXON_COLUMN.fullmatch(name)
            if m:
                key = (m[2], m[3], m[1].lower())
                if key in columns:
                    raise ValueError(f"Ambiguous Plexon columns: {columns[key]}, {name}")
                columns[key] = name
        for (wave, fiber, kind), name in columns.items():
            if kind != "rawf":
                continue
            role = "control" if wave in ("405", "410", "415") else "signal"
            trace = Trace(f"F{fiber}", name, role, time, frame[name].to_numpy(float))
            trace.ratio_name = columns.get((wave, fiber, "r"), "")
            if trace.ratio_name:
                trace.ratio = pd.to_numeric(frame[trace.ratio_name], errors="coerce").to_numpy(float)
            baseline = columns.get((wave, fiber, "f0"))
            if baseline:
                trace.baseline_name = baseline
                trace.baseline = pd.to_numeric(frame[baseline], errors="coerce").to_numpy(float)
            traces.append(trace)
            if trace.ratio is not None:
                traces.append(Trace(trace.group, trace.ratio_name, "ratio", time, trace.ratio))
            if trace.baseline is not None:
                traces.append(Trace(trace.group, trace.baseline_name, "baseline", time, trace.baseline))
    else:
        state_col = next(c for c in frame if c.lower() in ("ledstate", "flags"))
        flags = frame[state_col].to_numpy(float)
        if not np.isfinite(flags).all() or np.any((flags < 0) | (flags > 65535) | (flags != np.floor(flags))):
            raise ValueError(f"Invalid LED states: {state_col}")
        flags = flags.astype(np.uint16)
        if flags[0] & 7 == 7:
            flags[0] = 0
        for name in frame:
            if not ROI.fullmatch(name):
                continue
            for bit, wave, role in ((2, 470, "signal"), (4, 560, "signal"), (1, 415, "control")):
                if (wave == 470 and name.upper().endswith("R")) or (wave == 560 and name.upper().endswith("G")):
                    continue
                rows = (flags & bit) != 0
                if rows.any():
                    traces.append(Trace(name, f"{wave}nm", role, time[rows], frame.loc[rows, name].to_numpy(float)))
    for column in frame:
        if indicator_annotation({column: "label"}):
            labels = frame[column].dropna().astype(str).str.strip().unique()
            if len(labels) == 1:
                for trace in traces:
                    trace.indicator = labels[0]
                break
    groups = list(dict.fromkeys(t.group for t in traces))
    if layout == "npm":
        # Expose changing upper bits by index, without assigning a device-specific TTL label.
        original = frame[state_col].to_numpy(np.uint16)
        for bit in range(3, 16):
            values = (original >> bit) & 1
            if np.ptp(values):
                traces.extend(Trace(g, f"{state_col} bit {bit}", "marker", time, values) for g in groups)
    for name in frame:
        if DIGITAL.fullmatch(name):
            values = frame[name].to_numpy(float)
            traces.extend(Trace(g, name, "marker", time, values) for g in groups)
    return traces


def _doric_traces(path):
    traces = []
    with h5py.File(path, "r") as f:
        if "DataAcquisition" not in f:
            raise ValueError("Missing Doric DataAcquisition group")
        def collect(key, node):
            if not isinstance(node, h5py.Group) or "Time" not in node:
                return
            parts = key.split("/")
            if len(parts) != 4 or parts[1] != "Signals" or not parts[2].startswith("Series"):
                return
            device, _, series, store = parts
            lockin = re.fullmatch(r"LockInAOUT\d+|AIN\d+xAOUT\d+-LockIn", store, re.I)
            digital = re.fullmatch(r"DIO\d*|DigitalIO", store, re.I)
            if not (lockin or digital or store == "AnalogIn"):
                return
            time = np.asarray(node["Time"], dtype=float).squeeze()
            for name, ds in node.items():
                if name == "Time" or not isinstance(ds, h5py.Dataset):
                    continue
                if not re.fullmatch(r"Values?|AIN\d+|DIO\d+", name, re.I):
                    continue
                traces.append(Trace(f"{device}/{series}", f"{store}:{name}",
                                    "marker" if digital else "", time, np.asarray(ds, dtype=float).squeeze(),
                                    indicator_annotation(ds.attrs, node.attrs, node.parent.attrs, f.attrs)))
        f["DataAcquisition"].visititems(collect)
    return traces


def device_traces(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        traces = _csv_traces(path)
    elif path.suffix.lower() == ".doric":
        traces = _doric_traces(path)
    else:
        raise ValueError(f"Unsupported recording format: {path.suffix}")
    if not traces:
        raise ValueError("No supported photometry channels / 未找到支持的光纤通道")
    return traces


def inspect_recording(path):
    if Path(path).suffix.lower() == ".tdms":
        return inspect_tdms(path)
    return [t.info() for t in device_traces(path)]


def read_recording(path, cfg: ChannelConfig):
    path = Path(path)
    if path.suffix.lower() == ".tdms":
        return read_tdms(path, cfg)
    traces = device_traces(path)
    infos = [t.info() for t in traces]
    mapped = auto_map(infos, cfg)
    channels = {t.name: t for t in traces if t.group == mapped.group}
    if path.suffix.lower() == ".doric" and not cfg.signal:
        analog = [t for t in channels.values() if t.role != "marker"]
        if len(analog) != 1:
            raise ValueError("Select the Doric signal and reference for this wiring / 请按实际接线选择通道")
        mapped.signal = analog[0].name
    if mapped.signal not in channels or channels[mapped.signal].role in ("marker", "baseline"):
        raise ValueError(f"Select a fluorescence channel / 请选择荧光通道 ({mapped.group})")
    signal = channels[mapped.signal]
    if signal.role == "ratio":
        signal = next(t for t in channels.values() if t.ratio_name == mapped.signal)
    time, values = signal.time, signal.values
    def check_gaps(trace):
        steps = np.diff(trace.time)
        if np.max(steps) > 1.8 * np.median(steps):
            raise ValueError(f"{trace.name}: acquisition gap, split into continuous segments / 请按连续片段导入")

    check_gaps(signal)
    control = None
    if mapped.control:
        if mapped.control not in channels or mapped.control == signal.name or channels[mapped.control].role in ("marker", "ratio"):
            raise ValueError(f"Invalid reference channel: {mapped.control}")
        reference = channels[mapped.control]
        check_gaps(reference)
        if not np.isfinite(reference.values).all():
            raise ValueError(f"Non-finite reference samples: {reference.name}")
        # No extrapolation at either end of a multiplexed acquisition.
        overlap = (time >= reference.time[0]) & (time <= reference.time[-1])
        time, values = time[overlap], values[overlap]
        control = np.interp(time, reference.time, reference.values)
    if len(time) < 2 or not np.isfinite(values).all() or np.ptp(values) < 1e-12:
        raise ValueError("Empty, constant or invalid signal / 荧光信号无效或时间轴无交集")
    fs = float(1 / np.median(np.diff(time)))
    if cfg.sampling_rate and not np.isclose(fs, cfg.sampling_rate, rtol=0.02):
        raise ValueError(f"Sampling rate {cfg.sampling_rate} conflicts with timestamps ({fs:.4g} Hz)")
    markers, offsets = {}, {}
    for name in mapped.markers:
        marker = channels.get(name)
        if marker is None or marker.role != "marker":
            continue
        if not np.isfinite(marker.values).all():
            raise ValueError(f"Invalid marker samples: {name}")
        on, off = detect_edges(marker.values, marker.time, cfg.marker_threshold)
        if len(on):
            markers[name], offsets[name] = on, off
    if cfg.marker_file:
        if Path(cfg.marker_file).suffix.lower() == ".tdms":
            on, off = read_marker_tdms(cfg.marker_file, time[0], fs, cfg.marker_threshold)
        else:
            on, off = read_marker_csv(cfg.marker_file)
        markers.update(on)
        offsets.update(off)
    raw = RawData(time, values, control, fs, markers, offsets, str(path),
                  {"group": mapped.group, "signal": signal.name, "control": mapped.control, "time": "seconds",
                   "preview_signal": mapped.signal})
    if mapped.control:
        raw.channel_names["control_role"] = channels[mapped.control].role
    raw.apply_indicator(cfg.indicator, signal.indicator)
    if signal.ratio is not None:
        raw.exported_dff = signal.ratio
        raw.channel_names["exported_dff"] = signal.ratio_name
    raw.exported_f0 = signal.baseline
    return raw
