import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tailtray.core import Device, Snapshot


try:
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

    from tailtray.qt_app import TailTrayQtApp
except ImportError:
    QApplication = None
    QMenu = None
    QSystemTrayIcon = None
    TailTrayQtApp = None
    QWidget = None


class _MenuCallbacks:
    def copy_address(self, address, name):
        self.copied = (address, name)


@unittest.skipUnless(QApplication is not None, "PySide6 is not installed")
class QtMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.application = QApplication.instance() or QApplication([])

    def test_device_submenus_are_eager_and_owned_by_root(self):
        snapshot = Snapshot(
            devices=(
                Device(id="one", name="laptop", ips=("100.64.0.1", "fd7a::1"), online=True),
                Device(id="two", name="phone", ips=("100.64.0.2",), online=False),
            )
        )
        menu = TailTrayQtApp._devices_menu(_MenuCallbacks(), snapshot)

        self.assertEqual(len(menu.actions()), 2)
        for action in menu.actions():
            submenu = action.menu()
            self.assertIsNotNone(submenu)
            self.assertIs(submenu.parent(), menu)
            self.assertGreater(len(submenu.actions()), 0)

    def test_left_click_popup_is_deferred_past_mouse_release(self):
        callback = lambda: None
        controller = SimpleNamespace(menu=object(), _show_menu_from_left_click=callback)
        with patch("tailtray.qt_app.QTimer.singleShot") as single_shot:
            TailTrayQtApp._tray_activated(
                controller,
                QSystemTrayIcon.ActivationReason.Trigger,
            )
        single_shot.assert_called_once_with(75, callback)

    def test_root_menu_has_wayland_transient_parent(self):
        anchor = QWidget()
        menu = QMenu()
        tray = Mock()
        controller = SimpleNamespace(
            menu=None,
            popup_anchor=anchor,
            tray=tray,
            _menu_opened=lambda: None,
            _menu_closed=lambda: None,
        )

        TailTrayQtApp._set_menu(controller, menu)

        self.assertIs(menu.parent(), anchor)
        tray.setContextMenu.assert_called_once_with(menu)


if __name__ == "__main__":
    unittest.main()
