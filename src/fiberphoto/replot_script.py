"""Source included in data exports so the plots can be redrawn without FiberLume."""
SCRIPT = '''"""Run from any directory after extracting the FiberLume ZIP."""
from pathlib import Path
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

root = Path(__file__).resolve().parent
out = root / "redrawn"
out.mkdir(exist_ok=True)
meta = json.loads((root / "metadata.json").read_text(encoding="utf-8"))

def table(name):
    return pd.read_csv(root / (name + ".csv"), float_precision="round_trip")

def finish(fig, axes, name, *, view=None):
    for ax, saved in zip(axes, meta["axes"].get(view or name, [])):
        ax.set(xlim=saved["xlim"], ylim=saved["ylim"], xlabel=saved["xlabel"], ylabel=saved["ylabel"])
        ax.set_title(saved["title"], loc="left")
    fig.savefig(out / (name + ".png"), dpi=150, bbox_inches="tight")
    plt.close(fig)

def paired(ax, first, second):
    a, b = np.array(first), np.array(second)
    valid = np.isfinite(a) & np.isfinite(b)
    a, b = a[valid], b[valid]
    j = np.random.default_rng(0).uniform(-.05, .05, len(a))
    for dx, x, y in zip(j, a, b):
        ax.plot([dx, 1+dx], [x, y], color=".7", lw=.8)
    ax.scatter(j, a, color="#eb6834")
    ax.scatter(1+j, b, color="#2a78d6")
    for i, group in enumerate((a, b)):
        if len(group):
            sem = np.std(group, ddof=1)/np.sqrt(len(group)) if len(group) > 1 else 0
            ax.errorbar(i+.18, group.mean(), yerr=sem, fmt="o", color="black", capsize=3)
    ax.set_xticks([0, 1], ["Baseline", "Target"])

if meta["page"] == "peth":
    settings = meta["settings"]
    means = table("time_summary")
    metrics = table("trial_metrics")
    stats = table("paired_statistics")
    for unit in ("native", "z", "dff"):
        frame = table("trials_"+unit)
        t = frame.pop("t_rel_s").to_numpy()
        trials = frame.to_numpy().T
        fig, (heat, mean) = plt.subplots(2, 1, figsize=(7, 7), layout="constrained")
        view = "peth_z" if unit == "z" else "peth_native"
        saved = meta["axes"].get(view, [{}])[0]
        if len(trials) and np.isfinite(trials).any():
            vmax = np.nanpercentile(np.abs(trials), 99) or 1.
            extent = [t[0]-(t[1]-t[0])/2, t[-1]+(t[1]-t[0])/2, len(trials)+.5, .5]
            im = heat.imshow(trials, extent=extent, interpolation="nearest", aspect="auto",
                             cmap=saved.get("cmap","RdBu_r"), vmin=-vmax, vmax=vmax)
            fig.colorbar(im, ax=heat)
        m, se = means["mean_"+unit].to_numpy(), means["sem_"+unit].to_numpy()
        mean.plot(t, m, color="#2a78d6")
        mean.fill_between(t, m-se, m+se, color="#2a78d6", alpha=.25)
        for key, color in (("baseline","#efeee9"), ("response","#e3edf9")):
            mean.axvspan(settings[key+"_start_s"], settings[key+"_end_s"], color=color, zorder=0)
        for ax in (heat, mean):
            ax.axvline(0, color=".2", lw=.8)
            ax.set_xlim(settings["trial_start_s"], settings["trial_end_s"])
        mean.axhline(0, color=".7", lw=.6)
        mean.set_xlabel("Time from stimulus (s)")
        mean.set_ylabel(meta["dff_unit"] if unit == "dff" else unit)
        native_view = unit != "dff" or meta["analysis_unit"] == meta["dff_unit"]
        finish(fig, [heat,mean], "peth_"+unit, view=view if native_view else "physical")
        if not len(metrics):
            continue
        fig, axes = plt.subplots(2, 1, figsize=(5,7), layout="constrained")
        for measure, ax in zip(("auc","peak"), axes):
            first = metrics[measure+"_baseline_"+unit]
            second = metrics[("peak_" if measure=="peak" else "auc_target_")+unit]
            paired(ax, first, second)
            report = stats[(stats.series==unit)&(stats.metric==measure)].iloc[0]
            ax.set_title(f"{measure}, n={report.n:g}, Wilcoxon p={report.wilcoxon_p:.4g}")
            ax.set_ylabel(unit + (" × s" if measure=="auc" else ""))
        finish(fig, [], "comparisons_"+unit)
else:
    f, d, ev = table("fluorescence"), table("deconvolution"), table("events")
    stim = table("stimulus_overlay")
    for unit in ("native","dff","z"):
        fig, (trace, activity) = plt.subplots(2,1,sharex=True,figsize=(12,5),
                                              gridspec_kw={"height_ratios":[3,1]},layout="constrained")
        trace.plot(f.time_s, f["signal_"+unit], color="#b5b4ae", lw=.5)
        trace.plot(d.time_s, d["denoised_"+unit], color="#1baf7a", lw=.8)
        trace.plot(ev.marker_time_s, ev["marker_y_"+unit], linestyle="none",
                   marker=np.array([[-.65,1],[0,0],[.65,1],[-.65,1]]), color="#e34948", ms=9)
        for t in stim.time_s:
            trace.axvline(t, color="#6250d6", lw=.7, alpha=.5)
        active = d["activity_"+unit].fillna(0) != 0
        activity.vlines(d.time_s[active], 0, d.loc[active,"activity_"+unit], colors=".3", lw=.6)
        threshold = meta["threshold"]
        if threshold[unit] is not None:
            (activity if threshold["axis"]=="activity" else trace).axhline(threshold[unit], ls="--", lw=.8)
        trace.set_ylabel(unit)
        activity.set(xlabel="Time (s)", ylabel="Inferred activity")
        finish(fig,[trace,activity],"event_trace_"+unit,view="event_trace" if unit=="native" else unit)
    bins = table("distributions")
    fig, axes = plt.subplots(1,2,figsize=(8,3),layout="constrained")
    for ax, name in zip(axes, ("amplitude","iei_s")):
        rows = bins[bins.variable==name]
        ax.bar(rows.left, rows["count"], width=rows.right-rows.left, align="edge",edgecolor="white")
    finish(fig, axes, "distributions")
    if (root / "stimulus_comparison.csv").is_file():
        frame = table("stimulus_comparison")
        fig, ax = plt.subplots(layout="constrained")
        paired(ax, frame.pre_rate_per_min, frame.post_rate_per_min)
        ax.set_xticks([0,1],["Pre","Post"])
        finish(fig,[ax],"rates")
print(out)
'''
