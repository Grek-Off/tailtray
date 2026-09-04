from pathlib import Path
import unittest


try:
    from PySide6.QtCore import QCoreApplication

    from tailtray.status_notifier import MenuOnlyStatusNotifier, qt_status_notifier_connection_name
except ImportError:
    QCoreApplication = None
    MenuOnlyStatusNotifier = None


@unittest.skipUnless(QCoreApplication is not None, "PySide6 is not installed")
class StatusNotifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

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


if __name__ == "__main__":
    unittest.main()
