"""Credential boundary tests use synthetic values and never the real wallet."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "github_credentials", Path(__file__).resolve().parents[1] / "scripts/github_credentials.py")
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)
TOKEN = "github_pat_" + "synthetic_test_only_" * 3


def completed(code=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], code, out, err)


class GitHubCredentialTests(unittest.TestCase):
    def test_store_uses_stdin_owner_attributes_and_verifies_readback(self):
        with patch.object(credentials.subprocess, "run", side_effect=[
            completed(1), completed(), completed(out=TOKEN.encode())
        ]) as run:
            credentials.store_token("Grek-Off", TOKEN)
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        arguments = calls[1].args[0]
        self.assertEqual(arguments[:3], ["secret-tool", "store", "--label"])
        self.assertEqual(arguments[-6:], ["application", "tailtray", "purpose", "github-token", "owner", "grek-off"])
        self.assertNotIn(TOKEN, " ".join(arguments))
        self.assertEqual(calls[1].kwargs["input"], TOKEN.encode())
        self.assertTrue(all(call.kwargs["capture_output"] and call.kwargs["timeout"] == 30 for call in calls))

    def test_existing_entry_is_not_replaced(self):
        with patch.object(credentials.subprocess, "run", return_value=completed(out=TOKEN.encode())) as run:
            with self.assertRaisesRegex(credentials.CredentialError, "refusing to overwrite"):
                credentials.store_token("Grek-Off", TOKEN)
        self.assertEqual(run.call_count, 1)

    def test_absent_is_distinguished_from_unavailable_or_malformed(self):
        with patch.object(credentials.subprocess, "run", return_value=completed(1)):
            self.assertIsNone(credentials.read_token("Grek-Off"))
        for result in (completed(1, err=TOKEN.encode()), completed(2), completed(),
                       completed(out=b"\xff"), completed(out=b"x" * 1025),
                       completed(out=TOKEN.encode() + b"\x00")):
            with self.subTest(result_code=result.returncode):
                with patch.object(credentials.subprocess, "run", return_value=result):
                    with self.assertRaises(credentials.CredentialError) as raised:
                        credentials.read_token("Grek-Off")
                self.assertNotIn(TOKEN, str(raised.exception))

    def test_missing_tool_and_timeouts_do_not_expose_exception_payloads(self):
        for error in (FileNotFoundError(TOKEN), subprocess.TimeoutExpired([TOKEN], 30, output=TOKEN.encode())):
            with patch.object(credentials.subprocess, "run", side_effect=error):
                with self.assertRaises(credentials.CredentialError) as raised:
                    credentials.read_token("Grek-Off")
                self.assertNotIn(TOKEN, str(raised.exception))

    def test_input_validation_fails_before_any_provider_call(self):
        with patch.object(credentials.subprocess, "run") as run:
            for owner in ("", "../other", "-other", "other\nowner", "x" * 40):
                with self.assertRaises(credentials.CredentialError):
                    credentials.read_token(owner)
            for token in ("", "classic_token", TOKEN + "\n", TOKEN + "\x00"):
                with self.assertRaises(credentials.CredentialError):
                    credentials.store_token("Grek-Off", token)
            with patch.object(credentials.sys, "platform", "win32"):
                with self.assertRaises(credentials.CredentialError):
                    credentials.read_token("Grek-Off")
        run.assert_not_called()

    def test_store_failure_and_readback_mismatch_are_not_success(self):
        for responses in ([completed(1), completed(2, err=TOKEN.encode())],
                          [completed(1), completed(), completed(1)],
                          [completed(1), completed(), completed(out=(TOKEN + "other").encode())]):
            with patch.object(credentials.subprocess, "run", side_effect=responses):
                with self.assertRaises(credentials.CredentialError) as raised:
                    credentials.store_token("Grek-Off", TOKEN)
                self.assertNotIn(TOKEN, str(raised.exception))

    def test_cli_hidden_input_and_check_never_print_the_token(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(credentials, "read_token", return_value=None), \
             patch.object(credentials, "store_token") as store, \
             patch.object(credentials.sys.stdin, "isatty", return_value=True), \
             patch.object(credentials.getpass, "getpass", return_value=" " + TOKEN + " "):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(credentials.main(["--owner", "Grek-Off"]), 0)
        store.assert_called_once_with("Grek-Off", TOKEN)
        with patch.object(credentials, "read_token", return_value=TOKEN):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(credentials.main(["--owner", "Grek-Off", "--check"]), 0)
        self.assertNotIn(TOKEN, output.getvalue() + errors.getvalue())

    def test_cli_refuses_pipe_or_getpass_echo_fallback(self):
        with patch.object(credentials, "read_token", return_value=None), \
             patch.object(credentials, "store_token") as store, \
             patch.object(credentials.sys.stdin, "isatty", return_value=False), \
             patch.object(credentials.getpass, "getpass") as prompt:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(credentials.main(["--owner", "Grek-Off"]), 1)
            prompt.assert_not_called()
            store.assert_not_called()
        with patch.object(credentials, "read_token", return_value=None), \
             patch.object(credentials, "store_token") as store, \
             patch.object(credentials.sys.stdin, "isatty", return_value=True), \
             patch.object(credentials.getpass, "getpass", side_effect=credentials.getpass.GetPassWarning(TOKEN)):
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                self.assertEqual(credentials.main(["--owner", "Grek-Off"]), 1)
            self.assertNotIn(TOKEN, output.getvalue())
            store.assert_not_called()

