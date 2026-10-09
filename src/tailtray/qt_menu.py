"""Update exported QActions in place, without relying on host close events."""

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from .menu import Entry


class QtMenu:
    def __init__(self, activate):
        self.menu = QMenu()
        self.activate = activate
        # Keep Python wrappers for nested QMenus alive.  Qt 6.10 can otherwise
        # release a wrapper while its menuAction is still installed in a menu.
        self._submenus: dict[str, QMenu] = {}

    def update(self, entries: tuple[Entry, ...]) -> bool:
        """Return whether items were added/removed, not merely changed or moved."""
        return self._sync(self.menu, entries)

    def _trigger(self, action: QAction, checked: bool) -> None:
        entry = action._entry
        if entry is not None and entry.enabled and entry.command:
            self.activate(entry, checked)

    def _sync(self, menu: QMenu, entries: tuple[Entry, ...]) -> bool:
        existing = {action.objectName(): action for action in menu.actions()}
        keys = {entry.key for entry in entries}
        membership_changed = existing.keys() != keys
        for key, action in existing.items():
            if key not in keys:
                action._entry = None
                action.setEnabled(False)
                menu.removeAction(action)
                submenu = action.menu()
                if submenu is not None:
                    self._submenus.pop(key, None)
                    submenu.deleteLater()
                else:
                    action.deleteLater()
        for index, entry in enumerate(entries):
            action = existing.get(entry.key)
            if action is None:
                if entry.children is not None:
                    submenu = QMenu(menu)
                    self._submenus[entry.key] = submenu
                    action = submenu.menuAction()
                else:
                    action = QAction(menu)
                    action.triggered.connect(lambda checked=False, a=action: self._trigger(a, checked))
                action.setObjectName(entry.key)
                menu.addAction(action)
            action._entry = entry
            action.setSeparator(entry.separator)
            action.setVisible(entry.separator or bool(entry.label))
            action.setText(entry.label)
            action.setEnabled(entry.enabled)
            action.setCheckable(entry.checked is not None)
            if entry.checked is not None:
                action.setChecked(entry.checked)
            if entry.children is not None:
                submenu = self._submenus.get(entry.key)
                if submenu is None:
                    submenu = action.menu()
                    self._submenus[entry.key] = submenu
                submenu.setTitle(entry.label)
                child_changed = self._sync(submenu, entry.children)
                membership_changed = membership_changed or child_changed
            current = menu.actions()
            # PySide may materialize a different Python wrapper for the same
            # QAction, especially for submenu menuActions on Qt 6.10.
            if current[index].objectName() != action.objectName():
                menu.removeAction(action)
                menu.insertAction(current[index], action)
        return membership_changed
