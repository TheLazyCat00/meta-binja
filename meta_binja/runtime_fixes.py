"""Runtime integration fixes that sit across Binary Ninja's core and Qt APIs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, List, Set

from . import core as _core
from . import git_provider as _git_provider

_HTML_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup",
    "dd", "del", "details", "div", "dl", "dt", "em", "h1", "h2", "h3", "h4",
    "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "ol", "p", "pre", "q",
    "s", "samp", "small", "span", "strong", "sub", "summary", "sup", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul", "var",
}
_BARE_ANGLE_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9_-]*)(/?)>")
_CREATE_NO_WINDOW = 0x08000000


class _HiddenConsoleSubprocess:
    """Proxy ``subprocess`` so child console programs stay hidden on Windows.

    Binary Ninja is a GUI process. Launching Git from it with the default
    Windows creation flags briefly creates a console window for every command,
    which is especially noticeable during startup when several worktrees are
    inspected in sequence. The proxy is local to Meta Binja's modules; it does
    not monkeypatch Python's global ``subprocess`` module or other plugins.
    """

    _meta_binja_hidden_console = True

    def __init__(self, module: object) -> None:
        """Wrap one module-like subprocess implementation."""
        self._module = module

    def run(self, *args, **kwargs):
        """Delegate to ``subprocess.run`` with ``CREATE_NO_WINDOW`` on Windows."""
        if os.name == "nt":
            no_window = getattr(self._module, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | int(no_window)
        return self._module.run(*args, **kwargs)

    def __getattr__(self, name: str):
        """Expose the rest of the wrapped subprocess module unchanged."""
        return getattr(self._module, name)


def install_subprocess_fixes() -> None:
    """Route Meta Binja's Git subprocesses through the hidden-console proxy."""
    for module in (_core, _git_provider):
        current = getattr(module, "subprocess", None)
        if current is None or getattr(current, "_meta_binja_hidden_console", False):
            continue
        module.subprocess = _HiddenConsoleSubprocess(current)


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
        """Preserve supported Qt HTML tags and escape unknown bare tags."""
        if match.group(2).lower() in _HTML_TAGS:
            return match.group(0)
        body = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        return f"&lt;{body}&gt;"

    return _BARE_ANGLE_TAG_RE.sub(replace, text)


def _activation_key(value: object) -> str:
    """Return a case-folded activation basename without lossy normalization."""
    if value is None:
        return ""
    try:
        return Path(str(value)).name.casefold()
    except (TypeError, ValueError):
        return str(value).casefold()


def filter_managed_native_duplicates(entries: Iterable[object], managed_names: Iterable[str]) -> List[object]:
    """Hide native rows that are merely views of Meta Binja-owned activations.

    Binary Ninja discovers plugin directories in the user plugin folder and can
    therefore surface a Git plugin activated by Meta Binja as a second native
    extension. Suppression requires an exact, case-insensitive match between a
    proven managed activation basename and the native backend path basename;
    display names and punctuation-normalized approximations are not ownership
    evidence.
    """
    managed = {_activation_key(name) for name in managed_names if name}
    if not managed:
        return list(entries)

    out: List[object] = []
    for entry in entries:
        if getattr(entry, "source", None) is _core.PluginSource.NATIVE:
            backend = getattr(entry, "backend", None)
            backend_key = _activation_key(getattr(backend, "path", ""))
            if backend_key and backend_key in managed:
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
    """Install settings, subprocess, and registry integration fixes exactly once."""
    install_subprocess_fixes()
    _core.register_settings = register_settings

    registry_type = _core.PluginRegistry
    if getattr(registry_type, "_meta_binja_runtime_fixed", False):
        return
    original_refresh = registry_type.refresh

    def refresh(self, *args, **kwargs):
        """Refresh the registry and remove only proven managed native duplicates."""
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
        """Render README Markdown after escaping unsupported bare angle tags."""
        original_render(self, prepare_markdown(text))

    browser_type.render_markdown = render_markdown
    browser_type._meta_binja_markdown_fixed = True
