"""Native Binary Ninja extension lifecycle integration."""
from __future__ import annotations

from . import core as _core


class NativeProvider(_core.NativeProvider):
    """Binary Ninja native provider with install-and-enable semantics.

    Binary Ninja's Extension Manager treats installation and enablement as two
    distinct operations. Meta Binja presents Install as the complete user-facing
    action, so a successful install must also enable the extension; otherwise it
    remains disabled across restarts and never runs.
    """

    @staticmethod
    def install(entry):
        """Install and enable a native extension through Binary Ninja."""
        if not entry.backend.install():
            return False
        return bool(entry.backend.enable())
