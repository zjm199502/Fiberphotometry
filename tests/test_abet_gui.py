import numpy as np
import pandas as pd
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog

from fiberphoto.abet import AbetConfig, CUE_RESPONDED
from fiberphoto.analysis import peri_event
from fiberphoto.gui.abet import AbetDialog
from fiberphoto.gui.main_window import MainWindow
from fiberphoto.preprocess import dedrift


def test_abet_inspect_without_recording_and_anchor_validation(qtbot, tmp_path, monkeypatch, abet_xml):
    window = MainWindow("zh")
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(abet_xml), ""))
    export = tmp_path / "behavior_only.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(export), ""))

    def inspect():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, AbetDialog) and dialog.model.rowCount() == 4
        assert dialog.mode.currentData() == "behavior_only" and not dialog.mode.isEnabled()
        dialog.export_native()
        dialog.accept()

    QTimer.singleShot(0, inspect)
    window.b_abet.click()
    assert pd.read_csv(export)["trial"].tolist() == [1, 2, 3, 4]
    assert not window.cfg.abet_by_recording

    dialog = AbetDialog(abet_xml, "en", calcium_time=np.arange(0., 150.))
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.Ok)
    dialog.mode.setCurrentIndex(dialog.mode.findData("affine"))
    assert not ok.isEnabled()
    dialog.anchors[0][1].setText("3")
    dialog.anchors[1][1].setText("2")
    assert not ok.isEnabled()
    dialog.anchors[1][1].setText("134")
    assert ok.isEnabled() and dialog.config().anchors == [[0.2, 3], [128, 134]]
    dialog.mode.setCurrentIndex(dialog.mode.findData("assumed_trigger"))
    assert dialog.config().anchors == [] and "startup" in dialog.help.text()
    assert dialog.config().latency_s == 0
    dialog.latency.setValue(.35)
    assert dialog.config().latency_s == .35


def test_apply_remove_restore_abet_and_invalidate_only_affected_results(qtbot, tmp_path, abet_xml):
    first, second = abet_xml.with_suffix(".csv"), tmp_path / "other.csv"
    t = np.arange(600) / 4
    for path in (first, second):
        pd.DataFrame({"time": t, "RawF_1": 50 + np.sin(t), "R_1": .03 * np.sin(t)}).to_csv(path, index=False)
    window = MainWindow("en")
    qtbot.addWidget(window)
    window.add_paths([first, second])
    assert "Matching XML" in window.lbl_abet.text()
    window.step_read()
    qtbot.waitUntil(lambda: window.A is not None)
    assert window.A.abet.info["mode"] == "behavior_only" and CUE_RESPONDED not in window.A.markers
    window.edit_manual_ttl(87.)
    window.set_abet(AbetConfig(path=str(abet_xml), mode="assumed_trigger"))
    qtbot.waitUntil(lambda: not window._busy)
    assert CUE_RESPONDED in window.A.markers and "Estimated" in window.lbl_abet.text()
    window.B = dedrift(window.A, window.cfg.dedrift)
    window.peth = peri_event(window.B, window.A.markers[CUE_RESPONDED], window.cfg.peth, CUE_RESPONDED)
    window.stim_events = pd.DataFrame()
    window.stim_events.attrs["marker"] = CUE_RESPONDED
    window.set_abet(AbetConfig(path=str(abet_xml), mode="offset", anchors=[[.2, 2.]]))
    qtbot.waitUntil(lambda: not window._busy)
    assert window.B is not None and window.peth is None and window.stim_events is None
    raw = window.A
    window.file_list.setCurrentRow(1)
    assert not window.lbl_abet.text()
    window.file_list.setCurrentRow(0)
    assert window.A is raw and window.cfg.abet_by_recording[str(first)].mode == "offset"
    window.b_abet_clear.click()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.abet is None and window.A.markers[window.A.manual_ttl_label].tolist() == [87.]
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.abet is None
