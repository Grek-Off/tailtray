#!/usr/bin/env bash
# Install in a disposable CI container, never start a VPN/desktop session.
set -euo pipefail
: "${TARGET_DISTRO:?Missing native build target}"
case "${1:-}:$TARGET_DISTRO" in
    rpm:fedora43|rpm:fedora44)
        curl --fail --silent --show-error --location https://pkgs.tailscale.com/stable/fedora/tailscale.repo \
            --output /etc/yum.repos.d/tailscale.repo
        # Build artifacts are unsigned here. Only this local pre-signing
        # install bypasses package verification; the published repo never does.
        dnf install -y --setopt=install_weak_deps=False --setopt=localpkg_gpgcheck=False \
            dist/tailtray-*.noarch.rpm
        rpm -q tailtray
        ;;
    deb:debian13|deb:ubuntu26.04)
        if [[ $TARGET_DISTRO == debian13 ]]; then
            vendor=debian; suite=trixie
        else
            vendor=ubuntu; suite=resolute
        fi
        curl --fail --silent --show-error --location \
            "https://pkgs.tailscale.com/stable/$vendor/$suite.noarmor.gpg" \
            --output /usr/share/keyrings/tailscale-archive-keyring.gpg
        curl --fail --silent --show-error --location \
            "https://pkgs.tailscale.com/stable/$vendor/$suite.tailscale-keyring.list" \
            --output /etc/apt/sources.list.d/tailscale.list
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ./dist/tailtray_*_all.deb
        dpkg-query --show tailtray
        ;;
    *) echo "Unsupported smoke test target" >&2; exit 2 ;;
esac
python3 - <<'PY'
import sys
from pathlib import Path
for relative in ('/usr/bin/tailtray', '/usr/share/applications/io.github.tailtray.desktop',
                 '/usr/share/metainfo/io.github.tailtray.metainfo.xml'):
    assert Path(relative).is_file(), f'Missing installed package file: {relative}'
for source in Path('src/tailtray').glob('*.py'):
    target = Path('/usr/lib/tailtray/tailtray') / source.name
    assert target.is_file() and target.read_bytes() == source.read_bytes(), f'Installed module mismatch: {source.name}'
for source in Path('data/tray-icons').glob('*.png'):
    target = Path('/usr/share/tailtray/icons') / source.name
    assert target.is_file() and target.read_bytes() == source.read_bytes(), f'Installed icon mismatch: {source.name}'
sys.path.insert(0, '/usr/lib/tailtray')
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Secret', '1')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, AyatanaAppIndicator3, Secret, Notify
from PySide6 import QtCore, QtGui, QtWidgets, QtDBus
import tailtray.launcher
import tailtray.qt_app
import tailtray.app
print('Installed TailTray: Qt/GTK/DBus/keyring imports OK')
PY
