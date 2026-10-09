#!/usr/bin/env python3
"""One-time Linux setup: keyring backup + protected GitLab file variable.

Run as the desktop user with a working Secret Service and authenticated glab.
Private material stays in memory/tmpfs, stdin, keyring and GitLab; never argv.
This command refuses to overwrite existing CI variables or signing keys.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile


def run(args, data=None, env=None):
    result = subprocess.run(args, input=data, env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Signing setup command {args[0]} failed (exit {result.returncode}); diagnostics suppressed")
    return result.stdout


def api(project, endpoint, payload=None):
    args = ["glab", "api", f"projects/{project}/{endpoint}"]
    if payload is not None:
        args += ["--method", "POST", "--header", "Content-Type: application/json", "--input", "-"]
    return json.loads(run(args, json.dumps(payload).encode() if payload is not None else None))


def setup(project):
    if not sys.platform.startswith("linux") or not project.isdecimal():
        raise RuntimeError("Linux desktop and numeric project ID required")
    print("Signing setup: checking CI variable names", file=sys.stderr)
    variables = api(project, "variables")
    if any(x["key"] in ("TAILTRAY_SIGNING_KEY", "TAILTRAY_SIGNING_FINGERPRINT") for x in variables):
        raise RuntimeError("Signing CI variables already exist; refusing replacement")
    attributes = ["application", "tailtray", "purpose", "release-signing", "project", project]
    print("Signing setup: checking Secret Service", file=sys.stderr)
    existing = subprocess.run(["secret-tool", "lookup", *attributes], capture_output=True, timeout=15)
    if existing.returncode not in (0, 1) or existing.stderr:
        raise RuntimeError("Secret Service unavailable or locked; no plaintext fallback")
    with tempfile.TemporaryDirectory(prefix="tailtray-signing-", dir="/dev/shm") as directory:
        os.chmod(directory, 0o700)
        env = dict(os.environ, GNUPGHOME=directory)
        if existing.returncode == 0 and existing.stdout:
            private = base64.b64decode(existing.stdout, validate=True)
            run(["gpg", "--batch", "--import"], private, env)
        else:
            print("Signing setup: generating key in tmpfs", file=sys.stderr)
            run(["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
                 "--quick-generate-key", "TailTray release signing <releases@tailtray.invalid>",
                 "rsa3072", "sign", "2y"], env=env)
            private = run(["gpg", "--batch", "--armor", "--export-secret-keys"], env=env)
        public = run(["gpg", "--batch", "--armor", "--export"], env=env).decode()
        keys = run(["gpg", "--batch", "--with-colons", "--list-keys"], env=env).decode()
        fingerprints = [line.split(":")[9] for line in keys.splitlines() if line.startswith("fpr:")]
        if len(fingerprints) != 1 or not re.fullmatch(r"[0-9A-F]{40}", fingerprints[0]):
            raise RuntimeError("Unexpected signing key inventory")
        fingerprint = fingerprints[0]
        encoded = base64.b64encode(private).decode()
        if existing.returncode != 0:
            print("Signing setup: saving keyring backup", file=sys.stderr)
            run(["secret-tool", "store", "--label", "TailTray GitLab release signing key", *attributes],
                encoded.encode())
        # Protect only release tags; no changes to existing rules or branches.
        print("Signing setup: configuring protected tags", file=sys.stderr)
        tags = api(project, "protected_tags")
        if not any(x["name"] == "v*" for x in tags):
            api(project, "protected_tags", {"name": "v*", "create_access_level": 40})
        print("Signing setup: creating protected hidden signing variable", file=sys.stderr)
        api(project, "variables", {"key": "TAILTRAY_SIGNING_KEY", "value": encoded,
                                  "variable_type": "file", "protected": True, "masked": True,
                                  "masked_and_hidden": True, "raw": True})
        api(project, "variables", {"key": "TAILTRAY_SIGNING_FINGERPRINT", "value": fingerprint,
                                  "protected": True, "raw": True})
        # Explicitly stop agents before the tmpfs directory is removed.
        run(["gpgconf", "--kill", "all"], env=env)
        print(json.dumps({"fingerprint": fingerprint, "public_key": public,
                          "backup": "Secret Service: application=tailtray purpose=release-signing project=" + project}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    try:
        setup(parser.parse_args().project)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, RuntimeError) else "Signing setup file/process operation failed", file=sys.stderr)
        print("Release signing setup incomplete; existing keys/variables were not overwritten.",
              file=sys.stderr)
        sys.exit(1)
