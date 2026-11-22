"""Main window: file list on the left, one tab per analysis step, log at the bottom."""
from __future__ import annotations

import warnings
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # figures are drawn off-screen and embedded via FigureCanvasQTAgg
import matplotlib.pyplot as plt
import numpy as np
from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFileDialog,
                               QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QTableView, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import __version__, analysis, plotting
from ..config import PipelineConfig
from ..io_tdms import auto_map, find_event_file, inspect_tdms, is_event_file
from ..recordings import device_traces
from ..pipeline import attach_abet, attach_behavior, read_with_behavior, run_batch, run_file
from ..stimulus_markers import configured_markers
from ..abet import AbetConfig
from ..abet_batch import find_abet_sidecar
from .abet import AbetDialog
from .behavior import BehaviorDialog
from .event_table import EventTableModel
from .responsive import ResponsivePanels, SectionTabs, style_tabs
from .dialog_layout import confirm_details
from .baseline import BaselinePicker
from ..preprocess import (DataWarning, decay_time_constant, dedrift, deconvolve, format_dff_formula,
                          signal_interval_baseline)
from .texts import DECONV, DEDRIFT, EVENTS, PETH, ui
from .previews import channel_previews, preview_trace, sparkline
from .widgets import FigureView, OptionalFloat, ParamForm, Worker
from .texts import ParamSpec, L

ROLE_KEYS = {"signal": "role_signal", "control": "role_control", "marker": "role_marker", "time": "role_time"}


def _primary(btn: QPushButton) -> QPushButton:
    btn.setProperty("primary", True)
    btn.setMinimumHeight(34)
    return btn


