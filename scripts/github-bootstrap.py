#!/usr/bin/env python3
"""Owner-only TailTray migration, scoped to Grek-Off/tailtray.

Reads a PAT from desktop Secret Service, never argv/files/environment. The
internal Git credential mode writes directly to Git's pipe, not a user log.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from github_credentials import CredentialError, read_token

OWNER = "Grek-Off"
REPO = "Grek-Off/tailtray"
PREFIX = "/repos/" + REPO
MAX_RESPONSE = 8 * 1024 * 1024


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("GitHub administrative redirects are refused")


class API:
    def __init__(self, token):
        if not token:
            raise RuntimeError("Scoped GitHub token is absent")
        self.token = token
        self.opener = build_opener(NoRedirects())

    def request(self, method, endpoint, payload=None, missing=False):
        parts = urlsplit(endpoint)
        path = unquote(parts.path)
        if (method not in {"GET", "POST", "PUT", "PATCH"} or parts.scheme or parts.netloc or parts.fragment
                or any(part in {".", ".."} for part in path.split("/")) or "\\" in path
                or not ((method, path) in {("GET", "/user"), ("POST", "/user/repos")}
                        or path == PREFIX or path.startswith(PREFIX + "/"))):
            raise RuntimeError("Endpoint is outside the authorized TailTray repository")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2026-03-10", "User-Agent": "TailTray-migration"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        try:
            with self.opener.open(Request("https://api.github.com" + endpoint, headers=headers,
                                          data=data, method=method), timeout=30) as response:
                content = response.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise RuntimeError("GitHub response exceeded the size limit")
                return json.loads(content) if content else None
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            raise RuntimeError(f"GitHub {method} {endpoint} failed (HTTP {error.code}); diagnostics suppressed") from None
        except (URLError, OSError, ValueError):
            raise RuntimeError("GitHub response unavailable or invalid; diagnostics suppressed") from None

    def identity(self):
        user = self.request("GET", "/user")
        if user.get("login", "").lower() != OWNER.lower() or user.get("id") != 24355001:
            raise RuntimeError("Token does not belong to the authorized GitHub owner")

    def repository(self, missing=False):
        repo = self.request("GET", PREFIX, missing=missing)
        if repo is not None and (repo.get("full_name", "").lower() != REPO.lower()
                                 or repo.get("owner", {}).get("id") != 24355001):
            raise RuntimeError("Unexpected target repository identity")
        return repo


def credential(action):
    if action != "get":
        return
    pairs = []
    size = 0
    # Git keeps stdin open while waiting for the response. A blank line, not
    # EOF, terminates a credential request; read() deadlocks against real Git.
    while True:
        line = sys.stdin.readline(4097 - size)
        size += len(line)
        if size > 4096:
            raise RuntimeError("Git credential request exceeded the size limit")
        if not line or line in ("\n", "\r\n"):
            break
        pairs.append(line.rstrip("\r\n"))
    values = {}
    for line in pairs:
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or (key in values and not key.endswith("[]")):
            raise RuntimeError("Invalid Git credential request")
        # Git's HTTP challenge/capability arrays legitimately repeat. They are
        # not needed by this narrowly scoped PAT helper and contain no authority.
        if key.endswith("[]"):
            continue
        values[key] = value
    if (values.get("protocol") != "https" or values.get("host") != "github.com"
            or values.get("path", "").lower() not in (REPO.lower(), REPO.lower() + ".git")):
        raise RuntimeError("Git authentication request is outside TailTray")
    token = read_token(OWNER)
    if token is None:
        raise RuntimeError("Scoped GitHub token is absent")
    # Internal credential helper stdout is consumed by Git over a pipe.
    sys.stdout.write("username=x-access-token\npassword=" + token + "\n\n")


def push(reviewed_commit=None):
    root = Path(__file__).resolve().parents[1]
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, check=True).stdout
    if status and reviewed_commit is None:
        raise RuntimeError("Commit reviewed local changes before pushing")
    if reviewed_commit is not None:
        import re
        if not re.fullmatch(r"[0-9a-f]{40}", reviewed_commit):
            raise RuntimeError("Reviewed commit requires a full SHA")
        ancestor = subprocess.run(["git", "merge-base", "--is-ancestor", reviewed_commit, "main"],
                                  cwd=root, capture_output=True)
        if ancestor.returncode:
            raise RuntimeError("Reviewed commit must be part of local main history")
    helper = "!python3 " + str(Path(__file__).resolve()) + " git-credential"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_TRACE") and key != "GIT_CURL_VERBOSE"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/false"
    env["SSH_ASKPASS"] = "/bin/false"
    arguments = ["git", "-c", "credential.helper=", "-c", "credential.useHttpPath=true",
                 "-c", "credential.helper=" + helper, "push", "--atomic",
                 "https://github.com/" + REPO + ".git", (reviewed_commit or "main") + ":refs/heads/main"]
    tags = subprocess.run(["git", "tag", "--list"], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    import re
    arguments += [f"refs/tags/{tag}:refs/tags/{tag}" for tag in tags if re.fullmatch(r"v\d+\.\d+\.\d+", tag)]
    result = subprocess.run(arguments, cwd=root, env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Non-forced Git push failed (exit {result.returncode}); diagnostics suppressed")
    print("Pushed main and version tags without rewriting history.")


def configure(api):
    repo = api.repository()
    if repo["private"]:
        raise RuntimeError("Make the authorized target public before configuring free-plan protections")
    api.request("PATCH", PREFIX, {"has_wiki": False, "has_projects": False,
                "delete_branch_on_merge": True, "allow_auto_merge": False,
                "security_and_analysis": {"secret_scanning": {"status": "enabled"},
                                          "secret_scanning_push_protection": {"status": "enabled"}}})
    api.request("PUT", PREFIX + "/private-vulnerability-reporting", {"enabled": True})
    api.request("PUT", PREFIX + "/immutable-releases")
    api.request("PUT", PREFIX + "/topics", {"names": ["tailscale", "linux", "kde", "system-tray", "python",
                                                       "fedora", "debian", "ubuntu"]})
    api.request("PUT", PREFIX + "/actions/permissions", {"enabled": True, "allowed_actions": "selected"})
    api.request("PUT", PREFIX + "/actions/permissions/selected-actions", {
        "github_owned_allowed": True, "verified_allowed": False, "patterns_allowed": []})
    api.request("PUT", PREFIX + "/actions/permissions/workflow", {
        "default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})
    rules = api.request("GET", PREFIX + "/rulesets")
    wanted = [
        {"name": "Immutable release tags", "target": "tag", "enforcement": "active",
         "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}}, "bypass_actors": [],
         "rules": [{"type": "update"}, {"type": "deletion"}]},
        {"name": "Maintainer release tags", "target": "tag", "enforcement": "active",
         "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
         "bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}],
         "rules": [{"type": "creation"}]},
    ]
    for rule in wanted:
        old = next((item for item in rules if item["name"] == rule["name"]), None)
        if old is None:
            api.request("POST", PREFIX + "/rulesets", rule)
        elif old.get("enforcement") != "active":
            raise RuntimeError("Existing release protection is not active; inspect rather than replace")
    for name in ("release", "github-pages"):
        api.request("PUT", PREFIX + "/environments/" + name, {"deployment_branch_policy": {
            "protected_branches": False, "custom_branch_policies": True}})
        path = PREFIX + "/environments/" + name + "/deployment-branch-policies"
        policies = api.request("GET", path)["branch_policies"]
        if not any(item["name"] == "v*.*.*" and item.get("type") == "tag" for item in policies):
            api.request("POST", path, {"name": "v*.*.*", "type": "tag"})
    print("Configured TailTray protections, read-only Actions and tag-only release environments.")


def signing(api):
    from nacl.public import PublicKey, SealedBox

    root = Path(__file__).resolve().parents[1]
    path = PREFIX + "/environments/release"
    environment = api.request("GET", path)
    policy = environment.get("deployment_branch_policy", {})
    policies = api.request("GET", path + "/deployment-branch-policies")["branch_policies"]
    if (policy.get("protected_branches") or not policy.get("custom_branch_policies")
            or not policies or any(item.get("type") != "tag" or item["name"] != "v*.*.*" for item in policies)):
        raise RuntimeError("Signing environment is not restricted to release tags")
    existing = api.request("GET", path + "/secrets/TAILTRAY_SIGNING_KEY", missing=True)
    if existing is not None:
        print("Signing secret already exists; not overwritten. A release must verify its pinned identity.")
        return
    backup = subprocess.run(["secret-tool", "lookup", "application", "tailtray", "purpose", "release-signing",
                             "project", "86100130"], capture_output=True, timeout=30)
    if backup.returncode or not backup.stdout or len(backup.stdout) > 2 * 1024 * 1024:
        raise RuntimeError("Existing TailTray signing backup unavailable; no replacement key generated")
    try:
        encoded = backup.stdout.strip()
        private = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise RuntimeError("Signing backup is not valid base64") from None
    with tempfile.TemporaryDirectory(prefix="tailtray-github-key-", dir="/dev/shm") as directory:
        os.chmod(directory, 0o700)
        env = dict(os.environ, GNUPGHOME=directory)
        try:
            imported = subprocess.run(["gpg", "--batch", "--import"], input=private, env=env,
                                      capture_output=True, timeout=30)
            listing = subprocess.run(["gpg", "--batch", "--with-colons", "--list-secret-keys"], env=env,
                                     capture_output=True, timeout=30)
            rows = listing.stdout.decode("ascii").splitlines()
            primary = [line.split(":") for line in rows if line.startswith("sec:")]
            fingerprints = [line.split(":")[9] for line in rows if line.startswith("fpr:")]
            if (imported.returncode or listing.returncode or len(primary) != 1
                    or primary[0][2:4] != ["3072", "1"] or "s" not in primary[0][11]
                    or any(line.startswith("ssb:") for line in rows)
                    or fingerprints != ["D432326D1167240864F8345763FA04F99681DA62"]):
                raise RuntimeError("Signing backup differs from the pinned RSA3072 identity")
            public = subprocess.run(["gpg", "--batch", "--armor", "--export", fingerprints[0]], env=env,
                                    capture_output=True, timeout=30)
            if public.returncode or public.stdout != (root / "packaging/repository-key.asc").read_bytes():
                raise RuntimeError("Signing backup public key differs from the tracked key")
            key = api.request("GET", path + "/secrets/public-key")
            sealed = SealedBox(PublicKey(base64.b64decode(key["key"], validate=True))).encrypt(encoded)
            api.request("PUT", path + "/secrets/TAILTRAY_SIGNING_KEY", {
                "key_id": key["key_id"], "encrypted_value": base64.b64encode(sealed).decode("ascii")})
        finally:
            subprocess.run(["gpgconf", "--kill", "all"], env=env, capture_output=True, timeout=15)
    print("Copied the existing pinned signing key to tag-only release environment; no rotation.")


def pages(api):
    api.repository()
    existing = api.request("GET", PREFIX + "/pages", missing=True)
    api.request("PUT" if existing else "POST", PREFIX + "/pages", {"build_type": "workflow"})
    current = api.request("GET", PREFIX + "/pages")
    print(json.dumps({"pages_url": current["html_url"], "build_type": current["build_type"]}))


def protect_main(api):
    repo = api.repository()
    if repo["private"] or repo["default_branch"] != "main":
        raise RuntimeError("Main protection requires the authorized public default branch")
    runs = api.request("GET", PREFIX + "/commits/main/check-runs?per_page=100")["check_runs"]
    gate = next((run for run in runs if run["name"] == "CI required"
                 and run["conclusion"] == "success" and run["app"]["slug"] == "github-actions"), None)
    if gate is None:
        raise RuntimeError("Protect main only after the actual all-jobs CI gate passes")
    api.request("PUT", PREFIX + "/branches/main/protection", {
        "required_status_checks": {"strict": True, "checks": [{"context": "CI required", "app_id": gate["app"]["id"]}]},
        "enforce_admins": False,
        "required_pull_request_reviews": {"dismiss_stale_reviews": True, "required_approving_review_count": 0},
        "restrictions": None, "required_linear_history": True,
        "allow_force_pushes": False, "allow_deletions": False, "required_conversation_resolution": True})
    rules = api.request("GET", PREFIX + "/rulesets")
    name = "Preserve public main history"
    if not any(rule["name"] == name for rule in rules):
        api.request("POST", PREFIX + "/rulesets", {
            "name": name, "target": "branch", "enforcement": "active", "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]})
    print("Main requires successful GitHub Actions CI on PRs; no force-push/deletion bypass.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "create", "public", "configure", "signing", "pages",
                                           "push", "git-credential", "protect-main"))
    parser.add_argument("credential_action", nargs="?")
    parser.add_argument("--reviewed-commit", help="Publish only this already-reviewed main commit during ongoing work")
    args = parser.parse_args()
    try:
        if args.action == "git-credential":
            credential(args.credential_action)
            return 0
        api = API(read_token(OWNER))
        api.identity()
        if args.action == "configure":
            configure(api)
            return 0
        if args.action in ("signing", "pages", "protect-main"):
            {"signing": signing, "pages": pages, "protect-main": protect_main}[args.action](api)
            return 0
        if args.action == "push":
            if api.repository() is None:
                raise RuntimeError("Target repository is absent")
            push(args.reviewed_commit)
            return 0
        repo = api.repository(missing=True)
        if args.action == "create" and repo is None:
            repo = api.request("POST", "/user/repos", {
                "name": "tailtray", "private": True, "auto_init": False,
                "description": "Unofficial native Linux Tailscale tray controller — KDE Plasma and GTK",
                "has_issues": True, "has_wiki": False, "has_projects": False,
                "delete_branch_on_merge": True})
        if repo is None:
            raise RuntimeError("Target repository is absent")
        if args.action == "public" and repo["private"]:
            repo = api.request("PATCH", PREFIX, {"private": False})
        print(json.dumps({key: repo.get(key) for key in ("id", "full_name", "html_url", "private", "default_branch")}))
    except (CredentialError, RuntimeError, OSError, subprocess.SubprocessError):
        # No arbitrary exception messages: provider output may contain secrets.
        error = sys.exc_info()[1]
        message = str(error) if isinstance(error, (CredentialError, RuntimeError)) else "Bootstrap process failed"
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
