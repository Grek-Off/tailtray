from pathlib import Path
import os
import re
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
SPEC = PROJECT_DIR / "packaging" / "tailtray.spec"
DEBIAN = PROJECT_DIR / "packaging" / "debian"
BUILD_SCRIPT = PROJECT_DIR / "scripts" / "build-packages.sh"


class PackagingTests(unittest.TestCase):
    def _host_target(self):
        os_release = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                os_release[key] = value.strip('"')
        return {
            ("fedora", "43"): "fedora43",
            ("fedora", "44"): "fedora44",
            ("debian", "13"): "debian13",
            ("ubuntu", "26.04"): "ubuntu26.04",
        }.get((os_release.get("ID"), os_release.get("VERSION_ID")))

    def _dist_snapshot(self):
        dist = PROJECT_DIR / "dist"
        return {path.name for path in dist.iterdir()} if dist.exists() else set()

    def test_all_package_versions_match_project(self):
        version = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text())["project"]["version"]
        self.assertEqual(runpy.run_path(str(PROJECT_DIR / "src/tailtray/__init__.py"))["__version__"], version)
        rpm_version = re.search(r"^Version:\s+(\S+)", SPEC.read_text(), re.MULTILINE).group(1)
        self.assertEqual(rpm_version, version)
        self.assertTrue((DEBIAN / "changelog").read_text().startswith(f"tailtray ({version}-"))
        self.assertIn(f'<release version="{version}"',
                      (PROJECT_DIR / "data/io.github.tailtray.metainfo.xml").read_text())

    def test_mismatched_release_tag_fails_before_creating_archive(self):
        result = subprocess.run(["bash", str(BUILD_SCRIPT), "source"], capture_output=True, text=True,
                                env=dict(os.environ, CI_COMMIT_TAG="v99999.0.0"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release tag must match", result.stderr)

    def test_target_boundary_rejects_unknown_target_before_dist_creation(self):
        before = self._dist_snapshot()
        result = subprocess.run(
            ["bash", str(BUILD_SCRIPT), "rpm"],
            capture_output=True,
            text=True,
            env=dict(os.environ, TARGET_DISTRO="fedora42"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported TARGET_DISTRO", result.stderr)
        self.assertEqual(self._dist_snapshot(), before)

    def test_target_boundary_rejects_fake_cross_build_before_dist_creation(self):
        host_target = self._host_target()
        if host_target not in {"fedora43", "fedora44"}:
            self.skipTest("host is not a supported Fedora packaging host")
        other_target = "fedora43" if host_target == "fedora44" else "fedora44"
        before = self._dist_snapshot()
        result = subprocess.run(
            ["bash", str(BUILD_SCRIPT), "rpm"],
            capture_output=True,
            text=True,
            env=dict(os.environ, TARGET_DISTRO=other_target),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cross-builds are unsupported", result.stderr)
        self.assertEqual(self._dist_snapshot(), before)

    def test_fake_rpmbuild_receives_target_macro_and_preserves_package_version(self):
        host_target = self._host_target()
        if host_target not in {"fedora43", "fedora44"}:
            self.skipTest("host is not a supported Fedora packaging host")
        version = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text())["project"]["version"]
        with tempfile.TemporaryDirectory() as temp_dir:
            tool_dir = Path(temp_dir)
            # Build a disposable repository: never overwrite or remove real
            # dist files, and work when the checkout is mounted read-only.
            fixture = tool_dir / "project"
            (fixture / "scripts").mkdir(parents=True)
            shutil.copy2(BUILD_SCRIPT, fixture / "scripts/build-packages.sh")
            shutil.copy2(PROJECT_DIR / "pyproject.toml", fixture)
            shutil.copytree(PROJECT_DIR / "packaging", fixture / "packaging")
            (fixture / "data").mkdir()
            shutil.copy2(PROJECT_DIR / "data/io.github.tailtray.metainfo.xml", fixture / "data")
            for arguments in (
                ["init", "-q"], ["add", "."],
                ["-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "fixture"],
            ):
                subprocess.run(["git", "-C", str(fixture), *arguments], check=True, capture_output=True)
            args_file = tool_dir / "rpmbuild.args"
            fake_rpmbuild = tool_dir / "rpmbuild"
            fake_rpmbuild.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "printf '%s\\n' \"$@\" > \"$FAKE_RPM_ARGS\"\n"
                "top=\n"
                "for ((i=1; i<=$#; i++)); do\n"
                "  if [[ ${!i} == --define ]]; then\n"
                "    ((i++)); value=${!i}; case $value in _topdir\\ *) top=${value#_topdir };; esac\n"
                "  fi\n"
                "done\n"
                "mkdir -p \"$top/RPMS/noarch\" \"$top/SRPMS\"\n"
                "touch \"$top/RPMS/noarch/tailtray-${FAKE_PACKAGE_VERSION}-1.fc${TARGET_DISTRO#fedora}.noarch.rpm\"\n"
                "touch \"$top/SRPMS/tailtray-${FAKE_PACKAGE_VERSION}-1.fc${TARGET_DISTRO#fedora}.src.rpm\"\n",
                encoding="utf-8",
            )
            fake_rpmbuild.chmod(0o755)
            result = subprocess.run(
                ["bash", str(fixture / "scripts/build-packages.sh"), "rpm"],
                capture_output=True,
                text=True,
                env=dict(
                    os.environ,
                    PATH=f"{tool_dir}:{os.environ['PATH']}",
                    TARGET_DISTRO=host_target,
                    FAKE_RPM_ARGS=str(args_file),
                    FAKE_PACKAGE_VERSION=version,
                ),
            )
            args = args_file.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            fedora_release = f"fc{host_target.removeprefix('fedora')}"
            self.assertIn(f"dist .{fedora_release}", args)
            names = {path.name for path in (fixture / "dist").iterdir()}
            self.assertIn(f"tailtray-{version}-1.{fedora_release}.noarch.rpm", names)
            self.assertIn(f"tailtray-{version}-1.{fedora_release}.src.rpm", names)

    def test_source_archive_is_reproducible_and_contains_only_committed_sources(self):
        version = tomllib.loads((PROJECT_DIR / "pyproject.toml").read_text())["project"]["version"]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            (fixture / "scripts").mkdir()
            shutil.copy2(BUILD_SCRIPT, fixture / "scripts/build-packages.sh")
            shutil.copy2(PROJECT_DIR / "pyproject.toml", fixture)
            (fixture / "LICENSE").write_text("synthetic license")
            for args in (["init", "-q"], ["add", "."],
                         ["-c", "user.name=Fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", "-C", str(fixture), *args], capture_output=True, check=True, timeout=10)
            (fixture / "untracked-local-data").write_text("must never be archived")
            archive = fixture / "dist" / f"tailtray-{version}.tar.gz"
            copies = []
            for _ in range(2):
                subprocess.run(["bash", str(fixture / "scripts/build-packages.sh"), "source"],
                               env=dict(os.environ, CI_COMMIT_TAG=f"v{version}"),
                               capture_output=True, check=True, timeout=10)
                copies.append(archive.read_bytes())
            self.assertEqual(copies[0], copies[1])
            self.assertEqual(copies[0][4:8], b"\x00" * 4, "gzip timestamp must not change release hashes")
            with tarfile.open(archive) as source:
                names = source.getnames()
                self.assertIn(f"tailtray-{version}/LICENSE", names)
                self.assertTrue(all(name == f"tailtray-{version}" or name.startswith(f"tailtray-{version}/")
                                    for name in names))
                self.assertFalse(any("untracked-local-data" in name or "/.git/" in name for name in names))

    def test_rpm_is_noarch_and_installs_the_complete_application_tree(self):
        spec = SPEC.read_text(encoding="utf-8")
        for expected in (
            "BuildArch:      noarch",
            "Requires:       tailscale >= 1.56",
            "Requires:       gobject-introspection",
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


if __name__ == "__main__":
    unittest.main()
