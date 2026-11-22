"""Read ABET II sound-cue / tray-response schedules and map their native seconds."""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .abet_resampling import (DURATION_RESAMPLE, behavior_to_source, grid_info,
                              resample_calcium, source_to_behavior)


CUE_ALL = "ABET: cue all"
CUE_RESPONDED = "ABET: cue regular responded"
CUE_TIMEOUT = "ABET: cue regular timeout"
CUE_SPECIAL = "ABET: cue special"
TRAY_FEEDER = "ABET: tray + feeder"
TRAY_OUTSIDE = "ABET: tray outside window"
# ABET software may log hardware and program transitions on successive scheduler ticks.
SCHEDULER_TOLERANCE_S = 0.05


@dataclass
class AbetConfig:
    path: str = ""
    mode: str = DURATION_RESAMPLE
    control_gap_s: float = 3.0


@dataclass
class AbetSession:
    path: str
    animal_id: str
    schedule: str
    trials: pd.DataFrame
    sync_pulses: tuple[float, ...]
    duration_s: float
    warnings: list[str]
    tray_onsets_s: np.ndarray
    feeder_onsets_s: np.ndarray
    sha256: str
    session_id: str = ""
    started_at: str = ""


@dataclass
class AbetResult:
    session: AbetSession
    trials: pd.DataFrame
    markers: dict[str, np.ndarray]
    marker_offsets: dict[str, np.ndarray]
    info: dict
    control_onsets_s: np.ndarray


@dataclass
class _Entry:
    time: float
    kind: str
    effect: str
    args: dict
    auto: int | None


def _read_entries(root):
    entries = []
    for node in root.findall("./DataEntries/DataEntry"):
        time = float(node.findtext("Time", "nan"))
        if not np.isfinite(time) or time < 0 or (entries and time < entries[-1].time):
            raise ValueError("ABET events must have finite, nondecreasing session timestamps.")
        auto = node.findtext("Auto")
        entries.append(_Entry(
            time, node.findtext("EventText", "").strip(), node.findtext("EffectText", "").strip(),
            {a.findtext("Name", ""): a.findtext("Value", "") for a in node.findall("Args/Arg")},
            None if not auto else int(auto),
        ))
    return entries


def _trial(number, block):
    cue = block[0]

    by_type = {}
    for entry in block:
        by_type.setdefault((entry.kind, entry.effect), []).append(entry)

    def single(kind, effect):
        matches = by_type.get((kind, effect), [])
        return matches[0] if len(matches) == 1 else None

    lamp = single("Output On Event", "TrayLight #1")
    opening_events = [e for e in block if e.kind == "Group Change Event" and e.args.get("New Group") == "4"]
    opened = opening_events[0] if len(opening_events) == 1 else None
    note = single("Condition Event", "Note Tray Entry")
    timeout = single("Condition Event", "Start Delay")
    feeder = single("Pulse Output Event", "Feeder #1")
    close = single("Output Off Event", "TrayLight #1")
    counter = single("Variable Event", "_Trial_Counter")
    tray = None
    if opened and note:
        tray = next((e for e in reversed(block) if e.kind == "Input Transition On Event"
                     and e.effect == "Tray #1" and opened.time <= e.time <= note.time
                     and note.time - e.time <= SCHEDULER_TOLERANCE_S), None)
    duration = float(cue.args.get("Duration", "nan"))
    if not np.isfinite(duration) or duration <= 0:
        duration = np.nan
    window_valid = bool(lamp and close and opened and lamp.time <= opened.time <= close.time
                        and np.isfinite(duration))
    regular = bool(window_valid and abs(lamp.time - cue.time - duration) <= SCHEDULER_TOLERANCE_S
                   and abs(lamp.time - opened.time) <= SCHEDULER_TOLERANCE_S)
    outcome = "incomplete"
    if window_valid and note and tray and feeder and ("Condition Event", "Start Delay") not in by_type:
        if (opened.time <= note.time <= close.time + SCHEDULER_TOLERANCE_S
                and abs(note.time - feeder.time) <= SCHEDULER_TOLERANCE_S):
            outcome = "responded"
    elif (window_valid and timeout and ("Condition Event", "Note Tray Entry") not in by_type
          and ("Pulse Output Event", "Feeder #1") not in by_type
          and abs(timeout.time - close.time) <= SCHEDULER_TOLERANCE_S):
        outcome = "timeout"
    outcome_entry = note if note else timeout
    return {
        "trial": number, "cue_on_s": cue.time, "cue_pulse_duration_s": duration,
        "traylight_on_s": lamp.time if lamp else np.nan,
        "response_window_open_s": opened.time if opened else np.nan,
        "window_close_s": close.time if close else np.nan,
        "outcome": outcome, "regular": regular,
        "tray_input_on_s": tray.time if tray else np.nan,
        "note_tray_entry_s": note.time if note else np.nan,
        "feeder_command_s": feeder.time if feeder else np.nan,
        "tray_latency_from_window_open_s": tray.time - opened.time if tray else np.nan,
        "cue_xml_auto": cue.auto, "window_open_xml_auto": opened.auto if opened else None,
        "outcome_xml_auto": outcome_entry.auto if outcome_entry else None,
        "counter_xml_auto": counter.auto if counter else None,
        "feeder_xml_auto": feeder.auto if feeder else None,
        "tray_xml_auto": tray.auto if tray else None,
        "traylight_on_xml_auto": lamp.auto if lamp else None,
        "window_close_xml_auto": close.auto if close else None,
    }


