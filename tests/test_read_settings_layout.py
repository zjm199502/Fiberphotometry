"""Exercise the import form at real desktop sizes, with optional Qt window captures."""
import os
from pathlib import Path

import numpy as np
import pytest
from nptdms import ChannelObject, GroupObject, RootObject, TdmsWriter
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea, QTabWidget

from fiberphoto.gui import main_window
from fiberphoto.gui.app import STYLE
from fiberphoto.sample_data import simulate


@pytest.fixture(scope="module")
def multichannel_recording(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic_channels") / "Mouse01_session1.tdms"
    sample = simulate(duration_s=120, fs=50, seed=7)
    entries = [RootObject(), GroupObject("Fiber1"),
               ChannelObject("Fiber1", "470nm", sample["signal"]),
               ChannelObject("Fiber1", "405nm", sample["control"])]
    for i in range(12):
        trace = np.roll(sample["ttl_stim"], 10 * i)
        entries.append(ChannelObject("Fiber1", f"DI{i + 1:02d}_Stim", trace))
    with TdmsWriter(path) as writer:
        writer.write_segment(entries)
    return path


def _ancestors(widget):
    parent = widget.parentWidget()
    while parent is not None:
        yield parent
        parent = parent.parentWidget()


def _show_control(widget):
    parents = list(_ancestors(widget))
    for parent in reversed(parents):
        if isinstance(parent, QTabWidget):
            for i in range(parent.count()):
                if parent.widget(i).isAncestorOf(widget):
                    parent.setCurrentIndex(i)
                    break
    QApplication.processEvents()
    for parent in parents:
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(widget, 2, 2)
    QApplication.processEvents()
    assert widget.isVisible() and widget.visibleRegion().contains(widget.rect().adjusted(1, 1, -1, -1))


def _capture(window, name):
    directory = os.environ.get("FIBERLUME_READ_LAYOUT_ARTIFACTS")
    if directory:
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(folder / f"{name}.png"))


@pytest.mark.parametrize("lang", ["zh", "en"])
@pytest.mark.parametrize("size", [(1400, 930), (1400, 900), (1366, 768), (980, 680)])
def test_read_settings_layout(qtbot, qapp, tmp_path, monkeypatch, multichannel_recording, lang, size):
    monkeypatch.setattr(main_window, "QSettings",
                        lambda *_: QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat))
    old_style = qapp.styleSheet()
    old_theme = qapp.style().objectName()
    qapp.setStyle("Fusion")
    qapp.setStyleSheet(STYLE)
    window = main_window.MainWindow(lang)
    qtbot.addWidget(window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    try:
        window.setFixedSize(*size)
        window.show()
        window.add_paths([multichannel_recording])
        qtbot.waitUntil(lambda: window.isVisible() and window.chan_table.rowCount() == 14)
        assert (window.width(), window.height()) == size
        page = window.tabs.widget(0)
        _show_control(window.b_read)
        messages = []
        monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
        qtbot.mouseClick(window.b_read, Qt.LeftButton)
        assert messages and window.A is None and window.lbl_fs_missing.isVisible()
        for field in (window.fs_edit, window.marker_file, window.b_marker_file, window.b_marker_clear,
                      window.b_exclude, window.b_restore, window.b_read):
            # A short window may scroll the page. Mapping controls should never be
            # clipped behind a second vertical scroller inside that page.
            _show_control(field)
            inner = [area for area in _ancestors(field) if isinstance(area, QScrollArea)
                     and area is not page and area.verticalScrollBar().maximum() > 0]
            assert not inner, f"Nested settings scroller clips {field} at {size} ({lang})"
        prefix = f"{lang}-{size[0]}x{size[1]}"
        _show_control(window.marker_file)
        if os.environ.get("FIBERLUME_READ_LAYOUT_ARTIFACTS"):
            print(prefix, "Qt", qapp.platformName(), "page scroll maximum", page.verticalScrollBar().maximum(),
                  "single settings/preview tab", bool(window.read_panels.compact.count()),
                  "single channels/mapping tab", bool(window.channel_panels.compact.count()), flush=True)
            if size[0] >= 1366:
                assert page.verticalScrollBar().maximum() == 0
        _capture(window, prefix + "-settings")
        _show_control(window.fs_edit)
        window.fs_edit.spin.setValue(50.0)
        _show_control(window.b_read)
        qtbot.mouseClick(window.b_read, Qt.LeftButton)
        qtbot.waitUntil(lambda: not window._busy and (bool(errors) or window.A is not None), timeout=20000)
        assert not errors
        assert window.A.fs == pytest.approx(50.0)
        assert window.fig_raw.canvas is not None
        assert window.fig_raw.isVisible() and window.fig_raw.scroll.viewport().height() > 60
        # Follow the preprocessing page's user-facing shortcut back to the
        # interval controls after the application has switched to the preview.
        _show_control(window.b_choose_windows)
        qtbot.mouseClick(window.b_choose_windows, Qt.LeftButton)
        qtbot.waitUntil(window.baseline_picker.button.isVisible)
        _show_control(window.baseline_picker.button)
        qtbot.mouseClick(window.baseline_picker.button, Qt.LeftButton)
        assert window.baseline_picker.kind == "baseline" and window.fig_raw.canvas.isVisible()
        _capture(window, prefix + "-preview")
        window.baseline_picker.stop()
    finally:
        window.close()
        qapp.setStyle(old_theme)
        qapp.setStyleSheet(old_style)
