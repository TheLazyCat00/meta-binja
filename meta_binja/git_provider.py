"""Git-backed Binary Ninja plugin lifecycle support.

The private checkout identity and the public Python package identity are kept
separate: checkouts use a hash-suffixed key under ``meta-binja/repos`` while
the user plugin directory exposes the repository's case-preserving basename.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from typing import Dict, Optional
from urllib.parse import urlparse

from binaryninja import log_warn, user_directory

from .core import PluginEntry, PluginSource, canonical_repo_url, is_repo_url

_MANAGED_ACTIVATION_MARKER = ".meta-binja-managed.json"


def repo_name_from_url(value: str) -> str:
    """Return the repository basename while preserving source-URL case.

    Canonical repository identity may case-fold hosts such as GitHub, but the
    directory in Binary Ninja's plugin path is also a Python package name. Its
    spelling therefore must not inherit canonicalization.
    """
    value = value.strip()
    if value.startswith("git@"):
        path = value.split(":", 1)[1] if ":" in value else value
    else:
        try:
            path = urlparse(value).path
        except ValueError:
            path = value
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name.lower().endswith(".git"):
        name = name[:-4]
    return name or "plugin"


def _validated_activation_name(name: str) -> str:
    """Validate a single directory component used as a Python plugin package."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError(f"Unsafe plugin activation name: {name!r}")
    return name


def _validated_subdir(value: Optional[str]) -> Optional[str]:
    """Normalize a repository-relative plugin subdirectory without allowing traversal."""
    if not value:
        return None
    if "\x00" in value:
        raise ValueError(f"Unsafe plugin subdirectory: {value!r}")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part == ".." or ":" in part for part in path.parts):
        raise ValueError(f"Unsafe plugin subdirectory: {value!r}")
    parts = [part for part in path.parts if part not in {"", "."}]
    return "/".join(parts) or None


