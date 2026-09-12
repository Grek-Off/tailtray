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
from PySide6.QtDBus import QDBusConnection
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from tailtray.core import Snapshot
from tailtray.status_notifier import MenuOnlyStatusNotifier, qt_status_notifier_connection_name
from test_state_integration import StateIntegrationTests


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
        assert notifier.adaptor.ItemIsMenu

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
        print("PASS: real Qt D-Bus menu, 5 Disconnect/Connect cycles, stable ID, no close events")
    finally:
        test.controller.tray.hide()
        test.doCleanups()


if __name__ == "__main__":
    with watcher_service():
        run_probe()
