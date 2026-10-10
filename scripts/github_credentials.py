#!/usr/bin/env python3
"""Store a GitHub fine-grained PAT in Linux Secret Service, never a file.

Interactive input only. Reading is a Python API for migration tooling; the CLI
never prints a token. Existing entries are not overwritten or deleted.
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import re
import subprocess
import sys
import warnings


class CredentialError(RuntimeError):
    """A sanitized diagnostic safe to show without provider output."""


def _attributes(owner: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
        raise CredentialError("Invalid GitHub owner name")
    if not sys.platform.startswith("linux"):
        raise CredentialError("Linux desktop Secret Service is required; no plaintext fallback")
    return ["application", "tailtray", "purpose", "github-token", "owner", owner.lower()]


def _run(arguments: list[str], data: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(arguments, input=data, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        raise CredentialError("Secret Service operation failed; unlock KDE Wallet and check secret-tool") from None


def read_token(owner: str) -> str | None:
    """Return a token only in memory; None means a genuinely absent entry."""
    result = _run(["secret-tool", "lookup", *_attributes(owner)])
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return None
    if result.returncode or not result.stdout or len(result.stdout) > 1024:
        raise CredentialError("Secret Service unavailable, locked or returned an invalid entry")
    try:
        token = result.stdout.decode("ascii").rstrip("\r\n")
    except UnicodeError:
        raise CredentialError("Invalid GitHub token entry") from None
    _validate_token(token)
    return token


def _validate_token(token: str) -> None:
    if not re.fullmatch(r"github_pat_[A-Za-z0-9_]{20,255}", token):
        raise CredentialError("Expected a fine-grained GitHub PAT; the supplied value was not saved")


def store_token(owner: str, token: str) -> None:
    """Refuse replacement, store over stdin, then verify the exact readback."""
    _validate_token(token)
    attributes = _attributes(owner)
    if read_token(owner) is not None:
        raise CredentialError("A GitHub token entry already exists; refusing to overwrite it")
    result = _run(["secret-tool", "store", "--label", f"TailTray GitHub access ({owner})", *attributes],
                  token.encode("ascii"))
    if result.returncode:
        raise CredentialError("Token storage failed; no plaintext fallback was used")
    stored = read_token(owner)
    if stored is None or not hmac.compare_digest(stored, token):
        raise CredentialError("Token readback failed; check KDE Wallet before using this entry")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="GitHub account that owns TailTray")
    parser.add_argument("--check", action="store_true", help="Check entry presence without printing it")
    args = parser.parse_args(argv)
    try:
        _attributes(args.owner)
        existing = read_token(args.owner)
        if args.check:
            if existing is None:
                raise CredentialError("No GitHub token entry exists for this owner")
            print("GitHub token entry is available (API permissions have not been checked).")
            return 0
        if existing is not None:
            raise CredentialError("A GitHub token entry already exists; refusing to overwrite it")
        if not sys.stdin.isatty():
            raise CredentialError("Run in an interactive terminal; tokens are not accepted through pipes or arguments")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Fine-grained GitHub PAT (hidden): ").strip()
        store_token(args.owner, token)
    except (CredentialError, getpass.GetPassWarning, EOFError, KeyboardInterrupt) as error:
        message = str(error) if isinstance(error, CredentialError) else "Token entry cancelled; no plaintext fallback"
        print(message, file=sys.stderr)
        return 1
    print("Token saved and verified in desktop Secret Service. Do not paste it into chat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
