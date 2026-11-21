"""Start the desktop application: ``fiberphoto-gui`` or ``python -m fiberphoto.gui``."""
from __future__ import annotations

import sys

STYLE = """
QWidget { font-size: 13px; }
QGroupBox { font-weight: 600; border: 1px solid #dddcd6; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel#help { background: #eef4fc; color: #23466e; border-radius: 6px; padding: 8px 10px; }
QPushButton, QToolButton {
    border: 1px solid #8b99a6; border-radius: 5px; background: #fafbfc; color: #192735;
}
QPushButton { padding: 5px 12px; }
QToolButton { padding: 2px; margin: 1px; }
QPushButton:hover, QToolButton:hover { background: #edf4fc; border-color: #6381a2; }
QPushButton:checked, QToolButton:checked { background: #cddff3; border-color: #527aa1; }
QPushButton:pressed, QToolButton:pressed { background: #d8e6f4; border-color: #466a90; }
QPushButton:focus { border: 2px solid #246ab1; padding: 4px 11px; }
QToolButton:focus { border: 2px solid #246ab1; padding: 1px; }
QToolButton#qt_toolbar_ext_button { padding: 0; margin: 0; border-radius: 3px; }
QDockWidget::float-button, QDockWidget::close-button {
    border: 1px solid #8b99a6; border-radius: 2px; background: #fafbfc; width: 16px; height: 14px;
}
QDockWidget::float-button:hover, QDockWidget::close-button:hover { background: #edf4fc; border-color: #6381a2; }
QDockWidget::float-button:pressed, QDockWidget::close-button:pressed { background: #d8e6f4; border-color: #466a90; }
QPushButton:disabled, QToolButton:disabled { color: #717983; border-color: #b8bfc6; background: #efefef; }
QPushButton[primary="true"] { background: #2a78d6; color: white; font-weight: 600; border-color: #185aa6; }
QPushButton[primary="true"]:hover { background: #2368bd; }
QPushButton[primary="true"]:pressed { background: #195396; }
QPushButton[primary="true"]:focus { border-color: #12395e; }
QPushButton[primary="true"]:disabled { background: #b9cbe3; color: #394f6c; border-color: #879bb5; }
QTabBar::tab { padding: 8px 16px; }
QProgressBar { border: none; background: #ecebe6; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #2a78d6; border-radius: 3px; }
"""


def main(argv=None) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("fiberphoto")
    app.setApplicationDisplayName("FiberLume")
    app.setOrganizationName("fiberphoto")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    lang = None
    for a in argv[1:]:
        if a in ("--en", "--zh"):
            lang = a[2:]
    win = MainWindow(lang=lang)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
