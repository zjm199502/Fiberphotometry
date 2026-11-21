"""Record the methods actually used for one exported session."""
import json
import platform
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
import sys

import numpy as np

from .analysis import paired_test
from .io_tdms import find_event_file
from .preprocess import decay_time_constant, format_dff_formula
from .recordings import recording_family


SCIPY = "https://docs.scipy.org/doc/scipy/reference/generated/"
NUMPY = "https://numpy.org/doc/stable/reference/generated/"
REFERENCES = {
    "Plexon Multi-Wavelength Photometry User Guide": "https://plexon.com/wp-content/uploads/2021/Multi-Wavelength-Photometry-User-Guide.pdf",
    "npTDMS · Reading TDMS files": "https://nptdms.readthedocs.io/en/stable/reading.html",
    "Doric Lenses · readDoric": "https://github.com/doriclenses/readDoric",
    "Neurophotometrics · Bonsai Guide": "https://neurophotometrics.com/s/BonsaiGuide_20221014.pdf",
    "NumPy · interp": NUMPY + "numpy.interp.html",
    "NumPy · median": NUMPY + "numpy.median.html",
    "pandas · Series.rolling": "https://pandas.pydata.org/docs/reference/api/pandas.Series.rolling.html",
    "SciPy · Welch PSD": SCIPY + "scipy.signal.welch.html",
    "NumPy · polyfit": NUMPY + "numpy.polyfit.html",
    "SciPy · curve_fit": SCIPY + "scipy.optimize.curve_fit.html",
    "SciPy · butter": SCIPY + "scipy.signal.butter.html",
    "SciPy · sosfiltfilt": SCIPY + "scipy.signal.sosfiltfilt.html",
    "SciPy · find_peaks": SCIPY + "scipy.signal.find_peaks.html",
    "SciPy · peak_widths": SCIPY + "scipy.signal.peak_widths.html",
    "SciPy · trapezoid": SCIPY + "scipy.integrate.trapezoid.html",
    "SciPy · wilcoxon": SCIPY + "scipy.stats.wilcoxon.html",
    "SciPy · ttest_rel": SCIPY + "scipy.stats.ttest_rel.html",
    "Keevers & Jean-Richard-dit-Bressel (2025) · 光纤荧光对照拟合与 ΔF/F": "https://pubmed.ncbi.nlm.nih.gov/40166421/",
    "Tukey biweight · 权重函数": "https://www.statsmodels.org/stable/generated/statsmodels.robust.norms.TukeyBiweight.weights.html",
    "Friedrich, Zhou & Paninski (2017) · OASIS": "https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1005423",
    "OASIS · 作者实现": "https://github.com/j-friedrich/OASIS",
    "Sherathiya et al. (2021) · GuPPy 逐试次基线扣除": "https://doi.org/10.1038/s41598-021-03626-9",
}


def _stage(key, title, implementation, parameters, rules, refs=()):
    return dict(id=key, title=title, implementation=implementation, parameters=parameters,
                calculations=rules, references={r: REFERENCES[r] for r in refs})


def _reader(raw, cfg):
    path = Path(raw.source)
    family = recording_family(path)
    channels = raw.channel_names
    refs = []
    if family == ".tdms":
        refs.append("npTDMS · Reading TDMS files")
        clock = ("时间直接取指定时间通道。" if channels.get("time") else
                 "tᵢ = wf_start_offset + i / fs；缺省起始偏移为零。")
    else:
        clock = "时间戳换算为秒，fs = 1 / median(diff(t))。"
        if family == "npm":
            clock += " 分离 ROI 和光源状态，LedState/Flags 的低三位按 1=415、2=470、4=560 nm 拆帧。"
            refs.append("Neurophotometrics · Bonsai Guide")
        if family == "plexon":
            refs.append("Plexon Multi-Wavelength Photometry User Guide")
        if family == ".doric":
            refs.append("Doric Lenses · readDoric")
        if raw.control is not None:
            clock += " 信号与对照取共同覆盖范围，对照线性插值到信号时间点。"
            refs.append("NumPy · interp")
    marker_file = cfg.channels.marker_file or (find_event_file(path) if family == ".tdms" else None)
    rules = [clock]
    if raw.markers:
        rules.append("标记来源按读取配置解析。记录中的采样 TTL 以 x > 阈值的上升沿/下降沿定时，自动阈值为 (min + max)/2；时间戳表直接读取事件时间。")
    params = {"format": family, "channels": channels, "indicator": raw.indicator,
              "indicator_source": raw.indicator_source, "samples": len(raw.time), "sampling_hz": raw.fs,
              "time_range_s": [raw.time[0], raw.time[-1]], "marker_counts": {k: len(v) for k, v in raw.markers.items()},
              "marker_file": str(marker_file) if marker_file else None,
              "excluded_channels": sorted(cfg.channels.exclusions_for(path))}
    return _stage("read", "读取与通道", "recordings.read_recording / io_tdms.read_tdms", params, rules, refs)


