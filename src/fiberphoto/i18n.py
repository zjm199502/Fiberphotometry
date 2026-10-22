"""Chinese/English strings shared by figures, logs and (later) the GUI."""
from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    # figure titles / axes
    "time_s": {"zh": "时间 (s)", "en": "Time (s)"},
    "time_rel": {"zh": "相对刺激时间 (s)", "en": "Time from stimulus (s)"},
    "raw_title": {"zh": "原始信号 (data A)", "en": "Raw signals (data A)"},
    "signal": {"zh": "钙信号通道", "en": "Signal"},
    "control": {"zh": "等吸收点对照通道", "en": "Isosbestic control"},
    "fluor": {"zh": "荧光强度 (a.u.)", "en": "Fluorescence (a.u.)"},
    "dedrift_title": {"zh": "去漂移与 ΔF/F (data B)", "en": "De-drifting and ΔF/F (data B)"},
    "reference": {"zh": "拟合基线", "en": "Fitted baseline"},
    "dff": {"zh": "ΔF/F", "en": "ΔF/F"},
    "deconv_title": {"zh": "OASIS 反卷积与事件检测 (data C)", "en": "OASIS deconvolution and events (data C)"},
    "denoised": {"zh": "去噪信号", "en": "Denoised"},
    "spikes": {"zh": "推断活动", "en": "Inferred activity"},
    "events": {"zh": "检测到的事件", "en": "Detected events"},
    "threshold": {"zh": "阈值", "en": "Threshold"},
    "peth_title": {"zh": "刺激锁定平均反应", "en": "Peri-event average"},
    "heatmap_title": {"zh": "逐试次热图", "en": "Trial heatmap"},
    "trial": {"zh": "试次", "en": "Trial"},
    "mean_sem": {"zh": "均值 ± SEM", "en": "Mean ± SEM"},
    "auc_title": {"zh": "基线与目标区间 AUC", "en": "AUC: baseline vs target"},
    "baseline_win": {"zh": "基线", "en": "Baseline"},
    "target_win": {"zh": "目标区间", "en": "Target"},
    "pre": {"zh": "刺激前", "en": "Pre"},
    "post": {"zh": "刺激后", "en": "Post"},
    "auc": {"zh": "AUC", "en": "AUC"},
    "amp_hist": {"zh": "事件幅度分布", "en": "Event amplitude"},
    "iei_hist": {"zh": "事件间隔分布", "en": "Inter-event interval"},
    "amplitude": {"zh": "幅度", "en": "Amplitude"},
    "iei_s": {"zh": "间隔 (s)", "en": "Interval (s)"},
    "count": {"zh": "数量", "en": "Count"},
    "rate_title": {"zh": "刺激前后事件频率", "en": "Event rate before vs after stimulus"},
    "rate": {"zh": "事件频率 (次/分钟)", "en": "Event rate (/min)"},
    "stimulus": {"zh": "刺激", "en": "Stimulus"},
    "summary_title": {"zh": "分析总览", "en": "Analysis summary"},
    "zscore": {"zh": "z 分数", "en": "z-score"},
    # pipeline log messages
    "log_read": {"zh": "读取数据: {name}", "en": "Reading: {name}"},
    "log_channels": {"zh": "通道: 信号={signal}, 对照={control}, 打标={markers}",
                     "en": "Channels: signal={signal}, control={control}, markers={markers}"},
    "log_dedrift": {"zh": "去漂移 (方法: {method})", "en": "De-drifting (method: {method})"},
    "log_deconv": {"zh": "OASIS 反卷积 (衰减 τ = {tau:.2f} s)", "en": "OASIS deconvolution (decay τ = {tau:.2f} s)"},
    "log_peth": {"zh": "刺激锁定分析: {marker}, {n} 个试次", "en": "Peri-event analysis: {marker}, {n} trials"},
    "log_events": {"zh": "事件检测: {n} 个事件, {rate:.2f} 次/分钟", "en": "Event detection: {n} events, {rate:.2f}/min"},
    "log_no_marker": {"zh": "未找到行为打标，跳过刺激锁定分析", "en": "No behaviour markers found; peri-event analysis skipped"},
    "log_figs": {"zh": "导出图表到 {path}", "en": "Figures saved to {path}"},
    "log_done": {"zh": "完成: {name}", "en": "Done: {name}"},
    "log_error": {"zh": "出错: {name}: {err}", "en": "Failed: {name}: {err}"},
}


def tr(key: str, lang: str = "zh", **kw) -> str:
    entry = STRINGS.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry["en"]
    return s.format(**kw) if kw else s
