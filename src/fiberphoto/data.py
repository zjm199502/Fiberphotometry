"""Containers for the three data stages and their HDF5/CSV backups.

data A  raw recording read from TDMS (signal, control, behaviour markers)
data B  de-drifted ΔF/F (used for stimulus-locked analysis)
data C  OASIS-denoised trace + inferred spikes (used for event detection)
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import h5py
import numpy as np
import pandas as pd


@dataclass
class RawData:
    """data A."""
    time: np.ndarray
    signal: np.ndarray
    control: Optional[np.ndarray]
    fs: float
    markers: dict[str, np.ndarray] = field(default_factory=dict)  # label -> onset times (s)
    marker_offsets: dict[str, np.ndarray] = field(default_factory=dict)  # label -> offset times (s)
    source: str = ""
    channel_names: dict[str, str] = field(default_factory=dict)
    exported_dff: Optional[np.ndarray] = None
    exported_f0: Optional[np.ndarray] = None
    indicator: str = "GCaMP"
    indicator_source: str = "default"
    behavior: Optional[np.ndarray] = None  # behavior on the raw calcium time axis
    behavior_info: dict = field(default_factory=dict)
    behavior_native: dict[str, np.ndarray] = field(default_factory=dict)  # mapped timestamps and original values

    def behavior_on(self, time):
        if self.behavior is None or time is self.time or np.array_equal(time, self.time):
            return self.behavior
        from .behavior import BehaviorTrace, align_behavior

        trace = BehaviorTrace(time=self.behavior_native["time"], values=self.behavior_native["values"],
                              value_column=self.behavior_info["value_column"], source=self.behavior_info["path"])
        return align_behavior(trace, time, method=self.behavior_info["method"], require_overlap=False)

    def apply_indicator(self, override="", annotation=""):
        if override.strip():
            self.indicator, self.indicator_source = override.strip(), "user"
        elif annotation:
            self.indicator, self.indicator_source = annotation, "file"
        else:
            name = self.channel_names.get("signal", "")
            match = re.search(r"(?:j?GCaMP|j?RGECO|RCaMP|dLight|GRAB|iGluSnFR)[\w.\-]*", name, re.I)
            if match:
                self.indicator, self.indicator_source = match[0], "channel"


def indicator_annotation(*metadata):
    for props in metadata:
        for key, value in props.items():
            if str(key).strip().lower() in {"indicator", "fluorophore", "fluorescent indicator", "指示器"}:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return ""


@dataclass
class DffData:
    """data B."""
    time: np.ndarray
    dff: np.ndarray
    fs: float
    signal: np.ndarray            # smoothed/downsampled signal the fit was done on
    reference: Optional[np.ndarray]  # fitted or device baseline when available
    method: str
    unit: str                     # "%" or "z"
    dff_unfiltered: Optional[np.ndarray] = None  # same ΔF/F without the low-pass, for deconvolution
    behavior: Optional[np.ndarray] = None
    signal_start_s: Optional[float] = None
    signal_end_s: Optional[float] = None
    formula_info: dict = field(default_factory=dict)  # actual fitted parameters used by this run
    dff_center_baseline: Optional[np.ndarray] = None  # subtracted local median, in the source ratio or percent


@dataclass
class DeconvData:
    """data C."""
    time: np.ndarray
    denoised: np.ndarray
    spikes: np.ndarray
    fs: float
    baseline: float
    g: tuple
    lam: float
    sn: float = float("nan")       # noise σ estimated by OASIS (same units as ΔF/F)
    behavior: Optional[np.ndarray] = None


def _write_group(h5: h5py.File, name: str, obj) -> None:
    if name in h5:
        del h5[name]
    g = h5.create_group(name)
    for k, v in vars(obj).items():
        if isinstance(v, np.ndarray):
            g.create_dataset(k, data=v, compression="gzip")
        elif isinstance(v, dict):
            sub = g.create_group(k)
            for kk, vv in v.items():
                if isinstance(vv, np.ndarray):
                    sub.create_dataset(kk, data=vv)
                else:
                    sub.attrs[kk] = vv
        elif v is None:
            continue
        elif isinstance(v, tuple):
            g.attrs[k] = np.asarray(v, dtype=float)
        else:
            g.attrs[k] = v


def save_stage(path: str | Path, stage: str, obj, csv: bool = False) -> Path:
    """Write one stage ("A", "B" or "C") into the session HDF5 file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "a") as h5:
        _write_group(h5, f"data_{stage}", obj)
    if csv:
        stage_to_csv(path.with_name(f"{path.stem}_data{stage}.csv"), obj)
    return path


def stage_to_csv(path: Path, obj) -> None:
    cols = {k: v for k, v in vars(obj).items()
            if isinstance(v, np.ndarray) and v.ndim == 1 and len(v) == len(obj.time)}
    precision = "%.17g" if obj.behavior is not None else "%.6g"
    pd.DataFrame(cols).to_csv(path, index=False, float_format=precision)
    if isinstance(obj, RawData) and obj.markers:
        rows = []
        for label, onsets in obj.markers.items():
            offs = obj.marker_offsets.get(label, np.full(len(onsets), np.nan))
            rows += [{"label": label, "onset_s": a, "offset_s": b} for a, b in zip(onsets, offs)]
        pd.DataFrame(rows).to_csv(path.with_name(path.stem + "_markers.csv"), index=False)


def load_stage(path: str | Path, stage: str):
    cls = {"A": RawData, "B": DffData, "C": DeconvData}[stage]
    with h5py.File(path, "r") as h5:
        g = h5[f"data_{stage}"]
        kw = {}
        for k in cls.__dataclass_fields__:
            if k in g and isinstance(g[k], h5py.Group):
                sub = g[k]
                kw[k] = {kk: sub[kk][()] for kk in sub} | dict(sub.attrs)
            elif k in g:
                kw[k] = g[k][()]
            elif k in g.attrs:
                v = g.attrs[k]
                kw[k] = tuple(v) if k == "g" else (v.item() if hasattr(v, "item") and np.ndim(v) == 0 else v)
            elif (cls.__dataclass_fields__[k].default is not dataclasses.MISSING
                  or cls.__dataclass_fields__[k].default_factory is not dataclasses.MISSING):
                continue  # field added later; older files fall back to its default
            else:
                kw[k] = None if k in ("control", "reference") else {}
    return cls(**kw)
