"""Exercise the real Qt controller with deterministic daemon replies."""

from concurrent.futures import Future
import os
import unittest
from unittest.mock import Mock, patch

from tailtray.core import Snapshot

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from tailtray.qt_app import TailTrayQtApp
except ImportError:
    QApplication = None


class ControlledExecutor:
    def __init__(self, *args, **kwargs):
        self.pending = []

    def submit(self, function):
        future = Future()
        self.pending.append((function, future))
        return future

    def shutdown(self, **kwargs):
        pass


@unittest.skipUnless(QApplication, "PySide6 not installed")
class StateIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patches = [
            patch("tailtray.qt_app.ThreadPoolExecutor", ControlledExecutor),
            patch("tailtray.qt_app.TailscaleCLI"),
            patch("tailtray.qt_app.AuthKeyStore", return_value=Mock(available=False)),
            patch("tailtray.qt_app.load_settings", return_value={
                "automatic_login": False, "automatic_reconnect": False, "want_connected": True,
            }),
            patch("tailtray.qt_app.TailTrayQtApp._acquire_singleton_lock", return_value=None),
            patch("tailtray.qt_app.QSystemTrayIcon.show"),
            patch("tailtray.qt_app.MenuOnlyStatusNotifier.install", return_value=None),
            patch("tailtray.qt_app.autostart_enabled", return_value=False),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.controller = TailTrayQtApp()
        self.controller.refresh_timer.stop()
        self.addCleanup(self.controller._operation_shutdown)

    def reply(self, snapshot):
        _function, future = self.controller.executor.pending.pop(0)
        future.set_result(snapshot)
        QApplication.processEvents()

    def labels(self):
        return [action.text() for action in self.controller.menu.actions()]

    def test_real_controller_recovers_from_status_error_without_connecting(self):
        self.reply(Snapshot(backend_state="Running", self_online=True))
        self.assertIn("Disconnect", self.labels())
        self.controller.refresh()
        self.reply(Snapshot(error="status read timed out", error_kind="timeout"))
        self.assertNotIn("Connect", self.labels())
        self.assertNotIn("Disconnect", self.labels())
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Running", self_online=True))
        self.assertIn("Disconnect", self.labels())
        self.assertEqual(self.controller.executor.pending, [])

    def test_repeated_refresh_does_not_invalidate_slow_pending_reply(self):
        for _ in range(5):
            self.controller.refresh()
        self.assertEqual(len(self.controller.executor.pending), 1)
        self.reply(Snapshot(backend_state="Stopped"))
        self.assertIn("Connect", self.labels())

    def test_open_menu_updates_status_without_replacing_tree(self):
        self.reply(Snapshot(backend_state="Running", self_online=True))
        old_menu = self.controller.menu
        disconnect = next(a for a in old_menu.actions() if a.text() == "Disconnect")
        self.controller._menu_opened()
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Stopped"))
        self.assertIs(self.controller.menu, old_menu)
        self.assertIn("Disconnected", self.labels()[0])
        self.assertFalse(disconnect.isEnabled())
        self.controller._menu_closed()
        QApplication.processEvents()
        self.assertIn("Connect", self.labels())

    def test_approval_state_never_starts_login(self):
        self.controller.settings["automatic_login"] = True
        self.reply(Snapshot(backend_state="NeedsMachineAuth"))
        self.assertEqual(self.controller.executor.pending, [])
        self.assertNotIn("Sign in…", self.labels())

    def test_rotating_login_urls_do_not_open_repeated_browser_tabs(self):
        self.controller.settings["automatic_login"] = True
        with patch.object(self.controller, "_open_login_url") as opened:
            self.reply(Snapshot(backend_state="NeedsLogin", auth_url="https://login.tailscale.com/a/one"))
            self.controller.refresh()
            self.reply(Snapshot(backend_state="NeedsLogin", auth_url="https://login.tailscale.com/a/two"))
        opened.assert_called_once_with("https://login.tailscale.com/a/one")

    def test_network_warning_does_not_claim_exit_node_definitely_failed(self):
        self.reply(Snapshot(backend_state="Running", self_online=True,
                            exit_node_id="exit-1", exit_node_online=True,
                            health=("Unable to connect to coordination server",)))
        self.assertIn("Exit node: exit-1 (availability unknown)", self.labels())
        self.assertIn("Disconnect", self.labels())
        self.assertEqual(self.controller.executor.pending, [])

    def test_disconnect_clicked_during_connect_wins_and_prevents_reconnect(self):
        self.reply(Snapshot(backend_state="Stopped"))
        with patch("tailtray.qt_app.save_settings"):
            self.controller.connect_tailscale()
            disconnect = next(a for a in self.controller.menu.actions()
                              if a.text() == "Disconnect after current operation")
            self.assertTrue(disconnect.isEnabled())
            disconnect.trigger()
        self.assertFalse(self.controller.settings["want_connected"])
        self.assertEqual(len(self.controller.executor.pending), 1)
        _up, up_future = self.controller.executor.pending.pop(0)
        up_future.set_result(None)
        QApplication.processEvents()
        down, down_future = self.controller.executor.pending.pop(0)
        self.assertEqual(down, self.controller.cli.disconnect)
        down_future.set_result(None)
        QApplication.processEvents()
        self.controller.settings["automatic_reconnect"] = True
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Stopped"))
        self.assertEqual(self.controller.executor.pending, [])

    def test_executor_failure_becomes_unknown_status_and_recovers(self):
        self.reply(Snapshot(backend_state="Running", self_online=True))
        with patch.object(self.controller.executor, "submit", side_effect=RuntimeError("executor rejected poll")):
            self.controller.refresh()
            QApplication.processEvents()
        self.assertFalse(self.controller.refresh_running)
        self.assertIn("executor rejected poll", self.labels()[0])
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Running", self_online=True))
        self.assertIn("Disconnect", self.labels())
