"""Deploy guard tests use imported scripts and mocked GitLab responses only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
RELEASE_SPEC = importlib.util.spec_from_file_location("gitlab_release", ROOT / "scripts/gitlab_release.py")
release = importlib.util.module_from_spec(RELEASE_SPEC)
assert RELEASE_SPEC.loader is not None
RELEASE_SPEC.loader.exec_module(release)
DEPLOY_SPEC = importlib.util.spec_from_file_location("check_repository_deploy", ROOT / "scripts/check-repository-deploy.py")
deploy = importlib.util.module_from_spec(DEPLOY_SPEC)
assert DEPLOY_SPEC.loader is not None
with patch.dict(sys.modules, {"gitlab_release": release}):
    DEPLOY_SPEC.loader.exec_module(deploy)


class DeployGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "public").mkdir()
        self.env = {"CI_API_V4_URL": "https://gitlab.invalid/api/v4", "CI_PROJECT_ID": "123",
                    "CI_JOB_TOKEN": "synthetic-not-a-secret"}

    def run_guard(self, versions, releases):
        (self.root / "public/manifest.json").write_text(json.dumps({"versions": versions}))

        class Client:
            def request(self, method, path):
                if len(self.paths) >= 2:
                    raise AssertionError("Deploy fixture exceeded its two-page request budget")
                self.paths.append(path)
                query = parse_qs(urlsplit(path).query)
                if query.get("page") == ["1"]:
                    return json.dumps(releases).encode()
                if query.get("page") == ["2"]:
                    return b"[]"
                raise AssertionError("Unexpected deploy history page")

            def __init__(self):
                self.paths = []

        client = Client()
        with patch.object(deploy, "GitLab", return_value=client), patch.dict(os.environ, self.env, clear=True), \
                patch.object(deploy, "Path", side_effect=lambda path: self.root / path):
            deploy.main()
        return client

    def test_manifest_containing_all_published_versions_is_safe(self):
        releases = [{"tag_name": "v1.0.0"}, {"tag_name": "v1.1.0"}, {"tag_name": "branch"}]
        client = self.run_guard(["1.0.0", "v1.1.0"], releases)
        self.assertEqual(client.paths, ["/releases?per_page=100&page=1"])

    def test_newer_release_missing_from_manifest_is_refused(self):
        releases = [{"tag_name": "v1.0.0"}, {"tag_name": "v1.2.0"}]
        with self.assertRaisesRegex(ValueError, "Newer release"):
            self.run_guard(["1.0.0", "1.1.0"], releases)

    def test_release_history_paginates_at_registry_boundary(self):
        first_page = [{"tag_name": f"v2.0.{index}"} for index in range(100)]
        client = self.run_guard([f"2.0.{index}" for index in range(100)], first_page)
        self.assertEqual(client.paths, ["/releases?per_page=100&page=1", "/releases?per_page=100&page=2"])


if __name__ == "__main__":
    unittest.main()
