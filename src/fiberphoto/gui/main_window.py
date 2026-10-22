"""Main window: file list on the left, one tab per analysis step, log at the bottom."""
from __future__ import annotations

import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # figures are drawn off-screen and embedded via FigureCanvasQTAgg
import matplotlib.pyplot as plt
import numpy as np
from PySide6.QtCore import QSettings, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import __version__, analysis, plotting
from ..config import PipelineConfig
from ..io_tdms import auto_map, find_event_file, inspect_tdms, is_event_file, read_tdms
from ..pipeline import pick_marker, run_batch, run_file
from ..preprocess import DataWarning, decay_time_constant, dedrift, deconvolve
from .texts import DECONV, DEDRIFT, EVENTS, PETH, ui
from .previews import channel_previews, sparkline
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
    sa.setMinimumWidth(340)
    return sa


class MainWindow(QMainWindow):
    def __init__(self, lang: str | None = None):
        super().__init__()
        self.settings = QSettings("fiberphoto", "fiberphoto")
        self.lang = lang or self.settings.value("lang", "zh")
        self.cfg = PipelineConfig()
        self.pool = QThreadPool.globalInstance()
        self._stop = False
        self._busy = False
        self._workers = set()
        self.current: Path | None = None
        self.channel_infos = []
        self.previews = {}
        self._reset_results()

        self._build_menu()
        self._build_files_panel()
        self._build_tabs()
        self._build_log()

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.files_box)
        split.addWidget(self.tabs)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 1160])
        self.setCentralWidget(split)
        self.resize(1400, 900)

        self.forms_load()
        self.retranslate()
        self._update_enabled()

    # ------------------------------------------------------------------ state
    def _reset_results(self):
        self.A = self.B = self.C = None
        self.peth = self.events = self.stim_events = None

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
        self.m_help.addAction(self.a_about)

        # language switch also lives in the corner for discoverability
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.currentIndexChanged.connect(lambda i: self.set_lang(self.lang_combo.itemData(i)))
        mb.setCornerWidget(self.lang_combo, Qt.TopRightCorner)

    def _build_files_panel(self):
        self.files_box = QGroupBox()
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

    def _help_label(self) -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        lab.setObjectName("help")
        return lab

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_read(), "")
        self.tabs.addTab(self._tab_pre(), "")
        self.tabs.addTab(self._tab_peth(), "")
        self.tabs.addTab(self._tab_events(), "")
        self.tabs.addTab(self._tab_export(), "")

    # tab ① ----------------------------------------------------------------
    def _tab_read(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_read = self._help_label()
        v.addWidget(self.help_read)

        top = QWidget()
        h = QHBoxLayout(top)
        h.setContentsMargins(0, 0, 0, 0)
        self.chan_box = QGroupBox()
        cl = QVBoxLayout(self.chan_box)
        self.chan_table = QTableWidget(0, 6)
        self.chan_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.chan_table.verticalHeader().setVisible(False)
        self.chan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.chan_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.chan_table.setIconSize(QSize(160, 30))
        self.chan_table.verticalHeader().setDefaultSectionSize(34)
        cl.addWidget(self.chan_table)
        h.addWidget(self.chan_box, 3)

        self.map_box = QGroupBox()
        f = QFormLayout(self.map_box)
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
        f.addRow(self.lbl_map[3], self.lst_markers)
        f.addRow(self.lbl_map[4], self.fs_edit)
        f.addRow(self.lbl_fs_missing)
        f.addRow(self.lbl_map[5], mf)
        self.b_read = _primary(QPushButton(clicked=self.step_read))
        f.addRow(self.b_read)
        self.lbl_markers_found = QLabel()
        self.lbl_markers_found.setWordWrap(True)
        f.addRow(self.lbl_markers_found)
        h.addWidget(self.map_box, 2)

        self.fig_raw = FigureView()
        split = QSplitter(Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self.fig_raw)
        split.setSizes([300, 500])
        v.addWidget(split, 1)
        return w

    # tab ② ----------------------------------------------------------------
    def _tab_pre(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_pre = self._help_label()
        v.addWidget(self.help_pre)
        left = QWidget()
        lv = QVBoxLayout(left)
        self.box_dedrift = QGroupBox()
        self.form_dedrift = ParamForm(DEDRIFT)
        QVBoxLayout(self.box_dedrift).addWidget(self.form_dedrift)
        self.box_deconv = QGroupBox()
        self.form_deconv = ParamForm(DECONV)
        QVBoxLayout(self.box_deconv).addWidget(self.form_deconv)
        self.b_pre = _primary(QPushButton(clicked=self.step_pre))
        self.lbl_pre = QLabel()
        self.lbl_pre.setWordWrap(True)
        lv.addWidget(self.box_dedrift)
        lv.addWidget(self.box_deconv)
        lv.addWidget(self.b_pre)
        lv.addWidget(self.lbl_pre)
        lv.addStretch(1)

        self.fig_dedrift = FigureView()
        self.fig_deconv = FigureView()
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.fig_dedrift)
        right.addWidget(self.fig_deconv)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(_scroll(left))
        split.addWidget(right)
        split.setSizes([380, 900])
        v.addWidget(split, 1)
        return w

    # tab ③ ----------------------------------------------------------------
    def _tab_peth(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_peth = self._help_label()
        v.addWidget(self.help_peth)
        left = QWidget()
        lv = QVBoxLayout(left)
        self.box_peth = QGroupBox()
        bl = QVBoxLayout(self.box_peth)
        mrow = QFormLayout()
        self.lbl_marker_use = QLabel()
        self.cb_marker_use = QComboBox()
        mrow.addRow(self.lbl_marker_use, self.cb_marker_use)
        bl.addLayout(mrow)
        self.form_peth = ParamForm(PETH)
        bl.addWidget(self.form_peth)
        self.b_peth = _primary(QPushButton(clicked=self.step_peth))
        lv.addWidget(self.box_peth)
        lv.addWidget(self.b_peth)
        self.box_trials = QGroupBox()
        tl = QVBoxLayout(self.box_trials)
        self.trial_table = QTableWidget(0, 0)
        self.trial_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.trial_table.verticalHeader().setVisible(False)
        tl.addWidget(self.trial_table)
        lv.addWidget(self.box_trials, 1)

        self.fig_peth = FigureView()
        self.fig_peth_dff = FigureView()
        self.fig_auc = FigureView()
        self.peth_views = QTabWidget()
        self.peth_views.addTab(self.fig_peth, "")
        self.peth_views.addTab(self.fig_peth_dff, "")
        right = QSplitter(Qt.Horizontal)
        right.addWidget(self.peth_views)
        right.addWidget(self.fig_auc)
        right.setSizes([560, 340])
        split = QSplitter(Qt.Horizontal)
        split.addWidget(_scroll(left))
        split.addWidget(right)
        split.setSizes([380, 900])
        v.addWidget(split, 1)
        return w

    # tab ④ ----------------------------------------------------------------
    def _tab_events(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_events = self._help_label()
        v.addWidget(self.help_events)
        left = QWidget()
        lv = QVBoxLayout(left)
        self.box_events = QGroupBox()
        self.form_events = ParamForm(EVENTS)
        QVBoxLayout(self.box_events).addWidget(self.form_events)
        self.b_events = _primary(QPushButton(clicked=self.step_events))
        self.lbl_events = QLabel()
        self.lbl_events.setWordWrap(True)
        lv.addWidget(self.box_events)
        lv.addWidget(self.b_events)
        lv.addWidget(self.lbl_events)
        lv.addStretch(1)

        self.fig_events = FigureView()
        self.fig_dist = FigureView()
        self.fig_rate = FigureView()
        bottom = QSplitter(Qt.Horizontal)
        bottom.addWidget(self.fig_dist)
        bottom.addWidget(self.fig_rate)
        bottom.setSizes([600, 300])
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.fig_events)
        right.addWidget(bottom)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(_scroll(left))
        split.addWidget(right)
        split.setSizes([380, 900])
        v.addWidget(split, 1)
        return w

    # tab ⑤ ----------------------------------------------------------------
    def _tab_export(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.help_export = self._help_label()
        v.addWidget(self.help_export)
        self.box_out = QGroupBox()
        f = QFormLayout(self.box_out)
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
        v.addWidget(self.box_out)

        brow = QHBoxLayout()
        self.b_export = _primary(QPushButton(clicked=self.export_current))
        self.b_batch = _primary(QPushButton(clicked=self.run_batch))
        self.b_cancel = QPushButton(clicked=self.cancel)
        self.b_open_out = QPushButton(clicked=self.open_out_dir)
        for b in (self.b_export, self.b_batch, self.b_cancel, self.b_open_out):
            brow.addWidget(b)
        brow.addStretch(1)
        v.addLayout(brow)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        v.addWidget(self.progress)
        self.lbl_export = QLabel()
        self.lbl_export.setWordWrap(True)
        self.lbl_export.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.lbl_export)
        v.addStretch(1)
        return w

    def _build_log(self):
        self.log_dock = QDockWidget(self)
        self.log_dock.setObjectName("log")
        self.log_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_dock.setWidget(self.log_view)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_view.setMinimumHeight(60)
        QTimer.singleShot(0, lambda: self.resizeDocks([self.log_dock], [110], Qt.Vertical))

    # ------------------------------------------------------------- language
    def set_lang(self, lang: str):
        if lang == self.lang and self.windowTitle():
            self._sync_lang_controls()
            return
        self.lang = lang
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
        self.setWindowTitle(f"{t('app_title')} · fiberphoto {__version__}")
        self._sync_lang_controls()
        self.m_file.setTitle(t("menu_file"))
        self.m_lang.setTitle(t("menu_lang"))
        self.m_help.setTitle(t("menu_help"))
        for a, k in ((self.a_add, "act_add_files"), (self.a_folder, "act_add_folder"),
                     (self.a_sample, "act_sample"), (self.a_load, "act_load_params"),
                     (self.a_save, "act_save_params"), (self.a_reset, "act_reset_params"),
                     (self.a_quit, "act_quit"), (self.a_manual, "act_manual"), (self.a_guide, "act_guide"), (self.a_about, "act_about")):
            a.setText(t(k))
        self.files_box.setTitle(t("files"))
        for b, k in ((self.b_add, "btn_add"), (self.b_folder, "btn_folder"),
                     (self.b_sample, "btn_sample"), (self.b_remove, "btn_remove")):
            b.setText(t(k))
        self.files_empty.setText(t("files_empty"))
        for i, k in enumerate(("tab_read", "tab_pre", "tab_peth", "tab_events", "tab_export")):
            self.tabs.setTabText(i, t(k))
        self.help_read.setText(t("help_read"))
        self.help_pre.setText(t("help_pre"))
        self.help_peth.setText(t("help_peth"))
        self.help_events.setText(t("help_events"))
        self.help_export.setText(t("help_export"))
        # tab 1
        self.chan_box.setTitle(t("channels"))
        self.chan_table.setHorizontalHeaderLabels([t(k) for k in ("col_group", "col_channel", "col_samples",
                                                                  "col_fs", "col_guess", "col_preview")])
        self.lbl_fs_missing.setText(t("fs_missing"))
        self._fill_channel_table()
        self.map_box.setTitle(t("mapping"))
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
        self.box_dedrift.setTitle(t("grp_dedrift"))
        self.box_deconv.setTitle(t("grp_deconv"))
        self.b_pre.setText(t("btn_pre"))
        for f in (self.form_dedrift, self.form_deconv, self.form_peth, self.form_events):
            f.set_lang(self.lang)
        # tab 3
        self.box_peth.setTitle(t("grp_peth"))
        self.lbl_marker_use.setText(t("lbl_marker_use"))
        self.b_peth.setText(t("btn_peth"))
        self.box_trials.setTitle(t("trial_table"))
        self.peth_views.setTabText(0, t("view_z"))
        self.peth_views.setTabText(1, t("view_dff"))
        # tab 4
        self.box_events.setTitle(t("grp_events"))
        self.b_events.setText(t("btn_events"))
        self._show_event_summary()
        # tab 5
        self.box_out.setTitle(t("grp_out"))
        for lab, k in zip(self.lbl_out, ("lbl_outdir", "lbl_formats", "lbl_dpi", "lbl_fig_lang", "lbl_csv")):
            lab.setText(t(k))
        self.b_out.setText(t("browse"))
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
    def forms_load(self):
        for f in (self.form_dedrift, self.form_deconv, self.form_peth, self.form_events):
            f.load(self.cfg)
        pc = self.cfg.plot
        for fmt, c in self.chk_fmt.items():
            c.setChecked(fmt in pc.formats)
        self.spin_dpi.setValue(pc.dpi)
        self.cb_fig_lang.setCurrentIndex(0 if pc.language == "zh" else 1)
        self.chk_csv.setChecked(self.cfg.save_csv)
        ch = self.cfg.channels
        self.fs_edit.setValue(ch.sampling_rate)
        self.marker_file.setText(ch.marker_file)

    def forms_store(self) -> PipelineConfig:
        for f in (self.form_dedrift, self.form_deconv, self.form_peth, self.form_events):
            f.store(self.cfg)
        ch = self.cfg.channels
        ch.group = self.cb_group.currentText()
        ch.signal = self.cb_signal.currentText()
        ch.control = self.cb_control.currentData() or ""
        ch.markers = [self.lst_markers.item(i).text() for i in range(self.lst_markers.count())
                      if self.lst_markers.item(i).checkState() == Qt.Checked]
        if not ch.markers:
            ch.markers = ["__none__"]  # keep auto-detection from re-adding unticked channels
        ch.sampling_rate = self.fs_edit.value()
        ch.marker_file = self.marker_file.text()
        self.cfg.peth.marker = self.cb_marker_use.currentText()
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
        self.forms_load()
        if self.current:
            self._apply_channel_cfg()
        self.log(self.t("params_loaded", path=path))

    def save_params(self):
        path, _ = QFileDialog.getSaveFileName(self, self.t("act_save_params"), "params.json", self.t("json_filter"))
        if not path:
            return
        self.forms_store().save(path)
        self.log(self.t("params_saved", path=path))

    def reset_params(self):
        self.cfg = PipelineConfig()
        self.forms_load()
        if self.current:
            self.on_file_selected(self.file_list.currentItem(), None)

    # ---------------------------------------------------------------- files
    def add_paths(self, paths):
        existing = {self.file_list.item(i).data(Qt.UserRole) for i in range(self.file_list.count())}
        for p in paths:
            if is_event_file(p):
                continue  # marker recordings are paired with their main file automatically
            p = str(Path(p).resolve())
            if p in existing:
                continue
            it = QListWidgetItem(Path(p).name)
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.file_list.addItem(it)
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
            self.file_list.takeItem(row)
        if self.file_list.count() == 0:
            self.current = None
            self.channel_infos = []
            self._reset_results()
            self._fill_channel_table()
            self.redraw_all()
        self._update_enabled()

    def on_file_selected(self, item, _prev):
        self._reset_results()
        self.redraw_all()
        if item is None:
            self.current = None
            self._update_enabled()
            return
        self.current = Path(item.data(Qt.UserRole))
        try:
            self.channel_infos = inspect_tdms(self.current)
            self.previews = channel_previews(self.current)
        except Exception as e:
            self.channel_infos = []
            self.previews = {}
            self.error(f"{self.current.name}: {e}")
        self._fill_channel_table()
        self._apply_channel_cfg()
        self.marker_file.clear()  # an explicit marker file belongs to one recording only
        self._show_auto_pair()
        self._check_fs()
        self.tabs.setCurrentIndex(0)
        self._update_enabled()

    # -------------------------------------------------------- channel panel
    def _is_flat(self, c) -> bool:
        return self.previews.get((c.group, c.name), (None, False))[1]

    def _fill_channel_table(self):
        self.chan_table.setRowCount(len(self.channel_infos))
        for r, c in enumerate(self.channel_infos):
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
        known = any(c.fs for c in self.channel_infos) or any(c.guess == "time" for c in self.channel_infos)
        missing = bool(self.channel_infos) and not known
        self.lbl_fs_missing.setVisible(missing and self.fs_edit.value() is None)
        if missing and self.fs_edit.value() is None:
            self.fs_edit.check.setChecked(True)
            self.fs_edit.spin.setValue(0)

    def _apply_channel_cfg(self):
        """Fill the mapping combos from saved settings, falling back to the guesses."""
        saved = self.cfg.channels
        base = type(saved)(**{**vars(saved), "group": "", "signal": "", "control": "", "time": "", "markers": []})
        guess = auto_map(self.channel_infos, base)
        groups = sorted({c.group for c in self.channel_infos}, key=[c.group for c in self.channel_infos].index)
        self.cb_group.blockSignals(True)
        self.cb_group.clear()
        self.cb_group.addItems(groups)
        g = saved.group if saved.group in groups else guess.group
        self.cb_group.setCurrentText(g)
        self.cb_group.blockSignals(False)
        self._fill_channel_combos(prefer=saved, guess=guess)

    def _fill_channel_combos(self, *_a, prefer=None, guess=None):
        g = self.cb_group.currentText()
        names = [c.name for c in self.channel_infos if c.group == g]
        all_names = [c.name for c in self.channel_infos]
        if guess is None:
            guess = auto_map(self.channel_infos, type(self.cfg.channels)(group=g))
        pick = lambda saved, fallback, pool: saved if saved and saved in pool else fallback
        flat = {c.name for c in self.channel_infos if self._is_flat(c)}
        live = [n for n in names if n not in flat]
        sig_guess = guess.signal if guess.signal not in flat else (live[0] if live else guess.signal)
        sig = pick(prefer.signal if prefer else "", sig_guess, names)
        ctl = pick(prefer.control if prefer else "", guess.control if guess.control not in flat else "", names)
        markers = [m for m in (prefer.markers if prefer else []) if m in all_names] or \
            [m for m in guess.markers if m not in flat]

        self.cb_signal.clear()
        self.cb_signal.addItems(names)
        self.cb_signal.setCurrentText(sig)
        self.cb_control.clear()
        self.cb_control.addItem(self.t("none"), "")
        for n in names:
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
        pair = find_event_file(self.current) if self.current else None
        self.marker_file.setPlaceholderText(
            self.t("auto_paired", name=pair.name) if pair else (self.t("no_pair") if self.current else ""))

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
    def _run(self, fn, done, pass_callbacks=False):
        if self._busy:
            return
        self._set_busy(True)
        w = Worker(fn, pass_callbacks)
        w.setAutoDelete(False)
        self._workers.add(w)   # keep the runnable and its signals alive until it reports back

        def finish(out):
            self._workers.discard(w)
            self._set_busy(False)
            done(out)

        def fail(msg):
            self._workers.discard(w)
            self._set_busy(False)
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
            self.fs_edit.spin.setFocus()
            return
        self.lbl_fs_missing.hide()

        def done(a):
            self._reset_results()
            self.A = a
            self.log(plotting_tr("log_read", self.lang, name=path.name))
            self._refresh_marker_choices()
            self._show_markers_found()
            self.redraw_all()

        self._run(lambda: read_tdms(path, cfg.channels), done)

    def step_pre(self):
        if not self._need("file", "A"):
            return
        cfg = self.forms_store()
        a = self.A

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
            self.redraw_all()

        self._run(work, done)

    def step_peth(self):
        if not self._need("file", "A", "B"):
            return
        cfg = self.forms_store()
        marker, onsets = pick_marker(self.A, cfg.peth.marker, cfg.peth.align_to, cfg.peth.shift_s)
        if not marker:
            QMessageBox.information(self, self.t("app_title"), self.t("no_markers"))
            return
        b = self.B

        def done(p):
            self.peth = p
            self.log(plotting_tr("log_peth", self.lang, marker=marker, n=len(p.trials)))
            self._fill_trial_table()
            self.redraw_all()

        self._run(lambda: analysis.peri_event(b, onsets, cfg.peth, marker), done)

    def step_events(self):
        if not self._need("file", "A", "B", "C"):
            return
        cfg = self.forms_store()
        b, c, a = self.B, self.C, self.A
        marker, onsets = pick_marker(a, cfg.peth.marker, cfg.peth.align_to, cfg.peth.shift_s)

        def work():
            ev = analysis.detect_events(c, b, cfg.events)
            sdf = (analysis.events_around_stimuli(ev, onsets, cfg.events.compare_window_s, b.time[0], b.time[-1])
                   if marker else None)
            return ev, sdf

        def done(out):
            self.events, self.stim_events = out
            s = self.events.summary
            self.log(plotting_tr("log_events", self.lang, n=s["n_events"], rate=s["rate_per_min"]))
            self._show_event_summary()
            self.redraw_all()

        self._run(work, done)

    def _refresh_marker_choices(self):
        cur = self.cfg.peth.marker
        self.cb_marker_use.clear()
        if self.A is not None:
            self.cb_marker_use.addItems(list(self.A.markers))
            if cur in self.A.markers:
                self.cb_marker_use.setCurrentText(cur)

    def _fill_trial_table(self):
        m = self.peth.metrics if self.peth is not None else None
        if m is None or not len(m):
            self.trial_table.setRowCount(0)
            return
        cols = ["trial", "onset_s", "peak_latency_s", "peak_z", "auc_baseline_z", "auc_target_z",
                "peak_dff", "auc_baseline_dff", "auc_target_dff"]
        heads = ["col_trial", "col_onset", "col_latency", "col_peak_z", "col_aucb_z", "col_auct_z",
                 "col_peak_dff", "col_aucb_dff", "col_auct_dff"]
        self.trial_table.setColumnCount(len(cols))
        self.trial_table.setHorizontalHeaderLabels([self.t(h) for h in heads])
        self.trial_table.setRowCount(len(m))
        for r, row in enumerate(m[cols].itertuples(index=False)):
            for c, v in enumerate(row):
                txt = str(int(v)) if c == 0 else f"{v:.3g}"
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.trial_table.setItem(r, c, it)
        self.trial_table.resizeColumnsToContents()

    def _show_event_summary(self):
        if self.events is None:
            self.lbl_events.setText("")
            return
        s = self.events.summary
        self.lbl_events.setText(self.t("event_summary", n=s["n_events"], rate=s["rate_per_min"],
                                       amp=s["mean_amplitude"] if s["n_events"] else 0.0, sigma=s["noise_sigma"]))

    # -------------------------------------------------------------- figures
    def redraw_all(self):
        """Rebuild every figure from the current results, in the interface language."""
        lang = self.lang
        plotting.setup_style(lang)
        cfg = self.cfg
        onsets = self.peth.onsets if self.peth is not None else (
            pick_marker(self.A, cfg.peth.marker, cfg.peth.align_to, cfg.peth.shift_s)[1] if self.A is not None else ())

        def put(view, make):
            fig = make() if make else None
            if fig is not None:
                plt.close(fig)  # detach from pyplot; the canvas keeps it alive
                fig.set_layout_engine("tight")  # re-run the layout whenever the panel is resized
            view.set_figure(fig)

        put(self.fig_raw, (lambda: plotting.fig_raw(self.A, lang)) if self.A is not None else None)
        put(self.fig_dedrift, (lambda: plotting.fig_dedrift(self.B, onsets, lang)) if self.B is not None else None)
        if self.C is not None and self.B is not None:
            ev = self.events or analysis.EventResult(_empty_events(), {}, np.nan, np.nan)
            put(self.fig_deconv, lambda: _deconv_only(self.C, self.B, onsets, lang))
            put(self.fig_events, lambda: plotting.fig_deconv(self.C, self.B, ev, onsets, lang)
                if self.events is not None else None)
        else:
            put(self.fig_deconv, None)
            put(self.fig_events, None)
        ok_peth = self.peth is not None and len(self.peth.trials)
        put(self.fig_peth, (lambda: plotting.fig_peth(self.peth, lang, cfg.plot.colormap, cfg.peth, "z"))
            if ok_peth else None)
        put(self.fig_peth_dff, (lambda: plotting.fig_peth(self.peth, lang, cfg.plot.colormap, cfg.peth, "dff"))
            if ok_peth else None)
        put(self.fig_auc, (lambda: plotting.fig_auc(self.peth, lang, vertical=True)) if ok_peth else None)
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
            self.lbl_export.setText(self.t("export_done", path=res.out_dir))

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
        cfg.channels.marker_file = ""  # each recording uses its own paired marker file
        paths = [self.file_list.item(i).data(Qt.UserRole) for i in range(n)]
        self._stop = False

        def done(results):
            ok = sum(1 for r in results if not r.error)
            msg = self.t("batch_done", ok=ok, bad=len(results) - ok, path=out)
            self.lbl_export.setText(msg)
            self.log(msg)

        self._run(lambda log, progress: run_batch(paths, cfg, out, log, progress, lambda: self._stop),
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
        self.statusBar().showMessage(self.t("busy") if busy else self.t("ready"))
        if busy:
            self.progress.setValue(0)
        self._update_enabled()

    def _update_enabled(self):
        has = self.file_list.count() > 0
        self.files_empty.setVisible(not has)
        for b in (self.b_read, self.b_pre, self.b_peth, self.b_events, self.b_export, self.b_batch):
            b.setEnabled(has and not self._busy)
        self.b_remove.setEnabled(has and not self._busy)
        self.b_cancel.setEnabled(self._busy)

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

    def show_about(self):
        QMessageBox.about(self, self.t("act_about"), self.t("about_text", ver=__version__))

    def closeEvent(self, e):
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
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(b.time, b.dff, color="#b5b4ae", lw=0.5, label=tr("dff", lang))
    ax1.plot(c.time, c.denoised, color=plotting.C_DENOISED, lw=1.0, label=tr("denoised", lang))
    for t0 in onsets:
        ax1.axvline(t0, color=plotting.C_STIM, lw=0.8, alpha=0.5)
    ax1.set_ylabel(f"{tr('dff', lang)} ({b.unit})")
    ax1.legend(loc="upper right", ncol=2)
    ax2.vlines(c.time, 0, c.spikes, color=plotting.INK_2, lw=0.6)
    ax2.set_ylabel(tr("spikes", lang))
    ax2.set_xlabel(tr("time_s", lang))
    tau = decay_time_constant(c)
    ax1.set_title(f"{tr('deconv_title', lang)} · τ = {tau:.2f} s", loc="left")
    fig.tight_layout()
    return fig
