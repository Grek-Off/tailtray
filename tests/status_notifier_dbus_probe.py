"""Subprocess probe for the StatusNotifierItem activation D-Bus methods."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusPendingCallWatcher

from tailtray.status_notifier import STATUS_NOTIFIER_PATH, MenuOnlyStatusNotifier


INTERFACE = "org.kde.StatusNotifierItem"


def debug(message: str) -> None:
    if os.environ.get("TAILTRAY_PROBE_DEBUG"):
        print(message, file=sys.stderr, flush=True)


def main() -> int:
    application = QCoreApplication([])
    server = QDBusConnection.connectToBus(
        QDBusConnection.BusType.SessionBus,
        f"tailtray-left-click-server-{os.getpid()}",
    )
    client = QDBusConnection.connectToBus(
        QDBusConnection.BusType.SessionBus,
        f"tailtray-left-click-client-{os.getpid()}",
    )
    service = f"io.github.tailtray.LeftClickProbe.p{os.getpid()}"
    notifier = MenuOnlyStatusNotifier(Path("/tmp/tailtray-connected.png"), application)
    debug("connections created")

    if not server.isConnected() or not client.isConnected():
        print("probe could not connect to the temporary session bus", file=sys.stderr)
        return 2
    if not server.registerService(service):
        print(server.lastError().message(), file=sys.stderr)
        return 3
    debug("service registered")
    if not server.registerObject(
        STATUS_NOTIFIER_PATH,
        notifier,
        QDBusConnection.RegisterOption.ExportAdaptors,
    ):
        print(server.lastError().message(), file=sys.stderr)
        return 4
    debug("object registered")

    # Exercise both host callbacks repeatedly. A regression to the old local
    # QMenu.popup timer either crashes or stalls this subprocess.
    calls = [(method, index, index + 1) for index in range(20) for method in ("Activate", "ContextMenu")]
    watchers: list[QDBusPendingCallWatcher] = []
    result = {"code": 6}

    def dispatch_next() -> None:
        if not calls:
            result["code"] = 0
            debug("all activation calls finished")
            application.quit()
            return
        method, x, y = calls.pop(0)
        message = QDBusMessage.createMethodCall(service, STATUS_NOTIFIER_PATH, INTERFACE, method)
        message.setArguments([x, y])
        watcher = QDBusPendingCallWatcher(client.asyncCall(message), application)
        watchers.append(watcher)
        watcher.finished.connect(call_finished)

    def call_finished(watcher: QDBusPendingCallWatcher) -> None:
        if watcher.isError():
            print(watcher.error().message(), file=sys.stderr)
            result["code"] = 7
            application.quit()
            return
        watchers.remove(watcher)
        watcher.deleteLater()
        dispatch_next()

    def timed_out() -> None:
        print("activation probe timed out", file=sys.stderr)
        result["code"] = 8
        application.quit()

    QTimer.singleShot(0, dispatch_next)
    QTimer.singleShot(5_000, timed_out)
    application.exec()
    debug(f"event loop exited with {result['code']}")
    server.unregisterObject(STATUS_NOTIFIER_PATH)
    server.unregisterService(service)
    QDBusConnection.disconnectFromBus(client.name())
    QDBusConnection.disconnectFromBus(server.name())
    debug("connections disconnected")
    return result["code"]


if __name__ == "__main__":
    raise SystemExit(main())