def _behavior(raw, b):
    info = raw.behavior_info
    clock = ("t = 文件时间列 × 秒/原时间单位" if info.get("time_column") else "tᵢ = i / 行为采样率")
    clock += ("；T = t − t₀ + 钙记录首帧时间 + offset_s。" if info.get("align_starts")
              else "；T = t + offset_s。")
    rule = {"linear": "v(t) = v₀ + (v₁−v₀)(t−t₀)/(t₁−t₀)。",
            "previous": "选取时间不晚于目标帧的最后一个行为样本。",
            "nearest": "选取与目标帧绝对时间差最小的样本，等距优先较早样本。"}[info["method"]]
    params = dict(info, processed_valid_frames=int(np.isfinite(b.behavior).sum()))
    return _stage("behavior", "行为序列对齐", "behavior.align_behavior / RawData.behavior_on", params,
                  [clock, rule, "按原始行为样本对齐到目标时间轴，重叠区间外及所需样本缺失时保留空值。"],
                  ["NumPy · interp"] if info["method"] == "linear" else [])


def _preprocess(raw, b, cfg, lang):
    formula = format_dff_formula(raw, b, lang)
    params = {"requested_method": cfg.method, "actual_method": b.method, "unit": b.unit, "formula_info": b.formula_info,
              "requested_signal_interval_s": [cfg.signal_start_s, cfg.signal_end_s],
              "output_samples": len(b.time), "output_range_s": [b.time[0], b.time[-1]], "output_hz": b.fs}
    refs, rules = [], [formula]
    if b.method.startswith("Plexon "):
        rules.append("按选定信号区间逐帧读取设备比值，保留采集时间戳。")
        refs.append("Plexon Multi-Wavelength Photometry User Guide")
    else:
        if cfg.signal_start_s is not None:
            rules.append("按采集时间选闭区间 [signal_start_s, signal_end_s]，优先于首尾裁剪。")
        else:
            rules.append("仅保留首尾裁剪范围内样本；随后才进行滤波及降采样。")
            if cfg.auto_trim and b.method != "signal_interval":
                rules.append("至少 10 秒数据时检查首秒中位数是否低于全段正中位数的 0.6 倍；若满足，找到首次超过两者中点的样本并再留 1 s，与人工起始裁剪取较大值。")
        if cfg.lowpass_hz and cfg.lowpass_hz < raw.fs / 2:
            params["lowpass_hz"] = cfg.lowpass_hz
            rules.append("四阶 Butterworth 低通，二阶节正反向滤波。")
            refs += ["SciPy · butter", "SciPy · sosfiltfilt"]
        if b.fs != raw.fs:
            params["bin_samples"] = round(raw.fs / b.fs)
            rules.append("相邻 k 帧（k = round(fs / 目标频率)）的时间和幅值分别取均值；丢弃末尾未满 k 帧的块。")
        if b.method == "isosbestic":
            rules.append("对照和常数项最小二乘初始化；s = median(|r−median(r)|)/0.6745，u = r/(4.685s)，"
                         "在 |u|<1 取 w=(1−u²)²，否则 w=0。对 sqrt(w) 加权后的设计和响应重复最小二乘，至收敛或 50 轮。")
            refs += ["Keevers & Jean-Richard-dit-Bressel (2025) · 光纤荧光对照拟合与 ΔF/F", "Tukey biweight · 权重函数"]
        elif b.method == "highpass":
            rules.append("基线由处理后信号减去二阶 Butterworth 正反向高通信号得到。")
            refs += ["SciPy · butter", "SciPy · sosfiltfilt"]
        elif b.method == "biexponential":
            refs.append("SciPy · curve_fit")
            if b.formula_info.get("fit") == "polynomial_fallback":
                refs.append("NumPy · polyfit")
        elif b.method == "signal_interval":
            refs.append("NumPy · median")
        elif b.method == "polynomial":
            refs.append("NumPy · polyfit")
        if cfg.method != b.method:
            rules.append(f"所选方法 {cfg.method} 无可用独立对照，已使用 {b.method}。")
    if b.formula_info.get("center_window_s", 0) > 0:
        rules.append("按 data B 的采集时刻取居中时间窗，包含两端，边缘缩短到有效信号区间，逐帧减去原 ΔF/F 的窗口中位数。"
                     "data B.dff_center_baseline 保存减去的局部基线，单位与设备比值或重算的百分比一致。")
        if b.dff_unfiltered is not None:
            rules.append("未低通的 ΔF/F 减去同一条局部基线，再按所选输出单位转换。")
        refs.append("pandas · Series.rolling")
    return _stage("preprocess", "预处理与 ΔF/F", "preprocess.dedrift", params, rules, refs)