class GitProvider:
    """Manage arbitrary Git-backed plugins outside Binary Ninja's manager.

    Checkouts stay in a collision-safe private store. Enabling a plugin exposes
    that checkout under the normal Binary Ninja plugin directory using a stable
    case-preserving name such as ``RouteNinja`` so absolute self-imports work.
    """

    def __init__(self) -> None:
        """Initialize the private checkout store and public activation directory."""
        self.root = Path(user_directory()) / "meta-binja" / "repos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_dir = Path(user_directory()) / "plugins"
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.root / "managed.json"

    def _key(self, url: str) -> str:
        """Return the stable private-storage key for a repository URL."""
        canonical = canonical_repo_url(url)
        storage_name = canonical.rstrip("/").rsplit("/", 1)[-1] or "plugin"
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:10]
        return f"{storage_name}-{digest}"

    def repo_path(self, url: str) -> Path:
        """Return the managed checkout path for *url*."""
        return self.root / self._key(url)

    def _metadata_record(self, repo: Path) -> Dict[str, str]:
        """Return persisted lifecycle metadata for one private checkout."""
        return self._read_metadata().get(repo.name, {})

    def activation_name(self, url: str, repo: Optional[Path] = None) -> str:
        """Return the stable directory name exposed in Binary Ninja/plugins."""
        if repo is not None:
            remembered = self._metadata_record(repo).get("activation_name")
            if remembered:
                return _validated_activation_name(remembered)
        return _validated_activation_name(repo_name_from_url(url))

    def active_path(self, url: str, repo: Optional[Path] = None) -> Path:
        """Return the public Binary Ninja plugin path for *url*."""
        return self.active_dir / self.activation_name(url, repo)

    def _legacy_active_path(self, repo: Path) -> Path:
        """Return the old hash-suffixed activation path for migration."""
        return self.active_dir / repo.name

    def _read_metadata(self) -> Dict[str, Dict[str, str]]:
        """Read the registry and normalize the legacy ``key -> URL`` shape.

        A legacy string record is also explicit evidence that older Meta Binja
        owned an activation named after that checkout key. This lets migration
        remain automatic without treating a deterministic path name alone as
        ownership proof.
        """
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict):
            return {}

        out: Dict[str, Dict[str, str]] = {}
        for key, value in payload.items():
            key = str(key)
            if isinstance(value, str):
                out[key] = {"url": value, "legacy_activation_name": key}
                continue
            if not isinstance(value, dict) or not isinstance(value.get("url"), str):
                continue
            record = {"url": value["url"]}
            for field in ("activation_name", "legacy_activation_name"):
                candidate = value.get(field)
                if isinstance(candidate, str) and candidate:
                    record[field] = candidate
            subdir = value.get("install_subdir")
            if isinstance(subdir, str) and subdir:
                record["install_subdir"] = _validated_subdir(subdir)
            out[key] = record
        return out

    def _write_metadata(self, metadata: Dict[str, Dict[str, str]]) -> None:
        """Persist the local checkout registry."""
        self.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    def _remember(
        self,
        url: str,
        repo: Path,
        activation_name: Optional[str] = None,
        legacy_activation_name: Optional[str] = None,
        install_subdir: Optional[str] = None,
    ) -> None:
        """Persist source URL, activation ownership, and an optional plugin subdirectory."""
        metadata = self._read_metadata()
        record = dict(metadata.get(repo.name, {}))
        record["url"] = url
        if activation_name:
            record["activation_name"] = _validated_activation_name(activation_name)
        if legacy_activation_name:
            record["legacy_activation_name"] = _validated_activation_name(legacy_activation_name)
        normalized_subdir = _validated_subdir(install_subdir)
        if normalized_subdir:
            record["install_subdir"] = normalized_subdir
        elif install_subdir == "":
            record.pop("install_subdir", None)
        metadata[repo.name] = record
        self._write_metadata(metadata)

    def _forget(self, repo: Path) -> None:
        """Remove a checkout from the persisted source registry."""
        metadata = self._read_metadata()
        if repo.name in metadata:
            metadata.pop(repo.name, None)
            self._write_metadata(metadata)


    def _entry_subdir(self, entry, repo: Path) -> Optional[str]:
        """Return the normalized plugin subdirectory from the entry or persisted metadata."""
        if hasattr(entry, "install_subdir"):
            return _validated_subdir(getattr(entry, "install_subdir"))
        return _validated_subdir(self._metadata_record(repo).get("install_subdir"))

    def _activation_source(self, repo: Path, install_subdir: Optional[str] = None) -> Path:
        """Return the checkout directory Binary Ninja should expose as the plugin package."""
        normalized = _validated_subdir(install_subdir)
        source = repo if normalized is None else repo.joinpath(*normalized.split("/"))
        try:
            source.resolve().relative_to(repo.resolve())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Plugin subdirectory escapes its checkout: {normalized!r}") from exc
        if not source.is_dir():
            raise RuntimeError(f"Plugin subdirectory does not exist: {normalized or '.'}")
        return source

    @staticmethod
    def _marker_path(active: Path) -> Path:
        """Return the ownership-marker path inside a copied activation."""
        return active / _MANAGED_ACTIVATION_MARKER

    def _write_copy_marker(self, active: Path, repo: Path) -> None:
        """Mark a copied activation as owned by the given private checkout."""
        self._marker_path(active).write_text(
            json.dumps({"checkout": str(repo.resolve())}, sort_keys=True),
            encoding="utf-8",
        )

    def _activation_owned_by(self, active: Path, repo: Path) -> bool:
        """Return whether an activation path is known to expose this managed checkout."""
        if active.is_symlink():
            try:
                expected = self._activation_source(repo, self._metadata_record(repo).get("install_subdir"))
                return active.resolve() == expected.resolve()
            except (OSError, RuntimeError, ValueError):
                return False
        if not active.is_dir():
            return False
        try:
            payload = json.loads(self._marker_path(active).read_text(encoding="utf-8"))
            checkout = payload.get("checkout") if isinstance(payload, dict) else None
            return isinstance(checkout, str) and Path(checkout).resolve() == repo.resolve()
        except (OSError, ValueError, TypeError):
            return False

    def _legacy_activation_owned_by(self, legacy: Path, repo: Path) -> bool:
        """Return whether a legacy activation has independent ownership evidence."""
        if self._activation_owned_by(legacy, repo):
            return True
        remembered = self._metadata_record(repo).get("legacy_activation_name")
        return bool(remembered and _validated_activation_name(remembered) == legacy.name)

    @staticmethod
    def _remove_path(path: Path) -> None:
        """Remove a file, symlink, or directory without following directory symlinks."""
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

    def _is_usable_worktree(self, repo: Path) -> bool:
        """Return whether *repo* is a usable non-bare Git worktree."""
        if not repo.is_dir():
            return False
        inside = self._git(repo, "rev-parse", "--is-inside-work-tree", check=False).strip().lower()
        bare = self._git(repo, "rev-parse", "--is-bare-repository", check=False).strip().lower()
        return inside == "true" and bare == "false"

    def _migrate_legacy_activation(self, url: str, repo: Path) -> Path:
        """Move an owned old hash-suffixed activation to its package name."""
        desired = self.active_path(url, repo)
        legacy = self._legacy_active_path(repo)
        if desired == legacy or desired.exists() or desired.is_symlink():
            return desired
        if not (legacy.exists() or legacy.is_symlink()):
            return desired
        if not self._legacy_activation_owned_by(legacy, repo):
            log_warn(f"Meta Binja: preserving unmanaged legacy activation {legacy}")
            return desired
        try:
            legacy.rename(desired)
            if desired.is_dir() and not desired.is_symlink():
                self._write_copy_marker(desired, repo)
            self._remember(url, repo, desired.name)
        except OSError as exc:
            log_warn(f"Meta Binja: could not migrate activation {legacy.name} to {desired.name}: {exc}")
        return desired

    def _remove_managed_activation(self, active: Path, repo: Path, *, legacy: bool = False) -> None:
        """Remove an activation only when Meta Binja can establish ownership."""
        if not (active.exists() or active.is_symlink()):
            return
        owned = self._legacy_activation_owned_by(active, repo) if legacy else self._activation_owned_by(active, repo)
        if not owned:
            return
        self._remove_path(active)

    def _write_subdir_wrapper(self, target: Path, repo: Path, install_subdir: str, activation_name: str) -> None:
        """Create a package shim that imports a catalog-declared nested plugin module."""
        target.mkdir(parents=True, exist_ok=False)
        self._write_copy_marker(target, repo)

        repo_path = repo.resolve()
        package_path = repo_path / activation_name
        search_paths = []
        if package_path.is_dir():
            search_paths.append(str(package_path))
        search_paths.append(str(repo_path))
        dotted_subdir = _validated_subdir(install_subdir).replace("/", ".")

        wrapper = (
            "# Generated by Meta Binja. Changes will be replaced.\n"
            "import importlib as _importlib\n"
            "import sys as _sys\n"
            f"_SEARCH_PATHS = {search_paths!r}\n"
            "for _path in reversed(_SEARCH_PATHS):\n"
            "    if _path not in _sys.path:\n"
            "        _sys.path.insert(0, _path)\n"
            "__path__ = list(_SEARCH_PATHS)\n"
            f"_importlib.import_module('.{dotted_subdir}', __name__)\n"
        )
        (target / "__init__.py").write_text(wrapper, encoding="utf-8")

    def _subdir_activation_transactionally(
        self,
        repo: Path,
        active: Path,
        install_subdir: str,
    ) -> None:
        """Build and atomically swap a wrapper for a nested catalog plugin."""
        token = uuid.uuid4().hex
        staged = active.parent / f".{active.name}.meta-binja-stage-{token}"
        backup = active.parent / f".{active.name}.meta-binja-backup-{token}"
        try:
            self._write_subdir_wrapper(staged, repo, install_subdir, active.name)
            if active.exists() or active.is_symlink():
                if not self._activation_owned_by(active, repo):
                    raise RuntimeError(f"Refusing to replace unmanaged activation: {active}")
                active.rename(backup)
                try:
                    staged.rename(active)
                except Exception:
                    backup.rename(active)
                    raise
                self._remove_path(backup)
            else:
                staged.rename(active)
        finally:
            if staged.exists() or staged.is_symlink():
                self._remove_path(staged)
            if backup.exists() or backup.is_symlink():
                if not (active.exists() or active.is_symlink()):
                    backup.rename(active)
                else:
                    self._remove_path(backup)

    def _copy_activation_transactionally(self, repo: Path, active: Path, source: Optional[Path] = None) -> None:
        """Build a marked copy off-path, then swap it into the stable activation path.

        Rebuilding an existing copied activation uses a sibling backup because
        replacing a non-empty directory is not portable. If the final rename
        fails, the previous activation is restored before the exception escapes.
        """
        source = source or self._activation_source(repo, self._metadata_record(repo).get("install_subdir"))
        token = uuid.uuid4().hex
        staged = active.parent / f".{active.name}.meta-binja-stage-{token}"
        backup = active.parent / f".{active.name}.meta-binja-backup-{token}"
        try:
            shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".git"))
            self._write_copy_marker(staged, repo)
            if active.exists() or active.is_symlink():
                if not self._activation_owned_by(active, repo):
                    raise RuntimeError(f"Refusing to replace unmanaged activation: {active}")
                active.rename(backup)
                try:
                    staged.rename(active)
                except Exception:
                    backup.rename(active)
                    raise
                self._remove_path(backup)
            else:
                staged.rename(active)
        finally:
            if staged.exists() or staged.is_symlink():
                self._remove_path(staged)
            if backup.exists() or backup.is_symlink():
                if not (active.exists() or active.is_symlink()):
                    backup.rename(active)
                else:
                    self._remove_path(backup)

    def _entry_from_checkout(
        self,
        url: str,
        repo: Path,
        check_updates: bool = True,
        install_subdir: Optional[str] = None,
    ) -> PluginEntry:
        """Build a Git entry for a known local checkout."""
        installed = self._is_usable_worktree(repo)
        normalized_subdir = _validated_subdir(install_subdir or self._metadata_record(repo).get("install_subdir"))
        active = self._migrate_legacy_activation(url, repo) if installed else self.active_path(url, repo)
        version = self._git(repo, "rev-parse", "--short", "HEAD", check=False).strip() if installed else None
        local = None
        if installed:
            try:
                local = str(self._activation_source(repo, normalized_subdir))
            except RuntimeError:
                local = str(repo)
        return PluginEntry(
            id=f"git:{canonical_repo_url(url)}",
            name=repo_name_from_url(url),
            source=PluginSource.GIT,
            repo_url=url,
            version=version or None,
            installed=installed,
            enabled=installed and self._activation_owned_by(active, repo),
            update_available=self._update_available(repo) if installed and check_updates else False,
            source_name="Git repository",
            local_path=local,
            install_subdir=normalized_subdir,
            backend=self,
        )

    def entry_from_url(
        self,
        url: str,
        check_updates: bool = True,
        install_subdir: Optional[str] = None,
    ) -> PluginEntry:
        """Build a Git entry for a URL, including its current local state."""
        return self._entry_from_checkout(url, self.repo_path(url), check_updates, install_subdir)

    def entries(self, check_updates: bool = True):
        """Enumerate previously installed Git plugins after refresh or restart."""
        metadata = self._read_metadata()
        out = []
        seen = set()
        for repo in sorted(self.root.iterdir()):
            if not repo.is_dir():
                continue
            record = metadata.get(repo.name, {})
            url = record.get("url", "")
            if not url and self._is_usable_worktree(repo):
                url = self._git(repo, "remote", "get-url", "origin", check=False).strip()
            if not is_repo_url(url):
                continue
            canonical = canonical_repo_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            self._remember(
                url,
                repo,
                record.get("activation_name"),
                record.get("legacy_activation_name"),
                record.get("install_subdir"),
            )
            out.append(self._entry_from_checkout(url, repo, check_updates, record.get("install_subdir")))
        return out

    def _install_requirements(self, repo: Path, source: Optional[Path] = None) -> bool:
        """Install root and plugin-subdirectory requirements through Binary Ninja."""
        source = source or repo
        requirement_files = [repo / "requirements.txt"]
        if source != repo:
            requirement_files.append(source / "requirements.txt")

        seen = set()
        for requirements in requirement_files:
            try:
                key = requirements.resolve()
            except OSError:
                key = requirements
            if key in seen or not requirements.is_file():
                continue
            seen.add(key)
            payload = requirements.read_bytes()
            try:
                from binaryninja import PythonScriptingProvider

                provider = PythonScriptingProvider()
                installer = getattr(provider, "_install_modules", None)
                if installer is None:
                    raise RuntimeError("Binary Ninja's Python dependency installer is unavailable")
                ok = bool(installer(None, payload))
            except Exception as exc:
                raise RuntimeError(f"Could not install Python dependencies from {requirements}: {exc}") from exc
            if not ok:
                raise RuntimeError(
                    f"Could not install Python dependencies from {requirements}; "
                    "see Binary Ninja's dependency log"
                )
        return True

    def _clone(self, url: str, repo: Path) -> bool:
        """Clone *url* recursively into *repo* and verify the resulting worktree."""
        result = subprocess.run(
            ["git", "clone", "--recursive", url, str(repo)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and self._is_usable_worktree(repo)

    def prepare_install(self, entry) -> bool:
        """Clone, validate, and install dependencies without exposing the plugin yet.

        Native-to-Git migration uses this phase before removing the old native
        package, so clone, subdirectory, dependency, and activation-name
        failures cannot leave the user with neither installation.
        """
        if not entry.repo_url or not is_repo_url(entry.repo_url):
            return False
        repo = self.repo_path(entry.repo_url)
        if repo.exists() or repo.is_symlink():
            if not self._is_usable_worktree(repo):
                self._remove_path(repo)
                if not self._clone(entry.repo_url, repo):
                    self._remove_path(repo)
                    return False
        elif not self._clone(entry.repo_url, repo):
            self._remove_path(repo)
            return False

        install_subdir = _validated_subdir(getattr(entry, "install_subdir", None))
        source = self._activation_source(repo, install_subdir)
        self._remember(entry.repo_url, repo, install_subdir=install_subdir or "")

        active = self.active_path(entry.repo_url, repo)
        if (active.exists() or active.is_symlink()) and not self._activation_owned_by(active, repo):
            raise RuntimeError(
                f"Cannot enable {repo_name_from_url(entry.repo_url)}: {active} already exists "
                "and is not managed by Meta Binja"
            )
        self._install_requirements(repo, source)
        return True

    def install(self, entry) -> bool:
        """Prepare and activate an arbitrary Git plugin."""
        if not self.prepare_install(entry):
            return False
        return self.set_enabled(entry, True, install_requirements=False)

    def uninstall(self, entry) -> bool:
        """Deactivate and remove a managed Git plugin checkout."""
        assert entry.repo_url
        self.set_enabled(entry, False)
        repo = self.repo_path(entry.repo_url)
        self._remove_path(repo)
        self._forget(repo)
        return True

    def set_enabled(self, entry, enabled: bool, install_requirements: bool = True) -> bool:
        """Expose or hide a managed checkout in Binary Ninja's plugin directory."""
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        install_subdir = self._entry_subdir(entry, repo)
        if install_subdir:
            self._remember(entry.repo_url, repo, install_subdir=install_subdir)
        active = self._migrate_legacy_activation(entry.repo_url, repo)
        legacy = self._legacy_active_path(repo)
        if enabled:
            if not self._is_usable_worktree(repo):
                raise RuntimeError(f"Cannot enable {repo_name_from_url(entry.repo_url)}: checkout is not a usable Git worktree")
            source = self._activation_source(repo, install_subdir)
            if active.exists() or active.is_symlink():
                if not self._activation_owned_by(active, repo):
                    raise RuntimeError(
                        f"Cannot enable {repo_name_from_url(entry.repo_url)}: {active} already exists "
                        "and is not managed by Meta Binja"
                    )
                try:
                    if install_requirements:
                        self._install_requirements(repo, source)
                except Exception:
                    self._remove_managed_activation(active, repo)
                    raise
                if install_subdir:
                    self._subdir_activation_transactionally(repo, active, install_subdir)
                self._remember(entry.repo_url, repo, active.name, install_subdir=install_subdir)
                return True

            if install_requirements:
                self._install_requirements(repo, source)
            if install_subdir:
                self._subdir_activation_transactionally(repo, active, install_subdir)
            else:
                try:
                    os.symlink(source, active, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self._copy_activation_transactionally(repo, active, source)
            self._remember(entry.repo_url, repo, active.name, install_subdir=install_subdir)
            return True

        self._remove_managed_activation(active, repo)
        self._remove_managed_activation(legacy, repo, legacy=True)
        return True

    def update(self, entry) -> bool:
        """Fast-forward a plugin, reinstall requirements, and refresh copied activations."""
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        if not self._is_usable_worktree(repo):
            raise RuntimeError(f"Cannot update {repo_name_from_url(entry.repo_url)}: checkout is not a usable Git worktree")
        result = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only", "--recurse-submodules"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        install_subdir = self._entry_subdir(entry, repo)
        source = self._activation_source(repo, install_subdir)
        active = self._migrate_legacy_activation(entry.repo_url, repo)
        try:
            self._install_requirements(repo, source)
        except Exception:
            self._remove_managed_activation(active, repo)
            raise
        if active.exists() and self._activation_owned_by(active, repo):
            if install_subdir:
                self._subdir_activation_transactionally(repo, active, install_subdir)
            elif not active.is_symlink():
                self._copy_activation_transactionally(repo, active, source)
        return True

    @staticmethod
    def _git(repo: Path, *args: str, check=True) -> str:
        """Run a Git command in *repo* and return stdout."""
        if not repo.exists():
            return ""
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def _update_available(self, repo: Path) -> bool:
        """Return whether the tracked upstream commit differs from local HEAD."""
        try:
            subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], capture_output=True, timeout=10)
            upstream = self._git(repo, "rev-parse", "@{u}", check=False).strip()
            head = self._git(repo, "rev-parse", "HEAD", check=False).strip()
            return bool(upstream and head and upstream != head)
        except Exception:
            return False
