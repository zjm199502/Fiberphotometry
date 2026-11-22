"""Regenerate plots from the extracted FiberLume tables using the bundled renderer."""
from pathlib import Path
import json
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from fiberlume_render import plotting
from fiberlume_render.analysis import EventResult, PethResult
from fiberlume_render.config import PipelineConfig
from fiberlume_render.figure_style import apply_figure_edits


def replot(folder):
    folder = Path(folder)
    out = folder / "redrawn"
    out.mkdir(exist_ok=True)
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    lang = meta["language"]
    plotting.setup_style(lang)

    def read(name):
        precision = {"float_precision": "round_trip"} if int(pd.__version__.split(".")[0]) < 3 else {}
        return pd.read_csv(folder / (name + ".csv"), keep_default_na=False, na_values=[""], **precision)

    def save(fig, name, view=None):
        spec = meta["views"].get(view or name, {})
        if spec:
            fig.set_size_inches(spec["size_inches"], forward=False)
            fig.set_dpi(spec["dpi"])
            apply_figure_edits(fig, spec["figure_edits"])
            fig.set_layout_engine("none")
            for ax, params in zip(fig.axes, meta["axes"][view or name]):
                ax.set_position(params["position"])
                ax.set_xlim(params["xlim"])
                ax.set_ylim(params["ylim"])
                if ax.images:
                    ax.images[0].set_clim(params["clim"])
        for ext in ("svg", "png"):
            fig.savefig(out / (name + "." + ext), dpi=120)
        plt.close(fig)

    if meta["page"] == "peth":
        frame = read("trials_native")
        time = frame.pop("t_rel_s").to_numpy()
        native = frame.to_numpy().T
        z = read("trials_z").drop(columns="t_rel_s").to_numpy().T
        metrics = read("trial_metrics")
        # The original plotting API calls its displayed, input-unit columns "_dff".
        for col in list(metrics):
            if col.endswith("_native"):
                metrics[col.removesuffix("_native") + "_dff"] = metrics[col]
        cfg = PipelineConfig.from_dict({"peth": meta["settings"]}).peth
        result = PethResult(meta["marker"], time, z, native, metrics.onset_s.to_numpy(), metrics,
                            meta["analysis_unit"], peak_direction=meta["peak_direction"], window_config=cfg)
        for kind, name in (("z", "peth_z"), ("dff", "peth_native")):
            axes = meta["axes"].get(name) or [{}]
            save(plotting.fig_peth(result, lang, axes[0].get("cmap", "RdBu_r"), cfg, kind), name)
            view = "comparisons" if kind == meta["active_comparison"] else "other_comparison"
            save(plotting.fig_peth_comparisons(result, lang, kind=kind), "comparisons_"+kind, view)
        if meta["analysis_unit"] != meta["dff_unit"] and meta["dff_scale"] is not None:
            result.trials_dff = read("trials_dff").drop(columns="t_rel_s").to_numpy().T
            result.dff_unit = meta["dff_unit"]
            save(plotting.fig_peth(result, lang, cfg_peth=cfg, kind="dff"), "peth_physical_dff")
    else:
        f, d, events = read("fluorescence"), read("deconvolution"), read("events")
        onsets = read("stimulus_overlay").time_s.to_numpy()
        for kind, unit in (("native",meta["analysis_unit"]), ("dff",meta["dff_unit"]), ("z","z")):
            b = SimpleNamespace(time=f.time_s.to_numpy(), dff=f["signal_"+kind].to_numpy(), unit=unit)
            c = SimpleNamespace(time=d.time_s.to_numpy(), denoised=d["denoised_"+kind].to_numpy(),
                                spikes=d["activity_"+kind].to_numpy())
            threshold = meta["threshold"][kind]
            ev = EventResult(events, meta["summary"], np.nan if threshold is None else threshold,
                             meta["summary"]["noise_sigma"], meta["method"])
            save(plotting.fig_deconv(c, b, ev, onsets, lang), "event_trace_"+kind,
                 "event_trace" if kind == "native" else "alternate")
            if kind == "native":
                save(plotting.fig_event_distributions(ev, lang, unit), "distributions")
        if (folder / "stimulus_comparison.csv").is_file():
            save(plotting.fig_event_rate(read("stimulus_comparison"), meta["rate_statistics"], lang), "rates")
    return out


if __name__ == "__main__":
    print(replot(Path(__file__).resolve().parent))
