"""Meta Binja's Qt UI: a sortable plugin table plus a README-first detail page."""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

from binaryninja import log_error
from binaryninjaui import (
    Sidebar, SidebarContextSensitivity, SidebarWidget, SidebarWidgetLocation,
    SidebarWidgetType, UIAction, UIActionHandler
)
from PySide6.QtCore import QByteArray, QObject, QRunnable, QThreadPool, QUrl, Qt, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QGuiApplication, QImage, QKeySequence,
    QPainter, QPalette, QShortcut, QTextDocument
)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSizePolicy, QStackedWidget,
    QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)

from .core import (
    FILTER_ALL, FILTER_LABELS, PluginEntry, PluginRegistry, PluginSource,
    register_settings
)
from .metadata import open_request

COLUMN_NAME, COLUMN_VERSION, COLUMN_SOURCE, COLUMN_STATUS = range(4)

# Status is the lifecycle; source is where the plugin comes from. They are
# colored differently so neither one is mistaken for the other.
STATUS_COLORS = {
    "enabled": "#57a75b",
    "disabled": "#8a8a8a",
    "update": "#d9a441",
    "available": "#6f8b99",
}
SOURCE_COLORS = {
    PluginSource.NATIVE: "#4a90d9",
    PluginSource.GIT: "#a071c4",
    PluginSource.CATALOG: "#2f9e8f",
}

LINK_COLOR = "#6aa9e9"
# Qt bakes a link color into the HTML it generates from Markdown, so the color
# is rewritten after conversion rather than left to the theme's palette.
_ANCHOR_COLOR_RE = re.compile(r'(<a\b[^>]*>\s*<span style=" color:)#[0-9a-fA-F]{6};')
MAX_README_IMAGES = 24
MAX_README_IMAGE_BYTES = 4 * 1024 * 1024


class _TaskSignals(QObject):
    """Signals emitted by a background task once it finishes."""

    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    """Run a callable off the UI thread and report the outcome via signals."""

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self.work = work
        self.signals = _TaskSignals()
        # The pool would otherwise delete the runnable — and with it the signal
        # sender — before the UI thread dequeues the result.
        self.setAutoDelete(False)

    def run(self) -> None:
        """Execute the wrapped callable, converting failures into a signal."""
        try:
            result = self.work()
        except Exception as exc:  # Background failures must never kill the pool.
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(result)


class TaskRunner:
    """Run work on a thread pool, holding each task until it reports back."""

    def __init__(self, pool: QThreadPool) -> None:
        self.pool = pool
        self._tasks: set = set()

    def run(self, work: Callable[[], object], on_done=None, on_failed=None) -> None:
        """Queue *work*, delivering its result to the UI thread."""
        task = _Task(work)
        self._tasks.add(task)
        if on_done is not None:
            task.signals.done.connect(on_done)
        if on_failed is not None:
            task.signals.failed.connect(on_failed)
        task.signals.done.connect(lambda *_args: self._tasks.discard(task))
        task.signals.failed.connect(lambda *_args: self._tasks.discard(task))
        self.pool.start(task)


def _badge(text: str, color: str) -> QLabel:
    """Return a small rounded badge label."""
    label = QLabel(text)
    label.setStyleSheet(
        f"background-color: {color}; color: #ffffff; border-radius: 6px;"
        " padding: 1px 7px; font-size: 10px; font-weight: bold;"
    )
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
    return label


def _format_facts(entry: PluginEntry, details: Dict) -> str:
    """Build the compact one-line fact summary shown under a plugin's name."""
    facts: List[str] = []
    if entry.author:
        facts.append(entry.author)
    if entry.version:
        facts.append(f"v{entry.version}" if not str(entry.version).startswith("v") else str(entry.version))
    stars = details.get("stars")
    if isinstance(stars, int):
        facts.append(f"★ {stars:,}")
    license_id = details.get("license")
    if license_id and license_id != "NOASSERTION":
        facts.append(license_id)
    pushed = details.get("pushed_at")
    if isinstance(pushed, str) and len(pushed) >= 10:
        facts.append(f"updated {pushed[:10]}")
    if details.get("archived"):
        facts.append("archived")
    return "  ·  ".join(facts)


