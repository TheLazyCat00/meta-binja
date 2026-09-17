"""Meta Binja core package."""

# Keep the Git-backed provider isolated from the native/catalog core while
# preserving the existing public API. PluginRegistry resolves GitProvider at
# runtime, so replacing these globals here also updates existing callers of
# ``meta_binja.core`` without duplicating registry logic.
from . import core as _core
from .git_provider import GitProvider, repo_name_from_url
from .runtime_fixes import install_core_fixes

_core.GitProvider = GitProvider
_core.repo_name_from_url = repo_name_from_url
install_core_fixes()
