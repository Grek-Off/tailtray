#!/usr/bin/env bash
# Add only the signed TailTray repository; do not touch the Tailscale daemon.
set -euo pipefail
# shellcheck source=install-lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/install-lib.sh"
if [[ $# != 1 || $1 != https://* || $1 == *[\?\#]* ]]; then
    echo "Usage: bash scripts/install-repository.sh https://YOUR-PAGES-REPOSITORY" >&2
    exit 2
fi
repository=${1%/}
fingerprint=D432326D1167240864F8345763FA04F99681DA62
# shellcheck disable=SC1091
. /etc/os-release
case "$ID:${VERSION_ID:-}" in
    fedora:43|fedora:44) mode=rpm ;;
    debian:13) mode=apt; suite=trixie ;;
    ubuntu:26.04) mode=apt; suite=resolute ;;
    *) echo "Supported: Fedora 43/44, Debian 13, Ubuntu 26.04" >&2; exit 2 ;;
esac
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
export GNUPGHOME="$work_dir/gnupg"
mkdir -m 0700 "$GNUPGHOME"
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    "$repository/repository-key.asc" --output "$work_dir/key.asc"
tailtray_verify_repository_key "$work_dir/key.asc" "$fingerprint"
if [[ $mode == rpm ]]; then
    config=/etc/yum.repos.d/tailtray.repo
    printf '[tailtray]\nname=TailTray\nbaseurl=%s/rpm/fedora/$releasever/\nenabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=%s/repository-key.asc\n' \
        "$repository" "$repository" > "$work_dir/config"
else
    config=/etc/apt/sources.list.d/tailtray.sources
    printf 'Types: deb\nURIs: %s/apt\nSuites: %s\nComponents: main\nArchitectures: amd64 arm64\nSigned-By: /usr/share/keyrings/tailtray-archive-keyring.asc\n' \
        "$repository" "$suite" > "$work_dir/config"
fi
if [[ -f $config ]] && ! cmp --silent "$config" "$work_dir/config"; then
    echo "Existing $config differs; refusing to overwrite your configuration" >&2
    exit 1
fi
if [[ $mode == rpm ]]; then
    sudo rpmkeys --import "$work_dir/key.asc"
else
    key_path=/usr/share/keyrings/tailtray-archive-keyring.asc
    if [[ -f $key_path ]] && ! cmp --silent "$key_path" "$work_dir/key.asc"; then
        echo "Existing repository key differs; refusing automatic key replacement" >&2; exit 1
    fi
    sudo install -m 0644 "$work_dir/key.asc" "$key_path"
fi
sudo install -m 0644 "$work_dir/config" "$config"
if [[ $mode == rpm ]]; then
    sudo dnf --refresh install tailtray
else
    sudo apt-get update
    sudo apt-get install tailtray
fi
