Name:           tailtray
Version:        0.1.0
Release:        1%{?dist}
Summary:        Native tray controller for the Tailscale daemon
License:        MIT
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       tailscale >= 1.56
Requires:       python3
Requires:       python3-gobject
Requires:       libappindicator-gtk3
Requires:       libsecret
Requires:       libnotify
Recommends:     gnome-shell-extension-appindicator

%description
TailTray is an unofficial GTK/AppIndicator controller for the official
Tailscale daemon. It manages connection state, profiles, exit nodes, common
preferences, and optional keyring-backed automatic sign-in.

%prep
%autosetup

%build
# Pure Python; no build step required.

%install
install -d %{buildroot}%{_prefix}/lib/tailtray/tailtray
install -m 0644 src/tailtray/*.py %{buildroot}%{_prefix}/lib/tailtray/tailtray/
install -Dm 0755 bin/tailtray %{buildroot}%{_bindir}/tailtray
install -Dm 0644 data/io.github.tailtray.desktop %{buildroot}%{_datadir}/applications/io.github.tailtray.desktop
install -Dm 0644 data/io.github.tailtray.metainfo.xml %{buildroot}%{_metainfodir}/io.github.tailtray.metainfo.xml
for icon in data/icons/hicolor/scalable/status/*.svg; do
  install -Dm 0644 "$icon" "%{buildroot}%{_datadir}/icons/hicolor/scalable/status/$(basename "$icon")"
done
for icon in data/tray-icons/*.png; do
  install -Dm 0644 "$icon" "%{buildroot}%{_datadir}/tailtray/icons/$(basename "$icon")"
done

%check
PYTHONPATH=src %{python3} -m unittest discover -s tests -v

%files
%license LICENSE
%doc README.md
%{_bindir}/tailtray
%{_prefix}/lib/tailtray/
%{_datadir}/applications/io.github.tailtray.desktop
%{_metainfodir}/io.github.tailtray.metainfo.xml
%{_datadir}/icons/hicolor/scalable/status/tailtray-*.svg
%{_datadir}/tailtray/icons/tailtray-hc-v2-*.png

%changelog
* Wed Sep 02 2026 TailTray contributors <codex@local> - 0.1.0-1
- Initial Fedora package
