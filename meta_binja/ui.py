from __future__ import annotations

from typing import List, Optional

from binaryninja import log_error
from binaryninjaui import (
    Sidebar, SidebarContextSensitivity, SidebarWidget, SidebarWidgetLocation,
    SidebarWidgetType, UIAction, UIActionHandler
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget
)

from .core import PluginEntry, PluginRegistry, PluginSource, register_settings


class ManagerWidget(SidebarWidget):
    def __init__(self, name, frame, data):
        super().__init__(name)
        self.registry = PluginRegistry()
        self.results: List[PluginEntry] = []
        self.current_entry: Optional[PluginEntry] = None

        root = QVBoxLayout()
        root.setContentsMargins(8, 8, 8, 8)

        title = QLabel("Meta Binja")
        font = title.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search plugins or paste a repository URL…")
        self.search.textChanged.connect(self._search)
        self.search.returnPressed.connect(self._open_first)
        root.addWidget(self.search)

        actions = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda: self.refresh(True))
        actions.addWidget(refresh)
        hint = QPushButton("Catalog settings")
        hint.clicked.connect(self._settings_hint)
        actions.addWidget(hint)
        root.addLayout(actions)

        self.stack = QStackedWidget()
        self.list_page = QWidget()
        list_layout = QVBoxLayout(self.list_page)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._open_item)
        list_layout.addWidget(self.list)
        self.stack.addWidget(self.list_page)

        self.detail_page = QWidget()
        detail = QVBoxLayout(self.detail_page)
        back = QPushButton("← Back")
        back.clicked.connect(lambda: self.stack.setCurrentWidget(self.list_page))
        detail.addWidget(back)

        self.detail_name = QLabel()
        name_font = self.detail_name.font()
        name_font.setPointSize(name_font.pointSize() + 4)
        name_font.setBold(True)
        self.detail_name.setFont(name_font)
        detail.addWidget(self.detail_name)

        self.detail_meta = QLabel()
        self.detail_meta.setWordWrap(True)
        self.detail_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.addWidget(self.detail_meta)

        self.detail_description = QLabel()
        self.detail_description.setWordWrap(True)
        self.detail_description.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail.addWidget(self.detail_description)

        self.enabled = QCheckBox("Enabled")
        self.enabled.clicked.connect(self._toggle_enabled)
        detail.addWidget(self.enabled)

        buttons = QHBoxLayout()
        self.install_btn = QPushButton()
        self.install_btn.clicked.connect(self._install_or_uninstall)
        buttons.addWidget(self.install_btn)
        self.update_btn = QPushButton("Update")
        self.update_btn.clicked.connect(self._update)
        buttons.addWidget(self.update_btn)
        detail.addLayout(buttons)
        detail.addStretch()

        self.stack.addWidget(self.detail_page)
        root.addWidget(self.stack)
        self.setLayout(root)
        self.refresh(False)

    def refresh(self, check_updates=False):
        try:
            self.registry.refresh(check_updates)
        except Exception as exc:
            log_error(f"Meta Binja refresh failed: {exc}")
            QMessageBox.warning(self, "Meta Binja", f"Refresh failed:\n{exc}")
        self._search(self.search.text())

    def _search(self, text):
        self.results = self.registry.search(text)
        self.list.clear()
        for entry in self.results:
            state = "Installed" if entry.installed else entry.source.value.title()
            if entry.update_available:
                state = "Update available"
            item = QListWidgetItem(f"{entry.name}\n{state}")
            item.setData(Qt.UserRole, entry)
            self.list.addItem(item)
        self.stack.setCurrentWidget(self.list_page)

    def _open_first(self):
        if self.results:
            self._show_entry(self.results[0])

    def _open_item(self, item):
        self._show_entry(item.data(Qt.UserRole))

    def _show_entry(self, entry):
        if entry.source is PluginSource.CATALOG and entry.repo_url:
            entry = self.registry.git.entry_from_url(entry.repo_url)
        self.current_entry = entry
        self.detail_name.setText(entry.name)
        status = "Update available" if entry.update_available else ("Installed" if entry.installed else "Not installed")
        self.detail_meta.setText(
            f"Source: {entry.source_name or entry.source.value}\n"
            f"Repository: {entry.repo_url or 'n/a'}\n"
            f"Version: {entry.version or 'unknown'}\n"
            f"Status: {status}"
        )
        self.detail_description.setText(entry.description or "")
        self.enabled.blockSignals(True)
        self.enabled.setChecked(entry.enabled)
        self.enabled.setEnabled(entry.installed)
        self.enabled.blockSignals(False)
        self.install_btn.setText("Uninstall" if entry.installed else "Install")
        self.update_btn.setEnabled(entry.installed and entry.update_available)
        self.stack.setCurrentWidget(self.detail_page)

    def _run_action(self, label, callback):
        if not self.current_entry:
            return
        try:
            ok = callback(self.current_entry)
        except Exception as exc:
            ok = False
            QMessageBox.critical(self, "Meta Binja", f"{label} failed:\n{exc}")
        if not ok:
            QMessageBox.warning(self, "Meta Binja", f"{label} did not complete successfully.")
        self._reload_current()

    def _reload_current(self):
        if not self.current_entry:
            return
        old = self.current_entry
        self.refresh(False)
        if old.repo_url and old.source is not PluginSource.NATIVE:
            self._show_entry(self.registry.git.entry_from_url(old.repo_url))
            return
        for entry in self.registry.search(old.name):
            if entry.id == old.id:
                self._show_entry(entry)
                return
        self.stack.setCurrentWidget(self.list_page)

    def _install_or_uninstall(self):
        if not self.current_entry:
            return
        self._run_action("Uninstall" if self.current_entry.installed else "Install",
                         self.registry.uninstall if self.current_entry.installed else self.registry.install)

    def _toggle_enabled(self, checked):
        self._run_action("Enable" if checked else "Disable",
                         lambda entry: self.registry.set_enabled(entry, checked))

    def _update(self):
        self._run_action("Update", self.registry.update)

    def _settings_hint(self):
        QMessageBox.information(
            self, "Meta Binja",
            "Open Binary Ninja Settings and search for ‘Meta Binja’. Add raw Markdown or JSON catalog URLs under metaBinja.catalogSources."
        )


class MetaBinjaSidebarType(SidebarWidgetType):
    def __init__(self):
        icon = QImage(56, 56, QImage.Format_RGB32)
        icon.fill(0)
        painter = QPainter(icon)
        painter.setFont(QFont("Open Sans", 38, QFont.Bold))
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawText(icon.rect(), Qt.AlignCenter, "M")
        painter.end()
        super().__init__(icon, "Meta Binja")

    def createWidget(self, frame, data):
        return ManagerWidget("Meta Binja", frame, data)

    def defaultLocation(self):
        return SidebarWidgetLocation.RightContent

    def contextSensitivity(self):
        return SidebarContextSensitivity.GlobalSidebarContext


_registered = False


def register_ui():
    global _registered
    if _registered:
        return
    _registered = True
    register_settings()
    Sidebar.addSidebarWidgetType(MetaBinjaSidebarType())
