#!/bin/sh
set -eu
exec python3 scripts/gitlab_release.py release --directory dist
