"""Headless smoke tests for the Meta Binja Qt UI.

These run against stub Binary Ninja modules and Qt's offscreen platform, so
they exercise the real widget code without a Binary Ninja installation. They
skip when PySide6 is unavailable (for example in a plain CI container).
"""

from __future__ import annotations

import os
import unittest

try:
    from tests.stubs import FakeExtension, FakeRepository, install_binaryninja, install_binaryninjaui
except ImportError:  # pragma: no cover - direct ``python tests/test_ui.py`` run
    from stubs import FakeExtension, FakeRepository, install_binaryninja, install_binaryninjaui

try:
    import PySide6  # noqa: F401
except ImportError:  # pragma: no cover - PySide6 is provided by Binary Ninja
    PySide6 = None


def _sample_repositories():
    """Return a fake native repository with one installed and one available plugin."""
    return [
        FakeRepository(
            "community",
            [
                FakeExtension(
                    "HashDB",
                    description="Hash lookup service client.",
                    project_url="https://github.com/example/hashdb",
                    installed=True,
                    enabled=True,
                    version="2.1.0",
                ),
                FakeExtension(
                    "Sigmaker",
                    description="Signature generation.",
                    project_url="https://github.com/example/sigmaker",
                    version="0.4.0",
                ),
                FakeExtension(
                    "Angr Import",
                    description="Companion workflow for sigmaker output.",
                    project_url="https://github.com/example/angr-import",
                    version="0.9.0",
                ),
                FakeExtension(
                    "Debugger Helper",
                    description="Extra debugger glue.",
                    project_url="https://github.com/example/dbg",
                    installed=True,
                    enabled=True,
                    update_available=True,
                    version="1.2.0",
                ),
            ],
        )
    ]


