import unittest

from tailtray.core import Profile, Snapshot
from tailtray.menu import build_menu


def entries(snapshot=Snapshot(), **options):
    defaults = dict(status=snapshot.display_status, busy=False, operation=None, settings={},
                    secrets_available=False, has_key=False, autostart=False)
    defaults.update(options)
    return build_menu(snapshot, **defaults)


def flatten(items):
    for entry in items:
        yield entry
        yield from flatten(entry.children or ())


class MenuModelTests(unittest.TestCase):
    def test_tailnets_are_accessible_and_distinguish_the_same_login(self):
        profiles = (Profile("work", "me@example.com", "work.example", selected=True),
                    Profile("home", "me@example.com", "home.example"))
        for state in ("Running", "Stopped", "NeedsLogin"):
            root = {e.key: e for e in entries(Snapshot(backend_state=state, tailnet="work.example", profiles=profiles))}
            chooser = root["profiles"]
            self.assertEqual(chooser.label, "Tailnet: work.example")
            self.assertTrue(chooser.enabled)
            items = {e.key: e for e in chooser.children}
            self.assertEqual(items["profile:home"].args, ("home",))
            self.assertEqual(items["profile:home"].label, "home.example — me@example.com")
            self.assertTrue(items["profile:work"].checked)
            self.assertFalse(items["profile:home"].checked)
            self.assertTrue(items["add-tailnet"].enabled)
        for options in ({"busy": True}, {"current": False}):
            root = {e.key: e for e in entries(Snapshot(profiles=profiles), **options)}
            self.assertFalse(root["profiles"].enabled)
            self.assertTrue(all(not e.enabled for e in root["profiles"].children if not e.separator))

    def test_tailnet_can_be_added_without_saved_profiles(self):
        root = {e.key: e for e in entries(Snapshot(backend_state="NeedsLogin"))}
        self.assertTrue(root["profiles"].enabled)
        self.assertEqual(root["profiles"].children[-1].command, "add-tailnet")

    def test_every_backend_state_has_a_recovery_action(self):
        for state, command in (("Running", "disconnect"), ("Stopped", "connect"),
                               ("Starting", "disconnect"), ("NeedsLogin", "login"),
                               ("NeedsMachineAuth", "admin"), ("Unknown", "refresh")):
            with self.subTest(state=state):
                item = next(e for e in entries(Snapshot(backend_state=state)) if e.key == "connection")
                self.assertTrue(item.enabled)
                self.assertEqual(item.command, command)

    def test_every_error_has_a_recovery_action(self):
        for kind, command in (("missing", "installation"), ("daemon", "start-service"),
                              ("permission", "grant-operator"), ("timeout", "refresh"),
                              ("protocol", "refresh"), ("command", "refresh")):
            with self.subTest(kind=kind):
                item = next(e for e in entries(Snapshot(error_kind=kind)) if e.key == "connection")
                self.assertTrue(item.enabled)
                self.assertEqual(item.command, command)

    def test_root_identity_does_not_depend_on_busy_or_connection_state(self):
        baseline = [e.key for e in entries()]
        for state in ("Running", "Stopped", "Starting", "NeedsLogin", "Unknown"):
            for busy in (True, False):
                self.assertEqual([e.key for e in entries(Snapshot(backend_state=state), busy=busy)], baseline)

    def test_pending_disconnect_disables_only_primary_not_recovery_navigation(self):
        model = {e.key: e for e in entries(busy=True, operation="Disconnecting")}
        self.assertFalse(model["connection"].enabled)
        self.assertEqual(model["connection"].label, "Disconnecting…")
        self.assertTrue(model["more"].enabled)
        self.assertTrue(model["quit"].enabled)

    def test_stale_observation_has_refresh_but_no_mutating_actions(self):
        model = {e.key: e for e in flatten(entries(Snapshot(backend_state="Stopped"), current=False))}
        self.assertEqual(model["connection"].command, "refresh")
        self.assertFalse(model["restart"].enabled)
        self.assertFalse(model["exit-nodes"].enabled)
        self.assertTrue(model["refresh"].enabled)

    def test_stale_service_error_does_not_offer_another_start(self):
        model = {e.key: e for e in entries(Snapshot(error_kind="daemon"), current=False)}
        self.assertEqual(model["connection"].command, "refresh")

    def test_exit_node_can_be_configured_while_stopped(self):
        model = {e.key: e for e in flatten(entries(Snapshot(backend_state="Stopped")))}
        self.assertTrue(model["exit-nodes"].enabled)
        self.assertTrue(model["auto"].enabled)

    def test_auto_selection_without_candidate_is_not_none(self):
        for state, suffix in (("Stopped", "inactive"), ("Running", "availability unknown")):
            snapshot = Snapshot(backend_state=state, preferences={"exit-node": "auto:any"})
            model = {e.key: e for e in flatten(entries(snapshot))}
            self.assertTrue(model["auto"].checked)
            self.assertFalse(model["none"].checked)
            self.assertEqual(snapshot.exit_node_label, f"Exit node: Automatic ({suffix})")

    def test_configured_ip_without_status_is_not_active_or_none(self):
        snapshot = Snapshot(backend_state="Stopped", preferences={"exit-node": "100.64.0.1"})
        model = {e.key: e for e in flatten(entries(snapshot))}
        self.assertFalse(model["none"].checked)
        self.assertEqual(snapshot.exit_node_label, "Exit node: 100.64.0.1 (inactive)")

    def test_unavailable_preferences_are_not_editable_defaults(self):
        model = {e.key: e for e in flatten(entries(Snapshot(backend_state="Running")))}
        self.assertFalse(model["preferences"].enabled)
        self.assertFalse(model["pref:accept-dns"].enabled)

    def test_unknown_exit_preference_does_not_claim_none_is_configured(self):
        snapshot = Snapshot(backend_state="Stopped", preferences={})
        model = {e.key: e for e in flatten(entries(snapshot))}
        self.assertFalse(model["none"].checked)
        self.assertFalse(model["auto"].checked)
        self.assertEqual(snapshot.exit_node_state, "unknown")
        self.assertEqual(snapshot.exit_node_label, "Exit node: unknown")
