import base64
import io
import math
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageFilter, ImageStat, JpegImagePlugin

from extractor import image_processor
from extractor.image_processor import (
    MAX_LONG_EDGE, MAX_VISUAL_TOKENS, ImageUnreadable, prepare_image, process_image,
)
from tests.helpers import decode, jpeg_bytes, png16_bytes, webp_bytes


def _tokens(size) -> int:
    """Visual tokens the model counts for an image: one per started 28x28 px patch."""
    return math.ceil(size[0] / 28) * math.ceil(size[1] / 28)


def _with_bad_exif(fmt, size=(400, 300), **save_kwargs) -> bytes:
    """A photo whose pixels are fine but whose EXIF block has no valid TIFF header."""
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 200, 200)).save(buf, fmt, exif=b"Exif\x00\x00garbage", **save_kwargs)
    return buf.getvalue()


class PrepareImageTests(unittest.TestCase):
    def test_small_upright_jpeg_is_sent_untouched(self):
        raw = jpeg_bytes(400, 300)
        self.assertEqual(prepare_image(raw), (raw, "image/jpeg"))

    def test_a_photo_sent_as_is_stays_under_the_apis_5_mib_base64_limit(self):
        # A 1.4 MP PNG export needs no shrinking or rotating, but noise compresses worst: about
        # 4.2 MB, which as base64 is over the API's 5 MiB per image, so it must be re-encoded.
        buf = io.BytesIO()
        Image.frombytes("RGB", (1400, 1000), random.Random(0).randbytes(1400 * 1000 * 3)).save(buf, "PNG")
        self.assertGreater(len(buf.getvalue()), 3_932_160)
        data, _mime = prepare_image(buf.getvalue())
        self.assertLessEqual(len(base64.b64encode(data)), 5 * 1024 * 1024)

    def test_exif_rotation_is_baked_into_the_pixels(self):
        data, mime = prepare_image(jpeg_bytes(400, 300, orientation=6))
        img = decode(data)
        self.assertEqual((mime, img.size), ("image/jpeg", (300, 400)))
        self.assertIsNone(img.getexif().get(0x0112))

    def test_an_invalid_exif_orientation_counts_as_upright(self):
        # Only 2-8 turn or flip the picture. 0, which field apps write for "upright", and other
        # stray values do not, so such a photo goes out untouched instead of being re-encoded.
        for orientation in (0, 9):
            with self.subTest(orientation=orientation):
                raw = jpeg_bytes(400, 300, orientation=orientation)
                self.assertEqual(prepare_image(raw), (raw, "image/jpeg"))

    def test_a_re_encode_that_fits_at_full_quality_gets_it(self):
        # Quality 100 (every quantizer 1) and no chroma subsampling: nothing is thrown away
        # that the 5 MiB limit does not force.
        img = decode(prepare_image(jpeg_bytes(400, 300, orientation=6))[0])
        self.assertEqual(JpegImagePlugin.get_sampling(img), 0)
        self.assertTrue(all(q == 1 for table in img.quantization.values() for q in table))

    def test_a_re_encode_takes_the_highest_jpeg_quality_that_fits(self):
        img = Image.frombytes("RGB", (600, 400), random.Random(0).randbytes(600 * 400 * 3))
        img = img.filter(ImageFilter.GaussianBlur(1))
        png, q93 = io.BytesIO(), io.BytesIO()
        img.save(png, "PNG")
        img.save(q93, "JPEG", quality=93, subsampling=0, optimize=True)
        # With the limit at exactly the size of the quality-93 JPEG, no higher quality fits.
        with mock.patch.object(image_processor, "MAX_SEND_BYTES", len(q93.getvalue())):
            self.assertEqual(prepare_image(png.getvalue()), (q93.getvalue(), "image/jpeg"))

    def test_a_photo_always_fits_the_limit_even_when_no_jpeg_quality_does(self):
        # Noise compresses worst: at this limit it fits only once it is shrunk.
        buf = io.BytesIO()
        Image.frombytes("RGB", (800, 600), random.Random(0).randbytes(800 * 600 * 3)).save(buf, "PNG")
        with mock.patch.object(image_processor, "MAX_SEND_BYTES", 30_000):
            data, mime = prepare_image(buf.getvalue())
        self.assertEqual(mime, "image/jpeg")
        self.assertLessEqual(len(data), 30_000)
        w, h = decode(data).size
        self.assertLess(w, 800)
        self.assertAlmostEqual(w / h, 800 / 600, places=1)

    def test_a_4k_photo_is_shrunk_to_exactly_what_the_model_reads(self):
        # Anthropic's own example: 3840x2160 becomes 2576x1449, all 4,784 visual tokens.
        data, _ = prepare_image(jpeg_bytes(3840, 2160))
        self.assertEqual(decode(data).size, (2576, 1449))

    def test_50_mp_phone_photo_is_shrunk_to_what_the_model_uses(self):
        data, _ = prepare_image(jpeg_bytes(8160, 6120))   # over the API's 8000 px limit
        size = decode(data).size
        self.assertEqual(size, (2212, 1659))              # the largest 4:3 size the model reads
        self.assertLessEqual(_tokens(size), MAX_VISUAL_TOKENS)

    def test_a_turned_photo_is_shrunk_to_the_models_size_once_turned(self):
        data, _ = prepare_image(jpeg_bytes(4000, 3000, orientation=6))
        self.assertEqual(decode(data).size, (1659, 2212))

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

    def test_malformed_exif_does_not_make_a_photo_unreadable(self):
        # The JPEG needs a dpi in its header: without one Pillow parses (and swallows) the
        # bad EXIF while opening the file, and prepare_image never sees the error.
        for fmt, mime, kwargs in (("JPEG", "image/jpeg", {"dpi": (300, 300)}),
                                  ("PNG", "image/png", {}), ("WEBP", "image/webp", {})):
            with self.subTest(fmt):
                raw = _with_bad_exif(fmt, **kwargs)
                self.assertEqual(prepare_image(raw), (raw, mime))

    def test_malformed_exif_does_not_stop_a_photo_from_being_shrunk(self):
        data, mime = prepare_image(_with_bad_exif("JPEG", (3000, 2000), dpi=(300, 300)))
        self.assertEqual(mime, "image/jpeg")
        self.assertLessEqual(_tokens(decode(data).size), MAX_VISUAL_TOKENS)

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
