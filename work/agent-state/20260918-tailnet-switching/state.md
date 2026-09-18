# Tailnet switching — 2026-09-18

## Contract

Expose switching between locally authenticated tailnets in TailTray on Fedora/KDE,
without a new login for a valid saved profile. Support adding a profile through
the official browser login. Preserve Disconnect ordering and network-specific
device, exit-node and preference observations. Commit and push changes.

## Decisions and artifacts

- Root `Tailnet` submenu uses profile IDs; labels distinguish networks sharing a login.
- Saved selection invokes `tailscale switch ID`. New profile invokes `tailscale login`.
- Successful switch waits for fresh status; failure preserves disconnected intent.
- The optional single auth key is bound to a local profile, with fail-closed writes
  across config and Secret Service. The key itself is never stored in config.
- Main changes: core/menu/controller/settings, Qt and GTK key-save adapters,
  controller/model/CLI/settings tests, native D-Bus probe, README and state docs.
- Official behavior checked against local CLI help and
  https://tailscale.com/docs/features/client/fast-user-switching on 2026-09-18.
  Recheck on CLI/profile-contract changes; browser sessions are not local profiles.

## Pre-publication checkpoint

- Base revision: `41ce5d1`; implementation and review complete.
- `TAILTRAY_TEST_NATIVE_MENU=1 make check`: 103 tests passed, no skips, including KDE,
  generic Qt and GTK. Ruff passed in the existing Fedora verification container.
- User's active tailnet was not switched; native probes use private D-Bus and fake CLI.
- Independent review found a stale login token after Add tailnet → queued Disconnect.
  A new regression reproduced the failure. Manual Connect/Disconnect now reset the
  login episode; the repeated-poll deduplication remains. Reviewer confirmed the fix.
- Real read-only CLI snapshot renders the current tailnet and enabled Add tailnet.
- Next publication step: commit and push. On resume compare `git status` and the
  local/remote revisions before retrying; this checkpoint is not an exactly-once log.
- GitLab project API confirms private project `86100130`, `grekov-labs/tailtray`,
  matching origin `git@gitlab.com:grekov-labs/tailtray.git`.
- Installation remains pending: `sudo -n -v` requires a password. Do not claim the
  desktop is updated or that a real cross-tailnet session was exercised.
