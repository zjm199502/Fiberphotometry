"""Identify source-specific marker outputs superseded by a new analysis."""
import hashlib
import json
from pathlib import Path
from tempfile import mkdtemp


TRIAL_TABLES = {"peth_trials_z.csv", "peth_trials_dff.csv", "peth_metrics.csv", "peth_paired_stats.csv"}
TRIAL_PLOTS = {"02_dedrift_dataB", "03_deconv_events_dataC", "04_peth_heatmap_z", "04_peth_heatmap_dff",
               "05_auc_baseline_target", "05_peak_baseline_target", "07_event_rate_pre_post"}


def marker_outputs(res, cfg, manual_only=False, selected_labels=None):
    """Paths actually written by this run that may contain the selected markers."""
    out = res.out_dir
    outputs = set()
    if cfg.save_csv and res.A.markers:
        outputs.add(out / f"{res.name}_dataA_markers.csv")
    outputs.update(p for p in res.figures if p.stem == "01_raw_dataA")
    if manual_only and res.summary.get("marker") != res.A.manual_ttl_label:
        return outputs
    if selected_labels is not None and res.summary.get("marker") not in selected_labels:
        return outputs
    if res.peth is not None:
        outputs.update(out / name for name in TRIAL_TABLES)
    if res.stim_events is not None:
        outputs.add(out / "events_vs_stimulus.csv")
    outputs.update(p for p in res.figures if p.stem in TRIAL_PLOTS)
    return outputs


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _superseded_outputs(res, cfg, digest_key):
    report_file = res.out_dir / "analysis_methods.json"
    if not report_file.is_file():
        return []
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    source = str(Path(res.A.source).resolve())
    if not isinstance(report, dict) or report.get("recording") != source:
        return []
    if digest_key == "manual_ttl_output_sha256":
        configured = report.get("configured_parameters")
        manual = configured.get("manual_ttl_by_recording") if isinstance(configured, dict) else None
        if res.A.manual_ttl_label or not isinstance(manual, dict) or not manual.get(source):
            return []
    previous = report.get(digest_key, {})
    if not isinstance(previous, dict) or not previous:
        return []
    # Construct candidates from known output names rather than treating report
    # keys as paths. Compare contents too, in case the user replaced a file.
    out = res.out_dir
    candidates = {out / name for name in TRIAL_TABLES | {"events_vs_stimulus.csv", f"{res.name}_dataA_markers.csv"}}
    figs = out / "figures"
    if figs.is_dir() and figs.resolve() == out.resolve() / "figures":
        candidates.update(p for p in figs.iterdir() if p.stem in TRIAL_PLOTS | {"01_raw_dataA"})
    stale = []
    for p in candidates - marker_outputs(res, cfg):
        rel = p.relative_to(out)
        digest = previous.get(rel.as_posix())
        if (digest and p.is_file() and p.resolve() == out.resolve() / rel
                and str(p.resolve()) != source and sha256(p) == digest):
            stale.append(p)
    return stale


def superseded_manual_outputs(res, cfg):
    return _superseded_outputs(res, cfg, "manual_ttl_output_sha256")


def superseded_abet_outputs(res, cfg):
    return _superseded_outputs(res, cfg, "abet_marker_output_sha256")


def archive_superseded(out, paths, prefix="previous-manual-ttl-"):
    folder = Path(mkdtemp(prefix=prefix, dir=out))
    moved = []
    try:
        for src in paths:
            dest = folder / src.relative_to(out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            moved.append((src, dest))
    except OSError:
        for original, archived in reversed(moved):
            archived.rename(original)
        raise
    return folder
