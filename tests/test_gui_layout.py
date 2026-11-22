"""Native-window acceptance of the five analysis pages. Enable with FIBERLUME_LAYOUT_ARTIFACTS."""
import json
import os
import platform
import shutil
import sys
import threading
from pathlib import Path

import pytest

from matplotlib.legend import Legend
from matplotlib.transforms import Bbox
from PySide6.QtCore import QRect, QSettings, Qt, QTimer, qVersion
from PySide6.QtWidgets import (QApplication, QDialogButtonBox, QLabel, QMessageBox, QPushButton, QScrollArea,
                               QStyle, QStyleOptionButton, QStyleOptionTab, QTabWidget)

from fiberphoto.gui import main_window
from fiberphoto.gui.app import STYLE
from fiberphoto.sample_data import make_sample_dataset


PAGES = (
    ("read", ["b_read", "b_abet", "b_exclude"], ["fig_raw"]),
    ("pre", ["b_pre", "b_choose_windows"], ["fig_dedrift", "fig_deconv"]),
    ("peth", ["b_peth", "b_abet_compare"], ["fig_peth", "fig_peth_dff", "fig_auc"]),
    ("events", ["b_events", "b_export_events"], ["fig_events", "fig_dist", "fig_rate"]),
    ("export", ["b_export", "b_batch", "b_manifest", "b_manifest_template"], []),
)


def _reveal(target):
    """Select all containing tabs and scroll to the target using their normal controls."""
    parent = target.parentWidget()
    scroll_areas = []
    while parent is not None:
        if isinstance(parent, QTabWidget):
            for i in range(parent.count()):
                if parent.widget(i) is target or parent.widget(i).isAncestorOf(target):
                    parent.setCurrentIndex(i)
                    break
        if isinstance(parent, QScrollArea):
            scroll_areas.append(parent)
        parent = parent.parentWidget()
    QApplication.processEvents()
    for area in scroll_areas:
        area.ensureWidgetVisible(target, 1, 1)
    QApplication.processEvents()


def _reachable(button, layout_root):
    _reveal(button)
    if layout_root.isWindow():
        return button.isVisible() and button.visibleRegion().contains(button.rect().adjusted(1, 1, -1, -1))
    # For a large desktop render, test every clipping parent within that virtual
    # desktop. The testing monitor's smaller host window is outside the layout.
    if not button.isVisibleTo(layout_root):
        return False
    parent = button.parentWidget()
    while parent is not None:
        box = QRect(button.mapTo(parent, button.rect().topLeft()), button.size()).adjusted(1, 1, -1, -1)
        if not parent.rect().contains(box):
            return False
        if parent is layout_root:
            return True
        parent = parent.parentWidget()
    return False


