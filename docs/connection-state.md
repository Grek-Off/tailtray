# Connection state and recovery

TailTray controls the official daemon; it does not implement tunnel recovery.
The UI must distinguish three independent facts: the daemon's observed state,
the user's saved connection intent, and the command currently executing.
A successful command is not proof of connectivity: a new status observation
is required after it completes.

## Expected transitions

| Situation | Presentation and action |
| --- | --- |
| Running, healthy | Normal connected state; selected exit node must also report online. |
| Running with health warnings or local node offline | Degraded/uncertain availability; keep Disconnect available. Do not cycle down/up. |
| Stopped, user wants connection | One automatic up attempt with bounded retry delay. |
| Manual Disconnect | Persist disconnected intent; automatic up and login must not undo it. |
| Disconnect during an outstanding command | Record intent immediately, execute down after that command finishes. |
| Starting | Wait for daemon progress; do not launch competing up commands. |
| NeedsLogin | Sign-in flow only when connection is desired; no repeated browser opening from polls. |
| NeedsMachineAuth | Device approval required; repeating login cannot approve the device. |
| Daemon missing/unreachable or permission denied | Explicit error; no automatic privileged restart. |
| Status read timeout or malformed response | Status unknown, not a fabricated disconnected/connected result. |
| Optional preferences/profile read fails | Preserve primary status; unavailable metadata must not erase connectivity. |
| Poll started before a command changes state | Reject that observation after the operation boundary. |
| Quit while work is outstanding | Ignore late UI callbacks and do not launch queued work. |

## CLI contract and menu ownership

Verified against installed Tailscale 1.102.3 help on 2026-09-09; recheck when
changing command construction or supporting a different CLI version.

| Tray action | CLI operation |
| --- | --- |
| Connect | Bare `tailscale up`; preserves saved non-default settings. Subprocess timeout, not an `up` flag. |
| Disconnect | `tailscale down`; does not log out or erase preferences. |
| Exit node / boolean preference | `tailscale set --flag=value`; works while Stopped, changes only that preference. |
| Switch saved account | `tailscale switch PROFILE_ID`. |
| Sign in when NeedsLogin | `tailscale login`; separate from enabling an authenticated node. |
| Full restart | down → privileged service restart → wait for daemon → up. |

[Official CLI reference](https://tailscale.com/docs/reference/tailscale-cli).

The toolkit-independent menu model derives labels, commands and enablement
from the same observation and operation state. Qt and GTK reconcile stable
item keys in place. Root menus and fixed submenus are never replaced; neither
backend tracks an open/closed latch. In particular, Plasma need not deliver
a `closed` event for Disconnect to turn into Connect. Programmatic checkbox
updates do not submit commands; selections remain observed, not optimistic.
Removed items lose their callbacks before destruction.

Saved `exit-node=auto:any` and the current `ExitNodeStatus.ID` are separate:
automatic selection without a candidate is not reported as None or active.
When optional preferences are unavailable their toggles are disabled.
If neither a saved exit preference nor a resolved node is known, show unknown
with no selected radio; missing data must not silently mean None.

## Limits of observation

`Running` means the backend is enabled, not that every destination is reachable.
`ExitNodeStatus.Online` is the daemon's report that the node is alive; it is not
an end-to-end Internet probe. Health warnings or loss of control-plane contact
make availability uncertain. Existing peer tunnels can survive a coordination
server outage, so forcefully restarting them would be counterproductive.

Sources:

- [Tailscale status structure](https://github.com/tailscale/tailscale/blob/main/ipn/ipnstate/ipnstate.go)
- [Coordination server outages](https://tailscale.com/docs/reference/coordination-server-down)
- [Out-of-sync health warning](https://tailscale.com/docs/reference/messages/client/not-in-map-poll)

## Verification

Use controlled futures and fake CLI responses to exercise ordering and failures
without changing the developer's active network. Run `make check` for the full
suite. `TAILTRAY_TEST_NATIVE_MENU=1 make check` additionally tests the actual
Qt `/MenuBar` exporter (five Disconnect/Connect cycles with stable item IDs
and no close events) and native GTK widgets. The earlier activation-only probe
remains a separate regression check. Both use a private session bus and fake
CLI responses, never the desktop's session bus or a live down/up cycle. A
graphical display and dependencies must be present for native checks.

The new no-close controller regression fails against commit c69978f with
`'Disconnect after current operation' != 'Connect'` and passes with the
stable-item renderer. This is evidence for the missing-Connect fix, not a
claim that all possible network failures have been simulated.

Real suspend/resume, Wi-Fi loss and Plasma interactions remain desktop
integration checks; unit tests do not establish that every network outage
or third-party exit-node failure has been reproduced.
