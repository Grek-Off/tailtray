"""Offline regression tests for the GitHub Releases backend."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("github_release", ROOT / "scripts/github_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class MemoryGitHub:
    def __init__(self, *, tag_commit=None):
        self.tag_commit_value = tag_commit or "a" * 40
        self.releases = {}
        self.uploads = {}
        self.calls = []
        self.fail_after_upload = False
        self.archive_bytes = None
        self.release_page = None

    def tag_commit(self, tag):
        self.calls.append(("tag", tag))
        return self.tag_commit_value

    def release(self, tag):
        return self.releases.get(tag)

    def json(self, method, path, data=None, *, missing=False):
        self.calls.append((method, path, data))
        if method == "GET" and path.startswith("/releases/") and path != "/releases/tags/v0.1.0":
            return self.releases["v0.1.0"]
        if method == "POST" and path == "/releases":
            value = {"id": 1, "tag_name": data["tag_name"], "target_commitish": data["target_commitish"],
                     "draft": True, "upload_url": "https://uploads.invalid/assets{?name}", "assets": []}
            self.releases[data["tag_name"]] = value
            return value
        if method == "PATCH":
            self.releases["v0.1.0"]["draft"] = False
            self.releases["v0.1.0"]["immutable"] = True
            return self.releases["v0.1.0"]
        raise AssertionError((method, path))

    def request(self, method, path, data=None, json_data=None, *, missing=False, accept=None):
        self.calls.append((method, path))
        if method == "GET":
            if path.startswith("/releases?"):
                if self.release_page is not None:
                    return json.dumps(self.release_page).encode()
                if self.archive_bytes is not None:
                    archive = self.archive_bytes
                    return json.dumps([{"tag_name": "v0.1.0", "draft": False, "prerelease": False,
                                         "assets": [{"name": "tailtray-repository-0.1.0.tar.gz", "url": "archive",
                                                     "state": "uploaded", "size": len(archive),
                                                     "digest": "sha256:" + hashlib.sha256(archive).hexdigest()}]}]).encode()
                return b"[]"
            if path == "archive":
                return self.archive_bytes
            return self.uploads[path]
        if method == "POST":
            name = parse_qs(urlsplit(path).query)["name"][0]
            asset_url = "asset:" + name
            self.uploads[asset_url] = data
            release = self.releases["v0.1.0"]
            release.setdefault("assets", []).append({"name": name, "url": asset_url, "state": "uploaded",
                                                       "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
            if self.fail_after_upload:
                self.fail_after_upload = False
                raise ValueError("simulated timeout after accepted upload")
            return b"{}"
        raise AssertionError((method, path))


class GitHubReleaseTests(unittest.TestCase):
    def test_by_tag_404_falls_back_to_authenticated_draft_listing(self):
        client = release.GitHub("Grek-Off/tailtray", "synthetic")
        draft = {"tag_name": "v0.1.0", "draft": True, "id": 1}
        client.json = Mock(side_effect=[None, [draft]])
        self.assertIs(client.release("v0.1.0"), draft)
        self.assertIn("page=1", client.json.call_args.args[1])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.commit = "a" * 40
        self.version = "0.1.0"
        for name in release.artifact_names(self.version):
            (self.root / name).write_bytes(name.encode())
        paths = [self.root / name for name in release.artifact_names(self.version)]
        (self.root / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in sorted(paths)))
        (self.root / "SHA256SUMS.asc").write_bytes(b"signature")
        (self.root / "repository-key.asc").write_bytes(b"key")
        self.client = MemoryGitHub(tag_commit=self.commit)
        self.addCleanup(self.tmp.cleanup)

    def test_https_redirect_drops_authorization_and_http_is_rejected(self):
        request = Request("https://api.invalid/repos/Grek-Off/tailtray/releases", headers={"Authorization": "Bearer secret"})
        redirected = release.SafeRedirects().redirect_request(request, None, 302, "Found", {}, "https://cdn.invalid/a")
        self.assertFalse(redirected.has_header("Authorization"))
        with self.assertRaises(ValueError):
            release.SafeRedirects().redirect_request(request, None, 302, "Found", {}, "http://cdn.invalid/a")

    def test_wrong_tag_commit_fails_before_upload(self):
        self.client.tag_commit_value = "b" * 40
        with patch.object(release, "source_matches_commit"):
            with self.assertRaisesRegex(ValueError, "does not match"):
                release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertFalse(any(call[0] == "POST" for call in self.client.calls))

    def test_partial_draft_resume_uploads_only_missing_assets(self):
        existing = self.root / release.artifact_names(self.version)[0]
        self.client.releases["v0.1.0"] = {"id": 1, "tag_name": "v0.1.0", "target_commitish": self.commit,
            "draft": True, "upload_url": "https://uploads.invalid/assets{?name}", "assets": [
                {"name": existing.name, "digest": "sha256:" + hashlib.sha256(existing.read_bytes()).hexdigest(),
                 "size": existing.stat().st_size, "state": "uploaded", "url": "asset:" + existing.name}]}
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        uploads = [call for call in self.client.calls if call[0] == "POST"]
        self.assertEqual(len(uploads), 9)

    def test_existing_release_uses_authoritative_tag_commit_not_target_commitish(self):
        self.client.releases["v0.1.0"] = {"id": 1, "tag_name": "v0.1.0", "target_commitish": "main",
            "draft": True, "upload_url": "https://uploads.invalid/assets{?name}", "assets": []}
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertTrue(any(call[0] == "POST" for call in self.client.calls))

    def test_accepted_but_failed_upload_is_refreshed_and_not_reuploaded(self):
        self.client.releases["v0.1.0"] = {"id": 1, "tag_name": "v0.1.0", "target_commitish": "main",
            "draft": True, "upload_url": "https://uploads.invalid/assets{?name}", "assets": []}
        self.client.fail_after_upload = True
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        uploads = [call for call in self.client.calls if call[0] == "POST"]
        self.assertEqual(len(uploads), 10)

    def test_published_immutable_resume_only_verifies_assets(self):
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.client.releases["v0.1.0"]["draft"] = False
        self.client.releases["v0.1.0"]["immutable"] = True
        before = len(self.client.calls)
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertEqual(len([call for call in self.client.calls[before:] if call[0] == "POST"]), 0)

    def test_plus_in_asset_name_is_percent_encoded_and_decodes_as_plus(self):
        with patch.object(release, "source_matches_commit"), patch.object(release, "verify_signature"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        names = [parse_qs(urlsplit(call[1]).query)["name"][0]
                 for call in self.client.calls if call[0] == "POST" and "name=" in call[1]]
        self.assertIn("tailtray_0.1.0-1+debian13_all.deb", names)

    def test_absolute_asset_url_must_be_github_api_or_upload_endpoint(self):
        client = release.GitHub("Grek-Off/tailtray", "synthetic-token")
        with self.assertRaisesRegex(ValueError, "trusted"):
            client.request("GET", "https://evil.invalid/repos/Grek-Off/tailtray/releases/assets/1")
        with self.assertRaisesRegex(ValueError, "outside"):
            client.request("POST", "https://uploads.github.com/repos/other/repo/releases/1/assets", data=b"x")

    def test_api_constructor_is_fixed_and_request_uses_current_api_version(self):
        for api in ("https://evil.example", "https://api.github.com:443", "https://user@api.github.com",
                    "https://api.github.com/repos/../other"):
            with self.assertRaises(ValueError):
                release.GitHub("Grek-Off/tailtray", "synthetic-token", api)
        client = release.GitHub("Grek-Off/tailtray", "synthetic-token")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"{}"
        client.opener.open = Mock(return_value=response)
        client.request("GET", "/releases/1")
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-github-api-version"), "2026-03-10")

    def test_current_package_assets_must_match_signed_manifest_mapping(self):
        manifest = {"files": {}}
        assets = []
        for name in release.artifact_names(self.version):
            relative = release._manifest_path_for_artifact(self.version, name)
            manifest["files"][relative] = {"size": 1, "sha256": "a" * 64}
            assets.append({"name": name, "state": "uploaded", "size": 1, "digest": "sha256:" + "a" * 64})
        release_data = {"assets": assets}
        release.verify_package_assets_against_manifest(release_data, manifest, self.version)
        expected_sums = "".join("a" * 64 + "  " + name + "\n"
                                 for name in sorted(release.artifact_names(self.version))).encode()
        self.assertEqual(release.package_checksums_from_manifest(manifest, self.version), expected_sums)
        assets[0]["digest"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ValueError, "signed repository manifest"):
            release.verify_package_assets_against_manifest(release_data, manifest, self.version)

    def test_malicious_manifest_path_and_integrity_are_rejected(self):
        public = self.root / "public"
        public.mkdir()
        (public / "manifest.json").write_text(json.dumps({"schema": 1, "files": {"../escape": {"size": 1, "sha256": "0" * 64}}, "versions": [self.version]}))
        (public / "manifest.json.asc").write_bytes(b"signature")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            release.load_manifest(public)
        (public / "manifest.json").write_text(json.dumps({"schema": 1, "files": {"safe": {"size": 3, "sha256": "0" * 64}}, "versions": [self.version]}))
        (public / "safe").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "checksum"):
            release.load_manifest(public)

    def test_repeated_pagination_is_bounded(self):
        page = json.dumps([{"tag_name": "v0.1.0", "draft": False, "assets": []}] * 100).encode()
        self.client.request = Mock(return_value=page)
        with self.assertRaisesRegex(ValueError, "repeated"):
            release.fetch_previous(self.client, self.root / "previous", "A" * 40)

    def test_archive_member_limit_is_enforced_before_extracting_all_members(self):
        output = io.BytesIO()
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for index in range(release.MAX_REPOSITORY_FILES + 4):
                    info = tarfile.TarInfo(f"file-{index}")
                    archive.addfile(info)
        self.client.archive_bytes = output.getvalue()
        with self.assertRaisesRegex(ValueError, "archive is invalid"):
            release.fetch_previous(self.client, self.root / "previous", "A" * 40)

    def test_prerelease_is_not_selected_as_previous_history(self):
        self.client.release_page = [{"tag_name": "v9.9.9", "draft": False, "prerelease": True,
                                     "assets": [{"name": "tailtray-repository-9.9.9.tar.gz", "url": "archive",
                                                 "state": "uploaded", "size": 1, "digest": "sha256:" + "0" * 64}]}]
        self.assertFalse(release.fetch_previous(self.client, self.root / "previous", "A" * 40))

    def test_repository_archive_is_deterministic_and_rejects_tag_mismatch(self):
        public = self.root / "public"
        payload = public / "downloads/source/tailtray-0.1.0.tar.gz"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"source")
        manifest = {"schema": 1, "versions": [self.version], "files": {
            payload.relative_to(public).as_posix(): {"size": 6, "sha256": hashlib.sha256(b"source").hexdigest()}}}
        (public / "manifest.json").write_text(json.dumps(manifest))
        (public / "manifest.json.asc").write_bytes(b"signature")
        (public / "repository-key.asc").write_bytes(b"key")
        first, second = release.repository_archive(public), release.repository_archive(public)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "tagged source"):
            release.publish_repository(self.client, public, "v0.1.1")

    def test_repository_finalizer_rejects_package_digest_before_history_upload(self):
        public = self.root / "public"
        manifest_files = {}
        for name in release.artifact_names(self.version):
            relative = release._manifest_path_for_artifact(self.version, name)
            path = public / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            manifest_files[relative] = {"size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
        manifest = {"schema": 1, "versions": [self.version], "files": manifest_files}
        (public / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        (public / "manifest.json.asc").write_bytes(b"manifest-signature")
        (public / "repository-key.asc").write_bytes(b"key")
        (public / "SHA256SUMS").write_text("".join(f"{item['sha256']}  {name}\n"
                                                     for name, item in sorted(manifest_files.items())))
        package_assets = [{"name": name, "state": "uploaded", "size": 1,
                           "digest": "sha256:" + ("b" * 64 if index == 0 else hashlib.sha256(b"x").hexdigest())}
                          for index, name in enumerate(release.artifact_names(self.version))]
        package_sums = release.package_checksums_from_manifest(manifest, self.version)
        metadata = [
            {"name": "SHA256SUMS", "state": "uploaded", "size": len(package_sums),
             "digest": "sha256:" + hashlib.sha256(package_sums).hexdigest()},
            {"name": "SHA256SUMS.asc", "state": "uploaded", "size": 1, "digest": "sha256:" + "c" * 64},
            {"name": "repository-key.asc", "state": "uploaded", "size": 3, "digest": "sha256:" + hashlib.sha256(b"key").hexdigest()},
        ]
        self.client.releases["v0.1.0"] = {"id": 1, "tag_name": "v0.1.0", "draft": True,
            "upload_url": "https://uploads.invalid/assets{?name}", "assets": package_assets + metadata}
        with patch.object(release, "verify_signature"):
            with self.assertRaisesRegex(ValueError, "signed repository manifest"):
                release.publish_repository(self.client, public, "v0.1.0")
        self.assertFalse(any(call[0] == "POST" for call in self.client.calls))

    def test_repository_finalizer_revalidates_separate_dist_directory_before_finalize(self):
        names = release.artifact_names(self.version) + list(release.EXPECTED_ASSET_NAMES) + [
            "tailtray-repository-0.1.0.tar.gz"]
        assets = [{"name": name, "state": "uploaded", "size": 1,
                   "digest": "sha256:" + "a" * 64} for name in names]
        self.client.releases["v0.1.0"] = {"id": 1, "tag_name": "v0.1.0", "draft": False,
            "immutable": True, "assets": assets}
        public = self.root / "public"
        public.mkdir()
        (public / "repository-key.asc").write_bytes(b"key")
        manifest = {"versions": [self.version], "files": {
            "downloads/source/tailtray-0.1.0.tar.gz": {"size": 1, "sha256": "a" * 64}}}
        dist_paths = [self.root / "dist-validated"]
        with patch.dict("os.environ", {"GITHUB_SHA": self.commit}), \
                patch.object(release, "load_manifest", return_value=(manifest, [])), \
                patch.object(release, "verify_signature"), \
                patch.object(release, "verify_public_checksums"), \
                patch.object(release, "repository_archive", return_value=b"archive"), \
                patch.object(release, "validate_release_inputs", return_value=dist_paths) as validate, \
                patch.object(release, "verify_release_assets") as verify_assets, \
                patch.object(release, "verify_package_assets_against_manifest"), \
                patch.object(release, "package_checksums_from_manifest", return_value=b"sums"), \
                patch.object(release, "asset_matches", return_value=True), \
                patch.object(release, "asset_bytes_match", return_value=True):
            release.publish_repository(self.client, self.root / "public", "v0.1.0", self.root / "dist")
        validate.assert_called_once_with(self.client, self.root / "dist", "v0.1.0", self.commit)
        verify_assets.assert_called_once_with(self.client, self.client.releases["v0.1.0"], dist_paths)


if __name__ == "__main__":
    unittest.main()
