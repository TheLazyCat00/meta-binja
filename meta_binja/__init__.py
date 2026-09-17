"""Meta Binja core package."""

# Keep the source-specific providers isolated from the native/catalog core while
# preserving the existing public API. PluginRegistry resolves these provider
# globals at runtime, so replacing them here also updates existing callers of
# ``meta_binja.core`` without duplicating registry logic.
from . import core as _core
from .git_provider import GitProvider, repo_name_from_url
from .native_provider import NativeProvider
from .runtime_fixes import install_core_fixes

_core.GitProvider = GitProvider
_core.repo_name_from_url = repo_name_from_url
_core.NativeProvider = NativeProvider
install_core_fixes()
