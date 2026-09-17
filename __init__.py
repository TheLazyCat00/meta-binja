"""Meta Binja plugin entrypoint."""
from binaryninja import log_error

try:
    import binaryninjaui  # Must precede PySide6 imports in Binary Ninja UI plugins.
    from .meta_binja.ui import register_ui

    register_ui()
except Exception as exc:  # pragma: no cover
    log_error(f"Meta Binja: UI unavailable: {exc}")
