"""Native Qt 6 tray interface used on KDE Plasma."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
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
    Snapshot,
    TailscaleCLI,
    TailscaleError,
    privileged_restart_service_command,
    privileged_start_service_command,
)
from .controller import OperationControllerMixin
from .secrets import AuthKeyStore
from .settings import load_settings, save_settings, set_autostart
from .status_notifier import MenuOnlyStatusNotifier
from .qt_menu import QtMenu


APP_ID = "io.github.tailtray"
REFRESH_MILLISECONDS = 5_000
TRAY_ICON_PREFIX = "tailtray-hc-v2"


def _tray_icon_directory() -> Path:
    development_icons = Path(__file__).resolve().parents[2] / "data" / "tray-icons"
    if development_icons.exists():
        return development_icons
    return Path("/usr/share/tailtray/icons")


class _WorkerBridge(QObject):
    """Marshal Future completions back onto the Qt event-loop thread."""

    refresh_finished = Signal(object)
    dispatch = Signal(object)


class TailTrayQtApp(OperationControllerMixin, QObject):
    def __init__(self) -> None:
        super().__init__()
        self.cli = TailscaleCLI()
        self.secrets = AuthKeyStore()
        self.auth_key = self._load_auth_key()
        self.settings = load_settings()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tailtray")
        self.snapshot = Snapshot()
        self.refresh_running = False
        self.login_token = ""
        self.menu: QMenu | None = None
        self.indicator_revision: tuple[str, str] | None = None
        self.status_notifier: MenuOnlyStatusNotifier | None = None
        self.icon_path = Path()
        self.lock_handle = self._acquire_singleton_lock()

        self.bridge = _WorkerBridge(self)
        self.bridge.refresh_finished.connect(self._finish_refresh)
        self.bridge.dispatch.connect(lambda callback: callback())
        self._init_operation_controller(self.executor, self._dispatch_callback)

        self.tray = QSystemTrayIcon(self)
        self.tray.setToolTip("TailTray")
        self._set_icon("disconnected")
        self.menu_renderer = QtMenu(self._menu_activate)
        self.menu = self.menu_renderer.menu
        self._render_menu()
        self.tray.setContextMenu(self.menu)
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

    def _render_menu(self) -> None:
        if not self.operation_coordinator._shutdown:
            membership_changed = self.menu_renderer.update(self._menu_entries())
            if membership_changed and self.status_notifier is not None:
                # Plasma can swallow the first LayoutUpdated after initially
                # fetching an empty submenu (m_idsRefreshedByAboutToShow).
                # Invalidate that host cache only when membership changes;
                # labels, checkmarks and online-status reordering stay in place.
                self.status_notifier.menu_changed()

    def _dispatch_callback(self, callback) -> None:
        if self.operation_coordinator._shutdown:
            return
        try:
            self.bridge.dispatch.emit(callback)
        except (RuntimeError, ReferenceError):
            self.operation_coordinator.shutdown()

    @staticmethod
    def _save_settings(settings) -> None:
        save_settings(settings)

    def _schedule_refresh_after_action(self) -> None:
        QTimer.singleShot(350, self.refresh)

    def refresh(self) -> None:
        if self.refresh_running:
            return
        token = self.operation_coordinator.start_refresh()
        if token is None:
            return
        self.refresh_running = True
        try:
            future = self.executor.submit(self.cli.snapshot)
        except Exception as exc:
            future = Future()
            future.set_exception(exc)
        future.add_done_callback(lambda completed: self._emit_refresh(token, completed))

    def _emit_refresh(self, token, future) -> None:
        if self.operation_coordinator._shutdown:
            return
        try:
            self.bridge.refresh_finished.emit((token, future))
        except (RuntimeError, ReferenceError):
            self.operation_coordinator.shutdown()

    def _finish_refresh(self, payload) -> None:
        token, future = payload
        self.refresh_running = False
        if not self.operation_coordinator.accept_refresh(token):
            return
        try:
            snapshot = future.result()
        except Exception as exc:  # defensive: keep the tray alive
            snapshot = Snapshot(error=str(exc), error_kind="command")
        self.snapshot = snapshot
        self._update_indicator()
        self._render_menu()
        self._maybe_prepare_control()
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

    def toggle_automatic_login(self, checked: bool) -> None:
        self.settings["automatic_login"] = checked
        save_settings(self.settings)
        if checked:
            self.login_token = ""
            self._maybe_auto_login()

    def toggle_automatic_reconnect(self, checked: bool) -> None:
        self.settings["automatic_reconnect"] = checked
        save_settings(self.settings)
        if checked and not self.operation_busy:
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

    def _open_login_url(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            self.notify("TailTray", "No sign-in URL was returned")

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
            self._store_auth_key(key)
        except Exception as exc:
            self.notify("TailTray", f"Could not save key: {exc}")
            return
        self.auth_key = key
        self.login_token = ""
        self.notify("TailTray", "Automatic sign-in key saved in the desktop keyring")
        self.refresh()

    def forget_auth_key(self) -> None:
        try:
            self.secrets.clear()
            self.auth_key = ""
            self.notify("TailTray", "Automatic sign-in key removed")
        except Exception as exc:
            self.notify("TailTray", f"Could not remove key: {exc}")
        self.refresh()

    def start_service(self) -> None:
        self._privileged_action("Starting Tailscale service", privileged_start_service_command())

    def confirm_full_restart(self) -> None:
        if not self.operation_coordinator.observation_current:
            return
        answer = QMessageBox.warning(
            None,
            "Fully restart Tailscale?",
            "This runs tailscale down, restarts tailscaled.service, then runs tailscale up. "
            "All Tailscale connections will be interrupted briefly.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Ok and self.operation_coordinator.observation_current:
            self._set_connection_intent(True)
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
        self._operation_shutdown()
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
