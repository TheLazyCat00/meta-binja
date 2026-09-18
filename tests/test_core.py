"""Regression tests for Meta Binja's provider and URL handling."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


try:
    from tests.stubs import install_binaryninja
except ImportError:  # pragma: no cover - direct ``python tests/test_core.py`` run
    from stubs import install_binaryninja

install_binaryninja()

from meta_binja.core import (
    FILTER_AVAILABLE,
    FILTER_INSTALLED,
    FILTER_UPDATES,
    CatalogProvider,
    GitProvider,
    PluginEntry,
    PluginRegistry,
    PluginSource,
    canonical_repo_url,
    is_repo_url,
    matches_filter,
    relevance,
    repo_name_from_url,
)
from meta_binja.metadata import JsonCache


class UrlTests(unittest.TestCase):
    """Validate repository URL recognition and canonicalization."""

    def test_https_repo(self):
        """Normalize standard GitHub HTTPS repository URLs."""
        self.assertTrue(is_repo_url("https://github.com/Vector35/community-plugins"))
        self.assertEqual(
            canonical_repo_url("https://github.com/Vector35/community-plugins.git/"),
            "https://github.com/vector35/community-plugins",
        )

    def test_ssh_repo(self):
        """Normalize SCP-style and ssh:// GitHub clone URLs to one identity."""
        self.assertTrue(is_repo_url("git@github.com:owner/repo.git"))
        self.assertTrue(is_repo_url("ssh://git@github.com/owner/repo.git"))
        self.assertEqual(
            canonical_repo_url("git@github.com:owner/repo.git"),
            "https://github.com/owner/repo",
        )
        self.assertEqual(
            canonical_repo_url("ssh://git@github.com/owner/repo.git"),
            canonical_repo_url("https://github.com/owner/repo"),
        )

    def test_default_ports_are_normalized_and_custom_ports_preserved(self):
        """Ignore default transport ports while retaining custom SSH endpoints."""
        self.assertEqual(
            canonical_repo_url("ssh://git@github.com:22/owner/repo.git"),
            "https://github.com/owner/repo",
        )
        self.assertEqual(
            canonical_repo_url("https://github.com:443/owner/repo.git"),
            "https://github.com/owner/repo",
        )
        self.assertEqual(
            canonical_repo_url("ssh://git@github.com:2222/owner/repo.git"),
            "https://github.com:2222/owner/repo",
        )

    def test_preserves_path_case_for_arbitrary_hosts(self):
        """Keep repository path case distinct on unknown Git hosts."""
        self.assertEqual(
            canonical_repo_url("https://git.example.test/Team/Plugin.git"),
            "https://git.example.test/Team/Plugin",
        )
        self.assertNotEqual(
            canonical_repo_url("https://git.example.test/Team/Plugin.git"),
            canonical_repo_url("https://git.example.test/team/plugin.git"),
        )

    def test_rejects_cleartext_and_embedded_credentials(self):
        """Reject HTTP and credential-bearing web URLs before Git sees them."""
        self.assertFalse(is_repo_url("http://github.com/owner/repo"))
        self.assertFalse(is_repo_url("https://user@github.com/owner/repo"))
        self.assertFalse(is_repo_url("https://user:token@github.com/owner/repo"))
        self.assertTrue(is_repo_url("ssh://git@github.com/owner/repo"))

    def test_rejects_plain_text_and_non_strings(self):
        """Reject search text, host-only URLs, and non-string values."""
        self.assertFalse(is_repo_url("hashdb"))
        self.assertFalse(is_repo_url("https://example.com"))
        self.assertFalse(is_repo_url(123))

    def test_repo_name(self):
        """Derive a stable display name from a repository URL."""
        self.assertEqual(repo_name_from_url("https://github.com/a/my-plugin.git"), "my-plugin")


class CatalogTests(unittest.TestCase):
    """Validate Markdown/JSON discovery catalog behavior."""

    def test_extensionless_json_payload_is_detected(self):
        """Treat valid JSON as JSON even without a suffix or MIME type."""
        provider = CatalogProvider("https://example.test/catalog")
        provider._load = lambda: (
            json.dumps(["https://github.com/example/one"]),
            "text/plain",
        )
        entries = provider.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "one")

    def test_json_catalog_rejects_invalid_url_types(self):
        """Skip malformed URL fields and normalize optional metadata types."""
        provider = CatalogProvider("https://example.test/catalog")
        provider._load = lambda: (
            json.dumps(
                {
                    "plugins": [
                        {"url": 123, "name": ["bad"]},
                        {
                            "url": "https://github.com/example/good",
                            "name": ["not a string"],
                            "description": {"bad": "shape"},
                        },
                    ]
                }
            ),
            "text/plain",
        )
        entries = provider.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "good")
        self.assertEqual(entries[0].description, "")


