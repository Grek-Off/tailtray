#!/usr/bin/env bash
# Runtime tests use installed RPM/DEB modules, not an editable source checkout.
set -euo pipefail
[[ ${CI:-} == true ]] || { echo "Run only in disposable CI images" >&2; exit 2; }
case "${TARGET_DISTRO:-}" in
    fedora43|fedora44)
        dnf install -y --setopt=install_weak_deps=False python3-pip plasma-integration dbus-daemon dbus-tools \
            xorg-x11-server-Xvfb xorg-x11-xauth util-linux
        ;;
    debian13|ubuntu26.04)
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            python3-venv python3-pip plasma-integration dbus-daemon libglib2.0-bin xvfb xauth util-linux libxcb-cursor0
        ;;
    *) echo "Unsupported runtime test target" >&2; exit 2 ;;
esac
python3 -m venv --system-site-packages .runtime-venv
.runtime-venv/bin/pip install 'pytest>=8,<10' 'pytest-timeout>=2.4,<3'
mkdir -p reports
# 2 GiB address-space ceiling, bounded suite and individual test deadlines.
# Packaging/release unit tests run once in verify; runtime regressions run on
# each native Qt/GTK combination. Neither stage calls a live Tailscale daemon.
timeout --kill-after=5s 180s prlimit --as=2147483648 -- \
    xvfb-run -a env PYTHONPATH=/usr/lib/tailtray TAILTRAY_TEST_INSTALLED=1 \
    TAILTRAY_TEST_NATIVE_MENU=1 TAILTRAY_TEST_STRICT=1 \
    .runtime-venv/bin/python -m pytest -q --junitxml="reports/runtime-$TARGET_DISTRO.xml" \
    --ignore=tests/test_packaging.py --ignore=tests/test_release.py \
    --ignore=tests/test_repository.py --ignore=tests/test_repository_deploy.py
