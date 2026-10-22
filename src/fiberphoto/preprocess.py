"""De-drifting for ΔF/F (data B) and OASIS deconvolution (data C)."""
from __future__ import annotations

import warnings

import numpy as np
from scipy import optimize, signal as sps

from .config import DeconvConfig, DedriftConfig
from .data import DeconvData, DffData, RawData


def lowpass(x: np.ndarray, fs: float, cutoff: float, order: int = 4) -> np.ndarray:
    if cutoff is None or cutoff >= fs / 2:
        return x
    sos = sps.butter(order, cutoff, btype="low", fs=fs, output="sos")
    return sps.sosfiltfilt(sos, x)


def highpass(x: np.ndarray, fs: float, cutoff: float, order: int = 2) -> np.ndarray:
    sos = sps.butter(order, cutoff, btype="high", fs=fs, output="sos")
    return sps.sosfiltfilt(sos, x)


def downsample(t: np.ndarray, xs: list[np.ndarray], fs: float, target: float | None):
    """Bin-average to roughly ``target`` Hz. Returns (t, xs, new_fs)."""
    if not target or target >= fs:
        return t, xs, fs
    k = int(round(fs / target))
    if k <= 1:
        return t, xs, fs
    n = (len(t) // k) * k
    bin_ = lambda a: a[:n].reshape(-1, k).mean(axis=1)
    return bin_(t), [bin_(x) if x is not None else None for x in xs], fs / k


def fit_control_irls(control: np.ndarray, sig: np.ndarray, c: float = 4.685, n_iter: int = 50):
    """Robust linear fit sig ≈ a*control + b (Tukey bisquare IRLS).

    Robust weighting keeps large calcium transients from dragging the fit,
    which is what plain least squares does.
    """
    X = np.column_stack([control, np.ones_like(control)])
    w = np.ones_like(sig)
    beta = np.linalg.lstsq(X, sig, rcond=None)[0]
    for _ in range(n_iter):
        r = sig - X @ beta
        s = np.median(np.abs(r - np.median(r))) / 0.6745 or 1e-12
        u = r / (c * s)
        w_new = np.where(np.abs(u) < 1, (1 - u ** 2) ** 2, 0.0)
        sw = np.sqrt(w_new)
        beta_new = np.linalg.lstsq(X * sw[:, None], sig * sw, rcond=None)[0]
        if np.allclose(beta_new, beta, rtol=1e-8, atol=1e-12):
            break
        beta, w = beta_new, w_new
    return beta


def _biexp(t, a, tau1, b, tau2, c):
    return a * np.exp(-t / tau1) + b * np.exp(-t / tau2) + c


def fit_biexponential(t: np.ndarray, x: np.ndarray) -> np.ndarray:
    tt = t - t[0]
    span = max(tt[-1], 1.0)
    amp = max(x[: max(1, len(x) // 50)].mean() - x[-max(1, len(x) // 50):].mean(), 1e-6)
    p0 = [amp * 0.6, span / 10, amp * 0.4, span / 2, x[-len(x) // 10:].mean()]
    bounds = ([0, 1e-3, 0, 1e-3, -np.inf], [np.inf, 10 * span, np.inf, 100 * span, np.inf])
    try:
        p, _ = optimize.curve_fit(_biexp, tt, x, p0=p0, bounds=bounds, maxfev=20000)
        return _biexp(tt, *p)
    except (RuntimeError, ValueError):
        return np.polyval(np.polyfit(tt, x, 3), tt)


class DataWarning(UserWarning):
    """Something about the recording the user should know; shown in the log."""


def detect_led_onset(sig: np.ndarray, fs: float, ratio: float = 0.6) -> float:
    """Seconds from the start until the excitation light came on (0 if on from the start).

    Some rigs start recording before the LED; the first seconds then sit far
    below the rest of the trace and would wreck the baseline fit.
    """
    n1 = max(1, int(round(fs)))
    if sig.size < 10 * n1:
        return 0.0
    med = np.median(sig)
    start = np.median(sig[:n1])
    if med <= 0 or start >= ratio * med:
        return 0.0
    above = np.flatnonzero(sig > (start + med) / 2)
    if above.size == 0:
        return 0.0
    return float(above[0] / fs + 1.0)  # 1 s margin for the LED to settle


def dedrift(raw: RawData, cfg: DedriftConfig) -> DffData:
    t, sig, ctl, fs = raw.time, raw.signal, raw.control, raw.fs
    trim_start = cfg.trim_start_s
    if cfg.auto_trim:
        onset = detect_led_onset(sig, fs)
        if onset > trim_start:
            warnings.warn(f"检测到前 {onset:.1f} s 光源未开启，已自动去除 / "
                          f"Light source off for the first {onset:.1f} s; trimmed automatically", DataWarning)
            trim_start = onset
    i0 = np.searchsorted(t, t[0] + trim_start)
    i1 = np.searchsorted(t, t[-1] - cfg.trim_end_s, side="right")
    t, sig = t[i0:i1], sig[i0:i1]
    ctl = ctl[i0:i1] if ctl is not None else None

    sig_raw = sig
    if cfg.lowpass_hz:
        sig = lowpass(sig, fs, cfg.lowpass_hz)
        ctl = lowpass(ctl, fs, cfg.lowpass_hz) if ctl is not None else None
    t, (sig, ctl, sig_raw), fs = downsample(t, [sig, ctl, sig_raw], fs, cfg.downsample_hz)

    method = cfg.method
    if method == "isosbestic" and ctl is None:
        method = "biexponential"   # no control channel recorded

    if method == "isosbestic":
        a, b = fit_control_irls(ctl, sig)
        ref = a * ctl + b
    elif method == "biexponential":
        ref = fit_biexponential(t, sig)
    elif method == "polynomial":
        tt = (t - t[0]) / max(t[-1] - t[0], 1e-9)
        ref = np.polyval(np.polyfit(tt, sig, cfg.poly_order), tt)
    elif method == "highpass":
        ref = sig - highpass(sig, fs, cfg.highpass_hz)
    else:
        raise ValueError(f"Unknown de-drift method: {method}")

    if np.median(ref) <= 0:
        warnings.warn("基线荧光为零或负值，ΔF/F 的符号可能翻转，请检查信号通道 / "
                      "Baseline fluorescence is zero or negative, so ΔF/F may be sign-flipped; check the signal channel",
                      DataWarning)
    if method != cfg.method:
        warnings.warn(f"没有对照通道，改用 {method} 方法 / No control channel; using {method} instead", DataWarning)
    safe_ref = np.where(np.abs(ref) < 1e-12, 1e-12, ref)
    dff = (sig - ref) / safe_ref * 100.0
    # OASIS estimates noise from the high-frequency spectrum, which the low-pass
    # would remove; keep an unfiltered copy for deconvolution
    dff_raw = (sig_raw - ref) / safe_ref * 100.0
    unit = "%"
    if cfg.output == "zscore":
        centre, scale = np.median(dff), (np.std(dff) or 1.0)
        dff, dff_raw = (dff - centre) / scale, (dff_raw - centre) / scale
        unit = "z"
    return DffData(time=t, dff=dff, fs=fs, signal=sig, reference=ref, method=method, unit=unit,
                   dff_unfiltered=dff_raw)


def running_percentile(x: np.ndarray, window: int, q: float) -> np.ndarray:
    """Running percentile, computed on a coarse grid and interpolated (fast for long traces)."""
    n = x.size
    window = max(3, min(window, n))
    step = max(1, window // 10)
    centers = np.arange(0, n, step)
    half = window // 2
    vals = np.array([np.percentile(x[max(0, c - half): c + half + 1], q) for c in centers])
    return np.interp(np.arange(n), centers, vals)


def deconvolve(b: DffData, cfg: DeconvConfig) -> DeconvData:
    from oasis.functions import GetSn
    from oasis.functions import deconvolve as oasis_deconvolve

    y = np.asarray(b.dff if b.dff_unfiltered is None else b.dff_unfiltered, dtype=np.float64)
    drift = np.zeros_like(y)
    if cfg.baseline_window_s > 0:
        # OASIS assumes a constant baseline; take out residual slow drift first
        drift = running_percentile(y, int(cfg.baseline_window_s * b.fs), cfg.baseline_percentile)
        drift -= np.median(drift)
    y = np.ascontiguousarray(y - drift)
    import inspect
    if "tau_r" in inspect.signature(oasis_deconvolve).parameters:   # oasis-deconv >= 0.3
        model = {"tau_r": 0} if cfg.model == "ar1" else {"tau_r": None}
    else:
        model = {"g": (None,) if cfg.model == "ar1" else (None, None)}
    c, s, base, g, lam = oasis_deconvolve(y, penalty=cfg.penalty, optimize_g=cfg.optimize_g, **model)
    s = np.clip(s, 0.0, None)   # remove tiny negative round-off
    if cfg.smin > 0:
        s = np.where(s >= cfg.smin, s, 0.0)
    return DeconvData(time=b.time, denoised=c + base + drift, spikes=s, fs=b.fs,
                      baseline=float(base), g=tuple(float(v) for v in np.atleast_1d(g)),
                      lam=float(lam), sn=float(GetSn(y)))


def decay_time_constant(d: DeconvData) -> float:
    """Decay τ (s) implied by the AR(1) coefficient; NaN for AR(2)."""
    if len(d.g) == 1 and 0 < d.g[0] < 1:
        return -1.0 / (d.fs * np.log(d.g[0]))
    return float("nan")