class GitProviderTests(unittest.TestCase):
    """Validate persistence and restoration of managed Git plugins."""

    def test_installed_repositories_are_restored_from_metadata(self):
        """Re-enumerate an installed checkout using persisted source metadata."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repos"
            active = Path(temp_dir) / "plugins"
            root.mkdir()
            active.mkdir()
            checkout = root / "sample-deadbeef00"
            (checkout / ".git").mkdir(parents=True)

            provider = GitProvider.__new__(GitProvider)
            provider.root = root
            provider.active_dir = active
            provider.metadata_path = root / "managed.json"
            provider.metadata_path.write_text(
                json.dumps({checkout.name: "https://github.com/example/sample"}),
                encoding="utf-8",
            )
            def fake_git(_repo, *args, **_kwargs):
                if "--is-inside-work-tree" in args:
                    return "true\n"
                if "--is-bare-repository" in args:
                    return "false\n"
                if "rev-parse" in args:
                    return "abc123\n"
                return ""

            provider._git = fake_git
            provider._update_available = lambda _repo: False

            entries = provider.entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source, PluginSource.GIT)
            self.assertTrue(entries[0].installed)
            self.assertEqual(entries[0].repo_url, "https://github.com/example/sample")

    def test_install_rejects_unsafe_urls_before_clone(self):
        """Do not invoke Git for cleartext or credential-bearing repository URLs."""
        provider = GitProvider.__new__(GitProvider)
        for url in (
            "http://github.com/example/plugin",
            "https://user:token@github.com/example/plugin",
        ):
            with self.subTest(url=url), patch("meta_binja.core.subprocess.run") as run:
                entry = types.SimpleNamespace(repo_url=url)
                self.assertFalse(provider.install(entry))
                run.assert_not_called()


def _entry(name, **kwargs):
    """Build a catalog entry for presentation and filtering tests."""
    defaults = {"id": f"test:{name}", "name": name, "source": PluginSource.CATALOG}
    defaults.update(kwargs)
    return PluginEntry(**defaults)


class RegistryLifecycleTests(unittest.TestCase):
    """Validate that native catalogs feed the unified Git lifecycle."""

    @staticmethod
    def _registry(native_entries, git_entries):
        """Build a registry with deterministic in-memory lifecycle providers."""
        class Native:
            def __init__(self):
                self.handoffs = []
                self.installs = []
                self.handoff_error = None

            def entries(self):
                return list(native_entries)

            def prepare_git_handoff(self, entry):
                self.handoffs.append(entry)
                if self.handoff_error is not None:
                    raise self.handoff_error
                return True

            def install(self, entry):
                self.installs.append(entry)
                return True

        class Git:
            def __init__(self):
                self.installs = []
                self.prepared = []
                self.enabled = []
                self.prepare_result = True
                self.enable_error = None

            def entries(self, check_updates=False):
                return list(git_entries)

            def prepare_install(self, entry):
                self.prepared.append(entry)
                return self.prepare_result

            def set_enabled(self, entry, enabled, install_requirements=True):
                self.enabled.append((entry, enabled, install_requirements))
                if self.enable_error is not None and enabled:
                    raise self.enable_error
                return True

            def install(self, entry):
                self.installs.append(entry)
                return True

        registry = PluginRegistry.__new__(PluginRegistry)
        registry.native = Native()
        registry.git = Git()
        registry._entries = {}
        registry.errors = []
        return registry

    def test_source_backed_native_state_comes_from_git_not_extension_manager(self):
        """A stale native install does not masquerade as the managed installation."""
        native = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            installed=True,
            enabled=True,
            native_installed=True,
        )
        registry = self._registry([native], [])

        entries = registry.refresh()

        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0], native)
        self.assertFalse(native.installed)
        self.assertFalse(native.enabled)
        self.assertTrue(native.native_installed)

    def test_native_only_catalog_entry_does_not_hide_matching_direct_git_checkout(self):
        """Compiled/native-only entries and direct Git installs remain independently manageable."""
        native = PluginEntry(
            id="native:community:compiled",
            name="Compiled",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/example/compiled",
            installed=True,
            enabled=True,
            git_installable=False,
        )
        git = PluginEntry(
            id="git:https://github.com/example/compiled",
            name="compiled",
            source=PluginSource.GIT,
            repo_url="https://github.com/example/compiled",
            installed=True,
            enabled=True,
        )
        registry = self._registry([native], [git])

        entries = registry.refresh()

        self.assertIn(native, entries)
        self.assertIn(git, entries)
        self.assertEqual(len(entries), 2)

    def test_git_state_is_overlaid_on_native_catalog_metadata(self):
        """Installed source-backed plugins keep native metadata but use Git lifecycle state."""
        native = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            version="3.0",
            install_subdir=None,
        )
        git = PluginEntry(
            id="git:https://github.com/elbiazo/calltree",
            name="calltree",
            source=PluginSource.GIT,
            repo_url="https://github.com/elbiazo/calltree",
            version="abc123",
            installed=True,
            enabled=True,
            local_path="/tmp/calltree",
        )
        registry = self._registry([native], [git])

        entries = registry.refresh()

        self.assertEqual(len(entries), 1)
        self.assertIs(entries[0], native)
        self.assertEqual(native.source, PluginSource.NATIVE)
        self.assertTrue(native.installed)
        self.assertTrue(native.enabled)
        self.assertEqual(native.version, "abc123")
        self.assertEqual(native.local_path, "/tmp/calltree")

    def test_calltree_install_handoffs_native_copy_then_uses_git(self):
        """Calltree and other source-backed native entries never use native install()."""
        entry = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            native_installed=True,
        )
        registry = self._registry([], [])

        self.assertTrue(registry.install(entry))

        self.assertEqual(registry.git.prepared, [entry])
        self.assertEqual(registry.git.enabled, [(entry, True, False)])
        self.assertEqual(registry.native.handoffs, [entry])
        self.assertEqual(registry.native.installs, [])
        self.assertEqual(registry.git.installs, [])

    def test_failed_git_activation_keeps_existing_native_install(self):
        """Git activation failure happens before native cleanup begins."""
        entry = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            native_installed=True,
        )
        registry = self._registry([], [])
        registry.git.enable_error = RuntimeError("activation failed")

        with self.assertRaisesRegex(RuntimeError, "activation failed"):
            registry.install(entry)

        self.assertEqual(registry.git.prepared, [entry])
        self.assertEqual(registry.git.enabled, [(entry, True, False)])
        self.assertEqual(registry.native.handoffs, [])

    def test_failed_native_cleanup_rolls_back_git_activation(self):
        """Native cleanup failure removes the newly activated Git copy."""
        entry = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            native_installed=True,
        )
        registry = self._registry([], [])
        registry.native.handoff_error = RuntimeError("native cleanup failed")

        with self.assertRaisesRegex(RuntimeError, "native cleanup failed"):
            registry.install(entry)

        self.assertEqual(registry.native.handoffs, [entry])
        self.assertEqual(
            registry.git.enabled,
            [
                (entry, True, False),
                (entry, False, False),
            ],
        )

    def test_failed_git_preflight_keeps_existing_native_install(self):
        """A clone/preflight failure happens before native migration cleanup."""
        entry = PluginEntry(
            id="native:community:elbiazo_calltree",
            name="Calltree",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/elbiazo/calltree",
            native_installed=True,
        )
        registry = self._registry([], [])
        registry.git.prepare_result = False

        self.assertFalse(registry.install(entry))

        self.assertEqual(registry.git.prepared, [entry])
        self.assertEqual(registry.native.handoffs, [])
        self.assertEqual(registry.git.enabled, [])


    def test_package_only_native_entry_keeps_native_fallback(self):
        """Extensions without a clonable project URL retain Binary Ninja's lifecycle."""
        entry = PluginEntry(
            id="native:official:package-only",
            name="Package Only",
            source=PluginSource.NATIVE,
        )
        registry = self._registry([], [])

        self.assertTrue(registry.install(entry))

        self.assertEqual(registry.native.installs, [entry])
        self.assertEqual(registry.git.installs, [])


    def test_compiled_native_entry_with_repo_url_keeps_native_fallback(self):
        """Compiled/prebuilt extensions are not treated as clone-and-run Python plugins."""
        entry = PluginEntry(
            id="native:community:compiled",
            name="Compiled",
            source=PluginSource.NATIVE,
            repo_url="https://github.com/example/compiled",
            git_installable=False,
        )
        registry = self._registry([], [])

        self.assertTrue(registry.install(entry))

        self.assertEqual(registry.native.installs, [entry])
        self.assertEqual(registry.git.installs, [])


