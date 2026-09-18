"""Toolkit-independent tray menu: identity and action are separate from labels."""

from dataclasses import dataclass

from .core import Snapshot, preference_value


@dataclass(frozen=True)
class Entry:
    key: str
    label: str = ""
    command: str = ""
    args: tuple = ()
    enabled: bool = True
    checked: bool | None = None
    children: tuple["Entry", ...] | None = None
    separator: bool = False


def build_menu(snapshot: Snapshot, *, status: str, busy: bool, operation: str | None,
               settings: dict, secrets_available: bool, has_key: bool, autostart: bool,
               current: bool = True) -> tuple[Entry, ...]:
    s = snapshot
    editable = current and not busy and not s.error_kind and not s.error
    primary = Entry("connection", "Refresh status", "refresh")
    if busy:
        stopping = operation == "Disconnecting"
        primary = Entry("connection", "Disconnecting…" if stopping else "Disconnect after current operation",
                        "disconnect", enabled=not stopping)
    elif not current:
        pass  # A completed mutation needs a new observation, even after daemon errors.
    elif s.error_kind == "missing":
        primary = Entry("connection", "Open installation guide…", "installation")
    elif s.error_kind == "daemon":
        primary = Entry("connection", "Start Tailscale service…", "start-service")
    elif s.error_kind == "permission":
        primary = Entry("connection", "Grant this user control…", "grant-operator")
    elif editable:
        if s.backend_state == "NeedsLogin":
            primary = Entry("connection", "Sign in…", "login")
        elif s.backend_state == "NeedsMachineAuth":
            primary = Entry("connection", "Approve this device…", "admin")
        elif s.backend_state == "Stopped":
            primary = Entry("connection", "Connect", "connect")
        elif s.connected or s.backend_state == "Starting":
            primary = Entry("connection", "Disconnect", "disconnect")

    devices = tuple(Entry("device:" + d.id, d.label, enabled=bool(d.ips), children=tuple(
        Entry("ip:" + ip, ip, "copy", (ip, d.name)) for ip in d.ips)) for d in s.devices)
    suffix = " · last reported" if s.degraded else ""

    selected_profile = next((p for p in s.profiles if p.selected), None)
    profiles = tuple(Entry("profile:" + p.id, p.tailnet_label, "profile", (p.id,),
                           enabled=editable, checked=p.selected) for p in s.profiles)
    tailnets = profiles + (
        Entry("sep-tailnets", separator=True),
        Entry("add-tailnet", "Add tailnet…", "add-tailnet", enabled=editable),
    )
    options = (
        ("accept-dns", "Use Tailscale DNS", True),
        ("accept-routes", "Accept subnet routes", False),
        ("exit-node-allow-lan-access", "Allow LAN with exit node", False),
        ("shields-up", "Block incoming connections", False),
        ("update-check", "Check for Tailscale updates", True),
    )
    prefs = tuple(Entry("pref:" + name, label, "preference", (name,),
                        enabled=editable and name in s.preferences,
                        checked=preference_value(s.preferences, name, default))
                  for name, label, default in options)
    auth = [Entry("admin", "Tailscale admin console…", "admin")]
    if secrets_available:
        auth.append(Entry("key", "Replace automatic sign-in key…" if has_key else "Set up automatic sign-in…",
                          "auth-key", enabled=editable and selected_profile is not None))
        if has_key:
            auth.append(Entry("forget-key", "Forget automatic sign-in key", "forget-key", enabled=not busy))
    auth.append(Entry("automatic-login", "Sign in automatically when required", "auto-login",
                      checked=bool(settings.get("automatic_login", True))))
    maintenance = (
        Entry("restart", "Full restart…", "full-restart", enabled=current and not busy and s.error_kind != "missing"),
        Entry("refresh", "Refresh status", "refresh", enabled=not busy),
        Entry("version", f"Tailscale {s.version or 'unknown'}", enabled=False),
    )
    more = (
        Entry("preferences", "Preferences", enabled=editable and bool(s.preferences), children=prefs),
        Entry("authentication", "Authentication", children=tuple(auth)),
        Entry("maintenance", "Maintenance", children=maintenance),
        Entry("application", "Application", children=(
            Entry("reconnect", "Reconnect after unexpected disconnects", "auto-reconnect",
                  checked=bool(settings.get("automatic_reconnect", True))),
            Entry("autostart", "Start TailTray on login", "autostart", checked=autostart),
        )),
    )
    # `set` changes saved preferences even while Stopped; enable exit-node
    # selection independently of the network being up.
    exit_setting = s.configured_exit_node
    automatic_exit = exit_setting == "auto:any"
    exit_nodes = (
        Entry("none", "None", "exit-node", ("",), enabled=editable,
              checked=exit_setting == ""),
        Entry("auto", "Automatic (best available)", "exit-node", ("auto:any",), enabled=editable,
              checked=automatic_exit),
        *(Entry("exit:" + n.id, n.label, "exit-node", (n.target,),
                enabled=editable and (n.online or n.selected),
                checked=(n.selected if exit_setting is None else exit_setting in {n.target, n.id, n.name})
                and not automatic_exit) for n in s.exit_nodes),
    )
    detail = "  ·  ".join(part for part in (s.hostname, s.ips[0] if s.ips else "") if part)
    return (
        Entry("status", status, enabled=False),
        Entry("self", detail or "This device", "copy", (s.ips[0], s.hostname) if s.ips else (), enabled=bool(s.ips)),
        Entry("health", f"⚠ {s.health[0]}" if s.health else "", enabled=False),
        Entry("sep-main", separator=True),
        primary,
        Entry("profiles", f"Tailnet: {s.tailnet or (selected_profile.tailnet if selected_profile else '') or 'select'}",
              enabled=editable, children=tailnets),
        Entry("devices", f"Devices ({len(devices)}){suffix}", enabled=bool(devices), children=devices),
        Entry("exit-nodes", s.exit_node_label, enabled=editable, children=exit_nodes),
        Entry("sep-more", separator=True),
        Entry("more", "More", children=more),
        Entry("quit", "Quit TailTray", "quit"),
    )
