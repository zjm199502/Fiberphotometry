"""Choose a single behavior series and map its timestamps onto the recording clock."""
from pathlib import Path
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QVBoxLayout)

from ..behavior import BehaviorConfig, behavior_columns


class BehaviorDialog(QDialog):
    def __init__(self, path, lang, parent=None, previous=None):
        super().__init__(parent)
        self.lang = lang
        self.setWindowTitle(self.tr2("行为序列对齐", "Align behavior series"))
        self.resize(490, 360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        file_row = QHBoxLayout()
        self.filename = QLabel()
        self.filename.setTextFormat(Qt.PlainText)
        self.filename.setWordWrap(True)
        choose = QPushButton(self.tr2("更换 CSV…", "Change CSV…"), clicked=self.change_file)
        file_row.addWidget(self.filename, 1)
        file_row.addWidget(choose)
        form.addRow("CSV", file_row)
        self.time_col = QComboBox()
        self.value_col = QComboBox()
        self.unit = QComboBox()
        for label, unit in [(self.tr2("秒", "Seconds"), "s"), (self.tr2("毫秒", "Milliseconds"), "ms"),
                            (self.tr2("微秒", "Microseconds"), "us")]:
            self.unit.addItem(label, unit)
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(6)
        self.rate.setRange(0, 1e8)
        self.rate.setSpecialValueText(self.tr2("请输入采样率", "Enter sample rate"))
        self.rate.setSuffix(" Hz")
        self.rate_label = QLabel(self.tr2("行为采样率", "Behavior sample rate"))
        self.unit_label = QLabel(self.tr2("时间单位", "Timestamp unit"))
        form.addRow(self.tr2("时间列", "Timestamp column"), self.time_col)
        form.addRow(self.unit_label, self.unit)
        form.addRow(self.rate_label, self.rate)
        form.addRow(self.tr2("行为数值列", "Behavior value column"), self.value_col)
        self.starts = QCheckBox(self.tr2("将行为首帧对齐到钙记录首帧", "Align first behavior sample to first calcium sample"))
        form.addRow(self.starts)
        self.offset = QDoubleSpinBox()
        self.offset.setDecimals(6)
        self.offset.setRange(-1e10, 1e10)
        self.offset.setSuffix(" s")
        form.addRow(self.tr2("时间偏移", "Time offset"), self.offset)
        self.method = QComboBox()
        for label, method in [(self.tr2("线性插值（连续量）", "Linear (continuous values)"), "linear"),
                              (self.tr2("沿用前值（状态）", "Previous sample (states)"), "previous"),
                              (self.tr2("最近样本", "Nearest sample"), "nearest")]:
            self.method.addItem(label, method)
        form.addRow(self.tr2("重采样方式", "Resampling"), self.method)
        help_text = QLabel(self.tr2("未勾选首帧对齐时，两份记录使用共同的时间零点。正偏移将行为向后移动。重叠范围外留空。",
                                    "With first-sample alignment off, both recordings share a time origin. "
                                    "A positive offset moves behavior later. Values outside the overlap stay empty."))
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(self.tr2("应用", "Apply"))
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.tr2("取消", "Cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.time_col.currentIndexChanged.connect(self._time_changed)
        self.rate.valueChanged.connect(self._validate)
        self.value_col.currentIndexChanged.connect(self._validate)
        self.load_file(path, previous)

    def tr2(self, zh, en):
        return zh if self.lang == "zh" else en

    def change_file(self):
        path, _ = QFileDialog.getOpenFileName(self, self.windowTitle(), str(self.path.parent), "CSV (*.csv)")
        if path:
            try:
                self.load_file(path)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc))

    def load_file(self, path, previous=None):
        columns = behavior_columns(path)
        self.path = Path(path).resolve()
        self.columns = columns
        self.filename.setText(self.path.name)
        self.filename.setToolTip(str(self.path))
        self.time_col.blockSignals(True)
        self.time_col.clear()
        self.time_col.addItem(self.tr2("无时间列（按行为采样率）", "No timestamps (use behavior sample rate)"), None)
        for name in columns:
            self.time_col.addItem(name, name)
        guess = next((c for c in columns if re.search(r"timestamp|^time(?:$|[^a-z])|时间", c, re.I)), None)
        if len(columns) == 1:
            guess = None
        elif not guess and len(columns) == 2:
            guess = columns[0]
        time_name = previous.time_column if previous else guess
        self.time_col.setCurrentIndex(max(0, self.time_col.findData(time_name)))
        self.time_col.blockSignals(False)
        self.rate.setValue((previous.sampling_rate_hz or 0) if previous else 0)
        self._time_changed()
        self.offset.setValue(previous.offset_s if previous else 0)
        self.method.setCurrentIndex(self.method.findData(previous.method if previous else "linear"))
        if previous:
            self.value_col.setCurrentIndex(self.value_col.findData(previous.value_column))
            self.unit.setCurrentIndex(self.unit.findData(previous.time_unit))
            self.starts.setChecked(previous.align_starts)
        self._validate()

    def _time_changed(self):
        name = self.time_col.currentData()
        old_value = self.value_col.currentData()
        self.value_col.clear()
        for column in self.columns:
            if column != name:
                self.value_col.addItem(column, column)
        if self.value_col.findData(old_value) >= 0:
            self.value_col.setCurrentIndex(self.value_col.findData(old_value))
        self.unit.setVisible(name is not None)
        self.unit_label.setVisible(name is not None)
        self.rate.setVisible(name is None)
        self.rate_label.setVisible(name is None)
        self.starts.setChecked(name is None)
        unit = "s"
        if name and re.search(r"毫秒|(?<![a-z])(?:ms|msec)\b", name, re.I):
            unit = "ms"
        elif name and re.search(r"微秒|(?<![a-z])(?:us|µs|μs)\b", name, re.I):
            unit = "us"
        self.unit.setCurrentIndex(self.unit.findData(unit))
        self._validate()

    def _validate(self):
        valid = self.value_col.currentIndex() >= 0 and (self.time_col.currentData() is not None or self.rate.value() > 0)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(valid)

    def config(self):
        time_column = self.time_col.currentData()
        return BehaviorConfig(path=str(self.path), value_column=self.value_col.currentData(), time_column=time_column,
                              time_unit=self.unit.currentData(), sampling_rate_hz=None if time_column else self.rate.value(),
                              offset_s=self.offset.value(), method=self.method.currentData(), align_starts=self.starts.isChecked())
