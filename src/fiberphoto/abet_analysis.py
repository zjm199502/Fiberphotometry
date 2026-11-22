"""Cue and tray-entry analysis using calcium resampled onto the ABET clock."""
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from .analysis import peri_event
from .abet_resampling import DURATION_RESAMPLE
from .config import PethConfig
from .plotting import _outside_legend, setup_style


LABELS = {
    "cue_responded": ("声音 · 有效响应", "Cue · responded"),
    "cue_timeout": ("声音 · 超时", "Cue · timeout"),
    "cue_special": ("声音 · 特殊时序", "Cue · special timing"),
    "cue_incomplete": ("声音 · 试次不完整", "Cue · incomplete trial"),
    "tray_feeder": ("有效进槽 + 给食指令", "Tray entry + feeder command"),
    "tray_outside": ("独立窗外进槽", "Isolated out-of-window entry"),
}
COLORS = {"cue_responded": "#a73362", "cue_timeout": "#376da3",
          "tray_feeder": "#a73362", "tray_outside": "#39846a"}
METRICS = ["prestimulus_mean_input_dff", "target_mean_dff", "auc_target_dff", "peak_dff", "peak_latency_s"]


@dataclass
class AbetAnalysis:
    metrics: pd.DataFrame
    traces: pd.DataFrame
    groups: pd.DataFrame
    parameters: dict


def _events(result):
    for trial in result.trials.itertuples():
        if trial.outcome == "incomplete":
            group = "cue_incomplete"
        elif not trial.regular:
            group = "cue_special"
        else:
            group = "cue_" + trial.outcome
        yield trial.trial, group, trial.cue_on_s, trial.traylight_on_s
        if trial.outcome == "responded" and np.isfinite(trial.tray_input_on_s):
            yield trial.trial, "tray_feeder", trial.tray_input_on_s, trial.traylight_on_s
    for entry in result.control_onsets_s:
        yield None, "tray_outside", entry, np.nan


def analyze_abet(b, aligned, *, peak_direction="positive"):
    """Compute single-trial descriptive metrics, retaining excluded trials in the ledger."""
    mode = aligned.info["mode"]
    if mode != DURATION_RESAMPLE:
        raise ValueError("请重新读取并重采样钙记录 / Reload and resample the recording on the ABET clock")
    if not np.isfinite(b.fs) or round(3 * b.fs) < 1:
        raise ValueError("ABET 的 3 秒基线内至少需要一帧 / The 3-second baseline must contain a sample")
    settings = {lock: PethConfig(pre_s=3, post_s=post, baseline_start_s=-3,
                                 baseline_end_s=0, response_start_s=0, response_end_s=3,
                                 peak_direction=peak_direction)
                for lock, post in (("cue", 6), ("tray", 3))}
    metrics, traces = [], []
    t = np.asarray(b.time)
    for event_id, (trial, group, native, lamp) in enumerate(_events(aligned), 1):
        onset = native
        lock = "cue" if group.startswith("cue_") else "tray"
        cfg = settings[lock]
        reason = ""
        index = int(np.searchsorted(t, onset))
        left = index - round(cfg.pre_s * b.fs)
        right = index + round(cfg.post_s * b.fs)
        if not np.isfinite(onset):
            reason = "missing_onset"
        elif group == "cue_incomplete":
            reason = "incomplete_trial"
        elif left < 0 or right >= len(t):
            reason = "outside_analysis_window"
        elif (abs(t[index] - onset) > 1.5 / b.fs
              or np.any(np.diff(t[left:right + 1]) > 1.5 / b.fs)):
            reason = "acquisition_gap"
        elif not np.isfinite(b.dff[left:right + 1]).all():
            reason = "nonfinite_signal"
        record = dict(event_id=event_id, trial=trial, group=group, abet_onset_s=native,
                      calcium_onset_s=onset, light_relative_s=lamp-native,
                      sync_mode=mode, included=not reason, exclusion=reason)
        if not reason:
            p = peri_event(b, np.array([onset]), cfg, marker=group)
            record.update(p.metrics.loc[0, METRICS].to_dict())
            traces.append(pd.DataFrame({"event_id": event_id, "trial": trial, "group": group,
                                        "t_rel_s": p.t_rel, "dff_centered": p.trials_dff[0]}))
        metrics.append(record)
    columns = ["event_id", "trial", "group", "abet_onset_s", "calcium_onset_s", "light_relative_s",
               "sync_mode", "included", "exclusion"] + METRICS
    ledger = pd.DataFrame(metrics, columns=columns)
    ledger["trial"] = ledger.trial.astype("Int64")
    trace_columns = ["event_id", "trial", "group", "t_rel_s", "dff_centered"]
    trace = pd.concat(traces, ignore_index=True) if traces else pd.DataFrame(columns=trace_columns)
    trace["trial"] = trace.trial.astype("Int64")
    groups = []
    for group in LABELS:
        rows = ledger[ledger.group == group]
        use = rows[rows.included.astype(bool)]
        groups.append(dict(group=group, n_candidates=len(rows), n_used=len(use),
                           mean_target_dff=use.target_mean_dff.mean(), mean_signed_auc=use.auc_target_dff.mean()))
    params = dict(sync_mode=mode, dff_unit=b.unit,
                  alignment=aligned.info, dff_method=b.method, sampling_rate_hz=b.fs,
                  peak_direction=peak_direction, baseline_s=[-3., 0.], response_s=[0., 3.],
                  display_s={"cue": [-3., 6.], "tray": [-3., 3.]},
                  centering="B(t) - mean(B[-3, 0)) per event; zero is the first resampled frame at or after the ABET event",
                  auc="Signed trapezoidal integral of centered B over [0, 3] seconds",
                  aggregation="Session-level descriptive means across complete events; special cues reported separately",
                  source="ABET session event log, user-defined cue/tray protocol; FiberLume analysis.peri_event")
    return AbetAnalysis(ledger, trace, pd.DataFrame(groups), params)


