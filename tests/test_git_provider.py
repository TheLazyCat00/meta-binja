"""Regression tests for Git-backed plugin activation semantics."""

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stubs import install_binaryninja
except ImportError:  # pragma: no cover - direct test invocation
    from stubs import install_binaryninja

install_binaryninja()

import binaryninja

from meta_binja.core import GitProvider, repo_name_from_url


class GitActivationTests(unittest.TestCase):
    def _provider(self, temp_dir):
        root = Path(temp_dir) / "repos"
        active = Path(temp_dir) / "plugins"
        root.mkdir()
        active.mkdir()
        provider = GitProvider.__new__(GitProvider)
        provider.root = root
        provider.active_dir = active
        provider.metadata_path = root / "managed.json"
        return provider

    def _checkout(self, provider, url):
        repo = provider.repo_path(url)
        (repo / ".git").mkdir(parents=True)
        return repo

    def test_activation_preserves_repository_case_but_storage_key_does_not(self):
        url = "https://github.com/0Dr3f/RouteNinja.git"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = provider.repo_path(url)
            self.assertTrue(repo.name.startswith("routeninja-"))
            self.assertEqual(provider.active_path(url, repo).name, "RouteNinja")
        self.assertEqual(repo_name_from_url(url), "RouteNinja")
        self.assertEqual(repo_name_from_url("git@github.com:0Dr3f/RouteNinja.git"), "RouteNinja")

    def test_legacy_metadata_and_hash_activation_are_migrated(self):
        url = "https://github.com/0Dr3f/RouteNinja"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            provider.metadata_path.write_text(json.dumps({repo.name: url}), encoding="utf-8")
            legacy = provider.active_dir / repo.name
            legacy.mkdir()
            (legacy / "__init__.py").write_text("", encoding="utf-8")

            desired = provider._migrate_legacy_activation(url, repo)

            self.assertEqual(desired.name, "RouteNinja")
            self.assertTrue(desired.exists())
            self.assertFalse(legacy.exists())
            self.assertTrue(provider._activation_owned_by(desired, repo))
            metadata = json.loads(provider.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata[repo.name]["url"], url)
            self.assertEqual(metadata[repo.name]["activation_name"], "RouteNinja")

    def test_persisted_activation_name_cannot_escape_plugin_directory(self):
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            provider.metadata_path.write_text(
                json.dumps({repo.name: {"url": url, "activation_name": "../escape"}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unsafe plugin activation name"):
                provider.active_path(url, repo)

    def test_enable_refuses_to_overwrite_unmanaged_name_collision(self):
        url = "https://github.com/0Dr3f/RouteNinja"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            self._checkout(provider, url)
            active = provider.active_dir / "RouteNinja"
            active.mkdir()
            marker = active / "manual.txt"
            marker.write_text("keep", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url)

            with self.assertRaisesRegex(RuntimeError, "not managed by Meta Binja"):
                provider.set_enabled(entry, True)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_copy_fallback_is_marked_and_removed_safely(self):
        url = "https://github.com/0Dr3f/RouteNinja"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            (repo / "__init__.py").write_text("", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url)

            with patch("meta_binja.git_provider.os.symlink", side_effect=OSError("unavailable")):
                self.assertTrue(provider.set_enabled(entry, True))

            active = provider.active_dir / "RouteNinja"
            self.assertTrue(provider._activation_owned_by(active, repo))
            self.assertTrue((active / ".meta-binja-managed.json").exists())
            self.assertTrue(provider.set_enabled(entry, False))
            self.assertFalse(active.exists())

    def test_requirements_use_binary_ninjas_dependency_installer(self):
        calls = []

        class PythonProvider:
            def _install_modules(self, context, payload):
                calls.append((context, payload))
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            (repo / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")
            with patch.object(binaryninja, "PythonScriptingProvider", PythonProvider, create=True):
                self.assertTrue(provider._install_requirements(repo))

        self.assertEqual(calls, [(None, b"requests>=2\n")])

    def test_dependency_failure_does_not_create_activation_and_enable_retries(self):
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            entry = types.SimpleNamespace(repo_url=url)

            with patch.object(
                provider,
                "_install_requirements",
                side_effect=[RuntimeError("pip failed"), True],
            ) as install_requirements, patch(
                "meta_binja.git_provider.os.symlink",
                side_effect=OSError("unavailable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "pip failed"):
                    provider.install(entry)
                self.assertFalse((provider.active_dir / "Plugin").exists())

                self.assertTrue(provider.set_enabled(entry, True))
                self.assertTrue((provider.active_dir / "Plugin").exists())
                self.assertEqual(install_requirements.call_count, 2)

            self.assertTrue(repo.exists())


if __name__ == "__main__":
    unittest.main()
