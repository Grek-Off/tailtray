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
    def test_open_menu_defers_replacement_until_closed(self):
        controller = TailTrayApp.__new__(TailTrayApp)
        controller._init_operation_controller(Mock(), lambda callback: callback())
        controller.menu_open = True
        controller.menu_rebuild_pending = False
        controller.menu_revision = None
        controller._menu_revision = lambda snapshot: (snapshot,)
        controller._update_indicator = Mock()
        controller._update_open_menu = Mock()
        controller._set_menu = Mock()
        controller._build_menu = Mock()
        controller._maybe_auto_login = Mock()
        controller._maybe_auto_reconnect = Mock()
        token = controller.operation_coordinator.start_refresh()
        future = Future()
        stopped = Snapshot(backend_state="Stopped")
        future.set_result(stopped)
        controller._finish_refresh(token, future)
        controller._set_menu.assert_not_called()
        controller._update_open_menu.assert_called_once_with(stopped)
        with patch("tailtray.app.GLib.idle_add"):
            controller._menu_closed()
        controller._apply_pending_menu()
        controller._set_menu.assert_called_once()
        self.assertFalse(controller.menu_rebuild_pending)
