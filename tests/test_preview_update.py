import hashlib
import io
from zipfile import ZipFile

import numpy as np
import pytest
from matplotlib.backend_bases import MouseEvent
from matplotlib.figure import Figure

from fiberphoto import updates
from fiberlume_boot import activate
from fiberphoto.gui.widgets import FigureView


def test_preview_navigation(qtbot):
    view = FigureView()
    qtbot.addWidget(view)
    fig = Figure()
    top, lower = fig.subplots(2, sharex=True)
    top.plot([1, 10], [1, 4])
    lower.plot([1, 10], [0, 1])
    top.invert_yaxis()
    view.set_figure(fig)
    canvas = view.canvas
    canvas.draw()
    x, y = top.bbox.get_points().mean(axis=0).astype(int)
    origin = top.transData.inverted().transform((x, y))
    original = top.get_xlim(), top.get_ylim()

    def event(name, pos, button, step=0):
        ev = MouseEvent(name, canvas, *pos, button=button, step=step)
        canvas.callbacks.process(name, ev)

    event("button_press_event", (x, y), 3)
    event("motion_notify_event", (x + 25, y + 15), 3)
    event("button_release_event", (x + 25, y + 15), 3)
    assert np.ptp(top.get_xlim()) < np.ptp(original[0])
    assert np.ptp(top.get_ylim()) < np.ptp(original[1])
    assert top.yaxis_inverted()
    np.testing.assert_allclose(top.transData.transform(origin), (x, y))
    np.testing.assert_allclose(top.get_xlim(), lower.get_xlim())
    view.toolbar.home()
    np.testing.assert_allclose(top.get_xlim(), original[0])
    event("button_press_event", (x, y), 1)
    event("button_release_event", (x + 20, y), 1)
    assert top.get_xlim()[0] < original[0][0]
    np.testing.assert_allclose(np.ptp(top.get_xlim()), np.ptp(original[0]))
    event("scroll_event", (x,y), "up", 1)
    assert np.ptp(top.get_xlim()) < np.ptp(original[0])
    view.toolbar.home()
    view.set_figure(None)


def test_preview_keeps_extrema_and_restores_full_trace(qtbot, monkeypatch):
    from fiberphoto.display_traces import plot_trace
    from matplotlib.lines import Line2D

    view = FigureView()
    qtbot.addWidget(view)
    fig = Figure()
    ax = fig.subplots()
    t = np.arange(60000)
    values = np.zeros(len(t))
    values[1234:1236] = [-7, 11]
    plot_trace(ax, t, values, label="source")
    view.set_figure(fig)
    cv = view.canvas
    cv.draw()
    seen = []
    original = Line2D.draw
    def capture(line, renderer):
        if line.get_label() == "source":
            seen.append(np.asarray(line.get_ydata()))
        return original(line, renderer)
    monkeypatch.setattr(Line2D, "draw", capture)
    x, y = ax.bbox.get_points().mean(axis=0).astype(int)
    cv.callbacks.process("scroll_event", MouseEvent("scroll_event", cv, x, y, step=-1))
    cv.draw()
    assert len(seen[-1]) < len(t) // 5
    assert (seen[-1].min(), seen[-1].max()) == (-7, 11)
    import io
    fig.savefig(io.BytesIO(), format="png")
    np.testing.assert_array_equal(seen[-1], values)
    qtbot.waitUntil(lambda: not cv.interactive_preview)
    cv.draw()
    np.testing.assert_array_equal(seen[-1], values)
    np.testing.assert_array_equal(ax.lines[0].get_ydata(), values)


def test_patch_integrity_compatibility_and_activation(tmp_path, monkeypatch):
    import fiberlume_boot as boot
    import sys
    package = io.BytesIO()
    with ZipFile(package, "w") as z:
        z.writestr("fiberphoto/__init__.py", '__version__ = "0.6.1"\n')
    blob = package.getvalue()
    root = tmp_path / "0.6.0"
    monkeypatch.setattr(updates, "patch_root", lambda _: root)
    monkeypatch.setattr(updates, "fetch", lambda *_: blob)
    manifest = {"version": "0.6.1", "base": "0.6.0", "sha256": "0"*64}
    with pytest.raises(ValueError, match="SHA-256"):
        updates.stage(manifest, "0.6.0", "0.6.0")
    assert not root.exists()
    manifest["sha256"] = hashlib.sha256(blob).hexdigest()
    with pytest.raises(ValueError, match="Incompatible"):
        updates.stage(manifest, "0.5.0", "0.5.0")
    for _ in range(2):
        assert updates.stage(manifest, "0.6.0", "0.6.0") == "0.6.1"
    monkeypatch.setattr(boot, "base_version", lambda: "0.6.0")
    monkeypatch.setattr(sys, "path", sys.path.copy())
    assert activate(tmp_path) == root / "0.6.1"
    (root / "0.6.1/.disabled").touch()
    assert activate(tmp_path) is None


def test_manual_update_menu(qtbot, monkeypatch):
    import json
    from PySide6.QtWidgets import QMessageBox
    from fiberphoto import __version__
    from fiberphoto.gui.main_window import MainWindow
    import fiberlume_boot

    window = MainWindow(lang="zh")
    qtbot.addWidget(window)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda _, title, msg: messages.append(msg))
    base = __version__.rsplit(".", 1)[0] + ".0"
    monkeypatch.setattr(fiberlume_boot, "base_version", lambda: base)
    manifest = {"version": __version__, "base": base}
    monkeypatch.setattr(updates, "fetch", lambda *_: json.dumps(manifest).encode())
    assert window.a_update in window.m_help.actions()
    window.a_update.trigger()
    qtbot.waitUntil(lambda: len(messages) == 1)
    assert __version__ in messages[0] and "已是当前版本" in messages[0]
    manifest.update(version="1.0.1", base="1.0.0", sha256="a" * 64)
    assert updates.check("0.6.0", "0.6.0")[0] == "installer"
    assert updates.check("1.0.0", "1.0.0")[0] == "patch"
