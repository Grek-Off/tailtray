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


def main() -> int:
    backend = selected_backend()
    if backend == "qt":
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
