"""End-to-end processing of one recording or a folder of recordings.

The stages are separate functions so the GUI can run them one at a time,
show the intermediate figure, and let the user tweak parameters before
moving on. ``run_file`` chains them for batch use.
"""
from __future__ import annotations

import json
import traceback
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import analysis, plotting
from .behavior import BehaviorConfig, align_behavior, load_behavior_csv
from .config import ChannelConfig, PipelineConfig
from .data import DeconvData, DffData, RawData, save_stage
from .i18n import tr
from .recordings import is_recording, read_recording, recording_family
from .preprocess import DataWarning, decay_time_constant, dedrift, deconvolve, format_dff_formula

LogFn = Callable[[str], None]
ProgressFn = Callable[[float], None]


@dataclass
class SessionResult:
    name: str
    out_dir: Path
    A: Optional[RawData] = None
    B: Optional[DffData] = None
    C: Optional[DeconvData] = None
    peth: Optional[analysis.PethResult] = None
    events: Optional[analysis.EventResult] = None
    stim_events: Optional[pd.DataFrame] = None
    summary: dict = field(default_factory=dict)
    figures: list[Path] = field(default_factory=list)
    error: str = ""


def _noop(*_a, **_k):
    pass


def pick_marker(a: RawData, wanted: str, align: str = "onset", shift_s: float = 0.0,
                *, processed: DffData | None = None) -> tuple[str, np.ndarray]:
    """Stimulus times for one marker label.

    ``align="offset"`` uses the end of each TTL pulse (for rigs where the
    stimulus follows the pulse); pulses without a recorded end fall back to
    their onset. ``shift_s`` is then added to every time.
    """
    label = wanted if wanted and wanted in a.markers else next(
        (k for k, v in a.markers.items() if len(v)), "")
    if not label:
        return "", np.array([])
    times = np.asarray(a.markers[label], dtype=float)
    if align == "offset":
        off = np.asarray(a.marker_offsets.get(label, np.full(times.size, np.nan)), dtype=float)
        if off.size == times.size:
            times = np.where(np.isnan(off), times, off)
    times = times + shift_s
    if processed is not None and processed.signal_start_s is not None:
        times = times[(times >= processed.signal_start_s) & (times <= processed.signal_end_s)]
    return label, times


def attach_behavior(raw: RawData, spec: BehaviorConfig | None) -> RawData:
    if spec is None:
        return replace(raw, behavior=None, behavior_info={}, behavior_native={})
    trace = load_behavior_csv(spec.path, spec.value_column, time_column=spec.time_column,
                              time_unit=spec.time_unit, sampling_rate_hz=spec.sampling_rate_hz)
    mapped = replace(trace, time=trace.aligned_time(raw.time[0], offset_s=spec.offset_s,
                                                   align_starts=spec.align_starts))
    aligned = align_behavior(mapped, raw.time, method=spec.method)
    info = {k: v for k, v in asdict(spec).items() if v is not None}
    info.update(path=str(Path(spec.path).resolve()), value_column=trace.value_column,
                valid_frames=int(np.isfinite(aligned).sum()), calcium_frames=len(raw.time))
    return replace(raw, behavior=aligned, behavior_info=info,
                   behavior_native={"time": mapped.time, "values": mapped.values})


def read_with_behavior(path: str | Path, cfg: PipelineConfig) -> RawData:
    raw = read_recording(path, cfg.channels)
    spec = cfg.behavior_by_recording.get(str(Path(path).resolve()))
    return attach_behavior(raw, spec) if spec is not None else raw


