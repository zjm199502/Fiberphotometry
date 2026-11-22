"""Per-recording ABET association, duration resampling and result export."""
from __future__ import annotations

import csv
import math
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np

from .abet import AbetConfig, DURATION_RESAMPLE, align_abet, export_abet, read_abet, resample_calcium
from .config import abet_association


MANIFEST_COLUMNS = ("recording", "xml", "enabled", "control_gap_s")
_LEGACY_COLUMNS = {"mode", "abet_1_s", "calcium_1_s", "abet_2_s", "calcium_2_s", "latency_s"}


def find_abet_sidecar(recording: str | Path) -> Path | None:
    """Discover only an ABET II document next to a same-stem acquisition."""
    sidecar = Path(recording).with_suffix(".xml")
    if not sidecar.is_file():
        return None
    try:
        with sidecar.open("rb") as src:
            _, root = next(ET.iterparse(src, events=("start",)))
    except (OSError, StopIteration, ET.ParseError):
        return None
    return sidecar if root.tag == "AbetSession" else None


def attach_abet(raw, spec, *, session=None):
    if raw.abet_native is not None:
        raw = raw.abet_native
    previous = set(raw.abet_marker_names.values())
    markers = {k: v for k, v in raw.markers.items() if k not in previous}
    offsets = {k: v for k, v in raw.marker_offsets.items() if k not in previous}
    if previous:
        # Old data A backups can have ABET markers on their unmodified acquisition grid.
        raw = replace(raw, markers=markers, marker_offsets=offsets, abet=None, abet_marker_names={})
    if spec is None or spec.mode == "disabled":
        return raw
    if session is not None and Path(session.path).resolve() != Path(spec.path).resolve():
        raise ValueError("ABET XML path differs from the previewed session")
    result = align_abet(session if session is not None else read_abet(spec.path), spec, raw.time)
    aligned = resample_calcium(raw, result)
    markers, offsets, names = dict(aligned.markers), dict(aligned.marker_offsets), {}
    for label, events in result.markers.items():
        if not len(events):
            continue
        name = label
        suffix = 2
        while name in markers:
            name = f"{label} ({suffix})"
            suffix += 1
        names[label] = name
        markers[name] = np.asarray(events, dtype=float)
        if label in result.marker_offsets:
            offsets[name] = np.asarray(result.marker_offsets[label], dtype=float)
    return replace(aligned, markers=markers, marker_offsets=offsets, abet=result, abet_native=raw, abet_marker_names=names)


def _number(row, key, default=None):
    value = row.get(key, "").strip()
    if not value:
        return default
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite")
    return number


def load_abet_manifest(filename: str | Path) -> dict[str, AbetConfig]:
    """Read one row per recording. All relative paths resolve beside the CSV."""
    path = Path(filename).resolve()
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as src:
        reader = csv.DictReader(src)
        fields = reader.fieldnames or []
        if "recording" not in fields or len(set(fields)) != len(fields):
            raise ValueError("配对清单须含 recording 且列名唯一 / Expected unique headers including recording")
        unknown = set(fields) - set(MANIFEST_COLUMNS) - _LEGACY_COLUMNS
        if unknown:
            raise ValueError("Unknown ABET manifest columns: " + ", ".join(sorted(unknown)))
        for line, row in enumerate(reader, 2):
            try:
                if None in row:
                    raise ValueError("Unexpected extra columns")
                row = {k: (v or "").strip() for k, v in row.items()}
                if not row["recording"]:
                    raise ValueError("Missing recording")
                recording = (path.parent / row["recording"]).resolve()
                if str(recording) in result:
                    raise ValueError(f"Duplicate recording {recording}")
                enabled = row.get("enabled", "").lower()
                mode = row.get("mode") or DURATION_RESAMPLE
                if enabled:
                    if enabled not in ("0", "1", "false", "true"):
                        raise ValueError("enabled must be 1 or 0")
                    mode = "disabled" if enabled in ("0", "false") else DURATION_RESAMPLE
                xml = ((path.parent / row["xml"]).resolve() if row.get("xml")
                       else recording.with_suffix(".xml"))
                if not recording.is_file() or (mode != "disabled" and not xml.is_file()):
                    raise ValueError(f"Missing recording or XML: {recording}, {xml}")
                gap = _number(row, "control_gap_s", 3.)
                if gap < 0:
                    raise ValueError("control_gap_s must be nonnegative")
                result[str(recording)] = abet_association(dict(path=str(xml), mode=mode, control_gap_s=gap))
            except ValueError as exc:
                raise ValueError(f"{path.name}, row {line}: {exc}") from exc
    if not result:
        raise ValueError(f"Empty ABET manifest: {path.name}")
    return result


def write_abet_manifest_template(path, recordings, configured=None):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        for recording in recordings:
            key = str(Path(recording).resolve())
            spec = (configured or {}).get(key)
            if spec is None:
                sidecar = find_abet_sidecar(recording)
                spec = AbetConfig(path=str(sidecar or ""), mode=DURATION_RESAMPLE if sidecar else "disabled")
            row = {"recording": key, "xml": spec.path, "enabled": int(spec.mode != "disabled"),
                   "control_gap_s": spec.control_gap_s}
            writer.writerow(row)


def export_recording_abet(raw, processed, out, cfg, make_figures):
    """Build an entire new directory before replacing a previous run's output."""
    from .abet_analysis import analyze_abet, export_abet_analysis

    destination = out / "abet"
    result = raw.abet
    summary = {}
    if result is None and not destination.exists():
        return summary
    with tempfile.TemporaryDirectory(dir=out, prefix=".abet-stage-") as temp:
        staged = Path(temp) / "abet"
        if result is not None:
            staged.mkdir()
            export_abet(result, staged)
            mode = result.info["mode"]
            frame = result.trials
            summary = {"abet_mode": mode, "abet_xml": str(result.session.path),
                       "abet_animal_id": result.session.animal_id, "abet_trials": len(frame),
                       "abet_responded": int((frame.outcome == "responded").sum()),
                       "abet_timeouts": int((frame.outcome == "timeout").sum()),
                       "abet_incomplete": int((frame.outcome == "incomplete").sum()),
                       "abet_regular_trials": int(frame.regular.sum())}
            for key in ("source_first_s", "source_last_s", "source_samples", "source_span_s", "source_fs_hz",
                        "aligned_first_s", "aligned_last_s", "aligned_samples", "aligned_span_s", "aligned_fs_hz",
                        "source_to_abet_scale"):
                summary["abet_" + key] = result.info[key]
            locked = analyze_abet(processed, result, peak_direction=cfg.peth.peak_direction)
            export_abet_analysis(locked, staged / "locked", language=cfg.plot.language, make_figures=make_figures)
        archived = None
        if destination.exists():
            if not (destination.is_dir() and (destination / "abet_alignment.json").is_file()):
                raise ValueError(f"Unrecognized existing output directory: {destination}")
            archived = Path(tempfile.mkdtemp(prefix="previous-abet-", dir=out)) / "abet"
            _rename_output(destination, archived)
        try:
            if result is not None:
                _rename_output(staged, destination)
        except OSError:
            if archived is not None:
                _rename_output(archived, destination)
            raise
    return summary


def _rename_output(source, destination):
    # Windows indexers can briefly hold a new export directory open after its files close.
    for delay in (0.05, 0.1, 0.2, 0.4, 0.8, None):
        try:
            source.rename(destination)
            return
        except PermissionError as exc:
            if delay is None or getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(delay)
