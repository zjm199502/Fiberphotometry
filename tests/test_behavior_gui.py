import dataclasses

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog

from fiberphoto.gui.behavior import BehaviorDialog
from fiberphoto.gui.main_window import MainWindow


def test_behavior_dialog_mapping(qtbot, tmp_path):
    path = tmp_path / "position.csv"
    path.write_text("time_ms,speed,state\n1000,0.1,1\n1500,0.4,0\n")
    dialog = BehaviorDialog(path, "en")
    qtbot.addWidget(dialog)
    assert dialog.time_col.currentData() == "time_ms"
    assert dialog.unit.currentData() == "ms"
    assert dialog.value_col.currentData() == "speed" and not dialog.starts.isChecked()
    dialog.value_col.setCurrentText("state")
    dialog.offset.setValue(-0.5)
    dialog.method.setCurrentIndex(dialog.method.findData("previous"))
    again = BehaviorDialog(path, "zh", previous=dialog.config())
    qtbot.addWidget(again)
    assert again.config() == dialog.config()
    path.write_text("10\n11\n12\n")
    dialog.load_file(path)
    assert dialog.time_col.currentData() is None
    assert dialog.value_col.currentData() == "column_1"
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    dialog.rate.setValue(10)
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert dialog.config().sampling_rate_hz == 10 and dialog.starts.isChecked()


def test_behavior_import_reselect_and_remove(qtbot, tmp_path, monkeypatch):
    files = [tmp_path / "mouse1.csv", tmp_path / "mouse2.csv"]
    for p in files:
        p.write_text("time,RawF_1\n0,32\n1,33\n2,30\n3,30\n4,31\n")
    sidecar = tmp_path / "speed.csv"
    sidecar.write_text("time_ms,speed\n1000,2\n3000,6\n")
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    assert not window.b_behavior.isEnabled()
    window.add_paths(files)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(sidecar), ""))
    def apply_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, BehaviorDialog)
        dialog.buttons.button(QDialogButtonBox.Ok).click()
    QTimer.singleShot(0, apply_dialog)
    window.b_behavior.click()
    assert "Load the recording" in window.lbl_behavior.text()
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy, timeout=10000)
    assert window.A is not None
    np.testing.assert_allclose(window.A.behavior, [np.nan, 2, 4, 6, np.nan], equal_nan=True)
    np.testing.assert_allclose(window.fig_raw.canvas.figure.axes[-1].lines[0].get_ydata(),
                               window.A.behavior, equal_nan=True)
    assert "3/5" in window.lbl_behavior.text()
    spec = window.cfg.behavior_by_recording[str(files[0])]
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    window.set_behavior(dataclasses.replace(spec, offset_s=100))
    qtbot.waitUntil(lambda: not window._busy)
    assert errors and window.cfg.behavior_by_recording[str(files[0])] == spec
    window.file_list.setCurrentRow(1)
    assert window.lbl_behavior.text() == ""
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.behavior is None
    window.set_behavior(spec)
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.behavior is not None and "3/5" in window.lbl_behavior.text()
    window.b_behavior_clear.click()
    assert window.A.behavior is None
    assert str(files[1]) not in window.cfg.behavior_by_recording
    window.file_list.setCurrentRow(0)
    assert str(files[0]) in window.cfg.behavior_by_recording and "speed" in window.lbl_behavior.text()
