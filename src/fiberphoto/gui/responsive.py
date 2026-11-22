"""Move the same live controls or figures between splitter panes and compact tabs."""
from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import QLayout, QStackedLayout, QTabWidget, QWidget

from .widgets import FigureView


# Apply these rules to individual navigation bars, not to editors or their children.
def style_tabs(tabs, *, workflow=False, compact=False):
    bar = tabs.tabBar()
    bar.setFocusPolicy(Qt.StrongFocus)
    bar.setExpanding(False)
    color = "#185491" if workflow else "#d9eafb"
    ink = "#ffffff" if workflow else "#123f6d"
    padding = "2px 5px" if compact else ("5px 9px" if workflow else "3px 7px")
    bar.setStyleSheet(f"""
        QTabBar::tab {{ border: 1px solid #8798aa; border-radius: 4px;
            margin-right: 2px; padding: {padding}; background: #f5f7fa; color: #23384c; }}
        QTabBar::tab:hover {{ background: #e4effa; border-color: #406a95; }}
        QTabBar::tab:selected {{ background: {color}; color: {ink}; border-color: #225d98;
            font-weight: 600; }}
        QTabBar::tab:selected:focus {{ border: 1px dashed #713b00;
            background: #ffedbb; color: #402600; }}
        QTabBar::tab:disabled {{ color: #65727e; background: #f0f1f3; border-color: #b4bec7; }}
    """)


class SectionTabs(QTabWidget):
    """Named sections of a form, using the same widgets when switching language."""
    def __init__(self, sections):
        super().__init__()
        self.labels = [label for _, label in sections]
        style_tabs(self)
        for widget, label in sections:
            self.addTab(widget, label[0])

    def set_lang(self, lang):
        for index, label in enumerate(self.labels):
            self.setTabText(index, label[lang == "en"])


class ResponsivePanels(QWidget):
    def __init__(self, splitter, panels, labels, *, min_width, min_height=0, fit_contents=False, fit_plots=False):
        super().__init__()
        self.splitter = splitter
        self.panels = panels
        self.labels = labels
        self.lang = "zh"
        self.min_width = min_width
        self.min_height = min_height
        self.fit_contents = fit_contents
        self.fit_plots = fit_plots
        # Capture the original hierarchy before compact tabs reparent its figures.
        # Events have a vertical splitter containing a second, horizontal one.
        self.groups = {splitter: [splitter.widget(i) for i in range(splitter.count())]}
        for pane in panels:
            parent = pane.parentWidget()
            if parent not in self.groups:
                self.groups[parent] = [parent.widget(i) for i in range(parent.count())]
        if fit_plots:
            for view in splitter.findChildren(FigureView):
                view.previewSizeChanged.connect(self._reflow, Qt.QueuedConnection)
        self.positions = [(panel.parentWidget(), panel.parentWidget().indexOf(panel)) for panel in panels]
        self.original_sizes = {}
        self.compact = QTabWidget()
        style_tabs(self.compact)
        self.deck = QStackedLayout(self)
        self.deck.setSizeConstraint(QLayout.SetNoConstraint)
        self.deck.addWidget(splitter)
        self.deck.addWidget(self.compact)
        self.last_tab = 0

    def minimumSizeHint(self):
        if self.fit_contents:
            # Let the outer page scroll only when even one pane will not fit.
            height = max(self._panel_heights())
            if self.compact.count():
                height += self.compact.tabBar().sizeHint().height() + 6
            return QSize(165, height)
        return QSize(165, 105)

    def _panel_heights(self):
        return [max(p.minimumHeight(), p.minimumSizeHint().height()) for p in self.panels]

    def set_lang(self, lang):
        self.lang = lang
        for i in range(self.compact.count()):
            self.compact.setTabText(i, self.labels[i][lang == "en"])

    def _preferred(self, widget):
        if isinstance(widget, FigureView):
            return widget.preferredPaneSize()
        if isinstance(widget, QTabWidget):
            size = QSize()
            for i in range(widget.count()):
                size = size.expandedTo(self._preferred(widget.widget(i)))
            return size + QSize(4, widget.tabBar().sizeHint().height() + 4)
        if widget in self.groups:
            children = [self._preferred(pane) for pane in self.groups[widget]]
            handles = (len(children) - 1) * widget.handleWidth()
            if widget.orientation() == Qt.Horizontal:
                return QSize(sum(s.width() for s in children) + handles, max(s.height() for s in children))
            return QSize(max(s.width() for s in children), sum(s.height() for s in children) + handles)
        return widget.minimumSizeHint().expandedTo(widget.minimumSize())

    def _fit_splitters(self):
        for group, children in self.groups.items():
            if group.count() != len(children) or not group.isVisible():
                continue
            horizontal = group.orientation() == Qt.Horizontal
            needs = [self._preferred(pane) for pane in children]
            needs = [size.width() if horizontal else size.height() for size in needs]
            current = group.sizes()
            if all(actual >= need for actual, need in zip(current, needs)):
                continue
            extra = sum(current) - sum(needs)
            if extra >= 0:
                # Preserve the user's split unless a chart needs more space.
                surplus = [max(0, actual - need) for actual, need in zip(current, needs)]
                total = sum(surplus)
                group.setSizes([need + round(extra * part / total) for need, part in zip(needs, surplus)])

    def _reflow(self):
        minimum = self.min_height
        if self.fit_contents and self.splitter.orientation() == Qt.Vertical:
            minimum = max(minimum, sum(self._panel_heights()) + self.splitter.handleWidth())
        narrow = self.width() < self.min_width or self.height() < minimum
        if self.fit_plots:
            needed = self._preferred(self.splitter)
            narrow = narrow or self.width() < needed.width() or self.height() < needed.height()
        if narrow == bool(self.compact.count()):
            if self.fit_plots and not narrow:
                self._fit_splitters()
            return
        if narrow:
            self.original_sizes = {parent: parent.sizes() for parent, _ in self.positions}
            for panel, label in zip(self.panels, self.labels):
                self.compact.addTab(panel, label[self.lang == "en"])
            self.compact.setCurrentIndex(self.last_tab)
            self.deck.setCurrentWidget(self.compact)
        else:
            self.last_tab = self.compact.currentIndex()
            for panel, (parent, index) in zip(self.panels, self.positions):
                parent.insertWidget(index, panel)
                panel.show()
            for parent, sizes in self.original_sizes.items():
                parent.setSizes(sizes)
            self.deck.setCurrentWidget(self.splitter)
            if self.fit_plots:
                QTimer.singleShot(0, self, self._fit_splitters)
        self.updateGeometry()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.LayoutRequest and getattr(self, "fit_contents", False) and hasattr(self, "last_tab"):
            self._reflow()
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def showEvent(self, event):
        super().showEvent(event)
        if self.fit_plots:
            # A hidden workflow page can be resized before its splitter
            # receives its final geometry. Recheck after the tab is displayed.
            QTimer.singleShot(0, self, self._reflow)

    def select(self, index):
        self.last_tab = index
        if self.compact.count():
            self.compact.setCurrentIndex(index)