def _run_confirmed_preprocessing(window, qapp):
    # Cocoa sheets can suppress the title and do not always appear as
    # activeModalWidget(). Select the Yes button by its platform-neutral role.
    def confirm():
        candidates = [qapp.activeModalWidget(), *qapp.topLevelWidgets()]
        for dialog in candidates:
            if dialog is None or not dialog.isVisible() or dialog.parentWidget() is not window:
                continue
            if isinstance(dialog, QMessageBox):
                button = dialog.button(QMessageBox.Yes)
            else:
                buttons = dialog.findChild(QDialogButtonBox)
                button = buttons.button(QDialogButtonBox.Yes) if buttons else None
            if button is not None:
                button.click()
                return

    def timeout():
        # A native modal event loop can block a Qt timeout. End this opt-in
        # acceptance process with a diagnostic instead of stalling a CI worker.
        print("Timed out after 60 seconds waiting for the preprocessing confirmation dialog",
              file=sys.stderr, flush=True)
        os._exit(124)

    timer = QTimer(window)
    timer.timeout.connect(confirm)
    timer.start(30)
    deadline = threading.Timer(60, timeout)
    deadline.daemon = True
    deadline.start()
    try:
        window.step_pre()
    finally:
        deadline.cancel()
        timer.stop()


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
            for artist in [ax.title, ax._left_title, ax._right_title, ax.xaxis.label, ax.yaxis.label,
                           ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text(),
                           *ax.get_xticklabels(), *ax.get_yticklabels(), *ax.texts]:
                if artist.get_visible() and artist.get_text():
                    occupied.append(artist.get_window_extent(renderer))
            if any(overlap is not None and overlap.width > 1 and overlap.height > 1
                   for overlap in (Bbox.intersection(box, other) for other in occupied)):
                errors.append(f"legend overlaps axes or text: {labels}")
    viewport = view.scroll.viewport()
    canvas_origin = canvas.mapTo(viewport, canvas.rect().topLeft())
    # Matplotlib reports physical pixels from the lower left, Qt uses logical
    # pixels from the upper left. Account for both the scroll position and DPR.
    dpr = canvas.devicePixelRatioF()
    visible_axes = []
    for ax in figure.axes:
        tight = ax.get_tightbbox(renderer)
        if tight is not None and not (frame.x0 - 1 <= tight.x0 <= tight.x1 <= frame.x1 + 1
                                      and frame.y0 - 1 <= tight.y0 <= tight.y1 <= frame.y1 + 1):
            errors.append(f"axis text outside figure: {list(tight.bounds)} in {list(frame.bounds)}")
        left = canvas_origin.x() + ax.bbox.x0 / dpr
        right = canvas_origin.x() + ax.bbox.x1 / dpr
        top = canvas_origin.y() + (frame.y1 - ax.bbox.y1) / dpr
        bottom = canvas_origin.y() + (frame.y1 - ax.bbox.y0) / dpr
        visible_axes.append([max(0., min(right, viewport.width()) - max(left, 0.)),
                             max(0., min(bottom, viewport.height()) - max(top, 0.))])
    return {"canvas_px": list(frame.bounds), "legends": result,
            "viewport_dip": [viewport.width(), viewport.height()],
            "visible_axes_dip": visible_axes}, errors


