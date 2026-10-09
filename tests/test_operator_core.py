import json
import subprocess
import unittest
from unittest.mock import patch

from tailtray.core import CommandResult, Snapshot, TailscaleCLI, TailscaleError


STATUS_RUNNING = {
    "Version": "1.102.5",
    "BackendState": "Running",
    "Self": {"HostName": "host", "Online": True},
    "CurrentTailnet": {"Name": "example.ts.net"},
    "Health": [],
}


class OperatorCoreTests(unittest.TestCase):
    def test_operator_user_accepts_string_mapping_and_rejects_malformed_values(self):
        self.assertEqual(Snapshot(preferences={"operator": "alice"}).operator_user, "alice")
        self.assertEqual(
            Snapshot(preferences={"operator": {"value": "alice"}}).operator_user,
            "alice",
        )
        for value in (None, 7, {"value": 7}, {"other": "alice"}):
            with self.subTest(value=value):
                self.assertIsNone(Snapshot(preferences={"operator": value}).operator_user)

    def test_connected_status_survives_metadata_permission_error(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv[1:])
            if argv[1:3] == ["status", "--json"]:
                return CommandResult(stdout=json.dumps(STATUS_RUNNING))
            if argv[1:4] == ["switch", "--list", "--json"]:
                return CommandResult(stderr="permission denied", returncode=1)
            return CommandResult(stderr="permission denied", returncode=1)

        snapshot = TailscaleCLI(runner=runner).snapshot()
        self.assertTrue(snapshot.connected)
        self.assertEqual(snapshot.control_error_kind, "permission")
        self.assertIn("permission denied", snapshot.control_error)
        self.assertEqual(snapshot.operator_user, None)
        self.assertEqual(calls[0], ["status", "--json"])

    def test_missing_or_unsupported_get_does_not_create_false_permission_state(self):
        for stderr in ("The tailscale command is not installed", "unknown command get"):
            with self.subTest(stderr=stderr):
                def runner(argv, timeout):
                    if argv[1:3] == ["status", "--json"]:
                        return CommandResult(stdout=json.dumps(STATUS_RUNNING))
                    return CommandResult(stderr=stderr, returncode=1)

                snapshot = TailscaleCLI(runner=runner).snapshot()
                self.assertTrue(snapshot.connected)
                self.assertNotEqual(snapshot.control_error_kind, "permission")
                self.assertIsNone(snapshot.operator_user)

    def test_grant_operator_uses_only_pkexec_set_and_invalidates_cache_on_success(self):
        calls = []

        def runner(argv, timeout):
            calls.append((argv, timeout))
            return CommandResult()

        cli = TailscaleCLI(runner=runner)
        cli._profiles_cache = (0, (), ())
        cli._preferences_cache = (0, {"operator": "old"}, ())
        cli.grant_operator("alice")

        self.assertEqual(calls, [(["pkexec", "tailscale", "set", "--operator=alice"], 60)])
        self.assertIsNone(cli._profiles_cache)
        self.assertIsNone(cli._preferences_cache)
        for argv, _ in calls:
            self.assertNotIn(argv[2], ("up", "down", "login"))

    def test_grant_operator_invalidates_cache_on_failure(self):
        def runner(argv, timeout):
            return CommandResult(stderr="authorization failed", returncode=1)

        cli = TailscaleCLI(runner=runner)
        cli._profiles_cache = (0, (), ())
        cli._preferences_cache = (0, {"operator": "old"}, ())
        with self.assertRaises(TailscaleError):
            cli.grant_operator("alice")
        self.assertIsNone(cli._profiles_cache)
        self.assertIsNone(cli._preferences_cache)

    def test_grant_operator_rejects_invalid_username_without_running_command(self):
        calls = []

        def runner(argv, timeout):
            calls.append(argv)
            return CommandResult()

        cli = TailscaleCLI(runner=runner)
        with self.assertRaises(ValueError):
            cli.grant_operator("--help")
        self.assertEqual(calls, [])

    def test_interactive_login_keeps_new_profile_login_contract(self):
        calls = []

        def runner(argv, timeout):
            calls.append((argv, timeout))
            return CommandResult(stdout="https://login.tailscale.com/a/new-profile")

        self.assertEqual(
            TailscaleCLI(runner=runner).begin_interactive_login(),
            "https://login.tailscale.com/a/new-profile",
        )
        self.assertEqual(calls, [(["tailscale", "login", "--timeout=3s"], 7)])

    def test_sign_in_extracts_url_from_timeout_output_without_running_cli(self):
        timeout = subprocess.TimeoutExpired(
            ["tailscale", "up"], 7, output=b"To authenticate: https://login.tailscale.com/a/bytes123\n"
        )
        with patch("tailtray.core.subprocess.run", side_effect=timeout) as run:
            self.assertEqual(
                TailscaleCLI().begin_sign_in(),
                "https://login.tailscale.com/a/bytes123",
            )
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["tailscale", "up"])

    def test_sign_in_timeout_without_url_fails(self):
        timeout = subprocess.TimeoutExpired(["tailscale", "up"], 7, output=b"still starting")
        with patch("tailtray.core.subprocess.run", side_effect=timeout):
            with self.assertRaises(TailscaleError) as raised:
                TailscaleCLI().begin_sign_in()
        self.assertEqual(raised.exception.kind, "timeout")

    def test_sign_in_uses_bare_up_to_preserve_nondefault_preferences(self):
        calls = []

        def runner(argv, timeout):
            calls.append((argv, timeout))
            return CommandResult()

        self.assertEqual(TailscaleCLI(runner=runner).begin_sign_in(), "")
        self.assertEqual(calls, [(["tailscale", "up"], 7)])


if __name__ == "__main__":
    unittest.main()