def _scroll(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidget(w)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


@dataclass
class RecordingViewState:
    config: PipelineConfig
    results: tuple
    channel_infos: list
    previews: dict
    tab: int
    pre_note: str
    export_note: str
    export_progress: int
    loaded_once: bool


class MainWindow(QMainWindow):
    def __init__(self, lang: str | None = None):
        super().__init__()
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "fiberlume.png")))
        self.settings = QSettings("fiberphoto", "fiberphoto")
        self.lang = lang or self.settings.value("lang", "zh")
        self.cfg = PipelineConfig()
        self.pool = QThreadPool(self)
        self._closing = False
        self._stop = False
        self._busy = False
        self._workers = set()
        self.current: Path | None = None
        self.abet_candidate: Path | None = None
        # A recording keeps its own batch settings after its in-memory data is cleared.
        self._loaded_once = False
        self.recording_states: dict[Path, RecordingViewState] = {}
        self.channel_infos = []
        self.previews = {}
        self._reset_results()

        self._build_menu()
        self._build_files_panel()
        self._build_tabs()
        self._build_log()

        split = QSplitter(Qt.Horizontal)
        self.file_sidebar = _scroll(self.files_box)
        split.addWidget(self.file_sidebar)
        split.setChildrenCollapsible(False)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 1160])
        center = QWidget()
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        self.file_compact = QWidget()
        file_row = QHBoxLayout(self.file_compact)
        file_row.setContentsMargins(4, 0, 4, 0)
        self.file_combo = QComboBox()
        self.file_combo.setModel(self.file_list.model())
        self.file_combo.activated.connect(self.file_list.setCurrentRow)
        self.file_list.currentRowChanged.connect(self.file_combo.setCurrentIndex)
        self.file_menu_button = QPushButton()
        menu = QMenu(self.file_menu_button)
        menu.addActions([self.a_add, self.a_folder, self.a_sample])
        self.a_compact_remove = menu.addAction("", self.remove_file)
        self.file_menu_button.setMenu(menu)
        file_row.addWidget(self.file_menu_button)
        file_row.addWidget(self.file_combo, 1)
        layout.addWidget(self.file_compact)
        layout.addWidget(split, 1)
        self.setCentralWidget(center)
        self._short_window = None
        self._compact_navigation = None
        screen = self.screen().availableGeometry()
        self.resize(min(1400, int(screen.width() * .97)), min(900, int(screen.height() * .92)))

        self.forms_load()
        self.retranslate()
        self._update_enabled()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "file_compact"):
            return
        narrow = event.size().width() < max(860, 54 * self.fontMetrics().height())
        self.file_compact.setVisible(narrow)
        self.file_sidebar.setVisible(not narrow)
        compact_read = event.size().width() < max(1080, 68 * self.fontMetrics().height())
        self.baseline_picker.set_compact(compact_read or self.lang == "en")
        short = event.size().height() < 540
        compact_navigation = short or narrow
        if compact_navigation != self._compact_navigation:
            self._compact_navigation = compact_navigation
            style_tabs(self.tabs, workflow=True, compact=compact_navigation)
        if short != self._short_window:
            self._short_window = short
            self.log_dock.setVisible(not short)
            style_tabs(self.peth_views, compact=short)
            for label, help_button in self.page_help:
                label.setVisible(not short)
                help_button.setVisible(short)

    # ------------------------------------------------------------------ state
    def _reset_results(self):
        self.A = self.B = self.C = None
        self.peth = self.events = self.stim_events = None

    def _remember_recording(self):
        cfg = self.forms_store()
        # File-specific association maps are shared by the open session. Snapshot
        # just this view's settings; large analysis arrays are retained by reference.
        local = replace(cfg, channels=replace(cfg.channels, excluded_channels={}),
                        behavior_by_recording={}, abet_by_recording={}, intervals_by_recording={}, manual_ttl_by_recording={},
                        figure_edits_by_recording={})
        self.recording_states[self.current] = RecordingViewState(
            deepcopy(local), (self.A, self.B, self.C, self.peth, self.events, self.stim_events),
            self.channel_infos, self.previews, self.tabs.currentIndex(),
            self.lbl_pre.text(), self.lbl_export.text(), self.progress.value(), self._loaded_once)

    def _view_config(self, local):
        channels = replace(local.channels, excluded_channels=self.cfg.channels.excluded_channels)
        return replace(local, channels=channels, plot=self.cfg.plot, save_csv=self.cfg.save_csv,
                       behavior_by_recording=self.cfg.behavior_by_recording,
                       abet_by_recording=self.cfg.abet_by_recording,
                       intervals_by_recording=self.cfg.intervals_by_recording,
                       manual_ttl_by_recording=self.cfg.manual_ttl_by_recording,
                       figure_edits_by_recording=self.cfg.figure_edits_by_recording)

    def _restore_recording(self, state):
        self.cfg = self._view_config(state.config)
        self.A, self.B, self.C, self.peth, self.events, self.stim_events = state.results
        self.channel_infos, self.previews = state.channel_infos, state.previews
        self.forms_load()
        self.lbl_pre.setText(state.pre_note)
        self.lbl_export.setText(state.export_note)
        self.progress.setValue(state.export_progress)
        self._loaded_once = state.loaded_once

    def t(self, key, **kw):
        return ui(key, self.lang, **kw)

    # ------------------------------------------------------------------ build
    def _build_menu(self):
        mb = self.menuBar()
        self.m_file = mb.addMenu("")
        self.a_add = QAction(self, shortcut=QKeySequence.Open, triggered=self.add_files)
        self.a_folder = QAction(self, triggered=self.add_folder)
        self.a_sample = QAction(self, triggered=self.load_sample)
        self.a_load = QAction(self, triggered=self.load_params)
        self.a_save = QAction(self, shortcut=QKeySequence.Save, triggered=self.save_params)
        self.a_reset = QAction(self, triggered=self.reset_params)
        self.a_quit = QAction(self, shortcut=QKeySequence.Quit, triggered=self.close)
        for a in (self.a_add, self.a_folder, self.a_sample, None, self.a_load, self.a_save,
                  self.a_reset, None, self.a_quit):
            self.m_file.addSeparator() if a is None else self.m_file.addAction(a)

        self.m_lang = mb.addMenu("")
        grp = QActionGroup(self)
        self.a_zh = QAction("中文", self, checkable=True, triggered=lambda: self.set_lang("zh"))
        self.a_en = QAction("English", self, checkable=True, triggered=lambda: self.set_lang("en"))
        for a in (self.a_zh, self.a_en):
            grp.addAction(a)
            self.m_lang.addAction(a)

        self.m_help = mb.addMenu("")
        self.a_guide = QAction(self, shortcut=QKeySequence.HelpContents, triggered=self.show_guide)
        self.a_manual = QAction(self, triggered=self.open_manual)
        self.a_about = QAction(self, triggered=self.show_about)
        self.m_help.addAction(self.a_manual)
        self.m_help.addAction(self.a_guide)
        self.a_update = QAction(self, triggered=self.check_update)
        self.m_help.addAction(self.a_update)
        self.m_help.addAction(self.a_about)

        # language switch also lives in the corner for discoverability
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.currentIndexChanged.connect(lambda i: self.set_lang(self.lang_combo.itemData(i)))
        mb.setCornerWidget(self.lang_combo, Qt.TopRightCorner)

    def _build_files_panel(self):
        self.files_box = QGroupBox()
        self.files_box.setObjectName("recordingFiles")
        lay = QVBoxLayout(self.files_box)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_list.currentItemChanged.connect(self.on_file_selected)
        self.files_empty = QLabel()
        self.files_empty.setWordWrap(True)
        self.files_empty.setStyleSheet("color: #8a8984;")
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.b_add = QPushButton(clicked=self.add_files)
        self.b_folder = QPushButton(clicked=self.add_folder)
        self.b_sample = QPushButton(clicked=self.load_sample)
        self.b_remove = QPushButton(clicked=self.remove_file)
        row1.addWidget(self.b_add)
        row1.addWidget(self.b_folder)
        row2.addWidget(self.b_sample)
        row2.addWidget(self.b_remove)
        lay.addLayout(row1)
        lay.addLayout(row2)
        lay.addWidget(self.files_empty)
        lay.addWidget(self.file_list, 1)

    def _page_header(self, layout, page: str) -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        lab.setObjectName("help")
        row = QHBoxLayout()
        row.addWidget(lab, 1)
        help_button = QPushButton()
        help_button.clicked.connect(lambda: QMessageBox.information(self, self.tabs.tabText(self.tabs.currentIndex()),
                                                                    lab.text()))
        help_button.hide()
        row.addWidget(help_button)
        self.page_help.append((lab, help_button))
        button = QPushButton(clicked=lambda: self.clear_page_results(page))
        self.clear_buttons[page] = button
        row.addWidget(button)
        layout.addLayout(row)
        return lab

    def _build_tabs(self):
        self.clear_buttons = {}
        self.page_help = []
        self.tabs = QTabWidget()
        style_tabs(self.tabs, workflow=True)
        self.tabs.addTab(_scroll(self._tab_read()), "")
        self.tabs.widget(0).verticalScrollBar().rangeChanged.connect(
            self._reveal_read_shortcut, Qt.QueuedConnection)
        self.tabs.addTab(_scroll(self._tab_pre()), "")
        self.tabs.addTab(_scroll(self._tab_peth()), "")
        self.tabs.addTab(_scroll(self._tab_events()), "")
        self.tabs.addTab(_scroll(self._tab_export()), "")

    def _analysis_panels(self, controls, plots):
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(controls)
        split.addWidget(plots)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 760])
        return ResponsivePanels(split, [controls, plots], [("参数", "Parameters"), ("结果图", "Plots")],
                                min_width=max(840, 56 * self.fontMetrics().height()), min_height=320)

    # tab ① ----------------------------------------------------------------
    def _tab_read(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_read = self._page_header(v, "read")
        controls = QWidget()
        inputs = QVBoxLayout(controls)
        inputs.setContentsMargins(0, 0, 0, 0)
        associations = QWidget()
        association_layout = QVBoxLayout(associations)
        behavior_row = QHBoxLayout()
        self.b_behavior = QPushButton(clicked=self.pick_behavior_csv)
        self.b_behavior_clear = QPushButton(clicked=lambda: self.set_behavior(None))
        self.lbl_behavior = QLabel()
        self.lbl_behavior.setTextFormat(Qt.PlainText)
        self.lbl_behavior.setWordWrap(True)
        self.b_abet = QPushButton(clicked=self.pick_abet)
        self.b_abet_clear = QPushButton(clicked=self.remove_abet)
        self.lbl_abet = QLabel()
        self.lbl_abet.setTextFormat(Qt.PlainText)
        self.lbl_abet.setWordWrap(True)
        behavior_row.addWidget(self.b_behavior)
        behavior_row.addWidget(self.b_behavior_clear)
        behavior_row.addWidget(self.lbl_behavior, 1)
        association_layout.addLayout(behavior_row)
        abet_row = QHBoxLayout()
        abet_row.addWidget(self.b_abet)
        abet_row.addWidget(self.b_abet_clear)
        abet_row.addWidget(self.lbl_abet, 1)
        association_layout.addLayout(abet_row)
        association_layout.addStretch()

        self.chan_box = QWidget()
        cl = QVBoxLayout(self.chan_box)
        self.chan_table = QTableWidget(0, 6)
        self.chan_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.chan_table.verticalHeader().setVisible(False)
        self.chan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.chan_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.chan_table.setIconSize(QSize(160, 30))
        self.chan_table.verticalHeader().setDefaultSectionSize(max(34, self.fontMetrics().height() + 8))
        self.chan_table.setMinimumHeight(6 * self.fontMetrics().height())
        self.chan_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        cl.addWidget(self.chan_table)
        exclusions = QHBoxLayout()
        self.b_exclude = QPushButton(clicked=self.exclude_selected_channels)
        self.b_restore = QPushButton(clicked=self.restore_channels)
        exclusions.addWidget(self.b_exclude)
        exclusions.addWidget(self.b_restore)
        exclusions.addStretch()
        cl.addLayout(exclusions)

        self.map_box = QWidget()
        map_layout = QVBoxLayout(self.map_box)
        signal_page, marker_page = QWidget(), QWidget()
        f = QFormLayout(signal_page)
        marker_form = QFormLayout(marker_page)
        for form in (f, marker_form):
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setContentsMargins(4, 4, 4, 4)
        self.mapping_tabs = SectionTabs([(signal_page, ("荧光信号", "Fluorescence")),
                                         (marker_page, ("事件标记", "Markers"))])
        map_layout.addWidget(self.mapping_tabs)
        self.cb_group = QComboBox()
        self.cb_group.currentIndexChanged.connect(self._fill_channel_combos)
        self.cb_signal = QComboBox()
        self.cb_control = QComboBox()
        self.lst_markers = QListWidget()
        self.lst_markers.setMaximumHeight(90)
        self.fs_edit = OptionalFloat(ParamSpec("channels", "sampling_rate", "optfloat", L("", ""), L("", ""),
                                               min=0.1, max=1e6, step=1, decimals=2, suffix=" Hz"))
        self.fs_edit.spin.setMinimum(0)
        self.fs_edit.spin.setSpecialValueText("—")  # 0 shows as a dash = "please fill in"
        self.fs_edit.setValue(None)
        self.lbl_fs_missing = QLabel()
        self.lbl_fs_missing.setWordWrap(True)
        self.lbl_fs_missing.setStyleSheet("color: #b4530f; font-weight: 600;")
        self.lbl_fs_missing.hide()
        mf = QHBoxLayout()
        self.marker_file = QLineEdit()
        self.marker_file.setReadOnly(True)
        self.b_marker_file = QPushButton(clicked=self.pick_marker_file)
        self.b_marker_clear = QPushButton(clicked=lambda: self.marker_file.clear())
        mf.addWidget(self.marker_file, 1)
        mf.addWidget(self.b_marker_file)
        mf.addWidget(self.b_marker_clear)
        self.lbl_map = [QLabel() for _ in range(6)]
        f.addRow(self.lbl_map[0], self.cb_group)
        f.addRow(self.lbl_map[1], self.cb_signal)
        f.addRow(self.lbl_map[2], self.cb_control)
        self.lbl_indicator = QLabel()
        self.indicator_edit = QLineEdit()
        f.addRow(self.lbl_indicator, self.indicator_edit)
        marker_form.addRow(self.lbl_map[3], self.lst_markers)
        f.addRow(self.lbl_map[4], self.fs_edit)
        f.addRow(self.lbl_fs_missing)
        marker_form.addRow(self.lbl_map[5], mf)
        self.b_read = _primary(QPushButton(clicked=self.step_read))

        self.lbl_markers_found = QLabel()
        self.lbl_markers_found.setWordWrap(True)
        marker_form.addRow(self.lbl_markers_found)
        channel_split = QSplitter(Qt.Horizontal)
        channel_split.setChildrenCollapsible(False)
        channel_split.addWidget(self.chan_box)
        channel_split.addWidget(self.map_box)
        channel_split.setSizes([600, 440])
        self.channel_panels = ResponsivePanels(
            channel_split, [self.chan_box, self.map_box],
            [("通道", "Channels"), ("通道设置", "Channel settings")],
            min_width=max(750, 49 * self.fontMetrics().height()), fit_contents=True)

        self.fig_raw = FigureView()
        self.baseline_picker = BaselinePicker(self)
        intervals = QWidget()
        interval_layout = QVBoxLayout(intervals)
        interval_layout.addWidget(self.baseline_picker)
        interval_layout.addStretch()
        self.read_settings = SectionTabs([(self.channel_panels, ("通道", "Channels")),
                                          (associations, ("行为数据", "Behavior")),
                                          (intervals, ("选区与 TTL", "Intervals and TTL"))])
        inputs.addWidget(self.read_settings, 1)
        inputs.addWidget(self.b_read)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(controls)
        split.addWidget(self.fig_raw)
        self.fig_raw.setMinimumHeight(14 * self.fontMetrics().height())
        split.setStretchFactor(1, 1)
        self.read_panels = ResponsivePanels(split, [controls, self.fig_raw],
                                           [("通道与选区", "Channels and intervals"), ("原始预览", "Raw preview")],
                                           min_width=790, fit_contents=True, fit_plots=True)
        v.addWidget(self.read_panels, 1)
        return w

    # tab ② ----------------------------------------------------------------
    def _tab_pre(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_pre = self._page_header(v, "pre")
        left = QWidget()
        lv = QVBoxLayout(left)
        self.b_choose_windows = QPushButton(clicked=self._choose_windows)
        self.form_dedrift = ParamForm(DEDRIFT, sections=[
            ("lowpass_hz", "downsample_hz", "poly_order", "highpass_hz", "output"),
            ("auto_trim", "trim_start_s", "trim_end_s")])
        self.form_dedrift.changed.connect(self.baseline_picker.refresh)
        self.form_dedrift.editors["prefer_plexon_dff"].toggled.connect(self._sync_dff_source)
        self.form_dedrift.editors["method"].currentIndexChanged.connect(self._sync_dff_source)
        self.form_deconv = ParamForm(DECONV)
        baseline = QWidget()
        baseline_layout = QVBoxLayout(baseline)
        baseline_layout.setContentsMargins(3, 3, 3, 3)
        baseline_layout.addWidget(self.b_choose_windows)
        baseline_layout.addWidget(self.form_dedrift)
        baseline_layout.addStretch()
        details = QWidget()
        details_layout = QVBoxLayout(details)
        self.lbl_pre = QLabel()
        self.lbl_pre.setWordWrap(True)
        details_layout.addWidget(self.lbl_pre)
        details_layout.addStretch()
        self.pre_settings = SectionTabs([(baseline, ("基线", "Baseline")),
                                        (self.form_dedrift.sections[0], ("滤波", "Filtering")),
                                        (self.form_dedrift.sections[1], ("裁剪", "Trim")),
                                        (self.form_deconv, ("反卷积", "Deconvolution")),
                                        (details, ("摘要", "Summary"))])
        self.b_pre = _primary(QPushButton(clicked=self.step_pre))
        lv.addWidget(self.pre_settings, 1)
        lv.addWidget(self.b_pre)

        self.fig_dedrift = FigureView()
        self.fig_deconv = FigureView()
        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self.fig_dedrift)
        right.addWidget(self.fig_deconv)
        plots = ResponsivePanels(right, [self.fig_dedrift, self.fig_deconv],
                                 [("ΔF/F", "ΔF/F"), ("反卷积", "Deconvolution")],
                                 min_width=515, min_height=440, fit_plots=True)
        self.pre_panels = self._analysis_panels(left, plots)
        v.addWidget(self.pre_panels, 1)
        return w

    # tab ③ ----------------------------------------------------------------
    def _tab_peth(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_peth = self._page_header(v, "peth")
        left = QWidget()
        lv = QVBoxLayout(left)
        timing = QWidget()
        bl = QVBoxLayout(timing)
        mrow = QFormLayout()
        mrow.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.lbl_marker_use = QLabel()
        self.cb_marker_use = QComboBox()
        mrow.addRow(self.lbl_marker_use, self.cb_marker_use)
        bl.addLayout(mrow)
        self.form_peth = ParamForm(PETH, sections=[("response_start_s", "response_end_s", "peak_direction"),
                                                  ("display_pre_s", "display_post_s")])
        for key in ("display_pre_s", "display_post_s"):
            self.form_peth.editors[key].editingFinished.connect(self._apply_peth_display)
        bl.addWidget(self.form_peth)
        bl.addStretch()
        self.b_peth = _primary(QPushButton(clicked=self.step_peth))
        from .abet_comparison import compare_abet
        self.b_abet_compare = QPushButton(clicked=lambda: compare_abet(self))
        self.trial_table = QTableWidget(0, 0)
        self.trial_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.trial_table.verticalHeader().setVisible(False)
        trials = QWidget()
        trials_layout = QVBoxLayout(trials)
        trials_layout.setContentsMargins(3, 3, 3, 3)
        trials_layout.addWidget(self.trial_table, 1)
        trial_actions = QHBoxLayout()
        self.b_export_peth_plot_data = QPushButton(clicked=lambda: self.export_plot_data("peth"))
        trial_actions.addWidget(self.b_abet_compare)
        trial_actions.addWidget(self.b_export_peth_plot_data)
        trials_layout.addLayout(trial_actions)
        self.peth_settings = SectionTabs([(timing, ("刺激与基线", "Stimulus and baseline")),
                                         (self.form_peth.sections[0], ("响应", "Response")),
                                         (self.form_peth.sections[1], ("图形显示", "Display")),
                                         (trials, ("试次", "Trials"))])
        lv.addWidget(self.peth_settings, 1)
        lv.addWidget(self.b_peth)

        self.fig_peth = FigureView()
        self.fig_peth_dff = FigureView()
        self.fig_auc = FigureView()
        self.peth_views = QTabWidget()
        self.peth_views.addTab(self.fig_peth, "")
        self.peth_views.addTab(self.fig_peth_dff, "")
        self.peth_views.currentChanged.connect(self._draw_peth_comparisons)
        right = QSplitter(Qt.Horizontal)
        right.setChildrenCollapsible(False)
        right.addWidget(self.peth_views)
        right.addWidget(self.fig_auc)
        right.setSizes([560, 340])
        plots = ResponsivePanels(right, [self.peth_views, self.fig_auc],
                                 [("刺激锁定", "Peri-event"), ("分组统计", "Group statistics")],
                                 min_width=680, min_height=320, fit_plots=True)
        self.peth_panels = self._analysis_panels(left, plots)
        v.addWidget(self.peth_panels, 1)
        return w

    # tab ④ ----------------------------------------------------------------
    def _tab_events(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_events = self._page_header(v, "events")
        left = QWidget()
        lv = QVBoxLayout(left)
        self.form_events = ParamForm(EVENTS)
        self.b_events = _primary(QPushButton(clicked=self.step_events))
        self.lbl_events = QLabel()
        self.lbl_events.setWordWrap(True)
        self.event_table = QTableView()
        self.event_model = EventTableModel(self.event_table)
        self.event_table.setModel(self.event_model)
        self.event_table.setAlternatingRowColors(True)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.event_table.horizontalHeader().setResizeContentsPrecision(0)
        self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        event_list = QWidget()
        event_layout = QVBoxLayout(event_list)
        event_layout.setContentsMargins(3, 3, 3, 3)
        event_layout.addWidget(self.lbl_events)
        event_layout.addWidget(self.event_table, 1)
        self.b_export_events = QPushButton(clicked=self.export_event_table)
        event_actions = QHBoxLayout()
        self.b_export_event_plot_data = QPushButton(clicked=lambda: self.export_plot_data("events"))
        event_actions.addWidget(self.b_export_events)
        event_actions.addWidget(self.b_export_event_plot_data)
        event_layout.addLayout(event_actions)
        self.event_settings = SectionTabs([(self.form_events, ("检测参数", "Detection")),
                                          (event_list, ("事件列表", "Events table"))])
        lv.addWidget(self.event_settings, 1)
        lv.addWidget(self.b_events)

        self.fig_events = FigureView()
        self.fig_dist = FigureView()
        self.fig_rate = FigureView()
        bottom = QSplitter(Qt.Horizontal)
        bottom.setChildrenCollapsible(False)
        bottom.addWidget(self.fig_dist)
        bottom.addWidget(self.fig_rate)
        bottom.setSizes([600, 300])
        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        right.addWidget(self.fig_events)
        right.addWidget(bottom)
        plots = ResponsivePanels(right, [self.fig_events, self.fig_dist, self.fig_rate],
                                 [("事件波形", "Events"), ("分布", "Distributions"), ("频率", "Rates")],
                                 min_width=680, min_height=445, fit_plots=True)
        self.event_panels = self._analysis_panels(left, plots)
        v.addWidget(self.event_panels, 1)
        return w

    # tab ⑤ ----------------------------------------------------------------
    def _tab_export(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_export = self._page_header(v, "export")
        output, batch = QWidget(), QWidget()
        f = QFormLayout(output)
        f.setRowWrapPolicy(QFormLayout.WrapLongRows)
        batch_form = QFormLayout(batch)
        batch_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        orow = QHBoxLayout()
        self.out_dir = QLineEdit(self.settings.value("out_dir", ""))
        self.b_out = QPushButton(clicked=self.pick_out_dir)
        orow.addWidget(self.out_dir, 1)
        orow.addWidget(self.b_out)
        frow = QHBoxLayout()
        self.chk_fmt = {fmt: QCheckBox(fmt.upper()) for fmt in ("png", "pdf", "svg", "tif")}
        for c in self.chk_fmt.values():
            frow.addWidget(c)
        frow.addStretch(1)
        self.spin_dpi = QSpinBox()
        self.spin_dpi.setRange(72, 1200)
        self.spin_dpi.setSingleStep(50)
        self.cb_fig_lang = QComboBox()
        self.cb_fig_lang.addItem("中文", "zh")
        self.cb_fig_lang.addItem("English", "en")
        self.chk_csv = QCheckBox()
        self.lbl_out = [QLabel() for _ in range(5)]
        f.addRow(self.lbl_out[0], orow)
        f.addRow(self.lbl_out[1], frow)
        f.addRow(self.lbl_out[2], self.spin_dpi)
        f.addRow(self.lbl_out[3], self.cb_fig_lang)
        f.addRow(self.lbl_out[4], self.chk_csv)
        manifest_row = QHBoxLayout()
        self.abet_manifest = QLineEdit()
        self.abet_manifest.setClearButtonEnabled(True)
        self.lbl_manifest = QLabel()
        self.b_manifest = QPushButton(clicked=self.pick_abet_manifest)
        self.b_manifest_template = QPushButton(clicked=self.save_abet_manifest_template)
        manifest_row.addWidget(self.abet_manifest, 1)
        manifest_row.addWidget(self.b_manifest)
        manifest_row.addWidget(self.b_manifest_template)
        batch_form.addRow(self.lbl_manifest, manifest_row)

        brow = QGridLayout()
        self.b_export = _primary(QPushButton(clicked=self.export_current))
        self.b_batch = _primary(QPushButton(clicked=self.run_batch))
        self.b_cancel = QPushButton(clicked=self.cancel)
        self.b_open_out = QPushButton(clicked=self.open_out_dir)
        f.addRow(self.b_export)
        batch_form.addRow(self.b_batch)
        self.export_settings = SectionTabs([(output, ("输出设置", "Output settings")),
                                           (batch, ("批处理", "Batch"))])
        v.addWidget(self.export_settings, 1)
        brow.addWidget(self.b_open_out, 0, 0)
        brow.addWidget(self.b_cancel, 0, 1)
        v.addLayout(brow)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        v.addWidget(self.progress)
        self.lbl_export = QLabel()
        self.lbl_export.setWordWrap(True)
        self.lbl_export.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_export)
        return w

    def _build_log(self):
        self.log_dock = QDockWidget(self)
        self.log_dock.setObjectName("log")
        self.log_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable |
                                  QDockWidget.DockWidgetClosable)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.m_help.addAction(self.log_dock.toggleViewAction())
        self.log_view.setMinimumHeight(60)
        QTimer.singleShot(0, lambda: self.resizeDocks([self.log_dock], [110], Qt.Vertical))

    # ------------------------------------------------------------- language
    def set_lang(self, lang: str):
        if lang == self.lang and self.windowTitle():
            self._sync_lang_controls()
            return
        self._store_marker_choice()
        self.lang = lang
        self._refresh_marker_choices()
        self.settings.setValue("lang", lang)
        self.cb_fig_lang.setCurrentIndex(0 if lang == "zh" else 1)  # exported figures follow the interface
        self.retranslate()
        self.redraw_all()

    def _sync_lang_controls(self):
        self.a_zh.setChecked(self.lang == "zh")
        self.a_en.setChecked(self.lang == "en")
        self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentIndex(0 if self.lang == "zh" else 1)
        self.lang_combo.blockSignals(False)

    def retranslate(self):
        t = self.t
        self.baseline_picker.set_compact(self.width() < max(1080, 68 * self.fontMetrics().height())
                                         or self.lang == "en")
        self.setWindowTitle(f"FiberLume {__version__} · {t('app_title')}")
        self._sync_lang_controls()
        self.m_file.setTitle(t("menu_file"))
        self.m_lang.setTitle(t("menu_lang"))
        self.m_help.setTitle(t("menu_help"))
        self.a_update.setText("检查更新…" if self.lang == "zh" else "Check for updates…")
        for a, k in ((self.a_add, "act_add_files"), (self.a_folder, "act_add_folder"),
                     (self.a_sample, "act_sample"), (self.a_load, "act_load_params"),
                     (self.a_save, "act_save_params"), (self.a_reset, "act_reset_params"),
                     (self.a_quit, "act_quit"), (self.a_manual, "act_manual"), (self.a_guide, "act_guide"), (self.a_about, "act_about")):
            a.setText(t(k))
        self.files_box.setTitle(t("files"))
        self.file_menu_button.setText(t("files"))
        self.a_compact_remove.setText(t("btn_remove"))
        for panels in [*self.findChildren(ResponsivePanels), *self.findChildren(SectionTabs)]:
            panels.set_lang(self.lang)
        for _, help_button in self.page_help:
            help_button.setText("页面说明" if self.lang == "zh" else "Page help")
        for b, k in ((self.b_add, "btn_add"), (self.b_folder, "btn_folder"),
                     (self.b_sample, "btn_sample"), (self.b_remove, "btn_remove")):
            b.setText(t(k))
        self.files_empty.setText(t("files_empty"))
        for i, k in enumerate(("tab_read", "tab_pre", "tab_peth", "tab_events", "tab_export")):
            self.tabs.setTabText(i, t(k))
        self.help_read.setText(t("help_read"))
        self.baseline_picker.refresh()
        self._sync_behavior()
        self.lbl_indicator.setText("荧光指示器" if self.lang == "zh" else "Fluorescent indicator")
        self.indicator_edit.setPlaceholderText("自动读取，未注明时为 GCaMP" if self.lang == "zh" else "From file, otherwise GCaMP")
        self.help_pre.setText(t("help_pre"))
        self.help_peth.setText(t("help_peth"))
        self.help_events.setText(t("help_events"))
        self.help_export.setText(t("help_export"))
        for page, button in self.clear_buttons.items():
            button.setText(t("clear_page"))
            button.setToolTip(t(f"clear_{page}_tip"))
        # tab 1
        self.b_exclude.setText(t("exclude_channels"))
        self.b_exclude.setToolTip(t("exclude_channels_tip"))
        self.b_restore.setText(t("restore_channels"))
        self.chan_table.setHorizontalHeaderLabels([t(k) for k in ("col_group", "col_channel", "col_samples",
                                                                  "col_fs", "col_guess", "col_preview")])
        self.lbl_fs_missing.setText(t("fs_missing"))
        self._fill_channel_table()
        for lab, k in zip(self.lbl_map, ("lbl_group", "lbl_signal", "lbl_control", "lbl_markers", "lbl_fs",
                                         "lbl_marker_file")):
            lab.setText(t(k))
        self.fs_edit.off.setText(t("from_file"))
        if self.cb_control.count():
            self.cb_control.setItemText(0, t("none"))
        self.b_marker_file.setText(t("browse"))
        for wdg in (self.marker_file, self.lbl_map[5]):
            wdg.setToolTip(t("marker_file_tip"))
        self._show_auto_pair()
        self.b_marker_clear.setText(t("clear"))
        self.b_read.setText(t("btn_read"))
        self._show_markers_found()
        # tab 2
        self.b_choose_windows.setText(t("choose_windows"))
        self.b_pre.setText(t("btn_pre"))
        for f in (self.form_dedrift, self.form_deconv, self.form_peth, self.form_events):
            f.set_lang(self.lang)
        # tab 3
        self.lbl_marker_use.setText(t("lbl_marker_use"))
        self.cb_marker_use.setToolTip("可单选一种打标，也可选择“文件打标名 + 手动 TTL”合并分析。"
                                     if self.lang == "zh" else
                                     "Select one series, or choose a file marker + manual TTL to combine them.")
        self.b_peth.setText(t("btn_peth"))
        self.b_abet_compare.setText("Compare ABET groups…" if self.lang == "en"
                                    else "比较 ABET 声音与进槽分组…")
        self.b_abet_compare.setToolTip(
            "Requires preprocessed calcium resampled to the ABET interval. Baseline −3 to 0 s, response 0 to 3 s."
            if self.lang == "en" else "先读取 ABET 数据并预处理重采样后的钙信号。逐试次基线 −3 至 0 秒，响应窗 0 至 3 秒。")
        self.peth_views.setTabText(0, t("view_z"))
        self.peth_views.setTabText(1, t("view_dff"))
        # tab 4
        self.b_events.setText(t("btn_events"))
        self.b_export_events.setText(t("export_event_table"))
        for button in (self.b_export_peth_plot_data, self.b_export_event_plot_data):
            button.setText(t("export_plot_data"))
            button.setToolTip(t("export_plot_data_tip"))
        self._show_event_summary()
        # tab 5
        for lab, k in zip(self.lbl_out, ("lbl_outdir", "lbl_formats", "lbl_dpi", "lbl_fig_lang", "lbl_csv")):
            lab.setText(t(k))
        self.b_out.setText(t("browse"))
        self.lbl_manifest.setText("ABET 批处理文件清单" if self.lang == "zh" else "ABET batch file list")
        self.abet_manifest.setPlaceholderText("可选 CSV，用于指定各记录对应的 XML" if self.lang == "zh"
                                             else "Optional CSV of recording and XML file pairs")
        self.b_manifest.setText(t("browse"))
        self.b_manifest_template.setText("导出清单模板" if self.lang == "zh" else "Save template")
        self.b_export.setText(t("btn_export_one"))
        self.b_batch.setText(t("btn_batch"))
        self.b_cancel.setText(t("btn_cancel"))
        self.b_open_out.setText(t("btn_open_out"))
        self.log_dock.setWindowTitle(t("log"))
        if not self._busy:
            self.statusBar().showMessage(t("ready"))
        for fv in (self.fig_raw, self.fig_dedrift, self.fig_deconv, self.fig_peth, self.fig_peth_dff, self.fig_auc,
                   self.fig_events, self.fig_dist, self.fig_rate):
            fv.set_placeholder(t("fig_empty"))

    # --------------------------------------------------------------- params
    def _sync_dff_source(self):
        form = self.form_dedrift
        key = "prefer_plexon_dff"
        interval = form.editors["method"].currentData() == "signal_interval"
        # Only the Plexon reader populates exported_dff, for the selected signal's matched R column.
        available = self.A is not None and self.A.exported_dff is not None and not interval
        form.editors[key].setEnabled(available)
        form.labels[key].setEnabled(available)
        direct = available and form.editors[key].isChecked()
        for name, editor in form.editors.items():
            if name == key:
                continue
            visible = not direct or name in ("signal_start_s", "signal_end_s")
            if name in ("baseline_start_s", "baseline_end_s"):
                visible = interval
            elif name == "auto_trim" and interval:
                visible = False
            editor.setVisible(visible)
            form.labels[name].setVisible(visible)

    def _load_dedrift(self):
        effective = self.cfg.for_recording(self.current, raw=self.A)
        self.form_dedrift.load(effective)
        self.baseline_picker.previous_source = None
        if effective.dedrift.method == "signal_interval" and self.cfg.dedrift.method != "signal_interval":
            self.baseline_picker.previous_source = (self.cfg.dedrift.method, self.cfg.dedrift.prefer_plexon_dff)

    def forms_load(self):
        self._load_dedrift()
        for f in (self.form_deconv, self.form_events):
            f.load(self.cfg)
        left, right = self.cfg.peth.display_bounds_s()
        self.form_peth.load(replace(self.cfg, peth=replace(self.cfg.peth, display_pre_s=-left, display_post_s=right)))
        pc = self.cfg.plot
        for fmt, c in self.chk_fmt.items():
            c.setChecked(fmt in pc.formats)
        self.spin_dpi.setValue(pc.dpi)
        self.cb_fig_lang.setCurrentIndex(0 if pc.language == "zh" else 1)
        self.chk_csv.setChecked(self.cfg.save_csv)
        ch = self.cfg.channels
        self.fs_edit.setValue(ch.sampling_rate)
        self.indicator_edit.setText(ch.indicator)
        self._sync_dff_source()
        self.marker_file.setText(ch.marker_file)
        self._sync_behavior()

    def _store_dedrift(self):
        if not self.current:
            self.form_dedrift.store(self.cfg)
            return
        base = self.cfg.dedrift
        edited = replace(self.cfg, dedrift=replace(base))
        self.form_dedrift.store(edited)
        values = edited.dedrift
        # Store coordinates in acquisition seconds so changing XML preserves the same recorded interval.
        coords = ("signal_start_s", "signal_end_s", "baseline_start_s", "baseline_end_s")
        if self.A is not None and self.A.abet is not None:
            prior = self.cfg.for_recording(self.current).dedrift
            for key in coords:
                value = getattr(values, key)
                if value is not None:
                    stored = getattr(prior, key)
                    if stored is None or abs(value - self.A.from_acquisition_time(stored)) > 0.00000051:
                        stored = float(self.A.to_acquisition_time(value))
                    setattr(values, key, stored)
        # The four clock coordinates belong to this file. Selecting a baseline
        # also overrides the global method and the Plexon R preference for it.
        local = set(coords)
        if "signal_interval" in (base.method, values.method):
            local.update(("method", "prefer_plexon_dff"))
        self.cfg.dedrift = replace(values, **{key: getattr(base, key) for key in local})
        overrides = {key: getattr(values, key) for key in local if getattr(values, key) != getattr(base, key)}
        source = str(self.current.resolve())
        if overrides:
            self.cfg.intervals_by_recording[source] = overrides
        else:
            self.cfg.intervals_by_recording.pop(source, None)

    def forms_store(self) -> PipelineConfig:
        self._store_dedrift()
        for f in (self.form_deconv, self.form_peth, self.form_events):
            f.store(self.cfg)
        self.cfg.peth.pre_s = self.cfg.peth.post_s = None
        ch = self.cfg.channels
        ch.group = self.cb_group.currentText()
        ch.indicator = self.indicator_edit.text().strip()
        excluded = ch.exclusions_for(self.current) if self.current else set()
        # Keep hidden role choices for other recordings and for restoring the rows.
        # read_recording applies this recording's exclusions before resolving roles.
        hidden = {c.name for c in self.channel_infos if c.group == ch.group and not c.is_available(excluded)}
        if ch.signal not in hidden or self.cb_signal.currentText():
            ch.signal = self.cb_signal.currentText()
        if ch.control not in hidden or self.cb_control.currentData():
            ch.control = self.cb_control.currentData() or ""
        ch.auto_control = False
        shown = {self.lst_markers.item(i).text() for i in range(self.lst_markers.count())}
        hidden_markers = {name for _, name in excluded} - shown
        ch.markers = ([self.lst_markers.item(i).text() for i in range(self.lst_markers.count())
                       if self.lst_markers.item(i).checkState() == Qt.Checked]
                      + [name for name in ch.markers if name in hidden_markers])
        if not ch.markers:
            ch.markers = ["__none__"]  # keep auto-detection from re-adding unticked channels
        ch.sampling_rate = self.fs_edit.value()
        ch.marker_file = self.marker_file.text()
        if self.A is not None:
            self._store_marker_choice()
        pc = self.cfg.plot
        pc.formats = [f for f, c in self.chk_fmt.items() if c.isChecked()] or ["png"]
        pc.dpi = self.spin_dpi.value()
        pc.language = self.cb_fig_lang.currentData()
        self.cfg.save_csv = self.chk_csv.isChecked()
        return self.cfg

    def load_params(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t("act_load_params"), "", self.t("json_filter"))
        if not path:
            return
        try:
            self.cfg = PipelineConfig.load(path)
        except Exception as e:
            self.error(str(e))
            return
        self.recording_states.clear()
        self.forms_load()
        if self.current:
            self._channels_changed()
        self.log(self.t("params_loaded", path=path))

    def save_params(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t("act_save_params"), "params.json", self.t("json_filter"))
        if not path:
            return
        self.forms_store().save(path)
        self.log(self.t("params_saved", path=path))

    def reset_params(self):
        self.recording_states.clear()
        self.cfg = PipelineConfig()
        self.forms_load()
        if self.current:
            self.on_file_selected(self.file_list.currentItem(), None)

    # ---------------------------------------------------------------- files
    def add_paths(self, paths):
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for p in paths:
            if Path(p).suffix.lower() == ".tdms" and is_event_file(p):
                continue  # marker recordings are paired with their main file automatically
            p = str(Path(p).resolve())
            if p in existing:
                continue
            it = QListWidgetItem(Path(p).name)
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.file_list.addItem(it)
            existing.add(p)
        if self.file_list.currentRow() < 0 and self.file_list.count():
            self.file_list.setCurrentRow(0)
        if not self.out_dir.text() and self.file_list.count():
            first = Path(self.file_list.item(0).data(Qt.UserRole))
            self.out_dir.setText(str(first.parent / "fiberphoto_results"))
        self._update_enabled()

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, self.t("act_add_files"),
                                                self.settings.value("last_dir", ""), self.t("tdms_filter"))
        if paths:
            self.settings.setValue("last_dir", str(Path(paths[0]).parent))
            self.add_paths(paths)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, self.t("choose_folder"), self.settings.value("last_dir", ""))
        if d:
            from ..pipeline import find_recordings
            self.settings.setValue("last_dir", d)
            self.add_paths(find_recordings(d))

    def load_sample(self):
        from ..sample_data import make_sample_dataset
        docs = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home())
        folder = Path(docs) / "fiberphoto_sample_data"
        paths = sorted(folder.glob("*.tdms"))
        if not paths:
            paths = make_sample_dataset(folder)
            self.log(self.t("sample_written", path=folder))
        self.add_paths(paths)

    def remove_file(self):
        row = self.file_list.currentRow()
        if row >= 0:
            removed = Path(self.file_list.item(row).data(Qt.UserRole))
            self.file_list.takeItem(row)
            self.recording_states.pop(removed, None)
        if self.file_list.count() == 0:
            self.current = None
            self.channel_infos = []
            self._reset_results()
            self._fill_channel_table()
            self.redraw_all()
        self._update_enabled()

    def on_file_selected(self, item, _prev):
        if self.current and _prev is not None:
            self._remember_recording()
        self.baseline_picker.bind(None)
        self.baseline_picker.previous_source = None
        self._reset_results()
        self.channel_infos, self.previews = [], {}
        self._loaded_once = False
        self.lbl_pre.clear()
        self.lbl_export.clear()
        self.progress.reset()
        self.current = Path(item.data(Qt.UserRole)) if item is not None else None
        self.abet_candidate = find_abet_sidecar(self.current) if self.current else None
        state = self.recording_states.pop(self.current, None)
        if state is not None:
            self._restore_recording(state)
        self._load_dedrift()
        if self.current is not None and state is None:
            try:
                if self.current.suffix.lower() == ".tdms":
                    self.channel_infos = inspect_tdms(self.current)
                    self.previews = channel_previews(self.current)
                else:
                    traces = device_traces(self.current)
                    self.channel_infos = [t.info() for t in traces]
                    self.previews = {(t.group, t.name): preview_trace(t.values) for t in traces}
            except Exception as e:
                self.error(f"{self.current.name}: {e}")
            self.marker_file.clear()
        self._fill_channel_table()
        self._apply_channel_cfg()
        self._show_auto_pair()
        self._check_fs()
        self._refresh_marker_choices()
        self._show_markers_found()
        self.redraw_all()
        self.tabs.setCurrentIndex(state.tab if state else 0)
        self.read_panels.select(1 if self.A is not None else 0)
        self._update_enabled()

    # -------------------------------------------------------- channel panel
    def _is_flat(self, c) -> bool:
        return self.previews.get((c.group, c.name), (None, False))[1]

    def _included_channels(self):
        if self.current is None:
            return []
        excluded = self.cfg.channels.exclusions_for(self.current)
        return [c for c in self.channel_infos if c.is_available(excluded)]

    def exclude_selected_channels(self):
        if self.current is None or self._busy:
            return
        rows = self.chan_table.selectionModel().selectedRows()
        if not rows:
            return
        self.forms_store()
        groups = self.cfg.channels.excluded_channels.setdefault(str(self.current.resolve()), {})
        for index in rows:
            group = self.chan_table.item(index.row(), 0).text()
            name = self.chan_table.item(index.row(), 1).text()
            names = groups.setdefault(group, [])
            if name not in names:
                names.append(name)
        self._channels_changed()

    def restore_channels(self):
        if self.current is not None and not self._busy:
            self.forms_store()
            self.cfg.channels.excluded_channels.pop(str(self.current.resolve()), None)
            self._channels_changed()

    def _channels_changed(self):
        self._reset_results()
        self._load_dedrift()
        self.lbl_pre.clear()
        self.lbl_export.clear()
        self._refresh_marker_choices()
        self._show_markers_found()
        self._fill_channel_table()
        self._apply_channel_cfg()
        self._check_fs()
        self.redraw_all()
        self._update_enabled()

    def _fill_channel_table(self):
        included = self._included_channels()
        self.chan_table.clearSelection()
        self.chan_table.setRowCount(len(included))
        self.b_restore.setEnabled(bool(self.current and self.cfg.channels.exclusions_for(self.current)))
        for r, c in enumerate(included):
            flat = self._is_flat(c)
            guess = self.t("flat") if flat else (self.t(ROLE_KEYS[c.guess]) if c.guess in ROLE_KEYS else "")
            for col, val in enumerate((c.group, c.name, f"{c.length:,}",
                                       f"{c.fs:.1f}" if c.fs else "-", guess)):
                it = QTableWidgetItem(val)
                if flat:
                    it.setForeground(QColor("#9a9993"))
                self.chan_table.setItem(r, col, it)
            trace, _ = self.previews.get((c.group, c.name), (np.array([]), True))
            it = QTableWidgetItem()
            it.setIcon(QIcon(sparkline(trace, color="#b5b4ae" if flat else "#2a78d6")))
            self.chan_table.setItem(r, 5, it)

    def _check_fs(self):
        """Ask for the sampling rate when the file does not store one."""
        included = self._included_channels()
        known = any(c.fs for c in included) or any(c.guess == "time" for c in included)
        missing = bool(included) and not known
        self.lbl_fs_missing.setVisible(missing and self.fs_edit.value() is None)
        if missing and self.fs_edit.value() is None:
            self.fs_edit.check.setChecked(True)
            self.fs_edit.spin.setValue(0)

    def _apply_channel_cfg(self):
        """Fill the mapping combos from saved settings, falling back to the guesses."""
        saved = self.cfg.channels
        base = type(saved)(**{**vars(saved), "group": "", "signal": "", "control": "", "time": "", "markers": [],
                             "auto_control": True})
        included = self._included_channels()
        guess = auto_map(included, base)
        groups = list(dict.fromkeys(c.group for c in included))
        self.cb_group.blockSignals(True)
        self.cb_group.clear()
        self.cb_group.addItems(groups)
        g = saved.group if saved.group in groups else guess.group
        self.cb_group.setCurrentText(g)
        self.cb_group.blockSignals(False)
        self._fill_channel_combos(prefer=saved, guess=guess)

    def _fill_channel_combos(self, *_a, prefer=None, guess=None):
        g = self.cb_group.currentText()
        included = self._included_channels()
        names = [c.name for c in included if c.group == g]
        vendor = self.current and self.current.suffix.lower() != ".tdms"
        all_names = list(dict.fromkeys(c.name for c in included if not vendor or (c.group == g and c.guess == "marker")))
        controls = names
        if vendor:
            local = [c for c in included if c.group == g]
            names = [c.name for c in local if c.guess not in ("marker", "baseline")]
            controls = [c.name for c in local if c.guess not in ("marker", "ratio")]
        if guess is None or guess.group != g:
            guess = auto_map(included, type(self.cfg.channels)(group=g))
        pick = lambda saved, fallback, pool: saved if saved and saved in pool else fallback
        flat = {c.name for c in included if (not vendor or c.group == g) and self._is_flat(c)}
        live = [n for n in names if n not in flat]
        sig_guess = guess.signal if guess.signal not in flat else (live[0] if live else guess.signal)
        sig = pick(prefer.signal if prefer else "", sig_guess, names)
        ctl = pick(prefer.control if prefer else "", guess.control if guess.control not in flat else "", controls)
        if prefer and prefer.group == g and not prefer.auto_control and not prefer.control:
            ctl = ""
        markers = [m for m in prefer.markers if m in all_names] if prefer else []
        if not markers and not (prefer and prefer.group == g and prefer.markers):
            markers = [m for m in guess.markers if m not in flat]

        self.cb_signal.clear()
        self.cb_signal.addItems(names)
        self.cb_signal.setCurrentText(sig)
        if not sig and self.current and self.cfg.channels.exclusions_for(self.current):
            self.cb_signal.setCurrentIndex(-1)
        if (self.current and self.current.suffix.lower() == ".doric" and len(names) > 1
                and not (prefer and prefer.group == g and prefer.signal in names)):
            self.cb_signal.setCurrentIndex(-1)
        self.cb_control.clear()
        self.cb_control.addItem(self.t("none"), "")
        for n in controls:
            self.cb_control.addItem(n, n)
        self.cb_control.setCurrentIndex(max(0, self.cb_control.findData(ctl)) if ctl else 0)
        self.lst_markers.clear()
        for n in all_names:
            if n in (sig, ctl):
                continue
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if n in markers else Qt.Unchecked)
            self.lst_markers.addItem(it)

    def _show_auto_pair(self):
        pair = find_event_file(self.current) if self.current and self.current.suffix.lower() == ".tdms" else None
        self.marker_file.setPlaceholderText(
            self.t("auto_paired", name=pair.name) if pair else (self.t("no_pair") if self.current else ""))

    def _sync_behavior(self):
        self._sync_abet()
        key = str(self.current.resolve()) if self.current else None
        spec = self.cfg.behavior_by_recording.get(key)
        zh = self.lang == "zh"
        self.b_behavior.setText(("行为 CSV 设置…" if zh else "Behavior CSV settings…") if spec else
                                ("导入行为 CSV…" if zh else "Import behavior CSV…"))
        self.b_behavior.setEnabled(key is not None and not self._busy)
        self.b_behavior_clear.setText("移除行为序列" if zh else "Remove behavior series")
        self.b_behavior_clear.setVisible(spec is not None)
        self.b_behavior_clear.setEnabled(not self._busy)
        if spec is None:
            self.lbl_behavior.clear()
            self.lbl_behavior.setToolTip("")
            return
        status = "读取钙记录后对齐" if zh else "Load the recording to align"
        if self.A is not None and self.A.behavior is not None:
            valid = self.A.behavior_info["valid_frames"]
            total = len(self.A.time)
            status = (f"覆盖 {valid:,}/{total:,} 帧" if zh else f"{valid:,}/{total:,} frames covered")
        self.lbl_behavior.setText(f"{Path(spec.path).name} · {spec.value_column} · {status}")
        self.lbl_behavior.setToolTip(spec.path)

    def _sync_abet(self):
        zh = self.lang == "zh"
        self.b_abet.setText("ABET 行为试次与重采样…" if zh else "ABET trials and resampling…")
        self.b_abet.setEnabled(not self._busy)
        self.b_abet_clear.setText("解除 ABET 关联" if zh else "Unlink ABET")
        key = str(self.current.resolve()) if self.current else None
        spec = self.cfg.abet_by_recording.get(key)
        path = None
        if spec is None:
            path = self.abet_candidate if self.current else None
        elif spec.mode != "disabled":
            path = Path(spec.path)
        self.b_abet_clear.setVisible(path is not None)
        self.b_abet_clear.setEnabled(not self._busy)
        self.lbl_abet.setToolTip(str(path) if path else "")
        result = self.A.abet if self.A is not None else None
        if result is not None:
            n = len(result.trials)
            count = result.info["aligned_samples"]
            status = (f"{n} 个试次 · 已重采样 {count:,} 帧" if zh else
                      f"{n} trials · {count:,} resampled frames")
        else:
            status = "读取后按行为时长重采样" if zh else "Resample to the behavior interval on load"
        self.lbl_abet.setText(f"{path.name} · {status}" if path else "")

    def pick_abet(self):
        if self._busy:
            return
        previous = self.cfg.abet_by_recording.get(str(self.current.resolve())) if self.current else None
        path = self.abet_candidate if self.current else None
        if previous and previous.mode != "disabled":
            path = Path(previous.path)
        if path is None or not path.is_file():
            folder = str(self.current.parent) if self.current else self.settings.value("last_dir", "")
            path, _ = QFileDialog.getOpenFileName(self, self.b_abet.text(), folder, "ABET XML (*.xml *.XML)")
        if not path:
            return
        previous = previous if previous and Path(previous.path) == Path(path) else None
        try:
            native = (self.A.abet_native or self.A) if self.A is not None else None
            dialog = AbetDialog(path, self.lang, self, previous, native.time if native is not None else None,
                                standalone=self.current is None)
        except (ValueError, OSError) as exc:
            self.error(str(exc))
            return
        try:
            if dialog.exec() == AbetDialog.Accepted and self.current is not None:
                self.set_abet(dialog.config(), session=dialog.session)
        finally:
            dialog.deleteLater()

    def remove_abet(self):
        self.set_abet(AbetConfig(mode="disabled"))

    def set_abet(self, spec, *, session=None):
        if self._busy or self.current is None:
            return
        key = str(self.current.resolve())
        raw = self.A
        self._store_marker_choice()

        if raw is not None and raw.abet is not None and session is not None:
            info = raw.abet.info
            if (info["source"] == str(Path(spec.path).resolve()) and info["source_sha256"] == session.sha256
                    and info["control_gap_s"] == spec.control_gap_s):
                # Inspecting the same XML and confirming unchanged settings preserves calculated results.
                self.cfg.abet_by_recording[key] = spec
                self._sync_abet()
                return
        if raw is not None:
            self._store_dedrift()

        def apply(aligned):
            self.cfg.abet_by_recording[key] = spec
            if aligned is not None:
                self.A = aligned
                self._refresh_marker_choices()
                self._store_marker_choice()
                # The source samples and clock can both change when linking a session.
                self.B = self.C = self.peth = self.events = self.stim_events = None
                self.lbl_pre.clear()
                self._load_dedrift()
            self.lbl_export.clear()
            self.progress.reset()
            self.redraw_all()
            self._update_clear_buttons()
            if self.lbl_abet.text():
                self.log(self.lbl_abet.text())

        if raw is None:
            apply(None)
        else:
            self._run(lambda: attach_abet(raw, None if spec.mode == "disabled" else spec, session=session), apply)

    def pick_behavior_csv(self):
        if self.current is None or self._busy:
            return
        previous = self.cfg.behavior_by_recording.get(str(self.current.resolve()))
        path = previous.path if previous and Path(previous.path).is_file() else None
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, self.b_behavior.text(), str(self.current.parent), "CSV (*.csv)")
        if not path:
            return
        try:
            dialog = BehaviorDialog(path, self.lang, self, previous=previous if previous and path == previous.path else None)
        except (OSError, ValueError) as exc:
            self.error(str(exc))
            return
        try:
            if dialog.exec() == BehaviorDialog.Accepted:
                self.set_behavior(dialog.config())
        finally:
            dialog.deleteLater()

    def set_behavior(self, spec):
        if self.current is None or self._busy:
            return
        key = str(self.current.resolve())
        raw = self.A

        def apply(aligned):
            if spec is None:
                self.cfg.behavior_by_recording.pop(key, None)
            else:
                self.cfg.behavior_by_recording[key] = spec
            self.A = aligned
            if self.B is not None:
                self.B.behavior = aligned.behavior_on(self.B.time)
                if self.C is not None:
                    self.C.behavior = self.B.behavior
            if aligned is None:
                self._sync_behavior()
            else:
                self.redraw_all()

        if raw is None or spec is None:
            apply(attach_behavior(raw, None) if raw is not None else None)
        else:
            self._run(lambda: attach_behavior(raw, spec), apply)

    def edit_manual_ttl(self, timestamp=None):
        """Append a clicked frame, or undo the latest click in this recording."""
        if self.A is None or self._busy:
            return
        key = str(self.current.resolve())
        points = self.cfg.manual_ttl_by_recording.get(key, [])
        if timestamp is None:
            if not points:
                return
            points = points[:-1]
        else:
            timestamp = float(self.A.to_acquisition_time(timestamp))
            if timestamp in points:
                return
            points = [*points, timestamp]
        old_label = self.A.manual_ttl_label
        self._store_marker_choice()
        used_manual = self.cfg.peth.include_manual_ttl or self.cfg.peth.marker == old_label
        self.A = self.A.with_manual_ttl(points)
        if points:
            self.cfg.manual_ttl_by_recording[key] = points
        else:
            self.cfg.manual_ttl_by_recording.pop(key, None)
        self._refresh_marker_choices()
        self._store_marker_choice()
        peth_changed = self.peth is not None and old_label in (self.peth.marker_sources or (self.peth.marker,))
        comparison_changed = self.stim_events is not None and old_label in self.stim_events.attrs.get(
            "marker_sources", (self.stim_events.attrs.get("marker"),))
        if peth_changed:
            self.peth = None
            self._fill_trial_table()
            for view in (self.fig_peth, self.fig_peth_dff, self.fig_auc):
                view.set_figure(None)
        if comparison_changed:
            self.stim_events = None
            self.fig_rate.set_figure(None)
        plotting.update_manual_ttl_overlay(self.fig_raw.canvas.figure.axes, self.A)
        self.fig_raw.canvas.draw_idle()
        overlays_changed = used_manual or self.cfg.peth.marker == self.A.manual_ttl_label
        if self.B is not None and overlays_changed:
            times = configured_markers(self.A, self.cfg.peth, processed=self.B).times
            for view, axis in ((self.fig_dedrift, 1), (self.fig_deconv, 0), (self.fig_events, 0)):
                if view.canvas is not None:
                    plotting.set_stimulus_lines(view.canvas.figure.axes[axis], times)
                    view.canvas.draw_idle()
        self._show_markers_found()
        self.baseline_picker.refresh()
        self._update_clear_buttons()

    def pick_marker_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t("lbl_marker_file"), "", self.t("table_filter"))
        if path:
            self.marker_file.setText(path)

    def _show_markers_found(self):
        if self.A is None:
            self.lbl_markers_found.setText("")
            return
        txt = ", ".join(f"{k} ({len(v)})" for k, v in self.A.markers.items()) or self.t("none")
        self.lbl_markers_found.setText(self.t("markers_found", txt=txt))

    # ---------------------------------------------------------------- steps
    def clear_page_results(self, page: str):
        if self._busy or self.current is None:
            return
        if page == "read":
            self.forms_store()
            self.A = None
            self._load_dedrift()
            self.baseline_picker.bind(None)
            self.fig_raw.set_figure(None)
            self._refresh_marker_choices()
            self._show_markers_found()
            self._sync_dff_source()
        if page in ("read", "pre"):
            self.B = self.C = None
            self.lbl_pre.clear()
            self.fig_dedrift.set_figure(None)
            self.fig_deconv.set_figure(None)
        if page in ("read", "pre", "peth"):
            self.peth = None
            self._fill_trial_table()
            for view in (self.fig_peth, self.fig_peth_dff, self.fig_auc):
                view.set_figure(None)
        if page in ("read", "pre", "events"):
            self.events = self.stim_events = None
            self._show_event_summary()
            for view in (self.fig_events, self.fig_dist, self.fig_rate):
                view.set_figure(None)
        if page == "export":
            self.lbl_export.clear()
            self.progress.reset()
        self._update_enabled()
        self.log(self.t("cleared_page", file=self.current.name, page=self.t(f"tab_{page}").replace("&&", "&")))

    def _run(self, fn, done, pass_callbacks=False):
        if self._busy:
            return
        self._set_busy(True)
        if pass_callbacks:
            self.progress.setValue(0)
        w = Worker(fn, pass_callbacks)
        w.setAutoDelete(False)
        self._workers.add(w)   # keep the runnable and its signals alive until it reports back

        def finish(out):
            self._workers.clear()
            self._set_busy(False)
            if self._closing:
                self.close()
            else:
                done(out)
                self._update_clear_buttons()

        def fail(msg):
            self._workers.clear()
            self._set_busy(False)
            if self._closing:
                self.close()
            else:
                self.error(msg)

        w.signals.finished.connect(finish, Qt.QueuedConnection)
        w.signals.failed.connect(fail, Qt.QueuedConnection)
        w.signals.log.connect(self.log, Qt.QueuedConnection)
        w.signals.progress.connect(lambda x: self.progress.setValue(int(x * 1000)), Qt.QueuedConnection)
        self.pool.start(w)

    def _need(self, *what) -> bool:
        checks = {"file": (self.current, "need_file"), "A": (self.A, "need_read"),
                  "B": (self.B, "need_pre"), "C": (self.C, "need_deconv")}
        for k in what:
            obj, msg = checks[k]
            if obj is None:
                QMessageBox.information(self, self.t("app_title"), self.t(msg))
                return False
        return True

    def step_read(self):
        if not self._need("file"):
            return
        cfg = self.forms_store()
        path = self.current
        if not self.lbl_fs_missing.isHidden() and cfg.channels.sampling_rate is None:
            QMessageBox.information(self, self.t("app_title"), self.t("need_fs"))
            self.read_panels.select(0)
            self.read_settings.setCurrentIndex(0)
            self.channel_panels.select(1)
            self.mapping_tabs.setCurrentIndex(0)
            self.fs_edit.spin.setFocus()
            return
        self.lbl_fs_missing.hide()

        def done(a):
            self._reset_results()
            self.A = a
            self._load_dedrift()
            self._loaded_once = True
            self.lbl_pre.clear()
            self.lbl_export.clear()
            self.progress.reset()
            self.log(plotting_tr("log_read", self.lang, name=path.name))
            self._refresh_marker_choices()
            self._show_markers_found()
            self.redraw_all()
            self.read_panels.select(1)

        self._run(lambda: read_with_behavior(path, cfg), done)

    def _choose_windows(self):
        self.tabs.setCurrentIndex(0)
        self.read_panels.select(0)
        self.read_settings.setCurrentIndex(0 if self.A is None else 2)
        target = self.b_read if self.A is None else self.baseline_picker.signal_button
        target.setFocus()
        self._reveal_read_shortcut()

    def _reveal_read_shortcut(self):
        # Compact tabs may change the scroll range after focus has moved.
        target = self.focusWidget()
        if target in (self.b_read, self.baseline_picker.signal_button) and target.isVisible():
            self.tabs.widget(0).ensureWidgetVisible(target)

    def _confirm_preprocessing(self, raw, cfg):
        names = raw.channel_names
        signal = names["signal"]
        preview = names.get("preview_signal") or signal
        control = names.get("control") or self.t("none")
        method = cfg.dedrift.method
        if method == "isosbestic" and (raw.control is None or names.get("control_role") == "baseline"):
            method = "biexponential"
        signal_start, signal_end = cfg.dedrift.signal_start_s, cfg.dedrift.signal_end_s
        window = self.t("whole_signal")
        if signal_start is not None or signal_end is not None:
            from ..preprocess import signal_interval_slice
            try:
                selected = signal_interval_slice(raw, signal_start, signal_end)
            except ValueError as exc:
                QMessageBox.information(self, self.t("pre_confirm_title"), str(exc))
                return False
            window = self.t("chosen_signal", start=signal_start, end=signal_end, n=raw.time[selected].size)
        baseline = self.t("no_fixed_baseline")
        if method == "signal_interval":
            start, end = cfg.dedrift.baseline_start_s, cfg.dedrift.baseline_end_s
            try:
                f0, n = signal_interval_baseline(raw, start, end)
            except ValueError as exc:
                QMessageBox.information(self, self.t("pre_confirm_title"), str(exc))
                return False
            baseline = self.t("pre_interval_source", column=signal, start=start, end=end, f0=f0, n=n)
            source = f"100 × ({signal} − F0) / F0"
            if raw.control is not None:
                control = self.t("pre_unused_control", column=control)
        elif cfg.dedrift.prefer_plexon_dff and raw.exported_dff is not None:
            source = self.t("pre_ratio_source", column=names["exported_dff"])
        else:
            source = self.t("pre_raw_source", column=signal, method=method)
        if names.get("control_role") == "baseline" and method != "signal_interval":
            control = self.t("pre_device_baseline", column=control)
        message = self.t("pre_confirm_data", file=raw.source, group=names.get("group") or self.t("none"),
                         preview=preview, control=control, signal=signal, source=source, window=window, baseline=baseline,
                         count=len(raw.time), fs=raw.fs, start=raw.time[0], end=raw.time[-1])
        return confirm_details(self, self.t("pre_confirm_title"), message)

    def step_pre(self):
        if not self._need("file", "A"):
            return
        cfg = self.forms_store().for_recording(self.current, raw=self.A)
        a = self.A
        if not self._confirm_preprocessing(a, cfg):
            return

        def work():
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", DataWarning)
                b = dedrift(a, cfg.dedrift)
            c = deconvolve(b, cfg.deconv) if cfg.deconv.enabled else None
            notes = [str(w.message) for w in caught if issubclass(w.category, DataWarning)]
            return b, c, notes

        def done(out):
            self.B, self.C, notes = out
            pick_lang = (lambda m: m.split(" / ")[0 if self.lang == "zh" else -1])
            for n in notes:
                self.log("⚠ " + pick_lang(n))
            self.peth = self.events = self.stim_events = None
            msg = plotting_tr("log_dedrift", self.lang, method=self.B.method)
            if self.C is not None:
                msg += " · " + plotting_tr("log_deconv", self.lang, tau=decay_time_constant(self.C))
            self.lbl_pre.setText("\n".join([msg] + ["⚠ " + pick_lang(n) for n in notes]))
            self.log(msg)
            self.log(format_dff_formula(a, self.B, self.lang))
            self.redraw_all()
            self.pre_panels.select(1)

        self._run(work, done)

    def step_peth(self):
        if not self._need("file", "A", "B"):
            return
        cfg = self.forms_store()
        selected = configured_markers(self.A, cfg.peth, processed=self.B)
        if not selected.label:
            QMessageBox.information(self, self.t("app_title"), self.t("no_markers"))
            return
        b = self.B

        def done(p):
            self.peth = p
            self.log(plotting_tr("log_peth", self.lang, marker=selected.label, n=len(p.trials)))
            if p.skipped_incomplete_pre:
                self.log(plotting_tr("log_nonfinite_pre", self.lang, n=p.skipped_incomplete_pre))
            self._fill_trial_table()
            self.redraw_all()
            self.peth_panels.select(1)

        self._run(lambda: selected.analyze(b, cfg.peth), done)

    def step_events(self):
        if not self._need("file", "A", "B", "C"):
            return
        cfg = self.forms_store()
        b, c, a = self.B, self.C, self.A
        selected = configured_markers(a, cfg.peth, processed=b)

        def work():
            ev = analysis.detect_events(c, b, cfg.events)
            sdf = (analysis.events_around_stimuli(ev, selected.times, cfg.events.compare_window_s, b.time[0], b.time[-1])
                   if selected.label else None)
            if sdf is not None:
                sdf.attrs["marker"] = selected.label
                sdf.attrs["marker_sources"] = selected.sources
            return ev, sdf

        def done(out):
            self.events, self.stim_events = out
            s = self.events.summary
            self.log(plotting_tr("log_events", self.lang, n=s["n_events"], rate=s["rate_per_min"]))
            self._show_event_summary()
            self.redraw_all()
            self.event_panels.select(1)

        self._run(work, done)

    def _store_marker_choice(self):
        combo = self.cb_marker_use
        if combo.currentIndex() >= 0:
            self.cfg.peth.marker = combo.currentData()
            self.cfg.peth.include_manual_ttl = bool(combo.currentData(Qt.UserRole + 1))

    def _refresh_marker_choices(self):
        combo = self.cb_marker_use
        cfg = self.cfg.peth
        combo.clear()
        if self.A is None:
            return
        manual = self.A.manual_ttl_label
        for label in self.A.markers:
            combo.addItem(label, label)
        if cfg.marker in self.A.markers:
            combo.setCurrentIndex(combo.findData(cfg.marker))
        if manual:
            suffix = "手动 TTL" if self.lang == "zh" else "manual TTL"
            for label in self.A.markers:
                if label == manual:
                    continue
                combo.addItem(f"{label} + {suffix}", label)
                index = combo.count() - 1
                combo.setItemData(index, True, Qt.UserRole + 1)
                if cfg.include_manual_ttl and cfg.marker == label:
                    combo.setCurrentIndex(index)

    def _draw_peth_comparisons(self):
        if self.peth is None or not len(self.peth.trials):
            self.fig_auc.set_figure(None)
            return
        kind = "z" if self.peth_views.currentIndex() == 0 else "dff"
        self._bind_figure_edits(self.fig_auc, f"paired_{kind}")
        fig = plotting.fig_peth_comparisons(self.peth, self.lang, kind=kind)
        plt.close(fig)
        self.fig_auc.set_figure(fig)

    def _fill_trial_table(self):
        m = self.peth.metrics if self.peth is not None else None
        if m is None or not len(m):
            self.trial_table.setRowCount(0)
            return
        cols = ["trial", "onset_s", "peak_latency_s", "peak_baseline_z", "peak_z", "auc_baseline_z", "auc_target_z",
                "peak_baseline_dff", "peak_dff", "auc_baseline_dff", "auc_target_dff"]
        heads = ["col_trial", "col_onset", "col_latency", "col_peak_baseline_z", "col_peak_z", "col_aucb_z", "col_auct_z",
                 "col_peak_baseline_dff", "col_peak_dff", "col_aucb_dff", "col_auct_dff"]
        headers = [self.t(h) for h in heads]
        if "marker_source" in m:
            cols.insert(2, "marker_source")
            headers.insert(2, "打标来源" if self.lang == "zh" else "Marker source")
        self.trial_table.setColumnCount(len(cols))
        self.trial_table.setHorizontalHeaderLabels(headers)
        self.trial_table.setRowCount(len(m))
        for r, row in enumerate(m.reindex(columns=cols).itertuples(index=False)):
            for c, v in enumerate(row):
                if cols[c] == "marker_source":
                    txt = {"file": "文件", "manual": "手动", "file+manual": "文件和手动"}.get(v, v) if self.lang == "zh" else v
                else:
                    txt = str(int(v)) if c == 0 else f"{v:.3g}"
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.trial_table.setItem(r, c, it)
        self.trial_table.resizeColumnsToContents()

    def _show_event_summary(self):
        unit = self.B.unit if self.B is not None else ""
        self.event_model.set_result(self.events.events if self.events is not None else None, unit, self.lang)
        self.b_export_events.setEnabled(self.events is not None and not self._busy)
        if self.events is None:
            self.lbl_events.setText("")
            return
        s = self.events.summary
        self.lbl_events.setText(self.t("event_summary", n=s["n_events"], rate=s["rate_per_min"],
                                       amp=s["mean_amplitude"] if s["n_events"] else 0.0, sigma=s["noise_sigma"]))

    def export_event_table(self):
        if self.events is None or self.B is None or self.current is None or self._busy:
            return
        name = self.current.with_name(f"{self.current.stem}_events.csv")
        path, _ = QFileDialog.getSaveFileName(self, self.t("export_event_table"), str(name), "CSV (*.csv)")
        if path:
            try:
                self.events.to_csv(path, self.B.unit)
            except OSError as exc:
                self.error(str(exc))
                return
            self.log(self.t("export_done", path=path))

    def export_plot_data(self, page):
        from ..plot_data import capture_views, export_plot_bundle

        if self._busy or self.A is None or self.B is None or self.current is None:
            return
        if page == "peth":
            if self.peth is None:
                return
            views = dict(peth_native=self.fig_peth_dff, peth_z=self.fig_peth, comparisons=self.fig_auc)
            results = dict(peth=self.peth, active_comparison="z" if self.peth_views.currentIndex() == 0 else "dff")
        else:
            if self.events is None or self.C is None:
                return
            views = dict(event_trace=self.fig_events, distributions=self.fig_dist, rates=self.fig_rate)
            overlays = []
            if self.fig_events.canvas is not None:
                for collection in self.fig_events.canvas.figure.axes[0].collections:
                    if collection.get_gid() == "stimulus-times":
                        overlays = [segment[0, 0] for segment in collection.get_segments()]
            results = dict(events=self.events, deconv=self.C, comparisons=self.stim_events, overlays=overlays)
        target = self.current.with_name(f"{self.current.stem}_{page}_plot_data.zip")
        path, _ = QFileDialog.getSaveFileName(self, self.t("export_plot_data"), str(target), "ZIP (*.zip)")
        if not path:
            return
        try:
            captured = capture_views(views)
        except (OSError, ValueError, RuntimeError) as exc:
            self.error(str(exc))
            return
        raw, processed = self.A, self.B
        lang = self.lang
        self._run(lambda: export_plot_bundle(path, raw, processed, captured, language=lang, **results),
                  lambda _: self.log(self.t("export_done", path=path)))

    # -------------------------------------------------------------- figures
    def _apply_peth_display(self):
        p = self.peth
        if self._busy or self.B is None or p is None or p.window_config is None:
            return
        pre = self.form_peth.editors["display_pre_s"].value()
        post = self.form_peth.editors["display_post_s"].value()
        if (-pre, post) == p.window_config.display_bounds_s():
            return
        try:
            self.peth = p.with_display_window(self.B, replace(p.window_config, display_pre_s=pre, display_post_s=post))
        except ValueError as exc:
            left, right = p.window_config.display_bounds_s()
            self.form_peth.editors["display_pre_s"].setValue(-left)
            self.form_peth.editors["display_post_s"].setValue(right)
            self.error(str(exc))
            return
        self.cfg.peth.display_pre_s, self.cfg.peth.display_post_s = pre, post
        self._draw_peth_traces()

    def _draw_peth_traces(self):
        for view, kind in ((self.fig_peth, "z"), (self.fig_peth_dff, "dff")):
            fig = None
            if self.peth is not None and len(self.peth.trials):
                fig = plotting.fig_peth(self.peth, self.lang, self.cfg.plot.colormap, kind=kind)
                plt.close(fig)
            view.set_figure(fig)

    def _bind_figure_edits(self, view, key):
        view.edit_language = self.lang
        edits = self.cfg.figure_edits_by_recording.setdefault(str(self.current.resolve()), {}) if self.current else {}
        view.figure_edits = edits.setdefault(key, {})

    def redraw_all(self):
        """Rebuild every figure from the current results, in the interface language."""
        self._sync_behavior()
        self._sync_dff_source()
        lang = self.lang
        plotting.setup_style(lang)
        cfg = self.cfg
        for view, key in ((self.fig_raw, "01_raw_dataA"), (self.fig_dedrift, "02_dedrift_dataB"),
                          (self.fig_deconv, "preview_deconvolution"), (self.fig_events, "03_deconv_events_dataC"),
                          (self.fig_peth, "04_peth_heatmap_z"), (self.fig_peth_dff, "04_peth_heatmap_dff"),
                          (self.fig_dist, "06_event_distributions"), (self.fig_rate, "07_event_rate_pre_post")):
            self._bind_figure_edits(view, key)
        # Full-trace overlays include stimuli even when a peri-event trial is dropped.
        onsets = (configured_markers(self.A, cfg.peth, processed=self.B).times
                  if self.A is not None else ())

        def put(view, make):
            fig = make() if make else None
            if fig is not None:
                plt.close(fig)  # detach from pyplot; the canvas keeps it alive
            view.set_figure(fig)

        put(self.fig_raw, (lambda: plotting.fig_raw(self.A, lang)) if self.A is not None else None)
        self.baseline_picker.bind(self.fig_raw.canvas)
        put(self.fig_dedrift, (lambda: plotting.fig_dedrift(self.B, onsets, lang)) if self.B is not None else None)
        if self.C is not None and self.B is not None:
            ev = self.events or analysis.EventResult(_empty_events(), {}, np.nan, np.nan)
            put(self.fig_deconv, lambda: _deconv_only(self.C, self.B, onsets, lang))
            put(self.fig_events, lambda: plotting.fig_deconv(self.C, self.B, ev, onsets, lang)
                if self.events is not None else None)
        else:
            put(self.fig_deconv, None)
            put(self.fig_events, None)
        self._draw_peth_traces()
        self._draw_peth_comparisons()
        put(self.fig_dist, (lambda: plotting.fig_event_distributions(self.events, lang, self.B.unit))
            if self.events is not None else None)
        ok_rate = self.stim_events is not None and len(self.stim_events)
        put(self.fig_rate, (lambda: plotting.fig_event_rate(self.stim_events, analysis.paired_test(
            self.stim_events["pre_rate_per_min"], self.stim_events["post_rate_per_min"]), lang)) if ok_rate else None)
        self._fill_trial_table()
        self._show_event_summary()
        self._show_markers_found()

    # --------------------------------------------------------------- export
    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, self.t("choose_folder"), self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    def pick_abet_manifest(self):
        path, _ = QFileDialog.getOpenFileName(self, self.lbl_manifest.text(), "", "CSV (*.csv)")
        if path:
            self.abet_manifest.setText(path)

    def save_abet_manifest_template(self):
        from ..abet_batch import write_abet_manifest_template
        path, _ = QFileDialog.getSaveFileName(self, self.b_manifest_template.text(), "abet_files.csv", "CSV (*.csv)")
        if path:
            recordings = [self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())]
            try:
                write_abet_manifest_template(path, recordings, self.cfg.abet_by_recording)
            except (OSError, ValueError) as e:
                self.error(str(e))

    def _out_root(self) -> Path | None:
        txt = self.out_dir.text().strip()
        if not txt:
            self.pick_out_dir()
            txt = self.out_dir.text().strip()
        if not txt:
            return None
        self.settings.setValue("out_dir", txt)
        return Path(txt)

    def export_current(self):
        if not self._need("file"):
            return
        out = self._out_root()
        if out is None:
            return
        cfg = PipelineConfig.from_dict(self.forms_store().to_dict())
        path = self.current

        def done(res):
            if res.error:
                self.error(res.error)
                return
            msg = self.t("export_done", path=res.out_dir)
            if "abet_mode" in res.summary:
                msg += "\n" + ("钙信号已按 ABET 时长重采样" if self.lang == "zh" else "Calcium resampled to the ABET interval")
            self.lbl_export.setText(msg)

        self._run(lambda log, progress: run_file(path, cfg, out, log, progress), done, pass_callbacks=True)

    def run_batch(self):
        n = self.file_list.count()
        if n == 0:
            QMessageBox.information(self, self.t("app_title"), self.t("need_file"))
            return
        out = self._out_root()
        if out is None:
            return
        cfg = PipelineConfig.from_dict(self.forms_store().to_dict())
        configured = {path: deepcopy(self._view_config(state.config)) for path, state in self.recording_states.items()
                      if state.loaded_once}
        if self.current is not None:
            configured[self.current] = cfg
        cfg = replace(cfg, channels=replace(cfg.channels, marker_file=""))  # auto-pair unvisited files
        paths = [self.file_list.item(i).data(Qt.UserRole) for i in range(n)]
        manifest = self.abet_manifest.text().strip() or None
        self._stop = False

        def done(results):
            ok = sum(1 for r in results if not r.error)
            msg = self.t("batch_done", ok=ok, bad=len(results) - ok, path=out)
            count = sum(bool(r.summary.get("abet_mode")) for r in results if not r.error)
            if count:
                msg += (f"\n{count} 份记录已按 ABET 时长重采样" if self.lang == "zh" else
                        f"\n{count} recordings resampled to their ABET intervals")
            self.lbl_export.setText(msg)
            self.log(msg)

        source = self.current
        self._run(lambda log, progress: run_batch(paths, cfg, out, log, progress, lambda: self._stop,
                                                  mapping_source=source, recording_configs=configured,
                                                  abet_manifest=manifest, retain_results=False),
                  done, pass_callbacks=True)

    def cancel(self):
        self._stop = True

    def open_out_dir(self):
        p = self.out_dir.text().strip()
        if p and Path(p).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    # ---------------------------------------------------------------- misc
    def _set_busy(self, busy: bool):
        self._busy = busy
        self.a_update.setEnabled(not busy)
        self.files_box.setEnabled(not busy)
        self.file_compact.setEnabled(not busy)
        self.chan_box.setEnabled(not busy)
        self.m_file.setEnabled(not busy)
        self.m_lang.setEnabled(not busy)
        self.lang_combo.setEnabled(not busy)
        self.statusBar().showMessage(self.t("busy") if busy else self.t("ready"))
        self._update_enabled()

    def _update_enabled(self):
        self._sync_behavior()
        has = self.file_list.count() > 0
        self.files_empty.setVisible(not has)
        for b in (self.b_read, self.b_pre, self.b_peth, self.b_events, self.b_export, self.b_batch):
            b.setEnabled(has and not self._busy)
        self.b_remove.setEnabled(has and not self._busy)
        self.a_compact_remove.setEnabled(has and not self._busy)
        self.b_manifest_template.setEnabled(has and not self._busy)
        self.b_export_events.setEnabled(self.events is not None and not self._busy)
        self.b_cancel.setEnabled(self._busy)
        self.baseline_picker.refresh()
        self._update_clear_buttons()

    def _update_clear_buttons(self):
        self.b_export_peth_plot_data.setEnabled(self.peth is not None and self.B is not None and not self._busy)
        self.b_export_event_plot_data.setEnabled(self.events is not None and self.C is not None and not self._busy)
        aligned = getattr(self.A, "abet", None)
        self.b_abet_compare.setEnabled(self.B is not None and aligned is not None and not self._busy)
        ready = {"read": self.A is not None, "pre": self.B is not None,
                 "peth": self.peth is not None, "events": self.events is not None,
                 "export": bool(self.lbl_export.text()) or self.progress.value() >= 0}
        for page, button in self.clear_buttons.items():
            button.setEnabled(ready[page] and not self._busy)

    def log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def error(self, msg: str):
        self.log(f"[{self.t('error')}] {msg.splitlines()[0]}")
        box = QMessageBox(QMessageBox.Warning, self.t("error"), msg.split("\n\n")[0], parent=self)
        if "\n\n" in msg:
            box.setDetailedText(msg.split("\n\n", 1)[1])
        box.exec()

    def show_guide(self):
        QMessageBox.information(self, self.t("act_guide"), self.t("guide_text"))

    def open_manual(self):
        pdf = manual_path(self.lang)
        if pdf is None:
            QMessageBox.warning(self, self.t("act_manual"), self.t("manual_missing"))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf)))

    def check_update(self):
        from fiberlume_boot import base_version
        from .. import updates

        base = base_version()
        zh = self.lang == "zh"
        title = self.a_update.text().rstrip("…")

        def run_request(action, success, operation):
            def attempt():
                try:
                    return action(), None
                except updates.UpdateSourceError as exc:
                    return None, str(exc)

            def finish(result):
                value, failure = result
                if failure is None:
                    success(value)
                    return
                hint = ("请检查网络或系统代理后重试。也可以向软件提供方索取离线更新包。"
                        if zh else "Check your network or system proxy and try again. "
                                   "You can also request an offline update from your software provider.")
                self.log(f"{operation} · {failure}")
                QMessageBox.warning(self, title, f"{operation}\n{failure}\n\n{hint}")

            self._run(attempt, finish)

        def checked(result):
            kind, manifest = result
            tag = manifest["version"]
            detail = f"FiberLume {__version__} → {tag}"
            if kind == "current":
                QMessageBox.information(self, title, f"FiberLume {__version__}\n" +
                                        ("已是当前版本。" if zh else "You are up to date."))
                return
            if kind == "installer":
                prompt = "需要安装新的基础版。打开正式下载页？" if zh else "A new base installer is required. Open the release page?"
                if QMessageBox.question(self, title, detail + "\n" + prompt) == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl(updates.RELEASES + manifest["base"]))
                return
            prompt = "下载并应用补丁？下次启动生效。" if zh else "Download and apply the patch on the next launch?"
            if QMessageBox.question(self, title, detail + "\n" + prompt) == QMessageBox.Yes:
                run_request(lambda: updates.stage(manifest, __version__, base), installed,
                            "下载补丁失败" if zh else "Patch download failed")

        def installed(tag):
            import os
            import subprocess
            import sys

            msg = f"{tag} 已准备好。立即重启？" if zh else f"{tag} is ready. Restart now?"
            if QMessageBox.question(self, title, msg) == QMessageBox.Yes:
                try:
                    subprocess.Popen([sys.executable], env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
                except OSError as exc:
                    self.error(str(exc))
                    return
                self.close()

        run_request(lambda: updates.check(__version__, base), checked,
                    "读取更新清单失败" if zh else "Could not read the update manifest")

    def show_about(self):
        QMessageBox.about(self, self.t("act_about"), self.t("about_text", ver=__version__))

    def closeEvent(self, e):
        if self._busy:
            self._closing = True
            self.cancel()
            e.ignore()
            return
        self.pool.waitForDone()
        self.recording_states.clear()
        self.baseline_picker.bind(None)
        self._reset_results()
        self.previews.clear()
        self.channel_infos.clear()
        for view in self.findChildren(FigureView):
            view.set_figure(None)
        self.settings.setValue("out_dir", self.out_dir.text())
        super().closeEvent(e)


def manual_path(lang: str) -> Path | None:
    """The bundled PDF manual in the interface language (falls back to the other language)."""
    folder = Path(__file__).resolve().parent / "manual"
    for name in (f"manual_{lang}.pdf", "manual_zh.pdf", "manual_en.pdf"):
        if (folder / name).exists():
            return folder / name
    return None


def plotting_tr(key, lang, **kw):
    from ..i18n import tr
    return tr(key, lang, **kw)


def _empty_events():
    import pandas as pd
    return pd.DataFrame(columns=["time_s", "amplitude", "prominence", "half_width_s", "iei_s"])


def _deconv_only(c, b, onsets, lang):
    """Deconvolution result before any events are detected."""
    from ..i18n import tr
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]}, layout="constrained")
    plotting.plot_trace(ax1, b.time, b.dff, color="#b5b4ae", lw=0.5, label=tr("dff", lang))
    plotting.plot_trace(ax1, c.time, c.denoised, color=plotting.C_DENOISED, lw=1.0, label=tr("denoised", lang))
    plotting.set_stimulus_lines(ax1, onsets)
    ax1.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    plotting.plot_trace(ax2, c.time, c.spikes, spikes=True, color=plotting.INK_2, lw=0.6)
    ax2.set_ylabel(tr("spikes", lang))
    ax2.set_xlabel(tr("time_s", lang))
    tau = decay_time_constant(c)
    ax1.set_title(f"{tr('deconv_title', lang)} · τ = {tau:.2f} s", loc="left")
    plotting._outside_legend(fig, (ax1, ax2))
    return fig
