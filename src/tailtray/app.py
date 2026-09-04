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
from .secrets import AuthKeyStore
from .settings import autostart_enabled, load_settings, save_settings, set_autostart


APP_ID = "io.github.tailtray"
REFRESH_SECONDS = 5


def _menu_item(label: str, callback: Callable | None = None, *, sensitive: bool = True) -> Gtk.MenuItem:
    item = Gtk.MenuItem.new_with_label(label)
    item.set_sensitive(sensitive)
    if callback is not None:
        item.connect("activate", callback)
    item.show()
    return item


def _separator() -> Gtk.SeparatorMenuItem:
    item = Gtk.SeparatorMenuItem()
    item.show()
    return item


class TailTrayApp:
    def __init__(self) -> None:
        self.cli = TailscaleCLI()
        self.secrets = AuthKeyStore()
        self.auth_key = self._load_auth_key()
        self.settings = load_settings()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tailtray")
        self.snapshot = Snapshot()
        self.refresh_running = False
        self.login_token = ""
        self.lock_handle = self._acquire_singleton_lock()

        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            "tailtray-disconnected-symbolic",
            AppIndicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        development_icons = Path(__file__).resolve().parents[2] / "data" / "icons" / "hicolor" / "scalable" / "status"
        if development_icons.exists():
            self.indicator.set_icon_theme_path(str(development_icons))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("TailTray")
        self._set_menu(self._loading_menu())

        GLib.timeout_add_seconds(REFRESH_SECONDS, self._refresh_timer)
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

    def _loading_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.append(_menu_item("Loading Tailscale…", sensitive=False))
        menu.append(_separator())
        menu.append(_menu_item("Quit TailTray", self.quit))
        menu.show_all()
        return menu

    def _set_menu(self, menu: Gtk.Menu) -> None:
        self.indicator.set_menu(menu)

    def _refresh_timer(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self, *_args) -> None:
        if self.refresh_running:
            return
        self.refresh_running = True
        future = self.executor.submit(self.cli.snapshot)
        future.add_done_callback(lambda completed: GLib.idle_add(self._finish_refresh, completed))

    def _finish_refresh(self, future: Future) -> bool:
        self.refresh_running = False
        try:
            self.snapshot = future.result()
        except Exception as exc:  # defensive: keep the tray alive
            self.snapshot = Snapshot(error=str(exc), error_kind="command")
        self._update_indicator()
        self._set_menu(self._build_menu())
        self._maybe_auto_login()
        return GLib.SOURCE_REMOVE

    def _update_indicator(self) -> None:
        snapshot = self.snapshot
        if snapshot.connected and snapshot.exit_node_id:
            icon = "tailtray-exit-node-symbolic"
        elif snapshot.connected:
            icon = "tailtray-connected-symbolic"
        elif snapshot.backend_state == "Starting":
            icon = "tailtray-connecting-symbolic"
        else:
            icon = "tailtray-disconnected-symbolic"
        self.indicator.set_icon_full(icon, snapshot.display_status)
        self.indicator.set_title(snapshot.display_status)

    def _build_menu(self) -> Gtk.Menu:
        snapshot = self.snapshot
        menu = Gtk.Menu()
        menu.append(_menu_item(snapshot.display_status, sensitive=False))
        if snapshot.hostname or snapshot.ips:
            detail = snapshot.hostname
            if snapshot.ips:
                detail += f"  ·  {snapshot.ips[0]}" if detail else snapshot.ips[0]
            menu.append(_menu_item(detail, self.copy_ip if snapshot.ips else None, sensitive=bool(snapshot.ips)))
        if snapshot.health:
            menu.append(_menu_item(f"⚠ {snapshot.health[0]}", sensitive=False))
        menu.append(_separator())

        if snapshot.error_kind == "missing":
            menu.append(_menu_item("Open installation guide…", lambda *_: webbrowser.open("https://tailscale.com/download/linux")))
        elif snapshot.error_kind == "daemon":
            menu.append(_menu_item("Start Tailscale service…", self.start_service))
        elif snapshot.error_kind == "permission":
            menu.append(_menu_item("Grant this user control…", self.grant_operator))
        elif snapshot.needs_login:
            menu.append(_menu_item("Sign in…", self.interactive_login))
        elif snapshot.connected:
            menu.append(_menu_item("Disconnect", lambda *_: self._action("Disconnecting", self.cli.disconnect)))
        else:
            menu.append(_menu_item("Connect", lambda *_: self._action("Connecting", self.cli.connect)))
        if not snapshot.error_kind and not snapshot.needs_login:
            menu.append(_menu_item("Full restart…", self.confirm_full_restart))

        if snapshot.profiles:
            menu.append(self._profiles_item(snapshot))
        if snapshot.devices:
            menu.append(self._devices_item(snapshot))
        if snapshot.connected:
            menu.append(self._exit_nodes_item(snapshot))
            menu.append(self._preferences_item(snapshot))
        menu.append(_separator())
        menu.append(_menu_item("Tailscale admin console…", lambda *_: webbrowser.open("https://login.tailscale.com/admin/machines")))
        if self.secrets.available:
            if self.auth_key:
                menu.append(_menu_item("Replace automatic sign-in key…", self.prompt_auth_key))
                menu.append(_menu_item("Forget automatic sign-in key", self.forget_auth_key))
            else:
                menu.append(_menu_item("Set up automatic sign-in…", self.prompt_auth_key))

        automatic = Gtk.CheckMenuItem.new_with_label("Sign in automatically when required")
        automatic.set_active(bool(self.settings.get("automatic_login", True)))
        automatic.connect("toggled", self.toggle_automatic_login)
        automatic.show()
        menu.append(automatic)

        startup = Gtk.CheckMenuItem.new_with_label("Start TailTray on login")
        startup.set_active(autostart_enabled())
        startup.connect("toggled", self.toggle_autostart)
        startup.show()
        menu.append(startup)

        menu.append(_separator())
        menu.append(_menu_item("Refresh", self.refresh))
        menu.append(_menu_item("Quit TailTray", self.quit))
        menu.show_all()
        return menu

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
        root = _menu_item(f"Devices ({len(snapshot.devices)})")
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
        selected = next((node for node in snapshot.exit_nodes if node.selected), None)
        root = _menu_item(f"Exit node: {selected.name if selected else 'None'}")
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

    def _action(self, description: str, function: Callable[[], None], *, on_success: Callable | None = None) -> None:
        future = self.executor.submit(function)
        future.add_done_callback(
            lambda completed: GLib.idle_add(self._finish_action, description, completed, on_success)
        )

    def _finish_action(self, description: str, future: Future, on_success: Callable | None) -> bool:
        try:
            result = future.result()
        except Exception as exc:
            self.notify("TailTray", f"{description} failed: {exc}")
        else:
            if on_success is not None:
                on_success(result)
        GLib.timeout_add(350, self._refresh_once)
        return GLib.SOURCE_REMOVE

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
        if self.snapshot.auth_url:
            webbrowser.open(self.snapshot.auth_url)
            return
        self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

    def _open_login_url(self, url: str) -> None:
        if url:
            webbrowser.open(url)
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
            webbrowser.open(snapshot.auth_url)
        else:
            self._action("Sign-in", self.cli.begin_interactive_login, on_success=self._open_login_url)

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
        if response == Gtk.ResponseType.OK:
            self._action("Full restart", self._full_restart)

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
