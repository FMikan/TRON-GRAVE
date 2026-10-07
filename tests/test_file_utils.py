import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from extractor.file_utils import (
    back_up_outputs, clear_byhand, copy_to_byhand, extract_id, is_heic, is_supported_image,
    remove_from_byhand,
)


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


class ByhandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "in" / "a.jpg"
        self.src.parent.mkdir()
        self.src.write_bytes(b"one")
        self.byhand = self.tmp / "out" / "byhand"

    def test_copy_overwrites_an_earlier_read_only_copy(self):
        os.chmod(self.src, stat.S_IREAD)            # "Protect" set on the camera
        copy_to_byhand(self.src, self.byhand)
        os.chmod(self.src, stat.S_IREAD | stat.S_IWRITE)
        self.src.write_bytes(b"two")
        copy_to_byhand(self.src, self.byhand)
        self.assertEqual((self.byhand / "a.jpg").read_bytes(), b"two")

    def test_copying_a_file_that_is_already_in_byhand_is_a_no_op(self):
        self.byhand.mkdir(parents=True)
        inside = self.byhand / "b.jpg"
        inside.write_bytes(b"keep")
        copy_to_byhand(inside, self.byhand)
        self.assertEqual(inside.read_bytes(), b"keep")

    def test_remove_from_byhand(self):
        copy_to_byhand(self.src, self.byhand)
        remove_from_byhand(self.src, self.byhand)
        self.assertFalse((self.byhand / "a.jpg").exists())
        remove_from_byhand(self.src, self.byhand)   # already gone: no error

    def test_clear_byhand_removes_only_photo_copies(self):
        copy_to_byhand(self.src, self.byhand)
        (self.byhand / "notes.txt").write_text("mine")
        clear_byhand(self.byhand)
        self.assertEqual([p.name for p in self.byhand.iterdir()], ["notes.txt"])
        clear_byhand(self.tmp / "missing")          # no folder: no error


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "out"
        (self.out / "byhand").mkdir(parents=True)
        (self.out / "byhand" / "a.jpg").write_bytes(b"x")
        (self.out / "byhand_retry").mkdir()
        (self.out / "output.csv").write_text("ID\n1\n", encoding="utf-8")

    def test_everything_moves_aside_under_one_stamp(self):
        moved = back_up_outputs(self.out, "20260101-000001")
        self.assertEqual(sorted(p.name for p in moved),
                         ["byhand.20260101-000001.bak", "byhand_retry.20260101-000001.bak",
                          "output.20260101-000001.bak.csv"])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(p.name for p in moved))

    def test_what_is_missing_is_skipped(self):
        shutil.rmtree(self.out / "byhand")
        shutil.rmtree(self.out / "byhand_retry")
        self.assertEqual([p.name for p in back_up_outputs(self.out, "s")], ["output.s.bak.csv"])

    def test_a_second_backup_in_the_same_second_gets_its_own_name(self):
        back_up_outputs(self.out, "20260101-000001")
        (self.out / "output.csv").write_text("ID\n2\n", encoding="utf-8")
        moved = back_up_outputs(self.out, "20260101-000001")
        self.assertEqual([p.name for p in moved], ["output.20260101-000001-2.bak.csv"])
        self.assertEqual((self.out / "output.20260101-000001.bak.csv").read_text(encoding="utf-8"), "ID\n1\n")

    def test_a_folder_that_will_not_move_puts_everything_back(self):
        with mock.patch.object(Path, "rename", side_effect=PermissionError("in use")):
            with self.assertRaises(PermissionError):
                back_up_outputs(self.out, "s")
        self.assertTrue((self.out / "output.csv").exists())
        self.assertEqual(list(self.out.glob("*.bak*")), [])
