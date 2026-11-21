"""Time-series artists with an interactive, peak-preserving display resolution."""
from copy import copy

import numpy as np
from matplotlib.lines import Line2D


def _visible_indices(time, values, limits, width):
    left, right = sorted(limits)
    start = max(0, time.searchsorted(left) - 1)
    stop = min(len(time), time.searchsorted(right, side="right") + 1)
    block = (stop - start) // max(1, width)
    if block < 3:
        return slice(start, stop)
    # Keep both extrema in time order, including short spikes between screen pixels.
    rows = (stop - start) // block
    tail = start + rows * block
    bins = values[start:tail].reshape(rows, block)
    offsets = start + np.arange(rows) * block
    extrema = np.sort(np.column_stack((bins.argmin(axis=1), bins.argmax(axis=1))), axis=1)
    return np.r_[start, (offsets[:, None] + extrema).ravel(), np.arange(tail, stop), stop - 1]


class TimeTrace(Line2D):
    def __init__(self, time, values, *, spikes=False, **style):
        self.spikes = spikes
        time, values = np.asarray(time), np.asarray(values)
        if spikes:
            visible = values != 0
            time, values = time[visible], values[visible]
        self.time, self.values = time, values
        self.can_reduce = len(time) >= 2048 and np.isfinite(values).all()
        super().__init__(*self._coordinates(time, values), **style)

    def _coordinates(self, time, values):
        if self.spikes:
            # Independent vertical strokes, batched into a single path.
            y = np.empty((len(time), 3))
            y[:, 0], y[:, 1], y[:, 2] = 0, values, np.nan
            return np.repeat(time, 3), y.ravel()
        return time, values

    def draw(self, renderer):
        canvas = self.figure.canvas
        if (not self.can_reduce or not getattr(canvas, "interactive_preview", False)
                or canvas.is_saving() or not self.get_visible()):
            return super().draw(renderer)
        indices = _visible_indices(self.time, self.values, self.axes.get_xlim(), int(self.axes.bbox.width))
        proxy = copy(self)
        proxy.set_data(*self._coordinates(self.time[indices], self.values[indices]))
        Line2D.draw(proxy, renderer)


def plot_trace(ax, time, values, **style):
    line = TimeTrace(time, values, **style)
    ax.add_line(line)
    if line.spikes and len(time):
        ax.update_datalim([(time[0], 0), (time[-1], 0)])
    ax.autoscale_view()
