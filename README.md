# TailTray

[![CI](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml/badge.svg)](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml)
[Releases](https://github.com/Grek-Off/tailtray/releases) · [Getting started](#getting-started) · [Documentation](#documentation) · [Changelog](CHANGELOG.md)

An independent, unofficial Linux tray controller for the official Tailscale
daemon. Connect, switch tailnets, choose exit nodes and inspect devices from a
compact native menu, without replacing `tailscaled` or reimplementing the VPN.

## Disclaimer and project identity

> This is **TailTray by Grek-Off**, developed independently in
> [`Grek-Off/tailtray`](https://github.com/Grek-Off/tailtray).
> It is **not affiliated with [Tail Tray by SneWs](https://github.com/SneWs/tail-tray)**.
> The similar names do not imply shared maintainers, releases or support channels.
> Please use [this repository's issue tracker](https://github.com/Grek-Off/tailtray/issues)
> for TailTray reports, not the other project's tracker.

TailTray is not an official Tailscale product and is not endorsed by Tailscale
or OpenAI. Its independently drawn dot-grid icon does not imply an affiliation.
The VPN engine, WireGuard keys, device identity and node state remain managed
by the official Tailscale daemon.

## Features

- **Native tray menus:** Qt 6 on KDE Plasma, GTK/AppIndicator fallback on GNOME
  and other desktops; high-contrast icons for light and dark panels.
- **Connection control:** connect, disconnect and perform a full
  down → service restart → up sequence. Automatic reconnect respects a manual
  Disconnect.
- **Tailnets and exit nodes:** switch saved profiles, add a tailnet, select an
  exit node or automatic selection, and control LAN access.
- **Devices and addresses:** see online/offline devices and copy their Tailscale
  IP addresses directly from submenus.
- **Common preferences:** DNS, subnet routes, shields-up and update checks.
- **Sign-in and permissions:** browser authentication, polkit-based operator
  repair and optional reusable auth keys stored in the desktop keyring.
- **Desktop integration:** optional autostart; on KDE, left and right clicks open
  the same native Plasma menu.
- **Signed updates:** RPM/DEB packages and APT/DNF repositories, plus reproducible
  source archives.

TailTray focuses on tray-based connection management. It does not currently
provide Taildrop file transfer, Taildrive management or a dedicated Mullvad
location browser. See the [usage guide](docs/usage.md) for detailed behavior,
recovery rules and limitations.

## Prerequisites

- The [official Tailscale client](https://tailscale.com/docs/install/linux),
  installed from its repository. `tailscaled` must be running to manage connections.
- A Linux graphical desktop with a system tray. KDE Plasma uses the native Qt
  backend; GNOME may need an AppIndicator extension.
- Python 3.11 or newer. Native packages install the required Qt/GTK libraries.
- For the repository setup commands below: Git, Bash, curl, GnuPG and permission
  to run the installer's privileged package-manager operations through `sudo`.

A reusable auth key is **not** required for normal use: Tailscale stores the
existing login in its system service. See [authentication and recovery](docs/usage.md#authentication-without-a-recurring-login-loop)
if sign-in does not persist.

## Supported platforms and downloads

| Distribution | Package | CI verification |
| --- | --- | --- |
| Fedora 43 / 44 | `noarch` RPM and source RPM | Native build, installation, Qt/GTK runtime and DNF |
| Debian 13 | `all` DEB | Native build, installation, Qt/GTK runtime and APT |
| Ubuntu 26.04 | `all` DEB | Native build, installation, Qt/GTK runtime and APT |

Download packages from [GitHub Releases](https://github.com/Grek-Off/tailtray/releases).
The [release guide](docs/releases.md) explains signatures, source archives and
updates.

CI exercises x86_64. Architecture-independent Python packages do not constitute
an end-to-end arm64 guarantee. Ubuntu 24.04 is not in the supported package
matrix; Windows and macOS are not supported by this project.

## Getting started

### Signed packages and automatic package-manager updates

Install the official Tailscale client first. Then, in your regular user session:

```fish
git clone https://github.com/Grek-Off/tailtray.git
cd tailtray
bash scripts/install-repository.sh https://grek-off.github.io/tailtray
tailtray
```

The setup script verifies the pinned repository key, adds only the TailTray
repository and installs the package. Future updates arrive through the normal
`dnf` / `apt` workflow. Package installation does not switch the VPN, assign
operator permissions, start the daemon or enable desktop autostart.

Existing repository configurations or keys that differ are not overwritten.
If you already use the old GitLab channel, follow the
[opt-in migration instructions](docs/releases.md) before changing repository
settings. The old channel remains available and uses the same signing identity.

### Run or install from a source checkout

From the cloned project directory, with the desktop dependencies installed:

```fish
make run
```

For Fedora, the [source installer](docs/usage.md#installing-on-fedora) is an
alternative to packaged installation. Unlike the repository setup script, it
also enables `tailscaled`, assigns the current user as operator and configures
TailTray autostart. Review those effects before running it.

## Documentation

| Topic | Guide |
| --- | --- |
| Sign-in, operator permissions, profiles and desktop setup | [Usage and troubleshooting](docs/usage.md) |
| Packages, signatures, APT/DNF updates and version history | [Releases and updates](docs/releases.md) |
| Connection states, recovery and verification limits | [Connection-state model](docs/connection-state.md) |
| Maintainer access and the GitHub release workflow | [GitHub migration and local access](docs/github-migration.md) |
| Changes between versions | [Changelog](CHANGELOG.md) |

## Development and tests

```fish
make check
```

Core tests do not require a running daemon or graphical session. Full manual
verification requires a desktop session, a running `tailscaled` and access to its
Unix socket.

You can also run automated integration tests in a graphical session:

```fish
env TAILTRAY_TEST_NATIVE_MENU=1 make check
```

These exercise the actual Qt `/MenuBar` export on a separate D-Bus bus: five
Disconnect/Connect cycles without a menu-close event, plus GTK widget updates.
Daemon commands are faked, so the tests never disconnect the active VPN. They
require PySide6, GTK 3, `dbus-run-session` and `gdbus`.

Build locally from the committed HEAD:

```fish
bash scripts/build-packages.sh source
bash scripts/build-packages.sh rpm  # Fedora: rpm-build, python3, git
bash scripts/build-packages.sh deb  # Debian: dpkg-dev, debhelper-compat, fakeroot, build-essential, python3, git
```

Bash is invoked explicitly because the build script is written for Bash. Output
is written to `dist/`. Uncommitted files are not included in the source archive.

## Support and contributing

Report bugs and request features in
[this repository's issues](https://github.com/Grek-Off/tailtray/issues).
Include your distribution, desktop environment, Tailscale version and steps to
reproduce the problem. Review and redact diagnostic output before posting it;
never include auth keys, login URLs or private network information.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and pull-request guidance,
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations, and
[SECURITY.md](SECURITY.md) for private vulnerability reports.

## License and credits

Released under the [MIT license](LICENSE).
Maintained by [Vladimir Grekov](https://github.com/Grek-Off), with AI-assisted
development by Codex/OpenAI. See [CONTRIBUTORS.md](CONTRIBUTORS.md) for credits.
Codex is credited as a development tool, not a human contributor or an
endorsement by OpenAI.

Tailscale and WireGuard are trademarks of their respective owners.
