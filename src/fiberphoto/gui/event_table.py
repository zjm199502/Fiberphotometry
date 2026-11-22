"""Read-only event measurements backed by the detector's result frame."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class EventTableModel(QAbstractTableModel):
    columns = ("peak_time_s", "amplitude", "auc", "time_s", "auc_start_s", "auc_end_s", "baseline")
    labels = {
        "zh": ("峰时间", "幅值", "AUC", "检测时间", "积分开始", "积分结束", "基线"),
        "en": ("Peak time", "Amplitude", "AUC", "Detection time", "AUC start", "AUC end", "Baseline"),
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.frame = None
        self.lang = "zh"
        self.unit = ""

    def set_result(self, frame, unit, lang):
        if self.frame is frame and self.unit == unit and self.lang == lang:
            return
        self.beginResetModel()
        self.frame, self.unit, self.lang = frame, unit, lang
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if self.frame is None or parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.DisplayRole:
            value = self.frame[self.columns[index.column()]].iat[index.row()]
            return f"{value:.7g}"

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return
        if orientation == Qt.Vertical:
            return str(section + 1)
        key = self.columns[section]
        label = self.labels[self.lang][section]
        unit = "s" if key.endswith("_s") else self.unit
        if key == "auc" and unit:
            unit += "·s"
        return f"{label} ({unit})" if unit else label
