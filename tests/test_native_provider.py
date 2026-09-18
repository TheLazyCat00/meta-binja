"""Regression tests for native Binary Ninja extension lifecycle behavior."""

import types
import unittest
from unittest.mock import patch

try:
    from tests.stubs import FakeExtension, install_binaryninja
except ImportError:  # pragma: no cover - direct test invocation
    from stubs import FakeExtension, install_binaryninja

install_binaryninja()

from meta_binja import core as meta_core
from meta_binja import native_provider


class NativeProviderInstallTests(unittest.TestCase):
    """Installing a native extension should leave it ready to run."""

    def test_install_enables_extension(self):
        """A successful native install is immediately followed by enablement."""
        extension = FakeExtension("Native Plugin")
        entry = types.SimpleNamespace(backend=extension)

        self.assertTrue(meta_core.NativeProvider().install(entry))
        self.assertTrue(extension.installed)
        self.assertTrue(extension.enabled)

    def test_failed_install_does_not_try_to_enable(self):
        """Do not enable an extension when Binary Ninja reports install failure."""
        calls = []

        class Extension:
            def install(self):
                calls.append("install")
                return False

            def enable(self):
                calls.append("enable")
                return True

        entry = types.SimpleNamespace(backend=Extension())

        self.assertFalse(meta_core.NativeProvider().install(entry))
        self.assertEqual(calls, ["install"])

    def test_enable_failure_is_reported(self):
        """Installation is not reported successful if the extension stays disabled."""
        class Extension:
            enabled = False

            def install(self):
                return True

            def enable(self):
                return False

        entry = types.SimpleNamespace(backend=Extension())
        self.assertFalse(meta_core.NativeProvider().install(entry))

    def test_persisted_enablement_wins_over_live_load_result(self):
        """A restart-ready extension is successful even if it cannot live-load now."""
        class Extension:
            enabled = False

            def install(self):
                return True

            def enable(self):
                self.enabled = True
                return False

        entry = types.SimpleNamespace(backend=Extension())
        self.assertTrue(meta_core.NativeProvider().install(entry))
        self.assertTrue(entry.backend.enabled)


class NativeProviderThreadingTests(unittest.TestCase):
    """Native activation must execute on Binary Ninja's registered main thread."""

    def _executor(self, calls):
        def execute(callback):
            calls.append("main-thread")
            callback()
        return execute

    def test_install_marshals_enable_to_main_thread(self):
        """Install remains on the worker while activation is marshalled."""
        calls = []

        class Extension:
            def install(self):
                calls.append("install")
                return True

            def enable(self):
                calls.append("enable")
                return True

        entry = types.SimpleNamespace(backend=Extension())
        with patch.object(native_provider, "is_main_thread", return_value=False), patch.object(
            native_provider, "execute_on_main_thread_and_wait", side_effect=self._executor(calls)
        ):
            self.assertTrue(meta_core.NativeProvider().install(entry))

        self.assertEqual(calls, ["install", "main-thread", "enable"])

    def test_enable_marshals_to_main_thread(self):
        """Explicit enable requests use the same main-thread bridge."""
        calls = []
        extension = FakeExtension("Native Plugin", installed=True)
        entry = types.SimpleNamespace(backend=extension)

        with patch.object(native_provider, "is_main_thread", return_value=False), patch.object(
            native_provider, "execute_on_main_thread_and_wait", side_effect=self._executor(calls)
        ):
            self.assertTrue(meta_core.NativeProvider().set_enabled(entry, True))

        self.assertEqual(calls, ["main-thread"])
        self.assertTrue(extension.enabled)

    def test_disable_marshals_to_main_thread(self):
        """Disabling an installed native extension is also main-thread safe."""
        calls = []
        extension = FakeExtension("Native Plugin", installed=True, enabled=True)
        entry = types.SimpleNamespace(backend=extension)

        with patch.object(native_provider, "is_main_thread", return_value=False), patch.object(
            native_provider, "execute_on_main_thread_and_wait", side_effect=self._executor(calls)
        ):
            self.assertTrue(meta_core.NativeProvider().set_enabled(entry, False))

        self.assertEqual(calls, ["main-thread"])
        self.assertFalse(extension.enabled)

    def test_main_thread_call_executes_directly(self):
        """Avoid re-entering the main-thread dispatcher when already on it."""
        extension = FakeExtension("Native Plugin", installed=True)
        entry = types.SimpleNamespace(backend=extension)

        with patch.object(native_provider, "is_main_thread", return_value=True), patch.object(
            native_provider, "execute_on_main_thread_and_wait"
        ) as execute:
            self.assertTrue(meta_core.NativeProvider().set_enabled(entry, True))

        execute.assert_not_called()
        self.assertTrue(extension.enabled)

    def test_main_thread_exception_is_reraised_to_worker(self):
        """Activation errors propagate through the bridge to the UI action handler."""
        class Extension:
            enabled = False

            def enable(self):
                raise RuntimeError("plugin load failed")

        entry = types.SimpleNamespace(backend=Extension())
        calls = []
        with patch.object(native_provider, "is_main_thread", return_value=False), patch.object(
            native_provider, "execute_on_main_thread_and_wait", side_effect=self._executor(calls)
        ):
            with self.assertRaisesRegex(RuntimeError, "plugin load failed"):
                meta_core.NativeProvider().set_enabled(entry, True)

        self.assertEqual(calls, ["main-thread"])


if __name__ == "__main__":
    unittest.main()
