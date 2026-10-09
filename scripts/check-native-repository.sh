#!/usr/bin/env bash
# Consume the actual signed indices with native tools in a disposable image.
set -euo pipefail
[[ ${CI:-} == true ]] || { echo "Native repository checks require disposable CI images" >&2; exit 2; }
: "${TARGET_DISTRO:?Missing repository test target}"
case "$TARGET_DISTRO" in
    fedora43|fedora44|debian13|ubuntu26.04) ;;
    *) echo "Unsupported repository test target" >&2; exit 2 ;;
esac
repository_url=${TAILTRAY_NATIVE_REPOSITORY_URL:-}
if [[ -n $repository_url && ( $repository_url != https://* || $repository_url == *[\?\#\@]* || $repository_url == *[[:space:]]* ) ]]; then
    echo "Repository checks require a credential-free HTTPS URL" >&2; exit 2
fi
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
chmod 0755 "$work_dir"
if [[ -n $repository_url ]]; then
    : "${CI_COMMIT_TAG:?Live check requires the deployed tag}"
    [[ $CI_COMMIT_TAG =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
    case "$TARGET_DISTRO" in
        fedora*) dnf install -y --setopt=install_weak_deps=False curl-minimal gnupg2 python3 ;;
        *) apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates gnupg python3 ;;
    esac
    export GNUPGHOME="$work_dir/gnupg"
    mkdir -m 0700 "$GNUPGHOME"
    repository_url=${repository_url%/}
    fetch() {
        curl --fail --silent --show-error --location --max-time 10 --max-redirs 3 \
            --proto '=https' --proto-redir '=https' "$repository_url/$1" --output "$work_dir/$1"
    }
    # pages:deploy and edge caches can lag the producing job. Retry only
    # readiness, bounded by the CI timeout; never use stale apt/dnf metadata.
    ready=false
    for attempt in {1..12}; do
        if fetch repository-key.asc && fetch manifest.json && fetch manifest.json.asc; then
            actual=$(gpg --batch --show-keys --with-colons "$work_dir/repository-key.asc" 2>/dev/null \
                | awk -F: '$1 == "fpr" {print $10; exit}')
            [[ $actual == D432326D1167240864F8345763FA04F99681DA62 ]] || { echo "Unexpected public signing key" >&2; exit 1; }
            gpg --batch --import "$work_dir/repository-key.asc"
            gpg --batch --verify "$work_dir/manifest.json.asc" "$work_dir/manifest.json"
            if python3 -c 'import json,sys; sys.exit(sys.argv[2][1:] not in json.load(open(sys.argv[1]))["versions"])' \
                "$work_dir/manifest.json" "$CI_COMMIT_TAG"; then ready=true; break; fi
        fi
        sleep 5
    done
    [[ $ready == true ]] || { echo "Tagged public repository is not available anonymously" >&2; exit 1; }
    repo_base=$repository_url
    key_path=$work_dir/repository-key.asc
else
    repo_root=$(realpath "${TAILTRAY_REPOSITORY_DIR:-public}")
    repo_base=file://$repo_root
    key_path=$repo_root/repository-key.asc
fi
case "$TARGET_DISTRO" in
    fedora43|fedora44)
        target=${TARGET_DISTRO#fedora}
        printf '[tailtray]\nname=TailTray CI\nbaseurl=%s/rpm/fedora/%s/\nenabled=1\ngpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file://%s\n' \
            "$repo_base" "$target" "$key_path" > "$work_dir/tailtray.repo"
        rpmkeys --import "$key_path"
        dnf -y --setopt="reposdir=$work_dir" --repo=tailtray makecache
        dnf --setopt="reposdir=$work_dir" --repo=tailtray repoquery tailtray | grep -F ".fc$target.noarch"
        dnf --setopt="reposdir=$work_dir" --repo=tailtray download --destdir="$work_dir" tailtray
        rpmkeys --checksig "$work_dir/"*.rpm
        if [[ -n $repository_url ]]; then
            [[ $(rpm -qp --queryformat '%{VERSION}' "$work_dir/"*.rpm) == "${CI_COMMIT_TAG#v}" ]]
        fi
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
        printf 'deb [signed-by=%s] %s/apt %s main\n' \
            "$key_path" "$repo_base" "$suite" > "$work_dir/tailtray.list"
        mkdir -p "$work_dir/lists/partial"
        apt_options=(-o "Dir::Etc::sourcelist=$work_dir/tailtray.list" -o 'Dir::Etc::sourceparts=-' \
            -o "Dir::State::lists=$work_dir/lists" -o 'APT::Update::Error-Mode=any')
        apt-get "${apt_options[@]}" update
        apt-cache "${apt_options[@]}" policy tailtray | grep -F "1+$TARGET_DISTRO"
        (cd "$work_dir" && apt-get "${apt_options[@]}" download tailtray)
        if [[ -n $repository_url ]]; then
            [[ $(dpkg-deb -f "$work_dir/"*.deb Version) == "${CI_COMMIT_TAG#v}-1+$TARGET_DISTRO" ]]
        fi
        ;;
    *) echo "Unsupported repository test target" >&2; exit 2 ;;
esac