def figure_abet_analysis(result, language="zh"):
    """Show cue trials in acquisition order and compare single-session means."""
    setup_style(language)
    en = language == "en"
    fig = Figure(figsize=(10, 6), constrained_layout=True)
    axes = fig.subplot_mosaic([["heat", "cue"], ["entries", "tray"]])
    trace = result.traces
    for panel, keys, title in (("heat", ("cue_responded", "cue_timeout"), ("常规声音试次", "Standard cue trials")),
                               ("entries", ("tray_feeder",), ("有效进槽试次", "Accepted tray entries"))):
        heat = axes[panel]
        data = trace[trace.group.isin(keys)]
        if len(data):
            matrix = data.pivot(index="trial", columns="t_rel_s", values="dff_centered")
            limit = max(float(np.quantile(np.abs(matrix.values), .98)), 1e-12)
            dt = float(matrix.columns[1] - matrix.columns[0])
            im = heat.imshow(matrix.values, cmap="RdBu_r", vmin=-limit, vmax=limit, aspect="auto",
                             extent=(matrix.columns[0]-dt/2, matrix.columns[-1]+dt/2, len(matrix)-.5, -.5),
                             interpolation="nearest")
            fig.colorbar(im, ax=heat, label=result.parameters["dff_unit"], shrink=.7)
            ticks = np.unique(np.linspace(0, len(matrix)-1, min(7, len(matrix))).astype(int))
            heat.set_yticks(ticks, labels=matrix.index[ticks].astype(str))
            if panel == "entries":
                entries = result.metrics[result.metrics.group == "tray_feeder"].set_index("trial")
                lamp = entries.loc[matrix.index, "light_relative_s"].to_numpy(float)
                inside = (lamp >= matrix.columns[0]) & (lamp <= matrix.columns[-1])
                if inside.any():
                    heat.scatter(lamp[inside], np.flatnonzero(inside), c="#e69a19", edgecolors="black",
                                 s=24, marker="D", label="Tray light on" if en else "食槽灯亮")
        else:
            heat.text(.5, .5, "No complete trials" if en else "无完整试次", ha="center", transform=heat.transAxes)
        heat.set_title(title[int(en)])
        heat.set_ylabel("Trial number" if en else "试次序号")
    for name, keys in (("cue", ("cue_responded", "cue_timeout")), ("tray", ("tray_feeder", "tray_outside"))):
        ax = axes[name]
        for group in keys:
            g = trace[trace.group == group]
            if not len(g):
                continue
            values = g.groupby("t_rel_s").dff_centered.mean()
            ax.plot(values.index, values, color=COLORS[group],
                    label=f"{LABELS[group][int(en)]} (n={g.event_id.nunique()})")
        ax.set_ylabel(f"ΔF/F ({result.parameters['dff_unit']})")
        ax.set_title((("Cue onset", "Tray entry") if en else ("声音开始", "进槽"))[int(name == "tray")])
        ax.axhline(0, color="0.65", lw=.7)
    for ax in axes.values():
        ax.axvline(0, color="#555555", ls=":", lw=1)
        ax.set_xlabel("Event time (s)" if en else "相对事件时间 (s)")
    fig.suptitle("Calcium resampled to the ABET timeline" if en else "按 ABET 实验时长重采样的钙信号")
    _outside_legend(fig, axes.values(), 2)
    return fig


def export_abet_analysis(result, out, *, language="zh", make_figures=True):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("metrics", "traces", "groups"):
        getattr(result, name).to_csv(out / (name + ".csv"), index=False, float_format="%.12g")
    (out / "analysis.json").write_text(json.dumps(result.parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    if make_figures:
        figure_abet_analysis(result, language).savefig(out / "overview.png", dpi=160)
