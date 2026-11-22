"""Editable Matplotlib elements shared by live previews and figure export."""
from dataclasses import dataclass

import matplotlib as mpl
from matplotlib.collections import LineCollection
from matplotlib.colors import to_hex
from matplotlib.container import BarContainer
from matplotlib.lines import Line2D


@dataclass
class Element:
    key: str
    name: str
    kind: str
    artists: list

    def values(self):
        obj = self.artists[0]
        if self.kind == "image":
            return {"cmap": obj.get_cmap().name}
        if self.kind == "text":
            return {"text": obj.get_text(), "size": obj.get_fontsize(), "color": to_hex(obj.get_color())}
        if isinstance(obj, Line2D):
            color = obj.get_color()
        elif isinstance(obj, LineCollection):
            color = obj.get_edgecolor()[0]
        else:
            color = obj.get_facecolor()
            if getattr(color, "ndim", 0) == 2:
                color = color[0] if len(color) else "none"
        return {"color": to_hex(color)}

    def apply(self, changes):
        for obj in self.artists:
            if self.kind == "text":
                if "text" in changes:
                    obj.set_parse_math(False)
                    obj.set_text(changes["text"])
                if "size" in changes:
                    obj.set_fontsize(changes["size"])
            if self.kind == "image" and changes.get("cmap") in mpl.colormaps:
                obj.set_cmap(changes["cmap"])
            if "color" in changes:
                if self.kind == "text" or isinstance(obj, Line2D):
                    obj.set_color(changes["color"])
                elif isinstance(obj, LineCollection):
                    obj.set_edgecolor(changes["color"])
                else:
                    obj.set_facecolor(changes["color"])


def figure_elements(fig, lang="en"):
    en = lang == "en"
    elements = [Element(f"figure/text{i}", obj.get_text(), "text", [obj]) for i, obj in enumerate(fig.texts)
                if obj.get_visible() and obj.get_text()]
    legends = list(fig.legends)
    for i, ax in enumerate(fig.axes):
        prefix = f"ax{i}/"
        panel = f"{i+1} · "
        for name, obj in (("title-left", ax._left_title), ("title", ax.title), ("title-right", ax._right_title),
                          ("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label),
                          *((f"text{j}", item) for j, item in enumerate(ax.texts))):
            if obj.get_visible() and obj.get_text():
                elements.append(Element(prefix + name, panel + obj.get_text(), "text", [obj]))
        if getattr(ax, "_colorbar", None) is not None:
            continue
        if ax.get_legend() is not None:
            legends.append(ax.get_legend())
        for j, obj in enumerate(ax.images):
            elements.append(Element(prefix + f"image{j}", panel + ("Heatmap" if en else "热图色带"), "image", [obj]))
        groups = {}
        for line in ax.lines:
            label = line.get_label()
            # Raw TTL pulses form one series. Unlabelled paired-trial lines share a style.
            key = line.get_gid() or (label if not label.startswith("_") else
                                     (line.get_linestyle(), str(line.get_color()), str(line.get_marker())))
            groups.setdefault(key, []).append(line)
        bar_groups = [c for c in ax.containers if isinstance(c, BarContainer) and c.patches]
        used = {p for group in bar_groups for p in group.patches}
        series = list(groups.values()) + [list(group.patches) for group in bar_groups]
        series += [[p] for p in ax.patches if p not in used]
        series += [[c] for c in ax.collections if len(c.get_facecolor()) or len(c.get_edgecolor())]
        for j, objects in enumerate(series):
            label = objects[0].get_label()
            if not isinstance(label, str) or not label or label.startswith("_"):
                label = f"Series {j+1}" if en else f"图层 {j+1}"
            elements.append(Element(prefix + f"series{j}", panel + label, "series", objects))
    for i, legend in enumerate(legends):
        prefix = "Legend · " if en else "图例 · "
        for j, obj in enumerate(legend.get_texts()):
            elements.append(Element(f"legend{i}/text{j}", prefix + obj.get_text(), "text", [obj]))
            # Legend handles are copies created by Matplotlib, keep their colors in sync.
            label = getattr(obj, "_fiberlume_original", obj.get_text())
            obj._fiberlume_original = label
            for elem in elements:
                if elem.kind == "series" and " ".join(str(elem.artists[0].get_label()).split()) == " ".join(label.split()):
                    handle = legend.legend_handles[j]
                    if isinstance(handle, Line2D) or hasattr(handle, "set_facecolor"):
                        elem.artists.append(handle)
    return elements


def apply_figure_edits(fig, styles):
    if not styles:
        return
    for entry in figure_elements(fig):
        if entry.key in styles:
            entry.apply(styles[entry.key])
