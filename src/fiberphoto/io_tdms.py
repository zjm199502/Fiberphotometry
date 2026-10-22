"""Reading TDMS recordings and behaviour markers (produces data A)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from nptdms import TdmsFile

from .config import ChannelConfig
from .data import RawData

SIGNAL_PAT = re.compile(r"(470|465|473|490|488|gcamp|signal|ca2?\+?|green)", re.I)
CONTROL_PAT = re.compile(r"(405|410|415|iso|control|ref|uv|violet)", re.I)
MARKER_PAT = re.compile(r"(ttl|di\d*|dio|digital|event|marker|stim|trig|behav|input|label|打标|行为)", re.I)
TIME_PAT = re.compile(r"^(time|times|timestamp|t|时间)(\s*\(s\))?$", re.I)


@dataclass
class ChannelInfo:
    group: str
    name: str
    length: int
    fs: Optional[float]
    unit: str
    guess: str           # "signal" | "control" | "marker" | "time" | ""
    median: float = float("nan")  # of the first samples; ranks unnamed signal candidates


def _channel_fs(ch) -> Optional[float]:
    inc = ch.properties.get("wf_increment")
    if inc:
        return 1.0 / float(inc)
    for key in ("SamplingRate", "Sampling Rate", "sample_rate", "fs", "Fs"):
        if key in ch.properties:
            return float(ch.properties[key])
    return None


def _guess_role(name: str, values: Optional[np.ndarray] = None) -> str:
    if TIME_PAT.match(name.strip()):
        return "time"
    if CONTROL_PAT.search(name):
        return "control"
    if SIGNAL_PAT.search(name):
        return "signal"
    if MARKER_PAT.search(name):
        return "marker"
    if values is not None and values.size and np.unique(values[: min(values.size, 20000)]).size <= 3:
        return "marker"   # binary-looking channel
    return ""


def inspect_tdms(path: str | Path) -> list[ChannelInfo]:
    """List every channel with a best guess of its role, for the channel picker."""
    out = []
    with TdmsFile.open(path) as f:
        for g in f.groups():
            for ch in g.channels():
                n = len(ch)
                sample = ch[: min(n, 20000)] if n else None
                unit = str(ch.properties.get("unit_string", ""))
                med = float(np.nanmedian(sample)) if n else float("nan")
                out.append(ChannelInfo(g.name, ch.name, n, _channel_fs(ch), unit,
                                       _guess_role(ch.name, sample), med))
    return out


def auto_map(infos: list[ChannelInfo], cfg: ChannelConfig) -> ChannelConfig:
    """Fill in empty fields of ``cfg`` from the guessed roles."""
    cfg = ChannelConfig(**vars(cfg))
    groups = [i.group for i in infos if i.length > 0]
    if not cfg.group and groups:
        # prefer the group containing a signal guess
        sig_groups = [i.group for i in infos if i.guess == "signal" and i.length > 0]
        cfg.group = sig_groups[0] if sig_groups else groups[0]
    chans = [i for i in infos if i.group == cfg.group]
    if not cfg.signal:
        cand = [i for i in chans if i.guess == "signal"]
        if not cand:
            # unnamed channels (e.g. "Untitled 1..5"): raw fluorescence is the largest
            # positive trace; empty photodetector inputs sit near zero or below
            cand = sorted((i for i in chans if i.guess == ""),
                          key=lambda i: -i.median if np.isfinite(i.median) else np.inf)
        if cand:
            cfg.signal = cand[0].name
    if not cfg.control:
        cand = [i for i in chans if i.guess == "control" and i.name != cfg.signal]
        if cand:
            cfg.control = cand[0].name
    if not cfg.time:
        cand = [i for i in chans if i.guess == "time"]
        if cand:
            cfg.time = cand[0].name
    if not cfg.markers:
        cfg.markers = [i.name for i in infos if i.guess == "marker"]
    return cfg


def detect_edges(x: np.ndarray, t: np.ndarray, threshold: Optional[float] = None,
                 min_gap_s: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Rising/falling edge times of a TTL-like trace."""
    x = np.asarray(x, dtype=float)
    if threshold is None:
        lo, hi = np.nanmin(x), np.nanmax(x)  # percentiles miss sparse short pulses
        if hi - lo < 1e-12:
            return np.array([]), np.array([])
        threshold = (lo + hi) / 2
    high = x > threshold
    d = np.diff(high.astype(np.int8))
    on = t[1:][d == 1]
    off = t[1:][d == -1]
    if high[0]:
        on = np.r_[t[0], on]
    if high[-1]:
        off = np.r_[off, t[-1]]
    if min_gap_s > 0 and on.size > 1:
        keep = np.r_[True, np.diff(on) > min_gap_s]
        on = on[keep]
        off = off[: len(on)] if off.size >= len(on) else off
    return on, off


