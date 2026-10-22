"""Containers for the three data stages and their HDF5/CSV backups.

data A  raw recording read from TDMS (signal, control, behaviour markers)
data B  de-drifted ΔF/F (used for stimulus-locked analysis)
data C  OASIS-denoised trace + inferred spikes (used for event detection)
"""
from __future__ import annotations

import dataclasses
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


@dataclass
class DffData:
    """data B."""
    time: np.ndarray
    dff: np.ndarray
    fs: float
    signal: np.ndarray            # smoothed/downsampled signal the fit was done on
    reference: np.ndarray         # fitted baseline (scaled control or drift curve)
    method: str
    unit: str                     # "%" or "z"
    dff_unfiltered: Optional[np.ndarray] = None  # same ΔF/F without the low-pass, for deconvolution


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
    pd.DataFrame(cols).to_csv(path, index=False, float_format="%.6g")
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
            elif cls.__dataclass_fields__[k].default is not dataclasses.MISSING:
                continue  # field added later; older files fall back to its default
            else:
                kw[k] = None if k == "control" else {}
    return cls(**kw)
