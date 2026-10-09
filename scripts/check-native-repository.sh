#!/usr/bin/env bash
# Consume the actual signed indices with native tools in a disposable image.
set -euo pipefail
: "${TARGET_DISTRO:?Missing repository test target}"
repo_root=$(realpath public)
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
case "$TARGET_DISTRO" in
    fedora43|fedora44)
        target=${TARGET_DISTRO#fedora}
        printf '[tailtray]\nname=TailTray CI\nbaseurl=file://%s/rpm/fedora/%s/\nenabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file://%s/repository-key.asc\n' \
            "$repo_root" "$target" "$repo_root" > "$work_dir/tailtray.repo"
        rpmkeys --import "$repo_root/repository-key.asc"
        rpmkeys --checksig "$repo_root/rpm/fedora/$target/Packages/"*.rpm
        dnf -y --setopt="reposdir=$work_dir" --repo=tailtray makecache
        dnf --setopt="reposdir=$work_dir" --repo=tailtray repoquery tailtray | grep -F ".fc$target.noarch"
        ;;
    debian13|ubuntu26.04)
        if [[ $TARGET_DISTRO == debian13 ]]; then suite=trixie; else suite=resolute; fi
        # gpgv is present in native Debian/Ubuntu images (apt's dependency).
        # If the image instead uses sqv, explicitly install gpgv to test the
        # detached signature too; never change the trust rules.
        if ! command -v gpgv >/dev/null; then
            apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gpgv
        fi
        # gpgv accepts an armored key through apt's signed-by directly, but
        # its own --keyring needs a dearmored form, so apt verifies both chain
        # and package checksum in the actual download below.
        printf 'deb [signed-by=%s/repository-key.asc] file://%s/apt %s main\n' \
            "$repo_root" "$repo_root" "$suite" > "$work_dir/tailtray.list"
        apt_options=(-o "Dir::Etc::sourcelist=$work_dir/tailtray.list" -o 'Dir::Etc::sourceparts=-')
        apt-get "${apt_options[@]}" update
        apt-cache "${apt_options[@]}" policy tailtray | grep -F "1+$TARGET_DISTRO"
        (cd "$work_dir" && apt-get "${apt_options[@]}" download tailtray)
        ;;
    *) echo "Unsupported repository test target" >&2; exit 2 ;;
esac
