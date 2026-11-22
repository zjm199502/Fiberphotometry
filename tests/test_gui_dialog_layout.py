"""Native layout checks for the scientific import and review dialogs."""
import json
import os
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton

from fiberphoto.gui import main_window
from fiberphoto.gui.abet import AbetDialog
from fiberphoto.gui.abet_comparison import compare_abet
from fiberphoto.gui.app import STYLE
from fiberphoto.gui.behavior import BehaviorDialog
from fiberphoto.gui.widgets import FigureView
from fiberphoto.preprocess import dedrift
from test_gui_layout import _legend_geometry, _reachable, _reveal


@pytest.mark.skipif(not os.environ.get("FIBERLUME_LAYOUT_ARTIFACTS"), reason="Native desktop layout acceptance")
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_native_dialog_layout(qtbot, qapp, tmp_path, abet_xml, monkeypatch, lang):
    backend = qapp.platformName().lower()
    assert backend == {"Windows": "windows", "Darwin": "cocoa"}.get(platform.system())
    artifacts = Path(os.environ["FIBERLUME_LAYOUT_ARTIFACTS"])
    artifacts.mkdir(parents=True, exist_ok=True)
    qapp.setStyle("Fusion")
    qapp.setStyleSheet(STYLE)
    monkeypatch.setattr(main_window, "QSettings", lambda *_: QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    window = main_window.MainWindow(lang)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    # Use a real recording reader and ABET parser with generated data.
    recording = abet_xml.with_suffix(".csv")
    time = np.arange(600) / 4
    pd.DataFrame({"time": time, "RawF_1": 50 + np.sin(time), "R_1": .03 * np.sin(time)}).to_csv(recording, index=False)
    behavior = tmp_path / "synthetic_locomotion.csv"
    pd.DataFrame({"time_ms": [0, 500, 1000], "speed": [1, 3, 1]}).to_csv(behavior, index=False)
    monkeypatch.setattr(window, "_run", lambda compute, done: done(compute()))
    window.add_paths([recording])
    window.step_read()
    window.B = dedrift(window.A, window.cfg.dedrift)
    report = {}

    def inspect(name, dialog):
        dialog.setModal(True)
        dialog.show()
        qtbot.waitExposed(dialog)
        screen = dialog.screen().availableGeometry()
        margins = dialog.windowHandle().frameMargins()
        width = min(671, screen.width() - margins.left() - margins.right() - 12)
        height = min(420, screen.height() - margins.top() - margins.bottom() - 12)
        dialog.resize(width, height)
        dialog.move(screen.x() + 6, screen.y() + 6)
        qtbot.wait(80)
        shot = dialog.grab()
        assert shot.save(str(artifacts / f"{backend}-{lang}-dialog-{name}.png"))
        assert screen.contains(dialog.frameGeometry()) and dialog.width() <= width and dialog.height() <= height
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        footer = [button for button in buttons.buttons() if not button.isHidden()]
        assert footer and all(button.isVisible() and button.visibleRegion().contains(button.rect().adjusted(1, 1, -1, -1))
                              for button in footer)
        details = {"client_dip": [dialog.width(), dialog.height()], "dpr": dialog.devicePixelRatioF(),
                   "footer": [button.text() for button in footer]}
        for label in dialog.findChildren(QLabel):
            if label.text() and label.isVisibleTo(dialog) and label.wordWrap():
                assert label.heightForWidth(label.width()) <= label.height() + 1, label.text()
        if name == "behavior":
            for field in (dialog.time_col, dialog.value_col, dialog.starts, dialog.offset, dialog.method):
                assert _reachable(field, dialog)
            assert dialog.config().value_column == "speed" and dialog.config().time_unit == "ms"
        elif name == "abet":
            assert dialog.model.rowCount() == 4 and _reachable(dialog.gap, dialog)
            assert str(window.A.abet.info["aligned_samples"]) in dialog.span.text()
            _reveal(dialog.table)
            for column in (0, dialog.model.columnCount() - 1):
                cell = dialog.model.index(0, column)
                dialog.table.scrollTo(cell)
                qapp.processEvents()
                assert dialog.table.viewport().visibleRegion().contains(dialog.table.visualRect(cell).center())
        elif name == "comparison":
            view = dialog.findChild(FigureView)
            geometry, problems = _legend_geometry(view)
            assert not problems, problems
            assert len(view.canvas.figure.axes) >= 4
            assert geometry["visible_axes_dip"][0][0] > 90
            assert geometry["visible_axes_dip"][0][1] > 40
            details["plot"] = geometry
        else:
            review = dialog.findChild(QPlainTextEdit)
            assert review.isReadOnly() and "Synthetic channel" in review.toPlainText()
            bar = review.verticalScrollBar()
            assert bar.maximum() > 0
            bar.setValue(bar.maximum())
            assert bar.value() == bar.maximum()
        for button in dialog.findChildren(QPushButton):
            if button.text() and not button.isHidden():
                assert _reachable(button, dialog), button.text()
        report[name] = details
        dialog.reject()
        return QDialog.Rejected

    imports = [("behavior", BehaviorDialog(behavior, lang, window)),
               ("abet", AbetDialog(abet_xml, lang, window, calcium_time=window.A.abet_native.time))]
    for name, dialog in imports:
        qtbot.addWidget(dialog)
        inspect(name, dialog)

    # Intercept only the blocking event loop. The production dialogs are shown
    # as modal native windows and laid out, painted and checked normally.
    monkeypatch.setattr(QDialog, "exec", lambda dialog: inspect("comparison", dialog))
    compare_abet(window)
    monkeypatch.setattr(QDialog, "exec", lambda dialog: inspect("pre-confirmation", dialog))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: pytest.fail("Long confirmation should use a scrollable review"))
    window.A.channel_names["signal"] = " / ".join(f"Synthetic channel segment {i}" for i in range(55))
    assert not window._confirm_preprocessing(window.A, window.forms_store().for_recording(recording, raw=window.A))
    assert set(report) == {"behavior", "abet", "comparison", "pre-confirmation"}
    (artifacts / f"{backend}-{lang}-dialogs.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                            encoding="utf-8")
