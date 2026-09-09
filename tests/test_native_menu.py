"""Opt-in native integration checks: TAILTRAY_TEST_NATIVE_MENU=1 make check."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


@unittest.skipUnless(os.environ.get("TAILTRAY_TEST_NATIVE_MENU") == "1", "native menu checks need a display and private bus")
class NativeMenuTests(unittest.TestCase):
    def run_probe(self, filename):
        project = Path(__file__).resolve().parents[1]
        self.assertIsNotNone(shutil.which("dbus-run-session"))
        self.assertIsNotNone(shutil.which("gdbus"))
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(project / "src")
        environment["QT_QPA_PLATFORM"] = "wayland" if environment.get("WAYLAND_DISPLAY") else "xcb"
        environment["QT_QPA_PLATFORMTHEME"] = "generic"
        environment["NO_AT_BRIDGE"] = "1"
        completed = subprocess.run(["dbus-run-session", "--config-file=" + str(project / "tests/private-bus.conf"),
                                    "--", sys.executable, str(project / "tests" / filename)],
                                   env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("PASS:", completed.stdout)

    def test_real_qt_dbus_menu_disconnect_connect_without_close_events(self):
        self.run_probe("menu_export_dbus_probe.py")

    def test_real_gtk_items_update_without_dispatching_commands(self):
        self.run_probe("gtk_menu_probe.py")
