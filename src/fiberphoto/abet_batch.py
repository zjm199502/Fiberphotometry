"""Per-recording ABET association, batch clock manifest and result export."""
from __future__ import annotations

import csv
import math
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np

from .abet import AbetConfig, align_abet, export_abet, read_abet


MANIFEST_COLUMNS = ("recording", "xml", "mode", "abet_1_s", "calcium_1_s",
                    "abet_2_s", "calcium_2_s", "latency_s", "control_gap_s")


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
    previous = set(raw.abet_marker_names.values())
    markers = {k: v for k, v in raw.markers.items() if k not in previous}
    offsets = {k: v for k, v in raw.marker_offsets.items() if k not in previous}
    names = {}
    result = None
    if spec is not None and spec.mode != "disabled":
        if session is not None and Path(session.path).resolve() != Path(spec.path).resolve():
            raise ValueError("ABET XML path differs from the previewed session")
        result = align_abet(session if session is not None else read_abet(spec.path), spec, raw.time)
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
    return replace(raw, markers=markers, marker_offsets=offsets, abet=result, abet_marker_names=names)


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
        if not {"recording", "mode"} <= set(fields) or len(set(fields)) != len(fields):
            raise ValueError("同步清单须含 recording、mode 且列名唯一 / Expected unique headers with recording and mode")
        unknown = set(fields) - set(MANIFEST_COLUMNS)
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
                mode = row["mode"]
                if mode not in ("disabled", "behavior_only", "assumed_trigger", "offset", "affine"):
                    raise ValueError(f"Unknown sync mode {mode!r}")
                xml = ((path.parent / row["xml"]).resolve() if row.get("xml")
                       else recording.with_suffix(".xml"))
                if not recording.is_file() or (mode != "disabled" and not xml.is_file()):
                    raise ValueError(f"Missing recording or XML: {recording}, {xml}")
                anchors = []
                for i in (1, 2):
                    pair = [_number(row, f"abet_{i}_s"), _number(row, f"calcium_{i}_s")]
                    if pair != [None, None]:
                        if None in pair or len(anchors) != i-1:
                            raise ValueError(f"Incomplete anchor {i}")
                        anchors.append(pair)
                count = {"offset": 1, "affine": 2}.get(mode, 0)
                if len(anchors) != count:
                    raise ValueError(f"{mode}: expected {count} time pair(s)")
                latency = _number(row, "latency_s", 0.)
                if latency and mode != "assumed_trigger":
                    raise ValueError("latency_s applies only to assumed_trigger")
                result[str(recording)] = AbetConfig(path=str(xml), mode=mode, anchors=anchors,
                                                    latency_s=latency, control_gap_s=_number(row, "control_gap_s", 3.))
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
                spec = AbetConfig(path=str(sidecar or ""), mode="behavior_only" if sidecar else "disabled")
            row = {"recording": key, "xml": spec.path, "mode": spec.mode,
                   "latency_s": spec.latency_s, "control_gap_s": spec.control_gap_s}
            for index, (native, calcium) in enumerate(spec.anchors, 1):
                row[f"abet_{index}_s"] = native
                row[f"calcium_{index}_s"] = calcium
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
                       "abet_offset_s": result.info["offset_s"], "abet_scale": result.info["scale"],
                       "abet_span_difference_s": result.info["span_difference_s"],
                       "abet_responded": int((frame.outcome == "responded").sum()),
                       "abet_timeouts": int((frame.outcome == "timeout").sum()),
                       "abet_incomplete": int((frame.outcome == "incomplete").sum()),
                       "abet_regular_trials": int(frame.regular.sum())}
            for key in ("pulse_span_s", "calcium_span_s", "span_difference_s", "latency_s", "offset_s",
                        "scale", "first_frame_minus_mapped_start_s", "last_frame_minus_mapped_end_s"):
                summary["abet_" + key] = result.info[key]
            if mode != "behavior_only":
                locked = analyze_abet(processed, result, peak_direction=cfg.peth.peak_direction)
                export_abet_analysis(locked, staged / "locked", language=cfg.plot.language,
                                     make_figures=make_figures)
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
