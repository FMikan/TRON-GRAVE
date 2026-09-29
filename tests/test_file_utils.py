import unittest
from pathlib import Path

from extractor.file_utils import extract_id, is_heic, is_supported_image


class SupportedImageTests(unittest.TestCase):
    def test_regular_photos_are_supported(self):
        for name in ("a.jpg", "b.JPEG", "c.png", "d.webp"):
            self.assertTrue(is_supported_image(Path(name)), name)

    def test_dotfiles_and_appledouble_twins_are_skipped(self):
        for name in ("._IMG_0001.jpg", ".hidden.png", "._pokojnici-ploca_305_22-07-2026_11-29-39.jpg"):
            self.assertFalse(is_supported_image(Path(name)), name)

    def test_heic_is_recognised_but_not_supported(self):
        self.assertTrue(is_heic(Path("IMG_1.HEIC")))
        self.assertTrue(is_heic(Path("IMG_2.heif")))
        self.assertFalse(is_heic(Path("._IMG_3.heic")))
        self.assertFalse(is_supported_image(Path("IMG_1.HEIC")))


class ExtractIdTests(unittest.TestCase):
    def test_plate_id_is_kept(self):
        self.assertEqual(extract_id(Path("pokojnici-ploca_305_22-07-2026_11-29-39.jpg")), ("305", True))

    def test_bare_digit_stamp_falls_back_to_stem(self):
        self.assertEqual(extract_id(Path("IMG_20240513_142233.jpg")), ("IMG_20240513_142233", False))

    def test_dashed_date_falls_back_to_stem(self):
        self.assertEqual(extract_id(Path("photo_2024-05-13_14-22-33.jpg")),
                         ("photo_2024-05-13_14-22-33", False))

    def test_day_first_and_dotted_dates_fall_back_to_stem(self):
        self.assertFalse(extract_id(Path("pokojnici-ploca_22-07-2026_11-29-39.jpg"))[1])
        self.assertFalse(extract_id(Path("grob_13.05.2024_x.jpg"))[1])

    def test_short_dashed_plot_number_is_kept(self):
        self.assertEqual(extract_id(Path("grob_12-3_foto.jpg")), ("12-3", True))
