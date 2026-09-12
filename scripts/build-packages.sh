#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
dist_dir="$project_dir/dist"
package_name=tailtray

version=$(PROJECT_DIR="$project_dir" python3 -c '
import os
import tomllib
from pathlib import Path

with Path(os.environ["PROJECT_DIR"], "pyproject.toml").open("rb") as metadata:
    print(tomllib.load(metadata)["project"]["version"])
')

source_archive="$dist_dir/$package_name-$version.tar.gz"
if [[ -n ${CI_COMMIT_TAG:-} && $CI_COMMIT_TAG != "v$version" ]]; then
    echo "Release tag must match pyproject.toml: v$version" >&2
    exit 1
fi
build_root=""
cleanup() {
    if [[ -n $build_root && -d $build_root ]]; then
        rm -rf -- "$build_root"
    fi
}
trap cleanup EXIT

usage() {
    echo "Usage: $0 source|rpm|deb" >&2
    exit 2
}

build_source() {
    mkdir -p "$dist_dir"
    git -C "$project_dir" archive --format=tar --prefix="$package_name-$version/" HEAD \
        | gzip -n > "$source_archive"
    echo "Created $source_archive"
}

build_rpm() {
    command -v rpmbuild >/dev/null || { echo "rpmbuild is required" >&2; exit 1; }
    build_source

    build_root=$(mktemp -d)
    mkdir -p "$build_root"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
    cp "$source_archive" "$build_root/SOURCES/"
    cp "$project_dir/packaging/tailtray.spec" "$build_root/SPECS/"
    rpmbuild -ba "$build_root/SPECS/tailtray.spec" \
        --define "_topdir $build_root" \
        --define "_sourcedir $build_root/SOURCES" \
        --define "_srcrpmdir $build_root/SRPMS" \
        --define "_rpmdir $build_root/RPMS"
    find "$build_root/RPMS" "$build_root/SRPMS" -type f \
        \( -name '*.rpm' \) -exec cp -f '{}' "$dist_dir/" \;
    echo "Created RPM artifacts in $dist_dir"
}

build_deb() {
    local command_name
    for command_name in dpkg-buildpackage dh fakeroot; do
        command -v "$command_name" >/dev/null || {
            echo "$command_name is required (install debhelper-compat and fakeroot)" >&2
            exit 1
        }
    done
    build_source

    build_root=$(mktemp -d)
    tar -xzf "$source_archive" -C "$build_root"
    local source_dir="$build_root/$package_name-$version"
    cp -a "$source_dir/packaging/debian" "$source_dir/debian"
    chmod 0755 "$source_dir/debian/rules"
    (cd "$source_dir" && dpkg-buildpackage -us -uc -b)
    find "$build_root" -maxdepth 1 -type f -name 'tailtray_*.deb' -exec cp -f '{}' "$dist_dir/" \;
    echo "Created DEB artifacts in $dist_dir"
}

case "${1:-}" in
    source) build_source ;;
    rpm) build_rpm ;;
    deb) build_deb ;;
    *) usage ;;
esac
