"""Local desktop layout persistence, independent of controller access policies."""
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QMenu, QPlainTextEdit,
    QSplitter, QTabWidget, QTableView, QToolButton, QTreeView, QVBoxLayout, QWidget)


def split_content(page):
    """Make stacked content panes draggable while preserving intervening controls."""
    content = (QAbstractItemView, QPlainTextEdit, QTabWidget, QSplitter)
    for container in [page, *page.findChildren(QWidget)]:
        layout = container.layout()
        if not isinstance(layout, QVBoxLayout):
            continue
        indices = [index for index in range(layout.count()) if isinstance(layout.itemAt(index).widget(), content)]
        if len(indices) < 2:
            continue
        first, last = indices[0], indices[-1]
        items = [layout.takeAt(first) for _ in range(last - first + 1)]
        groups = []
        for item in items:
            if isinstance(item.widget(), content):
                prefix = []
                if groups and groups[-1] and isinstance(groups[-1][-1].widget(), QLabel):
                    prefix.append(groups[-1].pop())
                groups.append(prefix)
            groups[-1].append(item)
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        for group in groups:
            panel = QWidget()
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            for item in group:
                widget = item.widget()
                if widget is not None:
                    if isinstance(widget, content):
                        widget.setMaximumHeight(16777215)
                        widget.setMinimumHeight(max(70, widget.minimumHeight()))
                    column.addWidget(widget, 1 if isinstance(widget, content) else 0)
                elif item.layout() is not None:
                    item.layout().setParent(None)
                    column.addLayout(item.layout())
                else:
                    column.addItem(item)
            splitter.addWidget(panel)
        layout.insertWidget(first, splitter, 1)


def tab_controls(tabs, changed):
    tabs.setMovable(True)
    tabs.setUsesScrollButtons(True)
    tabs.tabBar().setElideMode(Qt.ElideRight)
    button = QToolButton(tabs)
    button.setText('Tabs ▾')
    button.setToolTip('Show or hide tabs; drag a tab header to reorder it')
    button.setPopupMode(QToolButton.InstantPopup)
    menu = QMenu(button)
    button.setMenu(menu)
    tabs.setCornerWidget(button, Qt.TopRightCorner)

    def set_visible(page, visible):
        index = tabs.indexOf(page)
        if visible or sum(tabs.isTabVisible(i) for i in range(tabs.count())) > 1:
            tabs.setTabVisible(index, visible)
            changed()

    def populate():
        menu.clear()
        show_all = menu.addAction('Show all tabs')
        show_all.triggered.connect(lambda: [set_visible(tabs.widget(i), True) for i in range(tabs.count())])
        menu.addSeparator()
        visible_count = sum(tabs.isTabVisible(i) for i in range(tabs.count()))
        for index in range(tabs.count()):
            page = tabs.widget(index)
            action = menu.addAction(tabs.tabText(index))
            action.setCheckable(True)
            action.setChecked(tabs.isTabVisible(index))
            action.setEnabled(visible_count > 1 or not action.isChecked())
            action.toggled.connect(lambda on, page=page: set_visible(page, on))
    menu.aboutToShow.connect(populate)
    tabs.tabBar().tabMoved.connect(changed)
    tabs.currentChanged.connect(changed)
    return button


