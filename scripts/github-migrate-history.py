#!/usr/bin/env python3
"""One-time, byte-preserving migration of the proven GitLab v0.1.5 release.

Only TailTray's signed public payloads and two exact authenticated GitLab
metadata objects are read. PAT stays in Secret Service/in memory; no rotation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import quote
from urllib.request import urlopen

from github_credentials import read_token
import github_release as release

OLD_SITE = "https://tailtray-657378.gitlab.io"
FINGERPRINT = "D432326D1167240864F8345763FA04F99681DA62"
COMMIT = "34b965591aa4e9463dc6a7565ed1b6aad96d96df"
ROOT = Path(__file__).resolve().parents[1]


def fetch(relative: str, limit: int) -> bytes:
    release.safe_path(relative)
    with urlopen(OLD_SITE + "/" + quote(relative, safe="/"), timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Historical public payload exceeds its verified size limit")
    return data


def allowed_payloads() -> set[str]:
    return {release._manifest_path_for_artifact(version, name)
            for version in ("0.1.4", "0.1.5") for name in release.artifact_names(version)}


def restore_public(public: Path) -> dict:
    public.mkdir()
    for name in ("repository-key.asc", "manifest.json", "manifest.json.asc", "SHA256SUMS", "SHA256SUMS.asc"):
        (public / name).write_bytes(fetch(name, release.MAX_MANIFEST))
    if (public / "repository-key.asc").read_bytes() != (ROOT / "packaging/repository-key.asc").read_bytes():
        raise ValueError("Historical signing key differs from the pinned tracked key")
    release.verify_signature(public / "manifest.json.asc", public / "manifest.json",
                             public / "repository-key.asc", FINGERPRINT)
    manifest = json.loads((public / "manifest.json").read_bytes())
    if (manifest.get("schema") != 1 or manifest.get("versions") != ["0.1.4", "0.1.5"]
            or set(manifest.get("files", {})) != allowed_payloads()):
        raise ValueError("Historical manifest is not the expected proven 14-file history")
    for name, metadata in manifest["files"].items():
        size = metadata.get("size")
        if not isinstance(size, int) or not 0 <= size <= release.MAX_DOWNLOAD:
            raise ValueError("Invalid historical payload size")
        payload = fetch(name, size)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
            raise ValueError("Historical payload checksum mismatch")
        destination = public / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    _, entries = release.load_manifest(public)
    release.verify_public_checksums(public, entries)
    release.verify_signature(public / "SHA256SUMS.asc", public / "SHA256SUMS",
                             public / "repository-key.asc", FINGERPRINT)
    return manifest


def main():
    # Public-key-only temporary GPG home. No access to the production private key.
    with tempfile.TemporaryDirectory(prefix="tailtray-history-") as temporary:
        root = Path(temporary)
        home = root / "gnupg"
        home.mkdir(mode=0o700)
        os.environ["GNUPGHOME"] = str(home)
        os.environ["TAILTRAY_SIGNING_FINGERPRINT"] = FINGERPRINT
        os.environ["GITHUB_SHA"] = COMMIT
        try:
            public, dist = root / "public", root / "dist"
            restore_public(public)
            dist.mkdir()
            for name in release.artifact_names("0.1.5"):
                shutil.copyfile(public / release._manifest_path_for_artifact("0.1.5", name), dist / name)
            shutil.copyfile(public / "repository-key.asc", dist / "repository-key.asc")
            for name in ("SHA256SUMS", "SHA256SUMS.asc"):
                endpoint = "/projects/86100130/packages/generic/tailtray/v0.1.5/" + name
                with (dist / name).open("wb") as output:
                    result = subprocess.run(["glab", "api", endpoint], stdout=output,
                                            stderr=subprocess.DEVNULL, timeout=30)
                if result.returncode or (dist / name).stat().st_size > release.MAX_MANIFEST:
                    raise ValueError("Original authenticated GitLab release metadata is unavailable")
            client = release.GitHub("Grek-Off/tailtray", read_token("Grek-Off"))
            release.publish(client, dist, "v0.1.5", COMMIT)
            release.publish_repository(client, public, "v0.1.5", dist)
            # Verify safe, complete idempotent rerun and signed history restoration.
            release.publish(client, dist, "v0.1.5", COMMIT)
            release.publish_repository(client, public, "v0.1.5", dist)
            if not release.fetch_previous(client, root / "restored", FINGERPRINT):
                raise ValueError("Migrated history could not be restored")
            print("Verified GitHub v0.1.5: original 10 assets, signed 14-payload history, safe rerun")
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, timeout=15)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        # Our validation errors contain only public filenames/HTTP status, never
        # token values, glab stderr or provider response bodies.
        raise SystemExit("Historical migration validation failed: " + str(error)) from None
    except (OSError, subprocess.SubprocessError):
        raise SystemExit("Historical migration failed; secrets and provider diagnostics suppressed")
