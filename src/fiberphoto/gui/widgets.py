"""Reusable widgets: parameter forms, embedded figures and a background worker."""
from __future__ import annotations

import traceback
from typing import Any, Callable

import numpy as np
from matplotlib.backend_bases import MouseButton
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from ..config import PipelineConfig
from .texts import ParamSpec


class OptionalFloat(QWidget):
    """A spin box with an on/off checkbox, for parameters where None means 'off'."""

    def __init__(self, spec: ParamSpec):
        super().__init__()
        self.check = QCheckBox()
        self.spin = QDoubleSpinBox()
        self.spin.setRange(spec.min, spec.max)
        self.spin.setSingleStep(spec.step)
        self.spin.setDecimals(spec.decimals)
        self.spin.setSuffix(spec.suffix)
        self.off = QLabel()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.check)
        lay.addWidget(self.spin, 1)
        lay.addWidget(self.off)
        self.check.toggled.connect(self._sync)
        self.check.setChecked(True)

    def _sync(self, on: bool):
        self.spin.setVisible(on)
        self.off.setVisible(not on)

    def value(self):
        if not self.check.isChecked() or self.spin.value() <= self.spin.minimum() and self.spin.specialValueText():
            return None
        return self.spin.value()

    def setValue(self, v):
        self.check.setChecked(v is not None)
        if v is not None:
            self.spin.setValue(float(v))
        self._sync(v is not None)


class ParamForm(QWidget):
    """A form generated from ParamSpecs, bound to sections of a PipelineConfig."""

    changed = Signal()

    def __init__(self, specs: list[ParamSpec], parent=None, *, sections=()):
        super().__init__(parent)
        self.specs = specs
        # All sections share the same editors and dependency bindings. Only
        # their presentation is separated, so saving/loading remains atomic.
        self.sections = [QWidget() for _ in sections]
        forms = [QFormLayout(page) for page in (self, *self.sections)]
        for form in forms:
            form.setContentsMargins(4, 4, 4, 4)
            form.setVerticalSpacing(4)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setFormAlignment(Qt.AlignTop | Qt.AlignLeft)
        field_sections = {key: index + 1 for index, keys in enumerate(sections) for key in keys}
        self.labels: dict[str, QLabel] = {}
        self.editors: dict[str, QWidget] = {}
        for s in specs:
            ed = self._make_editor(s)
            lab = QLabel()
            self.labels[s.name] = lab
            self.editors[s.name] = ed
            forms[field_sections.get(s.name, 0)].addRow(lab, ed)
        self.changed.connect(self._apply_depends)

    def _make_editor(self, s: ParamSpec) -> QWidget:
        if s.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(s.min, s.max)
            w.setSingleStep(s.step)
            w.setDecimals(s.decimals)
            w.setSuffix(s.suffix)
            w.valueChanged.connect(self.changed)
        elif s.kind == "int":
            w = QSpinBox()
            w.setRange(int(s.min), int(s.max))
            w.setSuffix(s.suffix)
            w.valueChanged.connect(self.changed)
        elif s.kind == "bool":
            w = QCheckBox()
            w.toggled.connect(self.changed)
        elif s.kind == "choice":
            w = QComboBox()
            w.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            for value, _ in s.choices:
                w.addItem("", value)
            w.currentIndexChanged.connect(self.changed)
        elif s.kind == "optfloat":
            w = OptionalFloat(s)
            w.spin.valueChanged.connect(self.changed)
            w.check.toggled.connect(self.changed)
        else:
            raise ValueError(s.kind)
        return w

    def _current(self, name: str):
        ed = self.editors[name]
        return ed.currentData() if isinstance(ed, QComboBox) else (
            ed.isChecked() if isinstance(ed, QCheckBox) else ed.value())

    def _apply_depends(self):
        for s in self.specs:
            if s.depends:
                other, allowed = s.depends
                on = self._current(other) in allowed
                self.editors[s.name].setEnabled(on)
                self.labels[s.name].setEnabled(on)

    def set_lang(self, lang: str):
        for s in self.specs:
            lab, ed = self.labels[s.name], self.editors[s.name]
            lab.setText(s.label[lang])
            tip = s.tip[lang]
            lab.setToolTip(tip)
            ed.setToolTip(tip)
            if s.kind == "choice":
                for i, (_, text) in enumerate(s.choices):
                    ed.setItemText(i, text[lang])
            if s.kind == "optfloat" and s.off_label:
                ed.off.setText(s.off_label[lang])

    def load(self, cfg: PipelineConfig):
        for s in self.specs:
            v = getattr(getattr(cfg, s.section), s.name)
            ed = self.editors[s.name]
            ed.blockSignals(True)
            if s.kind in ("float", "int"):
                ed.setValue(v)
            elif s.kind == "bool":
                ed.setChecked(bool(v))
            elif s.kind == "choice":
                i = ed.findData(v)
                ed.setCurrentIndex(max(i, 0))
            elif s.kind == "optfloat":
                ed.setValue(v)
            ed.blockSignals(False)
        self._apply_depends()

    def store(self, cfg: PipelineConfig):
        for s in self.specs:
            ed = self.editors[s.name]
            if s.kind == "float":
                v = ed.value()
            elif s.kind == "int":
                v = int(ed.value())
            elif s.kind == "bool":
                v = ed.isChecked()
            elif s.kind == "choice":
                v = ed.currentData()
            else:
                v = ed.value()
            setattr(getattr(cfg, s.section), s.name, v)


