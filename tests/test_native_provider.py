"""Regression tests for native Binary Ninja extension lifecycle behavior."""

import types
import unittest

try:
    from tests.stubs import FakeExtension, install_binaryninja
except ImportError:  # pragma: no cover - direct test invocation
    from stubs import FakeExtension, install_binaryninja

install_binaryninja()

from meta_binja import core as meta_core


class NativeProviderInstallTests(unittest.TestCase):
    """Installing a native extension should leave it ready to run."""

    def test_install_enables_extension(self):
        """A successful native install is immediately followed by enablement."""
        extension = FakeExtension("Native Plugin")
        entry = types.SimpleNamespace(backend=extension)

        self.assertTrue(meta_core.NativeProvider.install(entry))
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

        self.assertFalse(meta_core.NativeProvider.install(entry))
        self.assertEqual(calls, ["install"])

    def test_enable_failure_is_reported(self):
        """Installation is not reported successful if the extension stays disabled."""
        class Extension:
            def install(self):
                return True

            def enable(self):
                return False

        entry = types.SimpleNamespace(backend=Extension())
        self.assertFalse(meta_core.NativeProvider.install(entry))


if __name__ == "__main__":
    unittest.main()