@unittest.skipIf(PySide6 is None, "PySide6 is unavailable outside Binary Ninja")
class PanelTests(unittest.TestCase):
    """Validate that the manager panel builds and renders provider state."""

    @classmethod
    def setUpClass(cls):
        """Build one offscreen Qt application shared by the test methods."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        install_binaryninja(_sample_repositories())
        install_binaryninjaui()
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        """Give each test a fresh set of stub extensions."""
        install_binaryninja(_sample_repositories())

    def _panel(self):
        """Create a panel whose network-backed detail loading is stubbed out."""
        from meta_binja.ui import MetaBinjaPanel

        panel = MetaBinjaPanel()
        panel.registry.readme = lambda _entry, force=False: None
        panel.registry.details = lambda _entry, force=False: {"stars": 42, "license": "MIT"}
        self._settle(panel)
        return panel

    def _settle(self, panel):
        """Wait for background tasks and deliver their queued signals."""
        for _ in range(20):
            panel.pool.waitForDone(2000)
            self.app.processEvents()

    def test_table_lists_source_and_status_in_separate_columns(self):
        """Each plugin occupies one row with its own source and status cells."""
        from meta_binja.ui import COLUMN_NAME, COLUMN_SOURCE, COLUMN_STATUS, COLUMN_VERSION

        panel = self._panel()
        rows = {}
        for index in range(panel.table.topLevelItemCount()):
            item = panel.table.topLevelItem(index)
            rows[item.text(COLUMN_NAME)] = (
                item.text(COLUMN_VERSION),
                item.text(COLUMN_SOURCE),
                item.text(COLUMN_STATUS),
            )
        self.assertEqual(rows["HashDB"], ("2.1.0", "Native", "Enabled"))
        self.assertEqual(rows["Sigmaker"], ("0.4.0", "Native", "Available"))
        self.assertEqual(rows["Debugger Helper"], ("1.2.0", "Native", "Update"))
        panel.deleteLater()

    def test_filter_limits_rows_to_installed_plugins(self):
        """The filter selector narrows the table without touching the search text."""
        from meta_binja.core import FILTER_INSTALLED

        panel = self._panel()
        index = panel.filter.findData(FILTER_INSTALLED)
        panel.filter.setCurrentIndex(index)
        names = {
            panel.table.topLevelItem(row).text(0) for row in range(panel.table.topLevelItemCount())
        }
        self.assertEqual(names, {"HashDB", "Debugger Helper"})
        panel.deleteLater()

    def test_search_ranks_name_matches_first(self):
        """A name match outranks a description match that sorts before it."""
        panel = self._panel()
        panel.search.setText("sigmaker")
        names = [panel.table.topLevelItem(row).text(0) for row in range(panel.table.topLevelItemCount())]
        self.assertEqual(names, ["Sigmaker", "Angr Import"])
        panel.deleteLater()

    def test_opening_an_entry_shows_the_detail_page(self):
        """Activating a row switches to the detail page and renders its README area."""
        panel = self._panel()
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        self.assertEqual(panel.stack.currentIndex(), 1)
        self.assertTrue(panel.detail_name.text())
        self.assertIn(panel.status_badge.text(), {"ENABLED", "DISABLED", "UPDATE", "AVAILABLE"})
        self.assertIn("★ 42", panel.detail_facts.text())
        self.assertTrue(panel.readme.toPlainText().strip())
        panel.deleteLater()

    def test_back_returns_to_the_list(self):
        """The back action returns from a detail page to the result table."""
        panel = self._panel()
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        panel.show_list()
        self.assertEqual(panel.stack.currentIndex(), 0)
        panel.deleteLater()

    def test_install_action_runs_and_reloads_state(self):
        """Installing an available plugin updates its row through a refresh."""
        from meta_binja.core import FILTER_ALL

        panel = self._panel()
        panel.search.setText("sigmaker")
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        self.assertEqual(panel.install_btn.text(), "Install")
        panel._install_or_uninstall()
        self._settle(panel)
        entries = {e.name: e for e in panel.registry.search("", FILTER_ALL)}
        self.assertTrue(entries["Sigmaker"].installed)
        panel.deleteLater()


    def test_header_click_sorts_by_that_column(self):
        """Clicking a header takes ordering over from relevance ranking."""
        panel = self._panel()
        before = [panel.table.topLevelItem(row).text(0) for row in range(panel.table.topLevelItemCount())]
        panel._sort_by_column(0)
        after = [panel.table.topLevelItem(row).text(0) for row in range(panel.table.topLevelItemCount())]
        self.assertNotEqual(before, after)
        self.assertEqual(after, sorted(before))
        panel.deleteLater()

    def test_readme_links_are_readable_on_dark_themes(self):
        """Rendered README links do not keep Qt's near-black default color."""
        panel = self._panel()
        panel.readme.render_markdown("[docs](https://example.test/docs)")
        html = panel.readme.toHtml()
        self.assertIn("https://example.test/docs", html)
        self.assertNotIn("color:#0000ff", html.replace(" ", ""))
        panel.deleteLater()

    def test_confirmed_uninstall_removes_the_plugin(self):
        """Confirming the prompt runs the uninstall and clears installed state."""
        from unittest.mock import patch

        from PySide6.QtWidgets import QMessageBox

        panel = self._panel()
        panel.search.setText("hashdb")
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            panel._install_or_uninstall()
            self._settle(panel)
        entry = panel.registry.search("hashdb")[0]
        self.assertFalse(entry.installed)
        self.assertFalse(entry.enabled)
        panel.deleteLater()

    def test_failed_action_restores_the_enabled_checkbox(self):
        """A failed enable leaves the checkbox on the state actually applied."""
        panel = self._panel()
        panel.search.setText("hashdb")
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        panel.registry.set_enabled = lambda _entry, _enabled: (_ for _ in ()).throw(RuntimeError("denied"))
        panel.enabled.setChecked(False)
        panel._toggle_enabled(False)
        self._settle(panel)
        self.assertTrue(panel.enabled.isChecked())
        self.assertIn("failed", panel.status.text().lower())
        panel.deleteLater()

    def test_uninstall_asks_for_confirmation(self):
        """Uninstalling never runs without an explicit confirmation."""
        from unittest.mock import patch

        from PySide6.QtWidgets import QMessageBox

        panel = self._panel()
        panel.search.setText("hashdb")
        panel._open_item(panel.table.topLevelItem(0))
        self._settle(panel)
        self.assertEqual(panel.install_btn.text(), "Uninstall")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
            panel._install_or_uninstall()
            self._settle(panel)
        question.assert_called_once()
        self.assertTrue(panel.registry.search("hashdb")[0].installed)
        panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
