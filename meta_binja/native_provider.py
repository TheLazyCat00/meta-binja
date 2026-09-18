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
    """Binary Ninja native provider with thread-safe lifecycle semantics."""

    @staticmethod
    def install(entry):
        """Install a native extension, then enable it on Binary Ninja's main thread."""
        if not entry.backend.install():
            return False
        return bool(_on_main_thread(entry.backend.enable))

    @staticmethod
    def set_enabled(entry, enabled):
        """Enable or disable a native extension on Binary Ninja's main thread."""
        if enabled:
            return bool(_on_main_thread(entry.backend.enable))

        def disable() -> bool:
            entry.backend.enabled = False
            return not bool(entry.backend.enabled)

        return bool(_on_main_thread(disable))
