"""Native Qt 6 tray interface used on KDE Plasma."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import fcntl
import getpass
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
)

from .core import (
    ReconnectPolicy,
    Snapshot,
    TailscaleCLI,
    TailscaleError,
    preference_value,
    privileged_operator_command,
    privileged_restart_service_command,
    privileged_start_service_command,
)
from .secrets import AuthKeyStore
from .settings import autostart_enabled, load_settings, save_settings, set_autostart
from .status_notifier import MenuOnlyStatusNotifier


APP_ID = "io.github.tailtray"
REFRESH_MILLISECONDS = 5_000
TRAY_ICON_PREFIX = "tailtray-hc-v2"


def _tray_icon_directory() -> Path:
    development_icons = Path(__file__).resolve().parents[2] / "data" / "tray-icons"
    if development_icons.exists():
        return development_icons
    return Path("/usr/share/tailtray/icons")


def _add_action(
    menu: QMenu,
    label: str,
    callback: Callable[[], None] | None = None,
    *,
    enabled: bool = True,
) -> QAction:
    action = QAction(label, menu)
    action.setEnabled(enabled)
    if callback is not None:
        action.triggered.connect(lambda _checked=False: callback())
    menu.addAction(action)
    return action


def _add_menu(parent: QMenu, submenu: QMenu) -> None:
    # QMenu.addMenu() does not document an ownership transfer. Parenting the
    # submenu keeps every exported level alive for as long as the root tree.
    submenu.setParent(parent)
    parent.addMenu(submenu)


class _WorkerBridge(QObject):
    """Marshal Future completions back onto the Qt event-loop thread."""

    refresh_finished = Signal(object)
    action_finished = Signal(str, object, object, object)
    auto_reconnect_finished = Signal(object)


class TailTrayQtApp(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.cli = TailscaleCLI()
        self.secrets = AuthKeyStore()
        self.auth_key = self._load_auth_key()
        self.settings = load_settings()
        self.reconnect_policy = ReconnectPolicy()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tailtray")
        self.snapshot = Snapshot()
        self.refresh_running = False
        self.login_token = ""
        self.menu: QMenu | None = None
        self.menu_revision: tuple[object, ...] | None = None
        self.indicator_revision: tuple[str, str] | None = None
        self.menu_open = False
        self.menu_rebuild_pending = False
        self.status_notifier: MenuOnlyStatusNotifier | None = None
        self.icon_path = Path()
        self.lock_handle = self._acquire_singleton_lock()

        self.bridge = _WorkerBridge(self)
        self.bridge.refresh_finished.connect(self._finish_refresh)
        self.bridge.action_finished.connect(self._finish_action)
        self.bridge.auto_reconnect_finished.connect(self._finish_auto_reconnect)

        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("TailTray")
        self._set_icon("disconnected")
        self._set_menu(self._loading_menu())
        self.tray.show()
        try:
            self.status_notifier = MenuOnlyStatusNotifier.install(self.icon_path, self)
        except RuntimeError as exc:
            print(f"tailtray: native left-click menu is unavailable: {exc}", file=sys.stderr)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(REFRESH_MILLISECONDS)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    @staticmethod
    def _acquire_singleton_lock():
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        path = runtime / f"tailtray-{os.getuid()}.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        handle = os.fdopen(os.open(path, flags, 0o600), "w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError("TailTray is already running") from exc
        return handle

    def _load_auth_key(self) -> str:
        if not self.secrets.available:
            return ""
        try:
            return self.secrets.get()
        except Exception:
            return ""

    def _loading_menu(self) -> QMenu:
        menu = QMenu()
        _add_action(menu, "Loading Tailscale…", enabled=False)
        menu.addSeparator()
        _add_action(menu, "Quit TailTray", self.quit)
        return menu

    def _set_menu(self, menu: QMenu) -> None:
        """Publish a complete menu tree in one operation.

        Plasma talks directly to Qt's StatusNotifierItem implementation. The
        tree is built before publication, and an open tree is never replaced.
        """
        previous = self.menu
        menu.aboutToShow.connect(self._menu_opened)
        menu.aboutToHide.connect(self._menu_closed)
        self.menu = menu
        self.tray.setContextMenu(menu)
        if self.status_notifier is not None:
            self.status_notifier.menu_changed()
        if previous is not None:
            previous.deleteLater()

    def _menu_opened(self) -> None:
        self.menu_open = True

    def _menu_closed(self) -> None:
        self.menu_open = False
        if self.menu_rebuild_pending:
            QTimer.singleShot(0, self._apply_pending_menu)

    def _apply_pending_menu(self) -> None:
        if self.menu_open or not self.menu_rebuild_pending:
            return
        revision = self._menu_revision(self.snapshot)
        self._set_menu(self._build_menu())
        self.menu_revision = revision
        self.menu_rebuild_pending = False

    def _menu_revision(self, snapshot: Snapshot) -> tuple[object, ...]:
        return (
            snapshot,
            self.secrets.available,
            bool(self.auth_key),
            bool(self.settings.get("automatic_login", True)),
            bool(self.settings.get("automatic_reconnect", True)),
            autostart_enabled(),
        )

    def refresh(self) -> None:
        if self.refresh_running:
            return
        self.refresh_running = True
        future = self.executor.submit(self.cli.snapshot)
        future.add_done_callback(self.bridge.refresh_finished.emit)

    def _finish_refresh(self, future: Future) -> None:
        self.refresh_running = False
        try:
            snapshot = future.result()
        except Exception as exc:  # defensive: keep the tray alive
            snapshot = Snapshot(error=str(exc), error_kind="command")
        next_menu_revision = self._menu_revision(snapshot)
        self.snapshot = snapshot
        self._update_indicator()
        if next_menu_revision != self.menu_revision:
            if self.menu_open:
                self.menu_rebuild_pending = True
            else:
                self._set_menu(self._build_menu())
                self.menu_revision = next_menu_revision
                self.menu_rebuild_pending = False
        self._maybe_auto_login()
        self._maybe_auto_reconnect()

    def _set_icon(self, state: str) -> None:
        icon = _tray_icon_directory() / f"{TRAY_ICON_PREFIX}-{state}.png"
        self.icon_path = icon
        self.tray.setIcon(QIcon(str(icon)))
        if self.status_notifier is not None:
            self.status_notifier.set_icon(icon)

    def _update_indicator(self) -> None:
        snapshot = self.snapshot
        if snapshot.exit_node_active:
            state = "exit-node"
        elif snapshot.connected:
            state = "connected"
        elif snapshot.backend_state == "Starting":
            state = "connecting"
        else:
            state = "disconnected"
        revision = (state, snapshot.display_status)
        if revision == self.indicator_revision:
            return
        self._set_icon(state)
        self.tray.setToolTip(snapshot.display_status)
        self.indicator_revision = revision

    def _build_menu(self) -> QMenu:
        snapshot = self.snapshot
        menu = QMenu()
        _add_action(menu, snapshot.display_status, enabled=False)
        if snapshot.hostname or snapshot.ips:
            detail = snapshot.hostname
            if snapshot.ips:
                detail += f"  ·  {snapshot.ips[0]}" if detail else snapshot.ips[0]
            _add_action(menu, detail, self.copy_ip if snapshot.ips else None, enabled=bool(snapshot.ips))
        if snapshot.health:
            _add_action(menu, f"⚠ {snapshot.health[0]}", enabled=False)
        menu.addSeparator()

        if snapshot.error_kind == "missing":
            _add_action(menu, "Open installation guide…", self.open_installation_guide)
        elif snapshot.error_kind == "daemon":
            _add_action(menu, "Start Tailscale service…", self.start_service)
        elif snapshot.error_kind == "permission":
            _add_action(menu, "Grant this user control…", self.grant_operator)
        elif snapshot.needs_login:
            _add_action(menu, "Sign in…", self.interactive_login)
        elif snapshot.connected:
            _add_action(menu, "Disconnect", self.disconnect_tailscale)
        else:
            _add_action(menu, "Connect", self.connect_tailscale)

        if snapshot.devices:
            _add_menu(menu, self._devices_menu(snapshot))
        if snapshot.connected:
            _add_menu(menu, self._exit_nodes_menu(snapshot))

        menu.addSeparator()
        _add_menu(menu, self._more_menu(snapshot))
        _add_action(menu, "Quit TailTray", self.quit)
        return menu

    def _more_menu(self, snapshot: Snapshot) -> QMenu:
        menu = QMenu("More")
        if snapshot.profiles:
            _add_menu(menu, self._profiles_menu(snapshot))
        if snapshot.connected:
            _add_menu(menu, self._preferences_menu(snapshot))
        _add_menu(menu, self._authentication_menu())
        _add_menu(menu, self._maintenance_menu(snapshot))
        _add_menu(menu, self._application_menu())
        return menu

    def _authentication_menu(self) -> QMenu:
        menu = QMenu("Authentication")
        _add_action(menu, "Tailscale admin console…", self.open_admin_console)
        if self.secrets.available:
            menu.addSeparator()
            if self.auth_key:
                _add_action(menu, "Replace automatic sign-in key…", self.prompt_auth_key)
                _add_action(menu, "Forget automatic sign-in key", self.forget_auth_key)
            else:
                _add_action(menu, "Set up automatic sign-in…", self.prompt_auth_key)

        automatic = QAction("Sign in automatically when required", menu)
        automatic.setCheckable(True)
        automatic.setChecked(bool(self.settings.get("automatic_login", True)))
        automatic.triggered.connect(self.toggle_automatic_login)
        menu.addAction(automatic)
        return menu

    def _maintenance_menu(self, snapshot: Snapshot) -> QMenu:
        menu = QMenu("Maintenance")
        if not snapshot.error_kind and not snapshot.needs_login:
            _add_action(menu, "Full restart…", self.confirm_full_restart)
        _add_action(menu, "Refresh status", self.refresh)
        if snapshot.version:
            menu.addSeparator()
            _add_action(menu, f"Tailscale {snapshot.version}", enabled=False)
        return menu

    def _application_menu(self) -> QMenu:
        menu = QMenu("Application")
        reconnect = QAction("Reconnect after unexpected disconnects", menu)
        reconnect.setCheckable(True)
        reconnect.setChecked(bool(self.settings.get("automatic_reconnect", True)))
        reconnect.triggered.connect(self.toggle_automatic_reconnect)
        menu.addAction(reconnect)
        menu.addSeparator()
        startup = QAction("Start TailTray on login", menu)
        startup.setCheckable(True)
        startup.setChecked(autostart_enabled())
        startup.triggered.connect(self.toggle_autostart)
        menu.addAction(startup)
        return menu

    def _profiles_menu(self, snapshot: Snapshot) -> QMenu:
        selected = next((profile for profile in snapshot.profiles if profile.selected), None)
        menu = QMenu(f"Account: {selected.label if selected else 'select'}")
        group = QActionGroup(menu)
        group.setExclusive(True)
        for profile in snapshot.profiles:
            action = QAction(profile.label, menu)
            action.setCheckable(True)
            action.setChecked(profile.selected)
            action.triggered.connect(
                lambda checked, profile_id=profile.id, already_selected=profile.selected: self.switch_profile(
                    checked, profile_id, already_selected
                )
            )
            group.addAction(action)
            menu.addAction(action)
        return menu

    def _devices_menu(self, snapshot: Snapshot) -> QMenu:
        menu = QMenu(f"Devices ({len(snapshot.devices)})")
        for device in snapshot.devices:
            addresses = QMenu(device.label, menu)
            for address in device.ips:
                _add_action(
                    addresses,
                    address,
                    lambda ip=address, name=device.name: self.copy_address(ip, name),
                )
            addresses.menuAction().setEnabled(bool(device.ips))
            menu.addMenu(addresses)
        return menu

    def _exit_nodes_menu(self, snapshot: Snapshot) -> QMenu:
        selected = next((node for node in snapshot.exit_nodes if node.selected), None)
        selected_label = selected.name if selected else "None"
        if selected is not None and not snapshot.exit_node_active:
            selected_label += " (unavailable)"
        menu = QMenu(f"Exit node: {selected_label}")
        group = QActionGroup(menu)
        group.setExclusive(True)

        none = QAction("None", menu)
        none.setCheckable(True)
        none.setChecked(not snapshot.exit_node_id)
        none.triggered.connect(lambda checked: self.select_exit_node(checked, "", not snapshot.exit_node_id))
        group.addAction(none)
        menu.addAction(none)

        automatic = QAction("Automatic (best available)", menu)
        automatic.setCheckable(True)
        automatic.triggered.connect(lambda checked: self.select_exit_node(checked, "auto:any", False))
        group.addAction(automatic)
        menu.addAction(automatic)
        menu.addSeparator()

        if not snapshot.exit_nodes:
            _add_action(menu, "No exit nodes available", enabled=False)
        for node in snapshot.exit_nodes:
            action = QAction(node.label, menu)
            action.setCheckable(True)
            action.setChecked(node.selected)
            action.setEnabled(node.online)
            action.triggered.connect(
                lambda checked, target=node.target, already_selected=node.selected: self.select_exit_node(
                    checked, target, already_selected
                )
            )
            group.addAction(action)
            menu.addAction(action)
        return menu

    def _preferences_menu(self, snapshot: Snapshot) -> QMenu:
        menu = QMenu("Preferences")
        options = (
            ("accept-dns", "Use Tailscale DNS", True),
            ("accept-routes", "Accept subnet routes", False),
            ("exit-node-allow-lan-access", "Allow LAN with exit node", False),
            ("shields-up", "Block incoming connections", False),
            ("update-check", "Check for Tailscale updates", True),
        )
        for name, label, default in options:
            current = preference_value(snapshot.preferences, name, default)
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(current)
            action.triggered.connect(
                lambda checked, preference=name, old_value=current: self.toggle_preference(
                    checked, preference, old_value
                )
            )
            menu.addAction(action)
        return menu

    def _action(
        self,
        description: str,
        function: Callable[[], None],
        *,
        on_success: Callable | None = None,
        on_complete: Callable[[bool], None] | None = None,
    ) -> None:
        future = self.executor.submit(function)
        future.add_done_callback(
            lambda completed: self.bridge.action_finished.emit(description, completed, on_success, on_complete)
        )

    def _finish_action(
        self,
        description: str,
        future: Future,
        on_success: Callable | None,
        on_complete: Callable[[bool], None] | None,
    ) -> None:
        succeeded = False
        try:
            result = future.result()
            if on_success is not None:
                on_success(result)
        except Exception as exc:
            self.notify("TailTray", f"{description} failed: {exc}")
        else:
            succeeded = True
        if on_complete is not None:
            on_complete(succeeded)
        self.menu_revision = None
        QTimer.singleShot(350, self.refresh)

    def _set_connection_intent(self, want_connected: bool) -> None:
        self.settings["want_connected"] = want_connected
        save_settings(self.settings)

    def connect_tailscale(self) -> None:
        self._set_connection_intent(True)
        if self.reconnect_policy.in_flight:
            return
        self.reconnect_policy.reset()
        self._start_expected_connection_action("Connecting", self.cli.connect)

    def disconnect_tailscale(self) -> None:
        self._set_connection_intent(False)
        self.reconnect_policy.reset()
        self._action("Disconnecting", self.cli.disconnect)

    def _start_expected_connection_action(self, description: str, function: Callable[[], None]) -> None:
        if self.reconnect_policy.in_flight:
            return
        self.reconnect_policy.in_flight = True

        def complete(success: bool) -> None:
            self.reconnect_policy.finish(succeeded=success, now=time.monotonic())

        self._action(description, function, on_complete=complete)

    def _maybe_auto_reconnect(self) -> None:
        if not self.reconnect_policy.should_attempt(
            self.snapshot,
            enabled=bool(self.settings.get("automatic_reconnect", True)),
            want_connected=bool(self.settings.get("want_connected", True)),
            now=time.monotonic(),
        ):
            return
        future = self.executor.submit(self.cli.connect)
        future.add_done_callback(self.bridge.auto_reconnect_finished.emit)

    def _finish_auto_reconnect(self, future: Future) -> None:
        try:
            future.result()
        except Exception as exc:
            delay = self.reconnect_policy.finish(succeeded=False, now=time.monotonic())
            print(f"tailtray: automatic reconnect failed; retrying in {delay:g}s: {exc}", file=sys.stderr)
        else:
            self.reconnect_policy.finish(succeeded=True, now=time.monotonic())
        self.menu_revision = None
        QTimer.singleShot(350, self.refresh)

    def switch_profile(self, checked: bool, profile_id: str, already_selected: bool) -> None:
        if checked and not already_selected:
            self._action("Account switch", lambda: self.cli.switch_profile(profile_id))

    def select_exit_node(self, checked: bool, target: str, already_selected: bool) -> None:
        if checked and not already_selected:
            self._action("Exit node change", lambda: self.cli.set_exit_node(target))

    def toggle_preference(self, checked: bool, name: str, old_value: bool) -> None:
        if checked != old_value:
            self._action("Preference change", lambda: self.cli.set_preference(name, checked))

    def toggle_automatic_login(self, checked: bool) -> None:
        self.settings["automatic_login"] = checked
        save_settings(self.settings)
        if checked:
            self.login_token = ""
            self._maybe_auto_login()

    def toggle_automatic_reconnect(self, checked: bool) -> None:
        self.settings["automatic_reconnect"] = checked
        save_settings(self.settings)
        if not self.reconnect_policy.in_flight:
            self.reconnect_policy.reset()
        if checked and not self.reconnect_policy.in_flight:
            self._maybe_auto_reconnect()

    def toggle_autostart(self, checked: bool) -> None:
        try:
            set_autostart(checked)
        except Exception as exc:
            self.notify("TailTray", f"Could not change autostart: {exc}")

    def copy_ip(self) -> None:
        if self.snapshot.ips:
            self.copy_address(self.snapshot.ips[0], self.snapshot.hostname or "this device")

    def copy_address(self, address: str, device_name: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(address)
        self.notify("TailTray", f"Copied {device_name}: {address}")

    @staticmethod
    def open_installation_guide() -> None:
        QDesktopServices.openUrl(QUrl("https://tailscale.com/download/linux"))

    @staticmethod
    def open_admin_console() -> None:
        QDesktopServices.openUrl(QUrl("https://login.tailscale.com/admin/machines"))

    def interactive_login(self) -> None:
        if self.snapshot.auth_url:
            QDesktopServices.openUrl(QUrl(self.snapshot.auth_url))
            return
        self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

    def _open_login_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            self.notify("TailTray", "No sign-in URL was returned")

    def _maybe_auto_login(self) -> None:
        snapshot = self.snapshot
        if not self.settings.get("automatic_login", True) or not snapshot.needs_login:
            if snapshot.connected:
                self.login_token = ""
            return
        token = f"{snapshot.backend_state}:{snapshot.auth_url}"
        if self.login_token == token:
            return
        self.login_token = token
        if self.auth_key:
            auth_key = self.auth_key
            self._action("Automatic sign-in", lambda: self.cli.login_with_auth_key(auth_key))
        elif snapshot.auth_url:
            QDesktopServices.openUrl(QUrl(snapshot.auth_url))
        else:
            self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

    def prompt_auth_key(self) -> None:
        dialog = QDialog()
        dialog.setWindowTitle("Automatic Tailscale sign-in")
        dialog.resize(520, 180)
        layout = QVBoxLayout(dialog)
        explanation = QLabel(
            "Paste a reusable, preferably pre-authorized auth key. It will be stored "
            "in the desktop keyring and used only when tailscaled genuinely requires sign-in."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        entry = QLineEdit()
        entry.setEchoMode(QLineEdit.EchoMode.Password)
        entry.setPlaceholderText("tskey-auth-…")
        layout.addWidget(entry)
        link = QLabel('<a href="https://login.tailscale.com/admin/settings/keys">Create an auth key</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = entry.text().strip()
        if not key.startswith("tskey-"):
            self.notify("TailTray", "That does not look like a Tailscale auth key")
            return
        try:
            self.secrets.set(key)
        except Exception as exc:
            self.notify("TailTray", f"Could not save key: {exc}")
            return
        self.auth_key = key
        self.login_token = ""
        self.menu_revision = None
        self.notify("TailTray", "Automatic sign-in key saved in the desktop keyring")
        self.refresh()

    def forget_auth_key(self) -> None:
        try:
            self.secrets.clear()
            self.auth_key = ""
            self.menu_revision = None
            self.notify("TailTray", "Automatic sign-in key removed")
        except Exception as exc:
            self.notify("TailTray", f"Could not remove key: {exc}")
        self.refresh()

    def start_service(self) -> None:
        self._privileged_action("Starting Tailscale service", privileged_start_service_command())

    def confirm_full_restart(self) -> None:
        answer = QMessageBox.warning(
            None,
            "Fully restart Tailscale?",
            "This runs tailscale down, restarts tailscaled.service, then runs tailscale up. "
            "All Tailscale connections will be interrupted briefly.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Ok:
            self._set_connection_intent(True)
            if self.reconnect_policy.in_flight:
                self.notify("TailTray", "A connection attempt is already running")
                return
            self.reconnect_policy.reset()
            self._start_expected_connection_action("Full restart", self._full_restart)

    def _full_restart(self) -> None:
        def restart_service() -> None:
            completed = subprocess.run(
                privileged_restart_service_command(),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip()
                raise TailscaleError(detail or "Could not restart tailscaled.service")

        self.cli.full_restart(restart_service)

    def grant_operator(self) -> None:
        self._privileged_action("Granting Tailscale control", privileged_operator_command(getpass.getuser()))

    def _privileged_action(self, description: str, argv: list[str]) -> None:
        def run() -> None:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
            if completed.returncode:
                raise TailscaleError((completed.stderr or completed.stdout).strip() or description)

        self._action(description, run)

    def notify(self, title: str, message: str) -> None:
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5_000)

    def quit(self) -> None:
        self.refresh_timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.tray.hide()
        QApplication.instance().quit()


def main() -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("TailTray")
    application.setDesktopFileName(APP_ID)
    application.setQuitOnLastWindowClosed(False)
    try:
        controller = TailTrayQtApp()
    except RuntimeError as exc:
        print(f"tailtray: {exc}", file=sys.stderr)
        return 1
    result = application.exec()
    del controller
    return result


if __name__ == "__main__":
    raise SystemExit(main())
