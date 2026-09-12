"""Test release selection without contacting GitLab or reading credentials."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class ReleaseTests(unittest.TestCase):
    def run_release(self, files, tag="v0.1.0"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            for name in files:
                (root / "dist" / name).write_bytes(b"fixture")
            fake = root / "glab"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > arguments\n')
            fake.chmod(0o755)
            script = Path(__file__).resolve().parents[1] / "scripts/publish-release.sh"
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", CI_COMMIT_TAG=tag,
                       CI_COMMIT_SHA="a" * 40, CI_JOB_TOKEN="test-placeholder")
            result = subprocess.run(["sh", str(script)], cwd=root, env=env, capture_output=True, text=True)
            args = (root / "arguments").read_text().splitlines() if (root / "arguments").exists() else []
            sums = (root / "dist/SHA256SUMS").read_text() if (root / "dist/SHA256SUMS").exists() else ""
            return result, args, sums

    def test_publishes_only_matching_version_and_checksums_to_registry(self):
        files = ["tailtray-0.1.0.tar.gz", "tailtray-0.1.0-1.fc43.noarch.rpm", "tailtray_0.1.0-1_all.deb"]
        result, args, sums = self.run_release([*files, "unrelated.txt", "tailtray-0.0.9.tar.gz"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--use-package-registry", args)
        self.assertIn("--ref", args)
        self.assertNotIn("test-placeholder", " ".join(args))
        self.assertNotIn("0.0.9", sums)
        for name in files:
            self.assertIn("dist/" + name, args)
            self.assertIn(hashlib.sha256(b"fixture").hexdigest() + "  " + name, sums)

    def test_missing_package_never_invokes_gitlab(self):
        result, args, _sums = self.run_release(["tailtray-0.1.0.tar.gz"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(args, [])

    def test_invalid_tag_never_invokes_gitlab(self):
        for tag in ("main", "v1.x.2", "v1.2.3.4"):
            result, args, _sums = self.run_release([], tag)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(args, [])
