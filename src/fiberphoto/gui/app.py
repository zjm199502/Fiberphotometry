"""Start the desktop application: ``fiberphoto-gui`` or ``python -m fiberphoto.gui``."""
from __future__ import annotations

import sys

STYLE = """
QWidget { font-size: 13px; }
QGroupBox { font-weight: 600; border: 1px solid #dddcd6; border-radius: 6px; margin-top: 10px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QLabel#help { background: #eef4fc; color: #23466e; border-radius: 6px; padding: 8px 10px; }
QPushButton { padding: 5px 12px; border-radius: 5px; }
QPushButton[primary="true"] { background: #2a78d6; color: white; font-weight: 600; border: none; }
QPushButton[primary="true"]:hover { background: #2368bd; }
QPushButton[primary="true"]:disabled { background: #b9cbe3; }
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
