"""Toolkit-independent operation and refresh coordination for TailTray."""

from __future__ import annotations

from concurrent.futures import Future, Executor
from dataclasses import dataclass
import time
import sys
import os
import pwd
from typing import Callable, Any

from .core import ReconnectPolicy, TailscaleError
from .menu import build_menu, Entry
from .settings import autostart_enabled


@dataclass(frozen=True, slots=True)
class OperationState:
    name: str | None = None
    busy: bool = False
    queued_disconnect: bool = False


class OperationCoordinator:
    """Serialize daemon mutations and fence results from obsolete refreshes."""

    def __init__(self, executor: Executor, dispatch: Callable[[Callable[[], None]], None] | None = None):
        self.executor = executor
        self.dispatch = dispatch or (lambda callback: callback())
        self.state = OperationState()
        self._done: Callable[[str, bool, Any, Exception | None], None] | None = None
        self._mutation_epoch = 0
        self._refresh_serial = 0
        self._shutdown = False
        self._observed_epoch = None

    @property
    def busy(self) -> bool:
        return self.state.busy

    @property
    def mutation_epoch(self) -> int:
        return self._mutation_epoch

    def set_done_handler(self, callback: Callable[[str, bool, Any, Exception | None], None]) -> None:
        self._done = callback

    def start_refresh(self) -> tuple[int, int] | None:
        if self._shutdown or self.busy:
            return None
        self._refresh_serial += 1
        return self._refresh_serial, self._mutation_epoch

    def accept_refresh(self, token: tuple[int, int]) -> bool:
        serial, epoch = token
        accepted = not self._shutdown and epoch == self._mutation_epoch and serial == self._refresh_serial and not self.busy
        if accepted:
            self._observed_epoch = epoch
        return accepted

    @property
    def observation_current(self) -> bool:
        return not self._shutdown and not self.busy and self._observed_epoch == self._mutation_epoch

    def submit(self, name: str, function: Callable[[], Any], *, queue_disconnect: bool = False,
               on_success: Callable | None = None) -> bool:
        if self._shutdown:
            return False
        if self.busy:
            if queue_disconnect and self.state.name != "Disconnecting":
                self.state = OperationState(self.state.name, True, True)
            return False
        self._mutation_epoch += 1
        self.state = OperationState(name, True, False)
        self._active_success = on_success
        try:
            future = self.executor.submit(function)
        except Exception as exc:
            future = Future()
            future.set_exception(exc)
        future.add_done_callback(lambda completed: self._future_done(name, completed))
        return True

    def request_disconnect(self, function: Callable[[], Any]) -> bool:
        self._queued_disconnect_function = function
        return self.submit("Disconnecting", function, queue_disconnect=True)

    def _future_done(self, name: str, future: Future) -> None:
        def finish() -> None:
            if self._shutdown:
                return
            result = None
            error: Exception | None = None
            try:
                result = future.result()
            except Exception as exc:  # completion is reported to the UI, never raised in its event loop
                error = exc
            queued = self.state.queued_disconnect
            success_callback = getattr(self, "_active_success", None)
            self._active_success = None
            self._mutation_epoch += 1
            self.state = OperationState()
            if queued:
                disconnect = getattr(self, "_queued_disconnect_function", None)
                if disconnect is not None:
                    self.submit("Disconnecting", disconnect)
            if self._done is not None:
                try:
                    if not queued and error is None and success_callback is not None:
                        success_callback(result)
                except Exception as callback_error:
                    error = callback_error
                self._done(name, error is None, result, error)

        if self._shutdown:
            return
        try:
            self.dispatch(finish)
        except (RuntimeError, ReferenceError):
            self.shutdown()

    def shutdown(self) -> None:
        self._shutdown = True
        self.state = OperationState()


