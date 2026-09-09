"""Runtime GTK renderer assertions, without a window or daemon."""

from unittest.mock import Mock

from tailtray.gtk_menu import Gtk, GtkMenu
from tailtray.menu import Entry


def main():
    assert Gtk.init_check()[0], "GTK display is unavailable"
    activate = Mock()
    renderer = GtkMenu(activate)
    renderer.update((Entry("connection", "Disconnect", "disconnect"),
                     Entry("more", "More", children=(Entry("dns", "DNS", "preference", checked=False),))))
    connection, more = renderer.menu.get_children()
    submenu = more.get_submenu()
    dns = submenu.get_children()[0]
    renderer.update((Entry("connection", "Connect", "connect"),
                     Entry("more", "More", children=(Entry("dns", "DNS", "preference", checked=True),))))
    assert renderer.menu.get_children()[0] is connection
    assert more.get_submenu() is submenu
    assert submenu.get_children()[0] is dns
    assert connection.get_label() == "Connect"
    assert dns.get_active()
    activate.assert_not_called()
    connection.activate()
    assert activate.call_args.args[0].command == "connect"
    dns.activate()
    assert activate.call_args.args[1] is False
    renderer.update(())
    renderer.menu.destroy()
    print("PASS: GTK stable items, new callbacks, no programmatic checkbox activation")


if __name__ == "__main__":
    main()