def _deconv(res, cfg):
    b, c = res.B, res.C
    params = dict(asdict(cfg), fitted_g=c.g, fitted_baseline=c.baseline, fitted_lambda=c.lam,
                  noise_sigma=c.sn, decay_tau_s=decay_time_constant(c))
    rules = ["输入采用未经过低通的 ΔF/F 分支。" if b.dff_unfiltered is not None else "输入采用 data B 的 ΔF/F。"]
    if cfg.baseline_window_s > 0:
        rules.append("在移动窗口内取指定百分位，按粗网格线性插值并减去其中位数，得到慢漂移 D；OASIS 的输入为 y−D。")
    rules += ["自回归模型 cₜ = Σₖ gₖ cₜ₋ₖ + sₜ，sₜ ≥ 0。选定罚项和模型阶数传入 OASIS，输出为去噪分量加拟合基线及已移除的慢漂移。",
              "GetSn 对输入估计 Welch 功率谱，以默认高频段 (0.25, 0.5) 周期/样本计算 σ = √mean(PSD/2)。推断活动截断到 ≥ 0。"]
    if cfg.smin > 0:
        rules.append(f"将低于 {cfg.smin:g} 的推断活动置零。")
    if len(c.g) == 1 and 0 < c.g[0] < 1:
        rules.append("指数衰减常数 τ = −1 / (fs × ln g₁)，单位为秒。")
    return _stage("deconvolution", "OASIS 反卷积", "preprocess.deconvolve / oasis.functions.deconvolve",
                  params, rules, ["Friedrich, Zhou & Paninski (2017) · OASIS", "OASIS · 作者实现", "SciPy · Welch PSD"])


