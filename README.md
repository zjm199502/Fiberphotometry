# FiberLume · 光纤荧光分析

<img src="src/fiberphoto/gui/assets/fiberlume.png" alt="FiberLume" width="96" height="96">

### ΔF/F 来源和荧光指示器

Plexon 的 `Frame,time,RawF_1,F0_1,R_1` 以及按波长和光纤编号的导出均可直接读取。默认使用与所选 RawF 同组、同波长的 R 列作为 ΔF/F，保留逐帧原始比值和时间戳。0.03 表示 3%。F0 作为设备基线保留。进入“预处理”，取消“优先使用 Plexon 的 R 列”，重新运行即可从 RawF 拟合基线。未提供匹配 R 列时自动走原始荧光流程。导出摘要记录实际使用的方法和单位。

读取时可在“荧光指示器”中指定探针。留空时优先采用文件的指示器标注，无标注时按 GCaMP 荧光强度解释。摘要分别标记用户指定、文件标注、通道名和默认推定。

### 通道排除与行为数据对齐

将钙记录与同名 ABET II Touch 声音—食槽任务 XML 放在一起，读取时会把钙信号重采样到两个 `start #1` 上升沿之间的均匀时间轴。行为事件保留原始秒数，可直接选作刺激锁定标记，也可比较随后进槽与超时试次的峰值和 AUC。具体操作见[使用手册](docs/manual/用户手册.md)。

在“数据读取”页选中通道表中的一行或多行，可将这些通道从当前记录的分析中排除，也可恢复。Plexon 的比值通道与对应原始荧光配对；若只想改用原始荧光，可单独排除对应的 R 列。

每份钙信号记录可关联一个行为 CSV，从中选择一列数值（例如速度或 0/1 活动状态）。支持 UTF-8 逗号分隔文件。使用时间列时指定秒、毫秒或微秒；只有数值列时填写行为采样率（Hz）。纯数值且无表头的列按 `column_1` 等名称显示。

例如，`time_ms,speed` 两列记录在 0、100 和 200 ms 时的速度为 0、2 和 4。选择毫秒和线性插值后，钙信号在 0.05 s 的对应速度为 1。对于开关状态，可选择保持前值。两台设备使用同一时钟时保留原时间戳；需要将首帧对齐时选起点对齐，再按同步事件填写偏移量。正偏移使行为轨迹在钙信号时间轴上向后移动。此版本支持固定偏移，独立时钟的漂移需要另行校正。

读取后，行为轨迹与钙信号在共享时间轴上预览。超出行为记录覆盖范围的钙帧保留，行为值为空。数据 A、B、C 的 HDF5 和启用的逐帧 CSV 均以 `behavior` 保存对应时间点的行为值；裁剪、降采样后按处理后的时间重新对齐。参数文件保存每份记录的关联，导出摘要记录对齐设置和有效覆盖帧数。

### 分别框选信号采集段和可选基线段

读取记录后，在原始荧光图上框选实际采集信号的时间段。也可以从预处理页进入选区界面，或用起止秒数精确调整。明确选定信号段后，分析使用该区间，保留记录本身的时间轴，便于和行为数据对照。

如果实验记录了同一通道、相同光学条件下的静息荧光，可另外框选基线段。两个区间允许分开。软件取基线段原始荧光样本的中位数为固定 F₀，再在信号段上计算百分比 ΔF/F = 100 × (F − F₀) / F₀。预处理确认窗口显示区间、样本数和 F₀，导出摘要保留这些设置。固定 F₀ 用于相对量化，随时间变化的运动或漂白仍需独立评估。

没有专门基线段时，只框选信号采集段即可。可按记录条件沿用设备导出的 R、经接线确认的等吸收点对照，或从选定信号段拟合基线。开灯前的暗信号可留作背景检查；仅凭阶跃形状无法认定它是静息荧光。

### 手动 TTL 与逐事件测量

在“数据读取”页读取波形后，选择“添加 TTL 事件点”，在预览图内单击时间位置。软件将各点对齐到最近的原始采样帧。按 Esc 结束添加，可以逐个撤销。手动标记按文件保存到参数中，在刺激锁定页选择 `Manual TTL` 即可据此对齐试次。

