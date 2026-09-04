from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ICON_DIR = Path(__file__).resolve().parents[1] / "data" / "icons" / "hicolor" / "scalable" / "status"


class IconTests(unittest.TestCase):
    def test_tray_icons_are_adaptive_symbolic_svgs(self):
        icons = sorted(ICON_DIR.glob("tailtray-*-symbolic.svg"))
        self.assertEqual(len(icons), 4)
        for icon in icons:
            root = ET.parse(icon).getroot()
            source = icon.read_text(encoding="utf-8")
            self.assertEqual(root.attrib["viewBox"], "0 0 16 16")
            self.assertEqual(root.attrib["width"], "16")
            self.assertEqual(root.attrib["height"], "16")
            self.assertIn("#2e3436", source)
            self.assertNotIn('fill="#000"', source)

    def test_connected_and_disconnected_use_full_nine_dot_grid(self):
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        connected = ET.parse(ICON_DIR / "tailtray-connected-symbolic.svg")
        disconnected = ET.parse(ICON_DIR / "tailtray-disconnected-symbolic.svg")
        self.assertGreaterEqual(len(connected.findall(".//svg:circle", namespace)), 9)
        self.assertEqual(len(disconnected.findall(".//svg:circle", namespace)), 9)


if __name__ == "__main__":
    unittest.main()
