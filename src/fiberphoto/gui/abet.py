"""Inspect an ABET session and declare how its clock relates to this recording."""
from pathlib import Path

import numpy as np
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableView, QVBoxLayout)

from ..abet import AbetConfig, align_abet, read_abet


MODES = {
    "behavior_only": ("仅行为（保留 ABET 时间）", "Behavior only (ABET time)"),
    "assumed_trigger": ("估算：开始脉冲触发钙记录", "Estimated: start pulse triggers recording"),
    "offset": ("实测一个同步点（仅校正偏移）", "One measured anchor (offset only)"),
    "affine": ("实测两个同步点（校正偏移与漂移）", "Two measured anchors (offset and drift)"),
}


def mode_label(mode, lang):
    return MODES.get(mode, (mode, mode))[0 if lang == "zh" else 1]


class TrialModel(QAbstractTableModel):
    columns = {
        "trial": ("试次", "Trial"), "regular": ("常规时序", "Standard timing"),
        "outcome": ("试次结果", "Outcome"), "cue_on_s": ("声音开始", "Cue onset"),
        "traylight_on_s": ("食槽灯亮", "Tray light"), "response_window_open_s": ("响应窗开始", "Window opens"),
        "tray_input_on_s": ("有效进槽", "Valid tray entry"), "feeder_command_s": ("给食器指令", "Feeder command"),
        "tray_latency_from_window_open_s": ("进槽潜伏期", "Entry latency"),
    }
    outcomes = {"responded": "有效响应", "timeout": "超时", "incomplete": "不完整"}

    def __init__(self, lang, parent):
        super().__init__(parent)
        self.lang = lang
        self.frame = pd.DataFrame()

    def set_frame(self, frame):
        self.beginResetModel()
        self.frame = frame.reindex(columns=self.columns)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            col = list(self.columns)[section]
            return self.columns[col][self.lang != "zh"]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return
        col = list(self.columns)[index.column()]
        value = self.frame.iat[index.row(), index.column()]
        if pd.isna(value):
            return "—"
        if col.endswith("_s"):
            return f"{value:.3f}"
        if col == "regular":
            return ("是" if value else "否") if self.lang == "zh" else ("Yes" if value else "No")
        if col == "outcome" and self.lang == "zh":
            return self.outcomes.get(value, value)
        return str(value)


