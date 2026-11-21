"""Small channel thumbnails so users can tell unnamed channels apart."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from nptdms import TdmsFile
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

N_POINTS = 400


def preview_trace(x: np.ndarray) -> tuple[np.ndarray, bool]:
    if not len(x) or not np.isfinite(x).any():
        return np.array([]), True
    flat = np.nanmax(x) - np.nanmin(x) < 1e-12
    if len(x) > N_POINTS:
        width = (len(x) + N_POINTS // 2 - 1) // (N_POINTS // 2)
        starts = np.arange(0, len(x), width)
        x = np.column_stack((np.fmin.reduceat(x, starts), np.fmax.reduceat(x, starts))).ravel()
    return x, bool(flat)


def channel_previews(path: str | Path) -> dict[tuple[str, str], tuple[np.ndarray, bool]]:
    """(group, channel) -> (min/max-decimated trace, is_flat)."""
    out = {}
    with TdmsFile.open(path) as f:
        for g in f.groups():
            for ch in g.channels():
                n = len(ch)
                if n == 0:
                    out[(g.name, ch.name)] = (np.array([]), True)
                    continue
                x = np.asarray(ch[:], dtype=float)
                out[(g.name, ch.name)] = preview_trace(x)
    return out


def sparkline(y: np.ndarray, w: int = 160, h: int = 30, color: str = "#2a78d6") -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    if y.size < 2:
        return pm
    lo, hi = np.nanmin(y), np.nanmax(y)
    span = hi - lo or 1.0
    xs = np.linspace(2, w - 2, y.size)
    ys = h - 3 - (y - lo) / span * (h - 6)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.0))
    p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in zip(xs, np.nan_to_num(ys, nan=h / 2))]))
    p.end()
    return pm
