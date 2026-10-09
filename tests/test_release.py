"""Exercise publishing with in-memory GitLab; no credentials/network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request

_spec = importlib.util.spec_from_file_location(
    "gitlab_release", Path(__file__).resolve().parents[1] / "scripts/gitlab_release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


class MemoryGitLab(release.GitLab):
    def __init__(self):
        super().__init__("https://gitlab.invalid/api/v4", "123", "synthetic-not-a-secret")
        self.storage = {}
        self.writes = []

    def request(self, method, path, data=None, json_data=None, missing=False):
        if method == "GET":
            if path not in self.storage and not missing:
                raise ValueError("Missing fixture")
            return self.storage.get(path)
        self.writes.append((method, path))
        if path == "/releases":
            self.storage["/releases/" + json_data["tag_name"]] = json.dumps({
                "commit": {"id": json_data["ref"]}, "assets": json_data["assets"]}).encode()
        else:
            self.storage[path] = data if json_data is None else json.dumps(json_data).encode()
        return b"{}"


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.client = MemoryGitLab()
        self.commit = "a" * 40
        self.paths = [self.root / name for name in release.artifact_names("0.1.0")]
        for path in self.paths:
            path.write_bytes(path.name.encode())
        sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(self.paths))
        (self.root / "SHA256SUMS").write_text(sums)
        (self.root / "SHA256SUMS.asc").write_bytes(b"synthetic signature")
        (self.root / "repository-key.asc").write_bytes(b"synthetic public key")

    def test_complete_matrix_publishes_ten_assets_and_retry_is_noop(self):
        release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(len(self.client.writes), 11)
        before = list(self.client.writes)
        release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(self.client.writes, before)
        published = json.loads(self.client.storage["/releases/v0.1.0"])
        self.assertEqual(len(published["assets"]["links"]), 10)
        self.assertNotIn("synthetic-not-a-secret", json.dumps(published))

    def test_partial_matrix_does_not_publish(self):
        self.paths[-1].unlink()
        with self.assertRaisesRegex(ValueError, "Incomplete release matrix"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(self.client.writes, [])

    def test_bad_tag_or_commit_does_not_publish(self):
        for tag, commit in (("main", self.commit), ("v1.2.3.4", self.commit), ("v0.1.0", "short")):
            with self.assertRaises(ValueError):
                release.publish(self.client, self.root, tag, commit)
        self.assertEqual(self.client.writes, [])

    def test_stale_version_excluded_but_unexpected_target_rejected(self):
        (self.root / "tailtray-0.0.9.tar.gz").write_bytes(b"stale")
        self.assertEqual(len(release.artifacts(self.root, "0.1.0")), 7)
        (self.root / "tailtray-0.1.0-1.fc42.noarch.rpm").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "Unexpected"):
            release.artifacts(self.root, "0.1.0")

    def test_checksum_failure_does_not_publish(self):
        self.paths[0].write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "checksums"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(self.client.writes, [])

    def test_existing_different_commit_is_never_changed(self):
        self.client.storage["/releases/v0.1.0"] = json.dumps({"commit": {"id": "b" * 40}}).encode()
        with self.assertRaisesRegex(ValueError, "another commit"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(self.client.writes, [])

    def test_existing_asset_is_immutable(self):
        endpoint = self.client.package_path("tailtray", "v0.1.0", self.paths[0].name)
        self.client.storage[endpoint] = b"previous"
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(self.client.writes, [])

    def test_partial_upload_can_be_retried(self):
        first = self.paths[0]
        self.client.storage[self.client.package_path("tailtray", "v0.1.0", first.name)] = first.read_bytes()
        release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(len(self.client.writes), 10)

    def test_redirect_strips_authentication_on_foreign_https_host(self):
        request = Request("https://gitlab.invalid/api", headers={"JOB-TOKEN": "synthetic"})
        redirected = release.SafeRedirects().redirect_request(
            request, None, 302, "Found", {}, "https://storage.invalid/file")
        self.assertFalse(redirected.has_header("Job-token"))
        with self.assertRaises(ValueError):
            release.SafeRedirects().redirect_request(request, None, 302, "Found", {}, "http://storage.invalid/file")

    def test_empty_history_valid_but_auth_errors_not_first_release(self):
        self.client.request = Mock(return_value=b"[]")
        self.assertFalse(release.fetch_previous(self.client, self.root / "previous", "A" * 40))
        self.client.request.side_effect = ValueError("HTTP 403")
        with self.assertRaisesRegex(ValueError, "403"):
            release.fetch_previous(self.client, self.root / "previous", "A" * 40)

    def test_history_uses_semver_and_verifies_signer_before_payload(self):
        packages = [{"name": "tailtray-repository", "version": version, "status": "default"}
                    for version in ("v0.9.0", "v0.10.0", "v0.11.0")]
        manifest = {"schema": 1, "files": {"downloads/test": {"size": 7,
                    "sha256": hashlib.sha256(b"fixture").hexdigest()}}}
        calls = []

        def request(method, path, **kwargs):
            calls.append(path)
            if path.startswith("/packages?"):
                return json.dumps(packages).encode()
            if "v0.11.0" in path:
                return None
            if path.endswith("manifest.json.asc"):
                return b"signature"
            if path.endswith("manifest.json"):
                return json.dumps(manifest).encode()
            return b"fixture"

        self.client.request = request
        with patch.object(release.subprocess, "run", return_value=Mock(returncode=0,
                          stdout="[GNUPG:] VALIDSIG " + "A" * 40 + " 0 0 0")):
            self.assertTrue(release.fetch_previous(self.client, self.root / "previous", "A" * 40))
        self.assertEqual((self.root / "previous/downloads/test").read_bytes(), b"fixture")
        self.assertFalse(any("v0.9.0" in call for call in calls))

    def test_manifest_path_rejects_escape(self):
        for name in ("../secret", "/etc/file", "a/../b", "a\\b", "a//b", "./file"):
            with self.assertRaises(ValueError):
                release.safe_path(name)

    def test_real_cli_uses_protected_ci_fingerprint_without_extra_flag(self):
        env = {"CI_API_V4_URL": "https://gitlab.invalid/api/v4", "CI_PROJECT_ID": "123",
               "CI_JOB_TOKEN": "synthetic-not-a-secret", "TAILTRAY_SIGNING_FINGERPRINT": "A" * 40}
        arguments = ["gitlab_release.py", "fetch-previous", "--directory", str(self.root / "previous")]
        with patch.dict(os.environ, env), patch.object(release.sys, "argv", arguments), \
                patch.object(release, "GitLab", return_value=self.client), \
                patch.object(release, "fetch_previous") as fetch:
            self.assertEqual(release.main(), 0)
        fetch.assert_called_once_with(self.client, self.root / "previous", "A" * 40)

    def test_repository_upload_uses_nested_source_and_only_immutable_payload(self):
        public = self.root / "public"
        source = public / "downloads/source/tailtray-0.1.0.tar.gz"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        manifest = {"schema": 1, "versions": ["0.1.0"], "files": {
            "downloads/source/tailtray-0.1.0.tar.gz": {
                "sha256": hashlib.sha256(b"source").hexdigest(), "size": 6}}}
        (public / "manifest.json").write_text(json.dumps(manifest))
        (public / "manifest.json.asc").write_bytes(b"signature")
        (public / "index.html").write_text("generated index")
        release.publish_repository(self.client, public, "v0.1.0")
        self.assertEqual(len(self.client.writes), 3)
        (public / "index.html").write_text("regenerated index, not an immutable release payload")
        release.publish_repository(self.client, public, "v0.1.0")
        self.assertEqual(len(self.client.writes), 3)
        with self.assertRaisesRegex(ValueError, "tagged source"):
            release.publish_repository(self.client, public, "v0.1.1")
