"""Session-level ABET comparisons in the peri-event page."""
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox,
                               QPushButton)

from ..abet_analysis import LABELS, analyze_abet, export_abet_analysis, figure_abet_analysis
from .dialog_layout import dialog_layout
from .widgets import FigureView


def compare_abet(window):
    if window.B is None or window.A is None or window.A.abet is None:
        return
    aligned = window.A.abet
    b = window.B
    direction = window.forms_store().peth.peak_direction
    language = window.lang
    en = language == "en"

    def show(result):
        dialog = QDialog(window)
        dialog.setWindowTitle("ABET trial comparison" if en else "ABET 行为分组比较")
        outer, layout = dialog_layout(dialog, 980, 720)
        view = FigureView()
        window._bind_figure_edits(view, "abet_comparison")
        view.setMinimumHeight(10 * view.fontMetrics().height())
        view.set_figure(figure_abet_analysis(result, language))
        layout.addWidget(view, 1)
        counts = " · ".join(f"{LABELS[row.group][int(en)]} {row.n_used}/{row.n_candidates}"
                            for row in result.groups.itertuples() if row.n_candidates)
        note = QLabel(("Complete / candidate events: " if en else "纳入数 / 候选数　") + counts)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        save = QPushButton("Export this comparison…" if en else "导出本次比较…")
        layout.addWidget(save)
        buttons.rejected.connect(dialog.reject)

        def save_result():
            parent = QFileDialog.getExistingDirectory(dialog, save.text())
            if parent:
                out = Path(parent) / (window.current.stem + "_ABET_comparison")
                if out.exists():
                    question = ("Replace the comparison files in " if en else "覆盖此目录中的比较结果？\n") + str(out)
                    if QMessageBox.question(dialog, save.text(), question) != QMessageBox.Yes:
                        return
                try:
                    export_abet_analysis(result, out, language=language, make_figures=False)
                    view.canvas.figure.savefig(out / "overview.png", dpi=160)
                    window.log(str(out))
                except OSError as exc:
                    window.error(str(exc))
        save.clicked.connect(save_result)
        outer.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    window._run(lambda: analyze_abet(b, aligned, peak_direction=direction), show)