class PresentationTests(unittest.TestCase):
    """Validate that source and lifecycle status stay independent facts."""

    def test_status_is_independent_of_source(self):
        """An installed native plugin reports its source and its state separately."""
        entry = _entry("Native plugin", source=PluginSource.NATIVE, installed=True, enabled=True)
        self.assertEqual(entry.source_label, "Native")
        self.assertEqual(entry.status_kind, "enabled")
        self.assertEqual(entry.status_label, "Enabled")

    def test_status_kinds_cover_every_lifecycle_state(self):
        """Each combination of installed/enabled/update maps to one status."""
        self.assertEqual(_entry("a").status_kind, "available")
        self.assertEqual(_entry("b", installed=True).status_kind, "disabled")
        self.assertEqual(_entry("c", installed=True, enabled=True).status_kind, "enabled")
        self.assertEqual(
            _entry("d", installed=True, enabled=True, update_available=True).status_kind, "update"
        )

    def test_update_flag_on_uninstalled_entry_is_not_a_status(self):
        """A stale update flag never makes an uninstalled plugin look installed."""
        self.assertEqual(_entry("e", update_available=True).status_kind, "available")


class FilterTests(unittest.TestCase):
    """Validate the list filters and search ranking."""

    def test_filters_select_the_expected_entries(self):
        """Each filter keeps only the entries it names."""
        available = _entry("available")
        installed = _entry("installed", installed=True, enabled=True)
        outdated = _entry("outdated", installed=True, enabled=True, update_available=True)
        self.assertTrue(matches_filter(installed, FILTER_INSTALLED))
        self.assertFalse(matches_filter(available, FILTER_INSTALLED))
        self.assertTrue(matches_filter(outdated, FILTER_UPDATES))
        self.assertFalse(matches_filter(installed, FILTER_UPDATES))
        self.assertTrue(matches_filter(available, FILTER_AVAILABLE))
        self.assertFalse(matches_filter(outdated, FILTER_AVAILABLE))

    def test_name_matches_outrank_description_matches(self):
        """A name hit ranks above an entry that only mentions the term."""
        named = _entry("HashDB")
        described = _entry("Other", description="works with hashdb lookups")
        tokens = ["hashdb"]
        self.assertLess(relevance(named, tokens), relevance(described, tokens))

    def test_exact_and_prefix_matches_rank_first(self):
        """An exact name beats a prefix match, which beats a substring match."""
        tokens = ["hash"]
        self.assertLess(relevance(_entry("hash"), tokens), relevance(_entry("hashdb"), tokens))
        self.assertLess(relevance(_entry("hashdb"), tokens), relevance(_entry("my hash tool"), tokens))


