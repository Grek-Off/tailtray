"""Exercise the real Qt controller with deterministic daemon replies."""

from concurrent.futures import Future
import os
import unittest
from unittest.mock import Mock, patch

from tailtray.core import CommandResult, Device, ExitNode, Profile, Snapshot, TailscaleCLI, TailscaleError

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

    def finish_command(self, *, result=None, error=None):
        command, future = self.controller.executor.pending.pop(0)
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)
        QApplication.processEvents()
        return command

    def test_startup_operator_repair_precedes_login_and_requires_fresh_confirmation(self):
        self.controller.settings["automatic_login"] = True
        self.reply(Snapshot(backend_state="NeedsLogin", preferences={"operator": ""}))
        self.assertTrue(self.controller.operation_busy)
        command = self.controller.executor.pending[0][0]
        command()
        self.controller.cli.grant_operator.assert_called_once_with(self.controller.local_user)
        self.controller.cli.begin_sign_in.assert_not_called()
        self.finish_command()
        self.assertFalse(self.controller.operation_busy)
        self.assertEqual(self.action("connection").text(), "Refresh status")
        with patch.object(self.controller, "_open_login_url") as opened:
            self.controller.refresh()
            self.reply(Snapshot(backend_state="NeedsLogin", preferences={"operator": self.controller.local_user}))
            self.assertEqual(self.controller.executor.pending[0][0], self.controller.cli.begin_sign_in)
            self.finish_command(result="https://login.tailscale.com/a/new")
            self.controller.refresh()
            self.reply(Snapshot(backend_state="NeedsLogin", preferences={"operator": self.controller.local_user}))
            opened.assert_called_once_with("https://login.tailscale.com/a/new")
        self.assertTrue(self.action("connection").isEnabled())
        self.assertEqual(self.action("connection").text(), "Sign in…")
        self.assertEqual(self.controller.executor.pending, [])

    def test_matching_or_unknown_operator_does_not_request_privilege(self):
        for operator in (self.controller.local_user, None):
            if not self.controller.refresh_running:
                self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", preferences={"operator": operator}))
            self.assertEqual(self.controller.executor.pending, [])
            self.assertEqual(self.action("connection").text(), "Disconnect")
        self.controller.cli.grant_operator.assert_not_called()

    def test_cancelled_operator_prompt_keeps_recovery_and_never_repeats_on_polls(self):
        self.controller.settings.update(automatic_login=True, automatic_reconnect=True)
        self.controller.auth_key = "tskey-test"
        denied = Snapshot(backend_state="NeedsLogin", preferences={"operator": "other"})
        with patch.object(self.controller, "notify") as notify:
            self.reply(denied)
            self.finish_command(error=TailscaleError("Permission request cancelled", kind="cancelled"))
            for _ in range(4):
                self.controller.refresh()
                self.reply(denied)
                self.assertEqual(self.controller.executor.pending, [])
                self.assertTrue(self.action("connection").isEnabled())
                self.assertEqual(self.action("connection").text(), "Grant this user control…")
                self.assertTrue(self.action("grant-control").isEnabled())
                self.assertFalse(self.action("exit-nodes").isEnabled())
                self.assertIn("cancelled", self.action("health").text())
            self.action("connection").trigger()
            self.assertTrue(self.controller.operation_busy)
            self.assertEqual(len(self.controller.executor.pending), 1)
            notify.assert_called_once()
        self.controller.cli.begin_sign_in.assert_not_called()
        self.controller.cli.login_with_auth_key.assert_not_called()
        self.controller.cli.connect.assert_not_called()

    def test_control_read_permission_error_preserves_connection_and_recovers(self):
        snapshot = Snapshot(backend_state="Running", control_error="prefs access denied",
                            control_error_kind="permission")
        self.reply(snapshot)
        self.assertTrue(self.controller.snapshot.connected)
        self.assertTrue(self.controller.operation_busy)
        self.assertEqual(len(self.controller.executor.pending), 1)

    def test_service_or_timeout_error_does_not_trigger_operator_prompt(self):
        for kind in ("daemon", "missing", "timeout", "protocol"):
            if not self.controller.refresh_running:
                self.controller.refresh()
            self.reply(Snapshot(error_kind=kind, preferences={"operator": ""}))
            self.assertEqual(self.controller.executor.pending, [])
            self.assertTrue(self.action("connection").isEnabled())

    def test_manual_disconnect_intent_survives_operator_repair(self):
        self.controller.settings.update(want_connected=False, automatic_login=True, automatic_reconnect=True)
        self.reply(Snapshot(backend_state="Stopped", preferences={"operator": ""}))
        self.finish_command()
        self.controller.refresh()
        self.reply(Snapshot(backend_state="Stopped", preferences={"operator": self.controller.local_user}))
        self.assertEqual(self.action("connection").text(), "Connect")
        self.assertFalse(self.controller.settings["want_connected"])
        self.assertEqual(self.controller.executor.pending, [])

    def test_permission_denied_with_matching_operator_does_not_loop_automatic_repair(self):
        self.controller.settings["automatic_login"] = True
        snapshot = Snapshot(backend_state="NeedsLogin", preferences={"operator": self.controller.local_user})
        with patch.object(self.controller, "notify"):
            self.reply(snapshot)
            self.finish_command(error=TailscaleError("login access denied", kind="permission"))
            for _ in range(3):
                self.controller.refresh()
                self.reply(snapshot)
                self.assertEqual(self.controller.executor.pending, [])
            self.assertTrue(self.action("grant-control").isEnabled())
            self.action("grant-control").trigger()
            self.finish_command()
            self.controller.refresh()
            self.reply(snapshot)
            self.assertEqual(self.controller.executor.pending[0][0], self.controller.cli.begin_sign_in)

    def test_stale_refresh_cannot_skip_operator_repair(self):
        self.reply(Snapshot(backend_state="Running", preferences={"operator": ""}))
        stale_token = (1, 0)
        stale = Future()
        stale.set_result(Snapshot(backend_state="Running", preferences={"operator": self.controller.local_user}))
        self.controller._finish_refresh((stale_token, stale))
        self.assertTrue(self.controller.control_required)
        self.assertTrue(self.controller.operation_busy)

    def test_operator_checked_again_after_profile_switch_and_later_loss(self):
        user = self.controller.local_user
        first = Snapshot(backend_state="Running", profiles=(Profile("a", "a", selected=True), Profile("b", "b")),
                         preferences={"operator": user})
        self.reply(first)
        with patch("tailtray.qt_app.save_settings"):
            self.action("profile:b").trigger()
            self.finish_command()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", profiles=(Profile("b", "b", selected=True),),
                                preferences={"operator": ""}))
            self.assertTrue(self.controller.operation_busy)
            self.finish_command()
            recovered = Snapshot(backend_state="Running", profiles=(Profile("b", "b", selected=True),),
                                 preferences={"operator": user})
            self.controller.refresh()
            self.reply(recovered)
            self.assertEqual(self.controller.executor.pending, [])
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", profiles=recovered.profiles, preferences={"operator": ""}))
            self.assertTrue(self.controller.operation_busy)

    def test_failed_browser_login_remains_visible_and_can_be_retried_manually(self):
        snapshot = Snapshot(backend_state="NeedsLogin", preferences={"operator": self.controller.local_user})
        self.controller.settings["automatic_login"] = True
        with patch.object(self.controller, "notify"), patch("tailtray.qt_app.save_settings"):
            self.reply(snapshot)
            self.finish_command(error=TailscaleError("coordination server unavailable", kind="command"))
            self.controller.refresh()
            self.reply(snapshot)
            self.assertEqual(self.controller.executor.pending, [])
            self.assertTrue(self.action("sign-in").isEnabled())
            self.assertIn("coordination server unavailable", self.action("health").text())
            self.action("sign-in").trigger()
            self.assertEqual(self.controller.executor.pending[0][0], self.controller.cli.begin_sign_in)

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

    def test_tailnet_switch_waits_for_observation_and_replaces_network_data(self):
        def snapshot(selected):
            return Snapshot(backend_state="Running", tailnet=selected,
                            profiles=tuple(Profile(p, "same@login", p, selected=p == selected) for p in ("a", "b")),
                            devices=(Device(selected, selected, ("100.64.0.1",)),),
                            exit_nodes=(ExitNode(selected, selected, "100.64.0.2", online=True),),
                            exit_node_id=selected, exit_node_online=True,
                            preferences={"exit-node": "100.64.0.2"})

        self.reply(snapshot("a"))
        root = self.controller.menu
        with patch("tailtray.qt_app.save_settings"):
            for old, new in (("a", "b"), ("b", "a")):
                self.action("profile:" + new).trigger()
                self.assertTrue(self.action("profile:" + old).isChecked())
                self.assertFalse(self.action("profiles").isEnabled())
                command, future = self.controller.executor.pending.pop(0)
                future.set_result(command())
                QApplication.processEvents()
                self.controller.cli.switch_profile.assert_called_with(new)
                self.assertFalse(self.action("profiles").isEnabled())
                self.controller.refresh()
                self.reply(snapshot(new))
                self.assertIs(self.controller.menu, root)
                self.assertTrue(self.action("profile:" + new).isChecked())
                self.assertEqual(self.action("profiles").text(), "Tailnet: " + new)
                self.assertIsNone(self.action("device:" + old))
                self.assertIsNone(self.action("exit:" + old))
                self.assertIsNotNone(self.action("exit:" + new))
                self.action("profile:" + new).trigger()
                self.assertEqual(self.controller.executor.pending, [])
        self.controller.cli.begin_interactive_login.assert_not_called()
        self.controller.cli.login_with_auth_key.assert_not_called()
        self.controller.cli.connect.assert_not_called()

    def test_failed_tailnet_switch_does_not_connect_the_old_network(self):
        self.controller.settings.update(want_connected=False, automatic_reconnect=True)
        snapshot = Snapshot(backend_state="Stopped", profiles=(Profile("a", "a", selected=True), Profile("b", "b")))
        self.reply(snapshot)
        with patch.object(self.controller, "notify") as notify:
            self.action("profile:b").trigger()
            _command, future = self.controller.executor.pending.pop(0)
            future.set_exception(TailscaleError("switch failed"))
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(snapshot)
        notify.assert_called_once()
        self.assertTrue(self.action("profile:a").isChecked())
        self.assertFalse(self.controller.settings["want_connected"])
        self.assertEqual(self.controller.executor.pending, [])

    def test_add_tailnet_uses_new_browser_login_not_old_url_or_key(self):
        profiles = (Profile("a", "a", selected=True),)
        self.controller.auth_key = "tskey-test-old-network"
        self.controller.settings["automatic_login"] = True
        self.reply(Snapshot(backend_state="Running", profiles=profiles, auth_url="https://login.tailscale.com/old"))
        with patch("tailtray.qt_app.save_settings"), patch.object(self.controller, "_open_login_url") as opened:
            self.action("add-tailnet").trigger()
            command, future = self.controller.executor.pending.pop(0)
            self.assertEqual(command, self.controller.cli.begin_interactive_login)
            future.set_result("https://login.tailscale.com/new")
            QApplication.processEvents()
            opened.assert_called_once_with("https://login.tailscale.com/new")
            for _ in range(2):
                self.controller.refresh()
                self.reply(Snapshot(backend_state="NeedsLogin", profiles=profiles, auth_url="https://login.tailscale.com/new"))
                self.assertEqual(self.controller.executor.pending, [])
            self.assertEqual(self.controller.settings["auth_key_profile"], "a")
        self.controller.cli.login_with_auth_key.assert_not_called()

    def test_disconnect_during_add_tailnet_does_not_suppress_later_login(self):
        self.controller.settings["automatic_login"] = True
        self.reply(Snapshot(backend_state="Running", profiles=(Profile("a", "a", selected=True),)))
        with patch("tailtray.qt_app.save_settings"), patch.object(self.controller, "_open_login_url") as opened:
            self.controller.add_tailnet()
            self.controller.disconnect_tailscale()
            _add, future = self.controller.executor.pending.pop(0)
            future.set_result("https://login.tailscale.com/cancelled")
            QApplication.processEvents()
            opened.assert_not_called()
            down, future = self.controller.executor.pending.pop(0)
            self.assertEqual(down, self.controller.cli.disconnect)
            future.set_result(None)
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Stopped"))
            self.controller.connect_tailscale()
            _connect, future = self.controller.executor.pending.pop(0)
            future.set_result(None)
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="NeedsLogin", auth_url="https://login.tailscale.com/new"))
            opened.assert_called_once_with("https://login.tailscale.com/new")
            self.controller.refresh()
            self.reply(Snapshot(backend_state="NeedsLogin", auth_url="https://login.tailscale.com/new"))
            self.assertEqual(opened.call_count, 1)

    def test_bound_auth_key_never_crosses_tailnets_or_missing_metadata(self):
        self.controller.auth_key = "tskey-test-network-a"
        self.controller.settings["auth_key_profile"] = "a"
        for profiles, expected in (((Profile("a", "a", selected=True),), True),
                                   ((Profile("b", "b", selected=True),), False), ((), False)):
            self.controller.snapshot = Snapshot(profiles=profiles)
            self.assertEqual(self.controller._auth_key_matches_profile(), expected)
        self.controller.settings["automatic_login"] = True
        self.reply(Snapshot(backend_state="NeedsLogin", profiles=(Profile("b", "b", selected=True),)))
        command, _future = self.controller.executor.pending.pop(0)
        self.assertEqual(command, self.controller.cli.begin_sign_in)
        self.controller.cli.login_with_auth_key.assert_not_called()

    def test_failed_key_replacement_does_not_relabel_the_previous_key(self):
        self.reply(Snapshot(backend_state="Running", profiles=(Profile("b", "b", selected=True),)))
        self.controller.settings["auth_key_profile"] = "a"
        self.controller.auth_key = "tskey-test-a"
        self.controller.secrets.set.side_effect = RuntimeError("keyring locked")
        with patch("tailtray.qt_app.save_settings"), self.assertRaises(RuntimeError):
            self.controller._store_auth_key("tskey-test-b")
        self.assertEqual(self.controller.settings["auth_key_profile"], "unbound")
        self.assertEqual(self.controller.auth_key, "")
        self.assertFalse(self.controller._auth_key_matches_profile())

    def test_failed_binding_write_still_blocks_a_retried_profile_change(self):
        self.reply(Snapshot(backend_state="Running", profiles=(Profile("a", "a", selected=True),)))
        self.controller.auth_key = "tskey-test-a"
        with patch("tailtray.qt_app.save_settings", side_effect=OSError("disk full")) as save:
            for _ in range(2):
                with self.assertRaises(OSError):
                    self.controller.add_tailnet()
                self.assertNotIn("auth_key_profile", self.controller.settings)
                self.assertEqual(self.controller.executor.pending, [])
            self.assertEqual(save.call_count, 2)

    def test_disconnect_queued_during_tailnet_switch_keeps_disconnected_intent(self):
        self.reply(Snapshot(backend_state="Running", profiles=(Profile("a", "a", selected=True), Profile("b", "b"))))
        with patch("tailtray.qt_app.save_settings"):
            self.action("profile:b").trigger()
            self.controller.disconnect_tailscale()
            command, future = self.controller.executor.pending.pop(0)
            future.set_result(command())
            QApplication.processEvents()
            self.assertFalse(self.controller.settings["want_connected"])
            down, future = self.controller.executor.pending.pop(0)
            self.assertEqual(down, self.controller.cli.disconnect)
            future.set_result(None)
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Stopped", profiles=(Profile("b", "b", selected=True),)))
        self.assertEqual(self.controller.executor.pending, [])

    def test_permission_failure_keeps_checkbox_and_offers_control_recovery(self):
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
            self.assertTrue(self.controller.operation_busy)
            _grant, future = self.controller.executor.pending.pop(0)
            future.set_exception(TailscaleError("permission request cancelled", kind="cancelled"))
            QApplication.processEvents()
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", preferences={"accept-dns": True}))
        self.assertFalse(action.isEnabled())
        self.assertTrue(action.isChecked())
        self.assertTrue(self.action("grant-control").isEnabled())
        self.assertEqual(self.action("connection").text(), "Grant this user control…")
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

    def test_control_poll_changes_do_not_mark_running_tunnel_offline(self):
        connection = None
        for online in (True, False, None, True):
            with self.subTest(self_online=online):
                if connection is not None:
                    self.controller.refresh()
                self.reply(Snapshot(backend_state="Running", self_online=online,
                                    exit_node_id="exit-1", exit_node_online=True))
                self.assertIn("Connected", self.labels())
                self.assertIn("Exit node: exit-1", self.labels())
                self.assertFalse(any("last reported" in label for label in self.labels()))
                self.assertIn("Disconnect", self.labels())
                if connection is not None:
                    self.assertIs(self.action("connection"), connection)
                connection = self.action("connection")
                self.assertEqual(self.controller.executor.pending, [])

    def test_new_exit_node_invalidates_host_cache_once_without_replacing_actions(self):
        notifier = Mock()
        self.controller.status_notifier = notifier
        old = ExitNode(id="old", name="Zulu", target="100.64.0.1", online=True)
        new = ExitNode(id="new", name="Alpha", target="100.64.0.2", online=True)
        self.reply(Snapshot(backend_state="Running", exit_nodes=(old,)))
        root = self.controller.menu
        submenu = self.action("exit-nodes").menu()
        old_action = self.action("exit:old")
        notifier.reset_mock()
        for nodes, notifications in (((new, old), 1), ((new, old), 1), ((old, new), 1), ((old,), 2)):
            self.controller.refresh()
            self.reply(Snapshot(backend_state="Running", exit_nodes=nodes))
            self.assertEqual(notifier.menu_changed.call_count, notifications)
            self.assertIs(self.controller.menu, root)
            self.assertIs(self.action("exit-nodes").menu(), submenu)
            self.assertIs(self.action("exit:old"), old_action)
            self.assertEqual(self.controller.executor.pending, [])

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
