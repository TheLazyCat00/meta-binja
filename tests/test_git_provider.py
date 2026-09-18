"""Regression tests for Git-backed plugin activation semantics."""

import importlib.util
import json
import subprocess
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
    """Exercise Git checkout identity, ownership, and activation lifecycle behavior."""

    def _provider(self, temp_dir):
        """Create an isolated provider rooted under a temporary directory."""
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
        """Create a real non-bare Git worktree at the provider's checkout path."""
        repo = provider.repo_path(url)
        subprocess.run(
            ["git", "init", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        return repo

    def test_activation_preserves_repository_case_but_storage_key_does_not(self):
        """Keep public Python package spelling separate from canonical storage identity."""
        url = "https://github.com/0Dr3f/RouteNinja.git"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = provider.repo_path(url)
            self.assertTrue(repo.name.startswith("routeninja-"))
            self.assertEqual(provider.active_path(url, repo).name, "RouteNinja")
        self.assertEqual(repo_name_from_url(url), "RouteNinja")
        self.assertEqual(repo_name_from_url("git@github.com:0Dr3f/RouteNinja.git"), "RouteNinja")

    def test_legacy_metadata_and_hash_activation_are_migrated(self):
        """Migrate a legacy activation only when old metadata proves ownership."""
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

    def test_unowned_legacy_activation_is_preserved(self):
        """Never rename or delete a hash-looking legacy path without ownership evidence."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            provider._remember(url, repo)
            legacy = provider.active_dir / repo.name
            legacy.mkdir()
            marker = legacy / "manual.txt"
            marker.write_text("keep", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url)

            desired = provider._migrate_legacy_activation(url, repo)
            self.assertEqual(desired, provider.active_dir / "Plugin")
            self.assertTrue(legacy.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

            self.assertTrue(provider.set_enabled(entry, False))
            self.assertTrue(legacy.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_persisted_activation_name_cannot_escape_plugin_directory(self):
        """Reject path traversal in persisted activation names."""
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
        """Leave an unrelated public plugin directory untouched on name collision."""
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
        """Mark copy fallbacks so later lifecycle actions can prove ownership."""
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

    def test_copy_fallback_cleans_partial_stage_on_failure(self):
        """A failed copy activation must leave neither a public partial copy nor staging debris."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            (repo / "plugin.py").write_text("pass\n", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url)

            real_copytree = __import__("shutil").copytree

            def failing_copytree(src, dst, *args, **kwargs):
                real_copytree(src, dst, *args, **kwargs)
                raise OSError("copy failed")

            with patch("meta_binja.git_provider.os.symlink", side_effect=OSError("unavailable")), patch(
                "meta_binja.git_provider.shutil.copytree",
                side_effect=failing_copytree,
            ):
                with self.assertRaisesRegex(OSError, "copy failed"):
                    provider.set_enabled(entry, True)

            self.assertFalse((provider.active_dir / "Plugin").exists())
            self.assertEqual(list(provider.active_dir.iterdir()), [])

    def test_transactional_copy_update_restores_old_activation_on_swap_failure(self):
        """Restore the previous copied activation if the staged replacement cannot be installed."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            active = provider.active_dir / "Plugin"
            active.mkdir()
            (active / "old.txt").write_text("old", encoding="utf-8")
            provider._write_copy_marker(active, repo)

            original_rename = Path.rename

            def guarded_rename(path, target):
                if ".meta-binja-stage-" in path.name and target == active:
                    raise OSError("swap failed")
                return original_rename(path, target)

            with patch.object(Path, "rename", guarded_rename):
                with self.assertRaisesRegex(OSError, "swap failed"):
                    provider._copy_activation_transactionally(repo, active)

            self.assertTrue(active.exists())
            self.assertEqual((active / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertTrue(provider._activation_owned_by(active, repo))

    def test_requirements_use_binary_ninjas_dependency_installer(self):
        """Delegate requirements installation to Binary Ninja's configured Python provider."""
        calls = []

        class PythonProvider:
            """Record dependency-installer invocations made by the provider."""

            def _install_modules(self, context, payload):
                """Pretend installation succeeded while recording its arguments."""
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
        """Keep a failed install disabled and retry dependencies on explicit re-enable."""
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

    def test_dependency_failure_removes_existing_owned_activation(self):
        """Dependency failure during re-enable must remove an already exposed owned plugin."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            active = provider.active_dir / "Plugin"
            active.mkdir()
            provider._write_copy_marker(active, repo)
            entry = types.SimpleNamespace(repo_url=url)

            with patch.object(provider, "_install_requirements", side_effect=RuntimeError("pip failed")):
                with self.assertRaisesRegex(RuntimeError, "pip failed"):
                    provider.set_enabled(entry, True)

            self.assertFalse(active.exists())

    def test_invalid_existing_checkout_is_recloned_during_install(self):
        """Recover an interrupted private checkout instead of exposing it as a plugin."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = provider.repo_path(url)
            repo.mkdir()
            (repo / "partial.txt").write_text("partial", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url)

            def fake_clone(_url, target):
                self.assertFalse((target / "partial.txt").exists())
                subprocess.run(["git", "init", str(target)], check=True, capture_output=True, text=True)
                return True

            with patch.object(provider, "_clone", side_effect=fake_clone) as clone, patch.object(
                provider, "_install_requirements", return_value=True
            ), patch("meta_binja.git_provider.os.symlink"):
                self.assertTrue(provider.install(entry))

            clone.assert_called_once_with(url, repo)
            self.assertTrue(provider._is_usable_worktree(repo))

    def test_direct_enable_rejects_invalid_checkout(self):
        """Do not allow direct Enable to bypass private worktree validation."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = provider.repo_path(url)
            repo.mkdir()
            entry = types.SimpleNamespace(repo_url=url)

            with self.assertRaisesRegex(RuntimeError, "not a usable Git worktree"):
                provider.set_enabled(entry, True)
            self.assertFalse((provider.active_dir / "Plugin").exists())

    def test_install_subdir_change_preserves_owned_activation(self):
        """Changing catalog subdir rebuilds an owned activation before persisting new metadata."""
        url = "https://github.com/example/Plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            (repo / "__init__.py").write_text("ROOT = True\n", encoding="utf-8")
            nested = repo / "integrations" / "binja"
            nested.mkdir(parents=True)
            (nested / "__init__.py").write_text("NESTED = True\n", encoding="utf-8")

            root_entry = types.SimpleNamespace(repo_url=url, install_subdir=None)
            nested_entry = types.SimpleNamespace(repo_url=url, install_subdir="integrations/binja")

            with patch.object(provider, "_install_requirements", return_value=True):
                self.assertTrue(provider.set_enabled(root_entry, True))

                active = provider.active_dir / "Plugin"
                self.assertTrue(active.is_symlink())
                self.assertTrue(provider._activation_owned_by(active, repo))

                self.assertTrue(provider.prepare_install(nested_entry))
                metadata = json.loads(provider.metadata_path.read_text(encoding="utf-8"))
                self.assertNotIn("install_subdir", metadata[repo.name])
                self.assertTrue(provider._activation_owned_by(active, repo))

                self.assertTrue(provider.set_enabled(nested_entry, True, install_requirements=False))
                self.assertFalse(active.is_symlink())
                self.assertTrue((active / "__init__.py").exists())
                metadata = json.loads(provider.metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(metadata[repo.name]["install_subdir"], "integrations/binja")
                self.assertTrue(provider._activation_owned_by(active, repo))

                self.assertTrue(provider.prepare_install(root_entry))
                self.assertTrue(provider.set_enabled(root_entry, True, install_requirements=False))
                metadata = json.loads(provider.metadata_path.read_text(encoding="utf-8"))
                self.assertNotIn("install_subdir", metadata[repo.name])
                self.assertTrue(provider._activation_owned_by(active, repo))
                self.assertTrue((active / "__init__.py").exists())

    def test_native_catalog_subdir_uses_nested_module_wrapper(self):
        """Mirror Binary Ninja's native loader by importing the catalog-declared nested module."""
        url = "https://github.com/example/monorepo-plugin"
        module_name = "monorepo-plugin-test"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            source = repo / "integrations" / "binja"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("LOADED = True\n", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url, install_subdir="integrations/binja")

            with patch.object(provider, "_install_requirements", return_value=True) as requirements:
                self.assertTrue(provider.install(entry))

            active = provider.active_dir / "monorepo-plugin"
            wrapper = active / "__init__.py"
            self.assertTrue(wrapper.exists())
            self.assertFalse(active.is_symlink())
            requirements.assert_called_once_with(repo, source)
            metadata = json.loads(provider.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata[repo.name]["install_subdir"], "integrations/binja")

            spec = importlib.util.spec_from_file_location(
                module_name,
                wrapper,
                submodule_search_locations=[str(active)],
            )
            module = importlib.util.module_from_spec(spec)
            sys_modules = __import__("sys").modules
            sys_modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                nested = sys_modules[f"{module_name}.integrations.binja"]
                self.assertTrue(nested.LOADED)
            finally:
                for name in list(sys_modules):
                    if name == module_name or name.startswith(module_name + "."):
                        sys_modules.pop(name, None)

    def test_subdir_wrapper_preserves_real_package_initializer(self):
        """A binsync-style subdir keeps the real top-level package semantics."""
        url = "https://github.com/example/binsync"
        module_name = "binsync"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            package = repo / "binsync"
            plugin = package / "stub_files"
            plugin.mkdir(parents=True)
            (package / "__init__.py").write_text(
                "PACKAGE_FILE = __file__\nROOT_VALUE = 42\n",
                encoding="utf-8",
            )
            (plugin / "__init__.py").write_text(
                "from binsync import ROOT_VALUE\nLOADED_VALUE = ROOT_VALUE\n",
                encoding="utf-8",
            )
            entry = types.SimpleNamespace(repo_url=url, install_subdir="binsync/stub_files")

            with patch.object(provider, "_install_requirements", return_value=True):
                self.assertTrue(provider.install(entry))

            active = provider.active_dir / "binsync"
            spec = importlib.util.spec_from_file_location(
                module_name,
                active / "__init__.py",
                submodule_search_locations=[str(active)],
            )
            module = importlib.util.module_from_spec(spec)
            sys_modules = __import__("sys").modules
            previous = sys_modules.pop(module_name, None)
            sys_modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                nested = sys_modules["binsync.stub_files"]
                self.assertEqual(module.ROOT_VALUE, 42)
                self.assertEqual(nested.LOADED_VALUE, 42)
                self.assertEqual(Path(module.PACKAGE_FILE), package / "__init__.py")
            finally:
                for name in list(sys_modules):
                    if name == module_name or name.startswith(module_name + "."):
                        sys_modules.pop(name, None)
                if previous is not None:
                    sys_modules[module_name] = previous

    def test_subdir_activation_is_a_small_wrapper_not_a_repo_copy(self):
        """Nested plugins keep source in the private checkout and expose only a wrapper package."""
        url = "https://github.com/example/monorepo-plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = self._checkout(provider, url)
            (repo / "root-only.txt").write_text("not plugin", encoding="utf-8")
            source = repo / "plugins" / "binja"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("", encoding="utf-8")
            (source / "plugin.txt").write_text("plugin", encoding="utf-8")
            entry = types.SimpleNamespace(repo_url=url, install_subdir="plugins/binja")

            with patch.object(provider, "_install_requirements", return_value=True):
                self.assertTrue(provider.install(entry))

            active = provider.active_dir / "monorepo-plugin"
            self.assertTrue((active / "__init__.py").exists())
            self.assertTrue((active / ".meta-binja-managed.json").exists())
            self.assertFalse((active / "plugin.txt").exists())
            self.assertFalse((active / "root-only.txt").exists())
            self.assertTrue(provider._activation_owned_by(active, repo))

    def test_subdir_traversal_is_rejected(self):
        """Catalog metadata cannot escape the managed checkout during activation."""
        url = "https://github.com/example/plugin"
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            self._checkout(provider, url)
            entry = types.SimpleNamespace(repo_url=url, install_subdir="../other")

            with self.assertRaisesRegex(ValueError, "Unsafe plugin subdirectory"):
                provider.install(entry)

            self.assertEqual(list(provider.active_dir.iterdir()), [])

    def test_root_and_subdir_requirements_are_both_installed(self):
        """Monorepo plugins may carry shared root requirements plus plugin-specific ones."""
        calls = []

        class PythonProvider:
            """Record Binary Ninja dependency-installer payloads."""

            def _install_modules(self, context, payload):
                """Pretend pip succeeded while retaining each requirements payload."""
                calls.append((context, payload))
                return True

        with tempfile.TemporaryDirectory() as temp_dir:
            provider = self._provider(temp_dir)
            repo = Path(temp_dir) / "repo"
            source = repo / "plugins" / "binja"
            source.mkdir(parents=True)
            (repo / "requirements.txt").write_text("networkx>=2.5\n", encoding="utf-8")
            (source / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")

            with patch.object(binaryninja, "PythonScriptingProvider", PythonProvider, create=True):
                self.assertTrue(provider._install_requirements(repo, source))

        self.assertEqual(
            calls,
            [
                (None, b"networkx>=2.5\n"),
                (None, b"requests>=2\n"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