如需把补标与已有刺激一起分析，在“③ 刺激锁定分析 → 对齐的打标”中选择“文件打标名 + 手动 TTL”。例如原刺激通道叫 Untitled，就选“Untitled + 手动 TTL”。同一采样帧上的文件打标和手动点算一次，保留文件边沿时刻。导出的 `peth_metrics.csv` 在 `marker_source` 中逐试次注明文件、手动或两者重合。

撤销最后一个手动点并重新导出到同一目录时，本次不再生成的旧手动标记结果会归档到 `previous-manual-ttl-*` 子目录。软件通过上次方法报告识别这些文件，并保留内容已被手动编辑的文件。

事件检测页可查看每个事件的峰时间、幅值、AUC、基线及积分起止时间。点击“导出当前事件表 CSV”可保存屏幕上的检测结果。完整导出的 `events.csv` 使用相同字段。AUC 使用去噪信号减去事件基线后的带符号梯形积分，单位为原纵轴单位 × 秒。

刺激锁定页右侧同时显示 AUC 和峰值的基线窗与响应窗配对比较。选择正峰取两个窗口各自的最大值，选择负峰取各自的最小值并保留符号。响应峰潜伏期随峰方向更新。每个连线代表同一试次，Z / ΔF/F 页签决定图的单位。`peth_metrics.csv` 保存逐试次数据，`peth_paired_stats.csv` 保存有效配对数、两窗均值和配对检验结果。

从 0.6.4 起，Plexon R 列开关仅在当前记录已读取且具有匹配 R 列时可操作。原 ΔF/F 滑动中位数窗口已移除。从 0.6.3 配置重新分析时会忽略该设置，已保存的数据仍可读取。

### 文件切换、运行日志与分析方法导出

同一窗口内切换记录时，保留各文件已经读取的通道、预处理结果、刺激锁定分析、事件检测和上次查看的页面。需要跨次启动留存的结果，可在“导出与批处理”页保存。运行日志按文件显示实际使用的 ΔF/F 来源或计算公式；Plexon 直接读取 R 时标注该列的原始比值。

每份成功导出的记录包含 `parameters.json`（供软件重新加载）、`analysis_methods.md`（供阅读）及 `analysis_methods.json`（供程序读取）。方法报告按实际分析步骤记录公式、参数和方法来源，便于核对不同文件的分析口径。

### 支持的记录格式

| 设备或格式 | 导入方法 |
|---|---|
| NI TDMS | 选择主记录，外置事件 TDMS 可自动配对。 |
| Plexon Multi-Wavelength / Dual-Wavelength | 在采集软件中导出合并波长的 **PhotoData CSV**，在通道组中选择光纤 F1、F2 等。读取 RawF 原始荧光；Stim 列记录光源状态。独立行为 TTL 表可在打标文件栏选择。单波长导出只含该波长；双波长校正请使用合并表。 |
| Neurophotometrics | 选择带 Timestamp、LedState 或 Flags 和 Region/Channel 编号列的 CSV；按 ROI 和激发波长拆帧。参考轨迹按时间插值，只保留两路共同覆盖的信号帧。 |
| Doric Neuroscience Studio | 打开 .doric 文件，在对应 Series 中选择 AnalogIn 或 Lock-In 解调通道。信号和参考的对应关系按实验接线指定。 |

CSV 默认使用秒，显式标注 `Timestamp (ms)` 或 `Timestamp (us)` 时自动换算。文件夹扫描按表头识别记录；每次分析一个选定的光纤或 Series；同一文件导出多个通道组时，分别选择输出目录，以保留每一组结果。Plexon 此入口用于光纤软件导出的荧光表，PLX/PL2 属于另一种采集格式。

