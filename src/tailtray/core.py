"""Tailscale state and command layer.

The GUI deliberately delegates networking, identity, and key persistence to
the official ``tailscaled`` daemon.  This module only invokes the official CLI
using argument arrays (never a shell) and converts its JSON into stable local
models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence


LOGIN_URL_RE = re.compile(r"https://[^\s<>\"']+")


class TailscaleError(RuntimeError):
    """A safe, user-displayable failure from the Tailscale CLI."""

    def __init__(self, message: str, *, kind: str = "command", returncode: int = 1):
        super().__init__(message)
        self.kind = kind
        self.returncode = returncode


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


@dataclass(frozen=True, slots=True)
class Profile:
    id: str
    account: str
    tailnet: str = ""
    nickname: str = ""
    selected: bool = False

    @property
    def label(self) -> str:
        primary = self.nickname or self.account or self.id
        if self.tailnet and self.tailnet.casefold() != primary.casefold():
            return f"{primary} ({self.tailnet})"
        return primary


@dataclass(frozen=True, slots=True)
class ExitNode:
    id: str
    name: str
    target: str
    online: bool
    selected: bool = False
    country: str = ""
    city: str = ""

    @property
    def label(self) -> str:
        location = ", ".join(part for part in (self.city, self.country) if part)
        suffix = f" — {location}" if location else ""
        if not self.online:
            suffix += " (offline)"
        return f"{self.name}{suffix}"


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    ips: tuple[str, ...] = ()
    online: bool = False
    os: str = ""
    is_self: bool = False

    @property
    def label(self) -> str:
        marker = "●" if self.online else "○"
        suffix = " (this device)" if self.is_self else ""
        return f"{marker} {self.name}{suffix}"


@dataclass(frozen=True, slots=True)
class Snapshot:
    backend_state: str = "Unknown"
    version: str = ""
    tailnet: str = ""
    hostname: str = ""
    ips: tuple[str, ...] = ()
    auth_url: str = ""
    health: tuple[str, ...] = ()
    profiles: tuple[Profile, ...] = ()
    devices: tuple[Device, ...] = ()
    exit_nodes: tuple[ExitNode, ...] = ()
    exit_node_id: str = ""
    exit_node_online: bool = False
    preferences: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""
    error_kind: str = ""

    @property
    def connected(self) -> bool:
        return self.backend_state == "Running"

    @property
    def needs_login(self) -> bool:
        return self.backend_state in {"NeedsLogin", "NeedsMachineAuth"}

    @property
    def exit_node_active(self) -> bool:
        """Whether the selected exit node is usable in the current IPN state."""
        return self.connected and bool(self.exit_node_id) and self.exit_node_online

    @property
    def display_status(self) -> str:
        if self.error_kind == "missing":
            return "Tailscale is not installed"
        if self.error_kind == "daemon":
            return "Tailscale service is not running"
        if self.error_kind == "permission":
            return "No permission to control Tailscale"
        if self.backend_state == "Running":
            status = f"Connected to {self.tailnet}" if self.tailnet else "Connected"
            if self.exit_node_id and not self.exit_node_active:
                return f"{status} · exit node unavailable"
            return status
        if self.backend_state == "Starting":
            return "Connecting…"
        if self.backend_state == "NeedsMachineAuth":
            return "Device approval required"
        if self.backend_state == "NeedsLogin":
            return "Sign in required"
        if self.backend_state == "Stopped":
            return "Disconnected"
        return self.error or "Tailscale unavailable"


@dataclass(slots=True)
class ReconnectPolicy:
    """Track bounded automatic reconnect attempts independently of either UI."""

    base_delay: float = 5.0
    maximum_delay: float = 60.0
    failures: int = 0
    next_attempt_at: float = 0.0
    in_flight: bool = False

    def should_attempt(
        self,
        snapshot: Snapshot,
        *,
        enabled: bool,
        want_connected: bool,
        now: float,
    ) -> bool:
        if snapshot.connected:
            self.reset()
            return False
        if (
            not enabled
            or not want_connected
            or snapshot.backend_state != "Stopped"
            or self.in_flight
            or now < self.next_attempt_at
        ):
            return False
        self.in_flight = True
        return True

    def finish(self, *, succeeded: bool, now: float) -> float:
        self.in_flight = False
        if succeeded:
            self.reset()
            return 0.0
        self.failures += 1
        exponent = min(self.failures - 1, 16)
        delay = min(self.base_delay * (2**exponent), self.maximum_delay)
        self.next_attempt_at = now + delay
        return delay

    def reset(self) -> None:
        self.failures = 0
        self.next_attempt_at = 0.0
        self.in_flight = False


Runner = Callable[[Sequence[str], float], CommandResult]


def _default_runner(argv: Sequence[str], timeout: float) -> CommandResult:
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C.UTF-8"},
        )
    except FileNotFoundError as exc:
        raise TailscaleError("The tailscale command is not installed", kind="missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise TailscaleError("Tailscale did not respond in time", kind="timeout") from exc
    return CommandResult(completed.stdout, completed.stderr, completed.returncode)


def _classify_error(text: str) -> str:
    lowered = text.casefold()
    if "not installed" in lowered or "no such file" in lowered:
        return "missing"
    if any(part in lowered for part in ("doesn't appear to be running", "not running?", "failed to connect")):
        return "daemon"
    if any(part in lowered for part in ("access denied", "permission denied", "operation not permitted", "operator")):
        return "permission"
    return "command"


def _json(result: CommandResult, operation: str) -> Any:
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise TailscaleError(detail or f"{operation} failed", kind=_classify_error(detail), returncode=result.returncode)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TailscaleError(f"{operation} returned invalid JSON", kind="protocol") from exc


def parse_profiles(payload: Any) -> tuple[Profile, ...]:
    if isinstance(payload, Mapping):
        payload = payload.get("profiles", payload.get("Profiles", []))
    if not isinstance(payload, list):
        return ()
    profiles: list[Profile] = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        profile_id = str(item.get("id") or item.get("ID") or "")
        if not profile_id:
            continue
        profiles.append(
            Profile(
                id=profile_id,
                account=str(item.get("account") or item.get("Account") or ""),
                tailnet=str(item.get("tailnet") or item.get("Tailnet") or ""),
                nickname=str(item.get("nickname") or item.get("Nickname") or ""),
                selected=bool(item.get("selected") if "selected" in item else item.get("Selected", False)),
            )
        )
    return tuple(profiles)


def _peer_values(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        return [peer for peer in payload.values() if isinstance(peer, Mapping)]
    if isinstance(payload, list):
        return [peer for peer in payload if isinstance(peer, Mapping)]
    return []


_GENERIC_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "localhost6",
    "localhost6.localdomain6",
}


def _device_name(node: Mapping[str, Any], fallback: str = "Device") -> str:
    hostname = str(node.get("HostName") or "").rstrip(".")
    dns_name = str(node.get("DNSName") or "").rstrip(".")
    dns_label = dns_name.split(".", 1)[0]
    if not hostname or hostname.casefold() in _GENERIC_HOSTNAMES:
        return dns_label or hostname or str(node.get("ID") or fallback)
    return hostname


def parse_status(payload: Mapping[str, Any], *, profiles: tuple[Profile, ...] = (), preferences: Mapping[str, Any] | None = None) -> Snapshot:
    self_node = payload.get("Self") if isinstance(payload.get("Self"), Mapping) else {}
    tailnet = payload.get("CurrentTailnet") if isinstance(payload.get("CurrentTailnet"), Mapping) else {}
    exit_status = payload.get("ExitNodeStatus") if isinstance(payload.get("ExitNodeStatus"), Mapping) else {}
    selected_id = str(exit_status.get("ID") or "")
    root_ips = payload.get("TailscaleIPs") if isinstance(payload.get("TailscaleIPs"), list) else []
    self_ips = self_node.get("TailscaleIPs") if isinstance(self_node.get("TailscaleIPs"), list) else []
    self_ips = self_ips or root_ips
    devices: list[Device] = []
    self_name = _device_name(self_node, "This device")
    if self_node or self_ips:
        devices.append(
            Device(
                id=str(self_node.get("ID") or "self"),
                name=self_name,
                ips=tuple(str(ip) for ip in self_ips or []),
                online=str(payload.get("BackendState") or "") == "Running",
                os=str(self_node.get("OS") or ""),
                is_self=True,
            )
        )
    nodes: list[ExitNode] = []
    for peer in _peer_values(payload.get("Peer")):
        peer_dns_name = str(peer.get("DNSName") or "").rstrip(".")
        peer_name = _device_name(peer)
        peer_ips = peer.get("TailscaleIPs") if isinstance(peer.get("TailscaleIPs"), list) else []
        devices.append(
            Device(
                id=str(peer.get("ID") or peer.get("PublicKey") or peer_name),
                name=peer_name,
                ips=tuple(str(ip) for ip in peer_ips),
                online=bool(peer.get("Online")),
                os=str(peer.get("OS") or ""),
            )
        )
        if not peer.get("ExitNodeOption"):
            continue
        target = str(peer_ips[0]) if peer_ips else peer_dns_name or peer_name
        location = peer.get("Location") if isinstance(peer.get("Location"), Mapping) else {}
        peer_id = str(peer.get("ID") or "")
        nodes.append(
            ExitNode(
                id=peer_id,
                name=peer_name,
                target=target,
                online=bool(peer.get("Online")),
                selected=bool(peer.get("ExitNode")) or (bool(selected_id) and peer_id == selected_id),
                country=str(location.get("Country") or ""),
                city=str(location.get("City") or ""),
            )
        )
    devices.sort(key=lambda device: (not device.is_self, not device.online, device.name.casefold()))
    nodes.sort(key=lambda node: (not node.online, node.country.casefold(), node.city.casefold(), node.name.casefold()))
    ips = root_ips or self_ips
    health = payload.get("Health") if isinstance(payload.get("Health"), list) else []
    return Snapshot(
        backend_state=str(payload.get("BackendState") or "Unknown"),
        version=str(payload.get("Version") or ""),
        tailnet=str(tailnet.get("Name") or ""),
        hostname=self_name,
        ips=tuple(str(ip) for ip in ips or []),
        auth_url=str(payload.get("AuthURL") or ""),
        health=tuple(str(item) for item in health),
        profiles=profiles,
        devices=tuple(devices),
        exit_nodes=tuple(nodes),
        exit_node_id=selected_id,
        exit_node_online=bool(exit_status.get("Online")) if selected_id else False,
        preferences=preferences or {},
    )


def preference_value(preferences: Mapping[str, Any], name: str, default: bool = False) -> bool:
    candidates = (name, name.replace("-", "_"), "".join(part.capitalize() for part in name.split("-")))
    for candidate in candidates:
        if candidate in preferences:
            value = preferences[candidate]
            if isinstance(value, Mapping) and "value" in value:
                value = value["value"]
            return bool(value)
    return default


class TailscaleCLI:
    """Small compatibility layer around the official ``tailscale`` binary."""

    SAFE_BOOLEAN_PREFS = frozenset(
        {
            "accept-dns",
            "accept-routes",
            "exit-node-allow-lan-access",
            "shields-up",
            "ssh",
            "update-check",
            "auto-update",
        }
    )

    def __init__(self, executable: str = "tailscale", runner: Runner | None = None):
        self.executable = executable
        self._runner = runner or _default_runner

    def _run(self, args: Sequence[str], timeout: float = 15.0, *, check: bool = True) -> CommandResult:
        result = self._runner([self.executable, *args], timeout)
        if check and result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise TailscaleError(detail or "Tailscale command failed", kind=_classify_error(detail), returncode=result.returncode)
        return result

    def snapshot(self) -> Snapshot:
        try:
            status_payload = _json(self._run(["status", "--json"], timeout=8), "tailscale status")
            profiles: tuple[Profile, ...] = ()
            preferences: Mapping[str, Any] = {}
            profile_result = self._run(["switch", "--list", "--json"], timeout=5, check=False)
            if profile_result.returncode == 0:
                try:
                    profiles = parse_profiles(json.loads(profile_result.stdout))
                except json.JSONDecodeError:
                    pass
            pref_result = self._run(["get", "--json", "all"], timeout=5, check=False)
            if pref_result.returncode == 0:
                try:
                    decoded = json.loads(pref_result.stdout)
                    if isinstance(decoded, Mapping):
                        preferences = decoded
                except json.JSONDecodeError:
                    pass
            if not isinstance(status_payload, Mapping):
                raise TailscaleError("tailscale status returned an unexpected value", kind="protocol")
            return parse_status(status_payload, profiles=profiles, preferences=preferences)
        except TailscaleError as exc:
            return Snapshot(error=str(exc), error_kind=exc.kind)

    def connect(self) -> None:
        # With no flags, `tailscale up` restores the saved preferences. Passing
        # even the CLI timeout flag makes Tailscale require every non-default
        # setting (exit node, routes, DNS, and so on) to be repeated.
        self._run(["up"], timeout=25)

    def disconnect(self) -> None:
        self._run(["down"])

    def wait_for_daemon(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: TailscaleError | None = None
        while time.monotonic() < deadline:
            try:
                self._run(["status", "--json"], timeout=2)
                return
            except TailscaleError as exc:
                if exc.kind == "missing":
                    raise
                last_error = exc
                time.sleep(0.4)
        detail = f": {last_error}" if last_error else ""
        raise TailscaleError(f"tailscaled did not become ready after restart{detail}", kind="daemon")

    def full_restart(self, restart_service: Callable[[], None]) -> None:
        self.disconnect()
        restart_service()
        self.wait_for_daemon()
        self.connect()

    def set_exit_node(self, target: str) -> None:
        self._run(["set", f"--exit-node={target}"])

    def set_preference(self, name: str, enabled: bool) -> None:
        if name not in self.SAFE_BOOLEAN_PREFS:
            raise ValueError(f"Unsupported preference: {name}")
        self._run(["set", f"--{name}={'true' if enabled else 'false'}"])

    def switch_profile(self, profile_id: str) -> None:
        if not profile_id:
            raise ValueError("Profile ID cannot be empty")
        self._run(["switch", profile_id], timeout=30)

    def begin_interactive_login(self) -> str:
        result = self._run(["login", "--timeout=3s"], timeout=7, check=False)
        text = f"{result.stdout}\n{result.stderr}"
        matches = LOGIN_URL_RE.findall(text)
        if matches:
            return matches[-1].rstrip(".,;)")
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise TailscaleError(detail or "Could not start sign-in", kind=_classify_error(detail), returncode=result.returncode)
        return ""

    def login_with_auth_key(self, auth_key: str) -> None:
        if not auth_key.startswith("tskey-"):
            raise ValueError("This does not look like a Tailscale auth key")
        runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
        runtime_dir.mkdir(parents=True, exist_ok=True)
        fd, raw_path = tempfile.mkstemp(prefix="tailtray-auth-", dir=runtime_dir)
        path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(auth_key)
                handle.write("\n")
            self._run(["login", f"--auth-key=file:{path}", "--timeout=30s"], timeout=35)
        finally:
            path.unlink(missing_ok=True)


def privileged_operator_command(username: str) -> list[str]:
    if not username or username.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        raise ValueError("Unsafe local username")
    return ["pkexec", "tailscale", "set", f"--operator={username}"]


def privileged_start_service_command() -> list[str]:
    return ["pkexec", "systemctl", "enable", "--now", "tailscaled.service"]


def privileged_restart_service_command() -> list[str]:
    return ["pkexec", "systemctl", "restart", "tailscaled.service"]
