"""Exercise publishing with in-memory GitLab; no credentials/network."""
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, Request, build_opener
from urllib.response import addinfourl

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
        nested_endpoint = self.client.package_path(
            "tailtray-repository", "v0.1.0", "downloads/source/tailtray-0.1.0.tar.gz")
        self.assertIn("/downloads/source/tailtray-0.1.0.tar.gz", nested_endpoint)
        self.assertNotIn("downloads%2Fsource", nested_endpoint)
        self.assertIn(("PUT", nested_endpoint), self.client.writes)
        (public / "index.html").write_text("regenerated index, not an immutable release payload")
        release.publish_repository(self.client, public, "v0.1.0")
        self.assertEqual(len(self.client.writes), 3)
        with self.assertRaisesRegex(ValueError, "tagged source"):
            release.publish_repository(self.client, public, "v0.1.1")

    def test_repository_conflict_never_overwrites_payload_or_completes_version(self):
        public = self.root / "public"
        source = public / "downloads/source/tailtray-0.1.0.tar.gz"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        other = public / "downloads/source/tailtray-0.1.0-1.fc43.src.rpm"
        other.write_bytes(b"other")
        manifest = {"schema": 1, "versions": ["0.1.0"], "files": {
            source.relative_to(public).as_posix(): {"sha256": hashlib.sha256(b"source").hexdigest(), "size": 6},
            other.relative_to(public).as_posix(): {"sha256": hashlib.sha256(b"other").hexdigest(), "size": 5},
        }}
        (public / "manifest.json").write_text(json.dumps(manifest))
        (public / "manifest.json.asc").write_bytes(b"signature")
        conflict = self.client.package_path("tailtray-repository", "v0.1.0", other.relative_to(public).as_posix())
        self.client.storage[conflict] = b"different"
        with self.assertRaisesRegex(ValueError, "conflict"):
            release.publish_repository(self.client, public, "v0.1.0")
        # Generic uploads are not a transaction: harmless new payloads may
        # precede a conflict. The invariant is no overwrite/completion marker.
        self.assertEqual(self.client.storage[conflict], b"different")
        self.assertNotIn(("PUT", conflict), self.client.writes)
        self.assertFalse(any(path.endswith("/manifest.json.asc") for _, path in self.client.writes))

    def test_flat_registry_roundtrip_preserves_signed_paths_and_distinct_basenames(self):
        public = self.root / "public"
        names = ("downloads/source/tailtray-0.1.0.tar.gz",
                 "apt/pool/main/t/tailtray/tailtray_0.1.0-1+debian13_all.deb",
                 "downloads/other/tailtray_0.1.0-1+debian13_all.deb")
        manifest = {"schema": 1, "registry_layout": "flat-v1", "versions": ["0.1.0"], "files": {}}
        for name in names:
            path = public / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            manifest["files"][name] = {"sha256": hashlib.sha256(name.encode()).hexdigest(), "size": len(name)}
        signed_bytes = json.dumps(manifest).encode()
        (public / "manifest.json").write_bytes(signed_bytes)
        (public / "manifest.json.asc").write_bytes(b"signature")
        release.publish_repository(self.client, public, "v0.1.0")
        before = list(self.client.writes)
        release.publish_repository(self.client, public, "v0.1.0")
        self.assertEqual(before, self.client.writes)
        payloads = [path for _, path in before if "/payload-" in path]
        self.assertEqual(len(set(payloads)), 3)
        self.assertTrue(all(path.rsplit("/", 1)[1].startswith("payload-") and "+" not in path for path in payloads))
        self.assertEqual(self.client.storage[self.client.package_path(
            "tailtray-repository", "v0.1.0", "manifest.json")], signed_bytes)
        self.client.storage["/packages?package_name=tailtray-repository&package_type=generic&per_page=100&page=1"] = json.dumps(
            [{"name": "tailtray-repository", "version": "v0.1.0", "status": "default"}]).encode()
        with patch.object(release.subprocess, "run", return_value=Mock(
                returncode=0, stdout="[GNUPG:] VALIDSIG " + "A" * 40 + " 0 0 0")):
            release.fetch_previous(self.client, self.root / "previous", "A" * 40)
        for name in names:
            self.assertEqual((self.root / "previous" / name).read_bytes(), name.encode())

    def test_unknown_registry_layout_is_rejected_before_upload(self):
        public = self.root / "public"
        public.mkdir()
        (public / "manifest.json").write_text(json.dumps({"schema": 1, "files": {}, "registry_layout": "future"}))
        (public / "manifest.json.asc").write_bytes(b"signature")
        with self.assertRaisesRegex(ValueError, "registry layout"):
            release.publish_repository(self.client, public, "v0.1.0")
        self.assertEqual(self.client.writes, [])

    def test_http_error_context_identifies_path_without_credentials(self):
        client = release.GitLab("https://gitlab.invalid/api/v4", "123", "synthetic-not-a-secret")
        error = HTTPError(
            "https://gitlab.invalid/api/v4/projects/123/packages/generic/tailtray/v0.1.0/a.txt",
            400, "Bad Request", {}, None)
        self.addCleanup(error.close)
        with patch.object(client.opener, "open", side_effect=error), self.assertRaisesRegex(
                ValueError, r"GitLab GET request failed \(HTTP 400\).*a\.txt") as raised:
            client.request("GET", "/packages/generic/tailtray/v0.1.0/a.txt")
        self.assertNotIn("synthetic-not-a-secret", str(raised.exception))

    def test_real_urllib_preprocessing_keeps_binary_uploads_out_of_form_parser(self):
        requests = []

        class CaptureHTTPS(HTTPSHandler):
            def https_open(self, request):
                # The real opener's https_request preprocessing still runs;
                # only the socket transport is replaced with a small response.
                requests.append(request)
                response = addinfourl(BytesIO(b"{}"), {}, request.full_url, 200)
                response.msg = "OK"
                return response

        client = release.GitLab("https://gitlab.invalid/api/v4", "123", "synthetic-not-a-secret")
        client.opener = build_opener(release.SafeRedirects(), CaptureHTTPS())
        binary = b"!<arch>\n\x00\xff\x80%invalid&file[]=binary"
        for method, data, payload, content_type in (
            ("PUT", binary, None, "application/octet-stream"),
            ("POST", None, {"name": "release"}, "application/json"),
            ("GET", None, None, None),
        ):
            with self.subTest(method=method):
                self.assertEqual(client.request(method, "/fixture", data=data, json_data=payload), b"{}")
                request = requests[-1]
                self.assertEqual(request.get_method(), method)
                self.assertEqual(request.get_header("Content-type"), content_type)
                self.assertEqual(request.data, json.dumps(payload).encode() if payload is not None else data)
                self.assertEqual(request.get_header("Job-token"), "synthetic-not-a-secret")

    def test_missing_only_swallows_404_not_forbidden_or_bad_request(self):
        client = release.GitLab("https://gitlab.invalid/api/v4", "123", "synthetic-not-a-secret")
        for status in (404, 403, 400):
            error = HTTPError("https://gitlab.invalid/api/v4/projects/123/releases/v0.1.0", status,
                              "fixture", {}, None)
            self.addCleanup(error.close)
            with patch.object(client.opener, "open", side_effect=error):
                if status == 404:
                    self.assertIsNone(client.request("GET", "/releases/v0.1.0", missing=True))
                else:
                    with self.assertRaisesRegex(ValueError, rf"HTTP {status}"):
                        client.request("GET", "/releases/v0.1.0", missing=True)

    def test_download_limit_and_network_failure_are_fail_closed(self):
        client = release.GitLab("https://gitlab.invalid/api/v4", "123", "synthetic-not-a-secret")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, amount):
                self.asserted_amount = amount
                return b"x" * amount

        response = Response()
        # Exercise the boundary with a tiny limit, not a 128 MiB allocation.
        with patch.object(release, "MAX_DOWNLOAD", 64), patch.object(client.opener, "open", return_value=response):
            with self.assertRaisesRegex(ValueError, "size limit"):
                client.request("GET", "/large")
        self.assertEqual(response.asserted_amount, 65)
        with patch.object(client.opener, "open", side_effect=URLError("offline")):
            with self.assertRaisesRegex(ValueError, "unreachable"):
                client.request("GET", "/offline")

    def test_failed_upload_can_be_retried_without_overwriting_completed_payload(self):
        original = self.client.request
        failed = False

        def fail_once(method, path, data=None, json_data=None, missing=False):
            nonlocal failed
            if method == "PUT" and not failed:
                failed = True
                raise ValueError("transient upload failure")
            return original(method, path, data=data, json_data=json_data, missing=missing)

        self.client.request = fail_once
        with self.assertRaisesRegex(ValueError, "transient"):
            release.publish(self.client, self.root, "v0.1.0", self.commit)
        writes_after_failure = list(self.client.writes)
        release.publish(self.client, self.root, "v0.1.0", self.commit)
        self.assertGreater(len(self.client.writes), len(writes_after_failure))
        self.assertEqual(len({path for method, path in self.client.writes if method == "PUT"}),
                         len([path for method, path in self.client.writes if method == "PUT"]))

    def test_history_paginates_and_uses_highest_completed_semver_marker(self):
        first_page = [{"name": "tailtray-repository", "version": f"v1.0.{n}", "status": "default"}
                      for n in range(100)]
        second_page = [{"name": "tailtray-repository", "version": "v1.10.0", "status": "default"}]
        manifest = {"schema": 1, "files": {"downloads/test": {
            "size": 7, "sha256": hashlib.sha256(b"fixture").hexdigest()}}}
        calls = []
        pages = {"1": first_page, "2": second_page}

        def request(method, path, **kwargs):
            # Two pages, two completion markers, a manifest and its payload.
            # A broken paginator/fixture must fail before accumulating memory.
            self.assertLess(len(calls), 6, "History fixture exceeded its six-request budget; pagination loop")
            calls.append(path)
            parsed = urlsplit(path)
            if parsed.path == "/packages":
                query = parse_qs(parsed.query)
                self.assertEqual(query.get("per_page"), ["100"])
                page = query.get("page")
                self.assertIsNotNone(page, "Pagination request has no page parameter")
                self.assertEqual(len(page), 1)
                self.assertIn(page[0], pages, "Unexpected page requested by history paginator")
                return json.dumps(pages[page[0]]).encode()
            if "v1.10.0" in path:
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
        package_queries = [parse_qs(urlsplit(call).query) for call in calls if urlsplit(call).path == "/packages"]
        self.assertEqual([query["page"] for query in package_queries], [["1"], ["2"]])
        self.assertEqual(len(calls), 6)
        self.assertTrue(any("v1.10.0/manifest.json.asc" in call for call in calls))
        # Prove the fixture's guard itself works if pagination stops advancing:
        # a hard assertion on request seven, not a timeout or another OOM.
        calls.clear()
        with patch.object(release, "urlencode", return_value="per_page=100&page=1"):
            with self.assertRaisesRegex(AssertionError, "six-request budget"):
                release.fetch_previous(self.client, self.root / "loop", "A" * 40)
        self.assertEqual(len(calls), 6)

    def test_history_rejects_invalid_signature_wrong_signer_path_tamper_and_hash_tamper(self):
        def run_history(manifest, signer="A" * 40, content=b"fixture"):
            client = self.client
            client.request = lambda method, path, **kwargs: (
                json.dumps([{"name": "tailtray-repository", "version": "v1.0.0", "status": "default"}]).encode()
                if path.startswith("/packages?") else
                (b"signature" if path.endswith("manifest.json.asc") else
                 json.dumps(manifest).encode() if path.endswith("manifest.json") else content))
            status = Mock(returncode=0 if signer else 1,
                          stdout=("[GNUPG:] VALIDSIG " + signer + " 0 0 0") if signer else "")
            with patch.object(release.subprocess, "run", return_value=status):
                with self.assertRaises(ValueError):
                    release.fetch_previous(client, self.root / "previous", "A" * 40)

        run_history({"schema": 1, "files": {"../escape": {"size": 7,
                    "sha256": hashlib.sha256(b"fixture").hexdigest()}}})
        run_history({"schema": 1, "files": {"downloads/test": {"size": 7,
                    "sha256": "0" * 64}}})
        run_history({"schema": 1, "files": {"downloads/test": {"size": 7,
                    "sha256": hashlib.sha256(b"fixture").hexdigest()}}}, signer="B" * 40)
        run_history({"schema": 1, "files": {"downloads/test": {"size": 7,
                    "sha256": hashlib.sha256(b"fixture").hexdigest()}}}, signer="")

    def test_cli_validates_current_action_tag_sha_and_environment(self):
        with patch.object(release.sys, "argv", ["gitlab_release.py", "release", "--directory", str(self.root)]), \
                patch.dict(os.environ, {}, clear=True):
            self.assertEqual(release.main(), 1)
        env = {"CI_API_V4_URL": "https://gitlab.invalid/api/v4", "CI_PROJECT_ID": "123",
               "CI_JOB_TOKEN": "synthetic-not-a-secret", "CI_COMMIT_TAG": "v0.1.0", "CI_COMMIT_SHA": self.commit}
        with patch.dict(os.environ, env, clear=True), patch.object(release.sys, "argv",
                ["gitlab_release.py", "release", "--directory", str(self.root)]), \
                patch.object(release, "GitLab", return_value=self.client), patch.object(release, "publish") as publish:
            self.assertEqual(release.main(), 0)
        publish.assert_called_once_with(self.client, self.root, "v0.1.0", self.commit)
