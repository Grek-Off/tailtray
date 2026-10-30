# Usage, Authentication and Desktop Setup

[Back to the project overview](../README.md) · [Packages and updates](releases.md)

This guide contains the detailed behavior and desktop setup notes for
TailTray (`Grek-Off/tailtray`). For a first installation, start with the
[README](../README.md#getting-started).

Run the shell examples from the cloned project directory as your normal desktop
user, unless stated otherwise.

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
sudo dnf install tailscale python3-gobject gobject-introspection python3-pyside6 libappindicator-gtk3 libayatana-appindicator-gtk3 libsecret libnotify
```

The GTK fallback uses Ayatana AppIndicator. The source installer's dependency
check currently also requires the legacy `libappindicator-gtk3` package, so the
command above includes both libraries. Native RPM/DEB packages declare the
Ayatana dependency directly and do not require the legacy library.

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
another sign-in. See the [connection-state model](connection-state.md) for
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

## Why this is not a port of the official Windows GUI

The official Windows GUI source is not available. The
[`tailscale/tailscale`](https://github.com/tailscale/tailscale) repository contains
the daemon, CLI and much of the shared logic, but Tailscale states that the
Windows and macOS GUI wrappers are closed source. TailTray therefore implements
its own interface using the public contracts and behavior of the official
[`client/systray`](https://github.com/tailscale/tailscale/blob/main/client/systray/systray.go).
