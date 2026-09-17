"""Regression tests for Binary Ninja runtime integration fixes."""

import types
import unittest
from unittest.mock import patch

try:
    from tests.stubs import install_binaryninja
except ImportError:  # pragma: no cover - direct test invocation
    from stubs import install_binaryninja

install_binaryninja()

from meta_binja import core as meta_core
from meta_binja import git_provider as meta_git_provider
from meta_binja.core import PluginEntry, PluginSource
from meta_binja.runtime_fixes import (
    _HiddenConsoleSubprocess,
    filter_managed_native_duplicates,
    install_subprocess_fixes,
    prepare_markdown,
    register_settings,
)


class HiddenConsoleSubprocessTests(unittest.TestCase):
    """Validate that Meta Binja never flashes Git console windows on Windows."""

    class FakeSubprocess:
        """Minimal subprocess-like object used to capture delegated calls."""

        CREATE_NO_WINDOW = 0x08000000

        def __init__(self):
            """Initialize the captured call list and passthrough marker."""
            self.calls = []
            self.marker = object()

        def run(self, *args, **kwargs):
            """Record one subprocess invocation and return a sentinel result."""
            self.calls.append((args, kwargs))
            return "result"

    def test_windows_processes_include_create_no_window(self):
        """Existing creation flags are preserved while the no-window flag is added."""
        backend = self.FakeSubprocess()
        wrapped = _HiddenConsoleSubprocess(backend)

        with patch("meta_binja.runtime_fixes.os.name", "nt"):
            result = wrapped.run(["git", "status"], creationflags=0x2, capture_output=True)

        self.assertEqual(result, "result")
        args, kwargs = backend.calls[0]
        self.assertEqual(args[0], ["git", "status"])
        self.assertEqual(kwargs["creationflags"], 0x2 | backend.CREATE_NO_WINDOW)
        self.assertTrue(kwargs["capture_output"])

    def test_non_windows_processes_are_unchanged(self):
        """POSIX subprocess arguments are delegated without Windows-only flags."""
        backend = self.FakeSubprocess()
        wrapped = _HiddenConsoleSubprocess(backend)

        with patch("meta_binja.runtime_fixes.os.name", "posix"):
            wrapped.run(["git", "status"], capture_output=True)

        self.assertNotIn("creationflags", backend.calls[0][1])

    def test_proxy_preserves_other_subprocess_attributes(self):
        """Code using subprocess constants/helpers still sees the wrapped module."""
        backend = self.FakeSubprocess()
        wrapped = _HiddenConsoleSubprocess(backend)
        self.assertIs(wrapped.marker, backend.marker)

    def test_install_routes_both_git_implementations_through_proxy(self):
        """Both the active provider and legacy core provider use the hidden runner."""
        install_subprocess_fixes()
        self.assertTrue(getattr(meta_core.subprocess, "_meta_binja_hidden_console", False))
        self.assertTrue(getattr(meta_git_provider.subprocess, "_meta_binja_hidden_console", False))


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

    def test_supported_underline_tags_are_preserved(self):
        """Qt-supported underline tags must not be escaped as placeholders."""
        source = "Use <u>underlining</u>, but keep <preset> visible."
        rendered = prepare_markdown(source)
        self.assertIn("<u>underlining</u>", rendered)
        self.assertIn("&lt;preset&gt;", rendered)


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

    def test_punctuation_collision_does_not_suppress_unrelated_native_plugin(self):
        """Managed ``foo-bar`` must not hide a distinct native ``foo_bar`` path."""
        native = PluginEntry(
            id="native:community:foo_bar",
            name="foo-bar",
            source=PluginSource.NATIVE,
            backend=types.SimpleNamespace(path="foo_bar"),
        )

        self.assertEqual(
            filter_managed_native_duplicates([native], ["foo-bar"]),
            [native],
        )

    def test_backend_path_match_is_case_insensitive(self):
        """Activation identity remains case-insensitive without normalizing punctuation."""
        native = PluginEntry(
            id="native:user:RouteNinja",
            name="Route Ninja",
            source=PluginSource.NATIVE,
            backend=types.SimpleNamespace(path="routeninja"),
        )

        self.assertEqual(filter_managed_native_duplicates([native], ["RouteNinja"]), [])


class SettingsFixTests(unittest.TestCase):
    """Validate Binary Ninja setting group registration."""

    def test_catalog_setting_uses_its_actual_group_prefix(self):
        """``metaBinja.catalogSources`` belongs to the ``metaBinja`` group."""
        calls = []

        class Settings:
            """Capture the setting registration calls made by the runtime fix."""

            def register_group(self, group, title):
                """Record a setting-group registration."""
                calls.append(("group", group, title))
                return True

            def register_setting(self, key, payload):
                """Record a concrete setting registration."""
                calls.append(("setting", key, payload))
                return True

        with patch("meta_binja.runtime_fixes._core.Settings", Settings):
            register_settings()

        self.assertEqual(calls[0], ("group", "metaBinja", "Meta Binja"))
        self.assertEqual(calls[1][1], "metaBinja.catalogSources")
        self.assertFalse(any(call[1] == "metaBinja.general" for call in calls))


if __name__ == "__main__":
    unittest.main()
