"""Exercise the real Qt controller with deterministic daemon replies."""

from concurrent.futures import Future
import os
import unittest
from unittest.mock import Mock, patch

from tailtray.core import CommandResult, ExitNode, Profile, Snapshot, TailscaleCLI, TailscaleError

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
            # PySide inspects the QObject class dictionary when building dynamic
            # slots. A Mock here masquerades as arbitrary Qt metadata on 6.10.3.
            patch("tailtray.qt_app.TailTrayQtApp._acquire_singleton_lock", new=staticmethod(lambda: None)),
            patch("tailtray.qt_app.QSystemTrayIcon.show"),
            patch("tailtray.qt_app.MenuOnlyStatusNotifier.install", return_value=None),
            patch("tailtray.controller.autostart_enabled", return_value=False, create=True),
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

    def action(self, key, menu=None):
        for action in (menu or self.controller.menu).actions():
            if action.objectName() == key:
                return action
            if action.menu():
                found = self.action(key, action.menu())
                if found:
                    return found

    def test_failed_disconnect_and_connect_release_busy_and_restore_action(self):
        self.reply(Snapshot(backend_state="Running", self_online=True))
        with patch("tailtray.qt_app.save_settings"), patch.object(self.controller, "notify") as notify:
            for state, label in (("Running", "Disconnect"), ("Stopped", "Connect")):
                self.controller.refresh()
                self.reply(Snapshot(backend_state=state))
                self.action("connection").trigger()
                _command, future = self.controller.executor.pending.pop(0)
                future.set_exception(TailscaleError("daemon unavailable", kind="daemon"))
                QApplication.processEvents()
                self.assertFalse(self.controller.operation_busy)
                self.controller.refresh()
                self.reply(Snapshot(backend_state=state))
                self.assertEqual(self.action("connection").text(), label)
                self.assertTrue(self.action("connection").isEnabled())
            self.assertEqual(notify.call_count, 2)

    def test_real_cli_argv_from_menu_actions(self):
        snapshot = Snapshot(backend_state="Stopped", preferences={"exit-node": "", "accept-dns": True},
                            profiles=(Profile("p1", "first", selected=True), Profile("p2", "second")),
                            exit_nodes=(ExitNode("e1", "exit", "100.64.0.2", online=True),))
        self.reply(snapshot)
        runner = Mock(return_value=CommandResult())
        self.controller.cli = TailscaleCLI(runner=runner)
        cases = (("connection", ["up"]), ("exit:e1", ["set", "--exit-node=100.64.0.2"]),
                 ("auto", ["set", "--exit-node=auto:any"]), ("profile:p2", ["switch", "p2"]),
                 ("pref:accept-dns", ["set", "--accept-dns=false"]))
        with patch("tailtray.qt_app.save_settings"):
            for key, argv in cases:
                with self.subTest(key=key):
                    self.action(key).trigger()
                    command, future = self.controller.executor.pending.pop(0)
                    future.set_result(command())
                    QApplication.processEvents()
                    self.assertEqual(runner.call_args.args[0], ["tailscale", *argv])
                    self.controller.refresh()
                    self.reply(snapshot)

    def test_failed_preference_change_restores_observed_checkbox_without_extra_command(self):
        self.reply(Snapshot(backend_state="Running", preferences={"accept-dns": True}))
        action = self.action("pref:accept-dns")
        with patch.object(self.controller, "notify"):
            action.trigger()
            # Model restores observed state even before failure; no optimistic lie.
            self.assertTrue(action.isChecked())
            self.assertFalse(action.isEnabled())
            _command, future = self.controller.executor.pending.pop(0)
            future.set_exception(TailscaleError("permission denied", kind="permission"))
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", preferences={"accept-dns": True}))
        self.assertTrue(action.isEnabled())
        self.assertTrue(action.isChecked())
        self.assertEqual(self.controller.executor.pending, [])

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
        self.controller.menu.aboutToShow.emit()
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Stopped"))
        self.assertIs(self.controller.menu, old_menu)
        self.assertIn("Disconnected", self.labels()[0])
        self.assertTrue(disconnect.isEnabled())
        self.assertEqual(disconnect.text(), "Connect")
        self.assertIn("Connect", self.labels())

    def test_disconnect_connect_cycle_without_any_menu_closed_event(self):
        self.reply(Snapshot(backend_state="Running", self_online=True))
        root = self.controller.menu
        connection = next(a for a in root.actions() if a.text() == "Disconnect")
        root.aboutToShow.emit()
        with patch("tailtray.qt_app.save_settings"):
            for _ in range(3):
                self.assertEqual(connection.text(), "Disconnect")
                connection.trigger()
                down, future = self.controller.executor.pending.pop(0)
                self.assertEqual(down, self.controller.cli.disconnect)
                future.set_result(None)
                QApplication.processEvents()
                self.controller.refresh()
                self.reply(Snapshot(backend_state="Stopped"))
                self.assertIs(self.controller.menu, root)
                self.assertEqual(connection.text(), "Connect")
                self.assertTrue(connection.isEnabled())
                connection.trigger()
                up, future = self.controller.executor.pending.pop(0)
                self.assertEqual(up, self.controller.cli.connect)
                future.set_result(None)
                QApplication.processEvents()
                self.controller.refresh()
                self.reply(Snapshot(backend_state="Running", self_online=True))
                self.assertTrue(connection.isEnabled())

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
