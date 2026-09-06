"""Small user-level settings helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil


APP_ID = "io.github.tailtray"


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def config_path() -> Path:
    return config_home() / "tailtray" / "config.json"


def load_settings() -> dict[str, bool]:
    defaults = {
        "automatic_login": True,
        "automatic_reconnect": True,
        "want_connected": True,
    }
    try:
        payload = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if isinstance(payload, dict):
        for key in defaults:
            if isinstance(payload.get(key), bool):
                defaults[key] = payload[key]
    return defaults


def save_settings(settings: dict[str, bool]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def autostart_path() -> Path:
    return config_home() / "autostart" / f"{APP_ID}.desktop"


def autostart_enabled() -> bool:
    return autostart_path().exists()


def _desktop_source() -> Path:
    installed = Path("/usr/share/applications") / f"{APP_ID}.desktop"
    if installed.exists():
        return installed
    return Path(__file__).resolve().parents[2] / "data" / f"{APP_ID}.desktop"


def set_autostart(enabled: bool) -> None:
    destination = autostart_path()
    if not enabled:
        destination.unlink(missing_ok=True)
        return
    source = _desktop_source()
    if not source.exists():
        raise RuntimeError("TailTray desktop file is missing")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o644)
