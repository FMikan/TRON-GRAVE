import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import ImageStat

from extractor.image_processor import (
    MAX_LONG_EDGE, MAX_PIXELS, ImageUnreadable, prepare_image, process_image,
)
from tests.helpers import decode, jpeg_bytes, png16_bytes, webp_bytes


class PrepareImageTests(unittest.TestCase):
    def test_small_upright_jpeg_is_sent_untouched(self):
        raw = jpeg_bytes(400, 300)
        self.assertEqual(prepare_image(raw), (raw, "image/jpeg"))

    def test_exif_rotation_is_baked_into_the_pixels(self):
        data, mime = prepare_image(jpeg_bytes(400, 300, orientation=6))
        img = decode(data)
        self.assertEqual((mime, img.size), ("image/jpeg", (300, 400)))
        self.assertIsNone(img.getexif().get(0x0112))

    def test_50_mp_phone_photo_is_shrunk_to_what_the_model_uses(self):
        data, _ = prepare_image(jpeg_bytes(8160, 6120))   # over the API's 8000 px limit
        w, h = decode(data).size
        self.assertLessEqual(max(w, h), MAX_LONG_EDGE)
        self.assertLessEqual(w * h, MAX_PIXELS * 1.01)
        self.assertAlmostEqual(w / h, 8160 / 6120, places=2)

    def test_panorama_is_capped_by_the_long_edge(self):
        data, _ = prepare_image(jpeg_bytes(12000, 2600))
        self.assertEqual(max(decode(data).size), MAX_LONG_EDGE)

    def test_truncated_file_is_unreadable(self):
        raw = jpeg_bytes(1200, 900)
        with self.assertRaises(ImageUnreadable):
            prepare_image(raw[: len(raw) // 2])

    def test_non_image_is_unreadable(self):
        with self.assertRaises(ImageUnreadable):
            prepare_image(b"\x00\x05\x16\x07 Mac OS X        ATTR")

    def test_media_type_comes_from_the_content(self):
        raw = webp_bytes()                                 # e.g. a WebP saved as .jpg
        self.assertEqual(prepare_image(raw), (raw, "image/webp"))

    def test_16_bit_greyscale_is_scaled_not_clipped_to_white(self):
        data, mime = prepare_image(png16_bytes(value=30000))
        mean = ImageStat.Stat(decode(data).convert("L")).mean[0]
        self.assertEqual(mime, "image/jpeg")
        self.assertAlmostEqual(mean, 30000 / 256, delta=3)


class UnreadablePhotoTests(unittest.TestCase):
    def test_unreadable_photo_fails_before_any_api_call(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "p_1_x.jpg"
        path.write_bytes(b"not a photo")
        result = process_image(None, "claude-sonnet-5", path, "1")
        self.assertEqual((result.status, result.rows[0][5]), ("total_failure", "ne mogu otvoriti"))
