"""Synthetic fiber photometry recordings for training and testing.

Each file mimics a typical two-wavelength recording: a 470 nm GCaMP channel
and a 405 nm isosbestic channel that share photobleaching and motion
artefacts, plus TTL channels for stimulus and behaviour. The true event
times are written next to the data so students can check what the
pipeline recovers.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter


def _kernel(fs: float, rise: float, decay: float, length_s: float) -> np.ndarray:
    t = np.arange(int(length_s * fs)) / fs
    k = (1 - np.exp(-t / rise)) * np.exp(-t / decay)
    return k / k.max()


def simulate(duration_s: float = 600.0, fs: float = 100.0, n_stim: int = 20,
             stim_amp: float = 0.10, spont_rate_per_min: float = 6.0,
             seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    n = int(duration_s * fs)
    t = np.arange(n) / fs

    # stimulus onsets: first after 30 s, ~ every 25 s with jitter
    gap = (duration_s - 60) / n_stim
    stim = 30 + np.arange(n_stim) * gap + rng.uniform(-3, 3, n_stim)
    stim_dur = 1.0

    # spontaneous transients (Poisson) + evoked responses
    n_sp = rng.poisson(spont_rate_per_min * duration_s / 60)
    spont = np.sort(rng.uniform(5, duration_s - 5, n_sp))
    drive = np.zeros(n)
    sp_amp = rng.lognormal(np.log(0.05), 0.35, n_sp)
    np.add.at(drive, (spont * fs).astype(int), sp_amp)
    calcium = np.convolve(drive, _kernel(fs, 0.08, 0.7, 6), mode="full")[:n]
    evoked = np.zeros(n)
    st_amp = stim_amp * rng.lognormal(0, 0.25, n_stim)
    lat = rng.uniform(0.2, 0.6, n_stim)
    np.add.at(evoked, ((stim + lat) * fs).astype(int), st_amp)
    calcium += np.convolve(evoked, _kernel(fs, 0.25, 2.0, 15), mode="full")[:n]

    # photobleaching (biexponential) differs a little between wavelengths
    bleach_470 = 1.0 + 0.25 * np.exp(-t / 60) + 0.15 * np.exp(-t / 600)
    bleach_405 = 1.0 + 0.27 * np.exp(-t / 55) + 0.14 * np.exp(-t / 650)
    # motion artefacts: a few brief dips shared by both channels
    motion = np.zeros(n)
    for tm in rng.uniform(20, duration_s - 20, 6):
        motion -= 0.06 * np.exp(-0.5 * ((t - tm) / 0.4) ** 2)
    motion += 0.004 * np.sin(2 * np.pi * 0.3 * t + rng.uniform(0, 6))  # breathing-like

    f0_470, f0_405 = 1.6, 0.9
    # slow background activity (low-passed noise), present only in the 470 channel
    slow = np.convolve(rng.normal(0, 1, n), np.ones(int(2 * fs)) / int(2 * fs), mode="same")
    slow *= 0.004 / (slow.std() or 1)
    sig = f0_470 * bleach_470 * (1 + calcium + slow) * (1 + motion) + rng.normal(0, 0.015 * f0_470, n)
    ctl = f0_405 * bleach_405 * (1 + motion) + rng.normal(0, 0.012 * f0_405, n)

    ttl_stim = np.zeros(n)
    for s in stim:
        ttl_stim[int(s * fs): int((s + stim_dur) * fs)] = 5.0
    licks = np.sort(rng.uniform(10, duration_s - 10, 40))
    ttl_lick = np.zeros(n)
    for s in licks:
        ttl_lick[int(s * fs): int((s + 0.05) * fs)] = 5.0

    return {"t": t, "fs": fs, "signal": sig, "control": ctl, "ttl_stim": ttl_stim,
            "ttl_lick": ttl_lick, "stim": stim, "licks": licks, "spont": spont,
            "spont_amp": sp_amp, "stim_amp": st_amp}


def write_tdms(path: str | Path, sim: dict, animal: str = "") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    props = {"wf_increment": 1.0 / sim["fs"], "wf_start_offset": 0.0, "unit_string": "V"}
    root = RootObject(properties={"name": path.stem, "animal": animal,
                                  "description": "Synthetic fiber photometry recording (fiberphoto)"})
    group = GroupObject("Fiber1")
    chans = [
        ChannelObject("Fiber1", "470nm", sim["signal"], properties=props),
        ChannelObject("Fiber1", "405nm", sim["control"], properties=props),
        ChannelObject("Fiber1", "DI1_Stim", sim["ttl_stim"], properties=props),
        ChannelObject("Fiber1", "DI2_Lick", sim["ttl_lick"], properties=props),
    ]
    with TdmsWriter(path) as w:
        w.write_segment([root, group] + chans)
    return path


def make_sample_dataset(folder: str | Path, n_animals: int = 3, duration_s: float = 600.0) -> list[Path]:
    """Write a small training dataset: one TDMS per animal plus ground truth."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    amps = [0.12, 0.08, 0.025]  # the last animal barely responds, a useful contrast
    for i in range(n_animals):
        sim = simulate(duration_s=duration_s, stim_amp=amps[i % len(amps)], seed=100 + i)
        name = f"mouse{i + 1:02d}_session1"
        paths.append(write_tdms(folder / f"{name}.tdms", sim, animal=f"mouse{i + 1:02d}"))
        truth = {"stimulus_onsets_s": sim["stim"].round(3).tolist(),
                 "lick_onsets_s": sim["licks"].round(3).tolist(),
                 "spontaneous_event_times_s": sim["spont"].round(3).tolist(),
                 "evoked_amplitude_dff": sim["stim_amp"].round(4).tolist()}
        (folder / f"{name}_ground_truth.json").write_text(json.dumps(truth, indent=1))
        if i == 0:
            # the same markers as a table, to show the external-marker-file route
            pd.DataFrame({"onset_s": sim["stim"].round(3), "offset_s": (sim["stim"] + 1).round(3),
                          "label": "tone"}).to_csv(folder / f"{name}_markers.csv", index=False)
    return paths


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_data"
    for p in make_sample_dataset(out):
        print(p)
