"""Native-window acceptance of the five analysis pages. Enable with FIBERLUME_LAYOUT_ARTIFACTS."""
import json
import os
import platform
import shutil
from pathlib import Path

import pytest

from matplotlib.legend import Legend
from matplotlib.transforms import Bbox
from PySide6.QtCore import QSettings, qVersion
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea

from fiberphoto.gui import main_window
from fiberphoto.gui.app import STYLE
from fiberphoto.sample_data import make_sample_dataset


PAGES = (
    ("read", ["b_read", "b_abet", "b_exclude"], ["fig_raw"]),
    ("pre", ["b_pre", "b_choose_windows"], ["fig_dedrift", "fig_deconv"]),
    ("peth", ["b_peth", "b_abet_compare"], ["fig_peth", "fig_auc"]),
    ("events", ["b_events", "b_export_events"], ["fig_events", "fig_dist", "fig_rate"]),
    ("export", ["b_export", "b_batch", "b_manifest", "b_manifest_template"], []),
)


def _reachable(button):
    """Reveal the button through all enclosing scroll areas as a user could."""
    parent = button.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(button, 1, 1)
        parent = parent.parentWidget()
    QApplication.processEvents()
    return button.isVisible() and button.visibleRegion().contains(button.rect().adjusted(1, 1, -1, -1))


def _legend_geometry(view):
    canvas = view.canvas
    assert canvas is not None
    canvas.draw()
    figure, renderer = canvas.figure, canvas.get_renderer()
    frame = figure.bbox
    result, errors = [], []
    for legend in figure.findobj(Legend):
        box = legend.get_window_extent(renderer)
        labels = [label.get_text() for label in legend.get_texts()]
        result.append({"labels": labels, "bounds_px": list(box.bounds)})
        if not (frame.x0 - 1 <= box.x0 <= box.x1 <= frame.x1 + 1
                and frame.y0 - 1 <= box.y0 <= box.y1 <= frame.y1 + 1):
            errors.append(f"legend outside canvas: {labels}")
        for ax in figure.axes:
            # Test the plot, titles, tick labels and axis labels independently. The
            # empty area between e.g. the y caption and a rotated tick is not occupied.
            occupied = [ax.bbox]
            for artist in [ax.title, ax.xaxis.label, ax.yaxis.label,
                           *ax.get_xticklabels(), *ax.get_yticklabels(), *ax.texts]:
                if artist.get_visible() and artist.get_text():
                    occupied.append(artist.get_window_extent(renderer))
            if any(overlap is not None and overlap.width > 1 and overlap.height > 1
                   for overlap in (Bbox.intersection(box, other) for other in occupied)):
                errors.append(f"legend overlaps axes or text: {labels}")
    return {"canvas_px": list(frame.bounds), "legends": result}, errors


@pytest.mark.skipif(not os.environ.get("FIBERLUME_LAYOUT_ARTIFACTS"), reason="Native desktop layout acceptance")
@pytest.mark.parametrize("lang", ["zh", "en"])
def test_native_desktop_layout(qtbot, qapp, tmp_path, abet_xml, monkeypatch, lang):
    backend = qapp.platformName().lower()
    assert backend == {"Windows": "windows", "Darwin": "cocoa"}.get(platform.system())
    artifacts = Path(os.environ["FIBERLUME_LAYOUT_ARTIFACTS"])
    artifacts.mkdir(parents=True, exist_ok=True)
    qapp.setStyle("Fusion")
    qapp.setStyleSheet(STYLE)
    monkeypatch.setattr(main_window, "QSettings", lambda *_: QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    window = main_window.MainWindow(lang)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    paths = make_sample_dataset(tmp_path / "synthetic", n_animals=1, duration_s=150)
    shutil.copyfile(abet_xml, paths[0].with_suffix(".xml"))
    window.add_paths(paths)
    window.out_dir.setText(str(tmp_path / "export"))
    with qtbot.waitExposed(window):
        window.show()
    for page, step in enumerate((window.step_read, window.step_pre, window.step_peth, window.step_events)):
        window.tabs.setCurrentIndex(page)
        step()
        qtbot.waitUntil(lambda: not window._busy, timeout=90000)
        assert not errors, errors
    assert len(window.A.abet_marker_names) >= 6
    assert window.peth is not None and window.events is not None
    available = window.screen().availableGeometry()
    margins = window.windowHandle().frameMargins()
    failures = []
    for requested in ((1024, 720), (1400, 900)):
        width = min(requested[0], available.width() - margins.left() - margins.right() - 12)
        height = min(requested[1], available.height() - margins.top() - margins.bottom() - 12)
        window.move(available.x() + 6, available.y() + 6)
        window.resize(width, height)
        qtbot.wait(150)
        prefix = f"{backend}-{lang}-{requested[0]}x{requested[1]}"
        report = dict(platform=platform.platform(), qt=qVersion(), backend=backend, language=lang,
                      dpr=window.devicePixelRatioF(), logical_dpi=window.screen().logicalDotsPerInch(),
                      requested=list(requested), available=list(available.getRect()),
                      client=[window.width(), window.height()], frame=list(window.frameGeometry().getRect()), pages={})
        issues = []
        if window.width() > width or window.height() > height or not available.contains(window.frameGeometry()):
            issues.append(f"window exceeds requested work area ({width}, {height})")
        for index, (page, buttons, views) in enumerate(PAGES):
            window.tabs.setCurrentIndex(index)
            qtbot.wait(80)
            canvases = {}
            for name in views:
                view = getattr(window, name)
                geometry, collisions = _legend_geometry(view)
                canvases[name] = geometry
                issues.extend(f"{page}/{name}: {issue}" for issue in collisions)
                if name == "fig_raw" and sum(len(row["labels"]) for row in geometry["legends"]) < 6:
                    issues.append("raw preview is missing the ABET marker legend")
            shot = window.grab()
            assert shot.save(str(artifacts / f"{prefix}-{page}.png"))
            reach = {name: _reachable(getattr(window, name)) for name in buttons}
            issues.extend(f"{page}/{name}: unreachable" for name, ok in reach.items() if not ok)
            report["pages"][page] = dict(canvases=canvases, reachable=reach)
        report["errors"] = issues
        report["passed"] = not issues
        (artifacts / f"{prefix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        failures.extend(f"{prefix}: {issue}" for issue in issues)
    assert not failures, "\n".join(failures)
