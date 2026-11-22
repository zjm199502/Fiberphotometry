"""Portable tables and figure settings for an already completed analysis."""
from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
from io import BytesIO, TextIOWrapper
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import sys
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd

from . import __version__
from .analysis import paired_test
from .method_report import _json_safe
from .plotting import event_histogram_bins
from .preprocess import format_dff_formula
from .render_sources import add_renderer


def capture_views(views):
    """Capture the visible limits and reference images on the GUI thread."""
    axes, images, specs = {}, {}, {}
    for name, view in views.items():
        if view.canvas is None:
            continue
        figure = view.canvas.figure
        figure.canvas.draw()
        specs[name] = dict(size_inches=figure.get_size_inches().tolist(), dpi=figure.dpi,
                           figure_edits=deepcopy(view.figure_edits))
        axes[name] = []
        for ax in figure.axes:
            info = dict(xlim=ax.get_xlim(), ylim=ax.get_ylim(), position=ax.get_position().bounds, xlabel=ax.get_xlabel(),
                        ylabel=ax.get_ylabel(), title=ax.get_title(loc="left") or ax.get_title())
            if ax.images:
                heat = ax.images[0]
                info.update(cmap=heat.get_cmap().name, clim=heat.get_clim(), extent=heat.get_extent())
            axes[name].append(info)
        for ext in ("png", "svg"):
            image = BytesIO()
            figure.savefig(image, format=ext, dpi=120)
            images[f"{name}.{ext}"] = image.getvalue()
    return axes, images, specs


def _table(archive, name, data):
    with archive.open(name + ".csv", "w", force_zip64=True) as raw, TextIOWrapper(raw, encoding="utf-8", newline="") as stream:
        data.to_csv(stream, index=False, float_format="%.17g", chunksize=4096)


def _dff_scale(b):
    if b.unit == "z":
        # Reconstructed from the actual fitted transform. Legacy inputs may omit it.
        return "%", b.formula_info.get("scale", np.nan), b.formula_info.get("centre", np.nan)
    return b.unit, 1., 0.


def _peth_tables(archive, p, b, scale, offset):
    cfg = p.window_config
    columns = [f"trial_{i}" for i in range(1, len(p.onsets) + 1)]
    series = {"native": p.trials_dff, "dff": p.trials_dff * scale, "z": p.trials_z}
    summary = {"t_rel_s": p.t_rel}
    for name, trials in series.items():
        frame = pd.DataFrame(trials.T, columns=columns)
        frame.insert(0, "t_rel_s", p.t_rel)
        _table(archive, "trials_" + name, frame)
        kind = "z" if name == "z" else "dff"
        factor = scale if name == "dff" else 1.
        summary["mean_" + name] = p.mean(kind) * factor
        summary["sem_" + name] = p.sem(kind) * factor
        summary["n_" + name] = np.isfinite(trials).sum(axis=0)
    _table(archive, "time_summary", pd.DataFrame(summary))
    metrics = p.metrics.copy()
    # Keep original display coordinates while supplying physical units for standardized input.
    for name in p.metrics:
        if name.endswith("_dff"):
            metrics[name.removesuffix("_dff") + "_native"] = p.metrics[name]
            metrics[name] = p.metrics[name] * scale + (offset if name == "prestimulus_mean_input_dff" else 0.)
    if len(p.onsets):
        indices = np.searchsorted(b.time, p.onsets)
        metrics["alignment_sample"] = indices
        metrics["alignment_sample_time_s"] = b.time[indices]
        baseline = (p.t_rel >= cfg.baseline_start_s - 1e-9 / b.fs) & (p.t_rel < cfg.baseline_end_s - 1e-9 / b.fs)
        native_trials = p.trials_dff + p.metrics.prestimulus_mean_input_dff.to_numpy()[:, None]
        base = native_trials[:, baseline]
        metrics["baseline_mean_input_native"] = base.mean(axis=1) if base.shape[1] else np.nan
        metrics["baseline_sd_input_native"] = base.std(axis=1, ddof=1) if base.shape[1] > 1 else np.nan
    if not len(metrics.columns):
        metrics = pd.DataFrame(columns=["trial", "onset_s", "peak_latency_s"])
    _table(archive, "trial_metrics", metrics)
    comparisons = []
    for kind in ("native", "z", "dff"):
        for measure in ("auc", "peak"):
            a, d = p.paired_values(measure, "z" if kind == "z" else "dff")
            if kind == "dff":
                a, d = a * scale, d * scale
            comparisons.append(dict(series=kind, metric=measure, **paired_test(a, d)))
    _table(archive, "paired_statistics", pd.DataFrame(comparisons))
    return dict(marker=p.marker, marker_sources=p.marker_sources, settings=cfg.parameters(),
                trial_count=len(p.onsets), skipped_nonfinite_pre=p.skipped_incomplete_pre,
                peak_direction=p.peak_direction)


