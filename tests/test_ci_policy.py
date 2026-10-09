"""Test CI's safety policy with tiny isolated pytest subprocesses."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET


@unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest development tools not installed")
class CIPolicyTests(unittest.TestCase):
    def run_fixture(self, source, *, strict=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "test_policy_fixture.py").write_text(source)
            shutil.copy2(Path(__file__).with_name("conftest.py"), root / "conftest.py")
            env = {**os.environ, "TAILTRAY_TEST_STRICT": "1" if strict else "0",
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
            env.pop("TAILTRAY_TEST_INSTALLED", None)
            result = subprocess.run([
                sys.executable, "-m", "pytest", "-q", "-c", "/dev/null", "--rootdir", str(root),
                "--confcutdir", str(root), "-p", "pytest_timeout", "--timeout=0.2",
                "--junitxml", str(root / "report.xml"), str(root / "test_policy_fixture.py"),
            ], env=env, capture_output=True, text=True, timeout=10)
            return result, ET.parse(root / "report.xml").getroot()

    def test_strict_ci_skip_is_a_real_failure_in_exit_code_and_junit(self):
        result, report = self.run_fixture('import pytest\ndef test_missing_native_dependency():\n    pytest.skip("missing GTK")\n')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(report.findall(".//failure")), 1)
        self.assertIn("Unexpected CI skip", result.stdout)

    def test_developer_skip_remains_visible_without_a_fake_failure(self):
        result, report = self.run_fixture('import pytest\ndef test_optional():\n    pytest.skip("no desktop")\n', strict=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(report.findall(".//skipped")), 1)

    def test_hanging_fixture_is_terminated_and_reported(self):
        result, report = self.run_fixture('import time\ndef test_hang():\n    time.sleep(60)\n')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(report.findall(".//failure")), 1)
        self.assertIn("Timeout", result.stdout)
