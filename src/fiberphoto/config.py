"""Pipeline parameters.

Every tunable number in the pipeline lives here, so the GUI can build its
forms from these dataclasses and save/load them as JSON.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional


@dataclass
class ChannelConfig:
    """Which TDMS channels hold which signal. Empty strings mean auto-detect."""
    group: str = ""                 # TDMS group; "" = first group that has data
    signal: str = ""                # calcium-dependent channel (e.g. 470/465 nm)
    control: str = ""               # isosbestic channel (e.g. 405/410 nm); optional
    time: str = ""                  # explicit time channel; "" = use waveform props
    markers: list[str] = field(default_factory=list)  # digital/TTL behaviour channels
    sampling_rate: Optional[float] = None  # Hz; None = read from file
    marker_threshold: Optional[float] = None  # TTL threshold; None = midpoint of range
    marker_file: str = ""           # optional CSV/Excel or marker TDMS; "" = auto-pair <name>-Event.tdms


@dataclass
class DedriftConfig:
    """De-drifting and ΔF/F (produces data B)."""
    method: str = "isosbestic"      # "isosbestic" | "biexponential" | "polynomial" | "highpass"
    lowpass_hz: Optional[float] = 10.0   # smoothing before fit; None disables
    downsample_hz: Optional[float] = 20.0  # target rate after smoothing; None keeps raw rate
    poly_order: int = 3
    highpass_hz: float = 0.01
    auto_trim: bool = True          # detect and drop an initial stretch with the LED still off
    trim_start_s: float = 0.0       # drop LED warm-up at the start
    trim_end_s: float = 0.0
    output: str = "dff"             # "dff" (%) or "zscore"


@dataclass
class DeconvConfig:
    """OASIS deconvolution (produces data C)."""
    enabled: bool = True
    model: str = "ar1"              # "ar1" | "ar2"
    penalty: int = 1                # 0 = L0 (sparser), 1 = L1
    smin: float = 0.0               # drop inferred spikes smaller than this; 0 = keep all
    optimize_g: int = 0             # passes to refine the AR coefficient (0 = estimate once)
    baseline_window_s: float = 60.0  # running-percentile baseline removed first; 0 = off
    baseline_percentile: float = 20.0


@dataclass
class PethConfig:
    """Stimulus-locked ΔF/F on data B."""
    marker: str = ""                # which marker label to align to; "" = first available
    align_to: str = "onset"         # "onset" | "offset" of the TTL pulse
    shift_s: float = 0.0            # added to every stimulus time (e.g. known hardware delay)
    pre_s: float = 5.0
    post_s: float = 10.0
    baseline_start_s: float = -5.0
    baseline_end_s: float = 0.0
    response_start_s: float = 0.0   # target window for AUC / peak metrics (s from stimulus)
    response_end_s: float = 5.0
    min_interval_s: float = 0.0     # merge markers closer than this


@dataclass
class EventConfig:
    """Transient detection on data C."""
    method: str = "oasis"           # "oasis": events = OASIS spikes | "peak": peaks of the denoised trace
    spike_threshold: float = 0.8    # oasis: minimum spike size, see spike_threshold_mode
    spike_threshold_mode: str = "noise"  # "noise" (× noise σ) | "max" (× largest spike) | "absolute" (ΔF/F units)
    threshold_mad: float = 3.0      # peak height above median, in robust SDs
    min_distance_s: float = 0.5     # peak: minimum peak spacing; oasis: spikes closer than this merge
    min_prominence_mad: float = 2.0
    compare_window_s: float = 10.0  # pre/post stimulus window for rate comparison


@dataclass
class PlotConfig:
    language: str = "zh"            # "zh" | "en"
    dpi: int = 300
    formats: list[str] = field(default_factory=lambda: ["png", "pdf"])
    colormap: str = "RdBu_r"         # diverging, centred on 0 for z-scored trials


@dataclass
class PipelineConfig:
    channels: ChannelConfig = field(default_factory=ChannelConfig)
    dedrift: DedriftConfig = field(default_factory=DedriftConfig)
    deconv: DeconvConfig = field(default_factory=DeconvConfig)
    peth: PethConfig = field(default_factory=PethConfig)
    events: EventConfig = field(default_factory=EventConfig)
    plot: PlotConfig = field(default_factory=PlotConfig)
    save_csv: bool = True           # CSV copies of data A/B/C next to the HDF5 file

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PipelineConfig":
        kwargs = {}
        for f in fields(cls):
            if f.name not in d:
                continue
            sub = f.default_factory() if callable(f.default_factory) else None  # type: ignore[misc]
            if sub is not None and hasattr(sub, "__dataclass_fields__"):
                known = {k: v for k, v in d[f.name].items() if k in sub.__dataclass_fields__}
                kwargs[f.name] = type(sub)(**known)
            else:
                kwargs[f.name] = d[f.name]
        return cls(**kwargs)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
