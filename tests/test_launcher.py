import unittest

from tailtray.launcher import selected_backend


class LauncherTests(unittest.TestCase):
    def test_kde_uses_qt_backend(self):
        self.assertEqual(selected_backend({"XDG_CURRENT_DESKTOP": "KDE"}), "qt")
        self.assertEqual(selected_backend({"XDG_CURRENT_DESKTOP": "KDE:Plasma"}), "qt")

    def test_non_kde_uses_gtk_backend(self):
        self.assertEqual(selected_backend({"XDG_CURRENT_DESKTOP": "GNOME"}), "gtk")

    def test_explicit_backend_overrides_desktop(self):
        self.assertEqual(
            selected_backend({"XDG_CURRENT_DESKTOP": "KDE", "TAILTRAY_BACKEND": "gtk"}),
            "gtk",
        )
        self.assertEqual(
            selected_backend({"XDG_CURRENT_DESKTOP": "GNOME", "TAILTRAY_BACKEND": "qt"}),
            "qt",
        )

if __name__ == "__main__":
    unittest.main()
