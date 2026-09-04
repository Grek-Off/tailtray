"""KDE StatusNotifierItem adapter for menu-only tray applications."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import ClassInfo, Property, QObject, Signal, Slot
from PySide6.QtDBus import (
    QDBus,
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusInterface,
    QDBusObjectPath,
)


STATUS_NOTIFIER_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER_SERVICE = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"


def qt_status_notifier_connection_name(pid: int | None = None) -> str:
    """Return the private connection name used by Qt's first tray icon."""
    process_id = os.getpid() if pid is None else pid
    return f"org.kde.StatusNotifierItem-{process_id}-1"


@ClassInfo(**{"D-Bus Interface": "org.kde.StatusNotifierItem"})
class _StatusNotifierItemAdaptor(QDBusAbstractAdaptor):
    NewTitle = Signal()
    NewIcon = Signal()
    NewAttentionIcon = Signal()
    NewOverlayIcon = Signal()
    NewMenu = Signal()
    NewToolTip = Signal()
    NewStatus = Signal(str)

    Category = Property(str, lambda self: "ApplicationStatus", constant=True)
    Id = Property(str, lambda self: "TailTray", constant=True)
    Title = Property(str, lambda self: "TailTray", constant=True)
    Status = Property(str, lambda self: "Active", constant=True)
    WindowId = Property(int, lambda self: 0, constant=True)
    ItemIsMenu = Property(bool, lambda self: True, constant=True)
    Menu = Property(QDBusObjectPath, lambda self: QDBusObjectPath(MENU_PATH), constant=True)
    AttentionIconName = Property(str, lambda self: "", constant=True)
    AttentionMovieName = Property(str, lambda self: "", constant=True)
    OverlayIconName = Property(str, lambda self: "", constant=True)

    def __init__(self, parent: "MenuOnlyStatusNotifier") -> None:
        super().__init__(parent)

    @Property(str, notify=NewIcon)
    def IconThemePath(self) -> str:
        return str(self.parent().icon_path.parent)

    @Property(str, notify=NewIcon)
    def IconName(self) -> str:
        return self.parent().icon_path.stem

    @Slot(int, int)
    def Activate(self, _x: int, _y: int) -> None:
        # A conforming host opens MENU_PATH because ItemIsMenu is true.
        pass

    @Slot(int, int)
    def ContextMenu(self, _x: int, _y: int) -> None:
        # Plasma imports MENU_PATH itself; this is only a compatibility stub.
        pass

    @Slot(int, int)
    def SecondaryActivate(self, _x: int, _y: int) -> None:
        pass

    @Slot(int, str)
    def Scroll(self, _delta: int, _orientation: str) -> None:
        pass

    @Slot(str)
    def ProvideXdgActivationToken(self, token: str) -> None:
        os.environ["XDG_ACTIVATION_TOKEN"] = token


class MenuOnlyStatusNotifier(QObject):
    """Replace Qt's fixed ItemIsMenu=false adaptor on its own D-Bus link."""

    def __init__(self, icon_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.icon_path = icon_path
        self.adaptor = _StatusNotifierItemAdaptor(self)
        self.connection: QDBusConnection | None = None

    @classmethod
    def install(cls, icon_path: Path, parent: QObject | None = None) -> "MenuOnlyStatusNotifier":
        notifier = cls(icon_path, parent)
        connection = QDBusConnection(qt_status_notifier_connection_name())
        if not connection.isConnected():
            raise RuntimeError("Qt StatusNotifierItem D-Bus connection is unavailable")

        original = connection.objectRegisteredAt(STATUS_NOTIFIER_PATH)
        if original is None:
            raise RuntimeError("Qt StatusNotifierItem object is unavailable")

        connection.unregisterObject(
            STATUS_NOTIFIER_PATH,
            QDBusConnection.UnregisterMode.UnregisterNode,
        )
        if not connection.registerObject(
            STATUS_NOTIFIER_PATH,
            notifier,
            QDBusConnection.RegisterOption.ExportAdaptors,
        ):
            detail = connection.lastError().message()
            connection.registerObject(STATUS_NOTIFIER_PATH, original)
            raise RuntimeError(detail or "could not register menu-only StatusNotifierItem")

        notifier.connection = connection
        watcher = QDBusInterface(
            WATCHER_SERVICE,
            WATCHER_PATH,
            WATCHER_SERVICE,
            connection,
        )
        watcher.call(
            QDBus.CallMode.NoBlock,
            "RegisterStatusNotifierItem",
            connection.baseService(),
        )
        return notifier

    def set_icon(self, icon_path: Path) -> None:
        if icon_path == self.icon_path:
            return
        self.icon_path = icon_path
        self.adaptor.NewIcon.emit()

    def menu_changed(self) -> None:
        self.adaptor.NewMenu.emit()