def _events(res, cfg):
    ev = res.events
    params = dict(method=ev.method, actual_threshold=ev.threshold, noise_sigma=ev.noise_sigma, summary=ev.summary)
    refs = []
    if ev.method == "oasis":
        basis = {"noise": "σ", "max": "max(s)"}.get(cfg.spike_threshold_mode, "1")
        params.update(threshold_multiplier=cfg.spike_threshold, threshold_mode=cfg.spike_threshold_mode,
                      group_floor=min(ev.threshold, .5 * ev.noise_sigma), max_gap_s=cfg.min_distance_s)
        rules = [f"检测阈值 H = max(10⁻¹², {cfg.spike_threshold:g} × {basis})。σ 优先取 OASIS 噪声估计，其次取去噪信号一阶差分的 MAD/0.6745，再回退至 1。",
                 "s ≥ min(H,0.5σ) 的帧按相邻间隔不大于 max_gap_s 分组。仅保留组内最大 s ≥ H 的组；time_s 为首次达到 H 的时间。",
                 "peak_time_s 为去噪信号最高点的时间；搜索从组首到末帧后的首次下降之前，并在下一候选组开始前截止。",
                 "幅度 = 组首至组末去噪信号的最大值 − 组首前一帧的值；记录起点取首帧去噪值减首帧推断活动。spike_sum 是该段活动之和，max_spike 是候选帧最大活动。"]
    else:
        params.update(min_distance_samples=max(1, round(cfg.min_distance_s * res.C.fs)),
                      min_prominence=cfg.min_prominence_mad*ev.noise_sigma)
        rules = ["σ = median(|r−median(r)|)/0.6745，r = data B − 去噪信号；零值时改用 data B 的尺度，再回退至 1。",
                 f"以去噪信号中位数 M 为基线，峰高阈值 = M + {cfg.threshold_mad:g}σ；同时施加最小突出度和最小峰间距。",
                 "time_s 和 peak_time_s 均取检测峰时间；幅度 = 峰值 − M。半宽为相对于突出度的半高宽（样本数/fs）。"]
        refs = ["SciPy · find_peaks", "SciPy · peak_widths"]
    rules.append("IEI = 相邻 time_s 之差；每分钟事件数 = 60 × 事件数/(末帧时间−首帧时间)。均值幅度和 IEI 中位数按事件汇总。")
    return _stage("events", "钙事件与记录内汇总", "analysis.detect_events / analysis._summary", params, rules, refs)


def _peth(res, cfg):
    p = res.peth
    params = dict(asdict(cfg), actual_marker=p.marker, input_markers=res.summary.get("n_markers"),
                  accepted_trials=len(p.onsets), dt_s=1/res.B.fs,
                  dff_centering="subtract_entire_pre_stimulus_mean", pre_samples=int((p.t_rel < 0).sum()),
                  skipped_nonfinite_pre_trials=p.skipped_incomplete_pre)
    anchor = ("标记终点（缺失时回退到该脉冲起点）" if cfg.align_to == "offset" else "标记起点")
    rules = [f"刺激零点 = {anchor} + ({cfg.shift_s:g} s)。显式信号区间仅保留区间内的对齐时间。",
             "按时间排序后，距上一个保留标记不足 min_interval_s 的标记丢弃；取 data B 中第一帧 t ≥ 标记时间为零点。",
             "前后分别取 round(pre_s × fs)、round(post_s × fs) 帧。刺激前至少需一帧，越界或刺激前含非有限值的试次丢弃。",
             "第 i 试次的 μᵢ = mean(本试次 data B[t < 0])，归零曲线 yᵢ(t) = data Bᵢ(t) − μᵢ，保留 data B 的单位。"
             "μᵢ 写入 peth_metrics.csv 的 prestimulus_mean_input_dff 列。"]
    if len(p.onsets):
        rules += ["基线窗为 [baseline_start_s, baseline_end_s)，响应窗为 [response_start_s, response_end_s]。"
                  "逐试次 Z = (ΔF/F − 基线均值)/基线样本标准差(ddof=1)，空基线均值取 0，无有效标准差时分母取 1。",
                  "分别对刺激前归零曲线和 Z 分数计算窗内均值、响应窗最大值及 AUC = Σ(yᵢ+yᵢ₊₁) Δt/2。AUC 单位为对应纵轴单位 × s。潜伏期为响应窗内首次最大 Z 值的相对时间。",
                  "逐时点跨试次均值与 SEM = 样本标准差/√有效试次数。"]
    return _stage("peth", "刺激锁定试次", "pipeline.pick_marker / analysis.peri_event", params, rules,
                  ["Sherathiya et al. (2021) · GuPPy 逐试次基线扣除", "SciPy · trapezoid"] if len(p.onsets) else [])


