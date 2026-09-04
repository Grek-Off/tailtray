"""Select the desktop-native TailTray user-interface backend."""

from __future__ import annotations

import os
import sys


def selected_backend(environment: dict[str, str] | None = None) -> str:
    values = environment if environment is not None else os.environ
    requested = values.get("TAILTRAY_BACKEND", "").strip().casefold()
    if requested in {"qt", "gtk"}:
        return requested
    desktop = values.get("XDG_CURRENT_DESKTOP", "").casefold()
    return "qt" if "kde" in desktop else "gtk"


def configure_qt_platform(environment: dict[str, str] | None = None) -> str | None:
    """Use XCB for Qt tray popups inside a Wayland desktop session.

    Plasma activates a generic QSystemTrayIcon over D-Bus on left click. Qt's
    Wayland plugin cannot safely turn that activation into a grabbing QMenu,
    while the XCB plugin can. Respect an explicit platform override so this
    behavior remains debuggable and does not affect native X11 sessions.
    """
    values = environment if environment is not None else os.environ
    configured = values.get("QT_QPA_PLATFORM", "").strip()
    if configured:
        return configured
    if values.get("WAYLAND_DISPLAY") and values.get("DISPLAY"):
        values["QT_QPA_PLATFORM"] = "xcb"
        return "xcb"
    return None


def main() -> int:
    backend = selected_backend()
    if backend == "qt":
        configure_qt_platform()
        try:
            from .qt_app import main as backend_main
        except ImportError as exc:
            if os.environ.get("TAILTRAY_BACKEND", "").strip().casefold() == "qt":
                print(f"tailtray: Qt backend is unavailable: {exc}", file=sys.stderr)
                return 1
            print(f"tailtray: Qt backend is unavailable, falling back to GTK: {exc}", file=sys.stderr)
        else:
            return backend_main()

    from .app import main as backend_main

    return backend_main()


if __name__ == "__main__":
    raise SystemExit(main())