def read_abet(path: str | Path) -> AbetSession:
    """Reconstruct trials from cue outputs, observed response windows and their outcomes."""
    path = Path(path).resolve()
    payload = path.read_bytes()
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid ABET XML: {exc}") from exc
    if root.tag != "AbetSession":
        raise ValueError("Expected an ABET II AbetSession XML file.")
    entries = _read_entries(root)
    cue_indices = [i for i, e in enumerate(entries) if (e.kind, e.effect) == ("Pulse Output Event", "Sound_On #1")]
    if not cue_indices or not any(e.effect == "TrayLight #1" for e in entries):
        raise ValueError("Unsupported ABET schedule: expected Sound_On #1 cue and TrayLight #1 events.")
    trials = pd.DataFrame([_trial(n, entries[i:j]) for n, (i, j) in enumerate(
        zip(cue_indices, [*cue_indices[1:], len(entries)]), 1)])
    for col in trials.filter(like="_xml_auto"):
        trials[col] = trials[col].astype("Int64")
    incomplete = int((trials.outcome == "incomplete").sum())
    warnings = [f"{incomplete} incomplete or ambiguous trials."] if incomplete else []
    pulses = tuple(e.time for e in entries if (e.kind, e.effect) == ("Pulse Output Event", "start #1"))
    if len(pulses) != 2:
        warnings.append(f"Found {len(pulses)} start #1 pulses; inspect the schedule boundaries.")
    metadata = {v.findtext("Name"): v.findtext("Value", "")
                for v in root.findall("SessionData/SessionVariables/SessionVariable")}
    return AbetSession(
        str(path), metadata.get("Animal ID", ""), root.findtext("SessionData/Name", "").strip(),
        trials, pulses, entries[-1].time, warnings,
        np.array([e.time for e in entries if (e.kind, e.effect) == ("Input Transition On Event", "Tray #1")]),
        np.array([e.time for e in entries if (e.kind, e.effect) == ("Pulse Output Event", "Feeder #1")]),
        hashlib.sha256(payload).hexdigest(), root.findtext("SessionData/SessionId", ""),
        metadata.get("Schedule_Start_Time") or root.findtext("SessionData/Date", ""),
    )


def _controls(session, gap):
    """Keep isolated tray rises outside cue/response epochs and away from feeder pulses."""
    times = session.tray_onsets_s
    if not len(times):
        return times.copy()
    isolated = (np.diff(times, prepend=-np.inf) > gap) & (np.diff(times, append=np.inf) > gap)
    # Exclude all time in ambiguous/unclosed trials up to the next cue or end of log.
    cues = session.trials.cue_on_s.to_numpy()
    ends = session.trials.window_close_s.to_numpy()
    ends = np.where(np.isfinite(ends), ends, np.r_[cues[1:], session.duration_s])
    # Windows are ordered and cannot extend past the next cue in the parser.
    previous = np.searchsorted(cues - gap, times, side="right") - 1
    outside = (previous < 0) | (times > ends[np.maximum(previous, 0)] + gap)
    feeders = session.feeder_onsets_s
    if len(feeders):
        idx = np.searchsorted(feeders, times)
        left = feeders[np.maximum(idx - 1, 0)]
        right = feeders[np.minimum(idx, len(feeders) - 1)]
        outside &= np.minimum(np.abs(times - left), np.abs(times - right)) > gap
    return times[isolated & outside]


