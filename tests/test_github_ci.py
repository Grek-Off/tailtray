"""Guard migration CI boundaries, artifact layout and reusable native checks."""
from pathlib import Path
import subprocess
import unittest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


@unittest.skipIf(yaml is None, "PyYAML development dependency not installed")
class GitHubCITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.text, Loader=yaml.BaseLoader)
        cls.jobs = cls.workflow["jobs"]

    def steps(self):
        return [step for job in self.jobs.values() for step in job["steps"]]

    def test_untrusted_changes_cannot_publish_or_use_release_secrets(self):
        self.assertEqual(set(self.workflow["on"]), {"push", "pull_request"})
        self.assertEqual(self.workflow["on"]["push"]["branches"], ["main"])
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for forbidden in ("pull_request_target", "secrets.", "publish-release.sh", "release-ci.sh"):
            self.assertNotIn(forbidden, self.text)
        for job in self.jobs.values():
            self.assertNotIn("permissions", job)
            self.assertNotIn("environment", job)
        tag = self.workflow["env"]["CI_COMMIT_TAG"]
        self.assertIn("github.ref_type == 'tag'", tag)
        self.assertIn("github.ref_name", tag)

    def test_actions_are_official_immutable_and_do_not_keep_git_credentials(self):
        for step in self.steps():
            if "uses" not in step:
                continue
            action, sha = step["uses"].split("@")
            self.assertIn(action, {"actions/checkout", "actions/upload-artifact", "actions/download-artifact"})
            self.assertRegex(sha, r"^[a-f0-9]{40}$")
            if action == "actions/checkout":
                self.assertEqual(step["with"]["persist-credentials"], "false")

    def test_git_is_installed_before_checkout_in_every_minimal_container(self):
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                steps = job["steps"]
                checkout = next(index for index, step in enumerate(steps)
                                if step.get("uses", "").startswith("actions/checkout@"))
                before = "\n".join(step.get("run", "") for step in steps[:checkout])
                self.assertRegex(before, r"install[^\n]*\sgit(?:\s|$)")

    def test_shell_bounds_and_failure_evidence(self):
        self.assertEqual(self.workflow["defaults"]["run"]["shell"], "bash")
        self.assertEqual(self.workflow["concurrency"]["cancel-in-progress"], "true")
        for job in self.jobs.values():
            self.assertLessEqual(int(job["timeout-minutes"]), 30)
            self.assertRegex(job["container"]["options"], r"--memory=[234]g")
            uploads = [step for step in job["steps"]
                       if step.get("uses", "").startswith("actions/upload-artifact@")]
            self.assertTrue(uploads)
            self.assertTrue(all(step.get("if") == "always()" for step in uploads))
        for step in self.steps():
            if "run" in step:
                result = subprocess.run(["bash", "-n"], input=step["run"], text=True,
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_targets_and_shared_strict_checks_are_complete(self):
        expected = {"rpm": {"fedora43", "fedora44"}, "deb": {"debian13", "ubuntu26.04"},
                    "repository-rehearsal": {"fedora43", "fedora44", "debian13", "ubuntu26.04"}}
        for name, targets in expected.items():
            self.assertEqual({item["target"] for item in self.jobs[name]["strategy"]["matrix"]["include"]}, targets)
        verify = "\n".join(step.get("run", "") for step in self.jobs["verify"]["steps"])
        runtime = (ROOT / "scripts/test-installed-package.sh").read_text(encoding="utf-8")
        for commands in (verify, runtime):
            for required in ("TAILTRAY_TEST_STRICT=1", "xvfb-run", "timeout", "prlimit --as=2147483648"):
                self.assertIn(required, commands)
        for name in ("rpm", "deb"):
            self.assertTrue(any("bash scripts/test-installed-package.sh" in step.get("run", "")
                                for step in self.jobs[name]["steps"]))
        for path in ("tests/test_github_ci.py", "tests/test_github_credentials.py"):
            self.assertIn("--ignore=" + path, runtime)

    def test_package_artifacts_do_not_nest_dist_or_mix_runtime_reports(self):
        for name, suffix in (("rpm", "rpm"), ("deb", "deb")):
            upload = next(step for step in self.jobs[name]["steps"]
                          if step.get("with", {}).get("name") == "package-${{ matrix.target }}")
            self.assertEqual(upload["with"]["path"], "dist/*." + suffix)
            self.assertEqual(upload["with"]["if-no-files-found"], "error")
            self.assertTrue(any(step.get("with", {}).get("name") == "runtime-${{ matrix.target }}"
                                for step in self.jobs[name]["steps"]))
        downloads = [step["with"] for step in self.jobs["release-rehearsal"]["steps"]
                     if step.get("uses", "").startswith("actions/download-artifact@")]
        self.assertIn({"pattern": "package-*", "merge-multiple": "true", "path": "dist"}, downloads)
        self.assertEqual(self.jobs["release-rehearsal"]["needs"], ["source", "rpm", "deb"])
        self.assertEqual(self.jobs["repository-rehearsal"]["needs"], "release-rehearsal")

    def test_verified_build_and_consumer_scripts_remain_in_use(self):
        for command in ("scripts/build-packages.sh source", "scripts/build-packages.sh rpm",
                        "scripts/build-packages.sh deb", "scripts/smoke-package.sh rpm",
                        "scripts/smoke-package.sh deb", "tests/release_rehearsal.py",
                        "scripts/check-native-repository.sh"):
            self.assertIn(command, self.text)
            self.assertTrue((ROOT / command.split()[0]).is_file())
        self.assertIn("SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)", self.text)
