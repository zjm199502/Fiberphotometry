import numpy as np
import pandas as pd
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialogButtonBox, QFileDialog

from fiberphoto.abet import AbetConfig, CUE_RESPONDED
from fiberphoto.config import PipelineConfig
from fiberphoto.gui.abet import AbetDialog
from fiberphoto.gui.main_window import MainWindow
from fiberphoto.preprocess import dedrift


def test_abet_inspection_and_single_resampling_rule(qtbot, tmp_path, monkeypatch, abet_xml):
    window = MainWindow("zh")
    qtbot.addWidget(window)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(abet_xml), ""))
    export = tmp_path / "behavior_only.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (str(export), ""))

    def inspect():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, AbetDialog) and dialog.model.rowCount() == 4
        assert not dialog.findChildren(QComboBox)
        dialog.export_native()
        dialog.accept()

    QTimer.singleShot(0, inspect)
    window.b_abet.click()
    assert pd.read_csv(export)["trial"].tolist() == [1, 2, 3, 4]
    assert not window.cfg.abet_by_recording

    dialog = AbetDialog(abet_xml, "en", calcium_time=np.arange(0., 150.))
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.Ok)
    assert ok.isEnabled() and not dialog.findChildren(QComboBox)
    assert dialog.config().mode == "duration_resample"
    assert "127.800000 s" in dialog.span.text() and "149.000000 s" in dialog.span.text()
    assert "frames" in dialog.span.text()
    invalid = tmp_path / "single_pulse.xml"
    invalid.write_text(abet_xml.read_text().replace("start #1", "unused", 1))
    dialog.load_file(invalid)
    assert not ok.isEnabled()
    assert "two distinct" in dialog.error_text.text()


def test_apply_remove_restore_abet_and_invalidate_only_affected_results(qtbot, tmp_path, abet_xml):
    first, second = abet_xml.with_suffix(".csv"), tmp_path / "other.csv"
    t = np.arange(600) / 4
    for path in (first, second):
        pd.DataFrame({"time": t, "RawF_1": 50 + np.sin(t), "R_1": .03 * np.sin(t)}).to_csv(path, index=False)
    window = MainWindow("en")
    qtbot.addWidget(window)
    window.add_paths([first, second])
    assert "Resample" in window.lbl_abet.text()
    window.step_read()
    qtbot.waitUntil(lambda: window.A is not None)
    raw = window.A
    assert raw.abet.info["mode"] == "duration_resample" and CUE_RESPONDED in raw.markers
    assert raw.time[[0, -1]].tolist() == [.2, 128]
    point = raw.time[60]
    window.edit_manual_ttl(point)
    native_point = raw.to_acquisition_time(point)
    assert window.cfg.manual_ttl_by_recording[str(first)] == pytest.approx([native_point])
    assert window.A.markers[window.A.manual_ttl_label] == pytest.approx([point])
    edits = window.form_dedrift.editors
    edits["signal_start_s"].setValue(5.)
    edits["signal_end_s"].setValue(70.)
    cfg_path = tmp_path / "params.json"
    window.forms_store().save(cfg_path)
    assert PipelineConfig.load(cfg_path).for_recording(first, raw=raw).dedrift.signal_start_s == pytest.approx(5.)
    window.B = dedrift(window.A, window.cfg.dedrift)
    result = window.A
    window.file_list.setCurrentRow(1)
    assert not window.lbl_abet.text()
    window.file_list.setCurrentRow(0)
    assert window.A is result and edits["signal_start_s"].value() == pytest.approx(5.)
    window.b_abet_clear.click()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.abet is None and window.B is None
    assert window.A.markers[window.A.manual_ttl_label].tolist() == pytest.approx([native_point])
    assert edits["signal_start_s"].value() == pytest.approx(raw.to_acquisition_time(5.))
    assert np.array_equal(t, window.A.time)
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert window.A.abet is None
    window.set_abet(AbetConfig(path=str(abet_xml)))
    qtbot.waitUntil(lambda: not window._busy)
    assert edits["signal_start_s"].value() == pytest.approx(5.)
    assert window.A.markers[window.A.manual_ttl_label].tolist() == pytest.approx([point])
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    assert edits["signal_start_s"].value() == pytest.approx(5.)
    assert window.A.markers[window.A.manual_ttl_label].tolist() == pytest.approx([point])
