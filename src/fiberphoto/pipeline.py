"""End-to-end processing of one recording or a folder of recordings.

The stages are separate functions so the GUI can run them one at a time,
show the intermediate figure, and let the user tweak parameters before
moving on. ``run_file`` chains them for batch use.
"""
from __future__ import annotations

import json
import hashlib
import traceback
import warnings
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import analysis, plotting
from .abet import AbetConfig, DURATION_RESAMPLE
from .abet_batch import attach_abet, export_recording_abet, find_abet_sidecar, load_abet_manifest
from .behavior import BehaviorConfig, align_behavior, load_behavior_csv
from .config import ChannelConfig, PipelineConfig
from .data import DeconvData, DffData, RawData, save_stage
from .i18n import tr
from .recordings import is_recording, read_recording, recording_family
from .stimulus_markers import configured_markers, resolve_markers
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
    markers = resolve_markers(a, wanted, align, shift_s, processed=processed)
    return markers.label, markers.times


def attach_behavior(raw: RawData, spec: BehaviorConfig | None) -> RawData:
    source = raw.abet_native if raw.abet_native is not None else raw
    if spec is None:
        clean = replace(source, behavior=None, behavior_info={}, behavior_native={})
        return (replace(raw, behavior=None, behavior_info={}, behavior_native={}, abet_native=clean)
                if raw.abet_native is not None else clean)
    trace = load_behavior_csv(spec.path, spec.value_column, time_column=spec.time_column,
                              time_unit=spec.time_unit, sampling_rate_hz=spec.sampling_rate_hz)
    mapped = replace(trace, time=trace.aligned_time(source.time[0], offset_s=spec.offset_s,
                                                   align_starts=spec.align_starts))
    aligned = align_behavior(mapped, source.time, method=spec.method)
    info = {k: v for k, v in asdict(spec).items() if v is not None}
    info.update(path=str(Path(spec.path).resolve()), value_column=trace.value_column,
                valid_frames=int(np.isfinite(aligned).sum()), calcium_frames=len(source.time))
    acquisition = replace(source, behavior=aligned, behavior_info=info,
                          behavior_native={"time": mapped.time, "values": mapped.values})
    if raw.abet_native is None:
        return acquisition
    mapped = replace(mapped, time=raw.from_acquisition_time(mapped.time))
    aligned = align_behavior(mapped, raw.time, method=spec.method, require_overlap=False)
    info = dict(info, valid_frames=int(np.isfinite(aligned).sum()), calcium_frames=len(raw.time), clock=DURATION_RESAMPLE)
    return replace(raw, behavior=aligned, behavior_info=info,
                   behavior_native={"time": mapped.time, "values": mapped.values}, abet_native=acquisition)


def read_with_behavior(path: str | Path, cfg: PipelineConfig) -> RawData:
    raw = read_recording(path, cfg.channels)
    key = str(Path(path).resolve())
    if cfg.manual_ttl_by_recording.get(key):
        raw = raw.with_manual_ttl(cfg.manual_ttl_by_recording[key])
    spec = cfg.behavior_by_recording.get(key)
    if spec is not None:
        raw = attach_behavior(raw, spec)
    abet = cfg.abet_by_recording.get(key)
    if abet is None:
        sidecar = find_abet_sidecar(path)
        if sidecar is not None:
            abet = AbetConfig(path=str(sidecar.resolve()))
    return attach_abet(raw, abet) if abet is not None else raw


