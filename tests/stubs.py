"""Stand-ins for the Binary Ninja modules that Meta Binja imports.

Binary Ninja provides ``binaryninja`` and ``binaryninjaui`` from inside the
application, so tests install minimal substitutes before importing Meta Binja.
"""

from __future__ import annotations

import sys
import tempfile
import types
from typing import List, Optional

_USER_DIRECTORY: Optional[str] = None
_STATE = {"repositories": [], "catalog_sources": []}


class FakeVersion:
    """A single published version of a fake native extension."""

    def __init__(self, version: str = "1.0.0") -> None:
        self.version = version


class FakeExtension:
    """A stand-in for Binary Ninja's ``Extension`` object."""

    def __init__(
        self,
        name: str,
        path: str = "",
        description: str = "",
        project_url: str = "",
        author: str = "Author",
        installed: bool = False,
        enabled: bool = False,
        update_available: bool = False,
        version: str = "1.0.0",
        running: Optional[bool] = None,
    ) -> None:
        self.name = name
        self.path = path or name.lower().replace(" ", "-")
        self.long_description = description
        self.project_url = project_url
        self.author = author
        self.installed = installed
        self.enabled = enabled
        self.update_available = update_available
        self.running = enabled if running is None else running
        self.current_version = FakeVersion(version)
        self.latest_version_id = "latest"

    def install(self, _version_id=None) -> bool:
        """Mark the extension installed, as Binary Ninja would."""
        self.installed = True
        self.update_available = False
        return True

    def uninstall(self) -> bool:
        """Mark the extension uninstalled."""
        self.installed = False
        self.enabled = False
        self.running = False
        return True

    def enable(self) -> bool:
        """Mark the extension enabled."""
        self.enabled = True
        self.running = True
        return True


class FakeRepository:
    """A stand-in for a Binary Ninja extension repository."""

    def __init__(self, path: str, plugins: List[FakeExtension]) -> None:
        self.path = path
        self.plugins = plugins


def install_binaryninja(repositories: Optional[List[FakeRepository]] = None,
                        catalog_sources: Optional[List[str]] = None) -> types.ModuleType:
    """Install the ``binaryninja`` stub module, or reset its state when present.

    Meta Binja binds ``RepositoryManager`` at import time, so repeated calls
    update the stub's state in place instead of rebinding new classes; that
    lets each test start from a clean set of extensions.
    """
    global _USER_DIRECTORY
    if _USER_DIRECTORY is None:
        _USER_DIRECTORY = tempfile.mkdtemp(prefix="meta-binja-test-")

    _STATE["repositories"] = list(repositories or [])
    _STATE["catalog_sources"] = list(catalog_sources or [])

    module = sys.modules.get("binaryninja")
    if module is not None and getattr(module, "_meta_binja_stub", False):
        return module

    module = types.ModuleType("binaryninja")

    class _RepositoryManager:
        """Minimal RepositoryManager stub for tests outside Binary Ninja."""

        @property
        def repositories(self):
            """Return the extension repositories configured for this test."""
            return _STATE["repositories"]

        def check_for_updates(self):
            """Pretend native repository refresh succeeded."""
            return True

    class _Settings:
        """Minimal Binary Ninja settings stub used by core imports."""

        def register_group(self, *_args, **_kwargs):
            """Accept a settings group registration."""
            return True

        def register_setting(self, *_args, **_kwargs):
            """Accept a settings key registration."""
            return True

        def get_string_list(self, _key):
            """Return the catalogs configured for this test run."""
            return list(_STATE["catalog_sources"])

    module._meta_binja_stub = True
    module.RepositoryManager = _RepositoryManager
    module.Settings = _Settings
    module.log_warn = lambda *_args, **_kwargs: None
    module.log_error = lambda *_args, **_kwargs: None
    module.log_info = lambda *_args, **_kwargs: None
    module.user_directory = lambda: _USER_DIRECTORY
    module.is_main_thread = lambda: True
    module.execute_on_main_thread_and_wait = lambda callback: callback()
    sys.modules["binaryninja"] = module
    return module


def install_binaryninjaui() -> types.ModuleType:
    """Install a ``binaryninjaui`` stub backed by plain Qt widgets."""
    from PySide6.QtWidgets import QWidget

    module = types.ModuleType("binaryninjaui")

    class SidebarWidget(QWidget):
        """Qt-only stand-in for Binary Ninja's SidebarWidget base class."""

        def __init__(self, name):
            super().__init__()
            self._name = name

    class SidebarWidgetType:
        """Stand-in for the sidebar type registration base class."""

        def __init__(self, icon, name):
            self.icon = icon
            self.name = name

    class Sidebar:
        """Records sidebar registrations performed during a test."""

        registered = []

        @classmethod
        def addSidebarWidgetType(cls, widget_type):
            """Record a registered sidebar widget type."""
            cls.registered.append(widget_type)

    class UIAction:
        """Stand-in for Binary Ninja's UIAction."""

        registered = []

        def __init__(self, callback=None):
            self.callback = callback

        @classmethod
        def registerAction(cls, name, *_args):
            """Record a registered global action name."""
            cls.registered.append(name)

    class UIActionHandler:
        """Stand-in for Binary Ninja's UIActionHandler."""

        _global = None

        def setupActionHandler(self, _widget):
            """Accept action-handler setup for a widget."""
            return True

        def bindAction(self, _name, _action):
            """Accept a global action binding."""
            return True

        @classmethod
        def globalActions(cls):
            """Return a shared handler instance."""
            if cls._global is None:
                cls._global = cls()
            return cls._global

    class Menu:
        """Stand-in for Binary Ninja's main menu."""

        @staticmethod
        def mainMenu(_name):
            """Return an object that accepts action registration."""
            return Menu()

        def addAction(self, *_args, **_kwargs):
            """Accept a menu action registration."""
            return True

    module.SidebarWidget = SidebarWidget
    module.SidebarWidgetType = SidebarWidgetType
    module.Sidebar = Sidebar
    module.UIAction = UIAction
    module.UIActionHandler = UIActionHandler
    module.Menu = Menu
    module.SidebarWidgetLocation = types.SimpleNamespace(RightContent="right")
    module.SidebarContextSensitivity = types.SimpleNamespace(GlobalSidebarContext="global")
    sys.modules["binaryninjaui"] = module
    return module
