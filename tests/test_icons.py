from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET


ICON_DIR = Path(__file__).resolve().parents[1] / "data" / "icons" / "hicolor" / "scalable" / "status"
TRAY_ICON_DIR = Path(__file__).resolve().parents[1] / "data" / "tray-icons"


class IconTests(unittest.TestCase):
    def test_tray_icons_are_self_contrasting_svgs(self):
        icons = sorted(ICON_DIR.glob("tailtray-*.svg"))
        self.assertEqual(len(icons), 4)
        for icon in icons:
            root = ET.parse(icon).getroot()
            source = icon.read_text(encoding="utf-8")
            self.assertEqual(root.attrib["viewBox"], "0 0 16 16")
            self.assertEqual(root.attrib["width"], "16")
            self.assertEqual(root.attrib["height"], "16")
            self.assertIn("#000", source)
            self.assertRegex(source, r"#fff|#929292")
            self.assertNotIn("symbolic", icon.name)

    def test_connected_and_disconnected_use_full_nine_dot_grid(self):
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        connected = ET.parse(ICON_DIR / "tailtray-connected.svg")
        disconnected = ET.parse(ICON_DIR / "tailtray-disconnected.svg")
        self.assertGreaterEqual(len(connected.findall(".//svg:circle", namespace)), 9)
        self.assertEqual(len(disconnected.findall(".//svg:circle", namespace)), 9)

    def test_kde_tray_icons_are_cache_busting_rgba_pngs(self):
        icons = sorted(TRAY_ICON_DIR.glob("tailtray-hc-v2-*.png"))
        self.assertEqual(len(icons), 4)
        for icon in icons:
            data = icon.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (64, 64))
            self.assertEqual(data[25], 6, "tray PNG must contain an alpha channel")


if __name__ == "__main__":
    unittest.main()