class OperationControllerMixin:
    """Shared behavior; toolkit adapters provide rendering and dispatch hooks."""

    def _init_operation_controller(self, executor: Executor, dispatch: Callable[[Callable[[], None]], None]) -> None:
        self.reconnect_policy = ReconnectPolicy()
        self.local_user = pwd.getpwuid(os.getuid()).pw_name
        self._operator_attempted: set[tuple[str, ...]] = set()
        self._control_denied = False
        self.action_error = ""
        self.operation_coordinator = OperationCoordinator(executor, dispatch)
        self.operation_coordinator.set_done_handler(self._operation_finished)

    @property
    def operation_busy(self) -> bool:
        return self.operation_coordinator.busy

    @property
    def display_status(self) -> str:
        state = self.operation_coordinator.state
        if state.queued_disconnect:
            return "Disconnect queued — waiting for current operation…"
        if state.busy:
            return f"{state.name}…"
        if self.operation_coordinator.mutation_epoch and not self.operation_coordinator.observation_current:
            return "Updating status…"
        return self.snapshot.display_status

    def _menu_entries(self) -> tuple[Entry, ...]:
        return build_menu(self.snapshot, status=self.display_status, busy=self.operation_busy,
                          operation=self.operation_coordinator.state.name, settings=self.settings,
                          secrets_available=self.secrets.available, has_key=bool(self.auth_key),
                          autostart=autostart_enabled(), current=self.operation_coordinator.observation_current,
                          control_required=self.control_required, action_error=self.action_error)

    @property
    def control_required(self) -> bool:
        snapshot = self.snapshot
        return (self._control_denied or snapshot.error_kind == "permission"
                or snapshot.control_error_kind == "permission"
                or (snapshot.operator_user is not None and snapshot.operator_user != self.local_user))

    def _control_context(self) -> tuple[str, ...]:
        profile = next((p.id for p in self.snapshot.profiles if p.selected), "")
        return (profile, self.snapshot.tailnet, self.snapshot.hostname)

    def _maybe_prepare_control(self) -> None:
        if not self.operation_coordinator.observation_current:
            return
        context = self._control_context()
        if not self.control_required:
            if self.snapshot.operator_user == self.local_user:
                # Rearm only after verified recovery, not a timeout/absent field.
                self._operator_attempted.clear()
            return
        if self.snapshot.error_kind not in {"", "permission"}:
            return
        if self._control_denied and self.snapshot.operator_user == self.local_user:
            # A write can fail for reasons operator assignment cannot fix.
            # Offer explicit recovery, never loop polkit + failing login/up.
            return
        if context not in self._operator_attempted:
            self.grant_operator()

    def grant_operator(self, *_args) -> None:
        if self.operation_busy or not self.operation_coordinator.observation_current:
            return
        self._operator_attempted.add(self._control_context())

        def granted(_result) -> None:
            self._control_denied = False
            self.login_token = ""

        self._action("Granting Tailscale control", lambda: self.cli.grant_operator(self.local_user),
                     on_success=granted)

    def interactive_login(self, *_args) -> None:
        if self.operation_busy or not self.operation_coordinator.observation_current:
            return
        if self.control_required:
            self.grant_operator()
            return
        self._set_connection_intent(True)
        self.login_token = "NeedsLogin"
        if self.snapshot.auth_url:
            self._open_login_url(self.snapshot.auth_url)
            return
        self._action("Sign-in", self.cli.begin_sign_in, on_success=self._open_login_url)

    def _menu_activate(self, entry: Entry, checked: bool = False) -> None:
        if not entry.enabled:
            return
        try:
            command, args = entry.command, entry.args
            if command in {"profile", "exit-node"}:
                if not entry.checked:
                    if command == "profile":
                        self.switch_tailnet(args[0])
                    else:
                        self._action("Exit node change", lambda: self.cli.set_exit_node(args[0]))
            elif command == "preference":
                self._action("Preference change", lambda: self.cli.set_preference(args[0], checked))
            elif command == "copy":
                self.copy_address(*args)
            else:
                callbacks = {
                    "connect": self.connect_tailscale, "disconnect": self.disconnect_tailscale,
                    "refresh": self.refresh, "quit": self.quit, "admin": self.open_admin_console,
                    "installation": self.open_installation_guide, "login": self.interactive_login,
                    "full-restart": self.confirm_full_restart, "start-service": self.start_service,
                    "grant-operator": self.grant_operator, "auth-key": self.prompt_auth_key,
                    "forget-key": self.forget_auth_key,
                    "add-tailnet": self.add_tailnet,
                    "auto-login": lambda: self.toggle_automatic_login(checked),
                    "auto-reconnect": lambda: self.toggle_automatic_reconnect(checked),
                    "autostart": lambda: self.toggle_autostart(checked),
                }
                callbacks[command]()
        except Exception as exc:
            self.notify("TailTray", f"Action failed: {exc}")
        finally:
            if not self.operation_coordinator._shutdown:
                self._render_menu()

    def _action(self, description: str, function: Callable[[], Any], *, on_success: Callable | None = None) -> bool:
        started = self.operation_coordinator.submit(description, function, on_success=on_success)
        if hasattr(self, "_render_menu"):
            self._render_menu()
        return started

    def _operation_finished(self, description: str, succeeded: bool, result: Any, error: Exception | None) -> None:
        if error is not None:
            self.action_error = f"{description} failed: {error}"
            if isinstance(error, TailscaleError) and error.kind == "permission":
                self._control_denied = True
                self.login_token = ""
        else:
            self.action_error = ""
        if error is not None and description != "Automatic reconnect":
            self.notify("TailTray", f"{description} failed: {error}")
        elif error is not None:
            print(f"tailtray: automatic reconnect failed: {error}", file=sys.stderr)
        if description in {"Automatic reconnect", "Connecting", "Full restart"}:
            self.reconnect_policy.finish(succeeded=succeeded, now=time.monotonic())
        if description == "Tailnet switch" and succeeded:
            self.login_token = ""
        if hasattr(self, "_render_menu"):
            self._render_menu()
        self._schedule_refresh_after_action()

    def _set_connection_intent(self, want_connected: bool) -> None:
        self.settings["want_connected"] = want_connected
        try:
            self._save_settings(self.settings)
        except OSError as exc:
            self.notify("TailTray", f"Connection choice applies this session but could not be saved: {exc}")

    def _set_auth_key_owner(self, profile_id: str) -> None:
        previous = self.settings.get("auth_key_profile")
        self.settings["auth_key_profile"] = profile_id
        try:
            self._save_settings(self.settings)
        except Exception:
            if previous is None:
                self.settings.pop("auth_key_profile", None)
            else:
                self.settings["auth_key_profile"] = previous
            raise

    def _bind_auth_key(self) -> None:
        """Associate the one optional reusable key with its current local profile."""
        profile = next((p for p in self.snapshot.profiles if p.selected), None)
        # Unknown is deliberately not a wildcard: never use a previous network's
        # key to complete login to a newly selected network.
        self._set_auth_key_owner(profile.id if profile else "unbound")

    def _prepare_profile_change(self) -> None:
        if self.auth_key and not self.settings.get("auth_key_profile"):
            # Bind legacy keys before leaving their original profile. If saving
            # fails, do not switch and lose the only durable association.
            if len(self.snapshot.profiles) == 1:
                self._bind_auth_key()
            else:
                self._set_auth_key_owner("unbound")

    def _store_auth_key(self, key: str) -> None:
        if not any(p.selected for p in self.snapshot.profiles):
            raise ValueError("Sign in to a tailnet before setting its automatic sign-in key")
        # Secret Service and config are separate stores. Fail closed between
        # writes so a failed key replacement cannot relabel the previous key.
        self._set_auth_key_owner("unbound")
        self.auth_key = ""
        self.secrets.set(key)
        self._bind_auth_key()
        self.auth_key = key

    def switch_tailnet(self, profile_id: str) -> None:
        if self.operation_busy or not self.operation_coordinator.observation_current:
            return
        profile = next((p for p in self.snapshot.profiles if p.id == profile_id), None)
        if profile is None or profile.selected:
            return
        self._prepare_profile_change()
        self._action("Tailnet switch", lambda: self.cli.switch_profile(profile_id),
                     on_success=lambda _result: self._set_connection_intent(True))

    def add_tailnet(self) -> None:
        if self.operation_busy or not self.operation_coordinator.observation_current:
            return
        self._prepare_profile_change()
        # Always request a new profile, not a stale AuthURL or the stored key.
        # Subsequent NeedsLogin polls must not restart this browser login.
        self.login_token = "NeedsLogin"

        def open_login(url: str) -> None:
            self._set_connection_intent(True)
            self._open_login_url(url)

        self._action("Add tailnet", self.cli.begin_interactive_login, on_success=open_login)

    def _auth_key_matches_profile(self) -> bool:
        owner = self.settings.get("auth_key_profile")
        if owner:
            return owner != "unbound" and any(p.selected and p.id == owner for p in self.snapshot.profiles)
        # Compatibility with legacy single-profile setups. Multiple profiles
        # require an explicit binding; missing metadata cannot prove ownership.
        return len(self.snapshot.profiles) == 1 and self.snapshot.profiles[0].selected

    def connect_tailscale(self, *_args) -> None:
        if self.operation_busy:
            return
        self.login_token = ""
        self._set_connection_intent(True)
        self.reconnect_policy.reset()
        self._action("Connecting", self.cli.connect)

    def disconnect_tailscale(self, *_args) -> None:
        # A cancelled browser login must not suppress a later Connect episode.
        self.login_token = ""
        self._set_connection_intent(False)
        if not self.operation_busy:
            self.reconnect_policy.reset()
        self.operation_coordinator.request_disconnect(self.cli.disconnect)
        if hasattr(self, "_render_menu"):
            self._render_menu()

    def _start_expected_connection_action(self, description: str, function: Callable[[], Any]) -> None:
        self._action(description, function)

    def _maybe_auto_reconnect(self) -> None:
        if not self.operation_coordinator.observation_current or self.control_required:
            return
        if self.reconnect_policy.should_attempt(
            self.snapshot,
            enabled=bool(self.settings.get("automatic_reconnect", True)),
            want_connected=bool(self.settings.get("want_connected", True)),
            now=time.monotonic(),
        ):
            self._action("Automatic reconnect", self.cli.connect)

    def _maybe_auto_login(self) -> None:
        snapshot = self.snapshot
        if snapshot.backend_state != "NeedsLogin" or not self.settings.get("automatic_login", True):
            if snapshot.connected:
                self.login_token = ""
            return
        if (not self.settings.get("want_connected", True)
                or not self.operation_coordinator.observation_current
                or snapshot.error or snapshot.error_kind or self.control_required):
            return
        # URL rotation is not a new login episode. Retry remains available via
        # the explicit Sign in action; polling must never spawn browser tabs.
        token = "NeedsLogin"
        if self.login_token == token:
            return
        self.login_token = token
        if self.auth_key and self._auth_key_matches_profile():
            self._action("Automatic sign-in", lambda: self.cli.login_with_auth_key(self.auth_key))
        elif snapshot.auth_url:
            self._open_login_url(snapshot.auth_url)
        else:
            self._action("Sign-in", self.cli.begin_sign_in, on_success=self._open_login_url)

    def _operation_shutdown(self) -> None:
        self.operation_coordinator.shutdown()
