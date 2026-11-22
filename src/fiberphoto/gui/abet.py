"""Inspect ABET trials and the calcium resampling interval."""
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QTableView)

from ..abet import AbetConfig, align_abet, read_abet
from .dialog_layout import dialog_layout


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
        self.setWindowTitle(self.tr2("ABET 行为试次与钙信号重采样", "ABET trials and calcium resampling"))
        outer, layout = dialog_layout(self, 850, 650)
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
        self.table.setMinimumHeight(self.fontMetrics().height() * 7)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.span = self.label()
        layout.addWidget(self.span)
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.gap = self.seconds()
        if not standalone:
            self.form.addRow(self.tr2("对照进槽隔离间隔", "Control tray-entry isolation"), self.gap)
        else:
            self.gap.hide()
        self.help = self.label()
        layout.addWidget(self.help)
        self.error_text = self.label()
        self.error_text.setStyleSheet("color: #ad480c;")
        layout.addWidget(self.error_text)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(self.tr2("关闭", "Close") if standalone else
                                                         self.tr2("确认设置", "Apply"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.tr2("取消", "Cancel"))
        self.buttons.button(QDialogButtonBox.Cancel).setVisible(not standalone)
        export_btn = QPushButton(self.tr2("导出行为试次…", "Export behavior trials…"))
        export_btn.clicked.connect(self.export_native)
        layout.addWidget(export_btn)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.load_file(path, previous)

    def tr2(self, zh, en):
        return zh if self.lang == "zh" else en

    def label(self):
        label = QLabel()
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        return label

    def seconds(self):
        spin = QDoubleSpinBox(self)
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
        self.gap.setValue(cfg.control_gap_s)
        pulses = session.sync_pulses
        error = ""
        if len(pulses) == 2 and pulses[1] > pulses[0]:
            start, end = pulses
            span = self.tr2(f"行为范围（start #1 两个上升沿）　{start:.6f} – {end:.6f} s · 时长 {end-start:.6f} s",
                            f"Behavior interval (two start #1 rising edges)　{start:.6f} – {end:.6f} s · span {end-start:.6f} s")
        else:
            span = self.tr2(f"start #1 脉冲数　{len(pulses)}", f"start #1 pulses　{len(pulses)}")
            error = self.tr2("联合分析需要两个先后发生的 start #1 上升沿。", "Joint analysis requires two distinct, ordered start #1 rising edges.")
        if self.calcium_time is not None and not error:
            try:
                info = align_abet(session, self.config(), self.calcium_time).info
            except ValueError as exc:
                error = str(exc)
            else:
                span += self.tr2(
                    "\n原始钙记录　{source_span_s:.6f} s · {source_samples:,} 帧 · {source_fs_hz:.5f} Hz"
                    "\n重采样后　{aligned_span_s:.6f} s · {aligned_samples:,} 帧 · {aligned_fs_hz:.5f} Hz",
                    "\nOriginal calcium　{source_span_s:.6f} s · {source_samples:,} frames · {source_fs_hz:.5f} Hz"
                    "\nResampled　{aligned_span_s:.6f} s · {aligned_samples:,} frames · {aligned_fs_hz:.5f} Hz").format(**info)
        self.span.setText(span)
        note = ("" if self.standalone else self.tr2(
            "首末钙帧分别对准行为开始、结束脉冲，按接近原采样率的均匀间隔重采样。事件和分析结果使用行为设备的秒数。",
            "The first and last calcium frames align to the two behavior pulses. Calcium is resampled uniformly near its original rate. Events and analysis use behavior-clock seconds."))
        self.help.setText("\n".join(filter(None, [note, *session.warnings])))
        self.error_text.setText(error)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.standalone or not error)

    def config(self):
        return AbetConfig(path=str(self.path), control_gap_s=self.gap.value())

    def export_native(self):
        path, _ = QFileDialog.getSaveFileName(self, self.windowTitle(), str(self.path.with_name(self.path.stem + "_abet_trials.csv")), "CSV (*.csv)")
        if path:
            try:
                self.session.trials.to_csv(path, index=False, encoding="utf-8-sig")
            except OSError as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc))
