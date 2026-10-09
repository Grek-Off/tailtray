from pathlib import Path
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch


PROJECT_DIR = Path(__file__).resolve().parents[1]
INSTALL_LIB = PROJECT_DIR / "scripts" / "install-lib.sh"


def run_helper(function: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "/usr/bin/bash",
            "-c",
            'source "$1"; shift; "$@"',
            "tailtray-install-test",
            str(INSTALL_LIB),
            function,
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


class InstallationTests(unittest.TestCase):
    def test_repository_key_pin_rejects_extra_primary_keys_and_missing_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool = root / "gpg"
            tool.write_text('#!/usr/bin/bash\nprintf "%s\\n" "$TAILTRAY_TEST_KEY_LISTING"\n')
            tool.chmod(0o755)
            primary = "pub::::::::::\nfpr:::::::::" + "A" * 40 + ":\n"
            for listing, valid in (
                (primary, True),
                (primary + "sub::::::::::\nfpr:::::::::" + "B" * 40 + ":\n", True),
                (primary + "pub::::::::::\nfpr:::::::::" + "B" * 40 + ":\n", False),
                (primary.replace("A" * 40, "B" * 40), False),
                ("pub::::::::::\n", False),
            ):
                with self.subTest(listing=listing), patch.dict(os.environ, {
                    "PATH": str(root) + os.pathsep + os.environ["PATH"], "TAILTRAY_TEST_KEY_LISTING": listing,
                }):
                    result = run_helper("tailtray_verify_repository_key", str(root / "key.asc"), "A" * 40)
                    self.assertEqual(result.returncode == 0, valid, result.stderr)

    def test_native_repository_checker_refuses_host_mutations_and_credential_urls(self):
        script = PROJECT_DIR / "scripts/check-native-repository.sh"
        for ci, url, message in (
            ("false", "https://example.invalid", "disposable CI"),
            ("true", "https://token@example.invalid", "credential-free HTTPS"),
            ("true", "http://example.invalid", "credential-free HTTPS"),
            ("true", "https://example.invalid/?token=synthetic", "credential-free HTTPS"),
        ):
            with self.subTest(ci=ci, url=url):
                result = subprocess.run(["bash", str(script)], env={**os.environ, "CI": ci,
                    "TARGET_DISTRO": "fedora44", "TAILTRAY_NATIVE_REPOSITORY_URL": url},
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)

    def test_process_matcher_accepts_fedora_python_versions_only_for_installed_launcher(self):
        accepted = (
            ("/usr/bin/python3", "/usr/bin/tailtray"),
            ("/usr/bin/python3.14", "/usr/bin/tailtray"),
            ("python3.13", "/usr/bin/tailtray"),
        )
        rejected = (
            ("/usr/bin/python3", str(PROJECT_DIR / "bin" / "tailtray")),
            ("/usr/bin/python", "/usr/bin/tailtray"),
            ("/usr/bin/bash", "/usr/bin/tailtray"),
        )

        for command in accepted:
            with self.subTest(command=command):
                self.assertEqual(run_helper("tailtray_process_matches", *command).returncode, 0)
        for command in rejected:
            with self.subTest(command=command):
                self.assertNotEqual(run_helper("tailtray_process_matches", *command).returncode, 0)

    def test_process_discovery_uses_nul_separated_argv_and_ignores_checkout_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            installed = proc_root / "101"
            development = proc_root / "202"
            installed.mkdir()
            development.mkdir()
            installed.joinpath("cmdline").write_bytes(b"/usr/bin/python3.14\0/usr/bin/tailtray\0")
            development.joinpath("cmdline").write_bytes(
                f"/usr/bin/python3\0{PROJECT_DIR / 'bin' / 'tailtray'}\0".encode()
            )

            completed = run_helper("tailtray_running_pids", str(installed.stat().st_uid), str(proc_root))

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.splitlines(), ["101"])

    def test_verifier_rejects_the_missing_status_notifier_that_caused_stale_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            installed = root / "installed"
            source.mkdir()
            installed.mkdir()
            source.joinpath("qt_app.py").write_text("safe = True\n", encoding="utf-8")
            source.joinpath("status_notifier.py").write_text("item_is_menu = True\n", encoding="utf-8")
            installed.joinpath("qt_app.py").write_text("safe = True\n", encoding="utf-8")

            missing = run_helper("tailtray_verify_python_tree", str(source), str(installed))
            installed.joinpath("status_notifier.py").write_text("item_is_menu = True\n", encoding="utf-8")
            complete = run_helper("tailtray_verify_python_tree", str(source), str(installed))

        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing installed module", missing.stderr)
        self.assertEqual(complete.returncode, 0, complete.stderr)


if __name__ == "__main__":
    unittest.main()
