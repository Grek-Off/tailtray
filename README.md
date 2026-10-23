# TailTray

[![CI](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml/badge.svg)](https://github.com/Grek-Off/tailtray/actions/workflows/ci.yml)
[Releases](https://github.com/Grek-Off/tailtray/releases) · [Changelog](CHANGELOG.md)

An independent, unofficial Linux tray controller for the official Tailscale
daemon. Native KDE Plasma menus, a GTK/AppIndicator fallback, saved tailnet
switching, exit-node selection, device/IP lists and connection recovery —
without reimplementing the VPN or replacing `tailscaled`.

RPM packages target Fedora 43/44; DEB packages target Debian 13 and Ubuntu
26.04. See [signed packages and updates](docs/releases.md) for the currently
verified distribution channel. The public source and preserved tags are on
[GitHub](https://github.com/Grek-Off/tailtray); the
[migration checklist](docs/github-migration.md) records release verification.

Maintained by [Vladimir Grekov](https://github.com/Grek-Off),
with AI-assisted development by Codex/OpenAI. See [credits](CONTRIBUTORS.md),
[contribution guide](CONTRIBUTING.md), [security policy](SECURITY.md),
[code of conduct](CODE_OF_CONDUCT.md) and
the [MIT license](LICENSE). TailTray is not an official Tailscale product and
is not endorsed by Tailscale or OpenAI. Its independently drawn dot-grid icon
does not change that affiliation.

## Overview

TailTray was initially developed for Fedora with GNOME or KDE. It provides a
compact desktop interface to the official Tailscale system service: the VPN
engine, WireGuard keys, device identity and node state remain managed by
`tailscaled`.

## Features

- Connect and disconnect without opening a terminal.
- View connection status, device name, Tailscale IP addresses and health warnings.
- High-contrast Tailscale-style dot-grid icon: black and white dots with opposite
  outlines remain visible on light and dark panels. KDE Plasma uses a dedicated
  set of RGBA PNGs that the desktop theme cannot recolor.
- Native Qt 6 tray/menu backend on KDE Plasma, with GTK/AppIndicator as a fallback
  for GNOME and other desktops.
- Compact top-level menu for essential actions; accounts, preferences,
  authentication, maintenance and application settings live in submenus.
- List devices in the current tailnet, their online/offline state and all
  Tailscale IP addresses; click an address to copy it.
- Dedicated `Tailnet` menu for switching saved networks/profiles and adding a new one.
- Select, disable or automatically choose an exit node.
- Manage DNS, subnet routes, LAN access through an exit node, shields-up and update checks.
- Start `tailscaled` and grant the current user operator permissions through the
  standard polkit dialog.
- Check operator permissions at startup and after profile changes; repair them
  before automatic sign-in, without repeated password prompts after cancellation.
- Full restart: `tailscale down` → restart the systemd service → wait for the
  daemon → `tailscale up`.
- Automatically reconnect after an unexpected `Stopped` state, with bounded
  backoff. A manual **Disconnect** is remembered and never undone automatically.
- Distinguish active, unavailable and unknown exit-node states; a health warning
  is not treated as proof that the exit node has failed.
- Start automatically with the desktop session.
- Standard one-time browser sign-in.
- Optional unattended sign-in with an auth key stored in GNOME Keyring/libsecret.

## Control permissions and sign-in recovery

On the first fresh status response, TailTray reads `operator` from
`tailscale get --json all`. If it names a different user or is empty, the app runs
`pkexec tailscale set --operator=UNIX_USER` for the current Unix user. The system
polkit dialog requests the password; TailTray never receives or stores it.
Permissions are checked again after the command, then browser sign-in is offered
if the daemon reports `NeedsLogin`. No password prompt appears if the operator
is already correct.

Cancellation or failure does not open a new dialog on every poll. The menu keeps
`Grant this user control…` and the error text available for an explicit retry,
also accessible through `More → Authentication`. That submenu also contains
`Sign in…`. A timeout or an unsupported `get` command leaves permissions unknown;
it is not evidence that no operator is configured. Control permissions do not
replace VPN status: the network can work even when the current user cannot
change its settings.

Restoring sign-in to the current profile uses **bare `tailscale up`**, without
logout/reset or changes to the exit node or DNS. `tailscale login` is used only
when adding a new network because it creates an empty profile. Neither operator
checks nor automatic sign-in override a manual Disconnect.

## Switching tailnets

Choose a saved network from **Tailnet: …**. Both the tailnet name and account are
shown so that networks sharing one login remain distinguishable. Switching calls
`tailscale switch PROFILE_ID` without logout, reset or another login. Devices,
exit nodes and preferences are refreshed from the daemon's new response.

If the network is not saved yet, choose **Add tailnet…** and complete the official
browser sign-in, selecting the intended tailnet. A web-console session alone
does not add a profile to this computer. Adding a tailnet switches the client to
the new sign-in; if you change your mind, use the same menu to return to a saved
network. Only one network is active at a time. Reauthentication is necessary if
the profile has not been added or its device key has expired:
[official fast user switching documentation](https://tailscale.com/docs/features/client/fast-user-switching).

The optional reusable auth key stays in the keyring; the config stores only its
profile ID. Set up the key after signing in to the intended network. Switching
tailnets never automatically uses another network's key. A legacy unbound key
is migrated automatically only from an unambiguous single-profile configuration.
If several profiles already exist, set up the key again in the intended network.

## Why this is not a port of the official Windows GUI

The official Windows GUI source is not available. The
[`tailscale/tailscale`](https://github.com/tailscale/tailscale) repository contains
the daemon, CLI and much of the shared logic, but Tailscale states that the
Windows and macOS GUI wrappers are closed source. TailTray therefore implements
its own interface using the public contracts and behavior of the official
[`client/systray`](https://github.com/tailscale/tailscale/blob/main/client/systray/systray.go).

## Installing on Fedora

First install the official Tailscale package from its repository. Then run:

```fish
./scripts/install-fedora.sh
tailtray
```

The installer checks dependencies, stops only the installed TailTray instance,
installs the application files, compares every installed Python module
byte-for-byte with the checkout and starts the verified version. It also works
if the previous instance has already crashed. The installer then enables
`tailscaled`, assigns the current user as its operator and adds TailTray to
`~/.config/autostart`. It does not log out of Tailscale, reset it or change
tailnet preferences. Check whether the installed version matches the checkout
without `sudo`:

```fish
./scripts/install-fedora.sh --check
```

To install dependencies manually:

```fish
sudo dnf install tailscale python3-gobject python3-pyside6 libappindicator-gtk3 libsecret libnotify
```

GNOME may not show StatusNotifier/AppIndicator icons by default. If the icon is
missing, install and enable the extension:

```fish
sudo dnf install gnome-shell-extension-appindicator
gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com
```

KDE Plasma usually needs no extra extension. TailTray supplies its own raster
tray-icon directory to avoid loading an old symbolic icon from the global theme
cache. KDE automatically selects the native Qt 6 backend: menu entries and
actions have stable IDs, while labels, availability and commands update in place.
Updates do not depend on a menu-close event: after `down`, the same item becomes
**Connect**. TailTray marks its StatusNotifierItem as menu-only, so left and
right clicks open the same native Plasma menu without a separate popup window.
For diagnostics, explicitly select a backend with `TAILTRAY_BACKEND=qt` or
`TAILTRAY_BACKEND=gtk`.

Both interfaces share the same command controller. Operations run sequentially,
and stale poll responses are discarded after a state change. A Disconnect
requested while connecting is remembered and runs after the current command.
Failure to read optional preferences does not erase connection status; fresh
profiles and preferences are cached for 30 seconds.

A coordination-server outage can leave existing tunnels working. TailTray shows
a warning and lets the daemon recover; automatic `up` applies to `Stopped`, not
to every network warning. `NeedsMachineAuth` requires device approval rather than
another sign-in. See the [connection-state model](docs/connection-state.md) for
transitions and verification limits.

Run directly from the checkout without installing:

```fish
make run
```

## Authentication without a recurring login loop

In a normal Linux installation, authentication belongs to the daemon, not the
tray application. The system `tailscaled` service stores it in
`/var/lib/tailscale/tailscaled.state` and runs before the user signs in to the
desktop. TailTray therefore does not log in on every startup: if the daemon is
already authenticated, the app immediately shows its current state.

When the daemon reports `NeedsLogin`, TailTray opens the official login URL once
by default. For fully automatic re-registration, choose **Set up automatic
sign-in…** and save a reusable auth key. The key is stored through Secret Service
in the user's keyring. It is passed to the CLI through a temporary file with mode
`0600`, never through the JSON config or process arguments.

A reusable auth key is a bearer credential. Restrict its tags, pre-approval and
lifetime. For a regular laptop, a single browser sign-in with key expiration
disabled for that particular device is preferable to a permanent, broadly
privileged key.

If authentication still disappears after a reboot, run the read-only diagnostics:

```fish
./scripts/diagnose.sh
```

Common causes:

- `tailscaled` is started manually instead of through its systemd unit.
- `--state=mem:` or a temporary custom `--state` is configured.
- `/var/lib/tailscale` is cleared or not persisted.
- The tailnet administrator has enabled node-key expiration.
- The local user has not been assigned `--operator` permissions.

Official documentation: [Linux installation](https://tailscale.com/docs/install/linux),
[Linux operator permission](https://tailscale.com/docs/reference/troubleshooting/linux/linux-operator-permission),
[auth keys](https://tailscale.com/docs/features/access-control/auth-keys),
[daemon config file](https://tailscale.com/kb/1654/tailscaled-config-file).

## Packages, releases and updates

CI checks the application, including Qt/GTK and D-Bus under Xvfb, then builds the
source archive, RPMs for Fedora 43/44 and separate DEBs for Debian 13 / Ubuntu
26.04. Packages use the `noarch`/`all` architecture and do not bundle Tailscale.
Add its official repository before installing. Package installation does not
switch the VPN, assign an operator or start services automatically.

Jobs use GitHub-hosted runners and isolated containers, without privileged mode
or Docker socket forwarding. On every target distribution, CI installs the
package and verifies that its Qt/GTK/D-Bus dependencies can be imported.

To release a version, update `pyproject.toml`, `src/tailtray/__init__.py`,
`packaging/tailtray.spec`, `packaging/debian/changelog` and the AppStream file
`data/io.github.tailtray.metainfo.xml`, commit the changes and push a `vX.Y.Z` tag.
The tag must match the project version. The pipeline creates a public GitHub
Release containing RPMs, DEBs, `tailtray-X.Y.Z.tar.gz`, `SHA256SUMS`, the signature,
public key and a signed history archive. Download links do not depend on temporary
CI artifact retention. Publication uses a short-lived `GITHUB_TOKEN`, not a
personal token in CI. The private signing key is a secret in the protected
`release` environment, accessible only to release tags; Git contains only the
public key. The Release is published after native/runtime and anonymous APT/DNF
checks succeed.

CI also builds signed repositories for updates through `dnf` / `apt`. The new
channel is `https://grek-off.github.io/tailtray`, served by GitHub Pages after a
successful release. The previously verified GitLab Pages channel,
`https://tailtray-657378.gitlab.io`, remains available for installed clients.
Switching repositories is opt-in and does not change the signing key.
See [docs/releases.md](docs/releases.md) for installation, signatures, version
history and the next release procedure.

Build locally from the committed HEAD:

```fish
bash scripts/build-packages.sh source
bash scripts/build-packages.sh rpm  # Fedora: rpm-build, python3, git
bash scripts/build-packages.sh deb  # Debian: dpkg-dev, debhelper-compat, fakeroot, build-essential, python3, git
```

Bash is invoked explicitly because the build script is written for Bash. Output
is written to `dist/`. Uncommitted files are not included in the source archive.

## Testing

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

The project is distributed under the MIT license and is not affiliated with
Tailscale Inc. Tailscale and WireGuard are trademarks of their respective owners.
