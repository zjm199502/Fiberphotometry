import numpy as np
from matplotlib.backend_bases import MouseEvent
from PySide6.QtWidgets import QMessageBox

from fiberphoto.gui.main_window import MainWindow


def test_choose_baseline_on_raw_signal_from_ratio_preview(qtbot, monkeypatch, tmp_path):
    file = tmp_path / "camera.csv"
    file.write_text("time,RawF_1,F0_1,R_1\n0,20,10,1\n1,30,10,2\n2,50,10,4\n3,60,10,5\n4,70,10,6\n")
    window = MainWindow(lang="en")
    qtbot.addWidget(window)
    window.add_paths([file])
    window.cb_signal.setCurrentText("R_1")
    window.step_read()
    qtbot.waitUntil(lambda: not window._busy)
    picker = window.baseline_picker
    assert window.A.channel_names["preview_signal"] == "R_1"
    picker.canvas.figure.axes[0].set_xlim(-0.1, 4.2)
    picker.button.click()
    canvas = picker.canvas
    np.testing.assert_allclose(canvas.figure.axes[0].get_xlim(), [-0.1, 4.2])
    assert canvas.widgetlock.isowner(picker)
    np.testing.assert_array_equal(canvas.figure.axes[0].lines[0].get_ydata(), [20, 30, 50, 60, 70])
    canvas.draw()
    ax = canvas.figure.axes[0]

    def click(t):
        x, y = ax.transData.transform((t, 40))
        for name in ("button_press_event", "button_release_event"):
            canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))

    prompts = []
    def yes(*args):
        prompts.append(args[2])
        return QMessageBox.Yes
    monkeypatch.setattr(QMessageBox, "question", yes)
    click(2.8)
    assert picker.guide is not None
    click(.2)
    assert "median" in prompts[0] and "F0 = 40" in prompts[0]
    assert window.cfg.for_recording(file).dedrift.method == "signal_interval"
    assert not window.cfg.for_recording(file).dedrift.prefer_plexon_dff
    assert abs(window.cfg.for_recording(file).dedrift.baseline_start_s - .2) < .05
    assert abs(window.cfg.for_recording(file).dedrift.baseline_end_s - 2.8) < .05
    np.testing.assert_array_equal(picker.canvas.figure.axes[0].lines[0].get_ydata(), [1, 2, 4, 5, 6])
    assert "baseline" in picker.spans and "F0 = 40" in picker.note.text()
    window.redraw_all()
    assert picker.spans["baseline"] in picker.canvas.figure.axes[0].patches
    picker.button.click()
    picker.button.click()
    assert picker.kind is None and not picker.canvas.widgetlock.locked()

    # This signal region can be independent of the baseline. Drag should leave both axes fixed.
    picker.signal_button.click()
    frame = picker.canvas
    frame.draw()
    ax = frame.figure.axes[0]
    original_limits = ax.get_xlim(), ax.get_ylim()
    y = np.mean(ax.get_ylim())
    for name, t in [("button_press_event", 2.9), ("motion_notify_event", 3.6), ("button_release_event", 4.05)]:
        x_px, y_px = ax.transData.transform((t, y))
        frame.callbacks.process(name, MouseEvent(name, frame, x_px, y_px, button=1))
    np.testing.assert_allclose(ax.get_xlim(), original_limits[0])
    np.testing.assert_allclose(ax.get_ylim(), original_limits[1])
    assert window.cfg.for_recording(file).dedrift.signal_start_s == window.form_dedrift.editors["signal_start_s"].value()
    assert 2.85 < window.cfg.for_recording(file).dedrift.signal_start_s < 2.95
    assert window.cfg.for_recording(file).dedrift.signal_end_s == 4
    assert window.cfg.for_recording(file).dedrift.baseline_end_s < window.cfg.for_recording(file).dedrift.signal_start_s
    assert set(picker.spans) == {"signal", "baseline"}
    picker.no_baseline.click()
    assert window.cfg.for_recording(file).dedrift.method == "isosbestic" and window.cfg.for_recording(file).dedrift.prefer_plexon_dff
    assert set(picker.spans) == {"signal"}
    picker.whole.click()
    assert window.cfg.for_recording(file).dedrift.signal_start_s is None and window.cfg.for_recording(file).dedrift.signal_end_s is None
    assert not picker.spans

    # A baseline may also be dragged directly, with the ratio preserved as the original preview.
    picker.button.click()
    frame = picker.canvas
    frame.draw()
    ax = frame.figure.axes[0]
    for name, t in [("button_press_event", 2.7), ("motion_notify_event", 1.2), ("button_release_event", .1)]:
        x_px, y_px = ax.transData.transform((t, 42))
        frame.callbacks.process(name, MouseEvent(name, frame, x_px, y_px, button=1))
    assert window.cfg.for_recording(file).dedrift.method == "signal_interval"
    assert "F0 = 40" in picker.note.text()
    assert window.A.channel_names["preview_signal"] == "R_1"
    np.testing.assert_array_equal(window.A.signal, [20, 30, 50, 60, 70])
