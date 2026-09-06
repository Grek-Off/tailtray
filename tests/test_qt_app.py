import ast
from concurrent.futures import Future
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tailtray.core import Device, ExitNode, ReconnectPolicy, Snapshot


try:
    from PySide6.QtWidgets import QApplication

    from tailtray.qt_app import TailTrayQtApp
except ImportError:
    QApplication = None
    TailTrayQtApp = None


class _MenuCallbacks:
    def copy_address(self, address, name):
        self.copied = (address, name)

    def select_exit_node(self, checked, target, already_selected):
        self.selected = (checked, target, already_selected)


class QtTraySafetyTests(unittest.TestCase):
    def test_tray_clicks_cannot_schedule_a_local_qmenu_popup(self):
        """Keep Plasma in charge of opening /MenuBar for either mouse button."""
        source_path = Path(__file__).resolve().parents[1] / "src" / "tailtray" / "qt_app.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        popup_calls = []
        activation_connections = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "popup":
                popup_calls.append(node.lineno)
            if node.func.attr == "connect" and isinstance(node.func.value, ast.Attribute):
                if node.func.value.attr == "activated":
                    activation_connections.append(node.lineno)

        self.assertEqual(popup_calls, [], "Qt backend must not call QMenu.popup on KDE/Wayland")
        self.assertEqual(
            activation_connections,
            [],
            "Qt backend must let Plasma handle tray activation through StatusNotifierItem",
        )


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

    def test_exit_node_menu_marks_saved_but_inactive_node_unavailable(self):
        snapshot = Snapshot(
            backend_state="Running",
            exit_node_id="node-1",
            exit_node_online=False,
            exit_nodes=(
                ExitNode(
                    id="node-1",
                    name="exit-box",
                    target="100.64.0.2",
                    online=False,
                    selected=True,
                ),
            ),
        )

        menu = TailTrayQtApp._exit_nodes_menu(_MenuCallbacks(), snapshot)

        self.assertEqual(menu.title(), "Exit node: exit-box (unavailable)")

    def test_manual_disconnect_disables_reconnect_intent(self):
        controller = TailTrayQtApp.__new__(TailTrayQtApp)
        controller.settings = {"automatic_reconnect": True, "want_connected": True}
        controller.reconnect_policy = ReconnectPolicy(failures=2, next_attempt_at=30, in_flight=False)
        controller.cli = Mock()
        controller._action = Mock()

        with patch("tailtray.qt_app.save_settings") as save:
            controller.disconnect_tailscale()

        self.assertFalse(controller.settings["want_connected"])
        self.assertEqual(controller.reconnect_policy.failures, 0)
        save.assert_called_once_with(controller.settings)
        controller._action.assert_called_once_with("Disconnecting", controller.cli.disconnect)

    def test_manual_connect_restores_reconnect_intent(self):
        controller = TailTrayQtApp.__new__(TailTrayQtApp)
        controller.settings = {"automatic_reconnect": True, "want_connected": False}
        controller.reconnect_policy = ReconnectPolicy(failures=2, next_attempt_at=30)
        controller.cli = Mock()
        controller._start_expected_connection_action = Mock()

        with patch("tailtray.qt_app.save_settings") as save:
            controller.connect_tailscale()

        self.assertTrue(controller.settings["want_connected"])
        self.assertEqual(controller.reconnect_policy.failures, 0)
        save.assert_called_once_with(controller.settings)
        controller._start_expected_connection_action.assert_called_once_with("Connecting", controller.cli.connect)

    def test_stopped_snapshot_schedules_only_one_automatic_reconnect(self):
        controller = TailTrayQtApp.__new__(TailTrayQtApp)
        controller.settings = {"automatic_reconnect": True, "want_connected": True}
        controller.reconnect_policy = ReconnectPolicy()
        controller.snapshot = Snapshot(backend_state="Stopped")
        controller.cli = Mock()
        pending = Future()
        controller.executor = Mock()
        controller.executor.submit.return_value = pending
        controller.bridge = Mock()

        with patch("tailtray.qt_app.time.monotonic", return_value=10):
            controller._maybe_auto_reconnect()
            controller._maybe_auto_reconnect()

        controller.executor.submit.assert_called_once_with(controller.cli.connect)
        self.assertTrue(controller.reconnect_policy.in_flight)

if __name__ == "__main__":
    unittest.main()
