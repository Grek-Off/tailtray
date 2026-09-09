import ast
from concurrent.futures import Future
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from tailtray.core import Device, ExitNode, ReconnectPolicy, Snapshot
from tailtray.menu import Entry, build_menu


try:
    from PySide6.QtWidgets import QApplication

    from tailtray.qt_app import TailTrayQtApp
    from tailtray.qt_menu import QtMenu
except ImportError:
    QApplication = None
    TailTrayQtApp = None


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

    def render(self, snapshot):
        renderer = QtMenu(Mock())
        renderer.update(build_menu(snapshot, status=snapshot.display_status, busy=False,
                                   operation=None, settings={}, secrets_available=False,
                                   has_key=False, autostart=False))
        self.addCleanup(renderer.menu.deleteLater)
        return renderer.menu

    def test_device_submenus_are_eager_and_owned_by_root(self):
        snapshot = Snapshot(
            devices=(
                Device(id="one", name="laptop", ips=("100.64.0.1", "fd7a::1"), online=True),
                Device(id="two", name="phone", ips=("100.64.0.2",), online=False),
            )
        )
        root = self.render(snapshot)
        menu = next(a.menu() for a in root.actions() if a.objectName() == "devices")

        self.assertEqual(len(menu.actions()), 2)
        for action in menu.actions():
            submenu = action.menu()
            self.assertIsNotNone(submenu)
            self.assertIs(submenu.parent(), menu)
            self.assertGreater(len(submenu.actions()), 0)

    def test_exit_node_menu_marks_saved_but_inactive_node_unavailable(self):
        snapshot = Snapshot(
            backend_state="Running",
            self_online=True,
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

        root = self.render(snapshot)
        menu = next(a.menu() for a in root.actions() if a.objectName() == "exit-nodes")

        self.assertEqual(menu.title(), "Exit node: exit-box (unavailable)")

    def test_in_place_updates_change_callback_data_without_recreating_submenu(self):
        activate = Mock()
        renderer = QtMenu(activate)
        self.addCleanup(renderer.menu.deleteLater)
        renderer.update((Entry("devices", "Devices", children=(Entry("ip", "old", "copy", ("old",)),)),))
        submenu = renderer.menu.actions()[0].menu()
        action = submenu.actions()[0]
        renderer.update((Entry("devices", "Devices", children=(Entry("ip", "new", "copy", ("new",)),)),))
        self.assertIs(renderer.menu.actions()[0].menu(), submenu)
        self.assertIs(submenu.actions()[0], action)
        action.trigger()
        self.assertEqual(activate.call_args.args[0].args, ("new",))

    def test_programmatic_checkbox_updates_and_removed_actions_never_dispatch(self):
        activate = Mock()
        renderer = QtMenu(activate)
        self.addCleanup(renderer.menu.deleteLater)
        renderer.update((Entry("pref", "DNS", "preference", checked=False),))
        action = renderer.menu.actions()[0]
        renderer.update((Entry("pref", "DNS", "preference", checked=True),))
        self.assertTrue(action.isChecked())
        activate.assert_not_called()
        renderer.update(())
        action.trigger()
        activate.assert_not_called()

    def test_manual_disconnect_disables_reconnect_intent(self):
        controller = TailTrayQtApp.__new__(TailTrayQtApp)
        controller.settings = {"automatic_reconnect": True, "want_connected": True}
        controller.reconnect_policy = ReconnectPolicy(failures=2, next_attempt_at=30, in_flight=False)
        controller.cli = Mock()
        controller.operation_coordinator = Mock(busy=False)
        controller._render_menu = Mock()

        with patch("tailtray.qt_app.save_settings") as save:
            controller.disconnect_tailscale()

        self.assertFalse(controller.settings["want_connected"])
        self.assertEqual(controller.reconnect_policy.failures, 0)
        save.assert_called_once_with(controller.settings)
        controller.operation_coordinator.request_disconnect.assert_called_once_with(controller.cli.disconnect)

    def test_manual_connect_restores_reconnect_intent(self):
        controller = TailTrayQtApp.__new__(TailTrayQtApp)
        controller.settings = {"automatic_reconnect": True, "want_connected": False}
        controller.reconnect_policy = ReconnectPolicy(failures=2, next_attempt_at=30)
        controller.cli = Mock()
        controller.operation_coordinator = Mock(busy=False)
        controller._action = Mock()

        with patch("tailtray.qt_app.save_settings") as save:
            controller.connect_tailscale()

        self.assertTrue(controller.settings["want_connected"])
        self.assertEqual(controller.reconnect_policy.failures, 0)
        save.assert_called_once_with(controller.settings)
        controller._action.assert_called_once_with("Connecting", controller.cli.connect)

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
        controller._init_operation_controller(controller.executor, lambda callback: callback())
        controller._render_menu = Mock()
        controller.operation_coordinator.accept_refresh(controller.operation_coordinator.start_refresh())

        with patch("tailtray.controller.time.monotonic", return_value=10):
            controller._maybe_auto_reconnect()
            controller._maybe_auto_reconnect()

        controller.executor.submit.assert_called_once_with(controller.cli.connect)
        self.assertTrue(controller.reconnect_policy.in_flight)

if __name__ == "__main__":
    unittest.main()
