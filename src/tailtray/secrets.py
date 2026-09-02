"""GNOME Keyring/libsecret storage for the optional reusable auth key."""

from __future__ import annotations

try:
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ImportError, ValueError):  # pragma: no cover - depends on desktop packages
    Secret = None


class AuthKeyStore:
    """Stores one auth key in the desktop Secret Service.

    A key is optional. Normal Tailscale sessions persist in ``tailscaled`` and
    do not need it; it only enables unattended re-registration after a node key
    is expired or removed.
    """

    def __init__(self) -> None:
        self._schema = None
        if Secret is not None:
            self._schema = Secret.Schema.new(
                "io.github.tailtray.AuthKey",
                Secret.SchemaFlags.NONE,
                {"purpose": Secret.SchemaAttributeType.STRING},
            )
        self._attributes = {"purpose": "tailscale-reauth"}

    @property
    def available(self) -> bool:
        return self._schema is not None

    def get(self) -> str:
        if self._schema is None:
            return ""
        return Secret.password_lookup_sync(self._schema, self._attributes, None) or ""

    def set(self, auth_key: str) -> None:
        if self._schema is None:
            raise RuntimeError("Secret Service is unavailable")
        Secret.password_store_sync(
            self._schema,
            self._attributes,
            Secret.COLLECTION_DEFAULT,
            "TailTray automatic Tailscale sign-in",
            auth_key,
            None,
        )

    def clear(self) -> None:
        if self._schema is not None:
            Secret.password_clear_sync(self._schema, self._attributes, None)

