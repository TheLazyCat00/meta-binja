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
        return " ".join(filter(None, [self.name, self.description, self.repo_url or "", self.author or "", self.source_name])).lower()


_GIT_URL_RE = re.compile(r"^(?:(?:https?|ssh)://|git@)[^\s]+(?:\.git)?/?$", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def register_settings() -> None:
    settings = Settings()
    settings.register_group(f"{PREFIX}.general", "Meta Binja")
    settings.register_setting(CATALOG_SOURCES, json.dumps({
        "title": "Additional plugin catalog URLs",
        "type": "array",
        "elementType": "string",
        "default": [],
        "description": "GitHub repository URLs, raw Markdown awesome-lists, or JSON plugin catalogs to include in search.",
        "ignore": []
    }))


def catalog_sources() -> List[str]:
    return list(Settings().get_string_list(CATALOG_SOURCES))


def is_repo_url(value: str) -> bool:
    value = value.strip()
    if not _GIT_URL_RE.match(value):
        return False
    if value.startswith("git@"):
        return ":" in value
    parsed = urlparse(value)
    return len([p for p in parsed.path.strip("/").split("/") if p]) >= 2


def canonical_repo_url(value: str) -> str:
    value = value.strip()
    if value.startswith("git@"):
        host_path = value.split("@", 1)[1]
        host, path = host_path.split(":", 1)
        value = f"https://{host}/{path}"
    parsed = urlparse(value)
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"https://{parsed.netloc.lower()}{path}".lower()


def repo_name_from_url(value: str) -> str:
    return canonical_repo_url(value).rstrip("/").rsplit("/", 1)[-1] or "plugin"


def github_repo_parts(value: str):
    parsed = urlparse(value)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if parsed.netloc.lower() != "github.com" or len(parts) != 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


class NativeProvider:
    def __init__(self) -> None:
        self.manager = RepositoryManager()

    def refresh(self) -> None:
        self.manager.check_for_updates()

    def entries(self) -> List[PluginEntry]:
        out = []
        for repo in self.manager.repositories:
            for ext in repo.plugins:
                try:
                    version = ext.current_version.version
                except Exception:
                    version = None
                out.append(PluginEntry(
                    id=f"native:{repo.path}:{ext.path}", name=ext.name, source=PluginSource.NATIVE,
                    description=ext.long_description or "", repo_url=ext.project_url, author=ext.author,
                    version=version, installed=ext.installed, enabled=ext.enabled,
                    update_available=ext.update_available, source_name=repo.path, backend=ext
                ))
        return out

    @staticmethod
    def install(entry): return bool(entry.backend.install())

    @staticmethod
    def uninstall(entry): return bool(entry.backend.uninstall())

    @staticmethod
    def set_enabled(entry, enabled):
        if enabled:
            return bool(entry.backend.enable())
        entry.backend.enabled = False
        return True

    @staticmethod
    def update(entry): return bool(entry.backend.install(entry.backend.latest_version_id))


class CatalogProvider:
    """Read-only discovery provider for repo URLs, Markdown lists, and JSON catalogs."""

    def __init__(self, url: str) -> None:
        self.url = url.strip()

    def _load(self):
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
        raw, content_type = self._load()
        if "json" in content_type or self.url.lower().endswith(".json"):
            return self._json(raw)
        return self._markdown(raw)

    def _markdown(self, raw: str) -> List[PluginEntry]:
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
            out.append(PluginEntry(
                id=f"catalog:{canonical}", name=name.strip() or repo_name_from_url(url),
                source=PluginSource.CATALOG, repo_url=url, source_name=self.url
            ))
        return out

    def _json(self, raw: str) -> List[PluginEntry]:
        payload = json.loads(raw)
        items = payload.get("plugins", payload.get("entries", [])) if isinstance(payload, dict) else payload
        out = []
        if not isinstance(items, list):
            return out
        for item in items:
            if isinstance(item, str):
                url, name, desc = item, repo_name_from_url(item) if is_repo_url(item) else item, ""
            elif isinstance(item, dict):
                url = item.get("repo_url") or item.get("url") or item.get("repository")
                name = item.get("name") or (repo_name_from_url(url) if url else "Plugin")
                desc = item.get("description", "")
            else:
                continue
            if not url or not is_repo_url(url):
                continue
            out.append(PluginEntry(
                id=f"catalog:{canonical_repo_url(url)}", name=name, source=PluginSource.CATALOG,
                description=desc, repo_url=url, source_name=self.url
            ))
        return out


class GitProvider:
    def __init__(self) -> None:
        self.root = Path(user_directory()) / "meta-binja" / "repos"
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_dir = Path(user_directory()) / "plugins"
        self.active_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> str:
        digest = hashlib.sha256(canonical_repo_url(url).encode()).hexdigest()[:10]
        return f"{repo_name_from_url(url)}-{digest}"

    def repo_path(self, url): return self.root / self._key(url)
    def active_path(self, url): return self.active_dir / self._key(url)

    def entry_from_url(self, url: str) -> PluginEntry:
        repo, active = self.repo_path(url), self.active_path(url)
        installed = (repo / ".git").exists()
        version = self._git(repo, "rev-parse", "--short", "HEAD", check=False).strip() if installed else None
        return PluginEntry(
            id=f"git:{canonical_repo_url(url)}", name=repo_name_from_url(url), source=PluginSource.GIT,
            repo_url=url, version=version or None, installed=installed,
            enabled=active.exists() or active.is_symlink(),
            update_available=self._update_available(repo) if installed else False,
            source_name="Git repository", backend=self
        )

    def install(self, entry) -> bool:
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        if not repo.exists():
            result = subprocess.run(["git", "clone", "--recursive", entry.repo_url, str(repo)], capture_output=True, text=True)
            if result.returncode != 0:
                return False
        return self.set_enabled(entry, True)

    def uninstall(self, entry) -> bool:
        assert entry.repo_url
        self.set_enabled(entry, False)
        repo = self.repo_path(entry.repo_url)
        if repo.exists():
            shutil.rmtree(repo)
        return True

    def set_enabled(self, entry, enabled: bool) -> bool:
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
        assert entry.repo_url
        repo = self.repo_path(entry.repo_url)
        result = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only", "--recurse-submodules"], capture_output=True, text=True)
        if result.returncode != 0:
            return False
        active = self.active_path(entry.repo_url)
        if active.exists() and not active.is_symlink():
            shutil.rmtree(active)
            shutil.copytree(repo, active, ignore=shutil.ignore_patterns(".git"))
        return True

    @staticmethod
    def _git(repo: Path, *args: str, check=True) -> str:
        if not repo.exists():
            return ""
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        return result.stdout

    def _update_available(self, repo: Path) -> bool:
        try:
            subprocess.run(["git", "-C", str(repo), "fetch", "--quiet"], capture_output=True, timeout=10)
            upstream = self._git(repo, "rev-parse", "@{u}", check=False).strip()
            head = self._git(repo, "rev-parse", "HEAD", check=False).strip()
            return bool(upstream and head and upstream != head)
        except Exception:
            return False


class PluginRegistry:
    def __init__(self) -> None:
        self.native, self.git = NativeProvider(), GitProvider()
        self._entries: Dict[str, PluginEntry] = {}

    def refresh(self, check_native_updates=False) -> List[PluginEntry]:
        if check_native_updates:
            try:
                self.native.refresh()
            except Exception as exc:
                log_warn(f"Meta Binja: native repository refresh failed: {exc}")
        native_entries = self.native.entries()
        combined = list(native_entries)
        by_repo = {canonical_repo_url(e.repo_url): e for e in native_entries if e.repo_url}
        for source in catalog_sources():
            try:
                entries = CatalogProvider(source).entries()
            except Exception as exc:
                log_warn(f"Meta Binja: failed to load catalog {source}: {exc}")
                continue
            for entry in entries:
                canonical = canonical_repo_url(entry.repo_url) if entry.repo_url else None
                if canonical and canonical in by_repo:
                    continue
                if canonical:
                    by_repo[canonical] = entry
                combined.append(entry)
        self._entries = {e.id: e for e in combined}
        return list(self._entries.values())

    def search(self, query: str) -> List[PluginEntry]:
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
        if entry.source is PluginSource.NATIVE:
            return self.native.install(entry)
        if entry.source is PluginSource.CATALOG and entry.repo_url:
            entry = self.git.entry_from_url(entry.repo_url)
        return self.git.install(entry)

    def uninstall(self, entry):
        return self.native.uninstall(entry) if entry.source is PluginSource.NATIVE else self.git.uninstall(entry)

    def set_enabled(self, entry, enabled):
        return self.native.set_enabled(entry, enabled) if entry.source is PluginSource.NATIVE else self.git.set_enabled(entry, enabled)

    def update(self, entry):
        return self.native.update(entry) if entry.source is PluginSource.NATIVE else self.git.update(entry)
