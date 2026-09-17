from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from binaryninja import RepositoryManager, Settings, log_warn, user_directory

PREFIX = "metaBinja"
CATALOG_SOURCES = f"{PREFIX}.catalogSources"
_CASE_INSENSITIVE_REPO_PATH_HOSTS = {"github.com"}


class PluginSource(str, Enum):
    NATIVE = "native"
    GIT = "git"
    CATALOG = "catalog"


@dataclass
class PluginEntry:
    id: str
    name: str
    source: PluginSource
    description: str = ""
    repo_url: Optional[str] = None
    author: Optional[str] = None
    version: Optional[str] = None
    installed: bool = False
    enabled: bool = False
    update_available: bool = False
    source_name: str = ""
    backend: Any = field(default=None, repr=False, compare=False)

    @property
    def searchable_text(self) -> str:
        """Return normalized text used by the unified search UI."""
        return " ".join(
            filter(None, [self.name, self.description, self.repo_url or "", self.author or "", self.source_name])
        ).lower()


_GIT_URL_RE = re.compile(r"^(?:(?:https|ssh)://|git@)[^\s]+(?:\.git)?/?$", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def register_settings() -> None:
    """Register Meta Binja settings with Binary Ninja."""
    settings = Settings()
    settings.register_group(f"{PREFIX}.general", "Meta Binja")
    settings.register_setting(
        CATALOG_SOURCES,
        json.dumps(
            {
                "title": "Additional plugin catalog URLs",
                "type": "array",
                "elementType": "string",
                "default": [],
                "description": "GitHub repository URLs, raw Markdown awesome-lists, or JSON plugin catalogs to include in search.",
                "ignore": [],
            }
        ),
    )


def catalog_sources() -> List[str]:
    """Return configured additional discovery catalog URLs."""
    return list(Settings().get_string_list(CATALOG_SOURCES))


def is_repo_url(value: str) -> bool:
    """Return whether *value* is a credential-safe HTTPS or SSH repository URL."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not _GIT_URL_RE.match(value):
        return False
    if value.startswith("git@"):
        return ":" in value
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"https", "ssh"} or not parsed.hostname:
        return False
    if parsed.password is not None or (parsed.scheme.lower() == "https" and parsed.username is not None):
        return False
    try:
        parsed.port
    except ValueError:
        return False
    return len([p for p in parsed.path.strip("/").split("/") if p]) >= 2


def canonical_repo_url(value: str) -> str:
    """Normalize repository URLs without collapsing case-sensitive repository paths.

    Hosts are case-insensitive. SSH usernames and default transport ports are not
    part of repository identity. Repository paths are preserved unless the host
    is explicitly known to use case-insensitive owner/repository identifiers.
    """
    value = value.strip()
    if value.startswith("git@"):
        host_path = value.split("@", 1)[1]
        host, path = host_path.split(":", 1)
        value = f"ssh://git@{host}/{path}"
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    default_port = {"https": 443, "ssh": 22}.get(parsed.scheme.lower())
    host = hostname if port is None or port == default_port else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if hostname in _CASE_INSENSITIVE_REPO_PATH_HOSTS:
        path = path.lower()
    return f"https://{host}{path}"


def repo_name_from_url(value: str) -> str:
    """Extract a display-safe repository name from a repository URL."""
    return canonical_repo_url(value).rstrip("/").rsplit("/", 1)[-1] or "plugin"


def github_repo_parts(value: str):
    """Return ``(owner, repo)`` for a plain GitHub repository URL."""
    parsed = urlparse(value)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if parsed.netloc.lower() != "github.com" or len(parts) != 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


class NativeProvider:
    """Adapter around Binary Ninja's native Extension Manager API."""

    def __init__(self) -> None:
        self.manager = RepositoryManager()

    def refresh(self) -> None:
        """Ask Binary Ninja to refresh configured extension repositories."""
        self.manager.check_for_updates()

    def entries(self) -> List[PluginEntry]:
        """Convert native extensions into the unified entry model."""
        out = []
        for repo in self.manager.repositories:
            for ext in repo.plugins:
                try:
                    version = ext.current_version.version
                except Exception:
                    version = None
                out.append(
                    PluginEntry(
                        id=f"native:{repo.path}:{ext.path}",
                        name=ext.name,
                        source=PluginSource.NATIVE,
                        description=ext.long_description or "",
                        repo_url=ext.project_url,
                        author=ext.author,
                        version=version,
                        installed=ext.installed,
                        enabled=ext.enabled,
                        update_available=ext.update_available,
                        source_name=repo.path,
                        backend=ext,
                    )
                )
        return out

    @staticmethod
    def install(entry):
        """Install a native extension through Binary Ninja."""
        return bool(entry.backend.install())

    @staticmethod
    def uninstall(entry):
        """Uninstall a native extension through Binary Ninja."""
        return bool(entry.backend.uninstall())

    @staticmethod
    def set_enabled(entry, enabled):
        """Enable or disable a native extension."""
        if enabled:
            return bool(entry.backend.enable())
        entry.backend.enabled = False
        return True

    @staticmethod
    def update(entry):
        """Install the latest native extension version."""
        return bool(entry.backend.install(entry.backend.latest_version_id))


class CatalogProvider:
    """Read-only discovery provider for repo URLs, Markdown lists, and JSON catalogs."""

    def __init__(self, url: str) -> None:
        self.url = url.strip()

    def _load(self):
        """Fetch a configured catalog and return its decoded text and content type."""
        parts = github_repo_parts(self.url)
        if parts:
            owner, repo = parts
            request = urllib.request.Request(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "meta-binja"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
            raw = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
            return raw, "text/markdown"
        with urllib.request.urlopen(self.url, timeout=10) as response:
            return response.read().decode("utf-8", errors="replace"), response.headers.get("content-type", "")

    def entries(self) -> List[PluginEntry]:
        """Parse all discoverable plugin entries from this catalog."""
        raw, content_type = self._load()
        if "json" in content_type or self.url.lower().endswith(".json"):
            return self._json(raw)

        # Some raw/extensionless endpoints serve JSON as text/plain or omit a
        # useful Content-Type entirely. Honor the documented JSON catalog shape
        # before falling back to Markdown parsing.
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, list) or (
            isinstance(payload, dict) and ("plugins" in payload or "entries" in payload)
        ):
            return self._json(raw)
        return self._markdown(raw)

    def _markdown(self, raw: str) -> List[PluginEntry]:
        """Extract repository links from an awesome-list style Markdown file."""
        out, seen = [], set()
        for name, url in _MD_LINK_RE.findall(raw):
            if not is_repo_url(url):
                continue
            parsed = urlparse(canonical_repo_url(url))
            if parsed.netloc.lower() not in {"github.com", "gitlab.com", "codeberg.org", "bitbucket.org"}:
                continue
            canonical = canonical_repo_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append(
                PluginEntry(
                    id=f"catalog:{canonical}",
                    name=name.strip() or repo_name_from_url(url),
                    source=PluginSource.CATALOG,
                    repo_url=url,
                    source_name=self.url,
                )
            )
        return out

    def _json(self, raw: str) -> List[PluginEntry]:
        """Parse the simple JSON catalog format accepted by Meta Binja."""
        payload = json.loads(raw)
        items = payload.get("plugins", payload.get("entries", [])) if isinstance(payload, dict) else payload
        out = []
        if not isinstance(items, list):
            return out
        for item in items:
            if isinstance(item, str):
                url = item
                if not is_repo_url(url):
                    continue
                name = repo_name_from_url(url)
                desc = ""
            elif isinstance(item, dict):
                url = item.get("repo_url") or item.get("url") or item.get("repository")
                if not isinstance(url, str) or not is_repo_url(url):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    name = repo_name_from_url(url)
                else:
                    name = name.strip()
                desc = item.get("description", "")
                if not isinstance(desc, str):
                    desc = ""
            else:
                continue
            out.append(
                PluginEntry(
                    id=f"catalog:{canonical_repo_url(url)}",
                    name=name,
                    source=PluginSource.CATALOG,
                    description=desc,
                    repo_url=url,
                    source_name=self.url,
                )
            )
        return out


class GitProvider:
    """Manage arbitrary Git-backed plugins outside Binary Ninja's repository manager."""

    def __init__(self) -> None:
        self.root = Path(user_directory()) / "meta-binja" / "repos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_dir = Path(user_directory()) / "plugins"
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.root / "managed.json"

    def _key(self, url: str) -> str:
        """Return a stable local storage key for a repository URL."""
        digest = hashlib.sha256(canonical_repo_url(url).encode()).hexdigest()[:10]
        return f"{repo_name_from_url(url)}-{digest}"

    def repo_path(self, url):
        """Return the managed checkout path for *url*."""
        return self.root / self._key(url)

    def active_path(self, url):
        """Return the active Binary Ninja plugin path for *url*."""
        return self.active_dir / self._key(url)

    def _read_metadata(self) -> Dict[str, str]:
        """Read the local key-to-repository URL registry."""
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, str)}

    def _write_metadata(self, metadata: Dict[str, str]) -> None:
        """Persist the local key-to-repository URL registry atomically enough for this UI workflow."""
        self.metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    def _remember(self, url: str, repo: Path) -> None:
        """Persist the source URL for a managed checkout."""
        metadata = self._read_metadata()
        metadata[repo.name] = url
        self._write_metadata(metadata)

    def _forget(self, repo: Path) -> None:
        """Remove a checkout from the persisted source registry."""
        metadata = self._read_metadata()
        if repo.name in metadata:
            metadata.pop(repo.name, None)
            self._write_metadata(metadata)

    def _entry_from_checkout(self, url: str, repo: Path) -> PluginEntry:
        """Build a Git entry for a known local checkout."""
        active = self.active_dir / repo.name
        installed = (repo / ".git").exists()
        version = self._git(repo, "rev-parse", "--short", "HEAD", check=False).strip() if installed else None
        return PluginEntry(
            id=f"git:{canonical_repo_url(url)}",
            name=repo_name_from_url(url),
            source=PluginSource.GIT,
            repo_url=url,
            version=version or None,
            installed=installed,
            enabled=active.exists() or active.is_symlink(),
            update_available=self._update_available(repo) if installed else False,
            source_name="Git repository",
            backend=self,
        )

    def entry_from_url(self, url: str) -> PluginEntry:
        """Build a Git entry for a URL, including its current local state."""
        return self._entry_from_checkout(url, self.repo_path(url))

    def entries(self) -> List[PluginEntry]:
        """Enumerate previously installed Git plugins after refresh or restart."""
        metadata = self._read_metadata()
        out = []
        seen = set()
        for repo in sorted(self.root.iterdir()):
            if not repo.is_dir() or not (repo / ".git").exists():
                continue
            url = metadata.get(repo.name)
            if not url:
                url = self._git(repo, "remote", "get-url", "origin", check=False).strip()
            if not is_repo_url(url):
                continue
            canonical = canonical_repo_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            self._remember(url, repo)
            out.append(self._entry_from_checkout(url, repo))
        return out

    def install(self, entry) -> bool:
        """Clone and activate an arbitrary Git plugin."""
        if not entry.repo_url or not is_repo_url(entry.repo_url):
            return False
        repo = self.repo_path(entry.repo_url)
        if not repo.exists():
            result = subprocess.run(
                ["git", "clone", "--recursive", entry.repo_url, str(repo)], capture_output=True, text=True
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
        """Expose or hide a managed Git checkout in Binary Ninja's plugin directory."""
        assert entry.repo_url
        repo, active = self.repo_path(entry.repo_url), self.active_path(entry.repo_url)
        if enabled:
            if not repo.exists():
                return False
            if active.exists() or active.is_symlink():
                return True
            try:
                os.symlink(repo, active, target_is_directory=True)
            except (OSError, NotImplementedError):
                shutil.copytree(repo, active, ignore=shutil.ignore_patterns(".git"))
            return True
        if active.is_symlink():
            active.unlink()
        elif active.exists():
            shutil.rmtree(active)
        return True

    def update(self, entry) -> bool:
        """Fast-forward a managed Git plugin and refresh copy-based activation if needed."""
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        result = subprocess.run(
            ["git", "-C", str(repo), "pull", "--ff-only", "--recurse-submodules"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        active = self.active_path(entry.repo_url)
        if active.exists() and not active.is_symlink():
            shutil.rmtree(active)
            shutil.copytree(repo, active, ignore=shutil.ignore_patterns(".git"))
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


class PluginRegistry:
    """Aggregate native, Git, and discovery-catalog providers into one model."""

    def __init__(self) -> None:
        self.native, self.git = NativeProvider(), GitProvider()
        self._entries: Dict[str, PluginEntry] = {}

    @staticmethod
    def _copy_git_state(catalog: PluginEntry, git_entry: PluginEntry) -> PluginEntry:
        """Overlay Git lifecycle state while retaining catalog presentation metadata."""
        catalog.installed = git_entry.installed
        catalog.enabled = git_entry.enabled
        catalog.update_available = git_entry.update_available
        catalog.version = git_entry.version or catalog.version
        catalog.backend = git_entry.backend
        return catalog

    def refresh(self, check_native_updates=False) -> List[PluginEntry]:
        """Refresh and deduplicate entries from every configured provider."""
        if check_native_updates:
            try:
                self.native.refresh()
            except Exception as exc:
                log_warn(f"Meta Binja: native repository refresh failed: {exc}")

        native_entries = self.native.entries()
        git_entries = self.git.entries()
        combined = list(native_entries)
        by_repo = {canonical_repo_url(e.repo_url): e for e in native_entries if e.repo_url}

        # Native Extension Manager entries always win. Otherwise preserve direct
        # Git installations so they remain visible after refresh/restart.
        for entry in git_entries:
            canonical = canonical_repo_url(entry.repo_url) if entry.repo_url else None
            if canonical and canonical in by_repo:
                continue
            if canonical:
                by_repo[canonical] = entry
            combined.append(entry)

        for source in catalog_sources():
            try:
                entries = CatalogProvider(source).entries()
            except Exception as exc:
                log_warn(f"Meta Binja: failed to load catalog {source}: {exc}")
                continue
            for entry in entries:
                canonical = canonical_repo_url(entry.repo_url) if entry.repo_url else None
                existing = by_repo.get(canonical) if canonical else None
                if existing is not None:
                    if existing.source is PluginSource.GIT:
                        # Prefer catalog metadata for presentation while retaining
                        # the installed Git repository's lifecycle state.
                        merged = self._copy_git_state(entry, existing)
                        combined = [candidate for candidate in combined if candidate is not existing]
                        combined.append(merged)
                        by_repo[canonical] = merged
                    continue
                if canonical:
                    by_repo[canonical] = entry
                combined.append(entry)

        self._entries = {e.id: e for e in combined}
        return list(self._entries.values())

    def search(self, query: str) -> List[PluginEntry]:
        """Search unified entries, treating repository URLs as direct lookups."""
        query = query.strip()
        if is_repo_url(query):
            canonical = canonical_repo_url(query)
            for entry in self._entries.values():
                if entry.repo_url and canonical_repo_url(entry.repo_url) == canonical:
                    return [entry]
            return [self.git.entry_from_url(query)]
        tokens = query.lower().split()
        entries = list(self._entries.values())
        if tokens:
            entries = [e for e in entries if all(t in e.searchable_text for t in tokens)]
        return sorted(entries, key=lambda e: (not e.installed, e.name.lower()))

    def install(self, entry):
        """Install an entry through its native or Git lifecycle backend."""
        if entry.source is PluginSource.NATIVE:
            return self.native.install(entry)
        if entry.source is PluginSource.CATALOG and entry.repo_url:
            entry = self.git.entry_from_url(entry.repo_url)
        return self.git.install(entry)

    def uninstall(self, entry):
        """Uninstall an entry through its native or Git lifecycle backend."""
        return self.native.uninstall(entry) if entry.source is PluginSource.NATIVE else self.git.uninstall(entry)

    def set_enabled(self, entry, enabled):
        """Enable or disable an entry through its lifecycle backend."""
        return self.native.set_enabled(entry, enabled) if entry.source is PluginSource.NATIVE else self.git.set_enabled(entry, enabled)

    def update(self, entry):
        """Update an entry through its native or Git lifecycle backend."""
        return self.native.update(entry) if entry.source is PluginSource.NATIVE else self.git.update(entry)
