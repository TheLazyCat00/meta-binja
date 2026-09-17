"""Regression tests for Meta Binja's provider and URL handling."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


if "binaryninja" not in sys.modules:
    binaryninja = types.ModuleType("binaryninja")

    class _RepositoryManager:
        """Minimal RepositoryManager stub for tests outside Binary Ninja."""

        repositories = []

        def check_for_updates(self):
            """Pretend native repository refresh succeeded."""
            return True

    class _Settings:
        """Minimal Binary Ninja settings stub used by core imports."""

        def register_group(self, *_args, **_kwargs):
            """Accept a settings group registration."""
            return True

        def register_setting(self, *_args, **_kwargs):
            """Accept a settings key registration."""
            return True

        def get_string_list(self, _key):
            """Return no configured external catalogs by default."""
            return []

    binaryninja.RepositoryManager = _RepositoryManager
    binaryninja.Settings = _Settings
    binaryninja.log_warn = lambda *_args, **_kwargs: None
    binaryninja.user_directory = lambda: tempfile.gettempdir()
    sys.modules["binaryninja"] = binaryninja

from meta_binja.core import (
    CatalogProvider,
    GitProvider,
    PluginSource,
    canonical_repo_url,
    is_repo_url,
    repo_name_from_url,
)


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
        """Normalize SCP-style GitHub SSH clone URLs."""
        self.assertTrue(is_repo_url("git@github.com:owner/repo.git"))
        self.assertEqual(
            canonical_repo_url("git@github.com:owner/repo.git"),
            "https://github.com/owner/repo",
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
            provider._git = lambda _repo, *args, **_kwargs: "abc123\n" if "rev-parse" in args else ""
            provider._update_available = lambda _repo: False

            entries = provider.entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source, PluginSource.GIT)
            self.assertTrue(entries[0].installed)
            self.assertEqual(entries[0].repo_url, "https://github.com/example/sample")


if __name__ == "__main__":
    unittest.main()
