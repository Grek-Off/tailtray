# TailTray missing Connect regression — 2026-09-09

Outcome: repair the Disconnect → Connect cycle and unify tray state handling;
preserve existing tray features and commit implementation and tests.

Base: c69978f. Cause reproduced: menu updates depended on a close-event latch.
The new no-close controller regression fails against that base with
`'Disconnect after current operation' != 'Connect'`.

Accepted implementation: one declarative menu model, stable Qt/GTK item keys,
in-place label/enablement/callback updates, no menu-close dependency. Saved
exit-node preference is distinct from the current selected node. Stale
observations allow refresh but not another state-dependent mutation.

Evidence: `TAILTRAY_TEST_NATIVE_MENU=1 make check` passed 77 tests without skips,
plus compile/shell/XML/desktop checks. Ruff and diff whitespace checks passed.
Native probe exercises real Qt `/MenuBar` GetLayout/Event calls for five
Disconnect/Connect cycles without any close event, on an isolated bus with a
fake daemon. GTK runtime probe also passed. CLI help checked on Tailscale
1.102.3; command mapping and observation limits are in docs/connection-state.md.

Main owns all writes. Read-only CLI audit and menu review performed through
agent-workflow; the restart stale-observation finding was fixed in both UIs.
Final review's unavailable exit-preference finding was also fixed: unknown
data is not presented as None, and has a separate regression test. No review
work remains pending.

Installation remains pending: elevated `sudo -n -v` requires a password.
The installed system copy was not overwritten or restarted. Next user action
is scripts/install-fedora.sh, which installs/verifies the modules and restarts
TailTray. No live VPN disconnect, logout, account or key change was performed.
Actual Wi-Fi outages and physical Plasma mouse interactions remain manual
checks; the tests above do not claim exhaustive network-failure coverage.
