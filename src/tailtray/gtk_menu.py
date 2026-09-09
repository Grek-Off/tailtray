"""Stable GTK menu items driven by the same model as the Qt backend."""

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .menu import Entry


class GtkMenu:
    def __init__(self, activate):
        self.menu = Gtk.Menu()
        self.activate = activate
        self.updating = False

    def update(self, entries: tuple[Entry, ...]) -> None:
        self.updating = True
        try:
            self._sync(self.menu, entries)
        finally:
            self.updating = False

    def _trigger(self, item) -> None:
        entry = item._entry
        if not self.updating and entry is not None and entry.enabled and entry.command:
            self.activate(entry, item.get_active() if entry.checked is not None else False)

    def _sync(self, menu, entries):
        existing = {item._key: item for item in menu.get_children()}
        keys = {entry.key for entry in entries}
        for key, item in existing.items():
            if key not in keys:
                item._entry = None
                item.set_sensitive(False)
                menu.remove(item)
                item.destroy()
        for index, entry in enumerate(entries):
            item = existing.get(entry.key)
            if item is None:
                if entry.separator:
                    item = Gtk.SeparatorMenuItem()
                elif entry.checked is not None:
                    item = Gtk.CheckMenuItem()
                else:
                    item = Gtk.MenuItem()
                item._key = entry.key
                item.connect("activate", self._trigger)
                if entry.children is not None:
                    item.set_submenu(Gtk.Menu())
                menu.append(item)
            item._entry = entry
            if not entry.separator and item.get_label() != entry.label:
                item.set_label(entry.label)
            if item.get_sensitive() != entry.enabled:
                item.set_sensitive(entry.enabled)
            if entry.checked is not None and item.get_active() != entry.checked:
                item.set_active(entry.checked)
            if entry.children is not None:
                self._sync(item.get_submenu(), entry.children)
            visible = entry.separator or bool(entry.label)
            if item.get_visible() != visible:
                item.set_visible(visible)
            if menu.get_children()[index] is not item:
                menu.reorder_child(item, index)