def _content_geometry(window):
    """Measure native Qt text against the space used to paint it, in logical pixels."""
    measurements, errors = [], []

    def measure(kind, text, available, required):
        geometry = dict(kind=kind, text=text, available=list(available), required=list(required))
        measurements.append(geometry)
        if any(need > have + 1 for need, have in zip(required, available)):
            errors.append(f"clipped {kind}: {text!r} needs {required}, has {available}")

    for button in window.findChildren(QPushButton):
        if not button.text() or not button.isVisibleTo(window):
            continue
        opt = QStyleOptionButton()
        button.initStyleOption(opt)
        content = button.style().subElementRect(QStyle.SE_PushButtonContents, opt, button)
        text = button.fontMetrics().size(Qt.TextShowMnemonic, button.text())
        measure("button", button.text(), (content.width(), content.height()), (text.width(), text.height()))

    page = window.tabs.currentWidget()
    for label in page.findChildren(QLabel):
        if not label.text() or not label.isVisibleTo(window):
            continue
        if label.wordWrap():
            # QLabel accounts for its style-sheet padding and native line wrapping.
            measure("wrapped label", label.text(), (label.width(), label.height()),
                    (0, label.heightForWidth(label.width())))
        else:
            rect = label.contentsRect()
            text = label.fontMetrics().size(Qt.TextExpandTabs, label.text())
            measure("label", label.text(), (rect.width(), rect.height()), (text.width(), text.height()))

    bar = window.tabs.tabBar()
    for i in range(bar.count()):
        opt = QStyleOptionTab()
        bar.initStyleOption(opt, i)
        rect = bar.style().subElementRect(QStyle.SE_TabBarTabText, opt, bar)
        text = bar.fontMetrics().size(Qt.TextShowMnemonic, bar.tabText(i))
        measure("tab", bar.tabText(i), (rect.width(), rect.height()), (text.width(), text.height()))
        if not bar.rect().contains(bar.tabRect(i)):
            errors.append(f"workflow tab outside tab bar: {bar.tabText(i)}")
    return measurements, errors


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
        if page == 1:
            _run_confirmed_preprocessing(window, qapp)
        else:
            step()
        qtbot.waitUntil(lambda: not window._busy, timeout=90000)
        assert not errors, errors
    assert len(window.A.abet_marker_names) >= 6
    assert window.peth is not None and window.events is not None
    available = window.screen().availableGeometry()
    margins = window.windowHandle().frameMargins()
    failures = []
    for requested in ((1024, 720), (1400, 900)):
        compact = requested == (1024, 720)
        width, height = requested
        if compact:
            width = min(width, available.width() - margins.left() - margins.right() - 12)
            height = min(height, available.height() - margins.top() - margins.bottom() - 12)
        window.move(available.x() + 6, available.y() + 6)
        if compact:
            window.resize(width, height)
        else:
            window.setFixedSize(width, height)
        qtbot.wait(150)
        host = None
        if not compact and (width > available.width() or height > available.height()
                            or (window.width(), window.height()) != (width, height)):
            # Cocoa can cap even a fixed-size top-level window at the physical
            # monitor height. A fixed-size child of a native scroll window keeps
            # the production layout at its actual target dimensions.
            host = QScrollArea()
            qtbot.addWidget(host)
            host.resize(min(width, available.width() - margins.left() - margins.right() - 12),
                        min(height, available.height() - margins.top() - margins.bottom() - 12))
            window.hide()
            window.setParent(host.viewport(), Qt.Widget)
            host.setWidget(window)
            window.setFixedSize(width, height)
            window.show()
            with qtbot.waitExposed(host):
                host.show()
            qtbot.wait(150)
        prefix = f"{backend}-{lang}-{requested[0]}x{requested[1]}"
        report = dict(platform=platform.platform(), qt=qVersion(), backend=backend, language=lang,
                      dpr=window.devicePixelRatioF(), logical_dpi=window.screen().logicalDotsPerInch(),
                      requested=list(requested), available=list(available.getRect()),
                      client=[window.width(), window.height()], frame=list(window.frameGeometry().getRect()),
                      inside_desktop=host is None and available.contains(window.frameGeometry()),
                      capture="native scroll host widget grab" if host else "native top-level widget grab",
                      scenario="available desktop" if compact else "large desktop layout",
                      pages={})
        issues = []
        if (window.width(), window.height()) != (width, height):
            issues.append(f"native window did not render at requested client size ({width}, {height})")
        if compact and not available.contains(window.frameGeometry()):
            issues.append(f"window exceeds desktop work area ({width}, {height})")
        for index, (page, buttons, views) in enumerate(PAGES):
            window.tabs.setCurrentIndex(index)
            qtbot.wait(80)
            canvases = {}
            reach = {name: _reachable(getattr(window, name), window) for name in buttons}
            issues.extend(f"{page}/{name}: unreachable" for name, ok in reach.items() if not ok)
            measurements, clipped = _content_geometry(window)
            issues.extend(f"{page}: {error}" for error in clipped)
            if page in ("pre", "peth", "events"):
                for area in window.tabs.currentWidget().findChildren(QScrollArea):
                    if area.widget() and area.widget().isAncestorOf(getattr(window, buttons[0])):
                        if area.horizontalScrollBar().maximum() > 0:
                            issues.append(f"{page}: parameter controls require horizontal scrolling")
            for name in views:
                view = getattr(window, name)
                _reveal(view)
                if not view.isVisibleTo(window):
                    issues.append(f"{page}/{name}: graph cannot be opened")
                geometry, collisions = _legend_geometry(view)
                canvases[name] = geometry
                issues.extend(f"{page}/{name}: {issue}" for issue in collisions)
                if name == "fig_raw" and sum(len(row["labels"]) for row in geometry["legends"]) < 6:
                    issues.append("raw preview is missing the ABET marker legend")
                drawing_width, drawing_height = geometry["visible_axes_dip"][0]
                minimum_width = 96 if name in ("fig_auc", "fig_dist", "fig_rate") else 160
                if drawing_width < minimum_width or drawing_height < 4 * window.fontMetrics().height():
                    issues.append(f"{page}/{name}: insufficient visible plot area: {drawing_width} × {drawing_height}")
            if views:
                _reveal(getattr(window, views[0]))
            shot = window.grab()
            assert shot.deviceIndependentSize().toSize() == window.size()
            assert shot.save(str(artifacts / f"{prefix}-{page}.png"))
            report["pages"][page] = dict(canvases=canvases, reachable=reach, widget_text=measurements)
        report["errors"] = issues
        report["passed"] = not issues
        (artifacts / f"{prefix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        failures.extend(f"{prefix}: {issue}" for issue in issues)
    assert not failures, "\n".join(failures)
