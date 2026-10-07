import unittest
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]


class IconFileTests(unittest.TestCase):
    def test_the_ico_holds_every_size_windows_asks_for(self):
        with Image.open(REPO / "assets" / "tron-grave.ico") as ico:
            self.assertEqual(ico.info["sizes"], {(n, n) for n in (16, 24, 32, 48, 64, 128, 256)})
        with Image.open(REPO / "assets" / "tron-grave.png") as png:
            self.assertEqual((png.size, png.mode), ((256, 256), "RGBA"))

    def test_the_build_bundles_the_icon_and_stamps_the_version(self):
        spec = (REPO / "TRON-GRAVE.spec").read_text(encoding="utf-8")
        for needle in ("tron-grave.ico", "tron-grave.png", "VSVersionInfo", "_version.py"):
            self.assertIn(needle, spec)
