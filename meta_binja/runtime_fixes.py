"""Runtime integration fixes that sit across Binary Ninja's core and Qt APIs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List, Set

from . import core as _core

_HTML_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "dd", "del", "details", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "ol", "p", "pre", "q",
    "s", "samp", "small", "span", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "ul", "var",
}
_BARE_ANGLE_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_-]*)(/?)>")
_PLUGIN_KEY_RE = re.compile(r"[\s._-]+")


def register_settings() -> None:
    """Register settings under the group prefix Binary Ninja expects.

    A setting named ``metaBinja.catalogSources`` belongs to the ``metaBinja``
    group. Registering ``metaBinja.general`` creates a group that no setting
    actually belongs to and current Binary Ninja builds reject it as an invalid
    group ID during startup.
    """
    settings = _core.Settings()
    settings.register_group(_core.PREFIX, "Meta Binja")
    settings.register_setting(
        _core.CATALOG_SOURCES,
        json.dumps(
            {
                "title": "Additional plugin catalog URLs",
                "type": "array",
                "elementType": "string",
                "default": [],
                "description": (
                    "GitHub repository URLs, raw Markdown awesome-lists, or JSON "
                    "plugin catalogs to include in search."
                ),
                "ignore": [],
            }
        ),
    )


def prepare_markdown(text: str) -> str:
    """Escape angle-bracket placeholders that Qt mistakes for raw HTML tags.

    README prose commonly contains placeholders such as ``<preset>``. Qt's
    Markdown parser interprets an unknown angle-bracket word as an HTML tag;
    an unclosed one can swallow the rest of a list item (and sometimes later
    inline formatting). Real HTML tags remain untouched so README images,
    details blocks, tables, and other supported markup keep working.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group(2).lower() in _HTML_TAGS:
            return match.group(0)
        body = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        return f"&lt;{body}&gt;"

    return _BARE_ANGLE_TAG_RE.sub(replace, text)


def _plugin_key(value: object) -> str:
    """Normalize a plugin display/path name for activation deduplication."""
    if value is None:
        return ""
    try:
        name = Path(str(value)).name
    except (TypeError, ValueError):
        name = str(value)
    return _PLUGIN_KEY_RE.sub("", name.casefold())


def filter_managed_native_duplicates(entries: Iterable[object], managed_names: Iterable[str]) -> List[object]:
    """Hide native rows that are merely views of Meta Binja-owned activations.

    Binary Ninja discovers plugin directories in the user plugin folder and can
    therefore surface a Git plugin activated by Meta Binja as a second native
    extension. Only names backed by a Meta Binja-owned activation are eligible
    for suppression; unrelated native extensions remain untouched.
    """
    managed = {_plugin_key(name) for name in managed_names if name}
    if not managed:
        return list(entries)

    out: List[object] = []
    for entry in entries:
        if getattr(entry, "source", None) is _core.PluginSource.NATIVE:
            backend = getattr(entry, "backend", None)
            candidates = {
                _plugin_key(getattr(entry, "name", "")),
                _plugin_key(getattr(backend, "path", "")),
            }
            if managed.intersection(candidates):
                continue
        out.append(entry)
    return out


def _managed_activation_names(registry: object) -> Set[str]:
    """Return public activation names that the Git provider can prove it owns."""
    provider = getattr(registry, "git", None)
    if provider is None:
        return set()
    try:
        git_entries = provider.entries(check_updates=False)
    except Exception:
        return set()

    names: Set[str] = set()
    for entry in git_entries:
        url = getattr(entry, "repo_url", None)
        if not url or not getattr(entry, "enabled", False):
            continue
        try:
            repo = provider.repo_path(url)
            try:
                active = provider.active_path(url, repo)
            except TypeError:  # Compatibility with the original provider shape.
                active = provider.active_path(url)
            owns = getattr(provider, "_activation_owned_by", None)
            if callable(owns) and owns(active, repo):
                names.add(active.name)
        except Exception:
            continue
    return names


def install_core_fixes() -> None:
    """Install settings registration and registry deduplication exactly once."""
    _core.register_settings = register_settings

    registry_type = _core.PluginRegistry
    if getattr(registry_type, "_meta_binja_runtime_fixed", False):
        return
    original_refresh = registry_type.refresh

    def refresh(self, *args, **kwargs):
        entries = original_refresh(self, *args, **kwargs)
        filtered = filter_managed_native_duplicates(entries, _managed_activation_names(self))
        if len(filtered) != len(entries):
            self._entries = {entry.id: entry for entry in filtered}
        return filtered

    registry_type.refresh = refresh
    registry_type._meta_binja_runtime_fixed = True


def install_ui_fixes(ui_module: object) -> None:
    """Install README Markdown preprocessing on Meta Binja's browser only."""
    browser_type = ui_module.ReadmeBrowser
    if getattr(browser_type, "_meta_binja_markdown_fixed", False):
        return
    original_render = browser_type.render_markdown

    def render_markdown(self, text: str) -> None:
        original_render(self, prepare_markdown(text))

    browser_type.render_markdown = render_markdown
    browser_type._meta_binja_markdown_fixed = True