class ReadmeBrowser(QTextBrowser):
    """A Markdown view that loads remote README images in the background."""

    def __init__(self, tasks: TaskRunner) -> None:
        super().__init__()
        self.tasks = tasks
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        # Several Binary Ninja themes render the default link color almost
        # black on a dark background.
        palette = self.palette()
        palette.setColor(QPalette.Link, QColor(LINK_COLOR))
        palette.setColor(QPalette.LinkVisited, QColor(LINK_COLOR))
        self.setPalette(palette)
        self.viewport().setPalette(palette)
        self._images: Dict[str, QImage] = {}
        self._pending: set = set()

    def render_markdown(self, text: str) -> None:
        """Render Markdown with readable link colors on dark Binary Ninja themes."""
        document = QTextDocument(self)
        document.setMarkdown(text)
        self.setHtml(_ANCHOR_COLOR_RE.sub(lambda m: f"{m.group(1)}{LINK_COLOR};", document.toHtml()))

    def reset_images(self) -> None:
        """Drop image state when a different plugin is displayed."""
        self._images.clear()
        self._pending.clear()

    def loadResource(self, resource_type, url: QUrl):
        """Serve README images from cache, fetching unseen ones asynchronously.

        Only HTTPS images are fetched, matching Meta Binja's refusal to use
        cleartext transports for repository content.
        """
        if resource_type != QTextDocument.ImageResource:
            return super().loadResource(resource_type, url)
        if url.scheme() != "https":
            return None
        key = url.toString()
        if key in self._images:
            return self._images[key]
        if key in self._pending or len(self._images) + len(self._pending) >= MAX_README_IMAGES:
            return None
        self._pending.add(key)
        self.tasks.run(
            lambda target=key: self._download(target),
            self._image_ready,
            lambda _message, target=key: self._pending.discard(target),
        )
        return None

    @staticmethod
    def _download(url: str):
        """Fetch a README image, refusing oversized payloads."""
        with open_request(url, require_public=True) as response:
            data = response.read(MAX_README_IMAGE_BYTES + 1)
        if len(data) > MAX_README_IMAGE_BYTES:
            raise ValueError("README image too large")
        return url, data

    def _image_ready(self, payload) -> None:
        """Install a downloaded image and re-lay out the document in place."""
        url, data = payload
        self._pending.discard(url)
        image = QImage()
        if not image.loadFromData(QByteArray(data)):
            return
        self._images[url] = image
        document = self.document()
        document.addResource(QTextDocument.ImageResource, QUrl(url), image)
        position = self.verticalScrollBar().value()
        document.markContentsDirty(0, document.characterCount())
        self.verticalScrollBar().setValue(position)


