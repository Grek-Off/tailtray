# Startup operator and login recovery — 2026-10-09

## Contract and ownership

On startup check the local Unix operator and repair a verified mismatch through
polkit before automatic login/reconnect. Keep actionable errors and explicit
retry after cancellation. Preserve VPN/profile settings and manual Disconnect.
Commit and push to the user's existing private GitLab project.

Main owns implementation/integration/docs. Subagent
`01a11f37-7c15-75b2-9346-a399d21eb819` owns only `tests/test_operator_core.py`
and read-only official source investigation/review; no live VPN mutations.
Base revision: `6405682`; working diff is authoritative until publication.

## Decisions

- Optional preference permission errors are separate from primary network status.
- `operator` absent/malformed means unknown; empty means explicitly unset.
- Current process UID determines the Unix user, not environment/tailnet identity.
- One serialized pkexec operator repair per unresolved observed context; fresh
  successful verification rearms future checks. Cancel/failure leaves manual retry.
- Writes denied with an already matching operator do not cause automatic
  polkit/login loops. Grant control and errors remain visible in Authentication.
- Existing-profile sign-in uses bare up, not login (which creates an empty
  profile). A bounded subprocess retains an AuthURL emitted before timeout.
- Qt and GTK share this behavior; stale refreshes cannot override repair.

## Evidence and remaining steps

- `TAILTRAY_TEST_NATIVE_MENU=1 make check`: 129 tests passed, no skips, including
  native KDE and generic Qt D-Bus operator-cancel/retry/sign-in plus GTK.
- Ruff passed in existing read-only Fedora verification container.
- Tailscale 1.102.5 read-only runtime snapshot: Running, operator matches local
  user, no control error, Disconnect enabled. No live down/up/login/set was run.
- Native D-Bus test initially failed because clicks are asynchronously delivered;
  it now waits for dispatch exactly like the existing connection/profile probe.
- Private GitLab project 86100130 matches origin grekov-labs/tailtray.
- Installed modules differ from checkout. Elevated `sudo -n -v` requires a
  password: installation is not done, and a real denied-polkit/login session
  has not been exercised. User can run the existing Fedora installer.
- Source/test branch accepted: 10 isolated core tests, included in the full suite.
  Reviewer confirmed existing-profile bare up and no stale/docs URL acceptance;
  unknown-operator and matching-operator write-denial policies are tested.
  Subagent is finished and closed; no further writers remain.
- Login errors cannot become success by containing a documentation/stale URL;
  non-default setting errors mentioning --operator are not permission failures.
- Implementation commit: `d1f30a4`. Initial push was rejected (fetch first):
  remote main contained CI changes through `476e80a`. Fetched and inspected;
  merged normally without conflict or history rewriting. These changes restore
  shared build runners and adapt native probes for KDE exporter ID renumbering.
  Operator/login D-Bus probes also resolve current IDs across membership changes;
  local QAction identity is checked separately by controller regressions.
- Before final publication, reverify merged tests and push; verify refs.
  Do not treat this pre-publication checkpoint as proof of those later actions.

## Sources

- https://tailscale.com/docs/reference/troubleshooting/linux/linux-operator-permission
- https://github.com/tailscale/tailscale/blob/v1.102.5/cmd/tailscale/cli/login.go
- https://github.com/tailscale/tailscale/blob/v1.102.5/cmd/tailscale/cli/up.go

Read-only CLI help and primary sources checked 2026-10-09. Knowledge MCP was not
available; its fallback corpus points to an unavailable Windows path on this
Linux host. Prior context was verified with repository docs/checkpoints instead.
