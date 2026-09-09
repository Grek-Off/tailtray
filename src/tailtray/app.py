"""GTK/AppIndicator user interface for TailTray."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import fcntl
import getpass
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable
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
    preference_value,
    privileged_operator_command,
    privileged_restart_service_command,
    privileged_start_service_command,
)
from .controller import OperationControllerMixin
from .secrets import AuthKeyStore
from .settings import autostart_enabled, load_settings, save_settings, set_autostart


APP_ID = "io.github.tailtray"
REFRESH_SECONDS = 5
TRAY_ICON_PREFIX = "tailtray-hc-v2"


def _tray_icon_directory() -> Path:
    """Return a private icon path so Plasma cannot reuse themed/cached icons."""
    development_icons = Path(__file__).resolve().parents[2] / "data" / "tray-icons"
    if development_icons.exists():
        return development_icons
    return Path("/usr/share/tailtray/icons")


def _menu_item(label: str, callback: Callable | None = None, *, sensitive: bool = True) -> Gtk.MenuItem:
    item = Gtk.MenuItem.new_with_label(label)
    item.set_sensitive(sensitive)
    if callback is not None:
        item._activation_handler = item.connect("activate", callback)
    item.show()
    return item


def _separator() -> Gtk.SeparatorMenuItem:
    item = Gtk.SeparatorMenuItem()
    item.show()
    return item


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
        self.menu_open = False
        self.menu_rebuild_pending = False
        self.menu_revision: tuple[object, ...] | None = None
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
        self._set_menu(self._loading_menu())

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

    def _loading_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.append(_menu_item("Loading Tailscale…", sensitive=False))
        menu.append(_separator())
        menu.append(_menu_item("Quit TailTray", self.quit))
        menu.show_all()
        return menu

    def _set_menu(self, menu: Gtk.Menu) -> None:
        # Keep the exported GTK tree stable. Replacing it repeatedly makes KDE
        # invalidate DBusMenu item IDs while the pointer is inside a submenu.
        self.menu = menu
        menu.connect("show", self._menu_opened)
        menu.connect("hide", self._menu_closed)
        menu.connect("deactivate", self._menu_closed)
        self.indicator.set_menu(self.menu)

    def _menu_opened(self, *_args) -> None:
        self.menu_open = True

    def _menu_closed(self, *_args) -> None:
        self.menu_open = False
        if self.menu_rebuild_pending:
            GLib.idle_add(self._apply_pending_menu)

    def _apply_pending_menu(self) -> bool:
        if self.operation_coordinator._shutdown or self.menu_open or not self.menu_rebuild_pending:
            return GLib.SOURCE_REMOVE
        self._set_menu(self._build_menu())
        self.menu_revision = self._menu_revision(self.snapshot)
        self.menu_rebuild_pending = False
        return GLib.SOURCE_REMOVE

    def _menu_revision(self, snapshot: Snapshot) -> tuple[object, ...]:
        return (
            snapshot,
            self.secrets.available,
            bool(self.auth_key),
            bool(self.settings.get("automatic_login", True)),
            bool(self.settings.get("automatic_reconnect", True)),
            autostart_enabled(),
        )

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
        next_menu_revision = self._menu_revision(snapshot)
        self.snapshot = snapshot
        self._update_indicator()
        if next_menu_revision != self.menu_revision:
            if self.menu_open:
                self._update_open_menu(snapshot)
                self.menu_rebuild_pending = True
            else:
                self._set_menu(self._build_menu())
                self.menu_revision = next_menu_revision
                self.menu_rebuild_pending = False
        self._maybe_auto_login()
        self._maybe_auto_reconnect()

    def _update_open_menu(self, snapshot: Snapshot) -> None:
        # Update the headline without invalidating the currently exported tree.
        items = self.menu.get_children() if self.menu is not None else []
        if items and hasattr(items[0], "set_label"):
            items[0].set_label(snapshot.display_status)
        for item in items[1:]:
            label = item.get_label() if hasattr(item, "get_label") else ""
            if label not in {"Quit TailTray", "Refresh status"} and not label.startswith("Devices"):
                item.set_sensitive(False)

    def _set_busy_controls(self) -> None:
        if self.menu is None:
            return
        self.menu.get_children()[0].set_label(self.display_status)
        for item in self.menu.get_children():
            label = item.get_label() if hasattr(item, "get_label") else ""
            if label in {"Connect", "Disconnect", "Sign in…", "Disconnect after current operation"}:
                item.set_label("Disconnect after current operation")
                if hasattr(item, "_activation_handler"):
                    item.disconnect(item._activation_handler)
                item._activation_handler = item.connect("activate", self.disconnect_tailscale)
                item.set_sensitive(True)
                continue
            if label not in {"Quit TailTray", "Refresh status"} and not label.startswith("Devices"):
                item.set_sensitive(False)
        return GLib.SOURCE_REMOVE

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

    def _build_menu(self) -> Gtk.Menu:
        snapshot = self.snapshot
        menu = Gtk.Menu()
        menu.append(_menu_item(self.display_status, sensitive=False))
        if snapshot.hostname or snapshot.ips:
            detail = snapshot.hostname
            if snapshot.ips:
                detail += f"  ·  {snapshot.ips[0]}" if detail else snapshot.ips[0]
            menu.append(_menu_item(detail, self.copy_ip if snapshot.ips else None, sensitive=bool(snapshot.ips)))
        if snapshot.health:
            menu.append(_menu_item(f"⚠ {snapshot.health[0]}", sensitive=False))
        menu.append(_separator())

        if self.operation_busy:
            menu.append(_menu_item("Disconnect after current operation", self.disconnect_tailscale))
        elif snapshot.error_kind == "missing":
            menu.append(_menu_item("Open installation guide…", lambda *_: webbrowser.open("https://tailscale.com/download/linux")))
        elif snapshot.error_kind == "daemon":
            menu.append(_menu_item("Start Tailscale service…", self.start_service))
        elif snapshot.error_kind == "permission":
            menu.append(_menu_item("Grant this user control…", self.grant_operator))
        elif snapshot.backend_state == "NeedsLogin":
            menu.append(_menu_item("Sign in…", self.interactive_login))
        elif snapshot.backend_state == "NeedsMachineAuth":
            menu.append(_menu_item("Approve this device…", self.open_admin_console))
        elif snapshot.connected:
            menu.append(_menu_item("Disconnect", self.disconnect_tailscale, sensitive=not self.operation_busy))
        elif snapshot.backend_state == "Stopped":
            menu.append(_menu_item("Connect", self.connect_tailscale, sensitive=not self.operation_busy))
        else:
            menu.append(_menu_item("Refresh status", self.refresh, sensitive=not self.operation_busy))

        if snapshot.devices:
            menu.append(self._devices_item(snapshot))
        if snapshot.connected:
            menu.append(self._exit_nodes_item(snapshot))

        menu.append(_separator())
        menu.append(self._more_item(snapshot))
        menu.append(_menu_item("Quit TailTray", self.quit))
        menu.show_all()
        return menu

    def _more_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        root = _menu_item("More")
        submenu = Gtk.Menu()
        if snapshot.profiles:
            submenu.append(self._profiles_item(snapshot))
        if snapshot.connected and snapshot.preferences:
            submenu.append(self._preferences_item(snapshot))
        submenu.append(self._authentication_item())
        submenu.append(self._maintenance_item(snapshot))
        submenu.append(self._application_item())
        root.set_submenu(submenu)
        return root

    def _authentication_item(self) -> Gtk.MenuItem:
        root = _menu_item("Authentication")
        submenu = Gtk.Menu()
        submenu.append(
            _menu_item(
                "Tailscale admin console…",
                lambda *_: webbrowser.open("https://login.tailscale.com/admin/machines"),
            )
        )
        if self.secrets.available:
            submenu.append(_separator())
            if self.auth_key:
                submenu.append(_menu_item("Replace automatic sign-in key…", self.prompt_auth_key))
                submenu.append(_menu_item("Forget automatic sign-in key", self.forget_auth_key))
            else:
                submenu.append(_menu_item("Set up automatic sign-in…", self.prompt_auth_key))

        automatic = Gtk.CheckMenuItem.new_with_label("Sign in automatically when required")
        automatic.set_active(bool(self.settings.get("automatic_login", True)))
        automatic.connect("toggled", self.toggle_automatic_login)
        automatic.show()
        submenu.append(automatic)
        root.set_submenu(submenu)
        return root

    def _maintenance_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        root = _menu_item("Maintenance")
        submenu = Gtk.Menu()
        if not snapshot.error_kind and not snapshot.needs_login:
            submenu.append(_menu_item("Full restart…", self.confirm_full_restart))
        submenu.append(_menu_item("Refresh status", self.refresh))
        if snapshot.version:
            submenu.append(_separator())
            submenu.append(_menu_item(f"Tailscale {snapshot.version}", sensitive=False))
        root.set_submenu(submenu)
        return root

    def _application_item(self) -> Gtk.MenuItem:
        root = _menu_item("Application")
        submenu = Gtk.Menu()
        reconnect = Gtk.CheckMenuItem.new_with_label("Reconnect after unexpected disconnects")
        reconnect.set_active(bool(self.settings.get("automatic_reconnect", True)))
        reconnect.connect("toggled", self.toggle_automatic_reconnect)
        reconnect.show()
        submenu.append(reconnect)
        submenu.append(_separator())
        startup = Gtk.CheckMenuItem.new_with_label("Start TailTray on login")
        startup.set_active(autostart_enabled())
        startup.connect("toggled", self.toggle_autostart)
        startup.show()
        submenu.append(startup)
        root.set_submenu(submenu)
        return root

    def _profiles_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        selected = next((profile for profile in snapshot.profiles if profile.selected), None)
        root = _menu_item(f"Account: {selected.label if selected else 'select'}")
        submenu = Gtk.Menu()
        group = None
        for profile in snapshot.profiles:
            radio = Gtk.RadioMenuItem.new_with_label_from_widget(group, profile.label)
            group = group or radio
            radio.set_active(profile.selected)
            radio.connect("activate", self.switch_profile, profile.id, profile.selected)
            radio.show()
            submenu.append(radio)
        root.set_submenu(submenu)
        return root

    def _devices_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        suffix = " · last reported" if snapshot.degraded else ""
        root = _menu_item(f"Devices ({len(snapshot.devices)}){suffix}")
        submenu = Gtk.Menu()
        for device in snapshot.devices:
            device_item = _menu_item(device.label, sensitive=bool(device.ips))
            if device.ips:
                addresses = Gtk.Menu()
                for address in device.ips:
                    addresses.append(
                        _menu_item(address, lambda _item, ip=address, name=device.name: self.copy_address(ip, name))
                    )
                device_item.set_submenu(addresses)
            submenu.append(device_item)
        root.set_submenu(submenu)
        return root

    def _exit_nodes_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        root = _menu_item(snapshot.exit_node_label)
        submenu = Gtk.Menu()
        group = None

        none = Gtk.RadioMenuItem.new_with_label_from_widget(group, "None")
        group = none
        none.set_active(not snapshot.exit_node_id)
        none.connect("activate", self.select_exit_node, "", not snapshot.exit_node_id)
        none.show()
        submenu.append(none)

        automatic = Gtk.RadioMenuItem.new_with_label_from_widget(group, "Automatic (best available)")
        automatic.set_active(False)
        automatic.connect("activate", self.select_exit_node, "auto:any", False)
        automatic.show()
        submenu.append(automatic)
        submenu.append(_separator())

        if not snapshot.exit_nodes:
            submenu.append(_menu_item("No exit nodes available", sensitive=False))
        for node in snapshot.exit_nodes:
            radio = Gtk.RadioMenuItem.new_with_label_from_widget(group, node.label)
            radio.set_active(node.selected)
            radio.set_sensitive(node.online)
            radio.connect("activate", self.select_exit_node, node.target, node.selected)
            radio.show()
            submenu.append(radio)
        root.set_submenu(submenu)
        return root

    def _preferences_item(self, snapshot: Snapshot) -> Gtk.MenuItem:
        root = _menu_item("Preferences")
        submenu = Gtk.Menu()
        options = (
            ("accept-dns", "Use Tailscale DNS", True),
            ("accept-routes", "Accept subnet routes", False),
            ("exit-node-allow-lan-access", "Allow LAN with exit node", False),
            ("shields-up", "Block incoming connections", False),
            ("update-check", "Check for Tailscale updates", True),
        )
        for name, label, default in options:
            item = Gtk.CheckMenuItem.new_with_label(label)
            current = preference_value(snapshot.preferences, name, default)
            item.set_active(current)
            item.connect("toggled", self.toggle_preference, name, current)
            item.show()
            submenu.append(item)
        root.set_submenu(submenu)
        return root

    def _refresh_once(self) -> bool:
        self.refresh()
        return GLib.SOURCE_REMOVE

    def switch_profile(self, item: Gtk.RadioMenuItem, profile_id: str, already_selected: bool) -> None:
        if item.get_active() and not already_selected:
            self._action("Account switch", lambda: self.cli.switch_profile(profile_id))

    def select_exit_node(self, item: Gtk.RadioMenuItem, target: str, already_selected: bool) -> None:
        if item.get_active() and not already_selected:
            self._action("Exit node change", lambda: self.cli.set_exit_node(target))

    def toggle_preference(self, item: Gtk.CheckMenuItem, name: str, old_value: bool) -> None:
        value = item.get_active()
        if value != old_value:
            self._action("Preference change", lambda: self.cli.set_preference(name, value))

    def toggle_automatic_login(self, item: Gtk.CheckMenuItem) -> None:
        self.settings["automatic_login"] = item.get_active()
        save_settings(self.settings)
        if item.get_active():
            self.login_token = ""
            self._maybe_auto_login()

    def toggle_automatic_reconnect(self, item: Gtk.CheckMenuItem) -> None:
        enabled = item.get_active()
        self.settings["automatic_reconnect"] = enabled
        save_settings(self.settings)
        if enabled and not self.operation_busy:
            self._maybe_auto_reconnect()

    def toggle_autostart(self, item: Gtk.CheckMenuItem) -> None:
        try:
            set_autostart(item.get_active())
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
            self.secrets.set(key)
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
        if self.operation_busy:
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
        if response == Gtk.ResponseType.OK and not self.operation_busy:
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
