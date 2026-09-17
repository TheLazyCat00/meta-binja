"""Meta Binja plugin entrypoint."""
from binaryninja import log_error

try:
    import binaryninjaui  # Must precede PySide6 imports in Binary Ninja UI plugins.
    from .meta_binja import ui as _ui
    from .meta_binja.runtime_fixes import install_ui_fixes

    install_ui_fixes(_ui)
    _ui.register_ui()
except Exception as exc:  # pragma: no cover
    log_error(f"Meta Binja: UI unavailable: {exc}")
