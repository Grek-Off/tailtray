"""Synthetic libsecret calls only: never touch a user's actual keyring."""
import unittest
from unittest.mock import Mock, patch

from tailtray import secrets


class SecretStoreTests(unittest.TestCase):
    def test_missing_library_is_read_only_and_cannot_fallback_to_a_file(self):
        with patch.object(secrets, "Secret", None):
            store = secrets.AuthKeyStore()
            self.assertFalse(store.available)
            self.assertEqual(store.get(), "")
            store.clear()
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                store.set("synthetic-key")

    def test_store_lookup_and_clear_address_the_same_scoped_record(self):
        api = Mock()
        api.Schema.new.return_value = object()
        api.password_lookup_sync.return_value = "synthetic-key"
        with patch.object(secrets, "Secret", api):
            store = secrets.AuthKeyStore()
            store.set("synthetic-key")
            self.assertEqual(store.get(), "synthetic-key")
            store.clear()
        schema = api.Schema.new.return_value
        attributes = {"purpose": "tailscale-reauth"}
        api.password_store_sync.assert_called_once_with(
            schema, attributes, api.COLLECTION_DEFAULT, "TailTray automatic Tailscale sign-in", "synthetic-key", None)
        api.password_lookup_sync.assert_called_once_with(schema, attributes, None)
        api.password_clear_sync.assert_called_once_with(schema, attributes, None)

    def test_missing_record_is_empty_but_locked_service_error_is_not_hidden(self):
        api = Mock()
        api.password_lookup_sync.return_value = None
        with patch.object(secrets, "Secret", api):
            store = secrets.AuthKeyStore()
            self.assertEqual(store.get(), "")
            api.password_lookup_sync.side_effect = RuntimeError("keyring locked")
            with self.assertRaisesRegex(RuntimeError, "locked"):
                store.get()
