import unittest

from tailtray.launcher import configure_qt_platform, selected_backend


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

    def test_wayland_qt_uses_xcb_when_xwayland_is_available(self):
        environment = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}

        self.assertEqual(configure_qt_platform(environment), "xcb")
        self.assertEqual(environment["QT_QPA_PLATFORM"], "xcb")

    def test_explicit_qt_platform_is_preserved(self):
        environment = {
            "WAYLAND_DISPLAY": "wayland-0",
            "DISPLAY": ":0",
            "QT_QPA_PLATFORM": "wayland",
        }

        self.assertEqual(configure_qt_platform(environment), "wayland")
        self.assertEqual(environment["QT_QPA_PLATFORM"], "wayland")

    def test_native_x11_session_needs_no_override(self):
        environment = {"DISPLAY": ":0"}

        self.assertIsNone(configure_qt_platform(environment))
        self.assertNotIn("QT_QPA_PLATFORM", environment)


if __name__ == "__main__":
    unittest.main()
