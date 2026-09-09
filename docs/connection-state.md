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
suite. The private-session D-Bus subprocess exercises repeated activation
without touching the desktop's session bus. Qt dependencies must be installed
in CI so these tests run instead of being skipped.

Real suspend/resume, Wi-Fi loss and Plasma interactions remain desktop
integration checks; unit tests do not establish that every network outage
or third-party exit-node failure has been reproduced.
