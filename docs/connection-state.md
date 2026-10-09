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
| Running with health warnings | Degraded/uncertain availability; keep Disconnect available. Do not cycle down/up. |
| Running with Self.Online false or absent, no health warnings | Keep connected presentation; use ExitNodeStatus for reported exit-node availability. |
| Stopped, user wants connection | One automatic up attempt with bounded retry delay. |
| Manual Disconnect | Persist disconnected intent; automatic up and login must not undo it. |
| Disconnect during an outstanding command | Record intent immediately, execute down after that command finishes. |
| Starting | Wait for daemon progress; do not launch competing up commands. |
| NeedsLogin | Sign-in flow only when connection is desired; no repeated browser opening from polls. |
| NeedsMachineAuth | Device approval required; repeating login cannot approve the device. |
| Daemon missing/unreachable | Explicit error; no automatic privileged restart. |
| Missing/different Unix operator or control read denied | One polkit operator repair before automatic login/up; cancellation leaves manual recovery. |
| A write denied despite matching operator | Keep actionable error and explicit permission recovery; no automatic polkit/login loop. |
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
| Switch saved tailnet | `tailscale switch PROFILE_ID`; refresh before selecting the new radio or enabling mutations. |
| Add tailnet | `tailscale login --timeout=3s`; open the returned URL, never reuse the previous profile's AuthURL or auth key. |
| Sign in when NeedsLogin | Bare `tailscale up` for the existing profile; preserve saved flags, extract AuthURL even at subprocess timeout. |
| Grant local control | `pkexec tailscale set --operator=UNIX_USER`; invalidate prefs cache and re-observe before login/up. |
| Full restart | down → privileged service restart → wait for daemon → up. |

[Official CLI reference](https://tailscale.com/docs/reference/tailscale-cli).

Tailnet selection uses local profile IDs, not email addresses (one login can
belong to several tailnets). A successful switch restores connection intent;
a failed switch must not reconnect the old network against a previous manual
Disconnect. A queued Disconnect still wins. Login initiated by Add tailnet is
not restarted on subsequent NeedsLogin polls. Manual Disconnect ends that login
episode, so a later Connect can open a fresh login if needed. The optional stored auth key
is associated with its selected profile and is never automatically used when
that profile is absent, different, or unavailable. Key replacement first
persists an unbound marker, so failure between the config and Secret Service
writes cannot label the previous network's key as belonging to the new one.
Profile bindings contain no secrets.

### Operator and login recovery

Verified with installed CLI help/read-only prefs on Tailscale 1.102.5,
2026-10-09. Local Unix operator and the tailnet login are separate facts. The
current process UID (not `$USER` or a tailnet account) identifies the target.
Read-only status can succeed when writes cannot. Optional preference errors
therefore retain primary network status and separately carry control errors.
Absent/malformed operator metadata is unknown, not an unset operator.

A first verified mismatch triggers one serialized polkit request. Automatic
sign-in/reconnect wait for it and for fresh preference verification. Failed or
cancelled repair does not repeat on polls in the same observed context; its
explicit retry remains enabled. Confirmed recovery rearms checks for subsequent
loss/profile changes. Daemon/timeouts never trigger privilege repair. A write
denied even with the matching operator requires an explicit retry, to avoid a
polkit → login failure loop. Errors remain visible in the menu, not just in a
short-lived notification. Manual disconnected intent is unchanged by repair.

`tailscale login` first switches to an empty profile, so it is reserved for
Add tailnet. Existing `NeedsLogin` uses bare `up`; CLI timeout flags must not
reset/revert saved non-default preferences. The bounded subprocess preserves
captured AuthURL output when it times out; an output-free timeout stays an error.

Sources: [Linux operator permissions](https://tailscale.com/docs/reference/troubleshooting/linux/linux-operator-permission),
[1.102.5 login](https://github.com/tailscale/tailscale/blob/v1.102.5/cmd/tailscale/cli/login.go),
[1.102.5 up](https://github.com/tailscale/tailscale/blob/v1.102.5/cmd/tailscale/cli/up.go).

The toolkit-independent menu model derives labels, commands and enablement
from the same observation and operation state. Qt and GTK reconcile stable
item keys in place. Root menus and fixed submenus are never replaced; neither
backend tracks an open/closed latch. In particular, Plasma need not deliver
a `closed` event for Disconnect to turn into Connect. Programmatic checkbox
updates do not submit commands; selections remain observed, not optimistic.
Removed items lose their callbacks before destruction.

### KDE dynamic submenu cache

When membership changes (items added or removed at any depth), the Qt backend
also emits the standard StatusNotifierItem `NewMenu` signal. Plasma's importer
can retain an `m_idsRefreshedByAboutToShow` marker after first fetching an empty
local submenu; that consumes the next real `LayoutUpdated` even much later.
KDE's exporter batches changes into one signal per parent, so the first newly
available exit node can remain absent in Plasma despite being in `GetLayout`.
`NewMenu` invalidates the host importer, including that stale marker.

Our QMenus and surviving QActions remain stable. The host may dismiss its menu
once when membership changes; ordinary polling, label/checkmark changes and
online-status reordering do not invalidate it. A CLI refresh cannot repair an
already stale host cache if membership does not change.

Verified against Qt 6.11.2 and Plasma 6.7.5 on 2026-09-15. Recheck this
compatibility handling when Plasma changes its importer:
[importer](https://github.com/KDE/plasma-workspace/blob/master/libdbusmenuqt/dbusmenuimporter.cpp),
[StatusNotifierItem source](https://github.com/KDE/plasma-workspace/blob/master/applets/systemtray/statusnotifieritemsource.cpp).

Saved `exit-node=auto:any` and the current `ExitNodeStatus.ID` are separate:
automatic selection without a candidate is not reported as None or active.
When optional preferences are unavailable their toggles are disabled.
If neither a saved exit preference nor a resolved node is known, show unknown
with no selected radio; missing data must not silently mean None.

## Limits of observation

`Running` means the backend is enabled, not that every destination is reachable.
`ExitNodeStatus.Online` is the daemon's report that the node is alive; it is not
an end-to-end Internet probe. Health warnings make availability uncertain.
`Self.Online` reflects `GetInPollNetMap()`, not tunnel connectivity; false or
missing values alone must not mark the device offline or invalidate explicit
exit-node status. The local device marker follows `BackendState == Running`.
Existing peer tunnels can survive a coordination
server outage, so forcefully restarting them would be counterproductive.

Sources:

- [Tailscale 1.102.3 self-status implementation](https://github.com/tailscale/tailscale/blob/v1.102.3/ipn/ipnlocal/local.go) (checked 2026-09-12; recheck if status semantics change)
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

The export probe also checks first addition, removal and reappearance of exit
nodes with a previously opened submenu, simultaneous device-list changes,
and one `NewMenu` notification per membership update. It runs with both generic
Qt and, when installed, the KDE platform theme; generic Qt alone emits extra
layout signals and can conceal the KDE failure. Renderer/controller tests
check that identical polls and reordering neither invalidate the host cache
nor replace surviving actions. No test invokes live exit-node selection.

The new no-close controller regression fails against commit c69978f with
`'Disconnect after current operation' != 'Connect'` and passes with the
stable-item renderer. This is evidence for the missing-Connect fix, not a
claim that all possible network failures have been simulated.

Real suspend/resume, Wi-Fi loss and Plasma interactions remain desktop
integration checks; unit tests do not establish that every network outage
or third-party exit-node failure has been reproduced.