def _event_tables(archive, ev, b, c, stim, overlays, scale, offset):
    physical = b.dff * scale + offset
    finite = physical[np.isfinite(physical)]
    centre = finite.mean() if finite.size else np.nan
    sd = finite.std() if finite.size else np.nan
    div = sd if np.isfinite(sd) and sd > 0 else np.nan
    _table(archive, "fluorescence", pd.DataFrame(dict(time_s=b.time, signal_native=b.dff, signal_dff=physical,
                                                      signal_z=(physical - centre) / div)))
    denoised = c.denoised * scale + offset
    _table(archive, "deconvolution", pd.DataFrame(dict(time_s=c.time, denoised_native=c.denoised,
           denoised_dff=denoised, denoised_z=(denoised - centre) / div, activity_native=c.spikes,
           activity_dff=c.spikes * scale, activity_z=c.spikes * scale / div)))
    events = ev.events.copy()
    peaks = events.get("peak_time_s", events["time_s"]).to_numpy()
    index = np.searchsorted(c.time, peaks)
    events["marker_time_s"] = c.time[index]
    events["marker_y_native"] = c.denoised[index]
    events["marker_y_dff"] = denoised[index]
    events["marker_y_z"] = (denoised[index] - centre) / div
    for name in ("amplitude", "auc", "prominence", "max_spike", "spike_sum", "baseline"):
        if name in events:
            absolute = name == "baseline"
            events[name + "_dff"] = events[name] * scale + (offset if absolute else 0.)
            events[name + "_z"] = (events[name + "_dff"] - (centre if absolute else 0.)) / div
    _table(archive, "events", events)
    _table(archive, "stimulus_overlay", pd.DataFrame({"time_s": overlays}))
    hist = []
    for col in ("amplitude", "iei_s"):
        values = events[col].dropna()
        if len(values):
            counts, edges = np.histogram(values, bins=event_histogram_bins(len(events)))
            hist.extend((col, lo, hi, n) for lo, hi, n in zip(edges[:-1], edges[1:], counts))
    _table(archive, "distributions", pd.DataFrame(hist, columns=["variable", "left", "right", "count"]))
    rate_stats = None
    if stim is not None and len(stim):
        rate_stats = paired_test(stim.pre_rate_per_min, stim.post_rate_per_min)
        _table(archive, "stimulus_comparison", stim)
    threshold_dff = ev.threshold * scale + (offset if ev.method == "peak" else 0.)
    return dict(method=ev.method, settings=asdict(ev.settings) if ev.settings else None, summary=ev.summary,
                deconvolution=dict(fs=c.fs, baseline=c.baseline, g=c.g, lambda_=c.lam, noise_sigma=c.sn),
                threshold=dict(axis="activity" if ev.method == "oasis" else "denoised",
                               native=ev.threshold, dff=threshold_dff,
                               z=(threshold_dff - (centre if ev.method == "peak" else 0.)) / div),
                recording_z=dict(centre_dff=centre, sd_dff=sd, ddof=0), rate_statistics=rate_stats,
                comparison_marker=stim.attrs.get("marker") if stim is not None else None)