_TIMESTAMP_COLUMNS = (
    "cue_on_s", "traylight_on_s", "response_window_open_s", "window_close_s",
    "tray_input_on_s", "note_tray_entry_s", "feeder_command_s",
)


def align_abet(session: AbetSession, cfg: AbetConfig, calcium_time) -> AbetResult:
    if cfg.mode != DURATION_RESAMPLE:
        raise ValueError("ABET 联合分析只支持按行为时长重采样 / Expected ABET duration resampling")
    if not np.isfinite(cfg.control_gap_s) or cfg.control_gap_s < 0:
        raise ValueError("Uncued tray control spacing must be finite and nonnegative.")
    grid = grid_info(session.sync_pulses, calcium_time)
    first, last = grid["aligned_first_s"], grid["aligned_last_s"]
    trials = session.trials.copy()
    controls = _controls(session, cfg.control_gap_s)
    completed = trials.outcome != "incomplete"
    responded = trials.outcome == "responded"
    groups = {
        CUE_ALL: trials.cue_on_s, CUE_RESPONDED: trials.loc[trials.regular & responded, "cue_on_s"],
        CUE_TIMEOUT: trials.loc[trials.regular & (trials.outcome == "timeout"), "cue_on_s"],
        CUE_SPECIAL: trials.loc[completed & ~trials.regular, "cue_on_s"],
        TRAY_FEEDER: trials.loc[responded, "tray_input_on_s"], TRAY_OUTSIDE: controls,
    }
    markers = {}
    marker_offsets = {}
    exclusions = {}
    # Downstream calcium data now use the native ABET clock.
    for col in _TIMESTAMP_COLUMNS:
        trials[col[:-2] + "_calcium_s"] = trials[col]
    for label, native in groups.items():
        onsets = np.asarray(native, dtype=float)
        keep = (onsets >= first) & (onsets <= last)
        exclusions[label] = int((~keep).sum())
        if keep.any():
            markers[label] = onsets[keep]
            if label in (CUE_ALL, CUE_RESPONDED, CUE_TIMEOUT, CUE_SPECIAL):
                ends = onsets + trials.loc[native.index, "cue_pulse_duration_s"].to_numpy()
                marker_offsets[label] = np.where(ends <= last, ends, np.nan)[keep]
    info = {
        "source": session.path, "source_sha256": session.sha256, "animal_id": session.animal_id,
        "session_id": session.session_id, "started_at": session.started_at,
        "schedule": session.schedule, "mode": DURATION_RESAMPLE, **grid,
        "sync_pulses_s": list(session.sync_pulses), "duration_s": session.duration_s,
        "control_gap_s": cfg.control_gap_s, "scheduler_tolerance_s": SCHEDULER_TOLERANCE_S,
        "trial_rules": {
            "cue": "One trial per Pulse Output Event / Sound_On #1; observed XML sequence order.",
            "window": "Opens on Group Change Event to group 4, closes on TrayLight #1 off.",
            "responded": "Within-window Tray #1 rising input followed by Note Tray Entry and a Feeder #1 command, within scheduler tolerance.",
            "timeout": "Start Delay at tray light off without a Note Tray Entry or feeder command.",
            "regular": "Tray light and window both start at cue onset plus programmed pulse duration, within scheduler tolerance.",
            "incomplete": "Missing or contradictory outcome evidence. Retained in the trial ledger.",
        },
        "control_rule": "Tray rises more than control_gap_s from other rises, cues through window close, and feeder commands.",
        "control_onsets_abet_s": controls.tolist(),
        "marker_counts_native": {label: len(values) for label, values in groups.items()},
        "marker_counts_outside_calcium": exclusions, "warnings": session.warnings.copy(),
    }
    return AbetResult(session, trials, markers, marker_offsets, info, controls)


def export_abet(result: AbetResult, out_dir: str | Path) -> None:
    """Write full trials (native and mapped timestamps) and the clock/filter provenance."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    result.trials.to_csv(folder / "abet_trials.csv", index=False, encoding="utf-8-sig", float_format="%.12g")
    (folder / "abet_alignment.json").write_text(
        json.dumps(result.info, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
