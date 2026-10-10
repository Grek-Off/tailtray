#!/usr/bin/env bash
set -euo pipefail
: "${GITHUB_REF_NAME:?Release tag required}"
: "${GITHUB_SHA:?Commit identity required}"
: "${GITHUB_REPOSITORY:?Repository required}"
: "${GITHUB_TOKEN:?Short-lived GitHub Actions token required}"
: "${TAILTRAY_SIGNING_KEY:?Protected base64 signing key required}"
: "${TAILTRAY_SIGNING_FINGERPRINT:?Protected signing fingerprint required}"
[[ $GITHUB_REPOSITORY == Grek-Off/tailtray ]] || { echo "Unexpected repository" >&2; exit 1; }
[[ $GITHUB_REF_NAME =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Stable release tag required" >&2; exit 1; }
[[ $GITHUB_SHA =~ ^[0-9a-fA-F]{40}$ ]] || { echo "Full GITHUB_SHA required" >&2; exit 1; }
[[ $TAILTRAY_SIGNING_FINGERPRINT =~ ^[A-Fa-f0-9]{40}$ ]] || { echo "Pinned fingerprint required" >&2; exit 1; }
[[ -d /dev/shm && -w /dev/shm ]] || { echo "Writable tmpfs is required for signing" >&2; exit 1; }
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$GITHUB_SHA")
export GNUPGHOME
GNUPGHOME=$(mktemp -d /dev/shm/tailtray-release.XXXXXXXX)
chmod 0700 "$GNUPGHOME"
cleanup() { gpgconf --kill all >/dev/null 2>&1 || true; rm -rf -- "$GNUPGHOME"; }
trap cleanup EXIT
printf '%s' "$TAILTRAY_SIGNING_KEY" | base64 --decode | gpg --batch --import >/dev/null 2>&1
listing=$(gpg --batch --with-colons --list-secret-keys 2>/dev/null)
actual=$(printf '%s\n' "$listing" | awk -F: '$1 == "fpr" {print $10; exit}')
sec_count=$(printf '%s\n' "$listing" | awk -F: '$1 == "sec" {count++} END {print count+0}')
sub_count=$(printf '%s\n' "$listing" | awk -F: '$1 == "ssb" {count++} END {print count+0}')
sec_ok=$(printf '%s\n' "$listing" | awk -F: '$1 == "sec" && $3 == "3072" && $4 == "1" && $12 ~ /s/ {count++} END {print count+0}')
[[ $actual == "${TAILTRAY_SIGNING_FINGERPRINT^^}" && $sec_count == 1 && $sub_count == 0 && $sec_ok == 1 ]] || {
    echo "Signing key must be the pinned RSA3072 primary key without subkeys" >&2; exit 1;
}
rpmkeys --import packaging/repository-key.asc
rpmsign --define '_openpgp_sign gpg' --define "_openpgp_sign_id $TAILTRAY_SIGNING_FINGERPRINT" \
    --define "_gpg_sign_cmd_extra_args --faked-system-time $SOURCE_DATE_EPOCH" --addsign dist/*.rpm
rpmkeys --checksig dist/*.rpm
cp packaging/repository-key.asc dist/repository-key.asc
python3 - <<'PY'
import hashlib
from pathlib import Path
import os
import sys
sys.path.insert(0, 'scripts')
from github_release import artifacts
paths = artifacts(Path('dist'), os.environ['GITHUB_REF_NAME'][1:])
Path('dist/SHA256SUMS').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(paths)))
PY
gpg --batch --yes --armor --local-user "$TAILTRAY_SIGNING_FINGERPRINT" --faked-system-time "$SOURCE_DATE_EPOCH" \
    --detach-sign dist/SHA256SUMS
python3 scripts/github_release.py fetch-previous --directory previous-repository
previous_args=()
if [[ -d previous-repository ]]; then previous_args=(--previous previous-repository); fi
python3 scripts/build-repository.py --artifacts dist --output public --public-key packaging/repository-key.asc \
    --fingerprint "$TAILTRAY_SIGNING_FINGERPRINT" "${previous_args[@]}"
python3 scripts/github_release.py release --directory dist
# Final publication is a separate job after signed native and public HTTPS
# consumers succeed. This job leaves a recoverable draft, never a partial release.
