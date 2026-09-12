"""Minimal independent tray host for the private-bus Xvfb test only."""

import os

from PySide6.QtCore import ClassInfo, QCoreApplication, QObject, Property, QTimer, Slot
from PySide6.QtDBus import QDBusConnection


@ClassInfo(**{"D-Bus Interface": "org.kde.StatusNotifierWatcher"})
class Watcher(QObject):
    IsStatusNotifierHostRegistered = Property(bool, lambda self: True, constant=True)
    ProtocolVersion = Property(int, lambda self: 0, constant=True)

    @Slot(str)
    def RegisterStatusNotifierItem(self, service):
        pass


if __name__ == "__main__":
    app = QCoreApplication([])
    bus = QDBusConnection.sessionBus()
    watcher = Watcher()
    assert bus.registerService("org.kde.StatusNotifierWatcher")
    assert bus.registerObject("/StatusNotifierWatcher", watcher,
                              QDBusConnection.ExportAllSlots | QDBusConnection.ExportAllProperties)
    # The outer timeout can kill the probe before its context manager runs.
    parent_pid = os.getppid()
    watchdog = QTimer()
    watchdog.setInterval(200)
    def check_owner():
        if os.getppid() != parent_pid or not bus.isConnected():
            app.quit()
    watchdog.timeout.connect(check_owner)
    watchdog.start()
    print("READY", flush=True)
    app.exec()
