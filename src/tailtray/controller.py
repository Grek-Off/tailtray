"""Toolkit-independent operation and refresh coordination for TailTray."""

from __future__ import annotations

from concurrent.futures import Future, Executor
from dataclasses import dataclass
import time
import sys
from typing import Callable, Any

from .core import ReconnectPolicy


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
        return self.snapshot.display_status

    def _action(self, description: str, function: Callable[[], Any], *, on_success: Callable | None = None) -> bool:
        started = self.operation_coordinator.submit(description, function, on_success=on_success)
        if self.operation_busy and hasattr(self, "_set_busy_controls"):
            self._set_busy_controls()
        return started

    def _operation_finished(self, description: str, succeeded: bool, result: Any, error: Exception | None) -> None:
        if error is not None and description != "Automatic reconnect":
            self.notify("TailTray", f"{description} failed: {error}")
        elif error is not None:
            print(f"tailtray: automatic reconnect failed: {error}", file=sys.stderr)
        if description in {"Automatic reconnect", "Connecting", "Full restart"}:
            self.reconnect_policy.finish(succeeded=succeeded, now=time.monotonic())
        if description == "Account switch" and succeeded:
            self.login_token = ""
        self.menu_revision = None
        self._schedule_refresh_after_action()

    def _set_connection_intent(self, want_connected: bool) -> None:
        self.settings["want_connected"] = want_connected
        try:
            self._save_settings(self.settings)
        except OSError as exc:
            self.notify("TailTray", f"Connection choice applies this session but could not be saved: {exc}")

    def connect_tailscale(self, *_args) -> None:
        if self.operation_busy:
            return
        self._set_connection_intent(True)
        self.reconnect_policy.reset()
        self._action("Connecting", self.cli.connect)

    def disconnect_tailscale(self, *_args) -> None:
        self._set_connection_intent(False)
        if not self.operation_busy:
            self.reconnect_policy.reset()
        self.operation_coordinator.request_disconnect(self.cli.disconnect)
        if self.operation_busy and hasattr(self, "_set_busy_controls"):
            self._set_busy_controls()

    def _start_expected_connection_action(self, description: str, function: Callable[[], Any]) -> None:
        self._action(description, function)

    def _maybe_auto_reconnect(self) -> None:
        if not self.operation_coordinator.observation_current:
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
                or snapshot.error or snapshot.error_kind):
            return
        # URL rotation is not a new login episode. Retry remains available via
        # the explicit Sign in action; polling must never spawn browser tabs.
        token = "NeedsLogin"
        if self.login_token == token:
            return
        self.login_token = token
        if self.auth_key:
            self._action("Automatic sign-in", lambda: self.cli.login_with_auth_key(self.auth_key))
        elif snapshot.auth_url:
            self._open_login_url(snapshot.auth_url)
        else:
            self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

    def _operation_shutdown(self) -> None:
        self.operation_coordinator.shutdown()
