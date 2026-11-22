"""Change the appearance of the text or artist selected in a preview."""
from matplotlib import colormaps
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QPlainTextEdit, QPushButton, QVBoxLayout)

from ..figure_style import figure_elements


def edit_figure(view, event=None):
    en = view.edit_language == "en"
    entries = figure_elements(view.canvas.figure, view.edit_language)
    if not entries:
        return
    dialog = QDialog(view)
    dialog.setWindowTitle("Edit figure" if en else "编辑图形")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    layout.addLayout(form)
    select = QComboBox()
    select.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    select.setMinimumContentsLength(24)
    select.addItems([" ".join(e.name.split()) for e in entries])
    form.addRow("Element" if en else "图中元素", select)
    content = QPlainTextEdit()
    content.setMaximumHeight(80)
    form.addRow("Text" if en else "文字", content)
    size = QDoubleSpinBox()
    size.setRange(4, 96)
    size.setSuffix(" pt")
    form.addRow("Font size" if en else "字号", size)
    color = QPushButton()
    form.addRow("Color" if en else "颜色", color)
    palette = QComboBox()
    palette.addItems(sorted(colormaps))
    form.addRow("Color map" if en else "热图色带", palette)

    def set_color(value):
        color.setText(value)
        ink = "black" if QColor(value).lightnessF() > .55 else "white"
        color.setStyleSheet(f"background-color: {value}; color: {ink};")

    def pick_color():
        chosen = QColorDialog.getColor(QColor(color.text()), dialog)
        if chosen.isValid():
            set_color(chosen.name())
    color.clicked.connect(pick_color)

    def load(index):
        values = entries[index].values()
        for field, key in ((content, "text"), (size, "size"), (color, "color"), (palette, "cmap")):
            form.setRowVisible(field, key in values)
        content.setPlainText(values.get("text", ""))
        size.setValue(values.get("size", 10))
        set_color(values.get("color", "#000000"))
        palette.setCurrentText(values.get("cmap", "viridis"))
    select.currentIndexChanged.connect(load)
    index = 0
    if event is not None:
        # Text is on top of the data. Among data artists, prefer the uppermost visible series.
        candidates = sorted(enumerate(entries), key=lambda pair: (pair[1].kind in ("text", "category", "ticks"),
                            max(a.get_zorder() for a in pair[1].artists)), reverse=True)
        index = next((i for i, item in candidates
                      if any(a.get_visible() and a.contains(event)[0] for a in item.artists)), 0)
    select.setCurrentIndex(index)
    load(index)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText("OK" if en else "确定")
    buttons.button(QDialogButtonBox.Cancel).setText("Cancel" if en else "取消")
    reset = buttons.addButton("Restore figure defaults" if en else "恢复本图默认外观", QDialogButtonBox.ResetRole)
    reset.setEnabled(bool(view.figure_edits))
    def restore_appearance():
        for original, values in view.original_appearance.values():
            original.apply(values)
        view.original_appearance.clear()
        view.figure_edits.clear()
        view.canvas.draw_idle()
        dialog.reject()
    reset.clicked.connect(restore_appearance)
    layout.addWidget(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    if dialog.exec() == QDialog.Accepted:
        entry = entries[select.currentIndex()]
        edited = {"text": content.toPlainText(), "size": size.value(), "color": color.text(),
                  "cmap": palette.currentText()}
        changes = {key: edited[key] for key, previous in entry.values().items() if edited[key] != previous}
        if changes:
            view.original_appearance.setdefault(entry.key, (entry, entry.values()))
            entry.apply(changes)
            view.figure_edits.setdefault(entry.key, {}).update(changes)
            view.canvas.draw_idle()
    dialog.deleteLater()
