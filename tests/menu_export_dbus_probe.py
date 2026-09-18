"""Drive the real Qt /MenuBar export over a private bus, without close events.

Requires a Wayland or X11 display, but creates no windows and never touches the
desktop bus or the VPN. The controller uses controlled futures, not tailscale.
"""

import subprocess
import time
import select
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from gi.repository import GLib
from PySide6.QtCore import QObject, SLOT, Slot
from PySide6.QtDBus import QDBusConnection
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from tailtray.core import Device, ExitNode, Profile, Snapshot
from tailtray.status_notifier import MenuOnlyStatusNotifier, qt_status_notifier_connection_name
from test_state_integration import StateIntegrationTests


class LayoutSignals(QObject):
    def __init__(self):
        super().__init__()
        self.parents = []
        self.menu_changes = 0

    @Slot("uint", "int")
    def changed(self, _revision, parent):
        self.parents.append(parent)

    @Slot()
    def menu_changed(self):
        self.menu_changes += 1


@contextmanager
def watcher_service():
    # Qt queries this service synchronously, so it must not share the GUI thread.
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("status_notifier_watcher.py"))],
                               stdout=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([process.stdout], [], [], 3)
        assert ready and process.stdout.readline().strip() == "READY", "test tray host did not start"
        yield
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def run_probe():
    StateIntegrationTests.setUpClass()
    test = StateIntegrationTests()
    native_show = QSystemTrayIcon.show
    native_install = MenuOnlyStatusNotifier.install
    test.setUp()
    try:
        controller = test.controller
        controller._schedule_refresh_after_action = Mock()  # snapshots below are delivered explicitly
        native_show(controller.tray)
        QApplication.processEvents()
        server = QDBusConnection(qt_status_notifier_connection_name())
        assert server.isConnected(), "Qt platform did not create a native tray connection"
        assert server.objectRegisteredAt("/MenuBar") is not None, "Qt did not export the menu"
        notifier = native_install(controller.icon_path, controller)
        controller.status_notifier = notifier
        assert notifier.adaptor.ItemIsMenu
        signals = LayoutSignals()
        assert QDBusConnection.sessionBus().connect(
            server.baseService(), "/MenuBar", "com.canonical.dbusmenu", "LayoutUpdated",
            signals, SLOT("changed(uint,int)"),
        )
        assert QDBusConnection.sessionBus().connect(
            server.baseService(), "/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewMenu",
            signals, SLOT("menu_changed()"),
        )

        def call(method, *args):
            command = ["gdbus", "call", "--session", "--dest", server.baseService(),
                       "--object-path", "/MenuBar", "--method", "com.canonical.dbusmenu." + method,
                       "--", *map(str, args)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 3
            while process.poll() is None and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(.005)
            if process.poll() is None:
                process.kill()
            stdout, stderr = process.communicate()
            assert process.returncode == 0, stderr
            return GLib.Variant.parse(None, stdout).unpack()

        def find(label):
            layout = call("GetLayout", 0, -1, "[]")[1]
            def walk(node):
                identity, properties, children = node
                if properties.get("label") == label:
                    return identity, properties
                for child in children:
                    found = walk(child)
                    if found:
                        return found
            found = walk(layout)
            assert found, f"{label!r} not found in exported menu: {layout!r}"
            return found

        test.reply(Snapshot(backend_state="Running", self_online=True))
        identity, _props = find("Disconnect")
        # Opening once and NEVER closing used to pin the menu in its busy state.
        call("AboutToShow", 0)
        call("Event", 0, "opened", "<0>", "uint32 0")
        with patch("tailtray.qt_app.save_settings"):
            for _ in range(5):
                for label, command, state in (("Disconnect", controller.cli.disconnect, "Stopped"),
                                               ("Connect", controller.cli.connect, "Running")):
                    item_id, props = find(label)
                    assert item_id == identity, "connection menu item identity changed"
                    assert props.get("enabled", True), f"{label} is disabled"
                    call("Event", item_id, "clicked", "<0>", "uint32 0")
                    deadline = time.monotonic() + 1
                    while not controller.executor.pending and time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(.005)
                    assert controller.executor.pending, f"{label} D-Bus click did not dispatch a command"
                    function, future = controller.executor.pending.pop(0)
                    assert function == command, f"{label}: dispatched {function!r}, expected {command!r}"
                    future.set_result(None)
                    QApplication.processEvents()
                    controller.refresh()
                    test.reply(Snapshot(backend_state=state, self_online=state == "Running"))
        # A host caches each submenu separately. Reading the full root layout
        # can hide a broken LayoutUpdated notification for a nested insertion.
        old = ExitNode(id="old", name="Zulu", target="100.64.0.1", online=True)
        new = ExitNode(id="new", name="Alpha", target="100.64.0.2", online=True)
        controller.refresh()
        test.reply(Snapshot(backend_state="Running", exit_nodes=(old,)))
        exit_id, _ = find("Exit node: unknown")
        call("AboutToShow", exit_id)
        call("Event", exit_id, "opened", "<0>", "uint32 0")
        for nodes in ((new, old), (old,), (new, old)):
            signals.parents.clear()
            signals.menu_changes = 0
            controller.refresh()
            test.reply(Snapshot(backend_state="Running", exit_nodes=nodes,
                                devices=tuple(Device(id=n.id, name=n.name, ips=(n.target,)) for n in nodes)))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(.005)
            assert exit_id in signals.parents or 0 in signals.parents, (
                f"host was not notified about changed submenu {exit_id}: {signals.parents}"
            )
            # Model Plasma's initial empty-submenu fetch: it leaves this ID in
            # m_idsRefreshedByAboutToShow, consuming the next LayoutUpdated.
            # With KDE's single batched signal the host stays stale indefinitely
            # unless NewMenu invalidates its importer. Generic Qt emits more
            # than one signal and used to conceal this regression in our tests.
            assert signals.menu_changes == 1, "new menu membership did not invalidate the KDE cache"
            layout = call("GetLayout", exit_id, 1, "[]")[1]
            labels = [child[1].get("label") for child in layout[2]]
            assert labels == ["None", "Automatic (best available)", *(n.label for n in nodes)], labels
        def profile_snapshot(selected):
            return Snapshot(backend_state="Running", tailnet=selected,
                            profiles=tuple(Profile(p, "same@login", p, selected=p == selected)
                                           for p in ("home.example", "work.example")))

        controller.refresh()
        test.reply(profile_snapshot("home.example"))
        with patch("tailtray.qt_app.save_settings"):
            for selected in ("work.example", "home.example"):
                item_id, _ = find(f"{selected} — same@login")
                call("Event", item_id, "clicked", "<0>", "uint32 0")
                deadline = time.monotonic() + 1
                while not controller.executor.pending and time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(.005)
                assert controller.executor.pending, "tailnet click did not dispatch a command"
                command, future = controller.executor.pending.pop(0)
                future.set_result(command())
                QApplication.processEvents()
                controller.cli.switch_profile.assert_called_with(selected)
                controller.refresh()
                test.reply(profile_snapshot(selected))
                _item_id, props = find(f"{selected} — same@login")
                assert props.get("toggle-state") == 1, "observed tailnet was not selected"
        controller.cli.begin_interactive_login.assert_not_called()
        print("PASS: real Qt D-Bus menu, connection/tailnet cycles and dynamic exit-node notifications")
    finally:
        test.controller.tray.hide()
        test.doCleanups()


if __name__ == "__main__":
    with watcher_service():
        run_probe()