[厂商导出说明（Plexon）](https://plexon.com/wp-content/uploads/2022/05/Multiwavelength-Photometry-Release-Notes.pdf) · [Doric 官方示例](https://github.com/doriclenses/readDoric) · [Neurophotometrics 采集手册](https://neurophotometrics.com/s/Full-Length-Manual-2021.pdf)

Fiber photometry (calcium imaging) analysis for photometry recordings: a batch-processing
core plus a bilingual (Chinese/English) desktop app for users without programming experience.

钙成像光纤记录分析软件：批处理核心 + 中英双语桌面界面，面向没有编程基础的使用者。

## FiberLume 0.6.9 更新公告

在 **③ 刺激锁定分析 → 图形显示** 中可以分别设置刺激前、后的显示时长。新建分析默认显示 **−5～+10 秒**。默认统计基线仍为 **−5～0 秒**，目标窗为 **0～5 秒**。将刺激后显示改为 2 秒或 15 秒，同一试次的峰值、AUC、Z 分数归一化和纳入条件均按原统计窗计算。绘图数据包保留显示和统计所需的全分辨率样本。

[查看 0.6.9 更新公告](packaging/releases/0.6.9.md)

## FiberLume 0.6.8 更新公告

- 五个主流程页签和读取页的“通道与选区 / 原始预览”加强了边框和选中提示。窗口变小时，参数与结果可以分开查看。从预处理返回框选信号区间时，按钮会完整显示并获得焦点。
- 刺激锁定分析自动按基线窗和目标窗截取每个试次。默认基线 −5～0 秒、目标 0～5 秒，对应图中 −5～+5 秒。调整任一窗口后重新运行，预览和导出使用相同区间。
- 文件打标可以和当前记录的手动 TTL 一起用于刺激锁定。同帧重合点只计一次，逐试次表标明事件来源。原有的单独分析方式仍可选择。
- 双击预览图中的标题、轴标签、图例、曲线或热图即可编辑文字、字号、系列颜色或热图色带。工具栏的“编辑”也可以按名称选择元素。用该图的保存按钮即可导出编辑后的图。
- 第 ③ 页的“试次”和第 ④ 页的“事件列表”增加“绘图数据包”导出。每个 ZIP 包含全分辨率 ΔF/F、Z 分数、时间坐标、计算参数和独立重绘脚本。刺激锁定包保留逐试次曲线、峰值和 AUC，事件包保留去噪轨迹、检测阈值、峰标记以及分布和频率比较数据。

已安装 0.6.0–0.6.7 的用户可以在“帮助 → 检查更新”下载补丁，然后重启 FiberLume。网络受限时，可以使用相同版本的离线更新包。

[查看可转发的完整更新公告](packaging/releases/0.6.8.md)

## 0.6.7 软件内更新

使用 FiberLume 0.6.0–0.6.6 时，打开“帮助 → 检查更新”，下载 0.6.7 补丁并重启。旧版如连不上原更新地址，可向维护者索取 0.6.7 离线包。

- 读取更新清单或下载补丁失败时，0.6.7 会尝试同一仓库的 GitHub 官方备用地址，并继续校验补丁的 SHA-256。
- 数据读取页调整了通道和通道设置的布局，小窗口可切换查看。

### 检查更新时连接失败

在出现问题的电脑上打开[官方更新清单](https://raw.githubusercontent.com/zjm199502/Fiberphotometry/updates/channel.json)。正常响应含有 `version` 和 `base`。若浏览器能打开清单而应用仍报错，请记录错误原文及发生在检查还是下载阶段，供维护者排查代理、证书或下载中断。

0.6.0–0.6.6 的更新器通过 `raw.githubusercontent.com` 读取清单和补丁。持续无法访问时，可以向维护者索取对应版本的离线更新包，核对校验值并按随包说明安装。0.6.7 的更新器另外支持同一仓库的 `api.github.com` 官方备用地址，两个地址都需要网络连接。

## 0.6.6 软件内更新

- ABET 分析采用双脉冲首尾重采样，原始采集帧与分析时间轴可在 HDF5 中核对。
- 五个分析页面按窗口大小和系统字号调整布局。参数区域和图表可滚动，事件图例排在波形外。

## 0.6.4 软件内更新

使用 0.6.0–0.6.3 时，打开“帮助 → 检查更新”，下载补丁并重启。

- 数据读取页可直接在波形上添加或撤销手动 TTL 事件点。
- 事件检测页可查看并导出每个事件的幅值、AUC、基线和积分边界。
- 刺激锁定页同时比较基线窗与响应窗的 AUC 和峰值，可选择正峰或负峰。
- 预处理已移除 ΔF/F 滑动中位数窗口。读取含匹配 R 列的 Plexon 记录后，可选择使用设备比值或从原始荧光重算。

## 0.6.3 软件内更新

已安装 0.6.0–0.6.2 的用户可在“帮助 → 检查更新”下载补丁并重启。

- 刺激锁定的 ΔF/F 按试次减去该试次所有刺激前样本的均值，保留输入单位。热图、平均曲线及 ΔF/F 峰值与 AUC 均使用这条归零曲线。每个试次减去的数值保存在 `peth_metrics.csv` 的 `prestimulus_mean_input_dff` 列。Z 分数仍使用单独设置的基线区间。
- 连续记录存在残余慢漂移时，可在预处理中启用 ΔF/F 滑动中位数校正。窗口填 0 时关闭，沿用原先的 ΔF/F。窗口应长于希望保留的响应，并短于希望跟踪的漂移。持续反应占据大半个窗口时，中位数会随其变化，反应幅度会减小。
- 五个工作页面都可清除本页结果并用当前参数重跑。清除读取或预处理结果会同时清除依赖它们的结果。导出页的清除按钮只重置界面状态。操作按钮、图表工具按钮及弹窗按钮均有独立边框。

## 0.6.2 软件内更新

使用 0.6.0 或 0.6.1 时，打开“帮助 → 检查更新”，下载后重启。

- 数据读取页支持按文件排除通道、导入一维行为 CSV，并按时间轴对齐预览。
- 可以独立框选信号采集段与可选基线段。预处理前确认，运行日志显示实际采用的 ΔF/F 公式。
- 临时切换文件后可恢复已加载的数据、分析结果和页面。批量导出保留已分析文件各自的设置。
- 导出结果附逐记录的方法、公式和来源报告。文件列表的四个操作按钮增加了可见边框。

## 0.6.1 软件内更新

已安装 0.6.0 的用户可从“帮助 → 检查更新”下载补丁，重启后生效。

- 事件图的三角尖指向去噪荧光曲线的峰值。`events.csv` 的 `peak_time_s` 记录峰值时间，`time_s` 保留检测时间。
- 运行预处理前可核对已读取的文件、通道、采样范围和本次 ΔF/F 的来源。
- 拖动或滚轮缩放时，长波形临时以较少的点绘制并保留区间极值。结束交互后恢复完整细节，分析和导出使用完整数据。

## 0.6.0 安装版 / Installation

[Windows x64 安装程序](https://github.com/zjm199502/Fiberphotometry/releases/download/v0.6.0/FiberLume-0.6.0-Windows-Setup.exe) · [macOS Apple silicon 安装镜像](https://github.com/zjm199502/Fiberphotometry/releases/download/v0.6.0/FiberLume-0.6.0-macOS-AppleSilicon.dmg)

从 0.5.0 或更早版本升级，请先下载安装版。Windows 双击 Setup 覆盖安装，macOS 打开 DMG，将 FiberLume 拖入 Applications。两个安装包都包含 Python 和分析依赖。

从 0.6.0 开始，打开“帮助 → 检查更新”可查询正式通道。有兼容补丁时，确认下载后，软件会核验 SHA-256 并保存补丁。可立即重启，也可在下次启动时生效。需要更换基础版本时，菜单会引导用户下载完整安装包。

在“数据读取”页，Plexon 钙信号可选择 `RawF_1`（原始荧光）或 `R_1`（设备 ΔF/F），对照通道可选 `F0_1`（设备基线）。选好后点击“读取”刷新预览。切换预览不会改变预处理页的 ΔF/F 来源设置。

在预览图内按住左键或中键拖动可平移，按住右键拖动可围绕按下的位置缩放。向右拖放大横轴，向上拖放大纵轴。滚轮围绕指针缩放，在坐标轴刻度处操作只改变该轴。工具栏的主页按钮可恢复全图。

## 从源码启动 / Run from source

```bash
pip install -e .[gui]
fiberphoto-gui            # 或 python -m fiberphoto.gui ；加 --en 以英文启动
```

界面按分析步骤分为 5 个标签页：① 数据读取 → ② 预处理 → ③ 刺激锁定分析 → ④ 事件检测 → ⑤ 导出与批处理。
每一步都可以单独运行并立即看到图表（可缩放、平移、保存），参数旁边有悬停说明。右上角随时切换中文/English，
图表语言同步切换。左侧“示例数据”按钮会在“文档/fiberphoto_sample_data”生成培训用数据。
参数可通过“文件 → 保存参数”存成 JSON，供全组共用。

新生图文操作手册：[docs/manual/用户手册.md](docs/manual/用户手册.md)（PDF：`docs/manual/manual_zh.pdf`），
English: [docs/manual/User_Manual.md](docs/manual/User_Manual.md)。软件内“帮助 → 用户手册”可直接打开 PDF。
修改手册后运行 `python docs/manual/build_pdf.py <chromium>` 重新生成 PDF。

![刺激锁定页合并文件打标和手动 TTL 后的热图、峰值与 AUC](docs/manual/img/zh_04_peth_z.png)

### 打包成可执行文件 / Building the app

```bash
pip install -e .[gui] pyinstaller
pyinstaller packaging/fiberphoto.spec --noconfirm   # → dist/fiberphoto/ （macOS 为 dist/fiberphoto.app）
```

PyInstaller 只能在目标系统上打包（Windows 版要在 Windows 上打包）。仓库放到 GitHub 后，
`.github/workflows/build.yml` 会在推送 `v*` 标签或手动触发时自动打包 Windows 和 macOS 两个版本。

## 流程 / Pipeline

| 阶段 Stage | 模块 Module | 输入 → 输出 |
|---|---|---|
| 数据读取 Read | `recordings.py`、`io_tdms.py` | 原始光纤记录 → **data A**（信号、对照通道、行为打标） |
| 去漂移 De-drift | `preprocess.dedrift` | data A → **data B**（ΔF/F，备份） |
| 反卷积 Deconvolve | `preprocess.deconvolve` (OASIS) | data B → **data C**（去噪信号 + 推断活动，备份） |
| 刺激锁定 Peri-event | `analysis.peri_event` | data B + 打标 → 逐试次矩阵、AUC、峰值、潜伏期 |
| 事件检测 Events | `analysis.detect_events` | data C → 事件表、频率、幅度、间隔 |
| 图表 Figures | `plotting.py` | 原始信号、去漂移、反卷积、热图 + PETH、AUC、事件分布、刺激前后频率 |

**通道识别**: 根据通道名自动猜测（470/465 → 信号，405/410 → 对照，TTL/DI/Stim → 打标），
也可以手动指定。打标可以来自 TDMS 的数字通道（自动检测上升沿），也可以来自 CSV/Excel 表格
（列名 onset/offset/label）。

**去漂移方法**: `isosbestic`（405 对照通道稳健回归拟合，默认），`biexponential`（无对照时自动使用），
`polynomial`，`highpass`。

## 安装 / Install

```bash
pip install -e .            # Python ≥ 3.10
```

## 使用 / Usage

```bash
fiberphoto sample examples/sample_data          # 生成示例数据（3 只小鼠）
fiberphoto inspect examples/sample_data/mouse01_session1.tdms   # 查看通道
fiberphoto config params.json                   # 导出默认参数，可编辑
fiberphoto run examples/sample_data -o results -c params.json --lang zh
```

Python:

```python
from fiberphoto import PipelineConfig, run_file
cfg = PipelineConfig()
cfg.peth.baseline_start_s, cfg.peth.baseline_end_s = -5, 0
cfg.peth.response_start_s, cfg.peth.response_end_s = 0, 5
res = run_file("mouse01_session1.tdms", cfg, "results", log=print)
res.peth.metrics, res.events.summary
```

## 输出 / Outputs (per recording)

```
results/<文件名>/
  <文件名>.h5                 data A / B / C 备份（HDF5）
  <文件名>_dataA.csv, _dataB.csv, _dataC.csv, _dataA_markers.csv
  peth_trials_dff.csv        逐试次归零 ΔF/F 曲线、均值和 SEM
  peth_trials_z.csv          逐试次 Z 分数曲线、均值和 SEM
  peth_metrics.csv           每个试次的 AUC、峰值、潜伏期，合并打标时另含来源
  events.csv                 每个事件的时间、幅度、半宽、间隔
  events_vs_stimulus.csv     每次刺激前后的事件数和频率
  summary.json               汇总指标
  parameters.json            本次使用的全部参数（可复现）
  figures/                   PNG + PDF（PDF 文字可在 Illustrator 中编辑）
results/batch_summary.csv    所有文件的汇总表
```

## 示例数据 / Sample data

`examples/sample_data/` 中的三份合成记录模拟 470/405 双通道、光漂白、运动伪迹、自发钙事件和
刺激诱发反应（mouse01 强反应，mouse02 中等，mouse03 几乎无反应）。每份数据附带
`_ground_truth.json`（真实事件时间），便于培训时核对分析结果。

## 测试 / Tests

```bash
pip install -e .[gui,test] && QT_QPA_PLATFORM=offscreen pytest
```
