import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backend_bases import MouseEvent
from matplotlib.colors import to_hex
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from fiberphoto.config import PipelineConfig
from fiberphoto.figure_style import apply_figure_edits, figure_elements
from fiberphoto.gui.widgets import FigureView
from fiberphoto.pipeline import run_file
from fiberphoto.sample_data import make_sample_dataset


def test_direct_text_edit_survives_redraw_and_exports(qtbot, tmp_path):
    view = FigureView()
    qtbot.addWidget(view)
    view.resize(720, 600)
    view.show()
    def chart():
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1], label="Signal")
        ax.set_title("Recorded signal", loc="left")
        ax.legend()
        return fig, ax
    fig, ax = chart()
    view.set_figure(fig)
    view.canvas.draw()
    target = ax._left_title.get_window_extent(view.canvas.get_renderer())
    event = MouseEvent("button_press_event", view.canvas, target.x0 + 8, target.y0 + 6, button=1, dblclick=True)
    def rename():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        entry = dialog.findChild(QPlainTextEdit)
        assert entry.toPlainText() == "Recorded signal"
        entry.setPlainText("Animal A, cue onset")
        dialog.accept()
    QTimer.singleShot(0, rename)
    view.canvas.callbacks.process("button_press_event", event)
    assert ax.get_title(loc="left") == "Animal A, cue onset" and view.canvas._drag is None
    fig2, ax2 = chart()
    view.set_figure(fig2)
    assert ax2.get_title(loc="left") == "Animal A, cue onset"
    recording, = make_sample_dataset(tmp_path / "samples", n_animals=1, duration_s=70)
    cfg = PipelineConfig()
    cfg.plot.formats = ["svg"]
    cfg.deconv.enabled = False
    cfg.figure_edits_by_recording[str(recording.resolve())] = {"01_raw_dataA": view.figure_edits}
    res = run_file(recording, cfg, tmp_path / "out")
    assert not res.error, res.error
    assert "Animal A, cue onset" in (res.out_dir / "figures/01_raw_dataA.svg").read_text(encoding="utf-8")
    plt.close(fig)
    plt.close(fig2)


def test_heatmap_and_trace_recolor_legend_and_preserve_data():
    fig, ax = plt.subplots()
    matrix = np.arange(12).reshape(3, 4)
    heatmap = ax.imshow(matrix)
    line, = ax.plot([0, 1], [2, 3], label="Calcium", color="blue")
    legend = ax.legend()
    styles = {}
    for item in figure_elements(fig):
        if line in item.artists:
            styles[item.key] = {"color": "#e64128"}
        if heatmap in item.artists:
            styles[item.key] = {"cmap": "magma"}
    apply_figure_edits(fig, styles)
    assert heatmap.get_cmap().name == "magma"
    assert to_hex(line.get_color()) == to_hex(legend.legend_handles[0].get_color()) == "#e64128"
    np.testing.assert_array_equal(heatmap.get_array(), matrix)
    np.testing.assert_array_equal(line.get_ydata(), [2, 3])
    plt.close(fig)
