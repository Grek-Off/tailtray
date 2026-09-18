"""GTK/AppIndicator user interface for TailTray."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import fcntl
import getpass
import os
from pathlib import Path
import subprocess
import sys
import webbrowser

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as AppIndicator
from gi.repository import Gdk, GLib, Gtk

from .core import (
    Snapshot,
    TailscaleCLI,
    TailscaleError,
    privileged_operator_command,
    privileged_restart_service_command,
    privileged_start_service_command,
)
from .controller import OperationControllerMixin
from .secrets import AuthKeyStore
from .settings import load_settings, save_settings, set_autostart
from .gtk_menu import GtkMenu


APP_ID = "io.github.tailtray"
REFRESH_SECONDS = 5
TRAY_ICON_PREFIX = "tailtray-hc-v2"


def _tray_icon_directory() -> Path:
    """Return a private icon path so Plasma cannot reuse themed/cached icons."""
    development_icons = Path(__file__).resolve().parents[2] / "data" / "tray-icons"
    if development_icons.exists():
        return development_icons
    return Path("/usr/share/tailtray/icons")


class TailTrayApp(OperationControllerMixin):
    def __init__(self) -> None:
        self.cli = TailscaleCLI()
        self.secrets = AuthKeyStore()
        self.auth_key = self._load_auth_key()
        self.settings = load_settings()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tailtray")
        self.snapshot = Snapshot()
        self.refresh_running = False
        self.login_token = ""
        self.menu: Gtk.Menu | None = None
        self.indicator_revision: tuple[str, str] | None = None
        self.lock_handle = self._acquire_singleton_lock()
        self._init_operation_controller(self.executor, lambda callback: GLib.idle_add(callback))

        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            f"{TRAY_ICON_PREFIX}-disconnected",
            AppIndicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        tray_icons = _tray_icon_directory()
        if tray_icons.exists():
            self.indicator.set_icon_theme_path(str(tray_icons))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("TailTray")
        self.menu_renderer = GtkMenu(self._menu_activate)
        self.menu = self.menu_renderer.menu
        self._render_menu()
        self.indicator.set_menu(self.menu)

        GLib.timeout_add_seconds(REFRESH_SECONDS, self._refresh_timer)
        self.refresh()

    @staticmethod
    def _save_settings(settings) -> None:
        save_settings(settings)

    def _schedule_refresh_after_action(self) -> None:
        GLib.timeout_add(350, self._refresh_once)

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
            self.menu_renderer.update(self._menu_entries())

    def _refresh_timer(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self, *_args) -> None:
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
        future.add_done_callback(lambda completed: GLib.idle_add(self._finish_refresh, token, completed))

    def _finish_refresh(self, token, future: Future) -> bool:
        self.refresh_running = False
        if not self.operation_coordinator.accept_refresh(token):
            return GLib.SOURCE_REMOVE
        try:
            snapshot = future.result()
        except Exception as exc:  # defensive: keep the tray alive
            snapshot = Snapshot(error=str(exc), error_kind="command")
        self.snapshot = snapshot
        self._update_indicator()
        self._render_menu()
        self._maybe_auto_login()
        self._maybe_auto_reconnect()

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
        icon = f"{TRAY_ICON_PREFIX}-{state}"
        revision = (icon, snapshot.display_status)
        if revision == self.indicator_revision:
            return
        self.indicator.set_icon_full(icon, snapshot.display_status)
        self.indicator.set_title(snapshot.display_status)
        self.indicator_revision = revision

    def _refresh_once(self) -> bool:
        self.refresh()
        return GLib.SOURCE_REMOVE

    def toggle_automatic_login(self, checked: bool) -> None:
        self.settings["automatic_login"] = checked
        save_settings(self.settings)
        if checked:
            self.login_token = ""
            self._maybe_auto_login()

    def toggle_automatic_reconnect(self, enabled: bool) -> None:
        self.settings["automatic_reconnect"] = enabled
        save_settings(self.settings)
        if enabled and not self.operation_busy:
            self._maybe_auto_reconnect()

    def toggle_autostart(self, checked: bool) -> None:
        try:
            set_autostart(checked)
        except Exception as exc:
            self.notify("TailTray", f"Could not change autostart: {exc}")

    def copy_ip(self, *_args) -> None:
        if not self.snapshot.ips:
            return
        self.copy_address(self.snapshot.ips[0], self.snapshot.hostname or "this device")

    def copy_address(self, address: str, device_name: str) -> None:
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(address, -1)
        self.notify("TailTray", f"Copied {device_name}: {address}")

    def interactive_login(self, *_args) -> None:
        if self.operation_busy:
            return
        self._set_connection_intent(True)
        if self.snapshot.auth_url:
            webbrowser.open(self.snapshot.auth_url)
            return
        self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

    @staticmethod
    def open_installation_guide() -> None:
        webbrowser.open("https://tailscale.com/download/linux")

    @staticmethod
    def open_admin_console(*_args) -> None:
        webbrowser.open("https://login.tailscale.com/admin/machines")

    def _open_login_url(self, url: str) -> None:
        if url:
            webbrowser.open(url)
        else:
            self.notify("TailTray", "No sign-in URL was returned")

    def prompt_auth_key(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Automatic Tailscale sign-in")
        dialog.set_default_size(520, 170)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save securely", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        label = Gtk.Label(
            label=(
                "Paste a reusable, preferably pre-authorized auth key. It will be stored\n"
                "in GNOME Keyring and used only when tailscaled genuinely requires sign-in."
            )
        )
        label.set_xalign(0)
        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_placeholder_text("tskey-auth-…")
        link = Gtk.LinkButton.new_with_label("https://login.tailscale.com/admin/settings/keys", "Create an auth key")
        content.pack_start(label, True, True, 8)
        content.pack_start(entry, True, True, 8)
        content.pack_start(link, True, True, 8)
        dialog.show_all()
        response = dialog.run()
        key = entry.get_text().strip()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
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

    def forget_auth_key(self, *_args) -> None:
        try:
            self.secrets.clear()
            self.auth_key = ""
            self.notify("TailTray", "Automatic sign-in key removed")
        except Exception as exc:
            self.notify("TailTray", f"Could not remove key: {exc}")
        self.refresh()

    def start_service(self, *_args) -> None:
        self._privileged_action("Starting Tailscale service", privileged_start_service_command())

    def confirm_full_restart(self, *_args) -> None:
        if not self.operation_coordinator.observation_current:
            return
        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Fully restart Tailscale?",
        )
        dialog.format_secondary_text(
            "This runs tailscale down, restarts tailscaled.service, then runs tailscale up. "
            "All Tailscale connections will be interrupted briefly."
        )
        dialog.add_button("Restart", Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK and self.operation_coordinator.observation_current:
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

    def grant_operator(self, *_args) -> None:
        self._privileged_action("Granting Tailscale control", privileged_operator_command(getpass.getuser()))

    def _privileged_action(self, description: str, argv: list[str]) -> None:
        def run() -> None:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
            if completed.returncode:
                raise TailscaleError((completed.stderr or completed.stdout).strip() or description)

        self._action(description, run)

    @staticmethod
    def notify(title: str, message: str) -> None:
        try:
            subprocess.Popen(
                ["notify-send", "--app-name=TailTray", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def quit(self, *_args) -> None:
        self._operation_shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)
        Gtk.main_quit()


def main() -> int:
    try:
        app = TailTrayApp()
    except RuntimeError as exc:
        print(f"tailtray: {exc}", file=sys.stderr)
        return 1
    Gtk.main()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
