"""Native Binary Ninja extension lifecycle integration."""
from __future__ import annotations

from typing import Callable, TypeVar

from binaryninja import execute_on_main_thread_and_wait, is_main_thread

from . import core as _core

_T = TypeVar("_T")


def _on_main_thread(callback: Callable[[], _T]) -> _T:
    """Run *callback* on Binary Ninja's main thread and return its result.

    Native extension enable/disable operations can load or unload plugin code,
    including UI plugins. Meta Binja invokes provider actions from a worker
    thread, so those operations must be marshalled back to Binary Ninja's
    registered main thread. Exceptions are captured in the callback and
    re-raised on the calling worker so the UI's normal error handling still
    reports the real failure.
    """
    if is_main_thread():
        return callback()

    result = {}
    error = {}

    def invoke() -> None:
        try:
            result["value"] = callback()
        except BaseException as exc:
            error["exception"] = exc

    execute_on_main_thread_and_wait(invoke)
    if "exception" in error:
        raise error["exception"]
    return result.get("value")


class NativeProvider(_core.NativeProvider):
    """Binary Ninja native provider with restart-aware lifecycle semantics."""

    def _resolve_backend(self, entry):
        """Return a fresh Extension handle for *entry* when the repository still exposes it.

        Installation can replace the on-disk extension and update repository-manager
        state. Re-resolving avoids making the follow-up enable decision through the
        pre-install wrapper object.
        """
        target_path = getattr(entry.backend, "path", None)
        target_repo = getattr(entry, "source_name", "")
        if not target_path:
            return entry.backend
        for repo in self.manager.repositories:
            if target_repo and repo.path != target_repo:
                continue
            for extension in repo.plugins:
                if extension.path == target_path:
                    return extension
        return entry.backend

    def install(self, entry):
        """Install and persist enablement without requiring an immediate live load.

        Binary Ninja documents native Extension Manager installation as
        install -> enable -> restart. Extension.enable() can therefore report
        that the current-process load did not succeed even though the enabled state
        was persisted correctly for the next launch. Treat the persisted state as
        authoritative instead of turning that case into a failed installation.
        """
        backend = self._resolve_backend(entry)
        if not backend.install():
            return False

        backend = self._resolve_backend(entry)
        enable_result = bool(_on_main_thread(backend.enable))
        backend = self._resolve_backend(entry)
        entry.backend = backend
        return enable_result or bool(backend.enabled)

    def set_enabled(self, entry, enabled):
        """Apply native enable/disable state and verify the persisted result."""
        backend = self._resolve_backend(entry)
        if enabled:
            enable_result = bool(_on_main_thread(backend.enable))
            backend = self._resolve_backend(entry)
            entry.backend = backend
            return enable_result or bool(backend.enabled)

        def disable() -> None:
            backend.enabled = False

        _on_main_thread(disable)
        backend = self._resolve_backend(entry)
        entry.backend = backend
        return not bool(backend.enabled)
