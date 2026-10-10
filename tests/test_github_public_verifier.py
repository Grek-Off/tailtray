"""Public release verification must be credential-free and non-mutating."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("public_verifier", ROOT / "scripts/verify-github-release.py")
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
sys.path.pop(0)


class PublicVerifierTests(unittest.TestCase):
    def test_verifier_rejects_mutations_foreign_asset_endpoints_and_insecure_redirects(self):
        client = verifier.Anonymous()
        with patch.object(verifier, "get") as get:
            with self.assertRaises(ValueError):
                client.request("POST", "/releases")
            with self.assertRaises(ValueError):
                client.request("GET", "https://api.github.com/repos/other/repo/releases/assets/1")
        get.assert_not_called()
        with self.assertRaises(ValueError):
            verifier.HTTPSOnly().redirect_request(Request("https://github.com/a"), None, 302,
                                                 "Found", {}, "http://unsafe.invalid/a")

    def test_anonymous_request_never_sends_authorization_or_reads_keyring(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"public"
        opener = Mock()
        opener.open.return_value = response
        with patch.object(verifier, "build_opener", return_value=opener):
            self.assertEqual(verifier.get(verifier.API + "/releases"), b"public")
        request = opener.open.call_args.args[0]
        self.assertFalse(request.has_header("Authorization"))
        self.assertFalse(request.has_header("Cookie"))

    def test_invalid_version_fails_before_git_or_network(self):
        with patch.object(verifier.subprocess, "check_output") as git, patch.object(verifier, "get") as get:
            with self.assertRaises(ValueError):
                verifier.verify("../../main", ROOT)
        git.assert_not_called()
        get.assert_not_called()