def _statistics(res, cfg):
    comparisons = {}
    if res.peth is not None and len(res.peth.metrics):
        m = res.peth.metrics
        for unit in ("dff", "z"):
            comparisons["auc_" + unit] = paired_test(m["auc_baseline_" + unit], m["auc_target_" + unit])
    if res.stim_events is not None and len(res.stim_events):
        frame = res.stim_events
        comparisons["rate_per_min"] = paired_test(frame["pre_rate_per_min"], frame["post_rate_per_min"])
    rules = []
    if res.stim_events is not None:
        rules.append("以事件检测 time_s 在 [t−W,t) 和 [t,t+W) 分别计数，速率 = 60 × 计数/W。越界的刺激窗不参与比较。")
    refs = []
    if comparisons:
        rules.append("按试次配对并剔除任一值缺失的整对。至少 3 对时运行双侧配对 t 检验，若存在非零配对差则同时运行双侧 Wilcoxon 符号秩检验。")
        if any(v["n"] >= 3 for v in comparisons.values()):
            refs.append("SciPy · ttest_rel")
        if any(np.isfinite(v["wilcoxon_p"]) for v in comparisons.values()):
            refs.append("SciPy · wilcoxon")
    if rules:
        return _stage("statistics", "配对比较", "analysis.events_around_stimuli / analysis.paired_test",
                      {"comparisons": comparisons, "event_window_s": cfg.events.compare_window_s if res.stim_events is not None else None},
                      rules, refs)


def _json_safe(x):
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_json_safe(v) for v in x]
    if isinstance(x, np.generic):
        return _json_safe(x.item())
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


def write_method_report(res, cfg):
    from . import __version__

    a, b = res.A, res.B
    cfg = cfg.for_recording(a.source)
    stages = [_reader(a, cfg)]
    if a.behavior is not None:
        stages.append(_behavior(a, b))
    stages.append(_preprocess(a, b, cfg.dedrift, cfg.plot.language))
    if res.C is not None:
        stages.append(_deconv(res, cfg.deconv))
    if res.events is not None:
        stages.append(_events(res, cfg.events))
    if res.peth is not None:
        stages.append(_peth(res, cfg.peth))
    stats = _statistics(res, cfg)
    if stats:
        stages.append(stats)

    versions = {"FiberLume": __version__, "Python": platform.python_version()}
    for package, module in (("numpy", "numpy"), ("scipy", "scipy"), ("pandas", "pandas"), ("oasis-deconv", "oasis")):
        if package == "oasis-deconv" and res.C is None:
            continue
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = getattr(sys.modules.get(module), "__version__", None)
    configured = cfg.to_dict()
    # Never include associations or exclusion lists from other recordings in an export.
    key = str(Path(a.source).resolve())
    for mapping in [configured["channels"]["excluded_channels"], configured["behavior_by_recording"],
                    configured["intervals_by_recording"]]:
        for other in list(mapping):
            if other != key:
                del mapping[other]
    report = _json_safe(dict(schema_version=1, recording=key, software=versions, stages=stages,
                             configured_parameters=configured))
    def dump(value, indent=None):
        return json.dumps(value, indent=indent, ensure_ascii=False, allow_nan=False)
    (res.out_dir / "analysis_methods.json").write_text(dump(report, 2) + "\n", encoding="utf-8")
    lines = [f"# {res.name} 分析方法", "", f"原始文件　{key}", "", "软件版本　" + dump(versions), ""]
    for stage in report["stages"]:
        lines.extend(["## " + stage["title"], "", "实现　`" + stage["implementation"] + "`", ""])
        lines.extend("- " + rule for rule in stage["calculations"])
        lines.extend(["", "本记录参数与结果", "", "```json", dump(stage["parameters"], 2), "```", ""])
        if stage["references"]:
            lines.extend(["方法或函数来源", ""])
            lines.extend(f"- [{name}]({url})" for name, url in stage["references"].items())
            lines.append("")
    lines.extend(["## 运行配置", "", "以下为本记录的配置快照，实际执行的分支与拟合值见上文。", "",
                  "```json", dump(configured, 2), "```", ""])
    (res.out_dir / "analysis_methods.md").write_text("\n".join(lines), encoding="utf-8")
