"""Publication gates: trusted tags, isolated signing and real public consumers."""
from pathlib import Path
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github/workflows/release.yml").read_text()
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.jobs = cls.workflow["jobs"]

    def test_only_trusted_tags_can_reach_protected_signing(self):
        self.assertEqual(self.workflow["on"], {"push": {"tags": ["v*.*.*"]}})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(self.workflow["concurrency"], {"group": "tailtray-release", "cancel-in-progress": "false"})
        self.assertEqual(self.jobs["checks"]["uses"], "./.github/workflows/ci.yml")
        self.assertNotIn("secrets", self.jobs["checks"])
        self.assertEqual(self.jobs["prepare"]["needs"], "checks")
        self.assertEqual(self.jobs["prepare"]["environment"], "release")
        self.assertEqual(self.text.count("secrets.TAILTRAY_SIGNING_KEY"), 1)
        for name, job in self.jobs.items():
            if name in {"prepare", "finalize"}:
                self.assertEqual(job["permissions"], {"contents": "write"})
            elif name == "pages":
                self.assertEqual(job["permissions"], {"pages": "write", "id-token": "write"})
            else:
                self.assertNotIn("write", str(job.get("permissions", {})))

    def test_release_is_finalized_only_after_signed_and_anonymous_consumers(self):
        for name, previous in [("repository", "prepare"), ("pages", "repository"),
                               ("anonymous-consumer", "pages"), ("finalize", "anonymous-consumer")]:
            self.assertEqual(self.jobs[name]["needs"], previous)
        for name in ("repository", "anonymous-consumer"):
            self.assertEqual({x["target"] for x in self.jobs[name]["strategy"]["matrix"]["include"]},
                             {"fedora43", "fedora44", "debian13", "ubuntu26.04"})
            self.assertTrue(any("scripts/check-native-repository.sh" in s.get("run", "")
                                for s in self.jobs[name]["steps"]))
        self.assertEqual(self.jobs["anonymous-consumer"]["env"]["TAILTRAY_NATIVE_REPOSITORY_URL"],
                         "https://grek-off.github.io/tailtray")
        sign = (ROOT / "scripts/github-release-ci.sh").read_text()
        self.assertNotIn("github_release.py repository", sign)
        final = "\n".join(s.get("run", "") for s in self.jobs["finalize"]["steps"])
        self.assertIn("--release-directory dist", final)
        downloaded = {s.get("with", {}).get("name") for s in self.jobs["finalize"]["steps"]}
        self.assertTrue({"production-repository", "production-release"} <= downloaded)

    def test_actions_are_pinned_and_shells_parse(self):
        for job in self.jobs.values():
            for step in job.get("steps", []):
                if "uses" in step:
                    self.assertRegex(step["uses"], r"^actions/[a-z-]+@[a-f0-9]{40}$")
                    if step["uses"].startswith("actions/checkout@"):
                        self.assertEqual(step["with"]["persist-credentials"], "false")
                if "run" in step:
                    checked = subprocess.run(["bash", "-n"], input=step["run"], text=True,
                                             capture_output=True, timeout=5)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
