from concurrent.futures import Future
import unittest
from unittest.mock import Mock, patch

from tailtray.core import Snapshot

try:
    from tailtray.app import TailTrayApp
except (ImportError, ValueError):
    TailTrayApp = None


@unittest.skipUnless(TailTrayApp, "GTK/AppIndicator not installed")
class GtkStateTests(unittest.TestCase):
    def test_startup_checks_operator_before_login(self):
        controller = TailTrayApp.__new__(TailTrayApp)
        executor = Mock()
        executor.submit.return_value = Future()
        controller._init_operation_controller(executor, lambda callback: callback())
        controller.settings = {"automatic_login": True}
        controller.secrets = Mock(available=False)
        controller.auth_key = ""
        controller.login_token = ""
        controller.cli = Mock()
        controller.menu_renderer = Mock()
        controller._update_indicator = Mock()
        token = controller.operation_coordinator.start_refresh()
        future = Future()
        future.set_result(Snapshot(backend_state="NeedsLogin", preferences={"operator": ""}))
        with patch("tailtray.controller.autostart_enabled", return_value=False):
            controller._finish_refresh(token, future)
        self.assertTrue(controller.operation_busy)
        self.assertEqual(controller.operation_coordinator.state.name, "Granting Tailscale control")
        executor.submit.call_args.args[0]()
        controller.cli.grant_operator.assert_called_once_with(controller.local_user)
        controller.cli.begin_sign_in.assert_not_called()

    def test_refresh_updates_connection_without_menu_close_event(self):
        controller = TailTrayApp.__new__(TailTrayApp)
        controller._init_operation_controller(Mock(), lambda callback: callback())
        controller.settings = {}
        controller.secrets = Mock(available=False)
        controller.auth_key = ""
        controller.menu_renderer = Mock()
        controller._update_indicator = Mock()
        controller._maybe_auto_login = Mock()
        controller._maybe_auto_reconnect = Mock()
        token = controller.operation_coordinator.start_refresh()
        future = Future()
        stopped = Snapshot(backend_state="Stopped")
        future.set_result(stopped)
        with patch("tailtray.controller.autostart_enabled", return_value=False):
            controller._finish_refresh(token, future)
        entries = controller.menu_renderer.update.call_args.args[0]
        connection = next(e for e in entries if e.key == "connection")
        self.assertEqual(connection.label, "Connect")
        self.assertEqual(connection.command, "connect")
        self.assertTrue(connection.enabled)