def read_marker_csv(path: str | Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Behaviour markers from a CSV/Excel table.

    Accepted columns (case-insensitive): onset/time/start (s), optional offset/end (s),
    optional label/event/type. Without a label column every row is labelled "marker".
    """
    path = Path(path)
    df = pd.read_excel(path) if path.suffix.lower() in (".xls", ".xlsx") else pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(*names):
        for n in names:
            for k, c in cols.items():
                if k == n or k.startswith(n):
                    return c
        return None

    on_c = pick("onset", "time", "start", "开始", "时间")
    off_c = pick("offset", "end", "stop", "结束")
    lab_c = pick("label", "event", "type", "name", "标签", "事件")
    if on_c is None:
        on_c = df.columns[0]
    labels = df[lab_c].astype(str) if lab_c else pd.Series(["marker"] * len(df))
    onsets, offsets = {}, {}
    for lab in labels.unique():
        sel = labels == lab
        onsets[lab] = df.loc[sel, on_c].to_numpy(float)
        offsets[lab] = df.loc[sel, off_c].to_numpy(float) if off_c else np.full(sel.sum(), np.nan)
    return onsets, offsets


EVENT_SUFFIXES = ("-Event", "_Event", "-event", "_event", "-Events", "_Events")


def is_event_file(path: str | Path) -> bool:
    """True for a separate marker recording such as ``dSub-SUM 1-Event.tdms``."""
    return Path(path).stem.endswith(EVENT_SUFFIXES)


def find_event_file(path: str | Path) -> Optional[Path]:
    """Marker TDMS that sits next to a recording: ``<stem>-Event.tdms`` and similar."""
    path = Path(path)
    for suf in EVENT_SUFFIXES:
        cand = path.with_name(path.stem + suf + ".tdms")
        if cand.exists():
            return cand
    return None


def read_marker_tdms(path: str | Path, t0: float = 0.0, fallback_fs: Optional[float] = None,
                     threshold: Optional[float] = None, channel: str = "",
                     rec_duration: Optional[float] = None
                     ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Stimulus onsets from the first non-empty channel of a separate marker TDMS.

    Sampled TTL traces are edge-detected; short monotonic channels are taken as
    timestamps in seconds. ``channel`` picks a specific channel by name.
    If the stored rate gives a length more than 5 % off ``rec_duration`` (some
    acquisition programs write wf_increment = 1), the marker file is assumed to
    span the same time as the recording and its rate is derived from that.
    """
    with TdmsFile.open(path) as f:
        chans = [c for g in f.groups() for c in g.channels() if len(c)]
        if channel:
            chans = [c for c in chans if c.name == channel] or chans
        if not chans:
            return {}, {}
        ch = chans[0]
        vals = ch[:].astype(float)
        fs = _channel_fs(ch) or fallback_fs
        start = float(ch.properties.get("wf_start_offset", 0.0) or 0.0)
    name = ch.name or "marker"
    if np.unique(vals[: min(vals.size, 20000)]).size > 3 and np.all(np.diff(vals) > 0):
        return {name: vals}, {name: np.full(vals.size, np.nan)}
    if rec_duration and (not fs or abs(vals.size / fs - rec_duration) > 0.05 * rec_duration):
        fs = vals.size / rec_duration
    if not fs:
        raise ValueError("Marker file sampling rate unknown / 打标文件采样率未知")
    t = t0 + start + np.arange(vals.size) / fs
    on, off = detect_edges(vals, t, threshold)
    return {name: on}, {name: np.r_[off, np.full(max(0, on.size - off.size), np.nan)][: on.size]}


def read_tdms(path: str | Path, cfg: ChannelConfig) -> RawData:
    path = Path(path)
    cfg = auto_map(inspect_tdms(path), cfg)
    if not cfg.signal:
        raise ValueError("No signal channel found / 未找到信号通道")
    f = TdmsFile.read(path)
    g = f[cfg.group]
    sig_ch = g[cfg.signal]
    signal = sig_ch[:].astype(float)
    n = signal.size
    if n == 0 or np.nanmax(signal) - np.nanmin(signal) < 1e-12:
        raise ValueError(f"Signal channel '{cfg.signal}' is empty or constant (not connected?) / "
                         f"信号通道 '{cfg.signal}' 为空或恒定值（可能未接入）")
    control = g[cfg.control][:].astype(float)[:n] if cfg.control else None
    if control is not None and control.size == 0:
        control = None
    if control is not None and control.size < n:
        signal = signal[: control.size]
        n = control.size

    if cfg.time:
        time = g[cfg.time][:].astype(float)[:n]
        fs = cfg.sampling_rate or 1.0 / np.median(np.diff(time))
    else:
        fs = cfg.sampling_rate or _channel_fs(sig_ch)
        if not fs:
            raise ValueError("Sampling rate unknown; set it manually / 无法读取采样率，请手动填写")
        t0 = float(sig_ch.properties.get("wf_start_offset", 0.0) or 0.0)
        time = t0 + np.arange(n) / fs

    markers, offsets = {}, {}
    for name in cfg.markers:
        grp = cfg.group if name in [c.name for c in g.channels()] else None
        if grp is None:  # marker may live in another group
            for gg in f.groups():
                if name in [c.name for c in gg.channels()]:
                    grp = gg.name
                    break
        if grp is None:
            continue
        ch = f[grp][name]
        vals = ch[:].astype(float)
        if vals.size == 0:
            continue
        if vals.size < n / 10 and np.all(np.diff(vals) >= 0):
            # channel stores event timestamps rather than a sampled TTL trace
            markers[name], offsets[name] = vals, np.full(vals.size, np.nan)
            continue
        mfs = _channel_fs(ch) or fs
        mt = time[: vals.size] if vals.size <= n and mfs == fs else np.arange(vals.size) / mfs + time[0]
        on, off = detect_edges(vals, mt, cfg.marker_threshold)
        if on.size:
            markers[name], offsets[name] = on, np.r_[off, np.full(max(0, on.size - off.size), np.nan)][: on.size]

    marker_file = cfg.marker_file or find_event_file(path) or ""
    if marker_file and Path(marker_file).suffix.lower() == ".tdms":
        m_on, m_off = read_marker_tdms(marker_file, float(time[0]), fs, cfg.marker_threshold,
                                        rec_duration=n / fs)
        markers.update(m_on)
        offsets.update(m_off)
    elif marker_file:
        m_on, m_off = read_marker_csv(marker_file)
        markers.update(m_on)
        offsets.update(m_off)

    return RawData(time=time, signal=signal, control=control, fs=float(fs),
                   markers=markers, marker_offsets=offsets, source=str(path),
                   channel_names={"group": cfg.group, "signal": cfg.signal,
                                  "control": cfg.control or "", "time": cfg.time or ""})
