#!/usr/bin/env python3
"""Immutable release uploads using the short-lived CI job token, never a PAT."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = re.compile(r"v(\d+)\.(\d+)\.(\d+)\Z")
MAX_DOWNLOAD = 128 * 1024 * 1024
FINGERPRINT = re.compile(r"[0-9A-Fa-f]{40}\Z")


def artifact_names(version):
    return [f"tailtray-{version}.tar.gz", *[
        f"tailtray-{version}-1.fc{target}.{arch}.rpm"
        for target in (43, 44) for arch in ("noarch", "src")
    ], *[f"tailtray_{version}-1+{target}_all.deb" for target in ("debian13", "ubuntu26.04")]]


def artifacts(directory, version):
    paths = [directory / name for name in artifact_names(version)]
    if any(not path.is_file() or path.is_symlink() for path in paths):
        raise ValueError("Incomplete release matrix: require Fedora 43/44, Debian 13, Ubuntu 26.04 and source")
    expected = {path.name for path in paths}
    candidates = {p.name for p in directory.iterdir() if p.name.startswith(
        (f"tailtray-{version}-", f"tailtray_{version}-", f"tailtray-{version}.tar")
    ) and p.suffix in (".rpm", ".deb", ".gz")}
    if candidates != expected:
        raise ValueError("Unexpected release artifacts; refusing a mixed or incomplete matrix")
    return paths


def artifact_version(directory):
    archives = sorted(directory.glob("tailtray-*.tar.gz"))
    versions = []
    for archive in archives:
        match = re.fullmatch(r"tailtray-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz", archive.name)
        if match:
            versions.append(match.group(1))
    if len(versions) != 1:
        raise ValueError("Require exactly one versioned source archive")
    return versions[0]


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing a non-HTTPS redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Job-token")
        return redirected


class GitLab:
    def __init__(self, api, project, token):
        if urlsplit(api).scheme != "https" or not project.isdecimal() or not token:
            raise ValueError("HTTPS API, numeric project ID and CI job authentication are required")
        self.base = api.rstrip("/") + "/projects/" + project
        self.token = token
        self.opener = build_opener(SafeRedirects())

    def request(self, method, path, data=None, json_data=None, missing=False):
        headers = {"JOB-TOKEN": self.token}
        if json_data is not None:
            data = json.dumps(json_data).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(Request(self.base + path, data=data, headers=headers, method=method),
                                  timeout=90) as response:
                result = response.read(MAX_DOWNLOAD + 1)
                if len(result) > MAX_DOWNLOAD:
                    raise ValueError("Release download exceeded the size limit")
                return result
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise ValueError(
                f"GitLab {method} request failed (HTTP {error.code}) at {path}; no credentials logged"
            ) from None
        except URLError:
            raise ValueError("GitLab is unreachable; publication was not completed") from None

    def package_path(self, package, tag, filename):
        # Generic package filenames may contain `/` to preserve directory structure.
        # Encode each path component, not the whole relative filename as one segment.
        filename_path = "/".join(quote(component, safe="") for component in filename.split("/"))
        return "/packages/generic/" + "/".join((quote(package, safe=""), quote(tag, safe=""), filename_path))

    def upload(self, package, tag, path):
        endpoint = self.package_path(package, tag, path.name)
        content = path.read_bytes()
        existing = self.request("GET", endpoint, missing=True)
        if existing is not None:
            if existing != content:
                raise ValueError(f"Published artifact differs: {path.name}; immutable versions cannot be overwritten")
        else:
            self.request("PUT", endpoint, data=content)
        return self.base + endpoint


def publish(client, directory, tag, commit):
    if not VERSION.fullmatch(tag) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Require vMAJOR.MINOR.PATCH tag and full commit SHA")
    version = artifact_version(directory)
    if version != tag[1:]:
        raise ValueError("Release tag does not match the common source archive")
    paths = artifacts(directory, version)
    for name in ("SHA256SUMS", "SHA256SUMS.asc", "repository-key.asc"):
        path = directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing signed release asset: {name}")
    sums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(paths))
    if (directory / "SHA256SUMS").read_text() != sums:
        raise ValueError("Release checksums do not match the complete package matrix")
    endpoint = "/releases/" + quote(tag, safe="")
    raw = client.request("GET", endpoint, missing=True)
    existing = json.loads(raw) if raw is not None else None
    if existing and existing.get("commit", {}).get("id") != commit:
        raise ValueError("Existing release belongs to another commit")
    paths += [directory / x for x in ("SHA256SUMS", "SHA256SUMS.asc", "repository-key.asc")]
    links = [{"name": p.name, "url": client.upload("tailtray", tag, p),
              "direct_asset_path": "/" + p.name, "link_type": "package"} for p in paths]
    description = (
        "Native Tailscale tray controller. Fedora 43/44 (RPM + source RPM), "
        "Debian 13 and Ubuntu 26.04 (DEB), source .tar.gz and signed SHA256SUMS. "
        "Install official Tailscale first. Packages never restart the VPN or change its preferences. "
        f"Commit: `{commit}`. See docs/releases.md for signed dnf/apt repositories."
    )
    if existing is None:
        client.request("POST", "/releases", json_data={"tag_name": tag, "ref": commit,
                       "name": "TailTray " + tag, "description": description, "assets": {"links": links}})
    else:
        old_links = {link["name"]: link for link in existing.get("assets", {}).get("links", [])}
        for link in links:
            old = old_links.get(link["name"])
            if old and old["url"] != link["url"]:
                raise ValueError("Existing release contains conflicting asset links")
            if not old:
                client.request("POST", endpoint + "/assets/links", json_data=link)
    print(f"Verified release {tag}: {len(paths)} immutable assets")


def safe_path(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or "\\" in name or str(path) != name:
        raise ValueError("Unsafe repository manifest path")
    return path


def fetch_previous(client, destination, fingerprint):
    fingerprint = fingerprint.replace(" ", "").upper()
    if not FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("Require a full 40-hex-digit release fingerprint")
    if destination.exists():
        raise ValueError("Previous-repository destination must not exist")
    packages = []
    page = 1
    while True:
        query = urlencode({"package_name": "tailtray-repository", "package_type": "generic",
                           "per_page": 100, "page": page})
        batch = json.loads(client.request("GET", "/packages?" + query))
        packages.extend(x for x in batch if x.get("name") == "tailtray-repository"
                        and VERSION.fullmatch(x.get("version", "")) and x.get("status") == "default")
        if len(batch) < 100:
            break
        page += 1
    if not packages:
        print("No previous repository: first release")
        return False
    # Partial registry uploads are not published releases. Only use versions
    # whose signed completion marker exists; HTTP errors other than 404 fail.
    for package in sorted(packages, key=lambda x: tuple(map(int, VERSION.fullmatch(x["version"]).groups())),
                          reverse=True):
        tag = package["version"]
        signature = client.request("GET", client.package_path("tailtray-repository", tag, "manifest.json.asc"),
                                   missing=True)
        if signature is not None:
            break
    else:
        print("No completed repository history")
        return False
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}.", dir=str(parent)) as temporary:
        staging = Path(temporary)
        (staging / "manifest.json.asc").write_bytes(signature)
        (staging / "manifest.json").write_bytes(client.request(
            "GET", client.package_path("tailtray-repository", tag, "manifest.json")))
        checked = subprocess.run(["gpg", "--batch", "--status-fd", "1", "--verify",
                                  str(staging / "manifest.json.asc"), str(staging / "manifest.json")],
                             capture_output=True, text=True)
        if checked.returncode or not any(
                line.split()[2].upper() == fingerprint
                for line in checked.stdout.splitlines()
                if line.startswith("[GNUPG:] VALIDSIG ") and len(line.split()) > 2):
            raise ValueError("Previous repository signature is not from the pinned release key")
        manifest = json.loads((staging / "manifest.json").read_text())
        if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("Unsupported repository manifest")
        for name, metadata in manifest["files"].items():
            relative = safe_path(name)
            if (not isinstance(metadata, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(metadata.get("sha256", "")))
                    or not isinstance(metadata.get("size"), int)):
                raise ValueError("Invalid previous repository manifest entry")
            content = client.request("GET", client.package_path("tailtray-repository", tag, name))
            if hashlib.sha256(content).hexdigest() != metadata["sha256"] or len(content) != metadata["size"]:
                raise ValueError("Previous repository content failed integrity validation")
            target = staging.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        os.replace(staging, destination)
    print(f"Restored verified repository history from {tag}")
    return True


def publish_repository(client, directory, tag):
    if not VERSION.fullmatch(tag):
        raise ValueError("Repository requires a stable version tag")
    version = tag[1:]
    manifest_path = directory / "manifest.json"
    signature_path = directory / "manifest.json.asc"
    if not manifest_path.is_file() or manifest_path.is_symlink() or not signature_path.is_file() or signature_path.is_symlink():
        raise ValueError("Missing repository manifest or signature")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported repository manifest")
    if version not in manifest.get("versions", []) or f"downloads/source/tailtray-{version}.tar.gz" not in manifest["files"]:
        raise ValueError("Repository manifest does not contain the tagged source archive")
    payloads = []
    for name, metadata in manifest["files"].items():
        relative = safe_path(name).as_posix()
        path = directory / relative
        if not path.is_file() or path.is_symlink() or not isinstance(metadata, dict):
            raise ValueError("Manifest references a missing or invalid payload")
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256") or path.stat().st_size != metadata.get("size"):
            raise ValueError("Repository manifest checksum mismatch")
        payloads.append((relative, path))
    for relative, path in payloads:
        endpoint = client.package_path("tailtray-repository", tag, relative)
        content = path.read_bytes()
        old = client.request("GET", endpoint, missing=True)
        if old is not None and old != content:
            raise ValueError("Immutable repository payload conflict")
        if old is None:
            client.request("PUT", endpoint, data=content)
    # Completion marker LAST; retries must never overwrite an old version.
    client.upload("tailtray-repository", tag, manifest_path)
    client.upload("tailtray-repository", tag, signature_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("release", "fetch-previous", "repository"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--fingerprint", default=os.environ.get("TAILTRAY_SIGNING_FINGERPRINT"))
    args = parser.parse_args()
    try:
        client = GitLab(os.environ["CI_API_V4_URL"], os.environ["CI_PROJECT_ID"], os.environ["CI_JOB_TOKEN"])
        if args.action == "fetch-previous":
            if not args.fingerprint:
                raise ValueError("--fingerprint is required for fetch-previous")
            fetch_previous(client, args.directory, args.fingerprint)
        elif args.action == "release":
            publish(client, args.directory, os.environ["CI_COMMIT_TAG"], os.environ["CI_COMMIT_SHA"])
        else:
            publish_repository(client, args.directory, os.environ["CI_COMMIT_TAG"])
    except (ValueError, KeyError, OSError) as error:
        print(f"Release failed: {error}" if isinstance(error, ValueError) else "Release setup/file operation failed",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
