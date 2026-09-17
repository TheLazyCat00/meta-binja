"""Cached repository metadata: README documents and host-provided repo facts.

This module is intentionally free of Qt and of Binary Ninja UI imports so the
fetching/parsing logic stays testable outside Binary Ninja. Network access is
best effort: every public helper degrades to ``None``/``{}`` instead of raising.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

HTTP_TIMEOUT = 10
USER_AGENT = "meta-binja"
CATALOG_TTL = 6 * 3600
README_TTL = 24 * 3600
DETAILS_TTL = 24 * 3600

_README_NAMES = ("README.md", "README.markdown", "README.rst", "README.txt", "README")
_MARKDOWN_SUFFIXES = (".md", ".markdown", "")
_ABSOLUTE_TARGET_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)
# A target is either an angle-bracketed path, which may contain spaces, or a
# bare path that may not.
_MD_TARGET = r"(<[^<>]*>|[^)\s]+)"
_MD_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()" + _MD_TARGET + r"((?:\s+\"[^\"]*\")?\))")
_MD_LINK_RE = re.compile(r"(?<!!)(\[[^\]]*\]\()" + _MD_TARGET + r"((?:\s+\"[^\"]*\")?\))")
_HTML_SRC_RE = re.compile(r"(<img\b[^>]*?\bsrc=[\"'])([^\"']+)([\"'])", re.IGNORECASE)


@dataclass
class ReadmeDocument:
    """A fetched README together with how it should be rendered."""

    text: str
    is_markdown: bool = True
    source_url: Optional[str] = None


_CACHE_LOCKS: Dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()


def _cache_lock(path: Path) -> threading.Lock:
    """Return the lock shared by every cache instance using *path*."""
    key = str(Path(path).absolute())
    with _CACHE_LOCKS_GUARD:
        return _CACHE_LOCKS.setdefault(key, threading.Lock())


class JsonCache:
    """Small TTL cache persisted as a single JSON file.

    Every operation is best effort: an unreadable or corrupt cache behaves like
    an empty one rather than breaking plugin discovery.

    Updates are a read-modify-write, and background tasks share a cache file, so
    each path has a lock and each write lands through its own temporary file.
    """

    def __init__(self, path: Path, ttl: float) -> None:
        self.path = Path(path)
        self.ttl = ttl
        self._lock = _cache_lock(self.path)

    def _read(self) -> Dict[str, Any]:
        """Return the cache payload, or an empty mapping when unusable."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write(self, payload: Dict[str, Any]) -> None:
        """Persist the cache payload, ignoring filesystem failures."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(f"{self.path.suffix}.{uuid.uuid4().hex[:12]}.tmp")
            temp.write_text(json.dumps(payload), encoding="utf-8")
            temp.replace(self.path)
        except OSError:
            return

    @staticmethod
    def _key(key: str) -> str:
        """Return a filesystem-safe, collision-resistant cache key."""
        return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:32]

    def get(self, key: str) -> Optional[Any]:
        """Return a cached value that is still within its TTL."""
        record = self._read().get(self._key(key))
        if not isinstance(record, dict):
            return None
        stamp = record.get("ts")
        if not isinstance(stamp, (int, float)) or time.time() - stamp > self.ttl:
            return None
        return record.get("value")

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key* with the current timestamp."""
        with self._lock:
            payload = self._read()
            payload[self._key(key)] = {"ts": time.time(), "value": value}
            self._write(payload)

    def clear(self) -> None:
        """Drop every cached record."""
        with self._lock:
            try:
                self.path.unlink()
            except OSError:
                return


def default_cache_dir() -> Path:
    """Return Meta Binja's cache directory beneath Binary Ninja's user directory."""
    from binaryninja import user_directory  # Imported lazily so tests can stub it.

    return Path(user_directory()) / "meta-binja" / "cache"


def is_public_address(host: str) -> bool:
    """Return whether every address *host* resolves to is publicly routable."""
    try:
        resolved = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    if not resolved:
        return False
    for info in resolved:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def guard_public_url(url: str) -> None:
    """Raise unless *url* is an HTTPS URL aimed at a public host.

    README content comes from repositories Meta Binja does not control, so the
    addresses it asks the plugin to fetch are checked before a request opens:
    no credentials in the URL, and nothing pointed at loopback, link-local, or
    otherwise private services on the user's network.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"refusing non-HTTPS request: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("refusing request with embedded credentials")
    host = parsed.hostname
    if not host or not is_public_address(host):
        raise ValueError(f"refusing request to non-public address: {host or url}")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check the destination on every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject a redirect that leaves public HTTPS space."""
        guard_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_public_opener: Optional[urllib.request.OpenerDirector] = None


