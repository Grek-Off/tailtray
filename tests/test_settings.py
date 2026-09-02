import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tailtray.settings import autostart_enabled, load_settings, save_settings, set_autostart


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_mode(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
            self.assertTrue(load_settings()["automatic_login"])
            save_settings({"automatic_login": False})
            self.assertFalse(load_settings()["automatic_login"])
            path = Path(directory) / "tailtray" / "config.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_user_autostart_can_be_enabled_and_disabled(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
            set_autostart(True)
            self.assertTrue(autostart_enabled())
            set_autostart(False)
            self.assertFalse(autostart_enabled())


if __name__ == "__main__":
    unittest.main()