class PreviewCanvas(FigureCanvasQTAgg):
    """Direct manipulation in display coordinates, including log and inverted axes."""

    editRequested = Signal(object)

    def __init__(self, figure):
        super().__init__(figure)
        self.interactive_preview = False
        self._frame = QTimer(self)
        self._frame.setSingleShot(True)
        self._frame.timeout.connect(self.draw_idle)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._finish_preview)
        self._drag = None
        self._moved = False
        self.mpl_connect("button_press_event", self._press)
        self.mpl_connect("motion_notify_event", self._move)
        self.mpl_connect("button_release_event", self._release)
        self.mpl_connect("scroll_event", self._scroll)
        self.setToolTip("双击编辑文字或颜色。左键或中键平移，右键拖动缩放，滚轮缩放，Home 恢复全图。\n"
                        "Double-click to edit text or colors. Left/middle drag to pan, right drag to scale, scroll to zoom.")

    def draw(self):
        engine = self.figure.get_layout_engine()
        if not self.interactive_preview or engine is None or self.is_saving():
            return super().draw()
        # Axes positions stay fixed throughout a gesture. Reflow labels once it ends.
        self.figure.set_layout_engine("none")
        try:
            return super().draw()
        finally:
            self.figure.set_layout_engine(engine)

    def _preview_frame(self):
        self.interactive_preview = True
        if not self._frame.isActive():
            self._frame.start(16)

    def _finish_preview(self):
        if self.interactive_preview:
            self.interactive_preview = False
            self._frame.stop()
            self._settle.stop()
            self.draw_idle()

    def _target(self, event):
        if self.toolbar is None or self.toolbar.mode or not self.widgetlock.available(self):
            return None
        axes = [ax for ax in self.figure.axes if ax.get_visible() and ax.get_navigate()
                and ax.name == "rectilinear" and getattr(ax, "_colorbar", None) is None]
        if event.inaxes in axes:
            return event.inaxes, (True, True)
        if event.inaxes is None:
            renderer = self.get_renderer()
            for ax in axes:
                for axis, mask in ((ax.xaxis, (True, False)), (ax.yaxis, (False, True))):
                    box = axis.get_tightbbox(renderer)
                    if box is not None and box.contains(event.x, event.y):
                        return ax, mask
        return None

    def _press(self, event):
        if event.dblclick and event.button == MouseButton.LEFT and self.toolbar and not self.toolbar.mode and self.widgetlock.available(self):
            self._drag = None
            self.editRequested.emit(event)
            return
        if self.toolbar and self.toolbar.mode == "pan/zoom" and event.inaxes is not None:
            self._settle.stop()
            self._preview_frame()
            return
        target = self._target(event)
        if not target or event.button not in (MouseButton.LEFT, MouseButton.MIDDLE, MouseButton.RIGHT):
            return
        ax, mask = target
        self._settle.stop()
        self._drag = (ax, mask, event.button, np.array([event.x, event.y]),
                      ax.bbox.get_points().copy(), ax.transData.frozen().inverted())
        self._moved = False

    def _limits(self, ax, mask, pixels, inverse):
        limits = inverse.transform(pixels)
        if not np.isfinite(limits).all() or np.any(limits[0] == limits[1]):
            return
        if mask[0]:
            ax.set_xlim(limits[:, 0])
        if mask[1]:
            ax.set_ylim(limits[:, 1])
        self._preview_frame()

    def _move(self, event):
        if self._drag is None:
            return
        ax, mask, button, anchor, bounds, inverse = self._drag
        delta = np.array([event.x, event.y]) - anchor
        if not self._moved:
            if max(abs(delta)) < 3 * self.device_pixel_ratio:
                return
            if self.toolbar._nav_stack() is None:
                self.toolbar.push_current()
            self._moved = True
        if button == MouseButton.RIGHT:
            # Matplotlib display y increases upwards; use logical screen pixels.
            factor = 1.02 ** np.clip(-delta / self.device_pixel_ratio, -500, 500)
            pixels = anchor + (bounds - anchor) * factor
        else:
            pixels = bounds - delta
        self._limits(ax, mask, pixels, inverse)

    def _release(self, event):
        if self._drag is None:
            self._finish_preview()
            return
        if event.button == self._drag[2]:
            self._move(event)
            if self._moved:
                self.toolbar.push_current()
            self._drag = None
            self._moved = False
            self._finish_preview()

    def _scroll(self, event):
        target = self._target(event)
        if not target or not event.step:
            return
        ax, mask = target
        anchor = np.array([event.x, event.y])
        factor = 1.02 ** np.clip(-15 * event.step, -500, 500)
        if self.toolbar._nav_stack() is None:
            self.toolbar.push_current()
        pixels = anchor + (ax.bbox.get_points() - anchor) * factor
        self._limits(ax, mask, pixels, ax.transData.inverted())
        self._settle.start(140)
        self.toolbar.push_current()