class MetaBinjaPanel(QWidget):
    """The complete manager UI, shared by the sidebar and the standalone window."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.registry = PluginRegistry()
        self.results: List[PluginEntry] = []
        self.current_entry: Optional[PluginEntry] = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.tasks = TaskRunner(self.pool)
        self._busy = 0
        self._detail_token = 0
        self._pending_status = ""
        self._sorted_by_user = False

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self._build_search(root)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list_page())
        self.stack.addWidget(self._build_detail_page())
        root.addWidget(self.stack, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.status)

        self._build_shortcuts()
        self.refresh(force=False)

    # ---------------------------------------------------------------- layout

    def _build_search(self, root: QVBoxLayout) -> None:
        """Build the search field, filter selector, and toolbar row."""
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search plugins or paste a repository URL…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self._render_results(show_list=True))
        self.search.returnPressed.connect(self._open_first)
        root.addWidget(self.search)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.filter = QComboBox()
        for value, label in FILTER_LABELS:
            self.filter.addItem(label, value)
        self.filter.currentIndexChanged.connect(lambda _index: self._render_results(show_list=True))
        row.addWidget(self.filter, 1)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-read catalogs and check every installed plugin for updates (F5)")
        self.refresh_btn.clicked.connect(lambda: self.refresh(force=True))
        row.addWidget(self.refresh_btn)

        settings_btn = QPushButton("Catalogs…")
        settings_btn.setToolTip("Where to configure additional plugin catalogs")
        settings_btn.clicked.connect(self._settings_hint)
        row.addWidget(settings_btn)
        root.addLayout(row)

    def _build_list_page(self) -> QWidget:
        """Build the result table page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.table = QTreeWidget()
        self.table.setHeaderLabels(["Name", "Version", "Source", "Status"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemActivated.connect(self._open_item)
        self.table.itemClicked.connect(self._open_item)

        header = self.table.header()
        header.setSectionResizeMode(COLUMN_NAME, QHeaderView.Stretch)
        for column in (COLUMN_VERSION, COLUMN_SOURCE, COLUMN_STATUS):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionsClickable(True)
        header.setStretchLastSection(False)
        header.sectionClicked.connect(self._sort_by_column)
        layout.addWidget(self.table, 1)

        self.summary = QLabel("")
        self.summary.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        layout.addWidget(self.summary)
        return page

    def _build_detail_page(self) -> QWidget:
        """Build the plugin detail page, with the README as its main content."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        back = QPushButton("← Back")
        back.setToolTip("Return to the plugin list (Esc)")
        back.clicked.connect(self.show_list)
        top.addWidget(back)
        top.addStretch(1)
        self.open_repo_btn = QPushButton("Open repo")
        self.open_repo_btn.clicked.connect(self._open_repository)
        top.addWidget(self.open_repo_btn)
        self.copy_url_btn = QPushButton("Copy URL")
        self.copy_url_btn.clicked.connect(self._copy_url)
        top.addWidget(self.copy_url_btn)
        layout.addLayout(top)

        self.detail_name = QLabel()
        self.detail_name.setWordWrap(True)
        name_font = self.detail_name.font()
        name_font.setPointSize(name_font.pointSize() + 3)
        name_font.setBold(True)
        self.detail_name.setFont(name_font)
        layout.addWidget(self.detail_name)

        badges = QHBoxLayout()
        badges.setSpacing(4)
        self.source_badge = _badge("", SOURCE_COLORS[PluginSource.NATIVE])
        self.status_badge = _badge("", STATUS_COLORS["available"])
        badges.addWidget(self.source_badge)
        badges.addWidget(self.status_badge)
        badges.addStretch(1)
        layout.addLayout(badges)

        self.detail_facts = QLabel()
        self.detail_facts.setWordWrap(True)
        self.detail_facts.setStyleSheet("color: #9a9a9a; font-size: 11px;")
        self.detail_facts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.detail_facts)

        actions = QHBoxLayout()
        self.install_btn = QPushButton()
        self.install_btn.clicked.connect(self._install_or_uninstall)
        actions.addWidget(self.install_btn)
        self.update_btn = QPushButton("Update")
        self.update_btn.clicked.connect(self._update)
        actions.addWidget(self.update_btn)
        self.enabled = QCheckBox("Enabled")
        self.enabled.clicked.connect(self._toggle_enabled)
        actions.addWidget(self.enabled)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.readme = ReadmeBrowser(self.tasks)
        layout.addWidget(self.readme, 1)
        return page

    def _build_shortcuts(self) -> None:
        """Register in-widget keyboard shortcuts."""
        for sequence, slot in (
            ("Esc", self.show_list),
            ("Ctrl+F", self._focus_search),
            ("F5", lambda: self.refresh(force=True)),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    # ----------------------------------------------------------- list render

    def refresh(self, force: bool = False) -> None:
        """Reload provider state, off the UI thread when it may hit the network.

        Refreshes rebuild the shared registry, so a request that arrives while
        one is already running — or during a lifecycle action — is dropped
        rather than racing it to overwrite newer state.
        """
        if self._busy:
            return
        self._set_busy(True, "Refreshing plugins…" if force else "Loading plugins…")
        self.tasks.run(
            lambda: self.registry.refresh(check_native_updates=force, force=force),
            lambda _entries: self._refresh_done(force),
            self._refresh_failed,
        )

    def _refresh_done(self, force: bool = False) -> None:
        """Re-render results once a refresh finishes."""
        self._set_busy(False, "")
        if self.registry.errors:
            self._set_error(" · ".join(self.registry.errors))
        elif self._pending_status:
            self._set_success(self._pending_status)
        self._pending_status = ""
        self._render_results()
        if self.current_entry is not None and self.stack.currentIndex() == 1:
            self._reload_current(force)

    def _refresh_failed(self, message: str) -> None:
        """Report a refresh failure without blocking the UI."""
        self._set_busy(False, "")
        log_error(f"Meta Binja refresh failed: {message}")
        self._set_error(f"Refresh failed: {message}")

    def _render_results(self, show_list: bool = False) -> None:
        """Render the current search/filter selection into the result table.

        ``show_list`` is only set by user-driven changes: a refresh finishing in
        the background must not yank the reader off a detail page.
        """
        self.results = self.registry.search(self.search.text(), self.filter.currentData() or FILTER_ALL)
        self.table.setSortingEnabled(False)
        self.table.clear()
        for entry in self.results:
            item = QTreeWidgetItem(
                [entry.name, entry.version or "", entry.source_label, entry.status_label]
            )
            item.setData(COLUMN_NAME, Qt.UserRole, entry)
            item.setForeground(COLUMN_SOURCE, QColor(SOURCE_COLORS[entry.source]))
            item.setForeground(COLUMN_STATUS, QColor(STATUS_COLORS[entry.status_kind]))
            item.setForeground(COLUMN_VERSION, QColor("#8a8a8a"))
            tooltip = entry.description.strip() or entry.repo_url or entry.name
            for column in range(4):
                item.setToolTip(column, tooltip)
            self.table.addTopLevelItem(item)
        if self._sorted_by_user:
            self.table.setSortingEnabled(True)
        self._render_summary()
        if show_list:
            self.stack.setCurrentIndex(0)

    def _render_summary(self) -> None:
        """Update the count line beneath the table."""
        counts = self.registry.counts()
        shown = len(self.results)
        text = f"{shown} shown · {counts['installed']} installed of {counts['total']}"
        if counts["updates"]:
            text += f" · {counts['updates']} update(s) available"
        self.summary.setText(text)

    def _sort_by_column(self, column: int) -> None:
        """Let a header click take over ordering from relevance ranking.

        Only the first click needs handling: once sorting is enabled the header
        toggles the order by itself, and the choice survives re-rendering.
        """
        if self._sorted_by_user:
            return
        self._sorted_by_user = True
        self.table.setSortingEnabled(True)
        self.table.sortItems(column, Qt.AscendingOrder)

    def show_list(self) -> None:
        """Return to the result table."""
        self.stack.setCurrentIndex(0)

    def _focus_search(self) -> None:
        """Move focus to the search field and select its text."""
        self.search.setFocus()
        self.search.selectAll()

    def _open_first(self) -> None:
        """Open the first result when Enter is pressed in the search field."""
        if self.results:
            self._show_entry(self.results[0])

    def _open_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        """Open the management page for an activated row."""
        entry = item.data(COLUMN_NAME, Qt.UserRole)
        if entry is not None:
            self._show_entry(entry)

    # --------------------------------------------------------- detail render

    def _show_entry(self, entry: PluginEntry, force: bool = False, rerender: bool = False) -> None:
        """Render an entry while preserving discovery metadata and live Git state.

        ``force`` comes from an explicit refresh and reaches the README and
        repository facts, which are otherwise served from the TTL cache.
        ``rerender`` refreshes the visible controls even when provider refresh
        reused the same entry object after a lifecycle action.
        """
        if entry is self.current_entry and self.stack.currentIndex() == 1 and not force and not rerender:
            return
        if entry.source is PluginSource.CATALOG and entry.repo_url:
            git_state = self.registry.git.entry_from_url(entry.repo_url, check_updates=False)
            entry.installed = git_state.installed
            entry.enabled = git_state.enabled
            entry.update_available = entry.update_available or git_state.update_available
            entry.version = git_state.version or entry.version
            entry.local_path = git_state.local_path
            entry.backend = git_state.backend

        self.current_entry = entry
        self._detail_token += 1
        token = self._detail_token

        self.detail_name.setText(entry.name)
        self.source_badge.setText(entry.source_label.upper())
        self._restyle_badge(self.source_badge, SOURCE_COLORS[entry.source])
        self.status_badge.setText(entry.status_label.upper())
        self._restyle_badge(self.status_badge, STATUS_COLORS[entry.status_kind])
        self.detail_facts.setText(_format_facts(entry, {}))

        self.enabled.blockSignals(True)
        self.enabled.setChecked(entry.enabled)
        self.enabled.setEnabled(entry.installed and not self._busy)
        self.enabled.blockSignals(False)
        self.install_btn.setText("Uninstall" if entry.installed else "Install")
        self.install_btn.setEnabled(bool(entry.repo_url or entry.source is PluginSource.NATIVE) and not self._busy)
        self.update_btn.setEnabled(entry.installed and entry.update_available and not self._busy)
        self.open_repo_btn.setEnabled(bool(entry.repo_url))
        self.copy_url_btn.setEnabled(bool(entry.repo_url))

        self.readme.reset_images()
        placeholder = entry.description.strip() or "No description available."
        self.readme.render_markdown(f"{placeholder}\n\n*Loading README…*")
        self.stack.setCurrentIndex(1)
        self._load_detail_content(entry, token, force)

    @staticmethod
    def _restyle_badge(label: QLabel, color: str) -> None:
        """Recolor an existing badge label in place."""
        label.setStyleSheet(
            f"background-color: {color}; color: #ffffff; border-radius: 6px;"
            " padding: 1px 7px; font-size: 10px; font-weight: bold;"
        )

    def _load_detail_content(self, entry: PluginEntry, token: int, force: bool = False) -> None:
        """Fetch README text and repository facts for *entry* in the background."""
        self.tasks.run(
            lambda: (self.registry.readme(entry, force), self.registry.details(entry, force)),
            lambda payload: self._detail_ready(entry, token, payload),
            lambda message: self._detail_failed(entry, token, message),
        )

    def _detail_ready(self, entry: PluginEntry, token: int, payload) -> None:
        """Render fetched README/fact data, ignoring results for a stale selection."""
        if token != self._detail_token:
            return
        document, details = payload
        self.detail_facts.setText(_format_facts(entry, details or {}))
        description = entry.description.strip() or (details or {}).get("description", "")
        if document is None:
            fallback = description or "No README available for this repository."
            link = f"\n\n[{entry.repo_url}]({entry.repo_url})" if entry.repo_url else ""
            self.readme.render_markdown(f"{fallback}{link}")
            return
        if document.is_markdown:
            self.readme.render_markdown(document.text)
        else:
            self.readme.setPlainText(document.text)
        self.readme.verticalScrollBar().setValue(0)

    def _detail_failed(self, entry: PluginEntry, token: int, message: str) -> None:
        """Fall back to the plain description when detail loading fails."""
        if token != self._detail_token:
            return
        self.readme.render_markdown(entry.description.strip() or f"Could not load README: {message}")

    # -------------------------------------------------------------- actions

    def _run_action(self, label: str, callback: Callable[[PluginEntry], bool]) -> None:
        """Execute a lifecycle action off the UI thread and reload the page."""
        entry = self.current_entry
        if entry is None:
            return
        self._set_busy(True, f"{label} in progress…")
        self.tasks.run(
            lambda: callback(entry),
            lambda ok: self._action_done(label, bool(ok)),
            lambda message: self._action_failed(label, message),
        )

    def _action_done(self, label: str, ok: bool) -> None:
        """Report the outcome of a lifecycle action and reload provider state."""
        self._set_busy(False, "")
        if not ok:
            self._set_error(f"{label} did not complete successfully.")
            self._restore_controls()
        else:
            self._pending_status = f"{label} completed."
        self.refresh(force=False)

    def _action_failed(self, label: str, message: str) -> None:
        """Report a lifecycle action that raised, restoring the controls."""
        self._set_busy(False, "")
        log_error(f"Meta Binja: {label} failed: {message}")
        self._set_error(f"{label} failed: {message}")
        self._restore_controls()

    def _restore_controls(self) -> None:
        """Put the action controls back on the entry's actual state.

        ``QCheckBox.clicked`` toggles before the action runs, so a failure would
        otherwise leave the box showing a state that was never applied and the
        next click would request the opposite action instead of a retry.
        """
        entry = self.current_entry
        if entry is None:
            return
        self.enabled.blockSignals(True)
        self.enabled.setChecked(entry.enabled)
        self.enabled.blockSignals(False)
        self.install_btn.setText("Uninstall" if entry.installed else "Install")

    def _reload_current(self, force: bool = False) -> None:
        """Re-show the current entry using freshly refreshed provider state."""
        old = self.current_entry
        if old is None:
            return
        if old.repo_url:
            matches = self.registry.search(old.repo_url)
            if matches:
                self._show_entry(matches[0], force, rerender=True)
                return
        for entry in self.registry.search(old.name):
            if entry.id == old.id:
                self._show_entry(entry, force, rerender=True)
                return
        self.show_list()

    def _install_or_uninstall(self) -> None:
        """Install or uninstall the selected entry according to current state."""
        entry = self.current_entry
        if entry is None:
            return
        if not entry.installed:
            self._run_action("Install", self.registry.install)
            return
        confirm = QMessageBox.question(
            self,
            "Meta Binja",
            f"Uninstall “{entry.name}”?\n\nThis removes its files from disk.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self._run_action("Uninstall", self.registry.uninstall)

    def _toggle_enabled(self, checked: bool) -> None:
        """Enable or disable the selected entry."""
        self._run_action(
            "Enable" if checked else "Disable",
            lambda entry: self.registry.set_enabled(entry, checked),
        )

    def _update(self) -> None:
        """Update the selected entry."""
        self._run_action("Update", self.registry.update)

    def _open_repository(self) -> None:
        """Open the selected plugin's repository in the system browser."""
        if self.current_entry and self.current_entry.repo_url:
            QDesktopServices.openUrl(QUrl(self.current_entry.repo_url))

    def _copy_url(self) -> None:
        """Copy the selected plugin's repository URL to the clipboard."""
        if self.current_entry and self.current_entry.repo_url:
            QGuiApplication.clipboard().setText(self.current_entry.repo_url)
            self._set_success("Repository URL copied.")

    # ---------------------------------------------------------------- status

    def _set_busy(self, busy: bool, message: str) -> None:
        """Track outstanding background work and reflect it in the controls."""
        self._busy = max(0, self._busy + (1 if busy else -1))
        active = self._busy > 0
        self.refresh_btn.setEnabled(not active)
        self.install_btn.setEnabled(not active and self.current_entry is not None)
        self.update_btn.setEnabled(
            not active and bool(self.current_entry and self.current_entry.installed and self.current_entry.update_available)
        )
        self.enabled.setEnabled(not active and bool(self.current_entry and self.current_entry.installed))
        if message:
            self.status.setStyleSheet("color: #8a8a8a; font-size: 11px;")
            self.status.setText(message)
        elif not active:
            self.status.setText("")

    def _set_success(self, message: str) -> None:
        """Show a transient confirmation in the status line."""
        self.status.setStyleSheet("color: #57a75b; font-size: 11px;")
        self.status.setText(message)

    def _set_error(self, message: str) -> None:
        """Show a non-blocking error in the status line."""
        self.status.setStyleSheet("color: #d9534f; font-size: 11px;")
        self.status.setText(message)

    def _settings_hint(self) -> None:
        """Explain where additional discovery catalogs are configured."""
        QMessageBox.information(
            self,
            "Meta Binja",
            "Open Binary Ninja Settings and search for ‘Meta Binja’. Add GitHub repository URLs, raw Markdown "
            "awesome-lists, or JSON catalog URLs under metaBinja.catalogSources.\n\n"
            "Set GITHUB_TOKEN in your environment to raise GitHub's API rate limit.",
        )


class ManagerWidget(SidebarWidget):
    """Global sidebar hosting the Meta Binja manager panel."""

    def __init__(self, name, frame, data):
        super().__init__(name)
        self.actionHandler = UIActionHandler()
        self.actionHandler.setupActionHandler(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = MetaBinjaPanel(self)
        layout.addWidget(self.panel)


class MetaBinjaSidebarType(SidebarWidgetType):
    """Sidebar type registered globally with Binary Ninja."""

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
        """Create the global Meta Binja sidebar widget."""
        return ManagerWidget("Meta Binja", frame, data)

    def defaultLocation(self):
        """Place Meta Binja in the right content sidebar by default."""
        return SidebarWidgetLocation.RightContent

    def contextSensitivity(self):
        """Use one global sidebar context across Binary Ninja views."""
        return SidebarContextSensitivity.GlobalSidebarContext


_registered = False
_window: Optional[QWidget] = None


def open_manager_window(_context=None) -> None:
    """Open (or raise) a full-size manager window for more room than the sidebar."""
    global _window
    if _window is None:
        _window = QWidget()
        _window.setWindowTitle("Meta Binja")
        _window.resize(900, 700)
        layout = QVBoxLayout(_window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(MetaBinjaPanel(_window))
    _window.show()
    _window.raise_()
    _window.activateWindow()


def register_ui():
    """Register settings, the sidebar, and the standalone window action once."""
    global _registered
    if _registered:
        return
    register_settings()
    Sidebar.addSidebarWidgetType(MetaBinjaSidebarType())
    try:
        action = "Meta Binja\\Open Plugin Manager"
        UIAction.registerAction(action)
        UIActionHandler.globalActions().bindAction(action, UIAction(open_manager_window))
        from binaryninjaui import Menu

        Menu.mainMenu("Plugins").addAction(action, "Meta Binja")
    except Exception as exc:  # pragma: no cover - menu APIs vary across builds
        log_error(f"Meta Binja: could not register manager window action: {exc}")
    _registered = True
