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

detect_target_distro() {
    local distro_id distro_version
    # shellcheck disable=SC1091
    . /etc/os-release
    distro_id=${ID:-}
    distro_version=${VERSION_ID:-}
    case "$distro_id:$distro_version" in
        fedora:43) echo fedora43 ;;
        fedora:44) echo fedora44 ;;
        debian:13) echo debian13 ;;
        ubuntu:26.04) echo ubuntu26.04 ;;
        *)
            echo "Unsupported build host: ${distro_id:-unknown} ${distro_version:-unknown}" >&2
            return 1
            ;;
    esac
}

validate_target() {
    local mode=$1
    local target=${TARGET_DISTRO:-}
    local host_target

    if [[ -z $target ]]; then
        target=$(detect_target_distro) || exit 2
    fi
    case "$mode:$target" in
        rpm:fedora43|rpm:fedora44|deb:debian13|deb:ubuntu26.04) ;;
        *)
            echo "Unsupported TARGET_DISTRO '$target' for build mode '$mode'" >&2
            exit 2
            ;;
    esac

    # Package builders are native-only.  In particular, do not create a Fedora
    # .fcNN package by pretending that a different Fedora release is present.
    host_target=$(detect_target_distro) || exit 2
    if [[ $host_target != "$target" ]]; then
        echo "TARGET_DISTRO '$target' does not match build host '$host_target'; cross-builds are unsupported" >&2
        exit 2
    fi
    TARGET_DISTRO=$target
}

build_source() {
    mkdir -p "$dist_dir"
    git -C "$project_dir" archive --format=tar --prefix="$package_name-$version/" HEAD \
        | gzip -n > "$source_archive"
    echo "Created $source_archive"
}

build_rpm() {
    local fedora_release
    validate_target rpm
    fedora_release="fc${TARGET_DISTRO#fedora}"
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
        --define "_rpmdir $build_root/RPMS" \
        --define "dist .${fedora_release}"
    local rpm_files=()
    mapfile -t rpm_files < <(find "$build_root/RPMS" "$build_root/SRPMS" -type f -name '*.rpm')
    ((${#rpm_files[@]} > 0)) || { echo "rpmbuild produced no RPM artifacts" >&2; exit 1; }
    local rpm_file
    for rpm_file in "${rpm_files[@]}"; do
        [[ $(basename "$rpm_file") == *".${fedora_release}."* ]] || {
            echo "RPM artifact does not match requested Fedora target: $(basename "$rpm_file")" >&2
            exit 1
        }
    done
    cp -f "${rpm_files[@]}" "$dist_dir/"
    echo "Created RPM artifacts in $dist_dir"
}

build_deb() {
    validate_target deb
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
    local deb_revision="1+${TARGET_DISTRO}"
    sed -i "1s/^${package_name} (${version}-[^)]*)/${package_name} (${version}-${deb_revision})/" \
        "$source_dir/debian/changelog"
    chmod 0755 "$source_dir/debian/rules"
    (cd "$source_dir" && dpkg-buildpackage -us -uc -b)
    local deb_files=()
    mapfile -t deb_files < <(find "$build_root" -maxdepth 1 -type f -name "${package_name}_*.deb")
    ((${#deb_files[@]} > 0)) || { echo "dpkg-buildpackage produced no DEB artifacts" >&2; exit 1; }
    local deb_file
    for deb_file in "${deb_files[@]}"; do
        [[ $(basename "$deb_file") == "${package_name}_${version}-${deb_revision}_"* ]] || {
            echo "DEB artifact does not match requested target version: $(basename "$deb_file")" >&2
            exit 1
        }
    done
    cp -f "${deb_files[@]}" "$dist_dir/"
    echo "Created DEB artifacts in $dist_dir"
}

case "${1:-}" in
    source) build_source ;;
    rpm) build_rpm ;;
    deb) build_deb ;;
    *) usage ;;
esac
