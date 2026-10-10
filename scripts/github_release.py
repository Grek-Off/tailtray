#!/usr/bin/env python3
"""Immutable GitHub Releases publication using a short-lived CI token."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
FINGERPRINT = re.compile(r"[0-9A-Fa-f]{40}\Z")
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MAX_PAGES = 50
MAX_REPOSITORY_FILES = 10000
MAX_REPOSITORY_BYTES = 512 * 1024 * 1024
EXPECTED_ASSET_NAMES = ("SHA256SUMS", "SHA256SUMS.asc", "repository-key.asc")


def artifact_names(version: str) -> list[str]:
    return [f"tailtray-{version}.tar.gz", *[
        f"tailtray-{version}-1.fc{target}.{arch}.rpm"
        for target in (43, 44) for arch in ("noarch", "src")
    ], *[f"tailtray_{version}-1+{target}_all.deb" for target in ("debian13", "ubuntu26.04")]]


def artifacts(directory: Path, version: str) -> list[Path]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("release directory must be a directory")
    paths = [directory / name for name in artifact_names(version)]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise ValueError("Incomplete release matrix: require Fedora 43/44, Debian 13, Ubuntu 26.04 and source")
    expected = {path.name for path in paths}
    candidates = {p.name for p in directory.iterdir() if p.name.startswith(
        (f"tailtray-{version}-", f"tailtray_{version}-", f"tailtray-{version}.tar")
    ) and p.name.endswith((".rpm", ".deb", ".tar.gz"))}
    if candidates != expected:
        raise ValueError("Unexpected release artifacts; refusing a mixed or incomplete matrix")
    return paths


def artifact_version(directory: Path) -> str:
    versions = [m.group(1) for archive in directory.glob("tailtray-*.tar.gz")
                if (m := re.fullmatch(r"tailtray-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz", archive.name))]
    if len(versions) != 1:
        raise ValueError("Require exactly one versioned source archive")
    return versions[0]


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != "https":
            raise ValueError("Refusing a non-HTTPS redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, repository: str, token: str, api: str = "https://api.github.com"):
        api_parts = urlsplit(api)
        if (api_parts.scheme != "https" or api_parts.hostname != "api.github.com" or api_parts.netloc != "api.github.com"
                or api_parts.path not in ("", "/") or api_parts.query or api_parts.fragment
                or api_parts.username is not None or api_parts.password is not None
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)):
            raise ValueError("Require an HTTPS GitHub API and a valid owner/repository")
        if repository != "Grek-Off/tailtray":
            raise ValueError("GITHUB_REPOSITORY must be Grek-Off/tailtray")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self.base = "https://api.github.com/repos/" + "/".join(quote(x, safe="") for x in repository.split("/"))
        self.base_path = urlsplit(self.base).path
        self.api_host = "api.github.com"
        self.upload_host = "uploads.github.com"
        self.token = token
        self.opener = build_opener(SafeRedirects())

    def request(self, method: str, path: str, data: bytes | None = None, json_data=None,
                *, missing: bool = False, accept: str = "application/vnd.github+json") -> bytes:
        url = path if path.startswith("https://") else self.base + path
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None:
            raise ValueError("GitHub requests must use HTTPS")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("GitHub URL must not contain a custom port") from None
        if port is not None or any(part in {".", ".."} for part in parsed.path.split("/")) or "\\" in parsed.path:
            raise ValueError("GitHub URL contains an unsafe path")
        if path.startswith("https://"):
            if parsed.hostname == self.api_host and parsed.netloc == self.api_host:
                if not parsed.path.startswith(self.base_path + "/releases/assets/"):
                    raise ValueError("GitHub API asset URL is outside the configured repository")
            elif parsed.hostname == self.upload_host and parsed.netloc == self.upload_host:
                if method != "POST" or not parsed.path.startswith(self.base_path + "/releases/") or not parsed.path.endswith("/assets"):
                    raise ValueError("GitHub upload URL is outside the configured repository")
            else:
                raise ValueError("GitHub URL host is not trusted")
        headers = {"Accept": accept, "Authorization": "Bearer " + self.token,
                   "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "tailtray-release-ci"}
        if json_data is not None:
            data = json.dumps(json_data, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(len(data))
        try:
            with self.opener.open(Request(url, data=data, headers=headers, method=method), timeout=90) as response:
                result = response.read(MAX_DOWNLOAD + 1)
                if len(result) > MAX_DOWNLOAD:
                    raise ValueError("GitHub response exceeded the size limit")
                return result
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise ValueError(f"GitHub {method} request failed (HTTP {error.code}); no credentials logged") from None
        except (URLError, OSError):
            raise ValueError("GitHub is unreachable; publication was not completed") from None

    def json(self, method: str, path: str, data=None, *, missing=False):
        raw = self.request(method, path, json_data=data, missing=missing)
        return None if raw is None else json.loads(raw)

    def release(self, tag: str):
        found = self.json("GET", "/releases/tags/" + quote(tag, safe=""), missing=True)
        if found:
            return found
        # GitHub's by-tag endpoint can hide drafts even from their author.
        # Authenticated listing includes them; otherwise a retry creates a
        # conflicting second draft and finalization cannot find its own release.
        seen = set()
        for page in range(1, MAX_PAGES + 1):
            batch = self.json("GET", "/releases?" + urlencode({"per_page": 100, "page": page}))
            if not isinstance(batch, list):
                raise ValueError("Invalid GitHub draft release listing")
            marker = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).digest()
            if marker in seen:
                raise ValueError("GitHub draft release listing repeated a page")
            seen.add(marker)
            matches = [item for item in batch if item.get("tag_name") == tag]
            if len(matches) > 1:
                raise ValueError("GitHub contains ambiguous releases for the tag")
            if matches:
                return matches[0]
            if len(batch) < 100:
                return None
        raise ValueError("GitHub draft release listing exceeded the safety bound")

    def tag_commit(self, tag: str) -> str:
        ref = self.json("GET", "/git/ref/tags/" + quote(tag, safe=""))
        obj = ref.get("object", {})
        for _ in range(4):
            if obj.get("type") == "commit":
                return obj.get("sha", "")
            if obj.get("type") != "tag" or not obj.get("sha"):
                break
            obj = self.json("GET", "/git/tags/" + quote(obj["sha"], safe="")).get("object", {})
        raise ValueError("GitHub tag does not resolve to a commit")


def safe_path(name: object) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Unsafe repository manifest path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or str(path) != name:
        raise ValueError("Unsafe repository manifest path")
    return path


def _read_limited(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ValueError("File exceeds the size limit")
    with path.open("rb") as source:
        result = source.read(limit + 1)
    if len(result) > limit:
        raise ValueError("File exceeds the size limit")
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_equal(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            first_chunk, second_chunk = left.read(1024 * 1024), right.read(1024 * 1024)
            if first_chunk != second_chunk:
                return False
            if not first_chunk:
                return True


def load_manifest(directory: Path) -> tuple[dict, list[tuple[str, Path, dict]]]:
    manifest_path, signature_path = directory / "manifest.json", directory / "manifest.json.asc"
    if any(not p.is_file() or p.is_symlink() for p in (manifest_path, signature_path)):
        raise ValueError("Missing repository manifest or signature")
    try:
        raw_manifest = _read_limited(manifest_path, MAX_MANIFEST)
        manifest = json.loads(raw_manifest.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid repository manifest: {error}") from None
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported repository manifest")
    if len(manifest["files"]) > MAX_REPOSITORY_FILES:
        raise ValueError("Repository manifest has too many files")
    entries, total = [], 0
    for name, metadata in manifest["files"].items():
        relative = safe_path(name).as_posix()
        if relative in {"manifest.json", "manifest.json.asc", "repository-key.asc"}:
            raise ValueError("Repository manifest reserves an archive root member")
        path = directory / relative
        if not path.is_file() or path.is_symlink() or not isinstance(metadata, dict):
            raise ValueError("Manifest references a missing or invalid payload")
        size, digest = metadata.get("size"), metadata.get("sha256")
        if not isinstance(size, int) or size < 0 or size > MAX_DOWNLOAD or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("Invalid repository manifest entry")
        if path.stat().st_size != size or _sha256_file(path) != digest:
            raise ValueError("Repository manifest checksum mismatch")
        total += size
        entries.append((relative, path, metadata))
    if total > MAX_REPOSITORY_BYTES:
        raise ValueError("Repository payload exceeds the size limit")
    return manifest, sorted(entries)


def repository_archive(directory: Path) -> bytes:
    manifest, entries = load_manifest(directory)
    members = [("manifest.json", directory / "manifest.json"),
               ("manifest.json.asc", directory / "manifest.json.asc"),
               ("repository-key.asc", directory / "repository-key.asc")]
    members += [(name, path) for name, path, _ in entries]
    if len({name for name, _ in members}) != len(members):
        raise ValueError("Repository archive contains duplicate root members")
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, path in members:
                if "/" in name and name not in manifest["files"]:
                    raise ValueError("Repository archive path is not in manifest")
                if not path.is_file() or path.is_symlink():
                    raise ValueError("Missing repository archive member")
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 0
                with path.open("rb") as source:
                    archive.addfile(info, source)
    result = output.getvalue()
    if len(result) > MAX_DOWNLOAD:
        raise ValueError("Repository archive exceeds the size limit")
    return result


def source_matches_commit(directory: Path, version: str, commit: str) -> None:
    source = directory / f"tailtray-{version}.tar.gz"
    if source.stat().st_size > MAX_DOWNLOAD:
        raise ValueError("Source archive exceeds the size limit")
    try:
        with tempfile.TemporaryDirectory(prefix="tailtray-source-", dir=str(directory.parent)) as temporary:
            archive_path, compressed_path = Path(temporary) / "source.tar", Path(temporary) / "source.tar.gz"
            with archive_path.open("wb") as output:
                result = subprocess.run(["git", "archive", "--format=tar", f"--prefix=tailtray-{version}/", commit],
                                        stdout=output, stderr=subprocess.DEVNULL, check=False, timeout=30)
            if result.returncode or not archive_path.stat().st_size or archive_path.stat().st_size > MAX_DOWNLOAD:
                raise ValueError("Unable to verify source archive against the tagged commit")
            with compressed_path.open("wb") as output, archive_path.open("rb") as source_tar:
                compressed = subprocess.run(["gzip", "-n"], stdin=source_tar, stdout=output,
                                            stderr=subprocess.DEVNULL, check=False, timeout=30)
            if compressed.returncode or compressed_path.stat().st_size > MAX_DOWNLOAD or not _files_equal(source, compressed_path):
                raise ValueError("Source archive is not the exact deterministic archive of the tagged commit")
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Unable to verify source archive against the tagged commit") from None


def verify_signature(signature: Path, payload: Path, public_key: Path, fingerprint: str) -> None:
    if not FINGERPRINT.fullmatch(fingerprint.replace(" ", "")):
        raise ValueError("Require a full signing fingerprint")
    try:
        imported = subprocess.run(["gpg", "--batch", "--import", str(public_key)],
                                  capture_output=True, check=False, timeout=30)
        checked = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", str(signature), str(payload)],
                                 capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Unable to verify signed release metadata") from None
    valid = any(line.startswith("[GNUPG:] VALIDSIG ") and line.split()[2].upper() == fingerprint.replace(" ", "").upper()
                for line in checked.stdout.splitlines() if len(line.split()) > 2)
    if imported.returncode or checked.returncode or not valid:
        raise ValueError("Signed release metadata is not from the pinned release key")


def validate_release_inputs(client: GitHub, directory: Path, tag: str, commit: str) -> list[Path]:
    if not VERSION.fullmatch(tag) or not SHA.fullmatch(commit):
        raise ValueError("Require vMAJOR.MINOR.PATCH tag and full commit SHA")
    if client.tag_commit(tag).lower() != commit.lower():
        raise ValueError("GitHub tag does not match GITHUB_SHA")
    version = artifact_version(directory)
    if version != tag[1:]:
        raise ValueError("Release tag does not match the common source archive")
    paths = artifacts(directory, version)
    source_matches_commit(directory, version, commit)
    for name in EXPECTED_ASSET_NAMES:
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing signed release asset: {name}")
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(paths))
    if (directory / "SHA256SUMS").read_text(encoding="utf-8") != sums:
        raise ValueError("Release checksums do not match the complete package matrix")
    fingerprint = os.environ.get("TAILTRAY_SIGNING_FINGERPRINT", "")
    verify_signature(directory / "SHA256SUMS.asc", directory / "SHA256SUMS",
                     directory / "repository-key.asc", fingerprint)
    return paths + [directory / name for name in EXPECTED_ASSET_NAMES]


def asset_matches(client: GitHub, asset: dict, path: Path) -> bool:
    if asset.get("state") != "uploaded":
        return False
    expected_size = path.stat().st_size
    digest = asset.get("digest", "")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest[7:] == _sha256_file(path) and asset.get("size") == expected_size
    return asset.get("size") == expected_size and client.request("GET", asset["url"], accept="application/octet-stream") == _read_limited(path, MAX_DOWNLOAD)


def asset_bytes_match(client: GitHub, asset: dict, expected: bytes) -> bool:
    if asset.get("state") != "uploaded" or asset.get("size") != len(expected):
        return False
    digest = asset.get("digest", "")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest[7:] == hashlib.sha256(expected).hexdigest()
    return client.request("GET", asset["url"], accept="application/octet-stream") == expected


def _refresh_release(client: GitHub, release: dict) -> dict:
    refreshed = client.json("GET", "/releases/" + str(release["id"]))
    if not isinstance(refreshed, dict):
        raise ValueError("GitHub release refresh returned an invalid response")
    if refreshed is not release:
        release.clear()
        release.update(refreshed)
    return release


def _asset(release: dict, name: str) -> dict | None:
    return next((candidate for candidate in release.get("assets", [])
                 if candidate.get("name") == name), None)


def _uploaded_asset(asset: dict) -> bool:
    return asset.get("state") == "uploaded" and isinstance(asset.get("digest"), str) and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"]))


def _manifest_path_for_artifact(version: str, name: str) -> str:
    if name == f"tailtray-{version}.tar.gz":
        return f"downloads/source/{name}"
    match = re.fullmatch(rf"tailtray-{re.escape(version)}-1\.fc(43|44)\.(noarch|src)\.rpm", name)
    if match:
        return f"rpm/fedora/{match.group(1)}/Packages/{name}" if match.group(2) == "noarch" else f"downloads/source/{name}"
    if re.fullmatch(rf"tailtray_{re.escape(version)}-1\+(debian13|ubuntu26\.04)_all\.deb", name):
        return f"apt/pool/main/t/tailtray/{name}"
    raise ValueError(f"Unsupported release artifact name: {name}")


def _manifest_asset_matches(asset: dict, metadata: dict) -> bool:
    return (_uploaded_asset(asset) and asset.get("size") == metadata.get("size")
            and asset.get("digest") == "sha256:" + str(metadata.get("sha256", "")))


def verify_package_assets_against_manifest(release: dict, manifest: dict, version: str) -> None:
    for name in artifact_names(version):
        asset = _asset(release, name)
        relative = _manifest_path_for_artifact(version, name)
        metadata = manifest.get("files", {}).get(relative)
        if not isinstance(metadata, dict) or not _manifest_asset_matches(asset or {}, metadata):
            raise ValueError(f"Package asset does not match the signed repository manifest: {name}")


def package_checksums_from_manifest(manifest: dict, version: str) -> bytes:
    lines = []
    for name in sorted(artifact_names(version)):
        relative = _manifest_path_for_artifact(version, name)
        metadata = manifest.get("files", {}).get(relative)
        if not isinstance(metadata, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256", ""))):
            raise ValueError(f"Signed repository manifest lacks package checksum: {name}")
        lines.append(f"{metadata['sha256']}  {name}\n")
    return "".join(lines).encode()


def verify_public_checksums(directory: Path, entries: list[tuple[str, Path, dict]]) -> None:
    checksums = directory / "SHA256SUMS"
    if not checksums.is_file() or checksums.is_symlink():
        raise ValueError("Missing repository checksums")
    expected = "".join(f"{metadata['sha256']}  {relative}\n" for relative, _, metadata in entries)
    if _read_limited(checksums, MAX_MANIFEST).decode("utf-8") != expected:
        raise ValueError("Repository checksums do not match the signed manifest")


def upload_asset(client: GitHub, release: dict, path: Path) -> None:
    old = _asset(release, path.name)
    if old:
        if not asset_matches(client, old, path):
            raise ValueError(f"Published asset differs: {path.name}; immutable releases cannot be overwritten")
        return
    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if urlsplit(upload_url).scheme != "https":
        raise ValueError("Release upload URL must use HTTPS")
    payload = _read_limited(path, MAX_DOWNLOAD)
    try:
        client.request("POST", upload_url + ("&" if "?" in upload_url else "?") + urlencode({"name": path.name}),
                       data=payload, accept="application/vnd.github+json")
    except ValueError as original:
        _refresh_release(client, release)
        old = _asset(release, path.name)
        if not old or not asset_matches(client, old, path):
            raise original
        return
    _refresh_release(client, release)
    if not (old := _asset(release, path.name)) or not asset_matches(client, old, path):
        raise ValueError("GitHub did not confirm the uploaded asset digest and size")


def upload_bytes(client: GitHub, release: dict, name: str, payload: bytes) -> None:
    old = _asset(release, name)
    if old:
        if not asset_bytes_match(client, old, payload):
            raise ValueError(f"Published asset differs: {name}; immutable releases cannot be overwritten")
        return
    upload_url = release.get("upload_url", "").split("{", 1)[0]
    if urlsplit(upload_url).scheme != "https":
        raise ValueError("Release upload URL must use HTTPS")
    try:
        client.request("POST", upload_url + ("&" if "?" in upload_url else "?") + urlencode({"name": name}),
                       data=payload, accept="application/vnd.github+json")
    except ValueError as original:
        _refresh_release(client, release)
        old = _asset(release, name)
        if not old or not asset_bytes_match(client, old, payload):
            raise original
        return
    _refresh_release(client, release)
    if not (old := _asset(release, name)) or not asset_bytes_match(client, old, payload):
        raise ValueError("GitHub did not confirm the uploaded history digest and size")


def verify_release_assets(client: GitHub, release: dict, paths: list[Path]) -> None:
    if len(release.get("assets", [])) != len({a.get("name") for a in release.get("assets", [])}):
        raise ValueError("GitHub release contains duplicate asset names")
    for path in paths:
        old = _asset(release, path.name)
        if not old or not asset_matches(client, old, path):
            raise ValueError(f"Published asset missing or differs: {path.name}")


def release_body(tag: str, commit: str) -> str:
    if not VERSION.fullmatch(tag) or not SHA.fullmatch(commit):
        raise ValueError("Release notes require a stable tag and full source commit")
    base = "https://github.com/Grek-Off/tailtray"
    return ("Unofficial native Linux controller for the official Tailscale daemon.\n\n"
            "- Fedora 43/44: signed noarch RPM and source RPM.\n"
            "- Debian 13 / Ubuntu 26.04: all-architecture DEB.\n"
            "- Reproducible source `.tar.gz`, signed checksums and repository history.\n\n"
            f"[Changes]({base}/blob/{tag}/CHANGELOG.md) · "
            f"[Installation, updates and signature verification]({base}/blob/{tag}/docs/releases.md)\n\n"
            "[Signed APT/DNF repository](https://grek-off.github.io/tailtray). "
            "No GitHub token is needed. Existing GitLab installations remain opt-in.\n\n"
            "Signing fingerprint: `D432326D1167240864F8345763FA04F99681DA62`.\n\n"
            f"Source commit: [{commit}]({base}/commit/{commit}). "
            "Native installation/runtime and anonymous package-manager checks run on x86_64; "
            "arm64 end-to-end coverage is not claimed. Tailscale itself is not bundled.\n")


def publish(client: GitHub, directory: Path, tag: str, commit: str) -> None:
    paths = validate_release_inputs(client, directory, tag, commit)
    release = client.release(tag)
    if not release:
        try:
            release = client.json("POST", "/releases", {"tag_name": tag, "target_commitish": commit,
                "name": "TailTray " + tag, "body": release_body(tag, commit),
                "draft": True, "prerelease": False, "generate_release_notes": False})
        except ValueError:
            release = client.release(tag)
            if not release:
                raise
    elif not release.get("draft") or release.get("immutable"):
        verify_release_assets(client, release, paths)
        print(f"Verified published release {tag}: {len(paths)} immutable assets")
        return
    for path in paths:
        upload_asset(client, release, path)
    verify_release_assets(client, release, paths)
    print(f"Verified draft release {tag}: {len(paths)} immutable assets")


def fetch_previous(client: GitHub, destination: Path, fingerprint: str) -> bool:
    fingerprint = fingerprint.replace(" ", "").upper()
    if not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("Require a full 40-hex-digit release fingerprint")
    if destination.exists():
        raise ValueError("Previous-repository destination must not exist")
    candidates, seen_pages = [], set()
    for page in range(1, MAX_PAGES + 1):
        raw = client.request("GET", "/releases?" + urlencode({"per_page": 100, "page": page}))
        batch = json.loads(raw)
        if not isinstance(batch, list):
            raise ValueError("GitHub releases pagination returned a non-list response")
        marker = hashlib.sha256(json.dumps(batch, sort_keys=True).encode()).digest()
        if marker in seen_pages:
            raise ValueError("GitHub releases pagination repeated a page")
        seen_pages.add(marker)
        candidates.extend(x for x in batch if VERSION.fullmatch(str(x.get("tag_name", "")))
                         and not x.get("draft") and not x.get("prerelease"))
        if len(batch) < 100:
            break
    else:
        raise ValueError("GitHub releases pagination exceeded the safety bound")
    candidates.sort(key=lambda x: tuple(map(int, VERSION.fullmatch(x["tag_name"]).groups())), reverse=True)
    for release in candidates:
        name = f"tailtray-repository-{release['tag_name'][1:]}.tar.gz"
        asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
        if not asset or not _uploaded_asset(asset):
            continue
        raw = client.request("GET", asset["url"], accept="application/octet-stream")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tailtray-previous-", dir=str(destination.parent)) as temporary:
            root = Path(temporary)
            try:
                with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                    names = set()
                    total = 0
                    count = 0
                    for member in archive:
                        count += 1
                        if count > MAX_REPOSITORY_FILES + 3:
                            raise ValueError("Previous repository archive has too many files")
                        if not member.isreg() or member.name in names:
                            raise ValueError("Previous repository archive contains a non-file or duplicate")
                        names.add(member.name)
                        if member.name not in {"manifest.json", "manifest.json.asc", "repository-key.asc"}:
                            safe_path(member.name)
                        total += member.size
                        if total > MAX_REPOSITORY_BYTES:
                            raise ValueError("Previous repository archive exceeds the size limit")
                        destination_path = root / member.name
                        destination_path.parent.mkdir(parents=True, exist_ok=True)
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise ValueError("Previous repository archive member cannot be read")
                        written = 0
                        with destination_path.open("xb") as output:
                            for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                                output.write(chunk)
                                written += len(chunk)
                        if written != member.size:
                            raise ValueError("Previous repository archive member is truncated")
                    if {"manifest.json", "manifest.json.asc", "repository-key.asc"} - names:
                        raise ValueError("Previous repository archive lacks signed root members")
                    if total > MAX_REPOSITORY_BYTES:
                        raise ValueError("Previous repository archive exceeds the size limit")
            except (tarfile.TarError, ValueError, OSError):
                raise ValueError("Previous repository archive is invalid") from None
            manifest_path, sig_path = root / "manifest.json", root / "manifest.json.asc"
            if not manifest_path.is_file() or not sig_path.is_file():
                raise ValueError("Previous repository archive lacks signed manifest")
            try:
                checked = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify", str(sig_path), str(manifest_path)],
                                         capture_output=True, text=True, check=False, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("Unable to verify previous repository signature") from None
            valid = any(line.startswith("[GNUPG:] VALIDSIG ") and line.split()[2].upper() == fingerprint
                        for line in checked.stdout.splitlines() if len(line.split()) > 2)
            if checked.returncode or not valid:
                raise ValueError("Previous repository signature is not from the pinned release key")
            manifest, entries = load_manifest(root)
            expected_names = {"manifest.json", "manifest.json.asc", "repository-key.asc"} | {name for name, _, _ in entries}
            if names != expected_names:
                raise ValueError("Previous repository archive has an unexpected member closure")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(root, destination)
        print(f"Restored verified repository history from {release['tag_name']}")
        return True
    print("No completed repository history")
    return False


def publish_repository(client: GitHub, directory: Path, tag: str, release_directory: Path | None = None) -> None:
    if not VERSION.fullmatch(tag):
        raise ValueError("Repository requires a stable version tag")
    manifest, entries = load_manifest(directory)
    version = tag[1:]
    if version not in manifest.get("versions", []) or f"downloads/source/tailtray-{version}.tar.gz" not in manifest["files"]:
        raise ValueError("Repository manifest does not contain the tagged source archive")
    if not (directory / "repository-key.asc").is_file():
        raise ValueError("Missing repository signing key")
    verify_signature(directory / "manifest.json.asc", directory / "manifest.json",
                     directory / "repository-key.asc", os.environ.get("TAILTRAY_SIGNING_FINGERPRINT", ""))
    verify_public_checksums(directory, entries)
    release = client.release(tag)
    if not release:
        raise ValueError("Repository publication requires an existing draft release")
    release_paths = None
    if release_directory is not None:
        commit = os.environ.get("GITHUB_SHA", "")
        release_paths = validate_release_inputs(client, release_directory, tag, commit)
        verify_release_assets(client, release, release_paths)
    archive = repository_archive(directory)
    name = f"tailtray-repository-{version}.tar.gz"
    expected = set(artifact_names(version)) | set(EXPECTED_ASSET_NAMES) | {name}
    actual = [a.get("name") for a in release.get("assets", [])]
    expected_before_history = expected - {name}
    published = not release.get("draft") or release.get("immutable")
    allowed = [expected] if published else [expected_before_history, expected]
    if len(actual) != len(set(actual)) or set(actual) not in allowed:
        raise ValueError("Published release has an incomplete or unexpected asset set")
    verify_package_assets_against_manifest(release, manifest, version)
    sums_asset = _asset(release, "SHA256SUMS")
    if not sums_asset or not asset_bytes_match(client, sums_asset, package_checksums_from_manifest(manifest, version)):
        raise ValueError("Release package checksums do not match the signed repository manifest")
    signature_asset = _asset(release, "SHA256SUMS.asc")
    if not signature_asset or not _uploaded_asset(signature_asset):
        raise ValueError("Release checksum signature is not a completed upload")
    key_asset = _asset(release, "repository-key.asc")
    if not key_asset or not asset_matches(client, key_asset, directory / "repository-key.asc"):
        raise ValueError("Release repository key does not match the signed repository")
    if published:
        if not _asset(release, name):
            raise ValueError("Published release lacks the repository history asset")
    else:
        upload_bytes(client, release, name, archive)
        actual = [a.get("name") for a in release.get("assets", [])]
        if set(actual) != expected or len(actual) != len(set(actual)):
            raise ValueError("Cannot publish an incomplete GitHub release")
    repository_asset = _asset(release, name)
    if not repository_asset or not asset_bytes_match(client, repository_asset, archive):
        raise ValueError("Immutable repository asset conflict")
    if release.get("draft"):
        client.json("PATCH", "/releases/" + str(release["id"]), {"draft": False})
        print(f"Published verified release {tag}: repository contains {len(entries)} files")
    else:
        print(f"Verified published release {tag}: repository contains {len(entries)} files")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("release", "fetch-previous", "repository"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--release-directory", type=Path)
    parser.add_argument("--fingerprint", default=os.environ.get("TAILTRAY_SIGNING_FINGERPRINT"))
    args = parser.parse_args()
    try:
        client = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"])
        tag, commit = os.environ.get("GITHUB_REF_NAME", ""), os.environ.get("GITHUB_SHA", "")
        if args.action == "fetch-previous":
            if not args.fingerprint:
                raise ValueError("--fingerprint is required for fetch-previous")
            fetch_previous(client, args.directory, args.fingerprint)
        elif args.action == "release":
            publish(client, args.directory, tag, commit)
        else:
            publish_repository(client, args.directory, tag, args.release_directory)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"Release failed: {error}" if isinstance(error, ValueError) else "Release setup/file operation failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
