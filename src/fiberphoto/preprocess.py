"""De-drifting for ΔF/F (data B) and OASIS deconvolution (data C)."""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
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


def fit_biexponential(t: np.ndarray, x: np.ndarray, *, details: dict | None = None) -> np.ndarray:
    tt = t - t[0]
    span = max(tt[-1], 1.0)
    amp = max(x[: max(1, len(x) // 50)].mean() - x[-max(1, len(x) // 50):].mean(), 1e-6)
    p0 = [amp * 0.6, span / 10, amp * 0.4, span / 2, x[-len(x) // 10:].mean()]
    bounds = ([0, 1e-3, 0, 1e-3, -np.inf], [np.inf, 10 * span, np.inf, 100 * span, np.inf])
    try:
        p, _ = optimize.curve_fit(_biexp, tt, x, p0=p0, bounds=bounds, maxfev=20000)
        if details is not None:
            details.update(fit="biexponential", coefficients=p)
        return _biexp(tt, *p)
    except (RuntimeError, ValueError):
        p = np.polyfit(tt, x, 3)
        if details is not None:
            details.update(fit="polynomial_fallback", coefficients=p)
        return np.polyval(p, tt)


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


def signal_interval_slice(raw: RawData, start: float | None, end: float | None) -> slice:
    """Validate a closed acquisition-clock interval and return its original sample slice."""
    time = np.asarray(raw.time, dtype=float)
    if (start is None or end is None or not np.isfinite(start) or not np.isfinite(end)
            or start >= end or not time.size or start < time[0] or end > time[-1]):
        raise ValueError("请选择记录范围内起点早于终点的区间 / Select an increasing interval inside the recording")
    first = np.searchsorted(time, start, side="left")
    stop = np.searchsorted(time, end, side="right")
    if stop - first < 2:
        raise ValueError("所选区间至少需含两个样本 / Interval needs at least two samples")
    return slice(first, stop)


def signal_interval_baseline(raw: RawData, start: float, end: float) -> tuple[float, int]:
    """Median F0 and finite sample count on the original signal within [start, end] seconds."""
    window = np.asarray(raw.signal[signal_interval_slice(raw, start, end)], dtype=float)
    finite = window[np.isfinite(window)]
    if len(finite) < 2:
        raise ValueError("基线区间至少需含两个有效原始荧光样本 / Baseline needs at least two finite raw signal samples")
    baseline = float(np.median(finite))
    if not np.isfinite(baseline) or baseline <= 0:
        raise ValueError("所选区间的荧光中位数须为有限正值 / Baseline median fluorescence must be finite and positive")
    return baseline, len(finite)


def dedrift(raw: RawData, cfg: DedriftConfig) -> DffData:
    result = _dedrift_fluorescence(raw, cfg)
    window = cfg.center_window_s
    if not np.isfinite(window) or window < 0:
        raise ValueError("ΔF/F 滑动窗口须为有限的非负秒数 / The ΔF/F centering window must be finite and nonnegative")
    if window > 0:
        if len(result.time) < 3 or window * result.fs < 2 - 1e-9:
            raise ValueError("ΔF/F 滑动窗口过短：需要至少三个采样点 / The ΔF/F centering window needs at least three samples")
        clock = pd.to_timedelta(result.time - result.time[0], unit="s")
        trace = pd.Series(result.dff, index=clock)
        drift = trace.rolling(pd.Timedelta(seconds=window), center=True, closed="both", min_periods=1).median()
        result.dff_center_baseline = drift.to_numpy()
        result.dff = result.dff - result.dff_center_baseline
        if result.dff_unfiltered is not None:
            result.dff_unfiltered = result.dff_unfiltered - result.dff_center_baseline
        result.formula_info["center_window_s"] = window
    if cfg.output == "zscore" and result.unit == "%":
        centre, scale = np.median(result.dff), (np.std(result.dff) or 1.0)
        result.formula_info.update(centre=centre, scale=scale)
        result.dff = (result.dff - centre) / scale
        result.dff_unfiltered = (result.dff_unfiltered - centre) / scale
        result.unit = "z"
    result.behavior = raw.behavior_on(result.time)
    result.signal_start_s = cfg.signal_start_s
    result.signal_end_s = cfg.signal_end_s
    return result


def _dedrift_fluorescence(raw: RawData, cfg: DedriftConfig) -> DffData:
    t, sig, ctl, fs = raw.time, raw.signal, raw.control, raw.fs
    method = cfg.method
    selected = cfg.signal_start_s is not None or cfg.signal_end_s is not None
    interval = signal_interval_slice(raw, cfg.signal_start_s, cfg.signal_end_s) if selected else slice(None)
    if method != "signal_interval" and cfg.prefer_plexon_dff and raw.exported_dff is not None:
        ratio = raw.exported_dff[interval]
        if not np.isfinite(ratio).all():
            raise ValueError("Invalid Plexon R values; select raw recalculation / R 列含无效值，请选择从原始荧光重算")
        reference = raw.exported_f0[interval] if raw.exported_f0 is not None else None
        return DffData(t[interval], ratio.copy(), fs, sig[interval], reference,
                       f"Plexon {raw.channel_names['exported_dff']}", "ratio")
    details = {}
    if method == "signal_interval":
        baseline, n = signal_interval_baseline(raw, cfg.baseline_start_s, cfg.baseline_end_s)
        details.update(start=cfg.baseline_start_s, end=cfg.baseline_end_s, samples=n, f0=baseline)
        ctl = None
    if raw.channel_names.get("control_role") == "baseline":
        ctl = None  # F0 is a preview baseline, not an independently measured control
    if not selected:
        trim_start = cfg.trim_start_s
        if cfg.auto_trim and method != "signal_interval":
            onset = detect_led_onset(sig, fs)
            if onset > trim_start:
                warnings.warn(f"检测到前 {onset:.1f} s 光源未开启，已自动去除 / "
                              f"Light source off for the first {onset:.1f} s; trimmed automatically", DataWarning)
                trim_start = onset
        interval = slice(np.searchsorted(t, t[0] + trim_start),
                         np.searchsorted(t, t[-1] - cfg.trim_end_s, side="right"))
    t, sig = t[interval], sig[interval]
    ctl = ctl[interval] if ctl is not None else None

    sig_raw = sig
    if cfg.lowpass_hz:
        sig = lowpass(sig, fs, cfg.lowpass_hz)
        ctl = lowpass(ctl, fs, cfg.lowpass_hz) if ctl is not None else None
    t, (sig, ctl, sig_raw), fs = downsample(t, [sig, ctl, sig_raw], fs, cfg.downsample_hz)

    if selected and len(t) < 2:
        raise ValueError("降采样后区间内不足两个样本 / Signal interval too short for requested downsampling")
    if method == "isosbestic" and ctl is None:
        method = "biexponential"   # no control channel recorded

    if method == "isosbestic":
        a, b = fit_control_irls(ctl, sig)
        ref = a * ctl + b
        details.update(slope=a, intercept=b)
    elif method == "signal_interval":
        if not t.size:
            raise ValueError("裁剪或降采样后无可用样本 / No samples after trimming or downsampling")
        ref = np.full(sig.shape, baseline, dtype=float)
    elif method == "biexponential":
        ref = fit_biexponential(t, sig, details=details)
    elif method == "polynomial":
        span = max(t[-1] - t[0], 1e-9)
        tt = (t - t[0]) / span
        coeff = np.polyfit(tt, sig, cfg.poly_order)
        details.update(coefficients=coeff, span=span)
        ref = np.polyval(coeff, tt)
    elif method == "highpass":
        ref = sig - highpass(sig, fs, cfg.highpass_hz)
        details["cutoff_hz"] = cfg.highpass_hz
    else:
        raise ValueError(f"Unknown de-drift method: {method}")

    if np.median(ref) <= 0:
        warnings.warn("基线荧光为零或负值，ΔF/F 的符号可能翻转，请检查信号通道 / "
                      "Baseline fluorescence is zero or negative, so ΔF/F may be sign-flipped; check the signal channel",
                      DataWarning)
    if method != cfg.method:
        warnings.warn(f"没有对照通道，改用 {method} 方法 / No control channel; using {method} instead", DataWarning)
    safe_ref = ref if method == "signal_interval" else np.where(np.abs(ref) < 1e-12, 1e-12, ref)
    details["floored"] = bool(np.any(safe_ref != ref))
    dff = (sig - ref) / safe_ref * 100.0
    # OASIS estimates noise from the high-frequency spectrum, which the low-pass
    # would remove; keep an unfiltered copy for deconvolution
    dff_raw = (sig_raw - ref) / safe_ref * 100.0
    return DffData(time=t, dff=dff, fs=fs, signal=sig, reference=ref, method=method, unit="%",
                   dff_unfiltered=dff_raw, formula_info=details)


def format_dff_formula(raw: RawData, result: DffData, lang: str = "zh") -> str:
    """Describe the actual device source or fitted calculation for a completed run."""
    zh = lang == "zh"
    file = Path(raw.source).name or ("当前记录" if zh else "recording")
    prefix = f"[{file}] " + ("ΔF/F 公式　" if zh else "ΔF/F formula    ")
    d = result.formula_info
    window = d.get("center_window_s", 0)
    centered = (f" · Y(t) = X(t) − median(X(u), |u − t| ≤ {window / 2:.12g} s)" if window else "")
    if result.method.startswith("Plexon "):
        column = result.method.removeprefix("Plexon ")
        label = "设备导出比值" if zh else "device-exported ratio"
        name = "X(t)" if window else "ΔF/F(t)"
        return f"{prefix}{name} = {column}(t) ({label}){centered}"

    name = raw.channel_names.get("signal") or "signal"
    source = f"F(t) = {'预处理后' if zh else 'processed'} {name}(t)"
    if not d:  # Earlier HDF5 exports contain the baseline waveform but no fit coefficients.
        baseline = f"F0(t) = data B.reference ({result.method})"
    elif result.method == "signal_interval":
        baseline = (f"F0 = median({name}[{d['start']:.12g}–{d['end']:.12g} s])"
                    f" = {d['f0']:.12g} (n = {d['samples']})")
    elif result.method == "isosbestic":
        control = raw.channel_names.get("control") or "control"
        baseline = (f"F0(t) = {d['slope']:.12g} × C(t) {d['intercept']:+.12g}, "
                    f"C(t) = {'预处理后' if zh else 'processed'} {control}(t)")
    elif result.method == "highpass":
        baseline = f"F0(t) = F(t) − HP(F(t), {d['cutoff_hz']:.12g} Hz)"
    elif d.get("fit") == "biexponential":
        a, tau1, b, tau2, c = d["coefficients"]
        baseline = (f"F0(t) = {a:.12g} × exp(−u/{tau1:.12g}) {b:+.12g} × exp(−u/{tau2:.12g}) "
                    f"{c:+.12g}, u = t − ({result.time[0]:.12g} s)")
    else:
        coeff = ", ".join(f"{x:.12g}" for x in d["coefficients"])
        baseline = f"F0(t) = polyval([{coeff}], u), u = (t − ({result.time[0]:.12g} s))"
        if result.method == "polynomial":
            baseline += f" / ({d['span']:.12g} s)"
        else:
            baseline += "（双指数拟合失败，采用三次多项式）" if zh else " (cubic fallback after biexponential fit failure)"
    divisor = "F0" if result.method == "signal_interval" else "F0(t)"
    name = "X(t) (%)" if window else "ΔF/F (%)"
    formula = f"{name} = 100 × (F(t) − {divisor}) / "
    if d.get("floored"):
        formula += "D(t), D(t) = where(|F0(t)| < 1e−12, 1e−12, F0(t))"
    else:
        formula += divisor
    formula += centered
    if result.unit == "z":
        value = "Y(t)" if window else "ΔF/F (%)"
        formula += (f" · z(t) = ({value} − ({d['centre']:.12g})) / {d['scale']:.12g}"
                    if "centre" in d else f" · z(t) = ({value} − median) / SD")
    return f"{prefix}{source} · {baseline} · {formula}"


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
                      lam=float(lam), sn=float(GetSn(y)), behavior=b.behavior)


def decay_time_constant(d: DeconvData) -> float:
    """Decay τ (s) implied by the AR(1) coefficient; NaN for AR(2)."""
    if len(d.g) == 1 and 0 < d.g[0] < 1:
        return -1.0 / (d.fs * np.log(d.g[0]))
    return float("nan")
