import json
import unittest

from tailtray.core import (
    CommandResult,
    TailscaleCLI,
    parse_profiles,
    parse_status,
    preference_value,
    privileged_operator_command,
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
        self.assertEqual([node.name for node in snapshot.exit_nodes], ["amsterdam", "offline-router"])
        self.assertTrue(snapshot.exit_nodes[0].selected)
        self.assertEqual(snapshot.exit_nodes[0].target, "100.100.1.1")

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


if __name__ == "__main__":
    unittest.main()
