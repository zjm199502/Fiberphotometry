"""Choose the analyzed signal interval and an optional same-channel baseline on a shared clock."""
from dataclasses import replace

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QMessageBox, QPushButton, QWidget

from .. import plotting
from ..preprocess import signal_interval_baseline, signal_interval_slice


COLORS = {"signal": "#277fac", "baseline": "#c78616"}


class BaselinePicker(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.button = QPushButton(clicked=lambda: self.toggle("baseline"))
        self.signal_button = QPushButton(clicked=lambda: self.toggle("signal"))
        self.whole = QPushButton(clicked=lambda: self.clear_interval("signal"))
        self.no_baseline = QPushButton(clicked=lambda: self.clear_interval("baseline"))
        self.go_pre = QPushButton(clicked=lambda: self.host.tabs.setCurrentIndex(1))
        self.note, self.signal_note = QLabel(), QLabel()
        row = QGridLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        for i, (pick, clear, note) in enumerate(((self.signal_button, self.whole, self.signal_note),
                                                (self.button, self.no_baseline, self.note))):
            note.setWordWrap(True)
            color = COLORS["signal" if i == 0 else "baseline"]
            note.setStyleSheet(f"border-left: 3px solid {color}; padding-left: 6px;")
            row.addWidget(pick, i, 0)
            row.addWidget(clear, i, 1)
            row.addWidget(note, i, 2)
        row.setColumnStretch(2, 1)
        row.addWidget(self.go_pre, 0, 3, 2, 1)
        self.canvas = None
        self.kind = None
        self.callbacks = []
        self.first = None
        self.drag_x = None
        self.second_click = False
        self.guide = None
        self.spans = {}
        self.temporary_raw = False
        self.previous_source = None

    def tr2(self, zh, en):
        return zh if self.host.lang == "zh" else en

    def bind(self, canvas):
        self._disconnect()
        self.temporary_raw = False
        self.spans = {}
        self.canvas = canvas
        self.refresh()

    def refresh(self):
        raw = self.host.A
        ready = self.canvas is not None and raw is not None and not self.host._busy
        fields = self.host.form_dedrift.editors
        cancel = self.tr2("取消框选", "Cancel selection")
        self.button.setText(cancel if self.kind == "baseline" else self.tr2("框选基线区间", "Select baseline interval"))
        self.signal_button.setText(cancel if self.kind == "signal" else self.tr2("框选信号区间", "Select signal interval"))
        self.no_baseline.setText(self.tr2("不选基线", "No baseline interval"))
        self.whole.setText(self.tr2("整段记录", "Use whole recording"))
        self.go_pre.setText(self.tr2("进入预处理", "Go to preprocessing"))
        self.button.setEnabled(ready and self.kind != "signal")
        self.signal_button.setEnabled(ready and self.kind != "baseline")
        selected_baseline = fields["method"].currentData() == "signal_interval"
        selected_signal = any(fields[k].value() is not None for k in ("signal_start_s", "signal_end_s"))
        self.no_baseline.setEnabled(ready and selected_baseline and not self.kind)
        self.whole.setEnabled(ready and selected_signal and not self.kind)
        self.go_pre.setEnabled(ready and not self.kind)
        if self.kind:
            return
        self.note.clear()
        self.signal_note.clear()
        for artist in self.spans.values():
            artist.remove()
        self.spans.clear()
        if self.canvas is None or raw is None:
            return
        for kind, selected, label in (("signal", selected_signal, self.signal_note), ("baseline", selected_baseline, self.note)):
            if not selected:
                label.setText(self.tr2("使用整段记录", "Whole recording") if kind == "signal" else
                              self.tr2("未指定手动基线", "No manual baseline"))
                continue
            start = fields[f"{kind}_start_s"].value()
            end = fields[f"{kind}_end_s"].value()
            try:
                _, info = self._describe(kind, start, end)
            except ValueError as exc:
                label.setText(str(exc).split(" / ")[0 if self.host.lang == "zh" else -1])
                continue
            label.setText(info)
            self.spans[kind] = self.canvas.figure.axes[0].axvspan(start, end, color=COLORS[kind], alpha=0.20, zorder=0)
        self.canvas.draw_idle()

    def _describe(self, kind, start, end):
        raw = self.host.A
        if kind == "baseline":
            f0, count = signal_interval_baseline(raw, start, end)
            info = f"{raw.channel_names['signal']} · {start:g}–{end:g} s · F0 = {f0:.6g} · n = {count:,}"
            return self.tr2("将此区间原始荧光的中位数用作 ΔF/F 基线？", "Use the median raw fluorescence in this interval as the ΔF/F baseline?"), info
        frames = signal_interval_slice(raw, start, end)
        count = len(raw.time[frames])
        return self.tr2("使用此区间的信号进行分析？", "Use this signal interval for analysis?"), f"{start:g}–{end:g} s · n = {count:,}"

    def _show_raw(self, force_raw):
        xlim = self.canvas.figure.axes[0].get_xlim()
        raw = self.host.A
        if force_raw:
            raw = replace(raw, channel_names={**raw.channel_names, "preview_signal": raw.channel_names["signal"]})
        fig = plotting.fig_raw(raw, self.host.lang)
        fig.axes[0].set_xlim(xlim)
        plt.close(fig)
        fig.set_layout_engine("tight")
        self.host.fig_raw.set_figure(fig)
        self.canvas = self.host.fig_raw.canvas
        self.spans.clear()
        self.refresh()

    def toggle(self, kind="baseline"):
        if self.kind:
            self.stop()
            return
        raw = self.host.A
        if raw is None or self.canvas is None or self.host._busy:
            return
        note = self.note if kind == "baseline" else self.signal_note
        if self.canvas.toolbar.mode:
            note.setText(self.tr2("先关闭图表工具栏中的平移或缩放模式。", "Turn off toolbar pan or zoom first."))
            return
        names = raw.channel_names
        if kind == "baseline" and names.get("preview_signal") and names.get("preview_signal") == names.get("exported_dff"):
            self._show_raw(True)
            self.temporary_raw = True
        self.canvas.widgetlock(self)
        self.canvas.setCursor(Qt.CrossCursor)
        self.kind = kind
        for name, callback in (("button_press_event", self._press), ("motion_notify_event", self._move),
                               ("button_release_event", self._release)):
            self.callbacks.append(self.canvas.mpl_connect(name, callback))
        self.refresh()
        note.setText(self.tr2("在上方信号曲线上按住左键框选，或依次点击两个端点。", "Drag across the top signal plot, or click two endpoints."))

    def _time_at(self, event):
        if event.inaxes is self.canvas.figure.axes[0] and event.xdata is not None:
            return float(np.clip(event.xdata, self.host.A.time[0], self.host.A.time[-1]))
        return None

    def _press(self, event):
        t = self._time_at(event)
        if event.button != 1 or t is None:
            return
        self.drag_x = event.x
        self.second_click = self.first is not None
        if self.first is None:
            self.first = t
            ax = event.inaxes
            self.guide = Rectangle((t, 0), 0, 1, transform=ax.get_xaxis_transform(),
                                   facecolor=COLORS[self.kind], edgecolor=COLORS[self.kind], alpha=.4, zorder=3)
            ax.add_patch(self.guide)
        self._move(event)

    def _move(self, event):
        t = self._time_at(event)
        if self.first is not None and t is not None:
            self.guide.set_x(min(t, self.first))
            self.guide.set_width(abs(t - self.first))
            self.canvas._preview_frame()

    def _release(self, event):
        t = self._time_at(event)
        if event.button != 1 or self.drag_x is None:
            return
        moved = abs(event.x - self.drag_x) >= 4 * self.canvas.device_pixel_ratio
        self.drag_x = None
        self.canvas._finish_preview()
        if t is None or not (self.second_click or moved):
            return
        fields = self.host.form_dedrift.editors
        control = fields[f"{self.kind}_start_s"]
        digits = (control.spin if hasattr(control, "spin") else control).decimals()
        start, end = sorted(round(value, digits) for value in (self.first, t))
        try:
            message, info = self._describe(self.kind, start, end)
        except ValueError as exc:
            note = self.note if self.kind == "baseline" else self.signal_note
            self.stop()
            note.setText(str(exc).split(" / ")[0 if self.host.lang == "zh" else -1])
            return
        if QMessageBox.question(self, self.tr2("确认选区", "Confirm interval"), message + "\n" + info,
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            self.host._store_dedrift()
            fields[f"{self.kind}_start_s"].setValue(start)
            fields[f"{self.kind}_end_s"].setValue(end)
            if self.kind == "baseline":
                if fields["method"].currentData() != "signal_interval":
                    self.previous_source = fields["method"].currentData(), fields["prefer_plexon_dff"].isChecked()
                fields["prefer_plexon_dff"].setChecked(False)
                fields["method"].setCurrentIndex(fields["method"].findData("signal_interval"))
            self.host._store_dedrift()
        self.stop()

    def clear_interval(self, kind):
        fields = self.host.form_dedrift.editors
        if kind == "signal":
            fields["signal_start_s"].setValue(None)
            fields["signal_end_s"].setValue(None)
        else:
            method, direct = self.previous_source or ("isosbestic", fields["prefer_plexon_dff"].isChecked())
            fields["method"].setCurrentIndex(fields["method"].findData(method))
            fields["prefer_plexon_dff"].setChecked(direct)
            self.previous_source = None
        self.host._store_dedrift()
        self.refresh()

    def _disconnect(self):
        if self.kind:
            for cid in self.callbacks:
                self.canvas.mpl_disconnect(cid)
            self.callbacks.clear()
            self.canvas.widgetlock.release(self)
            self.canvas._finish_preview()
            self.canvas.unsetCursor()
        if self.guide is not None:
            self.guide.remove()
        self.guide = self.first = self.drag_x = self.kind = None

    def stop(self):
        self._disconnect()
        if self.temporary_raw:
            self.temporary_raw = False
            self._show_raw(False)
        self.refresh()
