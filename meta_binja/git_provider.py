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
from pathlib import Path
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


class GitProvider:
    """Manage arbitrary Git-backed plugins outside Binary Ninja's manager.

    Checkouts stay in a collision-safe private store. Enabling a plugin exposes
    that checkout under the normal Binary Ninja plugin directory using a stable
    case-preserving name such as ``RouteNinja`` so absolute self-imports work.
    """

    def __init__(self) -> None:
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
        """Read the registry and normalize the legacy ``key -> URL`` shape."""
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
                out[key] = {"url": value}
                continue
            if not isinstance(value, dict) or not isinstance(value.get("url"), str):
                continue
            record = {"url": value["url"]}
            activation_name = value.get("activation_name")
            if isinstance(activation_name, str) and activation_name:
                record["activation_name"] = activation_name
            out[key] = record
        return out

    def _write_metadata(self, metadata: Dict[str, Dict[str, str]]) -> None:
        """Persist the local checkout registry."""
        self.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    def _remember(self, url: str, repo: Path, activation_name: Optional[str] = None) -> None:
        """Persist source URL and, once known, the public activation name."""
        metadata = self._read_metadata()
        record = dict(metadata.get(repo.name, {}))
        record["url"] = url
        if activation_name:
            record["activation_name"] = _validated_activation_name(activation_name)
        metadata[repo.name] = record
        self._write_metadata(metadata)

    def _forget(self, repo: Path) -> None:
        """Remove a checkout from the persisted source registry."""
        metadata = self._read_metadata()
        if repo.name in metadata:
            metadata.pop(repo.name, None)
            self._write_metadata(metadata)

    @staticmethod
    def _marker_path(active: Path) -> Path:
        return active / _MANAGED_ACTIVATION_MARKER

    def _write_copy_marker(self, active: Path, repo: Path) -> None:
        self._marker_path(active).write_text(
            json.dumps({"checkout": str(repo.resolve())}, sort_keys=True),
            encoding="utf-8",
        )

    def _activation_owned_by(self, active: Path, repo: Path) -> bool:
        """Return whether an activation path is known to expose *repo*."""
        if active.is_symlink():
            try:
                return active.resolve() == repo.resolve()
            except OSError:
                return False
        if not active.is_dir():
            return False
        try:
            payload = json.loads(self._marker_path(active).read_text(encoding="utf-8"))
            checkout = payload.get("checkout") if isinstance(payload, dict) else None
            return isinstance(checkout, str) and Path(checkout).resolve() == repo.resolve()
        except (OSError, ValueError, TypeError):
            return False

    def _migrate_legacy_activation(self, url: str, repo: Path) -> Path:
        """Rename the old hash-suffixed activation to its package name.

        The old path is unambiguously Meta Binja-owned because it exactly
        matches the private checkout key. If the desired path is occupied, no
        destructive migration is attempted; enabling will report the conflict.
        """
        desired = self.active_path(url, repo)
        legacy = self._legacy_active_path(repo)
        if desired == legacy or desired.exists() or desired.is_symlink():
            return desired
        if not (legacy.exists() or legacy.is_symlink()):
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
        """Remove *active* only when Meta Binja can establish ownership."""
        if not (active.exists() or active.is_symlink()):
            return
        if not legacy and not self._activation_owned_by(active, repo):
            return
        if active.is_symlink() or active.is_file():
            active.unlink()
        else:
            shutil.rmtree(active)

    def _entry_from_checkout(self, url: str, repo: Path, check_updates: bool = True) -> PluginEntry:
        """Build a Git entry for a known local checkout."""
        installed = (repo / ".git").exists()
        active = self._migrate_legacy_activation(url, repo) if installed else self.active_path(url, repo)
        version = self._git(repo, "rev-parse", "--short", "HEAD", check=False).strip() if installed else None
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
            local_path=str(repo) if installed else None,
            backend=self,
        )

    def entry_from_url(self, url: str, check_updates: bool = True) -> PluginEntry:
        """Build a Git entry for a URL, including its current local state."""
        return self._entry_from_checkout(url, self.repo_path(url), check_updates)

    def entries(self, check_updates: bool = True):
        """Enumerate previously installed Git plugins after refresh or restart."""
        metadata = self._read_metadata()
        out = []
        seen = set()
        for repo in sorted(self.root.iterdir()):
            if not repo.is_dir() or not (repo / ".git").exists():
                continue
            record = metadata.get(repo.name, {})
            url = record.get("url", "")
            if not url:
                url = self._git(repo, "remote", "get-url", "origin", check=False).strip()
            if not is_repo_url(url):
                continue
            canonical = canonical_repo_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            self._remember(url, repo, record.get("activation_name"))
            out.append(self._entry_from_checkout(url, repo, check_updates))
        return out

    def _install_requirements(self, repo: Path) -> bool:
        """Install requirements through Binary Ninja's configured Python provider."""
        requirements = repo / "requirements.txt"
        if not requirements.is_file():
            return True
        payload = requirements.read_bytes()
        try:
            from binaryninja import PythonScriptingProvider

            provider = PythonScriptingProvider()
            installer = getattr(provider, "_install_modules", None)
            if installer is None:
                raise RuntimeError("Binary Ninja's Python dependency installer is unavailable")
            ok = bool(installer(None, payload))
        except Exception as exc:
            raise RuntimeError(f"Could not install Python dependencies from {requirements.name}: {exc}") from exc
        if not ok:
            raise RuntimeError(
                f"Could not install Python dependencies from {requirements.name}; "
                "see Binary Ninja's dependency log"
            )
        return True

    def install(self, entry) -> bool:
        """Clone and activate an arbitrary Git plugin."""
        if not entry.repo_url or not is_repo_url(entry.repo_url):
            return False
        repo = self.repo_path(entry.repo_url)
        if not repo.exists():
            result = subprocess.run(
                ["git", "clone", "--recursive", entry.repo_url, str(repo)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False
        self._remember(entry.repo_url, repo)
        return self.set_enabled(entry, True)

    def uninstall(self, entry) -> bool:
        """Deactivate and remove a managed Git plugin checkout."""
        assert entry.repo_url
        self.set_enabled(entry, False)
        repo = self.repo_path(entry.repo_url)
        if repo.exists():
            shutil.rmtree(repo)
        self._forget(repo)
        return True

    def set_enabled(self, entry, enabled: bool) -> bool:
        """Expose or hide a managed checkout in Binary Ninja's plugin directory."""
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        active = self._migrate_legacy_activation(entry.repo_url, repo)
        legacy = self._legacy_active_path(repo)
        if enabled:
            if not repo.exists():
                return False
            if active.exists() or active.is_symlink():
                if not self._activation_owned_by(active, repo):
                    raise RuntimeError(
                        f"Cannot enable {repo_name_from_url(entry.repo_url)}: {active} already exists "
                        "and is not managed by Meta Binja"
                    )
                self._install_requirements(repo)
                self._remember(entry.repo_url, repo, active.name)
                return True

            # Install dependencies before exposing the checkout. This also makes
            # an explicit re-enable a safe retry after a previous pip failure.
            self._install_requirements(repo)
            try:
                os.symlink(repo, active, target_is_directory=True)
            except (OSError, NotImplementedError):
                shutil.copytree(repo, active, ignore=shutil.ignore_patterns(".git"))
                self._write_copy_marker(active, repo)
            self._remember(entry.repo_url, repo, active.name)
            return True

        self._remove_managed_activation(active, repo)
        self._remove_managed_activation(legacy, repo, legacy=True)
        return True

    def update(self, entry) -> bool:
        """Fast-forward a plugin, reinstall requirements, and refresh copied activations."""
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        result = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only", "--recurse-submodules"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        try:
            self._install_requirements(repo)
        except Exception:
            # A checkout with unsatisfied dependencies must not remain exposed
            # for the next Binary Ninja restart.
            self.set_enabled(entry, False)
            raise
        active = self._migrate_legacy_activation(entry.repo_url, repo)
        if active.exists() and not active.is_symlink() and self._activation_owned_by(active, repo):
            shutil.rmtree(active)
            shutil.copytree(repo, active, ignore=shutil.ignore_patterns(".git"))
            self._write_copy_marker(active, repo)
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
