"""Figures for fiber photometry results.

Every function returns a matplotlib Figure so the GUI can embed it in a
canvas; ``save_figure`` writes it to disk in the configured formats.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import fill
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import TwoSlopeNorm
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

from .analysis import EventResult, PethResult, paired_test
from .data import DeconvData, DffData, RawData
from .i18n import tr
from .display_traces import plot_trace

# Categorical slots (fixed order) and text inks
C_SIGNAL = "#2a78d6"
C_CONTROL = "#eb6834"
C_DENOISED = "#1baf7a"
C_EVENT = "#e34948"
C_STIM = "#6250d6"
MARKER_COLORS = [C_STIM, "#eda100", "#e87ba4", "#008300"]
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

CJK_FONTS = ["Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC", "Noto Sans CJK SC",
             "Source Han Sans SC", "WenQuanYi Zen Hei", "Arial Unicode MS"]


def setup_style(lang: str = "zh") -> None:
    import logging
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    available = {f.name for f in font_manager.fontManager.ttflist}
    cjk = [f for f in CJK_FONTS if f in available]
    family = cjk + ["DejaVu Sans"] if lang == "zh" else ["DejaVu Sans", *cjk]
    plt.rcParams.update({
        "font.family": family,
        "axes.unicode_minus": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": INK_2,
        "axes.labelcolor": INK,
        "axes.titlesize": 11,
        "axes.labelsize": 9,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "legend.frameon": False,
        "lines.linewidth": 1.0,
        "figure.dpi": 100,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,     # editable text in Illustrator
        "svg.fonttype": "none",
    })


def set_stimulus_lines(ax, onsets, label: Optional[str] = None):
    segments = [[(t, 0), (t, 1)] for t in onsets]
    for lines in ax.collections:
        if lines.get_gid() == "stimulus-times":
            lines.set_segments(segments)
            return
    lines = LineCollection(segments, transform=ax.get_xaxis_transform(), colors=C_STIM, linewidths=.8,
                           alpha=.5, label=label)
    lines.set_gid("stimulus-times")
    ax.add_collection(lines, autolim=False)


def _outside_legend(fig, axes, columns=2):
    """Reserve a figure margin for labels across all plotted series."""
    for legend in list(fig.legends):
        legend.remove()
    unique = {}
    for ax in axes:
        handles, labels = ax.get_legend_handles_labels()
        unique.update(zip(labels, handles))
    if unique:
        fig.legend(list(unique.values()), [fill(label, 28) for label in unique],
                   loc="outside lower center", ncols=min(columns, len(unique)), fontsize=8)


def update_manual_ttl_overlay(axes, raw):
    """Update the editable marker series without rescaling the raw trace."""
    points = raw.markers.get(raw.manual_ttl_label, ())
    for ax in axes:
        for collection in list(ax.collections):
            if collection.get_gid() == "manual-ttl":
                collection.remove()
        if len(points):
            segments = [[(t, 0), (t, 1)] for t in points]
            lines = LineCollection(segments, transform=ax.get_xaxis_transform(), colors="#b44068",
                                   linewidths=1.1, linestyles="dashed", label=raw.manual_ttl_label)
            lines.set_gid("manual-ttl")
            ax.add_collection(lines, autolim=False)
    _outside_legend(axes[0].figure, axes[:1])


def fig_raw(a: RawData, lang: str = "zh") -> Figure:
    nrow = 1 + int(a.control is not None) + int(a.behavior is not None)
    legend_rows = sum(len(fill(label, 28).splitlines()) for label in a.markers)
    fig, axes = plt.subplots(nrow, 1, figsize=(10, 2.35 * nrow + .22 * np.ceil(legend_rows / 2)),
                             layout="constrained", sharex=True, squeeze=False)
    axes = axes[:, 0]
    name = a.channel_names.get("preview_signal") or a.channel_names.get("signal", "")
    ratio = a.exported_dff is not None and name == a.channel_names.get("exported_dff")
    plot_trace(axes[0], a.time, a.exported_dff if ratio else a.signal, color=C_SIGNAL, lw=0.6)
    axes[0].set_ylabel(f"{name}\nΔF/F (ratio)" if ratio else f"{a.indicator}\n{name}")
    if a.control is not None:
        plot_trace(axes[1], a.time, a.control, color=C_CONTROL, lw=0.6)
        axes[1].set_ylabel(f"{tr('control', lang)}\n{a.channel_names.get('control', '')}")
    if a.behavior is not None:
        style = "steps-post" if a.behavior_info["method"] == "previous" else "default"
        plot_trace(axes[-1], a.time, a.behavior, color="#7b4999", lw=0.8, drawstyle=style)
        label = "行为" if lang == "zh" else "Behavior"
        axes[-1].set_ylabel(f"{label}\n{a.behavior_info['value_column']}")
    for ax in axes:
        for k, (label, on) in enumerate(a.markers.items()):
            if label == a.manual_ttl_label:
                continue
            col = MARKER_COLORS[k % len(MARKER_COLORS)]
            for i, t in enumerate(on):
                ax.axvline(t, color=col, lw=0.6, alpha=0.5, label=label if i == 0 else None)
    update_manual_ttl_overlay(axes, a)
    axes[-1].set_xlabel(tr("time_s", lang))
    axes[0].set_title(tr("raw_title", lang), loc="left")
    return fig


def fig_dedrift(b: DffData, onsets=(), lang: str = "zh") -> Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True, layout="constrained")
    plot_trace(ax1, b.time, b.signal, color=C_SIGNAL, lw=0.7, label=tr("signal", lang))
    if b.reference is not None:
        reference = "F0" if b.method == "signal_interval" else tr("reference", lang)
        plot_trace(ax1, b.time, b.reference, color=C_CONTROL, lw=1.2, label=reference)
    ax1.set_ylabel(tr("fluor", lang))
    plot_trace(ax2, b.time, b.dff, color=C_SIGNAL, lw=0.6)
    set_stimulus_lines(ax2, onsets, tr("stimulus", lang))
    ax2.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    ax2.set_xlabel(tr("time_s", lang))
    title = f"{tr('dedrift_title', lang)} · {b.method}"
    window = b.formula_info.get("center_window_s", 0)
    if window:
        title += f" · {window:g} s " + ("滑动中位数校正" if lang == "zh" else "rolling median correction")
        ax2.axhline(0, color=INK_2, lw=.7, ls="--", zorder=0)
    ax1.set_title(title, loc="left")
    _outside_legend(fig, (ax1, ax2), 3)
    return fig


def fig_deconv(c: DeconvData, b: DffData, ev: EventResult, onsets=(), lang: str = "zh") -> Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
    plot_trace(ax1, b.time, b.dff, color="#b5b4ae", lw=0.5, label=tr("dff", lang))
    plot_trace(ax1, c.time, c.denoised, color=C_DENOISED, lw=1.0, label=tr("denoised", lang))
    if ev.method == "peak" and np.isfinite(ev.threshold):
        ax1.axhline(ev.threshold, color=INK_2, lw=0.8, ls="--", label=tr("threshold", lang))
    if len(ev.events):
        peak_times = ev.events.get("peak_time_s", ev.events["time_s"])
        idx = np.searchsorted(c.time, peak_times.to_numpy())
        # Anchor the downward tip to the sample, leaving the waveform visible below.
        triangle = np.array([[-0.65, 1], [0, 0], [0.65, 1], [-0.65, 1]])
        ax1.plot(c.time[idx], c.denoised[idx], linestyle="none", marker=triangle,
                 color=C_EVENT, ms=10, markeredgecolor="white", markeredgewidth=0.4,
                 label=tr("event_peaks", lang), zorder=4)
    set_stimulus_lines(ax1, onsets)
    ax1.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    plot_trace(ax2, c.time, c.spikes, spikes=True, color=INK_2, lw=0.6)
    if ev.method == "oasis" and np.isfinite(ev.threshold):
        ax2.axhline(ev.threshold, color=C_EVENT, lw=0.8, ls="--", label=tr("threshold", lang))
    ax2.set_ylabel(tr("spikes", lang))
    ax2.set_xlabel(tr("time_s", lang))
    ax1.set_title(tr("deconv_title", lang), loc="left")
    _outside_legend(fig, (ax1, ax2), 3)
    return fig


def _shade_windows(ax, cfg_peth, lang="zh"):
    if cfg_peth is None:
        return
    ax.axvspan(cfg_peth.baseline_start_s, cfg_peth.baseline_end_s, color="#efeee9", zorder=0,
               label=tr("baseline_win", lang))
    ax.axvspan(cfg_peth.response_start_s, cfg_peth.response_end_s, color="#e3edf9", zorder=0,
               label=tr("target_win", lang))


def _kind_label(kind: str, dff_unit: str, lang: str) -> str:
    if kind == "z":
        return tr("peth_z", lang)
    unit = tr("zscore", lang) if dff_unit == "z" else f"ΔF/F ({dff_unit})"
    return tr("peth_centered", lang, unit=unit)


def fig_peth(p: PethResult, lang: str = "zh", cmap: str = "RdBu_r", cfg_peth=None, kind: str = "z") -> Figure:
    """Heatmap of every trial above the mean ± SEM trace; ``kind`` is "z" or "dff"."""
    trials = p.get(kind)
    label = tr("zscore", lang) if kind == "z" or p.dff_unit == "z" else f"ΔF/F ({p.dff_unit})"
    fig = plt.figure(figsize=(5.8, 6), layout="constrained")
    grid = fig.add_gridspec(2, 2, height_ratios=[1.2, 1], width_ratios=[25, 1])
    ax1 = fig.add_subplot(grid[0, 0])
    ax2 = fig.add_subplot(grid[1, 0], sharex=ax1)
    if trials.size:
        vmax = np.nanpercentile(np.abs(trials), 99) or 1.0
        norm = TwoSlopeNorm(vcenter=0, vmin=-vmax, vmax=vmax)
        im = ax1.imshow(trials, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest",
                        extent=[p.t_rel[0], p.t_rel[-1], trials.shape[0] + 0.5, 0.5])
        cb = fig.colorbar(im, cax=fig.add_subplot(grid[0, 1]))
        cb.set_label(label)
        cb.outline.set_visible(False)
    ax1.axvline(0, color=INK, lw=0.8)
    ax1.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax1.tick_params(labelbottom=True)
    ax1.set_ylabel(tr("trial", lang))
    ax1.set_title(fill(f"{tr('heatmap_title', lang)} · {p.marker}", 38), loc="left")

    _shade_windows(ax2, cfg_peth, lang)
    if trials.size:
        m, s = p.mean(kind), p.sem(kind)
        ax2.fill_between(p.t_rel, m - s, m + s, color=C_SIGNAL, alpha=0.25, lw=0)
        ax2.plot(p.t_rel, m, color=C_SIGNAL, lw=2, label=f"{tr('mean_sem', lang)} (n={len(trials)})")
    ax2.axvline(0, color=INK, lw=0.8)
    ax2.axhline(0, color=GRID, lw=0.8, zorder=0)
    ax2.set_ylabel(label)
    ax2.set_xlabel(tr("time_rel", lang))
    ax2.set_title(tr("peth_title", lang), loc="left")
    _outside_legend(fig, [ax2], 2)
    return fig


def _paired_plot(ax, pre, post, lang, ylabel, title, stats: Optional[dict] = None,
                 labels=("pre", "post")):
    x = np.array([0, 1])
    pre, post = np.asarray(pre, float), np.asarray(post, float)
    valid = np.isfinite(pre) & np.isfinite(post)
    pre, post = pre[valid], post[valid]
    jit = np.random.default_rng(0).uniform(-0.05, 0.05, len(pre))  # separate tied values
    for j, a, b in zip(jit, pre, post):
        ax.plot(x + j, [a, b], color="#b5b4ae", lw=0.8, zorder=1)
    ax.scatter(jit, pre, s=28, color=C_CONTROL, edgecolor="white", lw=0.8, zorder=2)
    ax.scatter(1 + jit, post, s=28, color=C_SIGNAL, edgecolor="white", lw=0.8, zorder=2)
    for xi, v, col in ((0, pre, C_CONTROL), (1, post, C_SIGNAL)):
        if len(v):
            mu = np.nanmean(v)
            se = np.nanstd(v, ddof=1) / np.sqrt(len(v)) if len(v) > 1 else 0
            ax.errorbar(xi + 0.18, mu, yerr=se, fmt="o", color=INK, ms=5, capsize=3, lw=1.2, zorder=3)
    ax.set_xticks(x, [tr(labels[0], lang), tr(labels[1], lang)])
    ax.set_xlim(-0.4, 1.5)
    ax.set_ylabel(ylabel)
    p = (stats or {}).get("wilcoxon_p") if (stats or {}).get("n", 0) >= 3 else None
    if p is not None and not np.isnan(p):
        title += f"\nWilcoxon p = {p:.3g}\nn = {stats['n']}"
    ax.set_title(title, loc="left")


def auc_stats(p: PethResult, kind: str) -> dict:
    return paired_test(*p.paired_values("auc", kind)) if len(p.metrics) else {}


def fig_peth_comparisons(p: PethResult, lang: str = "zh", *, kind=None, metric="auc", vertical=False) -> Figure:
    """Show both measures in the active GUI unit, or export one measure in both units."""
    panels = [("auc", kind), ("peak", kind)] if kind else [(metric, "z"), (metric, "dff")]
    if kind or vertical:
        fig, axes = plt.subplots(2, 1, figsize=(3.5, 6.4), layout="constrained")
    else:
        fig, axes = plt.subplots(1, 2, figsize=(7, 3.9), layout="constrained")
    for ax, (measure, scale) in zip(axes, panels):
        pre, post = p.paired_values(measure, scale)
        unit = "z" if scale == "z" else p.dff_unit
        name = tr("auc" if measure == "auc" else f"peak_{p.peak_direction}", lang)
        ylabel = f"{name} ({unit} · s)" if measure == "auc" else f"{name} ({unit})"
        stats = paired_test(pre, post)
        title = name if kind else _kind_label(scale, p.dff_unit, lang)
        _paired_plot(ax, pre, post, lang, ylabel, title, stats, labels=("baseline_win", "target_win"))
        if stats["n"] < 3 or not np.isfinite(stats["wilcoxon_p"]):
            ax.set_title(f"{title}\nn = {stats['n']}", loc="left")
    return fig


def fig_auc(p: PethResult, lang: str = "zh", vertical: bool = False) -> Figure:
    return fig_peth_comparisons(p, lang, metric="auc", vertical=vertical)


def fig_event_distributions(ev: EventResult, lang: str = "zh", unit: str = "%") -> Figure:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3), layout="constrained")
    e = ev.events
    if len(e):
        ax1.hist(e["amplitude"], bins=min(30, max(5, len(e) // 3)), color=C_SIGNAL,
                 edgecolor="white", lw=0.8)
    if len(e) > 1:
        ax2.hist(e["iei_s"].dropna(), bins=min(30, max(5, len(e) // 3)), color=C_DENOISED,
                 edgecolor="white", lw=0.8)
    unit_txt = tr("zscore", lang) if unit == "z" else f"ΔF/F {unit}"
    ax1.set_xlabel(f"{tr('amplitude', lang)} ({unit_txt})")
    ax1.set_ylabel(tr("count", lang))
    ax1.set_title(tr("amp_hist", lang), loc="left")
    ax2.set_xlabel(tr("iei_s", lang))
    ax2.set_title(tr("iei_hist", lang), loc="left")
    return fig


def fig_event_rate(stim_df, stats: Optional[dict] = None, lang: str = "zh") -> Figure:
    fig, ax = plt.subplots(figsize=(3.5, 3.6), layout="constrained")
    _paired_plot(ax, stim_df.get("pre_rate_per_min", []), stim_df.get("post_rate_per_min", []),
                 lang, tr("rate", lang), fill(tr("rate_title", lang), 23), stats)
    return fig


def save_figure(fig: Figure, out_base: str | Path, formats=("png",), dpi: int = 300) -> list[Path]:
    out_base = Path(out_base)
    out_base.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for fmt in formats:
        p = out_base.with_suffix(f".{fmt}")
        fig.savefig(p, dpi=dpi)
        paths.append(p)
    plt.close(fig)
    return paths