def run_file(path: str | Path, cfg: PipelineConfig, out_root: str | Path,
             log: LogFn = _noop, progress: ProgressFn = _noop,
             make_figures: bool = True) -> SessionResult:
    path = Path(path)
    lang = cfg.plot.language
    out = Path(out_root) / path.stem
    out.mkdir(parents=True, exist_ok=True)
    res = SessionResult(name=path.stem, out_dir=out)
    h5 = out / f"{path.stem}.h5"
    try:
        log(tr("log_read", lang, name=path.name))
        a = read_with_behavior(path, cfg)
        if h5.exists():
            h5.unlink()
        res.A = a
        log(tr("log_channels", lang, signal=a.channel_names.get("signal"),
               control=a.channel_names.get("control") or "-",
               markers=", ".join(f"{k}({len(v)})" for k, v in a.markers.items()) or "-"))
        save_stage(h5, "A", a, csv=cfg.save_csv)
        progress(0.15)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DataWarning)
            dedrift_cfg = cfg.for_recording(path).dedrift
            b = dedrift(a, dedrift_cfg)
        log(tr("log_dedrift", lang, method=b.method))
        log(format_dff_formula(a, b, lang))
        for w in caught:
            if issubclass(w.category, DataWarning):
                log(f"⚠ {w.message}")
        res.B = b
        save_stage(h5, "B", b, csv=cfg.save_csv)
        progress(0.35)

        if cfg.deconv.enabled:
            c = deconvolve(b, cfg.deconv)
            res.C = c
            log(tr("log_deconv", lang, tau=decay_time_constant(c)))
            save_stage(h5, "C", c, csv=cfg.save_csv)
        progress(0.55)

        marker, onsets = pick_marker(a, cfg.peth.marker, cfg.peth.align_to, cfg.peth.shift_s, processed=b)
        summary = {"file": path.name, "fs_raw_hz": a.fs, "fs_processed_hz": b.fs,
                   "duration_s": float(a.time[-1] - a.time[0]),
                   "indicator": a.indicator, "indicator_source": a.indicator_source, "dff_unit": b.unit,
                   "dedrift_method": b.method, "marker": marker, "n_markers": int(len(onsets))}

        if b.signal_start_s is not None:
            summary["signal_interval_s"] = [b.signal_start_s, b.signal_end_s]
        if b.method == "signal_interval":
            from .preprocess import signal_interval_baseline
            start, end = dedrift_cfg.baseline_start_s, dedrift_cfg.baseline_end_s
            f0, count = signal_interval_baseline(a, start, end)
            summary["baseline_interval"] = {"signal": a.channel_names["signal"], "start_s": start,
                                            "end_s": end, "n_samples": count, "median_f0": f0}
        if a.behavior is not None:
            summary["behavior"] = a.behavior_info
        if marker:
            p = analysis.peri_event(b, onsets, cfg.peth, marker)
            res.peth = p
            log(tr("log_peth", lang, marker=marker, n=len(p.trials)))
            for kind in ("z", "dff"):
                trials = p.get(kind)
                pd.DataFrame(trials.T, index=pd.Index(np.round(p.t_rel, 6), name="t_rel_s"),
                             columns=[f"trial_{i + 1}" for i in range(len(trials))]
                             ).assign(mean=p.mean(kind), sem=p.sem(kind)
                                      ).to_csv(out / f"peth_trials_{kind}.csv", float_format="%.6g")
            p.metrics.to_csv(out / "peth_metrics.csv", index=False, float_format="%.6g")
            if len(p.metrics):
                summary["peth_n_trials"] = len(p.trials)
                summary["peth_latency_mean_s"] = float(p.metrics["peak_latency_s"].mean())
                for kind in ("z", "dff"):
                    st = plotting.auc_stats(p, kind)
                    summary |= {f"peak_{kind}_mean": float(p.metrics[f"peak_{kind}"].mean()),
                                f"auc_baseline_{kind}_mean": float(p.metrics[f"auc_baseline_{kind}"].mean()),
                                f"auc_target_{kind}_mean": float(p.metrics[f"auc_target_{kind}"].mean()),
                                f"auc_{kind}_wilcoxon_p": st.get("wilcoxon_p", np.nan)}
        else:
            log(tr("log_no_marker", lang))
        progress(0.7)

        stats_rate = None
        if res.C is not None:
            ev = analysis.detect_events(res.C, b, cfg.events)
            res.events = ev
            log(tr("log_events", lang, n=ev.summary["n_events"], rate=ev.summary["rate_per_min"]))
            ev.events.to_csv(out / "events.csv", index=False, float_format="%.6g")
            summary |= {f"events_{k}": v for k, v in ev.summary.items()}
            if marker:
                sdf = analysis.events_around_stimuli(ev, onsets, cfg.events.compare_window_s,
                                                     b.time[0], b.time[-1])
                res.stim_events = sdf
                sdf.to_csv(out / "events_vs_stimulus.csv", index=False, float_format="%.6g")
                if len(sdf):
                    stats_rate = analysis.paired_test(sdf["pre_rate_per_min"], sdf["post_rate_per_min"])
                    summary |= {"rate_pre_mean": float(sdf["pre_rate_per_min"].mean()),
                                "rate_post_mean": float(sdf["post_rate_per_min"].mean()),
                                "rate_wilcoxon_p": stats_rate["wilcoxon_p"]}
        progress(0.8)

        res.summary = summary
        (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False,
                                                     default=float), encoding="utf-8")
        key = str(path.resolve())
        recording_behavior = {key: cfg.behavior_by_recording[key]} if a.behavior is not None else {}
        exclusions = cfg.channels.excluded_channels
        recording_channels = replace(cfg.channels, excluded_channels={key: exclusions[key]} if key in exclusions else {})
        windows = cfg.intervals_by_recording
        recording_cfg = replace(cfg, channels=recording_channels, behavior_by_recording=recording_behavior,
                                intervals_by_recording={key: windows[key]} if key in windows else {})
        recording_cfg.save(out / "parameters.json")

        if make_figures:
            res.figures = export_figures(res, cfg)
            log(tr("log_figs", lang, path=out / "figures"))
        from .method_report import write_method_report
        write_method_report(res, recording_cfg.for_recording(path))
        progress(1.0)
        log(tr("log_done", lang, name=path.name))
    except Exception as e:  # keep batch runs going; report per file
        res.error = f"{type(e).__name__}: {e}"
        log(tr("log_error", lang, name=path.name, err=res.error))
        (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    return res


def export_figures(res: SessionResult, cfg: PipelineConfig) -> list[Path]:
    lang, pc = cfg.plot.language, cfg.plot
    plotting.setup_style(lang)
    fig_dir = res.out_dir / "figures"
    saved: list[Path] = []
    save = lambda fig, name: saved.extend(plotting.save_figure(fig, fig_dir / name, pc.formats, pc.dpi))
    onsets = res.peth.onsets if res.peth is not None else ()

    if res.A is not None:
        save(plotting.fig_raw(res.A, lang), "01_raw_dataA")
    if res.B is not None:
        save(plotting.fig_dedrift(res.B, onsets, lang), "02_dedrift_dataB")
    if res.C is not None and res.events is not None:
        save(plotting.fig_deconv(res.C, res.B, res.events, onsets, lang), "03_deconv_events_dataC")
        save(plotting.fig_event_distributions(res.events, lang, res.B.unit), "06_event_distributions")
    if res.peth is not None and len(res.peth.trials):
        save(plotting.fig_peth(res.peth, lang, pc.colormap, cfg.peth, "z"), "04_peth_heatmap_z")
        save(plotting.fig_peth(res.peth, lang, pc.colormap, cfg.peth, "dff"), "04_peth_heatmap_dff")
        save(plotting.fig_auc(res.peth, lang), "05_auc_baseline_target")
    if res.stim_events is not None and len(res.stim_events):
        stats = analysis.paired_test(res.stim_events["pre_rate_per_min"],
                                     res.stim_events["post_rate_per_min"])
        save(plotting.fig_event_rate(res.stim_events, stats, lang), "07_event_rate_pre_post")
    return saved


def find_recordings(folder: str | Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*")
                  if p.suffix.lower() in (".tdms", ".csv", ".doric") and p.is_file() and is_recording(p))


def run_batch(inputs: list[str | Path], cfg: PipelineConfig, out_root: str | Path,
              log: LogFn = _noop, progress: ProgressFn = _noop,
              should_stop: Callable[[], bool] = lambda: False, *,
              mapping_source: Path | None = None,
              recording_configs: dict[Path, PipelineConfig] | None = None) -> list[SessionResult]:
    files: list[Path] = []
    for i in inputs:
        i = Path(i)
        files += find_recordings(i) if i.is_dir() else [i]
    files = list(dict.fromkeys(p.resolve() for p in files))
    stems = set()
    for path in files:
        if path.stem.casefold() in stems:
            raise ValueError(f"Duplicate output folder '{path.stem}'. Run these recordings in separate output folders.")
        stems.add(path.stem.casefold())
    results = []
    source_family = recording_family(mapping_source) if mapping_source else None
    for k, f in enumerate(files):
        if should_stop():
            break
        sub = lambda x, k=k: progress((k + x) / max(len(files), 1))
        file_cfg = cfg
        if recording_configs and f in recording_configs:
            file_cfg = recording_configs[f]
        elif mapping_source and recording_family(f) != source_family:
            file_cfg = replace(cfg, channels=ChannelConfig(marker_threshold=cfg.channels.marker_threshold,
                                                           indicator=cfg.channels.indicator,
                                                           excluded_channels=cfg.channels.excluded_channels))
        results.append(run_file(f, file_cfg, out_root, log, sub))
    rows = [r.summary | {"file": r.name, "error": r.error} for r in results]
    if rows:
        Path(out_root).mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(Path(out_root) / "batch_summary.csv", index=False, float_format="%.6g")
    return results