def open_request(url: str, headers: Optional[Dict[str, str]] = None, require_public: bool = False):
    """Return an opened HTTPS response for *url* with Meta Binja's defaults.

    ``require_public`` is set for URLs taken from repository content rather than
    from the user: the destination, and every redirect it follows, must then be
    a public HTTPS address.
    """
    global _public_opener
    combined = {"User-Agent": USER_AGENT}
    combined.update(headers or {})
    request = urllib.request.Request(url, headers=combined)
    if not require_public:
        return urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)
    guard_public_url(url)
    if _public_opener is None:
        _public_opener = urllib.request.build_opener(_PublicRedirectHandler)
    return _public_opener.open(request, timeout=HTTP_TIMEOUT)


def _github_headers() -> Dict[str, str]:
    """Return GitHub API headers, authenticating when a token is in the environment."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def github_json(path: str) -> Any:
    """Fetch and decode a GitHub API endpoint such as ``/repos/owner/name``."""
    with open_request(f"https://api.github.com{path}", _github_headers()) as response:
        return json.loads(response.read().decode("utf-8"))


def absolutize_markdown(text: str, raw_base: Optional[str], html_base: Optional[str]) -> str:
    """Resolve relative README links against their repository.

    Images resolve against the raw-content base so they actually load; ordinary
    links resolve against the human-facing base. Absolute URLs, in-document
    anchors, and scheme-relative targets are left untouched.
    """
    if not raw_base and not html_base:
        return text

    def _join(base: str, target: str) -> str:
        # A leading slash means the repository root in GitHub-flavored Markdown,
        # not the host root, so it resolves against the README's own base.
        return urljoin(base, target.lstrip("/"))

    def _resolve(target: str, base: Optional[str]) -> str:
        cleaned = target.strip()
        if not cleaned or not base or _ABSOLUTE_TARGET_RE.match(cleaned):
            return target
        if cleaned.startswith("<") and cleaned.endswith(">"):
            inner = cleaned[1:-1]
            return target if _ABSOLUTE_TARGET_RE.match(inner) else f"<{_join(base, inner)}>"
        return _join(base, cleaned)

    text = _MD_IMAGE_RE.sub(lambda m: f"{m.group(1)}{_resolve(m.group(2), raw_base)}{m.group(3)}", text)
    text = _MD_LINK_RE.sub(lambda m: f"{m.group(1)}{_resolve(m.group(2), html_base)}{m.group(3)}", text)
    return _HTML_SRC_RE.sub(lambda m: f"{m.group(1)}{_resolve(m.group(2), raw_base)}{m.group(3)}", text)


def _is_markdown(name: str) -> bool:
    """Return whether a README file name should be rendered as Markdown."""
    suffix = Path(name).suffix.lower()
    return suffix in _MARKDOWN_SUFFIXES


def local_readme(directory: Path) -> Optional[Tuple[str, str]]:
    """Return ``(name, text)`` for a README inside a local checkout."""
    try:
        candidates = {entry.name.lower(): entry for entry in Path(directory).iterdir() if entry.is_file()}
    except OSError:
        return None
    for name in _README_NAMES:
        entry = candidates.get(name.lower())
        if entry is None:
            continue
        try:
            return entry.name, entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _github_parts(repo_url: str) -> Optional[Tuple[str, str]]:
    """Return ``(owner, repo)`` when *repo_url* points at github.com."""
    parsed = urlparse(repo_url)
    if (parsed.hostname or "").lower() != "github.com":
        return None
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _raw_candidates(repo_url: str) -> List[Tuple[str, str]]:
    """Return ``(raw_url, html_url)`` README candidates for known Git hosts."""
    parsed = urlparse(repo_url)
    host = (parsed.hostname or "").lower()
    path = "/".join(p for p in parsed.path.strip("/").split("/") if p).removesuffix(".git")
    if not host or not path:
        return []
    out = []
    for name in _README_NAMES[:3]:
        if host in {"gitlab.com", "salsa.debian.org"}:
            out.append((f"https://{host}/{path}/-/raw/HEAD/{name}", f"https://{host}/{path}/-/blob/HEAD/{name}"))
        elif host == "bitbucket.org":
            out.append((f"https://{host}/{path}/raw/HEAD/{name}", f"https://{host}/{path}/src/HEAD/{name}"))
        else:  # Gitea/Forgejo layout, used by codeberg.org among others.
            for branch in ("main", "master"):
                out.append(
                    (
                        f"https://{host}/{path}/raw/branch/{branch}/{name}",
                        f"https://{host}/{path}/src/branch/{branch}/{name}",
                    )
                )
    return out


def _github_readme(repo_url: str) -> Optional[ReadmeDocument]:
    """Fetch a README through the GitHub API, resolving its relative links."""
    parts = _github_parts(repo_url)
    if not parts:
        return None
    owner, repo = parts
    payload = github_json(f"/repos/{owner}/{repo}/readme")
    if not isinstance(payload, dict) or "content" not in payload:
        return None
    text = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    name = payload.get("name") or "README.md"
    raw_base = payload.get("download_url") or f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{name}"
    html_base = payload.get("html_url") or f"https://github.com/{owner}/{repo}/blob/HEAD/{name}"
    if _is_markdown(name):
        text = absolutize_markdown(text, raw_base, html_base)
    return ReadmeDocument(text=text, is_markdown=_is_markdown(name), source_url=html_base)


def _remote_readme(repo_url: str) -> Optional[ReadmeDocument]:
    """Fetch a README from a non-GitHub host by trying conventional raw paths."""
    for raw_url, html_url in _raw_candidates(repo_url):
        try:
            with open_request(raw_url) as response:
                text = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError):
            continue
        name = raw_url.rsplit("/", 1)[-1]
        if _is_markdown(name):
            text = absolutize_markdown(text, raw_url, html_url)
        return ReadmeDocument(text=text, is_markdown=_is_markdown(name), source_url=html_url)
    return None


def fetch_readme(
    repo_url: Optional[str],
    local_path: Optional[Path] = None,
    cache: Optional[JsonCache] = None,
    force: bool = False,
) -> Optional[ReadmeDocument]:
    """Return the best available README for a plugin.

    A local checkout wins because it matches the installed revision; otherwise
    the README is fetched from the hosting service and cached.
    """
    if local_path is not None:
        found = local_readme(Path(local_path))
        if found:
            name, text = found
            raw_base = html_base = None
            parts = _github_parts(repo_url or "")
            if parts:
                raw_base = f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}/HEAD/{name}"
                html_base = f"https://github.com/{parts[0]}/{parts[1]}/blob/HEAD/{name}"
            if _is_markdown(name):
                text = absolutize_markdown(text, raw_base, html_base)
            return ReadmeDocument(text=text, is_markdown=_is_markdown(name), source_url=html_base)

    if not repo_url:
        return None

    cache_key = f"readme:{repo_url}"
    if cache is not None and not force:
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("text"), str):
            return ReadmeDocument(
                text=cached["text"],
                is_markdown=bool(cached.get("is_markdown", True)),
                source_url=cached.get("source_url"),
            )

    try:
        document = _github_readme(repo_url) or _remote_readme(repo_url)
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        document = None
    if document is None:
        return None
    if cache is not None:
        cache.set(
            cache_key,
            {"text": document.text, "is_markdown": document.is_markdown, "source_url": document.source_url},
        )
    return document


def fetch_repo_details(
    repo_url: Optional[str],
    cache: Optional[JsonCache] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Return host-provided repository facts (stars, license, activity).

    Only GitHub is supported today; every other host yields an empty mapping so
    the UI simply omits the extra facts.
    """
    if not repo_url:
        return {}
    parts = _github_parts(repo_url)
    if not parts:
        return {}

    cache_key = f"details:{repo_url}"
    if cache is not None and not force:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return cached

    try:
        payload = github_json(f"/repos/{parts[0]}/{parts[1]}")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return {}
    if not isinstance(payload, dict):
        return {}
    license_info = payload.get("license")
    details = {
        "description": payload.get("description") or "",
        "stars": payload.get("stargazers_count"),
        "forks": payload.get("forks_count"),
        "open_issues": payload.get("open_issues_count"),
        "pushed_at": payload.get("pushed_at") or "",
        "homepage": payload.get("homepage") or "",
        "topics": [t for t in payload.get("topics", []) if isinstance(t, str)],
        "license": license_info.get("spdx_id") if isinstance(license_info, dict) else None,
        "archived": bool(payload.get("archived")),
    }
    if cache is not None:
        cache.set(cache_key, details)
    return details
