"""Figures for fiber photometry results.

Every function returns a matplotlib Figure so the GUI can embed it in a
canvas; ``save_figure`` writes it to disk in the configured formats.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import TwoSlopeNorm
from matplotlib.figure import Figure

from .analysis import EventResult, PethResult
from .data import DeconvData, DffData, RawData
from .i18n import tr

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
    family = [f for f in CJK_FONTS if f in available] if lang == "zh" else []
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": family + ["DejaVu Sans", "Arial"],
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


def _stim_lines(ax, onsets, label: Optional[str] = None):
    for i, t in enumerate(onsets):
        ax.axvline(t, color=C_STIM, lw=0.8, alpha=0.5, label=label if i == 0 else None)


def fig_raw(a: RawData, lang: str = "zh") -> Figure:
    nrow = 2 if a.control is not None else 1
    fig, axes = plt.subplots(nrow, 1, figsize=(10, 2.2 * nrow), sharex=True, squeeze=False)
    axes = axes[:, 0]
    axes[0].plot(a.time, a.signal, color=C_SIGNAL, lw=0.6)
    axes[0].set_ylabel(tr("signal", lang))
    if a.control is not None:
        axes[1].plot(a.time, a.control, color=C_CONTROL, lw=0.6)
        axes[1].set_ylabel(tr("control", lang))
    for ax in axes:
        for k, (label, on) in enumerate(a.markers.items()):
            col = MARKER_COLORS[k % len(MARKER_COLORS)]
            for i, t in enumerate(on):
                ax.axvline(t, color=col, lw=0.6, alpha=0.5, label=label if i == 0 else None)
    if a.markers:
        axes[0].legend(loc="upper right", ncol=len(a.markers))
    axes[-1].set_xlabel(tr("time_s", lang))
    axes[0].set_title(tr("raw_title", lang), loc="left")
    fig.tight_layout()
    return fig


def fig_dedrift(b: DffData, onsets=(), lang: str = "zh") -> Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True)
    ax1.plot(b.time, b.signal, color=C_SIGNAL, lw=0.7, label=tr("signal", lang))
    ax1.plot(b.time, b.reference, color=C_CONTROL, lw=1.2, label=tr("reference", lang))
    ax1.set_ylabel(tr("fluor", lang))
    ax1.legend(loc="upper right", ncol=2)
    ax2.plot(b.time, b.dff, color=C_SIGNAL, lw=0.6)
    _stim_lines(ax2, onsets, tr("stimulus", lang))
    ax2.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    ax2.set_xlabel(tr("time_s", lang))
    ax1.set_title(f"{tr('dedrift_title', lang)} · {b.method}", loc="left")
    fig.tight_layout()
    return fig


def fig_deconv(c: DeconvData, b: DffData, ev: EventResult, onsets=(), lang: str = "zh") -> Figure:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(b.time, b.dff, color="#b5b4ae", lw=0.5, label=tr("dff", lang))
    ax1.plot(c.time, c.denoised, color=C_DENOISED, lw=1.0, label=tr("denoised", lang))
    if ev.method == "peak" and np.isfinite(ev.threshold):
        ax1.axhline(ev.threshold, color=INK_2, lw=0.8, ls="--", label=tr("threshold", lang))
    if len(ev.events):
        idx = np.searchsorted(c.time, ev.events["time_s"].to_numpy())
        ax1.plot(c.time[idx], c.denoised[idx], "v", color=C_EVENT, ms=5,
                 markeredgecolor="white", markeredgewidth=0.6, label=tr("events", lang))
    _stim_lines(ax1, onsets)
    ax1.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    ax1.legend(loc="upper right", ncol=4)
    ax2.vlines(c.time, 0, c.spikes, color=INK_2, lw=0.6)
    if ev.method == "oasis" and np.isfinite(ev.threshold):
        ax2.axhline(ev.threshold, color=C_EVENT, lw=0.8, ls="--", label=tr("threshold", lang))
        ax2.legend(loc="upper right")
    ax2.set_ylabel(tr("spikes", lang))
    ax2.set_xlabel(tr("time_s", lang))
    ax1.set_title(tr("deconv_title", lang), loc="left")
    fig.tight_layout()
    return fig


def _shade_windows(ax, cfg_peth, lang="zh"):
    if cfg_peth is None:
        return
    ax.axvspan(cfg_peth.baseline_start_s, cfg_peth.baseline_end_s, color="#efeee9", zorder=0,
               label=tr("baseline_win", lang))
    ax.axvspan(cfg_peth.response_start_s, cfg_peth.response_end_s, color="#e3edf9", zorder=0,
               label=tr("target_win", lang))


def _kind_label(kind: str, dff_unit: str, lang: str) -> str:
    if kind == "z" or dff_unit == "z":
        return tr("zscore", lang)
    return f"ΔF/F ({dff_unit})"


def fig_peth(p: PethResult, lang: str = "zh", cmap: str = "RdBu_r", cfg_peth=None, kind: str = "z") -> Figure:
    """Heatmap of every trial above the mean ± SEM trace; ``kind`` is "z" or "dff"."""
    trials = p.get(kind)
    label = _kind_label(kind, p.dff_unit, lang)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(5, 6), sharex=True,
                                   gridspec_kw={"height_ratios": [1.2, 1]})
    if trials.size:
        vmax = np.nanpercentile(np.abs(trials), 99) or 1.0
        norm = TwoSlopeNorm(vcenter=0, vmin=-vmax, vmax=vmax)
        im = ax1.imshow(trials, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest",
                        extent=[p.t_rel[0], p.t_rel[-1], trials.shape[0] + 0.5, 0.5])
        cb = fig.colorbar(im, ax=ax1, pad=0.02, fraction=0.05)
        cb.set_label(label)
        cb.outline.set_visible(False)
    ax1.axvline(0, color=INK, lw=0.8)
    ax1.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    ax1.tick_params(labelbottom=True)
    ax1.set_ylabel(tr("trial", lang))
    ax1.set_title(f"{tr('heatmap_title', lang)} · {p.marker} · {label}", loc="left")

    _shade_windows(ax2, cfg_peth, lang)
    if trials.size:
        m, s = p.mean(kind), p.sem(kind)
        ax2.fill_between(p.t_rel, m - s, m + s, color=C_SIGNAL, alpha=0.25, lw=0)
        ax2.plot(p.t_rel, m, color=C_SIGNAL, lw=2, label=f"{tr('mean_sem', lang)} (n={len(trials)})")
        # below the axis so it never covers the response
        ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=3, fontsize=7.5)
    ax2.axvline(0, color=INK, lw=0.8)
    ax2.axhline(0, color=GRID, lw=0.8, zorder=0)
    ax2.set_ylabel(label)
    ax2.set_xlabel(tr("time_rel", lang))
    ax2.set_title(tr("peth_title", lang), loc="left")
    # keep the heatmap and trace x-extents aligned despite the colourbar
    fig.tight_layout()
    pos1, pos2 = ax1.get_position(), ax2.get_position()
    ax2.set_position([pos2.x0, pos2.y0, pos1.width, pos2.height])
    return fig


def _paired_plot(ax, pre, post, lang, ylabel, title, stats: Optional[dict] = None,
                 labels=("pre", "post")):
    x = np.array([0, 1])
    pre, post = np.asarray(pre, float), np.asarray(post, float)
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
        title += f"\nWilcoxon p = {p:.3g}, n = {stats['n']}"
    ax.set_title(title, loc="left")


def auc_stats(p: PethResult, kind: str) -> dict:
    from .analysis import paired_test
    m = p.metrics
    if not len(m):
        return {}
    return paired_test(m[f"auc_baseline_{kind}"], m[f"auc_target_{kind}"])


def fig_auc(p: PethResult, lang: str = "zh", vertical: bool = False) -> Figure:
    """AUC of the baseline vs target window, for z-score and ΔF/F side by side."""
    fig, axes = plt.subplots(2, 1, figsize=(3.4, 6.4)) if vertical else plt.subplots(1, 2, figsize=(6.4, 3.9))
    m = p.metrics
    for ax, kind in zip(axes, ("z", "dff")):
        unit = _kind_label(kind, p.dff_unit, lang)
        _paired_plot(ax, m.get(f"auc_baseline_{kind}", []), m.get(f"auc_target_{kind}", []), lang,
                     f"AUC ({unit} · s)", unit, auc_stats(p, kind), labels=("baseline_win", "target_win"))
    fig.suptitle(tr("auc_title", lang), x=0.02, ha="left", fontsize=11)
    fig.tight_layout()
    return fig


def fig_event_distributions(ev: EventResult, lang: str = "zh", unit: str = "%") -> Figure:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 3))
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
    fig.tight_layout()
    return fig


def fig_event_rate(stim_df, stats: Optional[dict] = None, lang: str = "zh") -> Figure:
    fig, ax = plt.subplots(figsize=(3.2, 3.6))
    _paired_plot(ax, stim_df.get("pre_rate_per_min", []), stim_df.get("post_rate_per_min", []),
                 lang, tr("rate", lang), tr("rate_title", lang), stats)
    fig.tight_layout()
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
