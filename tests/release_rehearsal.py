#!/usr/bin/env python3
"""Run the complete offline release pipeline against a real package matrix.

This is intentionally a test-side harness.  It copies the input artifacts, creates
an ephemeral signing key in /dev/shm, and never writes the source dist directory.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build-repository.py"
RELEASE = ROOT / "scripts" / "gitlab_release.py"
TOOLS = ("gpg", "gpgconf", "rpm", "rpmsign", "rpmkeys", "dpkg-deb", "createrepo_c")
VERSION_RE = re.compile(r"^tailtray-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz$")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_repository = load_module(BUILD, "release_rehearsal_build_repository")
gitlab_release = load_module(RELEASE, "release_rehearsal_gitlab_release")


def run_command(command: list[str], *, env: dict[str, str], input_text: str | None = None) -> str:
    try:
        result = subprocess.run(command, env=env, input=input_text, text=True,
                                capture_output=True, check=False, timeout=120)
    except OSError as exc:
        raise RuntimeError(f"required command unavailable: {command[0]}") from exc
    if result.returncode:
        detail = result.stderr.strip().splitlines()
        raise RuntimeError(f"command failed: {' '.join(command[:2])}: {detail[-1] if detail else 'unknown error'}")
    return result.stdout


def require_tools() -> None:
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        raise RuntimeError("required release rehearsal dependencies missing: " + ", ".join(missing))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matrix(artifacts: Path, version: str) -> list[Path]:
    names = [
        f"tailtray-{version}.tar.gz",
        *(f"tailtray-{version}-1.fc{target}.{kind}.rpm" for target in (43, 44) for kind in ("noarch", "src")),
        *(f"tailtray_{version}-1+{target}_all.deb" for target in ("debian13", "ubuntu26.04")),
    ]
    paths = [artifacts / name for name in names]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise RuntimeError("artifacts must contain the exact 7-file Fedora 43/44, Debian 13, Ubuntu 26.04 matrix")
    actual = {path.name for path in artifacts.iterdir()}
    if actual != set(names):
        raise RuntimeError("artifacts must contain exactly the 7 release matrix files")
    return paths


def source_version(artifacts: Path) -> str:
    matches = [match.group(1) for path in artifacts.iterdir()
               if (match := VERSION_RE.fullmatch(path.name))]
    if len(matches) != 1:
        raise RuntimeError("artifacts must contain exactly one semver source archive")
    return matches[0]


def gpg_key(home: Path, epoch: str, env: dict[str, str]) -> tuple[str, Path]:
    run_command(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
         "--faked-system-time", epoch, "--quick-generate-key",
         "TailTray release rehearsal <release-rehearsal@invalid>", "rsa3072", "sign", "0"], env=env)
    listing = run_command(["gpg", "--batch", "--with-colons", "--list-secret-keys"], env=env)
    primary = next((line.split(":")[9].upper() for line in listing.splitlines()
                    if line.startswith("fpr:") ), None)
    sec = [line for line in listing.splitlines() if line.startswith("sec:")]
    ssb = [line for line in listing.splitlines() if line.startswith("ssb:")]
    fields = sec[0].split(":") if len(sec) == 1 else []
    if (not primary or not fields or ssb or fields[2:4] != ["3072", "1"]
            or "s" not in fields[11].lower() or "e" in fields[11].lower()):
        raise RuntimeError("ephemeral key is not exactly one RSA3072 sign-only primary key")
    public = home / "repository-key.asc"
    public.write_bytes(subprocess.run(["gpg", "--batch", "--armor", "--export", primary], env=env,
                                     capture_output=True, check=True).stdout)
    return primary, public


def sign_rpms(artifacts: Path, fingerprint: str, env: dict[str, str], epoch: str) -> None:
    extra = f"--batch --pinentry-mode loopback --passphrase '' --faked-system-time {epoch}"
    for rpm in sorted(artifacts.glob("*.rpm")):
        run_command(["rpmsign", "--define", "_openpgp_sign gpg", "--define", f"_openpgp_sign_id {fingerprint}",
             "--define", f"_gpg_sign_cmd_extra_args {extra}", "--addsign", str(rpm)], env=env)
        run_command(["rpmkeys", "--checksig", str(rpm)], env=env)


def write_release_assets(artifacts: Path, fingerprint: str, env: dict[str, str], epoch: str) -> None:
    paths = sorted(path for path in artifacts.iterdir() if path.suffix in (".rpm", ".deb") or path.name.endswith(".tar.gz"))
    sums = "".join(f"{sha256(path)}  {path.name}\n" for path in paths)
    (artifacts / "SHA256SUMS").write_text(sums, encoding="utf-8")
    run_command(["gpg", "--batch", "--yes", "--armor", "--local-user", fingerprint, "--digest-algo", "SHA256",
         "--faked-system-time", epoch, "--detach-sign", "--output", str(artifacts / "SHA256SUMS.asc"),
         str(artifacts / "SHA256SUMS")], env=env)


def verify_release_assets(artifacts: Path, fingerprint: str, env: dict[str, str]) -> None:
    sums = (artifacts / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    for line in sums:
        digest, name = line.split("  ", 1)
        if digest != sha256(artifacts / name):
            raise RuntimeError(f"release checksum mismatch: {name}")
    run_command(["gpg", "--batch", "--status-fd", "1", "--verify", str(artifacts / "SHA256SUMS.asc"),
         str(artifacts / "SHA256SUMS")], env=env)
    if fingerprint not in run_command(["gpg", "--batch", "--with-colons", "--show-keys", str(artifacts / "repository-key.asc")], env=env):
        raise RuntimeError("exported public key fingerprint mismatch")


class MemoryGitLab(gitlab_release.GitLab):
    """Job-API-shaped transport: no token leaves this process and no network exists."""

    def __init__(self):
        super().__init__("https://gitlab.invalid/api/v4", "123", "in-memory-job-token")
        self.storage: dict[str, bytes] = {}
        self.writes: list[tuple[str, str]] = []
        self.requests = 0

    def request(self, method, path, data=None, json_data=None, missing=False):
        self.requests += 1
        if self.requests > 128:
            raise RuntimeError("Offline transport exceeded its request budget")
        if method == "GET":
            if path in self.storage:
                return self.storage[path]
            if missing:
                return None
            if path.startswith("/packages?"):
                versions = sorted({part.split("/")[4] for part in self.storage
                                   if part.startswith("/packages/generic/tailtray-repository/")})
                page = int(parse_qs(urlsplit(path).query)["page"][0])
                versions = versions[(page - 1) * 100:page * 100]
                return json.dumps([{"name": "tailtray-repository", "version": version, "status": "default"}
                                   for version in versions]).encode()
            raise ValueError(f"missing fixture: {path}")
        self.writes.append((method, path))
        if path == "/releases":
            payload = {"commit": {"id": json_data["ref"]}, "assets": json_data["assets"]}
            self.storage["/releases/" + json_data["tag_name"]] = json.dumps(payload).encode()
        elif json_data is not None:
            self.storage[path] = json.dumps(json_data).encode()
        else:
            self.storage[path] = data
        return b"{}"

def run_gitlab_roundtrip(artifacts: Path, public: Path, previous: Path, version: str, fingerprint: str) -> None:
    client = MemoryGitLab()
    commit = "a" * 40
    gitlab_release.publish(client, artifacts, "v" + version, commit)
    before = list(client.writes)
    gitlab_release.publish(client, artifacts, "v" + version, commit)
    if client.writes != before:
        raise RuntimeError("release publish is not idempotent")
    with patch.object(gitlab_release, "GitLab", return_value=client), patch.dict(os.environ, {
            "CI_API_V4_URL": "https://gitlab.invalid/api/v4", "CI_PROJECT_ID": "123",
            "CI_JOB_TOKEN": "job-token-is-not-published", "CI_COMMIT_TAG": "v" + version,
            "CI_COMMIT_SHA": commit, "TAILTRAY_SIGNING_FINGERPRINT": fingerprint}, clear=False), \
            patch.object(gitlab_release.sys, "argv", ["gitlab_release.py", "release", "--directory", str(artifacts)]):
        if gitlab_release.main() != 0:
            raise RuntimeError("gitlab release CLI main failed")
    gitlab_release.publish_repository(client, public, "v" + version)
    with patch.object(gitlab_release, "GitLab", return_value=client), patch.dict(os.environ, {
            "CI_API_V4_URL": "https://gitlab.invalid/api/v4", "CI_PROJECT_ID": "123",
            "CI_JOB_TOKEN": "job-token-is-not-published", "TAILTRAY_SIGNING_FINGERPRINT": fingerprint}, clear=False), \
            patch.object(gitlab_release.sys, "argv", ["gitlab_release.py", "fetch-previous", "--directory", str(previous)]):
        if gitlab_release.main() != 0:
            raise RuntimeError("gitlab fetch-previous CLI main failed")
    signature_path = next(path for path in client.storage if path.endswith("/manifest.json.asc"))
    client.storage[signature_path] = b"tampered"
    with tempfile.TemporaryDirectory(prefix="release-tamper-", dir="/dev/shm") as temporary:
        try:
            gitlab_release.fetch_previous(client, Path(temporary) / "rejected", fingerprint)
        except ValueError:
            pass
        else:
            raise RuntimeError("tampered previous signature was accepted")


def run_harness(args: argparse.Namespace) -> int:
    if os.environ.get("TAILTRAY_REHEARSAL_CONTAINER") != "1":
        raise RuntimeError("Run only in a disposable CI container, not on the desktop host")
    require_tools()
    artifacts = args.artifacts.resolve()
    version = source_version(artifacts)
    original = matrix(artifacts, version)
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "0")
    int(epoch)
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("output must be absent; refusing to remove existing public-test")
    with tempfile.TemporaryDirectory(prefix="tailtray-release-rehearsal-", dir="/dev/shm") as temporary:
        workspace = Path(temporary)
        copied = workspace / "dist"
        copied.mkdir()
        for path in original:
            shutil.copy2(path, copied / path.name)
        home = workspace / "gnupg"
        home.mkdir(mode=0o700)
        os.chmod(home, 0o700)
        env = {key: value for key, value in os.environ.items()
               if key != "CI_JOB_TOKEN" and not key.startswith("TAILTRAY_SIGNING_")}
        env.update(GNUPGHOME=str(home), SOURCE_DATE_EPOCH=epoch)
        try:
            fingerprint, public_key = gpg_key(home, epoch, env)
            shutil.copy2(public_key, copied / "repository-key.asc")
            # Only the disposable image's rpmdb is touched, never a host keyring.
            run_command(["rpmkeys", "--import", str(public_key)], env=env)
            sign_rpms(copied, fingerprint, env, epoch)
            write_release_assets(copied, fingerprint, env, epoch)
            verify_release_assets(copied, fingerprint, env)
            first = workspace / "public-first"
            run_command([sys.executable, str(BUILD), "--artifacts", str(copied), "--output", str(first),
                 "--public-key", str(copied / "repository-key.asc"), "--fingerprint", fingerprint], env=env)
            # The imported CLI's subprocess verifier must use the TEST keyring.
            with patch.dict(os.environ, env, clear=True):
                run_gitlab_roundtrip(copied, first, workspace / "previous", version, fingerprint)
            run_command([sys.executable, str(BUILD), "--artifacts", str(copied), "--output", str(output),
                 "--public-key", str(copied / "repository-key.asc"), "--fingerprint", fingerprint,
                 "--previous", str(workspace / "previous")], env=env)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            if version not in manifest["versions"] or len(manifest["files"]) < 7:
                raise RuntimeError("rebuilt repository lost the release matrix or source archive")
            for relative, metadata in manifest["files"].items():
                path = output / relative
                if not path.is_file() or sha256(path) != metadata["sha256"] or path.stat().st_size != metadata["size"]:
                    raise RuntimeError(f"manifest verification failed: {relative}")
            summary = {"version": version, "fingerprint": fingerprint, "matrix_files": len(original),
                       "manifest_files": len(manifest["files"]), "previous_roundtrip": True,
                       "tamper_rejected": True, "offline_transport": "in-memory"}
            (output / "release-rehearsal-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], env=env, capture_output=True, timeout=10, check=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        return run_harness(parser.parse_args())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"release rehearsal failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