class FigureView(QWidget):
    """A matplotlib figure with the zoom/pan toolbar; the figure can be swapped."""

    previewSizeChanged = Signal()

    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = None
        self.toolbar = None
        self.plot_size = QSize()
        self.figure_edits = {}
        self.original_appearance = {}
        self.edit_language = "zh"
        self.placeholder = QLabel(placeholder)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.placeholder.setStyleSheet("color: #70716c;")
        self.lay.addWidget(self.placeholder, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.lay.addWidget(self.scroll, 1)
        self.scroll.hide()

    def set_placeholder(self, text: str):
        self.placeholder.setText(text)

    def set_figure(self, fig: Figure | None):
        if self.toolbar is not None:
            self.lay.removeWidget(self.toolbar)
            self.toolbar.setParent(None)
            self.toolbar.deleteLater()
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.canvas = self.toolbar = None
        if fig is None:
            self.plot_size = QSize()
            self.scroll.hide()
            self.placeholder.show()
            self.previewSizeChanged.emit()
            return
        self.placeholder.hide()
        from ..figure_style import apply_figure_edits
        from .figure_editor import edit_figure
        self.original_appearance.clear()
        apply_figure_edits(fig, self.figure_edits, self.original_appearance)
        self.canvas = PreviewCanvas(fig)
        # Keep tick labels, legends and data readable even in a compact splitter pane.
        # These sizes are Qt logical pixels, independent of the backing display scale.
        # A wide exported time series can reflow into a single narrow column.
        # Reserve extra space only for additional plot columns and the actual legend.
        columns = {ax.get_subplotspec().colspan.start for ax in fig.axes
                   if ax.get_subplotspec() is not None and ax.get_label() != "<colorbar>"}
        width = 255 if len(fig.axes) == 1 else 330
        if len(columns) > 1:
            width = 450
        renderer = self.canvas.get_renderer()
        for legend in fig.legends:
            width = max(width, round(legend.get_window_extent(renderer).width /
                                     self.canvas.device_pixel_ratio) + 20)
        self.plot_size = QSize(width, max(195, round(77 * fig.get_figheight())))
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        edit = self.toolbar.addAction("Edit" if self.edit_language == "en" else "编辑")
        edit.setToolTip("Edit figure text and colors" if self.edit_language == "en" else "编辑图中文字和颜色")
        edit.triggered.connect(lambda: edit_figure(self))
        self.canvas.editRequested.connect(lambda event: edit_figure(self, event))
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        self.lay.insertWidget(0, self.toolbar)
        self.scroll.setWidget(self.canvas)
        self.scroll.show()
        self._fit_canvas()
        self.previewSizeChanged.emit()
        self.canvas.draw_idle()

    def preferredPaneSize(self):
        if not self.canvas:
            return self.minimumSizeHint()
        return self.plot_size + QSize(0, self.toolbar.sizeHint().height() + self.lay.spacing())

    def _fit_canvas(self):
        if self.canvas is None or self.toolbar is None:
            return
        # Choose split panes using the preferred size first. A single visible
        # chart may be slightly shorter; Matplotlib reflows its constrained layout.
        available = self.height() - self.toolbar.sizeHint().height() - self.lay.spacing()
        minimum_height = self.plot_size.height()
        parent = self.parentWidget()
        while parent and not isinstance(parent, QMainWindow):
            parent = parent.parentWidget()
        compact_window = parent and (parent.width() < 1100 or parent.height() < 600)
        # Extremely small desktops retain a scrollable full drawing so a pair
        # of stacked axes still has room for labels and a readable trace.
        if available >= (395 if compact_window else 250):
            minimum_height = min(minimum_height, available)
        self.canvas.setMinimumSize(self.plot_size.width(), minimum_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_canvas()


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)
    progress = Signal(float)


class Worker(QRunnable):
    """Run ``fn(log=…, progress=…)`` on the thread pool and report back via signals."""

    def __init__(self, fn: Callable[..., Any], pass_callbacks: bool = False):
        super().__init__()
        self.fn = fn
        self.pass_callbacks = pass_callbacks
        self.signals = WorkerSignals()

    def run(self):
        try:
            if self.pass_callbacks:
                out = self.fn(log=self.signals.log.emit, progress=self.signals.progress.emit)
            else:
                out = self.fn()
        except Exception as e:  # report to the GUI instead of crashing
            self.signals.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
            return
        self.signals.finished.emit(out)
