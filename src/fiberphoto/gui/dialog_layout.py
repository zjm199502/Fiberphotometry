"""Keep dialog contents scrollable and actions visible on compact work areas."""
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QMessageBox, QPlainTextEdit,
                               QScrollArea, QVBoxLayout, QWidget)


def dialog_layout(dialog, width, height):
    screen = dialog.screen().availableGeometry()
    dialog.resize(min(width, round(.92 * screen.width())), min(height, round(.86 * screen.height())))
    outer = QVBoxLayout(dialog)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    body = QWidget()
    content = QVBoxLayout(body)
    content.setContentsMargins(2, 2, 2, 2)
    scroll.setWidget(body)
    outer.addWidget(scroll, 1)
    return outer, content


def confirm_details(parent, title, message):
    """Use the platform prompt for short messages and a scrolling review for long ones."""
    screen = parent.screen().availableGeometry()
    width = min(720, round(.86 * screen.width()))
    document = QTextDocument()
    document.setDefaultFont(parent.font())
    document.setPlainText(message)
    document.setTextWidth(width - 60)
    if document.size().height() < min(360, .48 * screen.height()):
        return QMessageBox.question(parent, title, message, QMessageBox.Yes | QMessageBox.Cancel,
                                    QMessageBox.Cancel) == QMessageBox.Yes
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(width, min(590, round(.84 * screen.height())))
    layout = QVBoxLayout(dialog)
    review = QPlainTextEdit(message)
    review.setReadOnly(True)
    layout.addWidget(review, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Cancel).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        return dialog.exec() == QDialog.Accepted
    finally:
        dialog.deleteLater()
