import json
import unittest

from tailtray.core import (
    CommandResult,
    ReconnectPolicy,
    Snapshot,
    TailscaleCLI,
    parse_profiles,
    parse_status,
    preference_value,
    privileged_operator_command,
    privileged_restart_service_command,
)


STATUS = {
    "Version": "1.90.6",
    "BackendState": "Running",
    "TailscaleIPs": ["100.64.0.10", "fd7a:115c:a1e0::10"],
    "Self": {"HostName": "fedora"},
    "CurrentTailnet": {"Name": "example.com"},
    "ExitNodeStatus": {"ID": "node-1", "Online": True},
    "Health": [],
    "Peer": {
        "nodekey:abc": {
            "ID": "node-1",
            "HostName": "amsterdam",
            "DNSName": "amsterdam.example.ts.net.",
            "TailscaleIPs": ["100.100.1.1"],
            "Online": True,
            "ExitNode": True,
            "ExitNodeOption": True,
            "Location": {"Country": "Netherlands", "City": "Amsterdam"},
        },
        "nodekey:def": {
            "ID": "node-2",
            "HostName": "offline-router",
            "TailscaleIPs": ["100.100.1.2"],
            "Online": False,
            "ExitNodeOption": True,
        },
    },
}


class CoreTests(unittest.TestCase):
    def test_parse_status_and_exit_nodes(self):
        snapshot = parse_status(STATUS)
        self.assertTrue(snapshot.connected)
        self.assertEqual(snapshot.tailnet, "example.com")
        self.assertEqual(snapshot.hostname, "fedora")
        self.assertEqual(snapshot.ips[0], "100.64.0.10")
        self.assertEqual([device.name for device in snapshot.devices], ["fedora", "amsterdam", "offline-router"])
        self.assertTrue(snapshot.devices[0].is_self)
        self.assertTrue(snapshot.devices[1].online)
        self.assertEqual(snapshot.devices[2].ips, ("100.100.1.2",))
        self.assertEqual([node.name for node in snapshot.exit_nodes], ["amsterdam", "offline-router"])
        self.assertTrue(snapshot.exit_nodes[0].selected)
        self.assertEqual(snapshot.exit_nodes[0].target, "100.100.1.1")
        self.assertTrue(snapshot.exit_node_active)

    def test_exit_node_is_not_active_when_tailnet_or_exit_node_is_offline(self):
        stopped_payload = json.loads(json.dumps(STATUS))
        stopped_payload["BackendState"] = "Stopped"
        stopped = parse_status(stopped_payload)
        self.assertFalse(stopped.exit_node_active)
        self.assertEqual(stopped.display_status, "Disconnected")

        exit_node_offline_payload = json.loads(json.dumps(STATUS))
        exit_node_offline_payload["ExitNodeStatus"]["Online"] = False
        exit_node_offline = parse_status(exit_node_offline_payload)
        self.assertFalse(exit_node_offline.exit_node_active)
        self.assertIn("exit node unavailable", exit_node_offline.display_status)

    def test_generic_hostname_uses_magicdns_device_name(self):
        payload = json.loads(json.dumps(STATUS))
        payload["Peer"]["nodekey:ipad"] = {
            "ID": "node-ipad",
            "HostName": "localhost",
            "DNSName": "ipad-air-gen-4.example.ts.net.",
            "TailscaleIPs": ["100.68.249.3"],
            "OS": "iOS",
            "Online": False,
        }

        snapshot = parse_status(payload)
        ipad = next(device for device in snapshot.devices if device.id == "node-ipad")

        self.assertEqual(ipad.name, "ipad-air-gen-4")

    def test_reconnect_policy_respects_manual_disconnect_and_uses_backoff(self):
        policy = ReconnectPolicy(base_delay=5, maximum_delay=20)
        stopped = Snapshot(backend_state="Stopped")

        self.assertFalse(policy.should_attempt(stopped, enabled=False, want_connected=True, now=0))
        self.assertFalse(policy.should_attempt(stopped, enabled=True, want_connected=False, now=0))
        self.assertFalse(
            policy.should_attempt(Snapshot(backend_state="NeedsLogin"), enabled=True, want_connected=True, now=0)
        )
        self.assertFalse(
            policy.should_attempt(Snapshot(backend_state="Starting"), enabled=True, want_connected=True, now=0)
        )
        self.assertTrue(policy.should_attempt(stopped, enabled=True, want_connected=True, now=0))
        self.assertFalse(policy.should_attempt(stopped, enabled=True, want_connected=True, now=0))
        self.assertEqual(policy.finish(succeeded=False, now=0), 5)
        self.assertFalse(policy.should_attempt(stopped, enabled=True, want_connected=True, now=4.9))
        self.assertTrue(policy.should_attempt(stopped, enabled=True, want_connected=True, now=5))
        self.assertEqual(policy.finish(succeeded=False, now=5), 10)
        policy.failures = 100
        self.assertEqual(policy.finish(succeeded=False, now=15), 20)
        self.assertFalse(policy.should_attempt(Snapshot(backend_state="Running"), enabled=True, want_connected=True, now=6))
        self.assertEqual(policy.failures, 0)

    def test_parse_profiles_current_cli_shape(self):
        profiles = parse_profiles(
            [
                {"id": "p1", "nickname": "Work", "tailnet": "corp.example", "account": "me@example.com", "selected": True},
                {"id": "p2", "nickname": "", "tailnet": "Personal", "account": "me@gmail.com", "selected": False},
            ]
        )
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0].label, "Work (corp.example)")
        self.assertEqual(profiles[1].label, "me@gmail.com (Personal)")
        self.assertTrue(profiles[0].selected)

    def test_snapshot_uses_argument_arrays_and_combines_payloads(self):
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            if argv[1:3] == ["status", "--json"]:
                return CommandResult(json.dumps(STATUS), "", 0)
            if argv[1:4] == ["switch", "--list", "--json"]:
                return CommandResult('[{"id":"p1","account":"me","selected":true}]', "", 0)
            if argv[1:4] == ["get", "--json", "all"]:
                return CommandResult('{"accept-dns":true}', "", 0)
            self.fail(str(argv))

        snapshot = TailscaleCLI(runner=runner).snapshot()
        self.assertEqual(snapshot.profiles[0].id, "p1")
        self.assertTrue(preference_value(snapshot.preferences, "accept-dns"))
        self.assertEqual(calls[0], ("tailscale", "status", "--json"))

    def test_command_failures_are_classified_without_throwing_from_snapshot(self):
        def runner(argv, timeout):
            return CommandResult("", "failed to connect to local tailscaled; it doesn't appear to be running", 1)

        snapshot = TailscaleCLI(runner=runner).snapshot()
        self.assertEqual(snapshot.error_kind, "daemon")
        self.assertIn("not running", snapshot.display_status)

    def test_boolean_preference_is_allowlisted(self):
        cli = TailscaleCLI(runner=lambda argv, timeout: CommandResult())
        with self.assertRaises(ValueError):
            cli.set_preference("hostname", True)

    def test_operator_command_rejects_option_injection(self):
        self.assertEqual(privileged_operator_command("vladimir")[-1], "--operator=vladimir")
        with self.assertRaises(ValueError):
            privileged_operator_command("--help")

    def test_login_url_is_extracted_from_timed_login_output(self):
        def runner(argv, timeout):
            return CommandResult("", "To authenticate, visit:\nhttps://login.tailscale.com/a/abc123\n", 1)

        self.assertEqual(
            TailscaleCLI(runner=runner).begin_interactive_login(),
            "https://login.tailscale.com/a/abc123",
        )

    def test_connect_preserves_nondefault_preferences_by_passing_no_flags(self):
        calls = []

        def runner(argv, timeout):
            calls.append((tuple(argv), timeout))
            if len(argv) > 2:
                return CommandResult(
                    "",
                    "changing settings via 'tailscale up' requires mentioning all non-default flags: "
                    "tailscale up --exit-node=100.90.32.25",
                    1,
                )
            return CommandResult()

        TailscaleCLI(runner=runner).connect()

        self.assertEqual(calls, [(("tailscale", "up"), 25)])

    def test_full_restart_runs_down_service_restart_status_and_up(self):
        calls = []

        def runner(argv, timeout):
            calls.append(tuple(argv))
            return CommandResult("{}" if argv[1:3] == ["status", "--json"] else "", "", 0)

        cli = TailscaleCLI(runner=runner)
        cli.full_restart(lambda: calls.append(("systemctl", "restart", "tailscaled.service")))
        self.assertEqual(
            calls,
            [
                ("tailscale", "down"),
                ("systemctl", "restart", "tailscaled.service"),
                ("tailscale", "status", "--json"),
                ("tailscale", "up"),
            ],
        )

    def test_privileged_restart_command_is_fixed(self):
        self.assertEqual(
            privileged_restart_service_command(),
            ["pkexec", "systemctl", "restart", "tailscaled.service"],
        )


if __name__ == "__main__":
    unittest.main()
