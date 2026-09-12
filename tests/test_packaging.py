from pathlib import Path
import os
import re
import subprocess
import tomllib
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SPEC = PROJECT_DIR / "packaging" / "tailtray.spec"
DEBIAN = PROJECT_DIR / "packaging" / "debian"
BUILD_SCRIPT = PROJECT_DIR / "scripts" / "build-packages.sh"


class PackagingTests(unittest.TestCase):
    def test_all_package_versions_match_project(self):
        version = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text())["project"]["version"]
        rpm_version = re.search(r"^Version:\s+(\S+)", SPEC.read_text(), re.MULTILINE).group(1)
        self.assertEqual(rpm_version, version)
        self.assertTrue((DEBIAN / "changelog").read_text().startswith(f"tailtray ({version}-"))

    def test_mismatched_release_tag_fails_before_creating_archive(self):
        result = subprocess.run(["bash", str(BUILD_SCRIPT), "source"], capture_output=True, text=True,
                                env=dict(os.environ, CI_COMMIT_TAG="v99999.0.0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release tag must match", result.stderr)

    def test_build_interface_and_reproducible_source_archive_are_declared(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("source|rpm|deb", script)
        self.assertIn('git -C "$project_dir" archive --format=tar --prefix="$package_name-$version/" HEAD', script)
        self.assertIn("gzip -n", script)
        self.assertIn('version=$(PROJECT_DIR="$project_dir" python3', script)

    def test_rpm_is_noarch_and_installs_the_complete_application_tree(self):
        spec = SPEC.read_text(encoding="utf-8")
        for expected in (
            "BuildArch:      noarch",
            "Requires:       tailscale >= 1.56",
            "%{_prefix}/lib/tailtray/",
            "%{_bindir}/tailtray",
            "%{_datadir}/applications/io.github.tailtray.desktop",
            "%{_metainfodir}/io.github.tailtray.metainfo.xml",
            "%{_datadir}/tailtray/icons/tailtray-hc-v2-*.png",
            "%license LICENSE",
            "%doc README.md",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, spec)
        self.assertNotIn("systemctl enable", spec)
        self.assertNotIn("tailscale set", spec)

    def test_debian_metadata_is_architecture_independent_and_passive(self):
        control = (DEBIAN / "control").read_text(encoding="utf-8")
        rules = (DEBIAN / "rules").read_text(encoding="utf-8")
        self.assertIn("Architecture: all", control)
        self.assertIn("python3-pyside6.qtwidgets", control)
        self.assertIn("python3-pyside6.qtdbus", control)
        self.assertIn("gir1.2-ayatanaappindicator3-0.1", control)
        for expected in (
            "usr/lib/tailtray/tailtray",
            "usr/bin/tailtray",
            "usr/share/applications/io.github.tailtray.desktop",
            "usr/share/metainfo/io.github.tailtray.metainfo.xml",
            "usr/share/tailtray/icons",
        ):
            self.assertIn(expected, rules)
        self.assertNotIn("systemctl", rules)
        self.assertNotIn("tailscale set", rules)

    def test_package_build_checks_do_not_require_native_desktop_dependencies(self):
        spec = SPEC.read_text(encoding="utf-8")
        rules = (DEBIAN / "rules").read_text(encoding="utf-8")
        self.assertIn("%{python3} -m unittest tests.test_packaging", spec)
        self.assertIn("python3 -m unittest tests.test_packaging", rules)
        self.assertNotIn("python3-pyside6", (DEBIAN / "control").read_text(encoding="utf-8").split("Package:")[0])


if __name__ == "__main__":
    unittest.main()
