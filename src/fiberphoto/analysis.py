"""Stimulus-locked ΔF/F (on data B) and transient detection (on data C)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import signal as sps

from .config import EventConfig, PethConfig
from .data import DeconvData, DffData


def mad_sigma(x: np.ndarray) -> float:
    return float(np.median(np.abs(x - np.median(x))) / 0.6745)


def merge_close(onsets: np.ndarray, min_interval: float) -> np.ndarray:
    onsets = np.sort(np.asarray(onsets, dtype=float))
    if min_interval <= 0 or onsets.size < 2:
        return onsets
    keep = [onsets[0]]
    for t in onsets[1:]:
        if t - keep[-1] >= min_interval:
            keep.append(t)
    return np.array(keep)


@dataclass
class PethResult:
    """Stimulus-locked segments of data B, both as ΔF/F and as baseline z-scores."""
    marker: str
    t_rel: np.ndarray          # seconds relative to stimulus
    trials_z: np.ndarray       # (n_trials, n_samples), z-scored to each trial's baseline window
    trials_dff: np.ndarray     # (n_trials, n_samples), ΔF/F straight from data B
    onsets: np.ndarray         # stimulus times actually used
    metrics: pd.DataFrame      # one row per trial
    dff_unit: str              # unit of data B ("%" or "z")

    @property
    def trials(self) -> np.ndarray:
        return self.trials_z

    def get(self, kind: str = "z") -> np.ndarray:
        return self.trials_z if kind == "z" else self.trials_dff

    def mean(self, kind: str = "z") -> np.ndarray:
        return np.nanmean(self.get(kind), axis=0)

    def sem(self, kind: str = "z") -> np.ndarray:
        x = self.get(kind)
        n = np.sum(~np.isnan(x), axis=0)
        return np.nanstd(x, axis=0, ddof=1) / np.sqrt(np.maximum(n, 1))


def _window_metrics(seg: np.ndarray, t_rel: np.ndarray, base_m, targ_m, dt: float, tag: str) -> dict:
    base, targ = seg[base_m], seg[targ_m]
    k = int(np.argmax(targ)) if targ.size else 0
    return {
        f"baseline_mean_{tag}": float(base.mean()) if base.size else np.nan,
        f"target_mean_{tag}": float(targ.mean()) if targ.size else np.nan,
        f"peak_{tag}": float(targ[k]) if targ.size else np.nan,
        f"auc_baseline_{tag}": float(np.trapezoid(base, dx=dt)) if base.size > 1 else np.nan,
        f"auc_target_{tag}": float(np.trapezoid(targ, dx=dt)) if targ.size > 1 else np.nan,
    }


def peri_event(b: DffData, onsets: np.ndarray, cfg: PethConfig, marker: str = "") -> PethResult:
    """Cut data B around each stimulus; report ΔF/F and baseline z-score side by side.

    The baseline window [baseline_start_s, baseline_end_s) and the target window
    [response_start_s, response_end_s] are both set in seconds relative to the
    stimulus. AUC is the trapezoid integral over each window (unit × s).
    """
    onsets = merge_close(onsets, cfg.min_interval_s)
    n_pre = int(round(cfg.pre_s * b.fs))
    n_post = int(round(cfg.post_s * b.fs))
    t_rel = np.arange(-n_pre, n_post + 1) / b.fs
    base_m = (t_rel >= cfg.baseline_start_s) & (t_rel < cfg.baseline_end_s)
    targ_m = (t_rel >= cfg.response_start_s) & (t_rel <= cfg.response_end_s)
    dt = 1.0 / b.fs

    zs, dffs, used, rows = [], [], [], []
    for t0 in onsets:
        i = int(np.searchsorted(b.time, t0))
        if i - n_pre < 0 or i + n_post >= b.dff.size:
            continue  # window falls outside the recording
        seg = b.dff[i - n_pre: i + n_post + 1].astype(float)
        base = seg[base_m]
        sd = np.std(base, ddof=1) if base.size > 1 else 0.0
        z = (seg - (base.mean() if base.size else 0.0)) / (sd if sd > 0 else 1.0)
        zs.append(z)
        dffs.append(seg)
        used.append(t0)
        targ = z[targ_m]
        row = {"trial": len(zs), "onset_s": t0,
               "peak_latency_s": float(t_rel[targ_m][int(np.argmax(targ))]) if targ.size else np.nan}
        row |= _window_metrics(z, t_rel, base_m, targ_m, dt, "z")
        row |= _window_metrics(seg, t_rel, base_m, targ_m, dt, "dff")
        rows.append(row)
    empty = np.empty((0, t_rel.size))
    return PethResult(marker, t_rel, np.vstack(zs) if zs else empty, np.vstack(dffs) if dffs else empty,
                      np.array(used), pd.DataFrame(rows), b.unit)


@dataclass
class EventResult:
    events: pd.DataFrame       # one row per detected transient
    summary: dict
    threshold: float           # on the denoised trace ("peak") or on the spike train ("oasis")
    noise_sigma: float
    method: str = "peak"


def detect_events(c: DeconvData, b: DffData, cfg: EventConfig) -> EventResult:
    if cfg.method == "oasis":
        return detect_spike_events(c, cfg)
    return detect_peak_events(c, b, cfg)


def spike_threshold(c: DeconvData, cfg: EventConfig) -> tuple[float, float]:
    """(absolute threshold on the spike train, noise σ) for the chosen threshold mode."""
    sn = c.sn if np.isfinite(c.sn) and c.sn > 0 else mad_sigma(np.diff(c.denoised)) or 1.0
    if cfg.spike_threshold_mode == "noise":
        thr = cfg.spike_threshold * sn
    elif cfg.spike_threshold_mode == "max":
        thr = cfg.spike_threshold * (c.spikes.max() if c.spikes.size else 0.0)
    else:  # "absolute": same units as ΔF/F
        thr = cfg.spike_threshold
    return float(max(thr, 1e-12)), float(sn)


def detect_spike_events(c: DeconvData, cfg: EventConfig) -> EventResult:
    """Events straight from the OASIS spike train.

    OASIS often spreads one transient over neighbouring frames, so spikes are
    first grouped: frames with a spike of at least 0.5 × noise σ (or the
    threshold, if lower) that lie within ``min_distance_s`` of each other form
    one candidate. A candidate is an event when its largest spike reaches the
    threshold, so raising the threshold can only remove events.

    amplitude  rise of the denoised trace from just before the event to its peak (ΔF/F)
    spike_sum  summed spike size of the group (larger than the rise for long events,
               because the trace decays between spikes)
    """
    thr, sn = spike_threshold(c, cfg)
    floor = min(thr, 0.5 * sn)
    idx = np.flatnonzero(c.spikes >= floor)
    groups = np.split(idx, np.flatnonzero(np.diff(c.time[idx]) > cfg.min_distance_s) + 1) if idx.size else []
    rows = []
    for g in groups:
        sg = c.spikes[g]
        if sg.max() < thr:
            continue
        first, last = g[0], g[-1]
        before = c.denoised[first - 1] if first > 0 else c.denoised[first] - c.spikes[first]
        rows.append({"time_s": c.time[g[np.argmax(sg >= thr)]],
                     "amplitude": float(c.denoised[first: last + 1].max() - before),
                     "spike_sum": float(c.spikes[first: last + 1].sum()),
                     "max_spike": float(sg.max()),
                     "n_frames": int(g.size)})
    ev = pd.DataFrame(rows, columns=["time_s", "amplitude", "spike_sum", "max_spike", "n_frames"])
    ev["iei_s"] = ev["time_s"].diff()
    return EventResult(ev, _summary(ev, c, sn, thr), thr, sn, "oasis")


def _summary(ev: pd.DataFrame, c: DeconvData, sigma: float, thr: float) -> dict:
    dur_min = (c.time[-1] - c.time[0]) / 60.0
    return {
        "n_events": int(len(ev)),
        "duration_min": float(dur_min),
        "rate_per_min": float(len(ev) / dur_min) if dur_min > 0 else np.nan,
        "mean_amplitude": float(ev["amplitude"].mean()) if len(ev) else np.nan,
        "median_iei_s": float(ev["iei_s"].median()) if len(ev) > 1 else np.nan,
        "mean_half_width_s": float(ev["half_width_s"].mean()) if len(ev) and "half_width_s" in ev else np.nan,
        "noise_sigma": float(sigma),
        "threshold": float(thr),
        "spike_sum": float(c.spikes.sum()),
    }


def detect_peak_events(c: DeconvData, b: DffData, cfg: EventConfig) -> EventResult:
    """Find transients as peaks of the OASIS-denoised trace.

    Noise is estimated from the residual between data B and data C, so the
    threshold adapts to each recording.
    """
    y = c.denoised
    sigma = mad_sigma(b.dff - c.denoised) or mad_sigma(b.dff) or 1.0
    floor = np.median(y)
    thr = floor + cfg.threshold_mad * sigma
    dist = max(1, int(round(cfg.min_distance_s * c.fs)))
    peaks, props = sps.find_peaks(y, height=thr, distance=dist,
                                  prominence=cfg.min_prominence_mad * sigma)
    widths = sps.peak_widths(y, peaks, rel_height=0.5)[0] / c.fs if peaks.size else np.array([])
    ev = pd.DataFrame({
        "time_s": c.time[peaks],
        "amplitude": y[peaks] - floor,
        "prominence": props.get("prominences", np.array([])),
        "half_width_s": widths,
    })
    ev["iei_s"] = ev["time_s"].diff()
    return EventResult(ev, _summary(ev, c, sigma, thr), float(thr), float(sigma), "peak")


def events_around_stimuli(ev: EventResult, onsets: np.ndarray, window_s: float,
                          t_start: float, t_end: float) -> pd.DataFrame:
    """Event count, rate and mean amplitude before vs after each stimulus."""
    times = ev.events["time_s"].to_numpy()
    amps = ev.events["amplitude"].to_numpy()
    rows = []
    for i, t0 in enumerate(np.sort(onsets), 1):
        if t0 - window_s < t_start or t0 + window_s > t_end:
            continue
        pre = (times >= t0 - window_s) & (times < t0)
        post = (times >= t0) & (times < t0 + window_s)
        rows.append({
            "trial": i, "onset_s": t0,
            "pre_count": int(pre.sum()), "post_count": int(post.sum()),
            "pre_rate_per_min": pre.sum() / window_s * 60,
            "post_rate_per_min": post.sum() / window_s * 60,
            "pre_mean_amp": float(amps[pre].mean()) if pre.any() else np.nan,
            "post_mean_amp": float(amps[post].mean()) if post.any() else np.nan,
        })
    return pd.DataFrame(rows)


def paired_test(a: np.ndarray, b: np.ndarray) -> dict:
    """Wilcoxon signed-rank (and paired t) for pre vs post comparisons."""
    from scipy import stats
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    a, b = a[m], b[m]
    out = {"n": int(a.size), "wilcoxon_p": np.nan, "ttest_p": np.nan}
    if a.size >= 3:
        if np.any(a != b):
            out["wilcoxon_p"] = float(stats.wilcoxon(a, b).pvalue)
        out["ttest_p"] = float(stats.ttest_rel(a, b).pvalue)
    return out
