#!/usr/bin/env python3
"""Fail closed if an older tag would publish a repository lacking a newer release."""
import json
import os
from pathlib import Path
import sys

from gitlab_release import GitLab, VERSION


def main():
    client = GitLab(os.environ["CI_API_V4_URL"], os.environ["CI_PROJECT_ID"], os.environ["CI_JOB_TOKEN"])
    manifest = json.loads(Path("public/manifest.json").read_text())
    versions = {str(version).removeprefix("v") for version in manifest["versions"]}
    page = 1
    while True:
        releases = json.loads(client.request("GET", f"/releases?per_page=100&page={page}"))
        for item in releases:
            tag = item["tag_name"]
            if VERSION.fullmatch(tag) and tag[1:] not in versions:
                raise ValueError("Newer release missing from this repository; refusing stale Pages deployment")
        if len(releases) < 100:
            break
        page += 1
    print("Repository contains all published versions; safe to deploy")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError):
        print("Repository deployment refused: missing release history or GitLab check failed", file=sys.stderr)
        sys.exit(1)
