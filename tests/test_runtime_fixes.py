"""Regression tests for Binary Ninja runtime integration fixes."""

import types
import unittest
from unittest.mock import patch

try:
    from tests.stubs import install_binaryninja
except ImportError:  # pragma: no cover - direct test invocation
    from stubs import install_binaryninja

install_binaryninja()

from meta_binja.core import PluginEntry, PluginSource
from meta_binja.runtime_fixes import (
    filter_managed_native_duplicates,
    prepare_markdown,
    register_settings,
)


class MarkdownFixTests(unittest.TestCase):
    """Validate README preprocessing before Qt's Markdown parser sees it."""

    def test_angle_placeholder_is_escaped(self):
        """Unknown angle-bracket placeholders must render as text, not HTML tags."""
        source = "- **Quick scan → <preset>** — one-click scan.\n- **Edit presets…** — edit them."
        rendered = prepare_markdown(source)
        self.assertIn("&lt;preset&gt;", rendered)
        self.assertIn("**Edit presets…**", rendered)

    def test_real_html_tags_are_preserved(self):
        """Supported inline/block HTML remains available to repository READMEs."""
        source = '<details><summary>More</summary><img src="shot.png"></details>'
        self.assertEqual(prepare_markdown(source), source)


class DuplicateFilteringTests(unittest.TestCase):
    """Validate suppression of Binary Ninja's second view of managed Git plugins."""

    def test_managed_activation_suppresses_matching_native_row_only(self):
        """A Meta Binja Git activation should appear once while unrelated native rows remain."""
        duplicate = PluginEntry(
            id="native:user:api_xref_hunter",
            name="API Xref Hunter",
            source=PluginSource.NATIVE,
            backend=types.SimpleNamespace(path="api_xref_hunter"),
        )
        native = PluginEntry(
            id="native:community:other",
            name="Other Plugin",
            source=PluginSource.NATIVE,
            backend=types.SimpleNamespace(path="other_plugin"),
        )
        git = PluginEntry(
            id="git:https://github.com/thelazycat00/api_xref_hunter",
            name="api_xref_hunter",
            source=PluginSource.GIT,
            repo_url="https://github.com/TheLazyCat00/api_xref_hunter",
            installed=True,
            enabled=True,
        )

        result = filter_managed_native_duplicates(
            [duplicate, native, git],
            ["api_xref_hunter"],
        )

        self.assertNotIn(duplicate, result)
        self.assertIn(native, result)
        self.assertIn(git, result)

    def test_unmanaged_matching_name_is_not_suppressed(self):
        """Name similarity alone is never enough to remove a native extension."""
        native = PluginEntry(
            id="native:community:sample",
            name="Sample Plugin",
            source=PluginSource.NATIVE,
            backend=types.SimpleNamespace(path="sample_plugin"),
        )
        self.assertEqual(filter_managed_native_duplicates([native], []), [native])


class SettingsFixTests(unittest.TestCase):
    """Validate Binary Ninja setting group registration."""

    def test_catalog_setting_uses_its_actual_group_prefix(self):
        """``metaBinja.catalogSources`` belongs to the ``metaBinja`` group."""
        calls = []

        class Settings:
            def register_group(self, group, title):
                calls.append(("group", group, title))
                return True

            def register_setting(self, key, payload):
                calls.append(("setting", key, payload))
                return True

        with patch("meta_binja.runtime_fixes._core.Settings", Settings):
            register_settings()

        self.assertEqual(calls[0], ("group", "metaBinja", "Meta Binja"))
        self.assertEqual(calls[1][1], "metaBinja.catalogSources")
        self.assertFalse(any(call[1] == "metaBinja.general" for call in calls))


if __name__ == "__main__":
    unittest.main()
