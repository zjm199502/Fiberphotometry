"""Move the same live controls or figures between splitter panes and compact tabs."""
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLayout, QStackedLayout, QTabWidget, QWidget


COMPACT_TAB_STYLE = "QTabBar::tab { padding: 2px 8px; }"


class ResponsivePanels(QWidget):
    def __init__(self, splitter, panels, labels, *, min_width, min_height=0):
        super().__init__()
        self.splitter = splitter
        self.panels = panels
        self.labels = labels
        self.lang = "zh"
        self.min_width = min_width
        self.min_height = min_height
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
        return QSize(165, 105)

    def set_lang(self, lang):
        self.lang = lang
        for i in range(self.compact.count()):
            self.compact.setTabText(i, self.labels[i][lang == "en"])

    def _reflow(self):
        narrow = self.width() < self.min_width or self.height() < self.min_height
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def select(self, index):
        self.last_tab = index
        if self.compact.count():
            self.compact.setCurrentIndex(index)
