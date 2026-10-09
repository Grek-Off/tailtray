#!/usr/bin/env python3
"""Build a signed, static TailTray APT and RPM repository."""

from __future__ import annotations

import argparse
from email.utils import format_datetime
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import NoReturn


NAME = "tailtray"
RPM_TARGETS = {"43": "fc43", "44": "fc44"}
DEB_TARGETS = {"debian13": "trixie", "ubuntu26.04": "resolute"}
EXPECTED_FINGERPRINT = re.compile(r"^[0-9A-Fa-f]{40}$")


class RepositoryError(RuntimeError):
    pass


def fail(message: str) -> "NoReturn":
    raise RepositoryError(message)


def run_tool(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        fail(f"required tool unavailable: {args[0]}")
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        fail(f"tool failed: {args[0]}" + (f": {detail[-1]}" if detail else ""))
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular_file(path: Path, label: str) -> None:
    if not path.is_file() or path.is_symlink():
        fail(f"{label} is not a regular file")


def parse_version(artifacts: Path) -> tuple[str, dict[str, Path]]:
    if not artifacts.is_dir() or artifacts.is_symlink():
        fail("artifacts must be a directory")
    entries = list(artifacts.iterdir())
    if any(not entry.is_file() or entry.is_symlink() for entry in entries):
        fail("artifacts must contain regular files only")
    tarballs = [entry for entry in entries if entry.name.startswith(f"{NAME}-") and entry.name.endswith(".tar.gz")]
    if len(tarballs) != 1:
        fail("artifacts must contain exactly one source tarball")
    match = re.fullmatch(rf"{re.escape(NAME)}-([0-9][A-Za-z0-9.+~:-]*)\.tar\.gz", tarballs[0].name)
    if not match:
        fail("source tarball has an invalid version name")
    version = match.group(1)
    expected = {
        tarballs[0].name,
        *(f"{NAME}-{version}-1.{suffix}.noarch.rpm" for suffix in RPM_TARGETS.values()),
        *(f"{NAME}-{version}-1.{suffix}.src.rpm" for suffix in RPM_TARGETS.values()),
        *(f"{NAME}_{version}-1+{target}_all.deb" for target in DEB_TARGETS),
    }
    allowed_metadata = {"SHA256SUMS", "SHA256SUMS.asc", "repository-key.asc"}
    actual = {entry.name for entry in entries}
    if not expected <= actual or not (actual - expected) <= allowed_metadata:
        missing = ", ".join(sorted(expected - actual))
        extra = ", ".join(sorted((actual - expected) - allowed_metadata))
        details = []
        if missing:
            details.append(f"missing: {missing}")
        if extra:
            details.append(f"unexpected: {extra}")
        fail("artifacts must be the exact 7-file matrix (" + "; ".join(details) + ")")
    return version, {entry.name: entry for entry in entries}


def fingerprint_from_public_key(public_key: Path) -> str:
    require_regular_file(public_key, "public key")
    output = run_tool(["gpg", "--batch", "--with-colons", "--show-keys", str(public_key)]).stdout
    primary_fingerprint = None
    primary_seen = False
    for line in output.splitlines():
        fields = line.split(":")
        if not fields:
            continue
        if fields[0] == "pub":
            primary_seen = True
        elif fields[0] == "fpr" and primary_seen and primary_fingerprint is None and len(fields) > 9:
            primary_fingerprint = fields[9].upper()
    if primary_fingerprint is None:
        fail("public key has no primary fingerprint")
    return primary_fingerprint


def check_expected_fingerprint(public_key: Path, expected: str) -> str:
    normalized = expected.replace(" ", "").upper()
    if not EXPECTED_FINGERPRINT.fullmatch(normalized):
        fail("expected fingerprint must be a full 40-hex-digit fingerprint")
    actual = fingerprint_from_public_key(public_key)
    if actual != normalized:
        fail("public key fingerprint does not match expected fingerprint")
    return normalized


def rpm_fields(path: Path) -> dict[str, str]:
    result = run_tool([
        "rpm", "-qp", "--qf", "%{NAME}\\n%{VERSION}\\n%{RELEASE}\\n%{ARCH}\\n%{SOURCEPACKAGE}\\n", str(path),
    ])
    values = result.stdout.splitlines()
    if len(values) != 5:
        fail(f"rpm metadata is incomplete: {path.name}")
    return dict(zip(("name", "version", "release", "arch", "sourcepackage"), values))


def verify_rpm(path: Path, version: str, suffix: str, source: bool) -> None:
    fields = rpm_fields(path)
    expected_release = f"1.{suffix}"
    expected = {"name": NAME, "version": version, "release": expected_release, "arch": "noarch"}
    sourcepackage = fields.pop("sourcepackage", "")
    source_ok = sourcepackage == "1" if source else sourcepackage == "(none)"
    if fields != expected or not source_ok:
        fail(f"RPM metadata does not match {path.name}")
    run_tool(["rpmkeys", "--checksig", str(path)])


def deb_fields(path: Path) -> dict[str, str]:
    result = run_tool(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"])
    fields = {}
    for line in result.stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    if not all(key in fields for key in ("Package", "Version", "Architecture")):
        fail(f"DEB metadata is incomplete: {path.name}")
    return {"name": fields["Package"], "version": fields["Version"], "arch": fields["Architecture"]}


def verify_deb(path: Path, version: str, revision: str) -> None:
    fields = deb_fields(path)
    expected = {"name": NAME, "version": f"{version}-{revision}", "arch": "all"}
    if fields != expected:
        fail(f"DEB metadata does not match {path.name}")


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        fail("manifest contains an unsafe path")
    if any(part == "" for part in value.split("/")):
        fail("manifest contains an unsafe path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or str(path) != value:
        fail("manifest contains an unsafe path")
    return path


def safe_previous_file(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            fail(f"previous repository path is a symlink: {relative}")
    require_regular_file(current, f"previous repository file {relative}")
    return current


def immutable_manifest_path(relative: PurePosixPath) -> bool:
    value = relative.as_posix()
    return (
        value.startswith("downloads/source/")
        and (value.endswith(".tar.gz") or value.endswith(".src.rpm"))
    ) or (
        re.fullmatch(r"rpm/fedora/(43|44)/Packages/[^/]+\.noarch\.rpm", value) is not None
    ) or (
        re.fullmatch(r"apt/pool/main/t/tailtray/[^/]+\.deb", value) is not None
    )


def load_previous(previous: Path, fingerprint: str) -> dict[str, dict[str, object]]:
    if not previous.is_dir() or previous.is_symlink():
        fail("previous must be a directory")
    manifest_path = previous / "manifest.json"
    signature_path = previous / "manifest.json.asc"
    require_regular_file(manifest_path, "previous manifest")
    require_regular_file(signature_path, "previous manifest signature")
    result = subprocess.run(
        ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature_path), str(manifest_path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        fail("previous manifest signature verification failed")
    valid = [line.split() for line in result.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
    if not valid or valid[0][2].upper() != fingerprint:
        fail("previous manifest was not signed by the expected key")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid previous manifest: {exc}")
    if data.get("schema") != 1 or not isinstance(data.get("files"), dict):
        fail("previous manifest schema must be 1")
    previous_files: dict[str, dict[str, object]] = {}
    for name, item in data["files"].items():
        if not isinstance(name, str) or not isinstance(item, dict):
            fail("previous manifest file entry is invalid")
        relative = safe_relative(name)
        if not immutable_manifest_path(relative):
            fail(f"previous manifest contains a generated or foreign path: {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))) or not isinstance(item.get("size"), int):
            fail(f"invalid checksum entry: {relative}")
        path = safe_previous_file(previous, relative)
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
            fail(f"previous checksum mismatch: {relative}")
        previous_files[relative.as_posix()] = {"sha256": item["sha256"], "size": item["size"]}
    return previous_files


def merge_file(source: Path, relative: str, destination_root: Path, manifest: dict[str, dict[str, object]]) -> None:
    destination = destination_root / Path(relative)
    digest = sha256(source)
    size = source.stat().st_size
    existing = manifest.get(relative)
    if existing and (existing["sha256"] != digest or existing["size"] != size):
        fail(f"immutable path conflict: {relative}")
    copy_file(source, destination)
    manifest[relative] = {"path": relative, "sha256": digest, "size": size}


def write_manifest(root: Path, manifest: dict[str, dict[str, object]], versions: set[str]) -> None:
    data = {
        "schema": 1,
        "registry_layout": "flat-v1",
        "files": {
            key: {"sha256": manifest[key]["sha256"], "size": manifest[key]["size"]}
            for key in sorted(manifest)
        },
        "versions": sorted(versions),
    }
    (root / "manifest.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [f"{metadata['sha256']}  {path}" for path, metadata in data["files"].items()]
    (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def sign(root: Path, fingerprint: str, relative: str, detached: str | None = None, armor: bool = True) -> None:
    source = root / relative
    output = root / (detached or f"{relative}.asc")
    armor_arg = ["--armor"] if armor else []
    run_tool([
        "gpg", "--batch", "--yes", "--local-user", fingerprint,
        "--digest-algo", "SHA256", *fake_time_args(), *armor_arg,
        "--detach-sign", "--output", str(output), str(source),
    ])


def fake_time_args() -> list[str]:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        return []
    try:
        int(epoch)
    except ValueError as exc:
        fail(f"invalid SOURCE_DATE_EPOCH: {exc}")
    return ["--faked-system-time", epoch]


def build_rpm_metadata(root: Path) -> None:
    for fedora in RPM_TARGETS:
        repo = root / "rpm" / "fedora" / fedora
        run_tool(["createrepo_c", str(repo)])


def package_control_for_apt(path: Path) -> str:
    result = run_tool(["dpkg-deb", "-f", str(path)])
    control = result.stdout.rstrip("\n")
    if not control:
        fail(f"DEB control metadata is empty: {path.name}")
    return control


def build_apt_metadata(root: Path, now: str) -> None:
    entries = []
    pool = root / "apt" / "pool" / "main" / "t" / NAME
    debs = sorted(pool.glob("*.deb"))
    if not debs:
        fail("staged APT pool contains no DEB packages")
    for deb in debs:
        control = package_control_for_apt(deb)
        relative = deb.relative_to(root / "apt").as_posix()
        version = next((line.split(": ", 1)[1] for line in control.splitlines() if line.startswith("Version: ")), "")
        if not version:
            fail(f"DEB control metadata has no Version: {deb.name}")
        entries.append((version, control, relative, deb))
    for target, codename in DEB_TARGETS.items():
        selected = [entry for entry in entries if entry[0].endswith(f"+{target}")]
        if not selected:
            fail(f"staged APT pool has no package for {target}")
        package_text = ""
        for _, control, relative, deb in sorted(selected):
            package_text += (
                control + "\n"
                f"Filename: {relative}\nSize: {deb.stat().st_size}\n"
                f"SHA256: {sha256(deb)}\nSHA512: {sha512(deb)}\n\n"
            )
        package_bytes = package_text.encode()
        for arch in ("amd64", "arm64"):
            directory = root / "apt" / "dists" / codename / "main" / f"binary-{arch}"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "Packages").write_bytes(package_bytes)
            with (directory / "Packages.gz").open("wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
                    compressed.write(package_bytes)
        release = root / "apt" / "dists" / codename / "Release"
        hashes = []
        for algorithm in ("SHA256", "SHA512"):
            hasher = sha256 if algorithm == "SHA256" else sha512
            for relative in [
                "main/binary-amd64/Packages", "main/binary-amd64/Packages.gz",
                "main/binary-arm64/Packages", "main/binary-arm64/Packages.gz",
            ]:
                path = release.parent / relative
                hashes.append((algorithm, hasher(path), path.stat().st_size, relative))
        body = [
            "Origin: TailTray", "Label: TailTray", f"Suite: {codename}", f"Codename: {codename}",
            "Architectures: amd64 arm64", "Components: main", f"Date: {now}", "SHA256:",
        ]
        body += [f" {digest} {size:>16} {relative}" for algorithm, digest, size, relative in hashes if algorithm == "SHA256"]
        body += ["SHA512:"]
        body += [f" {digest} {size:>16} {relative}" for algorithm, digest, size, relative in hashes if algorithm == "SHA512"]
        release.write_text("\n".join(body) + "\n", encoding="utf-8")


def metadata_date() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is None:
        moment = datetime.now(timezone.utc)
    else:
        try:
            moment = datetime.fromtimestamp(int(epoch), timezone.utc)
        except (ValueError, OverflowError, OSError) as exc:
            fail(f"invalid SOURCE_DATE_EPOCH: {exc}")
    return format_datetime(moment, usegmt=True)


def sign_apt(root: Path, fingerprint: str) -> None:
    for codename in DEB_TARGETS.values():
        release = root / "apt" / "dists" / codename / "Release"
        sign(root, fingerprint, f"apt/dists/{codename}/Release", f"apt/dists/{codename}/Release.gpg", armor=False)
        run_tool([
            "gpg", "--batch", "--yes", "--local-user", fingerprint,
            "--digest-algo", "SHA256", *fake_time_args(), "--clearsign", "--output",
            str(release.parent / "InRelease"), str(release),
        ])


def build(args: argparse.Namespace) -> None:
    artifacts = Path(args.artifacts)
    output = Path(args.output)
    public_key = Path(args.public_key)
    version, files = parse_version(artifacts)
    fingerprint = check_expected_fingerprint(public_key, args.fingerprint)
    if output.exists() and (output.is_symlink() or not output.is_dir() or any(output.iterdir())):
        fail("output must be absent or an empty directory")
    previous_files = load_previous(Path(args.previous), fingerprint) if args.previous else {}
    previous_versions: set[str] = set()
    if args.previous:
        data = json.loads((Path(args.previous) / "manifest.json").read_text(encoding="utf-8"))
        previous_versions = {value for value in data.get("versions", []) if isinstance(value, str)}

    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=str(parent)) as temporary:
        stage = Path(temporary)
        manifest: dict[str, dict[str, object]] = {}
        for relative in sorted(previous_files):
            merge_file(Path(args.previous) / relative, relative, stage, manifest)
        source = files[f"{NAME}-{version}.tar.gz"]
        merge_file(source, f"downloads/source/{source.name}", stage, manifest)
        for fedora, suffix in RPM_TARGETS.items():
            binary = files[f"{NAME}-{version}-1.{suffix}.noarch.rpm"]
            source_rpm = files[f"{NAME}-{version}-1.{suffix}.src.rpm"]
            verify_rpm(binary, version, suffix, False)
            verify_rpm(source_rpm, version, suffix, True)
            merge_file(binary, f"rpm/fedora/{fedora}/Packages/{binary.name}", stage, manifest)
            merge_file(source_rpm, f"downloads/source/{source_rpm.name}", stage, manifest)
        for target, codename in DEB_TARGETS.items():
            deb = files[f"{NAME}_{version}-1+{target}_all.deb"]
            verify_deb(deb, version, f"1+{target}")
            destination = stage / "apt" / "pool" / "main" / "t" / NAME / deb.name
            merge_file(deb, destination.relative_to(stage).as_posix(), stage, manifest)
        write_manifest(stage, manifest, previous_versions | {version})
        copy_file(public_key, stage / "repository-key.asc")
        build_rpm_metadata(stage)
        build_apt_metadata(stage, metadata_date())
        sign(stage, fingerprint, "SHA256SUMS")
        sign(stage, fingerprint, "manifest.json")
        for fedora in RPM_TARGETS:
            sign(stage, fingerprint, f"rpm/fedora/{fedora}/repodata/repomd.xml")
        sign_apt(stage, fingerprint)
        stage.chmod(0o755)
        (stage / "index.html").write_text(
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>TailTray packages</title>'
            '<h1>TailTray signed package repository</h1><p>Fedora 43/44, Debian 13, Ubuntu 26.04.</p>'
            '<p><a href="repository-key.asc">Signing key</a> · <a href="SHA256SUMS">SHA256SUMS</a> · '
            '<a href="SHA256SUMS.asc">Signature</a></p><ul>'
            + ''.join(f'<li><a href="{name}">{name}</a></li>' for name in sorted(manifest))
            + '</ul></html>\n', encoding="utf-8")
        if output.exists():
            output.rmdir()
        os.replace(stage, output)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--artifacts", required=True)
    result.add_argument("--output", required=True)
    result.add_argument("--public-key", required=True)
    result.add_argument("--fingerprint", required=True)
    result.add_argument("--previous")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        build(parser().parse_args(argv))
    except RepositoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
