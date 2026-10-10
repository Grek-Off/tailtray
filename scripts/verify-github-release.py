#!/usr/bin/env python3
"""Read-only, anonymous verification of a completed TailTray GitHub release.

Never reads a credential or changes system package repositories/VPN services.
Uses a temporary public-key-only GPG home and the local immutable release tag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import github_release as release

API = "https://api.github.com/repos/Grek-Off/tailtray"
SITE = "https://grek-off.github.io/tailtray"
FINGERPRINT = "D432326D1167240864F8345763FA04F99681DA62"
ROOT = Path(__file__).resolve().parents[1]


class HTTPSOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Anonymous download attempted an insecure redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get(url, *, accept="application/vnd.github+json", limit=release.MAX_DOWNLOAD):
    request = Request(url, headers={"Accept": accept, "User-Agent": "TailTray-public-verifier",
                                   "X-GitHub-Api-Version": "2026-03-10"})
    with build_opener(HTTPSOnly()).open(request, timeout=30) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Anonymous response exceeded the limit")
    return payload


class Anonymous:
    def request(self, method, path, *, accept="application/vnd.github+json"):
        if method != "GET":
            raise ValueError("Verifier is read-only")
        if path.startswith("https://"):
            if not path.startswith(API + "/releases/assets/"):
                raise ValueError("Unexpected history API endpoint")
            return get(path, accept=accept)
        return get(API + path, accept=accept)

    def tag_commit(self, tag):
        obj = json.loads(get(API + "/git/ref/tags/" + quote(tag, safe="")))["object"]
        for _ in range(4):
            if obj["type"] == "commit":
                return obj["sha"]
            if obj["type"] != "tag" or not release.SHA.fullmatch(obj["sha"]):
                break
            obj = json.loads(get(API + "/git/tags/" + obj["sha"]))["object"]
        raise ValueError("Public release tag does not resolve to a commit")


def verify(tag, directory):
    if not release.VERSION.fullmatch(tag):
        raise ValueError("Stable vMAJOR.MINOR.PATCH required")
    commit = subprocess.check_output(["git", "rev-parse", tag + "^{commit}"], cwd=ROOT, text=True).strip()
    client = Anonymous()
    if client.tag_commit(tag) != commit:
        raise ValueError("Public tag differs from the local immutable tag")
    published = json.loads(get(API + "/releases/tags/" + tag))
    if published.get("draft") or published.get("prerelease") or not published.get("immutable"):
        raise ValueError("Require a completed immutable stable GitHub release")
    version = tag[1:]
    expected = set(release.artifact_names(version)) | set(release.EXPECTED_ASSET_NAMES) | {
        f"tailtray-repository-{version}.tar.gz"}
    assets = published["assets"]
    if {asset["name"] for asset in assets} != expected or len(assets) != len(expected):
        raise ValueError("Public release has an incomplete or duplicate asset matrix")
    for asset in assets:
        if not release._uploaded_asset(asset) or not 0 <= asset["size"] <= release.MAX_DOWNLOAD:
            raise ValueError("Public asset is not a bounded completed upload")
        url = asset["browser_download_url"]
        if not url.startswith("https://github.com/Grek-Off/tailtray/releases/download/" + tag + "/"):
            raise ValueError("Unexpected public download identity")
        payload = get(url, accept="application/octet-stream", limit=asset["size"])
        if len(payload) != asset["size"] or "sha256:" + hashlib.sha256(payload).hexdigest() != asset["digest"]:
            raise ValueError("Anonymous asset bytes differ from the published digest")
        (directory / asset["name"]).write_bytes(payload)
    tracked_key = (ROOT / "packaging/repository-key.asc").read_bytes()
    if (directory / "repository-key.asc").read_bytes() != tracked_key:
        raise ValueError("Public release key differs from the pinned tracked key")
    release.validate_release_inputs(client, directory, tag, commit)
    for name in ("repository-key.asc", "manifest.json", "manifest.json.asc"):
        (directory / ("site-" + name)).write_bytes(get(SITE + "/" + name, limit=release.MAX_MANIFEST))
    if (directory / "site-repository-key.asc").read_bytes() != tracked_key:
        raise ValueError("Pages signing key differs from the pinned tracked key")
    release.verify_signature(directory / "site-manifest.json.asc", directory / "site-manifest.json",
                             directory / "site-repository-key.asc", FINGERPRINT)
    manifest = json.loads((directory / "site-manifest.json").read_bytes())
    if version not in manifest["versions"]:
        raise ValueError("Public Pages lacks the tagged version")
    for relative, metadata in manifest["files"].items():
        release.safe_path(relative)
        size = metadata["size"]
        if not isinstance(size, int) or not 0 <= size <= release.MAX_DOWNLOAD:
            raise ValueError("Invalid Pages payload size")
        payload = get(SITE + "/" + quote(relative, safe="/"), accept="application/octet-stream", limit=size)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
            raise ValueError("Anonymous Pages payload checksum mismatch")
    if not release.fetch_previous(client, directory / "restored", FINGERPRINT):
        raise ValueError("Completed release history could not be restored anonymously")
    print(json.dumps({"tag": tag, "commit": commit, "immutable_assets": len(assets),
                      "signed_pages_payloads": len(manifest["files"]), "versions": manifest["versions"],
                      "signatures": "verified", "source": "exact git archive", "authentication": "none"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tailtray-public-proof-") as temporary:
        home = Path(temporary) / "gnupg"
        home.mkdir(mode=0o700)
        os.environ["GNUPGHOME"] = str(home)
        os.environ["TAILTRAY_SIGNING_FINGERPRINT"] = FINGERPRINT
        try:
            verify(args.tag, Path(temporary))
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], capture_output=True, timeout=15)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit("Public release verification failed; no credentials used")
