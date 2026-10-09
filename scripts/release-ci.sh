#!/usr/bin/env bash
set -euo pipefail
: "${CI_COMMIT_TAG:?Release tag required}"
: "${CI_COMMIT_SHA:?Commit identity required}"
: "${TAILTRAY_SIGNING_KEY:?Protected base64 file variable required}"
: "${TAILTRAY_SIGNING_FINGERPRINT:?Protected fingerprint required}"
[[ ${CI_COMMIT_REF_PROTECTED:-} == true ]] || { echo "Release tag must be protected" >&2; exit 1; }
[[ $TAILTRAY_SIGNING_FINGERPRINT =~ ^[A-F0-9]{40}$ ]] || exit 1
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git show -s --format=%ct "$CI_COMMIT_SHA")
export GNUPGHOME
GNUPGHOME=$(mktemp -d /dev/shm/tailtray-release.XXXXXXXX)
chmod 0700 "$GNUPGHOME"
cleanup() {
    gpgconf --kill all >/dev/null 2>&1 || true
    rm -rf -- "$GNUPGHOME"
}
trap cleanup EXIT
# The CI file contains a single masked base64 value. Decode straight to stdin;
# never export private key material into shell arguments, logs or artifacts.
base64 --decode "$TAILTRAY_SIGNING_KEY" | gpg --batch --import >/dev/null 2>&1
actual=$(gpg --batch --with-colons --list-secret-keys 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
key_listing=$(gpg --batch --with-colons --list-secret-keys 2>/dev/null)
sec_count=$(printf '%s\n' "$key_listing" | awk -F: '$1 == "sec" {count++} END {print count+0}')
sub_count=$(printf '%s\n' "$key_listing" | awk -F: '$1 == "ssb" {count++} END {print count+0}')
sec_ok=$(printf '%s\n' "$key_listing" | awk -F: '$1 == "sec" && $3 == "3072" && $4 == "1" && $12 ~ /s/ {count++} END {print count+0}')
[[ $actual == "$TAILTRAY_SIGNING_FINGERPRINT" && $sec_count == 1 && $sub_count == 0 && $sec_ok == 1 ]] || {
    echo "Signing key must be the pinned RSA3072 primary key without subkeys" >&2
    exit 1
}
rpmkeys --import packaging/repository-key.asc
rpmsign --define '_openpgp_sign gpg' \
    --define "_openpgp_sign_id $TAILTRAY_SIGNING_FINGERPRINT" \
    --define "_gpg_sign_cmd_extra_args --faked-system-time $SOURCE_DATE_EPOCH" \
    --addsign dist/*.rpm
rpmkeys --checksig dist/*.rpm
cp packaging/repository-key.asc dist/repository-key.asc
python3 - <<'PY'
import hashlib
import os
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from gitlab_release import artifacts
paths = artifacts(Path('dist'), os.environ['CI_COMMIT_TAG'][1:])
Path('dist/SHA256SUMS').write_text(''.join(
    f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in sorted(paths)))
PY
gpg --batch --yes --armor --local-user "$TAILTRAY_SIGNING_FINGERPRINT" \
    --faked-system-time "$SOURCE_DATE_EPOCH" --detach-sign dist/SHA256SUMS
python3 scripts/gitlab_release.py fetch-previous --directory previous-repository
previous_args=()
if [[ -d previous-repository ]]; then previous_args=(--previous previous-repository); fi
python3 scripts/build-repository.py --artifacts dist --output public \
    --public-key packaging/repository-key.asc --fingerprint "$TAILTRAY_SIGNING_FINGERPRINT" \
    "${previous_args[@]}"
python3 scripts/gitlab_release.py repository --directory public
sh scripts/publish-release.sh
