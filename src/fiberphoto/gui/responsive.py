"""Move the same live controls or figures between splitter panes and compact tabs."""
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QLayout, QStackedLayout, QTabWidget, QWidget


COMPACT_TAB_STYLE = "QTabBar::tab { padding: 2px 8px; }"


class ResponsivePanels(QWidget):
    def __init__(self, splitter, panels, labels, *, min_width, min_height=0, fit_contents=False):
        super().__init__()
        self.splitter = splitter
        self.panels = panels
        self.labels = labels
        self.lang = "zh"
        self.min_width = min_width
        self.min_height = min_height
        self.fit_contents = fit_contents
        self.positions = [(panel.parentWidget(), panel.parentWidget().indexOf(panel)) for panel in panels]
        self.original_sizes = {}
        self.compact = QTabWidget()
        self.compact.tabBar().setStyleSheet(COMPACT_TAB_STYLE)
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

    def _reflow(self):
        minimum = self.min_height
        if self.fit_contents and self.splitter.orientation() == Qt.Vertical:
            minimum = max(minimum, sum(self._panel_heights()) + self.splitter.handleWidth())
        narrow = self.width() < self.min_width or self.height() < minimum
        if narrow == bool(self.compact.count()):
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
        self.updateGeometry()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.LayoutRequest and getattr(self, "fit_contents", False) and hasattr(self, "last_tab"):
            self._reflow()
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def select(self, index):
        self.last_tab = index
        if self.compact.count():
            self.compact.setCurrentIndex(index)