class CatalogCacheTests(unittest.TestCase):
    """Validate that catalogs are served from the TTL cache between refreshes."""

    def test_second_load_is_served_from_cache(self):
        """A cached catalog is reused instead of refetched."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonCache(Path(temp_dir) / "catalogs.json", 3600)
            calls = []

            def _fetch():
                calls.append(1)
                return json.dumps(["https://github.com/example/one"]), "application/json"

            first = CatalogProvider("https://example.test/catalog", cache)
            first._fetch = _fetch
            second = CatalogProvider("https://example.test/catalog", cache)
            second._fetch = _fetch

            self.assertEqual(len(first.entries()), 1)
            self.assertEqual(len(second.entries()), 1)
            self.assertEqual(len(calls), 1)

    def test_forced_load_bypasses_the_cache(self):
        """An explicit refresh refetches even when a cached copy exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonCache(Path(temp_dir) / "catalogs.json", 3600)
            calls = []

            def _fetch():
                calls.append(1)
                return json.dumps(["https://github.com/example/one"]), "application/json"

            cached = CatalogProvider("https://example.test/catalog", cache)
            cached._fetch = _fetch
            cached.entries()
            forced = CatalogProvider("https://example.test/catalog", cache, force=True)
            forced._fetch = _fetch
            forced.entries()
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
