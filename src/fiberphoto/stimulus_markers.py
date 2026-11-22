"""Resolve stimulus times on the currently loaded recording's clock."""
from dataclasses import dataclass

import numpy as np

from . import analysis


@dataclass
class StimulusMarkers:
    label: str
    sources: tuple[str, ...]
    times: np.ndarray
    origins: dict[float, str]

    def analyze(self, processed, cfg):
        trials = analysis.peri_event(processed, self.times, cfg, self.label)
        trials.marker_sources = self.sources
        if cfg.include_manual_ttl:
            trials.metrics["marker_source"] = [self.origins.get(t, "file") for t in trials.onsets]
        return trials


def _nearest_frames(grid, times):
    right = np.clip(np.searchsorted(grid, times), 0, len(grid) - 1)
    left = np.maximum(right - 1, 0)
    return np.where(np.abs(times - grid[left]) <= np.abs(grid[right] - times), left, right)


def resolve_markers(raw, wanted, align="onset", shift_s=0.0, *, processed=None, include_manual=False):
    empty = StimulusMarkers("", (), np.array([], dtype=float), {})
    if include_manual and (not wanted or wanted == raw.manual_ttl_label):
        return empty  # This recording has no file series matching the requested merge.
    if wanted and wanted not in raw.markers and (include_manual or wanted.startswith("ABET:")):
        return empty
    label = wanted if wanted in raw.markers else next((k for k, v in raw.markers.items() if len(v)), "")
    if not label:
        return empty
    times = np.asarray(raw.markers[label], dtype=float)
    if align == "offset" and label in raw.marker_offsets:
        offsets = np.asarray(raw.marker_offsets[label], dtype=float)
        if offsets.size == times.size:
            times = np.where(np.isfinite(offsets), offsets, times)
    sources = (label,)
    origins = {}
    manual = raw.manual_ttl_label
    if include_manual and manual and manual != label:
        sources = (label, manual)
        clicks = np.asarray(raw.markers[manual], dtype=float)
        file_frames = _nearest_frames(raw.time, times)
        click_frames = _nearest_frames(raw.time, clicks)
        # Keep the recorded edge's precise timestamp when a click marks the same frame.
        coincident = np.isin(file_frames, click_frames)
        extra = clicks[~np.isin(click_frames, file_frames)]
        origins = {float(t + shift_s): "file+manual" if both else "file" for t, both in zip(times, coincident)}
        origins.update((float(t + shift_s), "manual") for t in extra)
        times = np.unique(np.concatenate((times, extra)))
    times = times + shift_s
    if processed is not None and processed.signal_start_s is not None:
        times = times[(times >= processed.signal_start_s) & (times <= processed.signal_end_s)]
    return StimulusMarkers(" + ".join(sources), sources, times, origins)


def configured_markers(raw, cfg, *, processed=None):
    return resolve_markers(raw, cfg.marker, cfg.align_to, cfg.shift_s, processed=processed,
                           include_manual=cfg.include_manual_ttl)