def export_plot_bundle(path, raw, b, views, *, peth=None, events=None, deconv=None, comparisons=None,
                       overlays=(), language="en", active_comparison="z"):
    """Write a complete ZIP transactionally; the caller supplies the current results."""
    path = Path(path)
    unit, scale, offset = _dff_scale(b)
    from importlib import metadata as pkg_metadata
    axes, images, specs = views
    metadata = dict(schema_version=1, software="FiberLume", version=__version__, recording=Path(raw.source).name,
                    page="peth" if peth is not None else "events", fs=b.fs, analysis_unit=b.unit, dff_unit=unit,
                    dff_scale=scale, dff_offset=offset, preprocessing=b.method, formula_info=b.formula_info,
                    formula=format_dff_formula(raw, b, "en"), clock="ABET" if raw.abet is not None else "acquisition",
                    axes=axes, views=specs, language=language, active_comparison=active_comparison,
                    python=sys.version.split()[0])
    with NamedTemporaryFile(dir=path.parent, prefix=".fiberlume-", suffix=".tmp", delete=False) as handle:
        temp = Path(handle.name)
    try:
        with ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=4) as archive:
            if peth is not None:
                metadata.update(_peth_tables(archive, peth, b, scale, offset))
            else:
                metadata.update(_event_tables(archive, events, b, deconv, comparisons, overlays, scale, offset))
            archive.writestr("metadata.json", json.dumps(_json_safe(metadata), ensure_ascii=False, indent=2,
                                                         allow_nan=False))
            archive.writestr("README.txt", _GUIDE)
            add_renderer(archive)
            dependencies = ("matplotlib", "numpy", "pandas", "scipy", "h5py")
            # Frozen builds may omit distribution metadata. Their loaded modules retain the version.
            requirements = []
            for package in dependencies:
                try:
                    version = pkg_metadata.version(package)
                except pkg_metadata.PackageNotFoundError:
                    version = getattr(sys.modules.get(package), "__version__", None)
                requirements.append(f"{package}=={version}" if version else package)
            archive.writestr("requirements.txt", "\n".join(requirements) + "\n")
            for name, image in images.items():
                archive.writestr(f"preview/{name}", image)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


_GUIDE = """FiberLume 绘图数据 / Plot data
解压后可用 Excel、R、Prism 或 Python 打开 CSV。运行 python replot.py 可根据 CSV 和 metadata.json 独立重绘。
Extract the ZIP into a single folder. Run python replot.py in a Python environment with the packages in requirements.txt.
The bundled original renderer reads CSV tables and metadata, applies the saved element edits and axes, and writes redrawn SVG and PNG files.
No FiberLume installation or network is required for rendering. Install the listed dependencies beforehand.
preview/ contains editable SVGs and PNG reference images of the current views, including zoom. Axis limits, edits, language and colors are in metadata.json.
Blank CSV values and JSON null denote undefined quantities. All times are seconds. All time-series data use full analysis resolution.

刺激锁定 / Peri-event
trials_native, trials_dff and trials_z have one column per accepted trial and one row per sample relative to its aligned frame.
native is the displayed input-unit trace minus its full pre-stimulus (t < 0) mean. dff is the same difference in physical ΔF/F (ratio or %).
z = (input − mean of that trial's baseline window) / sample SD (ddof=1); FiberLume uses divisor 1 when there is no positive SD.
trial_metrics retains both window peaks and trapezoidal AUC (trace units × seconds), trial centers, zero-based alignment frame and its recording time.
Baseline uses [start,end); target uses [start,end]. In time_summary, SEM = sample SD / sqrt(finite count); SEM needs at least two trials.
Per-trial z and full-recording z have different reference periods. Heatmap rows are trial order starting at 1. Peak direction is in metadata.
Paired comparisons use matching finite trials only. paired_statistics includes two-sided Wilcoxon and paired t-test results.

事件检测 / Events
fluorescence and deconvolution are full data B and data C series. native is the unit plotted in FiberLume.
signal_z and denoised_z use the same reference, the mean and population SD (ddof=0) of finite physical data B over this entire processed recording.
Signal levels subtract that mean and divide by SD. Differences, activity, amplitudes and baseline-subtracted AUC only divide by SD.
Physical ΔF/F is recorded as ratio or percent (dff_unit). For pre-standardized input the inverse is dff = native × dff_scale + dff_offset.
Missing conversion coefficients or zero recording SD yield blank values where the conversion is undefined.
events includes event onset, true plotted peak coordinates (marker_time_s, marker_y_native), baseline, amplitude, signed AUC and its integration limits.
OASIS amplitudes use the pre-event level. Peak-mode amplitudes use the median denoised recording. AUC integrates denoised minus event baseline.
threshold.axis selects the activity panel for OASIS or the fluorescence panel for peak mode. stimulus_overlay gives the exact plotted vertical lines.
distributions stores the displayed amplitude and IEI bin edges/counts. stimulus_comparison, when present, gives per-stimulus counts and rates per minute.
Original event table columns use native units. Columns ending in _dff and _z give physical and standardized values, respectively.
"""
