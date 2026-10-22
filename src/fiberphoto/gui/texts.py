"""Interface text (Chinese/English) and the parameter form specifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

UI: dict[str, dict[str, str]] = {
    "app_title": {"zh": "光纤记录钙信号分析", "en": "Fiber Photometry Analysis"},
    # menus
    "menu_file": {"zh": "文件", "en": "File"},
    "menu_lang": {"zh": "语言 / Language", "en": "Language / 语言"},
    "menu_help": {"zh": "帮助", "en": "Help"},
    "act_add_files": {"zh": "添加 TDMS 文件…", "en": "Add TDMS files…"},
    "act_add_folder": {"zh": "添加文件夹…", "en": "Add folder…"},
    "act_sample": {"zh": "加载示例数据", "en": "Load sample data"},
    "act_load_params": {"zh": "加载参数…", "en": "Load parameters…"},
    "act_save_params": {"zh": "保存参数…", "en": "Save parameters…"},
    "act_reset_params": {"zh": "恢复默认参数", "en": "Reset parameters"},
    "act_quit": {"zh": "退出", "en": "Quit"},
    "act_manual": {"zh": "用户手册 (PDF)", "en": "User manual (PDF)"},
    "manual_missing": {"zh": "没有找到用户手册文件。", "en": "The user manual file was not found."},
    "act_guide": {"zh": "快速入门", "en": "Quick start"},
    "act_about": {"zh": "关于", "en": "About"},
    # file panel
    "files": {"zh": "数据文件", "en": "Recordings"},
    "btn_add": {"zh": "添加文件", "en": "Add files"},
    "btn_folder": {"zh": "添加文件夹", "en": "Add folder"},
    "btn_sample": {"zh": "示例数据", "en": "Sample data"},
    "btn_remove": {"zh": "移除", "en": "Remove"},
    "files_empty": {"zh": "还没有文件。点击“添加文件”或“示例数据”开始。",
                    "en": "No recordings yet. Click “Add files” or “Sample data” to start."},
    # tabs
    "tab_read": {"zh": "① 数据读取", "en": "① Load data"},
    "tab_pre": {"zh": "② 预处理", "en": "② Preprocess"},
    "tab_peth": {"zh": "③ 刺激锁定分析", "en": "③ Peri-event"},
    "tab_events": {"zh": "④ 事件检测", "en": "④ Events"},
    "tab_export": {"zh": "⑤ 导出与批处理", "en": "⑤ Export && batch"},
    # tab help
    "help_read": {"zh": "选择左侧的数据文件，确认各通道的含义（软件已自动猜测），然后点击“读取数据”。读取结果即原始数据 data A。",
                  "en": "Pick a recording on the left, check what each channel is (guessed automatically), then click “Load data”. The result is the raw data A."},
    "help_pre": {"zh": "去除光漂白造成的缓慢漂移，得到 ΔF/F（data B）；再用 OASIS 反卷积得到去噪信号（data C），用于事件检测。",
                 "en": "Remove slow photobleaching drift to get ΔF/F (data B), then run OASIS deconvolution to get a denoised trace (data C) for event detection."},
    "help_peth": {"zh": "以刺激为时间零点截取每个试次的 ΔF/F（data B），同时给出 ΔF/F 和基线 z 分数两种结果，并计算基线区间与目标区间的峰值和 AUC。区间以秒为单位，可自行设置。",
                  "en": "Cut ΔF/F (data B) around each stimulus and report it both as ΔF/F and as a baseline z-score, with peak and AUC for the baseline and target windows. Both windows are set in seconds."},
    "help_events": {"zh": "按 OASIS 流程把反卷积推断出的 spike 作为钙事件（默认阈值 0.8 × 噪声 σ），统计频率、幅度和间隔，并比较刺激前后的事件频率。",
                    "en": "Following OASIS, inferred spikes count as calcium events (default threshold 0.8 × noise σ). Measures rate, amplitude and intervals, and compares event rate before and after stimuli."},
    "help_export": {"zh": "把当前文件的全部结果（数据备份、表格、图表）保存到输出文件夹，或用当前参数一次处理列表中的所有文件。",
                    "en": "Save every result for the current recording (data backups, tables, figures), or process all listed recordings with the current parameters."},
    # tab 1
    "channels": {"zh": "通道", "en": "Channels"},
    "col_group": {"zh": "组", "en": "Group"},
    "col_channel": {"zh": "通道", "en": "Channel"},
    "col_samples": {"zh": "采样点", "en": "Samples"},
    "col_fs": {"zh": "采样率 (Hz)", "en": "Rate (Hz)"},
    "col_guess": {"zh": "自动识别", "en": "Guessed role"},
    "col_preview": {"zh": "预览", "en": "Preview"},
    "flat": {"zh": "恒定/空（无数据）", "en": "flat / empty"},
    "fs_missing": {"zh": "⚠ 这个文件没有记录采样率，请在“采样率”中手动填写（例如 50 Hz）。",
                   "en": "⚠ This file does not store its sampling rate. Enter it under “Sampling rate” (e.g. 50 Hz)."},
    "need_fs": {"zh": "请先填写采样率。", "en": "Enter the sampling rate first."},
    "role_signal": {"zh": "信号", "en": "signal"},
    "role_control": {"zh": "对照", "en": "control"},
    "role_marker": {"zh": "打标", "en": "marker"},
    "role_time": {"zh": "时间", "en": "time"},
    "mapping": {"zh": "通道设置", "en": "Channel mapping"},
    "lbl_group": {"zh": "数据组", "en": "Group"},
    "lbl_signal": {"zh": "钙信号通道 (470/465 nm)", "en": "Signal channel (470/465 nm)"},
    "lbl_control": {"zh": "对照通道 (405/410 nm)", "en": "Control channel (405/410 nm)"},
    "lbl_markers": {"zh": "行为打标通道", "en": "Marker channels"},
    "lbl_fs": {"zh": "采样率 (Hz)", "en": "Sampling rate (Hz)"},
    "lbl_marker_file": {"zh": "外部打标文件 (可选)", "en": "Marker file (optional)"},
    "marker_file_tip": {"zh": "打标单独保存时在这里选择：CSV/Excel 表格，或打标 TDMS 文件（如 “xxx-Event.tdms”）。\n留空时，软件会自动使用同一文件夹里同名的 “-Event.tdms” 文件。",
                        "en": "Use this when markers are stored separately: a CSV/Excel table or a marker TDMS (e.g. “xxx-Event.tdms”).\nWhen empty, a matching “-Event.tdms” file in the same folder is used automatically."},
    "auto_paired": {"zh": "自动配对: {name}", "en": "Auto-paired: {name}"},
    "no_pair": {"zh": "未找到配对的打标文件", "en": "No paired marker file found"},
    "none": {"zh": "（无）", "en": "(none)"},
    "auto": {"zh": "自动", "en": "auto"},
    "from_file": {"zh": "从文件读取", "en": "from file"},
    "browse": {"zh": "浏览…", "en": "Browse…"},
    "clear": {"zh": "清除", "en": "Clear"},
    "btn_read": {"zh": "读取数据", "en": "Load data"},
    "markers_found": {"zh": "打标: {txt}", "en": "Markers: {txt}"},
    # tab 2
    "grp_dedrift": {"zh": "去漂移 → data B", "en": "De-drifting → data B"},
    "grp_deconv": {"zh": "OASIS 反卷积 → data C", "en": "OASIS deconvolution → data C"},
    "btn_pre": {"zh": "运行预处理", "en": "Run preprocessing"},
    # tab 3
    "grp_peth": {"zh": "刺激锁定参数", "en": "Peri-event settings"},
    "btn_peth": {"zh": "运行刺激锁定分析", "en": "Run peri-event analysis"},
    "lbl_marker_use": {"zh": "对齐的打标", "en": "Align to marker"},
    "trial_table": {"zh": "逐试次结果", "en": "Per-trial results"},
    "col_trial": {"zh": "试次", "en": "Trial"},
    "col_onset": {"zh": "刺激时间 (s)", "en": "Onset (s)"},
    "col_latency": {"zh": "潜伏期 (s)", "en": "Latency (s)"},
    "col_peak_z": {"zh": "峰值 z", "en": "Peak z"},
    "col_aucb_z": {"zh": "基线 AUC z", "en": "Baseline AUC z"},
    "col_auct_z": {"zh": "目标 AUC z", "en": "Target AUC z"},
    "col_peak_dff": {"zh": "峰值 ΔF/F", "en": "Peak ΔF/F"},
    "col_aucb_dff": {"zh": "基线 AUC ΔF/F", "en": "Baseline AUC ΔF/F"},
    "col_auct_dff": {"zh": "目标 AUC ΔF/F", "en": "Target AUC ΔF/F"},
    "view_z": {"zh": "z 分数", "en": "z-score"},
    "view_dff": {"zh": "ΔF/F", "en": "ΔF/F"},
    # tab 4
    "grp_events": {"zh": "事件检测参数", "en": "Event detection settings"},
    "btn_events": {"zh": "运行事件检测", "en": "Run event detection"},
    "event_summary": {"zh": "事件数 {n}，频率 {rate:.2f} 次/分钟，平均幅度 {amp:.2f}，噪声 σ {sigma:.3f}",
                      "en": "{n} events, {rate:.2f}/min, mean amplitude {amp:.2f}, noise σ {sigma:.3f}"},
    # tab 5
    "grp_out": {"zh": "输出设置", "en": "Output"},
    "lbl_outdir": {"zh": "输出文件夹", "en": "Output folder"},
    "lbl_formats": {"zh": "图片格式", "en": "Figure formats"},
    "lbl_dpi": {"zh": "分辨率 (dpi)", "en": "Resolution (dpi)"},
    "lbl_fig_lang": {"zh": "图表语言", "en": "Figure language"},
    "lbl_csv": {"zh": "同时保存 CSV 表格", "en": "Also save CSV tables"},
    "btn_export_one": {"zh": "导出当前文件", "en": "Export current recording"},
    "btn_batch": {"zh": "批处理全部文件", "en": "Process all recordings"},
    "btn_open_out": {"zh": "打开输出文件夹", "en": "Open output folder"},
    "btn_cancel": {"zh": "停止", "en": "Stop"},
    # status / dialogs
    "log": {"zh": "运行日志", "en": "Log"},
    "fig_empty": {"zh": "运行这一步后，图表会显示在这里。", "en": "The figure appears here after you run this step."},
    "ready": {"zh": "就绪", "en": "Ready"},
    "busy": {"zh": "正在处理…", "en": "Working…"},
    "need_file": {"zh": "请先在左侧选择一个数据文件。", "en": "Select a recording on the left first."},
    "need_read": {"zh": "请先完成“① 数据读取”。", "en": "Run “① Load data” first."},
    "need_pre": {"zh": "请先完成“② 预处理”。", "en": "Run “② Preprocess” first."},
    "need_deconv": {"zh": "请在“② 预处理”中启用反卷积。", "en": "Enable deconvolution in “② Preprocess”."},
    "no_markers": {"zh": "这个文件没有找到行为打标。请在“① 数据读取”中选择打标通道或导入打标表格。",
                   "en": "No behaviour markers in this recording. Choose marker channels or a marker table in “① Load data”."},
    "error": {"zh": "出错了", "en": "Error"},
    "batch_done": {"zh": "批处理完成：{ok} 个成功，{bad} 个失败。结果在 {path}",
                   "en": "Batch finished: {ok} succeeded, {bad} failed. Results in {path}"},
    "export_done": {"zh": "已导出到 {path}", "en": "Exported to {path}"},
    "sample_written": {"zh": "示例数据已生成在 {path}", "en": "Sample data written to {path}"},
    "params_loaded": {"zh": "已加载参数 {path}", "en": "Parameters loaded from {path}"},
    "params_saved": {"zh": "参数已保存到 {path}", "en": "Parameters saved to {path}"},
    "choose_folder": {"zh": "选择文件夹", "en": "Choose folder"},
    "tdms_filter": {"zh": "TDMS 文件 (*.tdms)", "en": "TDMS files (*.tdms)"},
    "json_filter": {"zh": "参数文件 (*.json)", "en": "Parameter files (*.json)"},
    "table_filter": {"zh": "打标文件 (*.tdms *.csv *.xlsx *.xls)", "en": "Marker files (*.tdms *.csv *.xlsx *.xls)"},
    "about_text": {"zh": "fiberphoto {ver}\n钙成像光纤记录分析软件，面向神经生物学研究生培训。\n反卷积算法: OASIS (Friedrich et al., 2017)。",
                   "en": "fiberphoto {ver}\nFiber photometry calcium analysis for neuroscience training.\nDeconvolution: OASIS (Friedrich et al., 2017)."},
    "guide_text": {
        "zh": "1. 点击左侧“示例数据”，或添加自己的 TDMS 文件。\n"
              "2. ① 数据读取：确认信号、对照和打标通道，点击“读取数据”。\n"
              "3. ② 预处理：一般保持默认，点击“运行预处理”，检查拟合基线是否贴合信号。\n"
              "4. ③ 刺激锁定分析：查看热图和平均反应曲线。\n"
              "5. ④ 事件检测：调整阈值，直到红色三角标记与肉眼可见的事件一致。\n"
              "6. ⑤ 导出：保存当前文件的结果，或用同一组参数批处理所有文件。\n\n"
              "鼠标停在任何参数上可以看到说明。参数可以通过“文件 → 保存参数”保存，方便全组统一。",
        "en": "1. Click “Sample data” on the left, or add your own TDMS files.\n"
              "2. ① Load data: check the signal, control and marker channels, then click “Load data”.\n"
              "3. ② Preprocess: defaults usually work. Check that the fitted baseline follows the signal.\n"
              "4. ③ Peri-event: inspect the heatmap and the average response.\n"
              "5. ④ Events: tune the threshold until the red triangles match the visible transients.\n"
              "6. ⑤ Export: save results for this recording, or batch-process every recording with the same settings.\n\n"
              "Hover over any parameter for an explanation. Save parameters via File → Save parameters to share them with the lab."},
}


def ui(key: str, lang: str, **kw) -> str:
    entry = UI.get(key)
    if entry is None:
        return key
    s = entry.get(lang) or entry["en"]
    return s.format(**kw) if kw else s


@dataclass
class ParamSpec:
    section: str                      # attribute of PipelineConfig
    name: str                         # attribute of the section dataclass
    kind: str                         # float | int | bool | choice | optfloat
    label: dict
    tip: dict
    choices: list = field(default_factory=list)   # [(value, {"zh":…, "en":…}), …]
    min: float = 0.0
    max: float = 1e6
    step: float = 1.0
    decimals: int = 2
    suffix: str = ""
    off_label: Optional[dict] = None  # optfloat: text of the "disabled" state
    depends: Optional[tuple] = None   # (other field, allowed values): enabled only then


def L(zh: str, en: str) -> dict:
    return {"zh": zh, "en": en}


DEDRIFT = [
    ParamSpec("dedrift", "method", "choice", L("去漂移方法", "Method"),
              L("isosbestic：用 405 nm 对照通道拟合并扣除漂移和运动伪迹（推荐，需要对照通道）。\n"
                "双指数：用双指数曲线拟合光漂白，适合没有对照通道的数据。\n"
                "多项式 / 高通：更简单的去趋势方法。",
                "Isosbestic: fit the 405 nm control to remove drift and motion artefacts (recommended; needs a control channel).\n"
                "Biexponential: fit photobleaching with a double exponential, for recordings without a control.\n"
                "Polynomial / high-pass: simpler detrending."),
              choices=[("isosbestic", L("等吸收点对照拟合 (isosbestic)", "Isosbestic control fit")),
                       ("biexponential", L("双指数拟合", "Biexponential")),
                       ("polynomial", L("多项式拟合", "Polynomial")),
                       ("highpass", L("高通滤波", "High-pass filter"))]),
    ParamSpec("dedrift", "lowpass_hz", "optfloat", L("低通滤波", "Low-pass filter"),
              L("去除高频噪声。钙信号变化较慢，10 Hz 通常足够。", "Removes high-frequency noise. Calcium signals are slow; 10 Hz is usually enough."),
              min=0.1, max=500, step=1, decimals=1, suffix=" Hz", off_label=L("不滤波", "off")),
    ParamSpec("dedrift", "downsample_hz", "optfloat", L("降采样到", "Downsample to"),
              L("减小数据量，加快后续计算。应低于原始采样率，并至少是低通频率的 2 倍。",
                "Reduces data size for faster processing. Must be below the raw rate and at least twice the low-pass cutoff."),
              min=1, max=1000, step=5, decimals=1, suffix=" Hz", off_label=L("保持原始", "keep raw")),
    ParamSpec("dedrift", "poly_order", "int", L("多项式阶数", "Polynomial order"),
              L("仅用于“多项式拟合”方法。", "Only used by the polynomial method."), min=1, max=10,
              depends=("method", ("polynomial",))),
    ParamSpec("dedrift", "highpass_hz", "float", L("高通截止频率", "High-pass cutoff"),
              L("仅用于“高通滤波”方法。低于这个频率的缓慢变化被视为漂移。",
                "Only used by the high-pass method. Slower changes are treated as drift."),
              min=0.0001, max=1, step=0.005, decimals=4, suffix=" Hz", depends=("method", ("highpass",))),
    ParamSpec("dedrift", "auto_trim", "bool", L("自动去除光源未开启段", "Auto-trim LED-off start"),
              L("有些设备在光源打开前就开始记录，开头一段信号明显偏低。勾选后软件会自动检测并去掉这一段。",
                "Some rigs start recording before the light source is on, so the first seconds are far too low. "
                "When ticked, that stretch is detected and removed automatically.")),
    ParamSpec("dedrift", "trim_start_s", "float", L("去掉开头", "Trim start"),
              L("去掉记录开头 LED 刚打开时快速衰减的部分。", "Drop the fast LED warm-up decay at the start of the recording."),
              max=3600, step=5, decimals=1, suffix=" s"),
    ParamSpec("dedrift", "trim_end_s", "float", L("去掉结尾", "Trim end"),
              L("去掉记录结尾的部分。", "Drop this much from the end of the recording."),
              max=3600, step=5, decimals=1, suffix=" s"),
    ParamSpec("dedrift", "output", "choice", L("输出单位", "Output unit"),
              L("ΔF/F (%)：相对于基线的百分比变化。z 分数：除以整段信号的标准差。",
                "ΔF/F (%): percent change from baseline. z-score: divided by the whole-trace standard deviation."),
              choices=[("dff", L("ΔF/F (%)", "ΔF/F (%)")), ("zscore", L("z 分数", "z-score"))]),
]

DECONV = [
    ParamSpec("deconv", "enabled", "bool", L("启用反卷积", "Enable deconvolution"),
              L("事件检测需要反卷积结果 (data C)。", "Event detection needs the deconvolved data C.")),
    ParamSpec("deconv", "model", "choice", L("钙动力学模型", "Calcium model"),
              L("AR(1)：只建模衰减，稳定，推荐。AR(2)：同时建模上升时间，适合上升较慢的指示剂。",
                "AR(1): decay only, robust, recommended. AR(2): also models the rise time, for slower indicators."),
              choices=[("ar1", L("AR(1) 仅衰减", "AR(1) decay only")), ("ar2", L("AR(2) 上升 + 衰减", "AR(2) rise + decay"))]),
    ParamSpec("deconv", "penalty", "choice", L("稀疏惩罚", "Sparsity penalty"),
              L("L1：标准设置。L0：推断的活动更稀疏、更像离散事件。", "L1: standard. L0: sparser, more spike-like inferred activity."),
              choices=[(1, L("L1（推荐）", "L1 (recommended)")), (0, L("L0（更稀疏）", "L0 (sparser)"))]),
    ParamSpec("deconv", "baseline_window_s", "float", L("基线校正窗口", "Baseline window"),
              L("反卷积前用滑动百分位数去掉残余的缓慢漂移。0 表示不校正。",
                "A running percentile removes residual slow drift before deconvolution. 0 turns it off."),
              max=3600, step=10, decimals=0, suffix=" s"),
    ParamSpec("deconv", "baseline_percentile", "float", L("基线百分位数", "Baseline percentile"),
              L("滑动窗口内取这个百分位数作为基线。事件越频繁，取值应越低。",
                "Percentile used as the baseline inside the window. Use lower values for very active recordings."),
              min=1, max=50, step=5, decimals=0, suffix=" %"),
    ParamSpec("deconv", "smin", "float", L("最小活动幅度", "Minimum activity size"),
              L("小于这个值的推断活动被置零。0 表示保留全部。", "Inferred activity below this size is set to zero. 0 keeps everything."),
              max=1000, step=0.5, decimals=2),
    ParamSpec("deconv", "optimize_g", "int", L("优化衰减常数", "Refine decay constant"),
              L("用多少个大的孤立事件进一步优化衰减时间常数。0 表示只估计一次。",
                "Number of large isolated events used to refine the decay constant. 0 estimates it once."),
              max=50),
]

PETH = [
    ParamSpec("peth", "align_to", "choice", L("刺激时刻", "Stimulus time"),
              L("TTL 开始：刺激与打标脉冲同时出现。\nTTL 结束：刺激在打标脉冲结束后才出现（例如电击控制软件先发 1 s TTL，结束后才给电击）。",
                "TTL start: the stimulus coincides with the marker pulse.\nTTL end: the stimulus follows the pulse "
                "(e.g. the shocker sends a 1 s TTL and delivers the shock when it ends)."),
              choices=[("onset", L("TTL 开始（上升沿）", "TTL start (rising edge)")),
                       ("offset", L("TTL 结束（下降沿）", "TTL end (falling edge)"))]),
    ParamSpec("peth", "shift_s", "float", L("额外偏移", "Extra shift"),
              L("在上面选定的时刻上再加一个固定时间（可为负），用于已知的设备延迟。一般为 0。",
                "A fixed time added to the chosen moment (may be negative), for a known hardware delay. Usually 0."),
              min=-60, max=60, step=0.1, decimals=2, suffix=" s"),
    ParamSpec("peth", "pre_s", "float", L("刺激前时长", "Time before"),
              L("每个试次截取刺激前多长时间。", "How much time before each stimulus to include."), max=600, step=1, decimals=1, suffix=" s"),
    ParamSpec("peth", "post_s", "float", L("刺激后时长", "Time after"),
              L("每个试次截取刺激后多长时间。", "How much time after each stimulus to include."), max=600, step=1, decimals=1, suffix=" s"),
    ParamSpec("peth", "baseline_start_s", "float", L("基线开始", "Baseline start"),
              L("基线区间的起点，单位秒，相对刺激时刻（负数表示刺激前）。z 分数以每个试次的基线均值和标准差计算。",
                "Start of the baseline window in seconds relative to the stimulus (negative = before). "
                "z-scores use each trial's baseline mean and SD."),
              min=-600, max=600, step=0.5, decimals=1, suffix=" s"),
    ParamSpec("peth", "baseline_end_s", "float", L("基线结束", "Baseline end"),
              L("基线区间的终点（秒），通常为 0。", "End of the baseline window (s), usually 0."),
              min=-600, max=600, step=0.5, decimals=1, suffix=" s"),
    ParamSpec("peth", "response_start_s", "float", L("目标区间开始", "Target window start"),
              L("目标区间的起点（秒，相对刺激时刻）。在这个区间里计算峰值、潜伏期和 AUC。",
                "Start of the target window (s from the stimulus), where peak, latency and AUC are measured."),
              min=-600, max=600, step=0.5, decimals=1, suffix=" s"),
    ParamSpec("peth", "response_end_s", "float", L("目标区间结束", "Target window end"),
              L("目标区间的终点（秒）。要比较基线和目标区间的 AUC，两个区间最好等长。",
                "End of the target window (s). Keep it as long as the baseline window when comparing their AUCs."),
              min=-600, max=600, step=0.5, decimals=1, suffix=" s"),
    ParamSpec("peth", "min_interval_s", "float", L("合并间隔", "Merge interval"),
              L("间隔小于这个值的打标视为同一次刺激（例如连续舔水），只保留第一个。",
                "Markers closer than this are treated as one (e.g. lick bouts); only the first is kept."),
              max=600, step=0.5, decimals=1, suffix=" s"),
]

EVENTS = [
    ParamSpec("events", "method", "choice", L("检测方法", "Method"),
              L("OASIS spike（推荐）：完全按 OASIS 流程，把反卷积推断出的 spike 作为事件。\n"
                "去噪信号峰值：在 OASIS 去噪信号上找峰。",
                "OASIS spikes (recommended): follow OASIS and treat inferred spikes as events.\n"
                "Denoised peaks: find peaks in the OASIS-denoised trace."),
              choices=[("oasis", L("OASIS spike（推荐）", "OASIS spikes (recommended)")),
                       ("peak", L("去噪信号峰值", "Denoised-trace peaks"))]),
    ParamSpec("events", "spike_threshold", "float", L("spike 阈值", "Spike threshold"),
              L("推断的 spike 至少要达到这个大小才算事件。默认 0.8，含义取决于下面的“阈值单位”。数值越大，事件越少、越可靠。",
                "Minimum inferred spike size that counts as an event. Default 0.8; its meaning depends on the threshold unit below. "
                "Higher values give fewer, more reliable events."),
              min=0, max=1000, step=0.1, decimals=2, depends=("method", ("oasis",))),
    ParamSpec("events", "spike_threshold_mode", "choice", L("阈值单位", "Threshold unit"),
              L("噪声倍数：阈值 × 该记录的噪声 σ（不同记录之间可比，推荐）。\n相对最大值：阈值 × 最大的 spike（0–1）。\n绝对值：直接以 ΔF/F 单位计。",
                "Noise multiple: threshold × this recording's noise σ (comparable across recordings; recommended).\n"
                "Relative to max: threshold × the largest spike (0–1).\nAbsolute: in ΔF/F units."),
              choices=[("noise", L("× 噪声 σ", "× noise σ")), ("max", L("× 最大 spike", "× largest spike")),
                       ("absolute", L("ΔF/F 绝对值", "absolute ΔF/F"))], depends=("method", ("oasis",))),
    ParamSpec("events", "threshold_mad", "float", L("峰值阈值", "Peak threshold"),
              L("事件峰值需要高于基线多少倍噪声标准差。数值越大，检测到的事件越少、越可靠。",
                "How many noise SDs above baseline a peak must reach. Higher values give fewer, more reliable events."),
              min=0.5, max=20, step=0.5, decimals=1, suffix=" σ", depends=("method", ("peak",))),
    ParamSpec("events", "min_prominence_mad", "float", L("最小突出度", "Minimum prominence"),
              L("峰值相对两侧低谷至少高出多少倍噪声标准差，用来区分叠加在一起的事件。",
                "How far a peak must rise above its neighbouring troughs, in noise SDs. Separates overlapping events."),
              min=0, max=20, step=0.5, decimals=1, suffix=" σ", depends=("method", ("peak",))),
    ParamSpec("events", "min_distance_s", "float", L("最小事件间隔", "Minimum spacing"),
              L("两个事件之间至少间隔多久。OASIS 方法中，间隔更短的 spike 合并为同一个事件。",
                "Minimum time between two events. With OASIS spikes, closer spikes are merged into one event."),
              max=60, step=0.1, decimals=2, suffix=" s"),
    ParamSpec("events", "compare_window_s", "float", L("刺激前后比较窗口", "Pre/post window"),
              L("比较刺激前后事件频率时使用的窗口长度。", "Window length used to compare event rates before and after each stimulus."),
              min=1, max=600, step=1, decimals=1, suffix=" s"),
]