def run_file(path: str | Path, cfg: PipelineConfig, out_root: str | Path,
             log: LogFn = _noop, progress: ProgressFn = _noop,
             make_figures: bool = True, *, distinguish_stem: bool = False) -> SessionResult:
    path = Path(path)
    lang = cfg.plot.language
    out = Path(out_root) / path.stem
    if not distinguish_stem and (out / "analysis_methods.json").is_file():
        try:
            source = json.loads((out / "analysis_methods.json").read_text(encoding="utf-8")).get("recording")
            distinguish_stem = bool(source and Path(source).resolve() != path.resolve())
        except (AttributeError, TypeError, OSError, ValueError):
            distinguish_stem = True
    if distinguish_stem:
        suffix = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
        out = out.with_name(f"{path.stem}--{suffix}")
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
            dedrift_cfg = cfg.for_recording(path, raw=a).dedrift
            b = dedrift(a, dedrift_cfg)
        log(tr("log_dedrift", lang, method=b.method))
        log(format_dff_formula(a, b, lang))
        for w in caught:
            if issubclass(w.category, DataWarning):
                log(f"⚠ {w.message}")
        res.B = b
        save_stage(h5, "B", b, csv=cfg.save_csv, full_precision=a.abet is not None)
        progress(0.35)

        if cfg.deconv.enabled:
            c = deconvolve(b, cfg.deconv)
            res.C = c
            log(tr("log_deconv", lang, tau=decay_time_constant(c)))
            save_stage(h5, "C", c, csv=cfg.save_csv, full_precision=a.abet is not None)
        progress(0.55)

        selected = configured_markers(a, cfg.peth, processed=b)
        marker, onsets = selected.label, selected.times
        summary = {"file": path.name, "fs_raw_hz": a.abet_native.fs if a.abet_native is not None else a.fs,
                   "fs_data_A_hz": a.fs, "fs_processed_hz": b.fs,
                   "duration_s": float(a.time[-1] - a.time[0]),
                   "indicator": a.indicator, "indicator_source": a.indicator_source, "dff_unit": b.unit,
                   "dedrift_method": b.method, "marker": marker, "marker_sources": list(selected.sources),
                   "n_markers": int(len(onsets))}

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
        summary.update(export_recording_abet(a, b, out, cfg, make_figures))
        if a.abet is not None:
            log(f"ABET · {summary['abet_mode']} · {summary['abet_trials']} trials · {out / 'abet'}")
        if marker:
            p = selected.analyze(b, cfg.peth)
            res.peth = p
            log(tr("log_peth", lang, marker=marker, n=len(p.trials)))
            if p.skipped_incomplete_pre:
                log(tr("log_nonfinite_pre", lang, n=p.skipped_incomplete_pre))
            for kind in ("z", "dff"):
                trials = p.get(kind)
                pd.DataFrame(trials.T, index=pd.Index(np.round(p.t_rel, 6), name="t_rel_s"),
                             columns=[f"trial_{i + 1}" for i in range(len(trials))]
                             ).assign(mean=p.mean(kind), sem=p.sem(kind)
                                      ).to_csv(out / f"peth_trials_{kind}.csv", float_format="%.17g")
            p.metrics.to_csv(out / "peth_metrics.csv", index=False, float_format="%.17g")
            if len(p.metrics):
                summary["peth_n_trials"] = len(p.trials)
                summary["peth_latency_mean_s"] = float(p.metrics["peak_latency_s"].mean())
            summary["peth_peak_direction"] = p.peak_direction
            comparisons = []
            for kind in ("z", "dff"):
                for metric in ("auc", "peak"):
                    pre, post = p.paired_values(metric, kind)
                    valid = np.isfinite(pre) & np.isfinite(post)
                    stats = analysis.paired_test(pre, post)
                    baseline = float(pre[valid].mean()) if valid.any() else np.nan
                    target = float(post[valid].mean()) if valid.any() else np.nan
                    trace_unit = "z" if kind == "z" else b.unit
                    comparisons.append({"metric": metric, "trace": kind,
                                        "unit": f"{trace_unit}·s" if metric == "auc" else trace_unit,
                                        "peak_direction": p.peak_direction if metric == "peak" else "",
                                        "baseline_mean": baseline, "target_mean": target, **stats})
                    summary[f"{metric}_baseline_{kind}_mean"] = baseline
                    summary[f"{metric}_target_{kind}_mean"] = target
                    summary[f"{metric}_{kind}_wilcoxon_p"] = stats["wilcoxon_p"]
                    if metric == "peak":
                        summary[f"peak_{kind}_mean"] = target
            pd.DataFrame(comparisons).to_csv(out / "peth_paired_stats.csv", index=False, float_format="%.17g")
        else:
            log(tr("log_no_marker", lang))
        progress(0.7)

        stats_rate = None
        if res.C is not None:
            ev = analysis.detect_events(res.C, b, cfg.events)
            res.events = ev
            log(tr("log_events", lang, n=ev.summary["n_events"], rate=ev.summary["rate_per_min"]))
            ev.to_csv(out / "events.csv", b.unit)
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
                                abet_by_recording={key: cfg.abet_by_recording[key]} if key in cfg.abet_by_recording else
                                ({key: AbetConfig(path=str(a.abet.session.path))} if a.abet is not None else {}),
                                intervals_by_recording={key: windows[key]} if key in windows else {},
                                manual_ttl_by_recording={key: cfg.manual_ttl_by_recording[key]}
                                if key in cfg.manual_ttl_by_recording else {})
        recording_cfg.save(out / "parameters.json")

        if make_figures:
            res.figures = export_figures(res, cfg)
            log(tr("log_figs", lang, path=out / "figures"))
        from .marker_exports import archive_superseded, superseded_manual_outputs, superseded_abet_outputs
        from .method_report import write_method_report
        stale_abet = superseded_abet_outputs(res, recording_cfg)
        stale = set(superseded_manual_outputs(res, recording_cfg)) | set(stale_abet)
        report_path = out / "analysis_methods.json"
        previous = report_path.read_bytes() if stale else None
        try:
            write_method_report(res, recording_cfg)
            if stale:
                archive = archive_superseded(out, stale, prefix="previous-abet-markers-") if stale_abet else archive_superseded(out, stale)
                log(f"已归档旧打标结果 / Previous marker outputs archived: {archive}")
        except OSError:
            if previous is not None:
                report_path.write_bytes(previous)  # Retain provenance for a retry after a locked file.
            raise
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
        save(plotting.fig_peth_comparisons(res.peth, lang, metric="peak"), "05_peak_baseline_target")
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
              recording_configs: dict[Path, PipelineConfig] | None = None,
              abet_manifest: str | Path | None = None, retain_results: bool = True) -> list[SessionResult]:
    files: list[Path] = []
    for i in inputs:
        i = Path(i)
        files += find_recordings(i) if i.is_dir() else [i]
    files = list(dict.fromkeys(p.resolve() for p in files))
    stems = Counter(path.stem.casefold() for path in files)
    associations = load_abet_manifest(abet_manifest) if abet_manifest else {}
    if abet_manifest and not any(str(path) in associations for path in files):
        raise ValueError("同步清单与选中文件不匹配 / No selected recording matches the ABET manifest")
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
        if str(f) in associations:
            file_cfg = replace(file_cfg, abet_by_recording={str(f): associations[str(f)]})
        result = run_file(f, file_cfg, out_root, log, sub, distinguish_stem=stems[f.stem.casefold()] > 1)
        if not retain_results:
            result.A = result.B = result.C = result.peth = result.events = result.stim_events = None
        results.append(result)
    rows = [r.summary | {"file": r.name, "recording_path": str(f), "output_directory": r.out_dir.name,
                         "error": r.error} for r, f in zip(results, files)]
    if rows:
        Path(out_root).mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(Path(out_root) / "batch_summary.csv", index=False, float_format="%.6g")
        groups = []
        for res, row in zip(results, rows):
            if res.error or row.get("abet_mode") != DURATION_RESAMPLE:
                continue
            table = pd.read_csv(res.out_dir / "abet" / "locked" / "groups.csv")
            groups.append(table.assign(recording_path=row["recording_path"], animal_id=row["abet_animal_id"],
                                       sync_mode=row["abet_mode"], dff_unit=row["dff_unit"]))
        group_file = Path(out_root) / "batch_abet_groups.csv"
        if groups or group_file.exists():
            group_table = (pd.concat(groups, ignore_index=True) if groups else
                           pd.DataFrame(columns=["group", "n_candidates", "n_used", "mean_target_dff", "mean_signed_auc",
                                                 "recording_path", "animal_id", "sync_mode", "dff_unit"]))
            group_table.to_csv(group_file, index=False, float_format="%.12g")
    return results