class AbetDialog(QDialog):
    def __init__(self, path, lang, parent=None, previous=None, calcium_time=None, standalone=False):
        super().__init__(parent)
        self.lang = lang
        self.calcium_time = calcium_time
        self.standalone = standalone
        self.setWindowTitle(self.tr2("ABET 行为试次与时钟对齐", "ABET trials and clock alignment"))
        self.resize(850, 610)
        layout = QVBoxLayout(self)
        file_row = QHBoxLayout()
        self.filename = self.label()
        file_row.addWidget(self.filename, 1)
        file_row.addWidget(QPushButton(self.tr2("更换 XML…", "Change XML…"), clicked=self.change_file))
        layout.addLayout(file_row)
        self.summary = self.label()
        layout.addWidget(self.summary)
        self.model = TrialModel(lang, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.span = self.label()
        layout.addWidget(self.span)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.mode = QComboBox()
        for key in MODES:
            self.mode.addItem(mode_label(key, lang), key)
        self.form.addRow(self.tr2("时钟对齐方式", "Clock alignment"), self.mode)
        self.latency = self.seconds()
        self.form.addRow(self.tr2("估计触发至首帧延迟", "Estimated trigger-to-first-frame delay"), self.latency)
        self.anchors = []
        self.anchor_rows = []
        for number in (1, 2):
            behavior, recording = QLineEdit(), QLineEdit()
            behavior.setPlaceholderText(self.tr2("ABET 秒", "ABET seconds"))
            recording.setPlaceholderText(self.tr2("钙记录秒", "Recording seconds"))
            row = QHBoxLayout()
            row.addWidget(behavior)
            row.addWidget(QLabel("→"))
            row.addWidget(recording)
            self.form.addRow(self.tr2(f"实测同步点 {number}", f"Measured anchor {number}"), row)
            self.anchors.append((behavior, recording))
            self.anchor_rows.append(row)
            behavior.textChanged.connect(self.validate)
            recording.textChanged.connect(self.validate)
        self.gap = self.seconds()
        self.form.addRow(self.tr2("对照进槽隔离间隔", "Control tray-entry isolation"), self.gap)
        self.help = self.label()
        layout.addWidget(self.help)
        self.error_text = self.label()
        self.error_text.setStyleSheet("color: #ad480c;")
        layout.addWidget(self.error_text)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(self.tr2("关闭", "Close") if standalone else
                                                         self.tr2("确认设置", "Apply settings"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.tr2("取消", "Cancel"))
        self.buttons.button(QDialogButtonBox.Cancel).setVisible(not standalone)
        export_btn = self.buttons.addButton(self.tr2("导出行为试次…", "Export behavior trials…"), QDialogButtonBox.ActionRole)
        export_btn.clicked.connect(self.export_native)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self.validate)
        self.mode.setEnabled(not standalone)
        if calcium_time is None:
            for index in range(1, self.mode.count()):
                self.mode.model().item(index).setEnabled(False)
        self.load_file(path, previous)

    def tr2(self, zh, en):
        return zh if self.lang == "zh" else en

    def label(self):
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        return label

    def seconds(self):
        spin = QDoubleSpinBox()
        spin.setDecimals(4)
        spin.setRange(0.0, 1e6)
        spin.setSuffix(" s")
        return spin

    def change_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.windowTitle(), str(self.path.parent), "ABET XML (*.xml *.XML)")
        if path:
            try:
                self.load_file(path)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc))

    def load_file(self, path, previous=None):
        session = read_abet(path)
        self.session = session
        self.path = Path(path).resolve()
        self.filename.setText(str(self.path))
        cfg = previous or AbetConfig(path=str(self.path))
        t = session.trials
        self.model.set_frame(t)
        self.table.resizeColumnsToContents()
        outcomes = t["outcome"].value_counts()
        counts = (len(t), int(t.regular.sum()), int(outcomes.get("responded", 0)),
                  int(outcomes.get("timeout", 0)), int(outcomes.get("incomplete", 0)))
        self.summary.setText(f"{session.animal_id} · {session.schedule}\n" +
                             self.tr2("共 {} 试次（{} 次常规时序）。全程 {} 次有效进槽、{} 次超时、{} 次不完整。表中时间为 ABET 秒。",
                                      "{} trials ({} with standard timing). {} valid responses, {} timeouts, {} incomplete. Table times are ABET seconds.").format(*counts))
        pulse = session.sync_pulses
        span = self.tr2(f"行为日志 {session.duration_s:g} s", f"Behavior log {session.duration_s:g} s")
        if pulse:
            span += self.tr2("；start #1 脉冲（秒）", " · start #1 pulses (s) ") + ", ".join(f"{p:.9g}" for p in pulse)
        if self.calcium_time is not None:
            length = float(self.calcium_time[-1] - self.calcium_time[0])
            span += self.tr2(f"；钙记录跨度 {length:.3f} s", f" · recording span {length:.3f} s")
            if len(pulse) >= 2:
                pulse_span = pulse[-1] - pulse[0]
                span += self.tr2(f"；首末脉冲间隔 {pulse_span:.3f} s；跨度差 {length-pulse_span:+.3f} s",
                                 f" · pulse span {pulse_span:.3f} s · difference {length-pulse_span:+.3f} s")
        self.span.setText(span)
        self.gap.setValue(cfg.control_gap_s)
        self.latency.setValue(cfg.latency_s)
        for i, (a, b) in enumerate(self.anchors):
            if i < len(cfg.anchors):
                x, y = cfg.anchors[i]
                a.setText(str(x))
                b.setText(str(y))
            else:
                x = pulse[0] if i == 0 and pulse else (pulse[-1] if len(pulse) >= 2 else None)
                a.setText("" if x is None else str(x))
                b.clear()
        self.mode.setCurrentIndex(max(0, self.mode.findData(cfg.mode)))
        self.validate()

    def config(self):
        mode = self.mode.currentData()
        n = {"offset": 1, "affine": 2}.get(mode, 0)
        anchors = [[float(a.text().strip()), float(b.text().strip())] for a, b in self.anchors[:n]]
        return AbetConfig(path=str(self.path), mode=mode, anchors=anchors,
                          latency_s=self.latency.value() if mode == "assumed_trigger" else 0.,
                          control_gap_s=self.gap.value())

    def validate(self):
        if not hasattr(self, "session"):
            return
        mode = self.mode.currentData()
        self.form.setRowVisible(self.latency, mode == "assumed_trigger")
        for i, row in enumerate(self.anchor_rows):
            self.form.setRowVisible(row, mode == "affine" or (mode == "offset" and i == 0))
        notes = {
            "behavior_only": ("保留试次和实验起止脉冲的行为时间。读取钙记录后可设置对齐方式。",
                              "Keep trial and session-pulse times on the behavior clock. Load the recording to set alignment."),
            "assumed_trigger": ("近似对齐。按首个 start #1 脉冲触发采集，将其映射到首帧时间减估计延迟。延迟未知时可填 0。首末跨度差不能确定本次启动延迟。",
                                "Estimated alignment. Map the first start pulse to first-frame time minus the assumed delay. Use 0 for unknown delay. The span difference cannot determine startup latency."),
            "offset": ("输入两端同一物理事件的时间。单点只能校正偏移，时钟漂移未知。",
                       "Enter the same physical event on both clocks. One anchor corrects offset only. Drift is unknown."),
            "affine": ("按先后顺序输入在两台设备上均有记录的两个事件。使用实测钙记录时间。",
                       "Enter two matching physical events in time order, using their measured recording times."),
        }
        note = self.tr2(*notes[mode])
        if self.session.warnings:
            note += "\n" + "\n".join(self.session.warnings)
        self.help.setText(note)
        error = ""
        if mode != "behavior_only" and self.calcium_time is None:
            error = self.tr2("请先读取钙记录以核对同步设置。", "Load the recording to check alignment settings.")
        elif mode == "assumed_trigger" and not self.session.sync_pulses:
            error = self.tr2("日志没有 start #1 脉冲。", "No start #1 pulse in this log.")
        elif mode in {"affine", "offset"}:
            try:
                anchors = np.asarray(self.config().anchors)
                valid = (np.isfinite(anchors).all() and (np.diff(anchors, axis=0) > 0).all()
                         and ((0 <= anchors[:, 0]) & (anchors[:, 0] <= self.session.duration_s)).all()
                         and ((self.calcium_time[0] <= anchors[:, 1]) & (anchors[:, 1] <= self.calcium_time[-1])).all())
            except ValueError:
                valid = False
            if not valid:
                error = self.tr2("请为每个同步点填写有效时间（秒）。两端时间须在各自记录范围内并依次递增。",
                                 "Enter measured seconds for each anchor, within each recording and in increasing order.")
        self.error_text.setText(error)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not error)

    def accept(self):
        if self.calcium_time is not None and self.mode.currentData() != "behavior_only":
            try:
                align_abet(self.session, self.config(), self.calcium_time)
            except (ValueError, OSError) as exc:
                self.error_text.setText(str(exc))
                return
        super().accept()

    def export_native(self):
        path, _ = QFileDialog.getSaveFileName(self, self.windowTitle(), str(self.path.with_name(self.path.stem + "_abet_trials.csv")), "CSV (*.csv)")
        if path:
            try:
                self.session.trials.to_csv(path, index=False, encoding="utf-8-sig")
            except OSError as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc))
