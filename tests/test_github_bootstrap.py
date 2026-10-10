"""Owner migration boundaries; all credentials and API responses are synthetic."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("github_bootstrap", ROOT / "scripts/github-bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)
sys.path.pop(0)


class BootstrapTests(unittest.TestCase):
    def test_credential_protocol_stops_at_blank_line_without_waiting_for_eof(self):
        class OpenPipe:
            def __init__(self):
                self.lines = iter(["protocol=https\n", "host=github.com\n",
                                   "path=Grek-Off/tailtray.git\n", "\n"])

            def readline(self, limit):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise AssertionError("Git is still waiting: no EOF is available")

        with patch.object(bootstrap.sys, "stdin", OpenPipe()), \
                patch.object(bootstrap, "read_token", return_value="synthetic"), \
                contextlib.redirect_stdout(io.StringIO()):
            bootstrap.credential("get")

    def test_credential_pipe_is_scoped_and_store_erase_cannot_mutate_wallet(self):
        request = "protocol=https\nhost=github.com\npath=Grek-Off/tailtray.git\n\n"
        with patch.object(bootstrap, "read_token", return_value="synthetic") as read:
            output = io.StringIO()
            with patch.object(bootstrap.sys, "stdin", io.StringIO(request)), contextlib.redirect_stdout(output):
                bootstrap.credential("get")
            self.assertEqual(output.getvalue(), "username=x-access-token\npassword=synthetic\n\n")
            for action in ("store", "erase"):
                bootstrap.credential(action)
            self.assertEqual(read.call_count, 1)

    def test_repeated_http_challenges_and_capabilities_are_valid_protocol_arrays(self):
        request = ("protocol=https\nhost=github.com\npath=Grek-Off/tailtray.git\n"
                   "wwwauth[]=Basic realm=GitHub\nwwwauth[]=Bearer\n"
                   "capability[]=authtype\ncapability[]=state\n\n")
        output = io.StringIO()
        with patch.object(bootstrap.sys, "stdin", io.StringIO(request)), \
                patch.object(bootstrap, "read_token", return_value="synthetic"), \
                contextlib.redirect_stdout(output):
            bootstrap.credential("get")
        self.assertIn("password=synthetic", output.getvalue())

    def test_credential_requests_for_other_hosts_repos_protocols_or_duplicates_fail(self):
        requests = ["protocol=http\nhost=github.com\npath=Grek-Off/tailtray.git",
                    "protocol=https\nhost=evil.invalid\npath=Grek-Off/tailtray.git",
                    "protocol=https\nhost=github.com\npath=Grek-Off/other.git",
                    "protocol=https\nhost=github.com\nhost=github.com\npath=Grek-Off/tailtray.git"]
        with patch.object(bootstrap, "read_token") as read:
            for request in requests:
                with patch.object(bootstrap.sys, "stdin", io.StringIO(request)):
                    with self.assertRaises(RuntimeError):
                        bootstrap.credential("get")
        read.assert_not_called()

    def test_admin_client_rejects_other_repos_traversal_and_destructive_methods(self):
        api = bootstrap.API("synthetic")
        api.opener = Mock()
        for method, path in [("GET", "/repos/Grek-Off/other"), ("DELETE", bootstrap.PREFIX),
                             ("GET", bootstrap.PREFIX + "/../other"),
                             ("GET", bootstrap.PREFIX + "/%2e%2e/other"),
                             ("GET", "https://api.github.com" + bootstrap.PREFIX),
                             ("GET", "/user/repos")]:
            with self.assertRaises(RuntimeError):
                api.request(method, path)
        api.opener.open.assert_not_called()

    def test_redirect_is_refused_and_wrong_account_fails(self):
        with self.assertRaises(RuntimeError):
            bootstrap.NoRedirects().redirect_request(None, None, 302, "Found", {}, "https://evil.invalid")
        api = bootstrap.API("synthetic")
        api.request = Mock(return_value={"login": "other", "id": 24355001})
        with self.assertRaisesRegex(RuntimeError, "authorized GitHub owner"):
            api.identity()

    def test_signing_requires_tag_only_environment_and_refuses_secret_replacement(self):
        api = Mock()
        api.request.side_effect = [{"deployment_branch_policy": {"custom_branch_policies": True}},
                                  {"branch_policies": [{"name": "v*.*.*", "type": "tag"}]},
                                  {"name": "TAILTRAY_SIGNING_KEY"}]
        with patch.object(bootstrap.subprocess, "run") as run, contextlib.redirect_stdout(io.StringIO()):
            bootstrap.signing(api)
        run.assert_not_called()
        self.assertTrue(all(call.args[0] == "GET" for call in api.request.call_args_list))
        api.request.side_effect = [{"deployment_branch_policy": {"protected_branches": True}},
                                  {"branch_policies": [{"name": "*", "type": "branch"}]}]
        with self.assertRaisesRegex(RuntimeError, "restricted to release tags"):
            bootstrap.signing(api)