class DesktopLayout(QObject):
    def __init__(self, owner, *, preference_key='layout'):
        super().__init__(owner)
        self.owner, self.tables, self.splitters, self.tabs = owner, {}, {}, {}
        self.preference_key = preference_key
        self.default_widths, self.default_tabs = {}, {}
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.save)
        saved = owner.settings.preferences().get(self.preference_key, {})
        saved = saved if isinstance(saved, dict) else {}
        for index in range(owner.tabs.count()):
            page = owner.tabs.widget(index)
            page_key = page.objectName() or type(page).__name__
            # Existing attributes provide stable IDs across data refreshes.
            for name, value in vars(page).items():
                if isinstance(value, (QTableView, QTreeView, QSplitter, QTabWidget)) and not value.objectName():
                    value.setObjectName(name)
            split_content(page)
            for number, table in enumerate([*page.findChildren(QTableView), *page.findChildren(QTreeView)]):
                key = page_key + '/table/' + (table.objectName() or str(number))
                header = table.horizontalHeader() if isinstance(table, QTableView) else table.header()
                count = header.count()
                labels = [str(table.model().headerData(i, Qt.Horizontal) or '') for i in range(count)]
                widths = [max(120, min(5000, header.sectionSize(i))) for i in range(count)]
                self.default_widths[key] = list(widths)
                stored = saved.get('tables', {}).get(key, {}) if isinstance(saved.get('tables'), dict) else {}
                if isinstance(stored, dict) and stored.get('labels') == labels:
                    candidate = stored.get('widths')
                    if isinstance(candidate, list) and len(candidate) == count and all(type(w) is int and 30 <= w <= 5000 for w in candidate):
                        widths = candidate
                header.setStretchLastSection(False)
                header.setMinimumSectionSize(30)
                header.setSectionResizeMode(QHeaderView.Interactive)
                for column, width in enumerate(widths):
                    header.resizeSection(column, width)
                self.tables[key] = (table, labels)
                header.sectionResized.connect(self.changed)
            for number, splitter in enumerate(page.findChildren(QSplitter)):
                key = page_key + '/split/' + (splitter.objectName() or str(number))
                self.splitters[key] = splitter
                stored = saved.get('splitters', {}).get(key) if isinstance(saved.get('splitters'), dict) else None
                if isinstance(stored, list) and len(stored) == splitter.count() and all(type(x) is int and 1 <= x <= 100000 for x in stored):
                    splitter.setSizes(stored)
                splitter.splitterMoved.connect(self.changed)
            for number, tabs in enumerate(page.findChildren(QTabWidget)):
                self.tabs[page_key + '/tabs/' + (tabs.objectName() or str(number))] = tabs
        self.tabs['main'] = owner.tabs
        for key, tabs in self.tabs.items():
            for index in range(tabs.count()):
                if not tabs.widget(index).objectName():
                    tabs.widget(index).setObjectName(tabs.tabText(index))
            self.default_tabs[key] = {
                'order': [tabs.widget(i).objectName() for i in range(tabs.count())],
                'current': tabs.currentWidget().objectName() if tabs.currentWidget() else None,
            }
            state = saved.get('tabs', {}).get(key, {}) if isinstance(saved.get('tabs'), dict) else {}
            if isinstance(state, dict):
                order = state.get('order', [])
                if isinstance(order, list):
                    for destination, name in enumerate(order[:tabs.count()]):
                        current = next((i for i in range(tabs.count()) if tabs.widget(i).objectName() == name), None)
                        if current is not None:
                            tabs.tabBar().moveTab(current, destination)
                hidden = state.get('hidden', [])
                if isinstance(hidden, list):
                    for i in range(tabs.count()):
                        tabs.setTabVisible(i, tabs.widget(i).objectName() not in hidden)
                if tabs.count() and not any(tabs.isTabVisible(i) for i in range(tabs.count())):
                    tabs.setTabVisible(0, True)
                current = state.get('current')
                for i in range(tabs.count()):
                    if tabs.widget(i).objectName() == current and tabs.isTabVisible(i):
                        tabs.setCurrentIndex(i)
            tab_controls(tabs, self.changed)

    def changed(self, *_):
        self.timer.start()

    def reset(self):
        """Restore view defaults without rebuilding widgets or losing staged edits."""
        self.timer.stop()
        for key, (table, _) in self.tables.items():
            header = table.horizontalHeader() if isinstance(table, QTableView) else table.header()
            header.setSectionResizeMode(QHeaderView.Interactive)
            for column, width in enumerate(self.default_widths[key]):
                header.resizeSection(column, width)
        for splitter in self.splitters.values():
            splitter.setSizes([1000] * splitter.count())
        for key, tabs in self.tabs.items():
            defaults = self.default_tabs[key]
            for destination, name in enumerate(defaults['order']):
                current = next((i for i in range(tabs.count()) if tabs.widget(i).objectName() == name), None)
                if current is not None:
                    tabs.tabBar().moveTab(current, destination)
            for index in range(tabs.count()):
                tabs.setTabVisible(index, True)
                if tabs.widget(index).objectName() == defaults['current']:
                    tabs.setCurrentIndex(index)
        if self.save():
            self.owner.statusBar().showMessage('Desktop layout reset: default columns, balanced panes and all tabs visible.')

    def save(self):
        self.timer.stop()
        tables = {}
        for key, (table, labels) in self.tables.items():
            header = table.horizontalHeader() if isinstance(table, QTableView) else table.header()
            tables[key] = {'labels': labels, 'widths': [header.sectionSize(i) for i in range(header.count())]}
        state = {'tables': tables, 'splitters': {key: split.sizes() for key, split in self.splitters.items()},
            'tabs': {key: {'order': [tabs.widget(i).objectName() for i in range(tabs.count())],
                'hidden': [tabs.widget(i).objectName() for i in range(tabs.count()) if not tabs.isTabVisible(i)],
                'current': tabs.currentWidget().objectName() if tabs.currentWidget() else None}
                for key, tabs in self.tabs.items()}}
        try:
            self.owner.settings.update_preferences(**{self.preference_key: state})
        except OSError:
            self.owner.statusBar().showMessage('Could not save desktop layout preferences.')
            return False
        return True
