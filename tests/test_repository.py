import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


PROJECT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_DIR / "scripts" / "build-repository.py"
SPEC = importlib.util.spec_from_file_location("build_repository", SCRIPT)
repository = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(repository)


class RepositoryTests(unittest.TestCase):
    def make_artifacts(self, names):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        for name in names:
            (directory / name).write_bytes(name.encode())
        return directory

    def matrix(self, version="1.2.3"):
        return [
            f"tailtray-{version}.tar.gz",
            f"tailtray-{version}-1.fc43.noarch.rpm",
            f"tailtray-{version}-1.fc43.src.rpm",
            f"tailtray-{version}-1.fc44.noarch.rpm",
            f"tailtray-{version}-1.fc44.src.rpm",
            f"tailtray_{version}-1+debian13_all.deb",
            f"tailtray_{version}-1+ubuntu26.04_all.deb",
        ]

    def test_cli_rejects_partial_and_unexpected_matrices(self):
        for names in (self.matrix()[:-1], self.matrix() + ["tailtray-1.2.3-2.fc44.noarch.rpm"]):
            artifacts = self.make_artifacts(names)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--artifacts", str(artifacts), "--output", str(artifacts / "out"),
                 "--public-key", str(artifacts / "key"), "--fingerprint", "0" * 40],
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exact 7-file matrix", result.stderr)

    def test_safe_relative_rejects_traversal_absolute_and_windows_paths(self):
        for value in ("../escape", "/absolute", "a/../../escape", r"a\b", "a//b"):
            with self.subTest(value=value):
                with self.assertRaises(repository.RepositoryError):
                    repository.safe_relative(value)

    def test_previous_checksum_and_conflicting_immutable_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            file_path = root / "downloads" / "source" / "old.tar.gz"
            file_path.parent.mkdir(parents=True)
            file_path.write_bytes(b"old")
            entry = {"path": "downloads/source/old.tar.gz", "sha256": repository.sha256(file_path), "size": 3}
            (root / "manifest.json").write_text(json.dumps({
                "schema": 1,
                "files": {entry["path"]: {"sha256": entry["sha256"], "size": entry["size"]}},
                "versions": ["1.0.0"],
            }))
            (root / "manifest.json.asc").write_bytes(b"signature")
            status = mock.Mock(returncode=0, stdout="[GNUPG:] VALIDSIG " + "A" * 40 + "\n", stderr="")
            with mock.patch.object(repository.subprocess, "run", return_value=status):
                manifest = repository.load_previous(root, "A" * 40)
            file_path.write_bytes(b"changed")
            with mock.patch.object(repository.subprocess, "run", return_value=status):
                with self.assertRaises(repository.RepositoryError):
                    repository.load_previous(root, "A" * 40)
            with tempfile.TemporaryDirectory() as destination:
                destination_root = Path(destination)
                with self.assertRaises(repository.RepositoryError):
                    repository.merge_file(file_path, "downloads/source/old.tar.gz", destination_root, manifest)

    def test_previous_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(b"data")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(repository.RepositoryError):
                repository.safe_previous_file(root, repository.safe_relative("link"))

    def test_generated_metadata_is_not_an_immutable_manifest_path(self):
        self.assertTrue(repository.immutable_manifest_path(repository.safe_relative("downloads/source/tailtray-1.0.0.tar.gz")))
        self.assertTrue(repository.immutable_manifest_path(repository.safe_relative("rpm/fedora/44/Packages/tailtray-1.0.0-1.fc44.noarch.rpm")))
        self.assertFalse(repository.immutable_manifest_path(repository.safe_relative("rpm/fedora/44/repodata/repomd.xml")))
        self.assertFalse(repository.immutable_manifest_path(repository.safe_relative("apt/dists/trixie/Release")))

    def test_manifest_schema_is_files_dict_and_date_honors_source_date_epoch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository.write_manifest(root, {
                "downloads/source/tailtray-1.0.0.tar.gz": {
                    "sha256": "a" * 64, "size": 3,
                },
            }, {"1.0.0"})
            data = json.loads((root / "manifest.json").read_text())
            self.assertEqual(data["registry_layout"], "flat-v1")
            self.assertEqual(data["files"], {
                "downloads/source/tailtray-1.0.0.tar.gz": {"sha256": "a" * 64, "size": 3},
            })
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "0"}):
            self.assertEqual(repository.metadata_date(), "Thu, 01 Jan 1970 00:00:00 GMT")

    def test_rpm_and_deb_metadata_targets_are_checked(self):
        rpm = Path("tailtray-1.2.3-1.fc44.noarch.rpm")
        deb = Path("tailtray_1.2.3-1+ubuntu26.04_all.deb")
        with mock.patch.object(repository, "rpm_fields", return_value={
            "name": "tailtray", "version": "1.2.3", "release": "1.fc44", "arch": "noarch", "sourcepackage": "(none)",
        }), mock.patch.object(repository, "run_tool") as tool:
            repository.verify_rpm(rpm, "1.2.3", "fc44", False)
            self.assertEqual(tool.call_args.args[0][:2], ["rpmkeys", "--checksig"])
        with mock.patch.object(repository, "deb_fields", return_value={
            "name": "tailtray", "version": "1.2.3-1+ubuntu26.04", "arch": "all",
        }):
            repository.verify_deb(deb, "1.2.3", "1+ubuntu26.04")
        with mock.patch.object(repository, "rpm_fields", return_value={
            "name": "tailtray", "version": "1.2.3", "release": "1.fc43", "arch": "noarch", "sourcepackage": "(none)",
        }):
            with self.assertRaises(repository.RepositoryError):
                repository.verify_rpm(rpm, "1.2.3", "fc44", False)
        with mock.patch.object(repository, "rpm_fields", return_value={
            "name": "tailtray", "version": "1.2.3", "release": "1.fc44", "arch": "noarch", "sourcepackage": "1",
        }), mock.patch.object(repository, "run_tool"):
            repository.verify_rpm(Path("tailtray-1.2.3-1.fc44.src.rpm"), "1.2.3", "fc44", True)

    def test_labelled_dpkg_metadata_is_parsed(self):
        output = "Package: tailtray\nVersion: 1.2.3-1+debian13\nArchitecture: all\n"
        with mock.patch.object(repository, "run_tool", return_value=mock.Mock(stdout=output)):
            self.assertEqual(repository.deb_fields(Path("tailtray.deb")), {
                "name": "tailtray", "version": "1.2.3-1+debian13", "arch": "all",
            })

    def test_apt_metadata_preserves_dependencies_description_and_all_pool_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pool = root / "apt/pool/main/t/tailtray"
            pool.mkdir(parents=True)
            old = pool / "tailtray_0.9.0-1+debian13_all.deb"
            new = pool / "tailtray_1.0.0-1+debian13_all.deb"
            ubuntu = pool / "tailtray_1.0.0-1+ubuntu26.04_all.deb"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            ubuntu.write_bytes(b"ubuntu")
            controls = {
                old: "Package: tailtray\nVersion: 0.9.0-1+debian13\nArchitecture: all\nDepends: tailscale (>= 1.56)\nDescription: old\n old multiline\n",
                new: "Package: tailtray\nVersion: 1.0.0-1+debian13\nArchitecture: all\nDepends: tailscale (>= 1.56)\nRecommends: gnome-shell-extension-appindicator\nDescription: new\n new multiline\n",
                ubuntu: "Package: tailtray\nVersion: 1.0.0-1+ubuntu26.04\nArchitecture: all\nDescription: ubuntu\n ubuntu multiline\n",
            }
            with mock.patch.object(repository, "package_control_for_apt", side_effect=controls.get):
                repository.build_apt_metadata(root, "Thu, 01 Jan 1970 00:00:00 UTC")
            packages = (root / "apt/dists/trixie/main/binary-amd64/Packages").read_text()
            self.assertIn("Version: 0.9.0-1+debian13", packages)
            self.assertIn("Version: 1.0.0-1+debian13", packages)
            self.assertIn("Depends: tailscale (>= 1.56)", packages)
            self.assertIn("Recommends: gnome-shell-extension-appindicator", packages)
            self.assertIn(" new multiline", packages)
            release = (root / "apt/dists/trixie/Release").read_text()
            self.assertNotIn("\n\n", release)
            self.assertIn("Date: Thu, 01 Jan 1970 00:00:00 UTC", release)

    def test_sign_defaults_to_ascii_armored_and_release_gpg_can_be_binary(self):
        with mock.patch.object(repository, "run_tool") as run:
            repository.sign(Path("/tmp"), "A" * 40, "input", "output")
            self.assertIn("--armor", run.call_args.args[0])
            run.reset_mock()
            repository.sign(Path("/tmp"), "A" * 40, "input", "output", armor=False)
            self.assertNotIn("--armor", run.call_args.args[0])

    def test_primary_fingerprint_ignores_subkey_fingerprint(self):
        output = "\n".join([
            "pub:-:rsa4096:1:KEY:::::u:::",
            "fpr:::::::::" + "A" * 40 + ":",
            "uid:::::::::TailTray:",
            "sub:-:rsa4096:1:SUB::::::",
            "fpr:::::::::" + "B" * 40 + ":",
        ])
        with mock.patch.object(repository, "run_tool", return_value=mock.Mock(stdout=output)):
            self.assertEqual(repository.fingerprint_from_public_key(Path(__file__)), "A" * 40)


if __name__ == "__main__":
    unittest.main()
