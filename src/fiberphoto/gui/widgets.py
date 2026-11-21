"""Reusable widgets: parameter forms, embedded figures and a background worker."""
from __future__ import annotations

import traceback
from typing import Any, Callable

import numpy as np
from matplotlib.backend_bases import MouseButton
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QSpinBox, QVBoxLayout, QWidget)

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

    def __init__(self, specs: list[ParamSpec], parent=None):
        super().__init__(parent)
        self.specs = specs
        self.form = QFormLayout(self)
        self.form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.labels: dict[str, QLabel] = {}
        self.editors: dict[str, QWidget] = {}
        for s in specs:
            ed = self._make_editor(s)
            lab = QLabel()
            self.labels[s.name] = lab
            self.editors[s.name] = ed
            self.form.addRow(lab, ed)
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

    def __init__(self, figure):
        super().__init__(figure)
        self._drag = None
        self._moved = False
        self.mpl_connect("button_press_event", self._press)
        self.mpl_connect("motion_notify_event", self._move)
        self.mpl_connect("button_release_event", self._release)
        self.mpl_connect("scroll_event", self._scroll)
        self.setToolTip("左键或中键平移，右键拖动缩放，滚轮缩放，Home 恢复全图。\n"
                        "Left/middle drag to pan, right drag to scale, scroll to zoom. Home resets.")

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
        target = self._target(event)
        if not target or event.button not in (MouseButton.LEFT, MouseButton.MIDDLE, MouseButton.RIGHT):
            return
        ax, mask = target
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
        self.draw_idle()

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
        if self._drag and event.button == self._drag[2]:
            self._move(event)
            if self._moved:
                self.toolbar.push_current()
            self._drag = None
            self._moved = False

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
        self.toolbar.push_current()


class FigureView(QWidget):
    """A matplotlib figure with the zoom/pan toolbar; the figure can be swapped."""

    def __init__(self, parent=None, placeholder: str = ""):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.canvas = None
        self.toolbar = None
        self.placeholder = QLabel(placeholder)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("color: #8a8984; font-size: 13px;")
        self.lay.addWidget(self.placeholder, 1)

    def set_placeholder(self, text: str):
        self.placeholder.setText(text)

    def set_figure(self, fig: Figure | None):
        for w in (self.toolbar, self.canvas):
            if w is not None:
                self.lay.removeWidget(w)
                w.setParent(None)
                w.deleteLater()
        self.canvas = self.toolbar = None
        if fig is None:
            self.placeholder.show()
            return
        self.placeholder.hide()
        self.canvas = PreviewCanvas(fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        self.toolbar.setIconSize(self.toolbar.iconSize() * 0.8)
        self.lay.addWidget(self.toolbar)
        self.lay.addWidget(self.canvas, 1)
        self.canvas.draw_idle()


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
