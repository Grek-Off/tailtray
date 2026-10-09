import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


try:
    from PySide6.QtWidgets import QApplication

    from tailtray.status_notifier import MenuOnlyStatusNotifier, qt_status_notifier_connection_name
except ImportError:
    QApplication = None
    MenuOnlyStatusNotifier = None


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class StatusNotifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_advertises_plasma_menu_for_both_mouse_buttons(self):
        notifier = MenuOnlyStatusNotifier(Path("/tmp/tailtray-connected.png"))

        self.assertTrue(notifier.adaptor.ItemIsMenu)
        self.assertEqual(notifier.adaptor.Menu.path(), "/MenuBar")
        self.assertEqual(notifier.adaptor.IconThemePath, "/tmp")
        self.assertEqual(notifier.adaptor.IconName, "tailtray-connected")

    def test_icon_and_menu_changes_emit_standard_signals(self):
        notifier = MenuOnlyStatusNotifier(Path("/tmp/old.png"))
        icon_changes = []
        menu_changes = []
        notifier.adaptor.NewIcon.connect(lambda: icon_changes.append(True))
        notifier.adaptor.NewMenu.connect(lambda: menu_changes.append(True))

        notifier.set_icon(Path("/tmp/new.png"))
        notifier.menu_changed()

        self.assertEqual(icon_changes, [True])
        self.assertEqual(menu_changes, [True])

    def test_uses_qt_private_tray_connection_name(self):
        self.assertEqual(
            qt_status_notifier_connection_name(1234),
            "org.kde.StatusNotifierItem-1234-1",
        )

    def test_both_activation_slots_never_open_a_local_qmenu(self):
        """The host opens /MenuBar; the adapter's activation slots are compatibility no-ops."""
        notifier = MenuOnlyStatusNotifier(Path("/tmp/tailtray-connected.png"))

        with patch("PySide6.QtWidgets.QMenu.popup") as popup:
            for _ in range(25):
                notifier.adaptor.Activate(100, 200)
                notifier.adaptor.ContextMenu(100, 200)
                QApplication.processEvents()

        popup.assert_not_called()
        self.assertTrue(notifier.adaptor.ItemIsMenu)
        self.assertEqual(notifier.adaptor.Menu.path(), "/MenuBar")

    @unittest.skipUnless(shutil.which("dbus-run-session"), "dbus-run-session is not installed")
    def test_repeated_left_and_right_click_dbus_calls_survive_in_a_subprocess(self):
        project_dir = Path(__file__).resolve().parents[1]
        environment = os.environ.copy()
        if environment.get("TAILTRAY_TEST_INSTALLED") != "1":
            environment["PYTHONPATH"] = str(project_dir / "src")
        environment["TAILTRAY_PROBE_DEBUG"] = "1"
        # Each process must survive both callbacks AND interpreter teardown.
        # This is a repetition regression check, never a retry of failed tests.
        for attempt in range(100):
            with self.subTest(process=attempt + 1):
                completed = subprocess.run(
                    [shutil.which("dbus-run-session"), "--", sys.executable,
                     str(project_dir / "tests" / "status_notifier_dbus_probe.py")],
                    capture_output=True, text=True, timeout=10, env=environment, check=False,
                )
                if completed.returncode == 127 and "Failed to start message bus" in completed.stderr:
                    self.skipTest("sandbox does not permit a temporary D-Bus Unix socket")
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
