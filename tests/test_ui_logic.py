import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ui_logic
from extractor.csv_writer import append_rows, init_csv
from extractor.pricing import MODEL_PRICING

REPO = Path(__file__).resolve().parents[1]


class ModelLabelTests(unittest.TestCase):
    def test_labels_map_back_to_ids(self):
        for model in ui_logic.MODELS:
            self.assertEqual(ui_logic.model_id(ui_logic.MODEL_LABELS[model]), model)
        for effort in ui_logic.EFFORT_LEVELS:
            self.assertEqual(ui_logic.effort_id(ui_logic.EFFORT_LABELS[effort]), effort)

    def test_the_current_models_are_offered_and_priced(self):
        for model in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"):
            self.assertIn(model, ui_logic.MODELS)
        for model in ui_logic.MODELS:
            self.assertIn(model, MODEL_PRICING)
        self.assertEqual((ui_logic.DEFAULT_MODEL, ui_logic.RETRY_MODEL),
                         ("claude-sonnet-5", "claude-opus-5-5"))


class CroatianUiTests(unittest.TestCase):
    ENGLISH = ["Browse…", "Dry run", "Open output.csv", "Open byhand", "Retry byhand", "Missing folder",
               "Bad input", "No images", "Missing API key", "Lockfile present", "Run failed",
               '"Stopped"', "Stopped —", "Starting…", '"Ready."', "Run in progress", "Not found",
               "Cannot open", "Find:", "Input folder", "Output folder", "API Key", '"Effort"',
               "manual ·", "manual,", "failed ·", "Done —", "[exit code", "failed, exit code",
               "stop requested", "Failed to launch", "See the log below", "ETA", "Found ", "Approx.",
               "Tombstone inscription extractor"]

    def test_no_english_ui_text_is_left(self):
        source = (REPO / "grave_ui.py").read_text(encoding="utf-8")
        for phrase in self.ENGLISH:
            self.assertNotIn(phrase, source, phrase)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_each_os_gets_its_own_config_folder(self):
        home = Path("/h")
        self.assertEqual(ui_logic.settings_path("win32", {"APPDATA": "C:/Users/a/AppData/Roaming"}, home),
                         Path("C:/Users/a/AppData/Roaming/tron-grave/ui.json"))
        self.assertEqual(ui_logic.settings_path("darwin", {}, home),
                         home / "Library" / "Application Support" / "tron-grave" / "ui.json")
        self.assertEqual(ui_logic.settings_path("linux", {"XDG_CONFIG_HOME": "/x"}, home),
                         Path("/x/tron-grave/ui.json"))
        self.assertEqual(ui_logic.settings_path("linux", {}, home), home / ".config" / "tron-grave" / "ui.json")

    def test_the_old_file_is_moved_once(self):
        legacy = self.tmp / "old" / "ui.json"
        legacy.parent.mkdir()
        legacy.write_text('{"output": "/o"}', encoding="utf-8")
        new = self.tmp / "new" / "ui.json"
        self.assertEqual(ui_logic.load_settings(new, legacy), {"output": "/o"})
        self.assertFalse(legacy.exists())
        self.assertEqual(ui_logic.load_settings(new, legacy), {"output": "/o"})

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_the_file_is_owner_only_from_the_first_byte(self):
        path = self.tmp / "cfg" / "ui.json"
        old = os.umask(0o022)
        try:
            # chmod is stubbed so only the mode the file was created with can make this pass
            with mock.patch.object(ui_logic.os, "chmod"):
                ui_logic.write_settings(path, {"api_key": "k"})
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipIf(sys.platform == "win32", "POSIX permissions")
    def test_a_leftover_temp_file_is_locked_down_before_the_key_lands(self):
        path = self.tmp / "cfg" / "ui.json"
        path.parent.mkdir()
        leftover = path.with_name("ui.json.tmp")
        leftover.write_text("stale", encoding="utf-8")
        os.chmod(leftover, 0o644)
        modes = []
        real_dump = json.dump

        def dump_and_note_mode(data, f):
            modes.append(stat.S_IMODE(os.fstat(f.fileno()).st_mode))
            real_dump(data, f)

        with mock.patch.object(ui_logic.json, "dump", dump_and_note_mode):
            ui_logic.write_settings(path, {"api_key": "k"})
        self.assertEqual(modes, [0o600])

    def test_a_failed_write_leaves_no_temp_file_and_the_old_settings(self):
        path = self.tmp / "ui.json"
        ui_logic.write_settings(path, {"output": "/old"})
        with mock.patch.object(ui_logic.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(OSError):
                ui_logic.write_settings(path, {"output": "/new"})
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["ui.json"])
        self.assertEqual(ui_logic.load_settings(path, self.tmp / "none.json"), {"output": "/old"})

    def test_a_damaged_file_reads_as_empty(self):
        path = self.tmp / "ui.json"
        path.write_text("[1, 2", encoding="utf-8")
        self.assertEqual(ui_logic.load_settings(path, self.tmp / "none.json"), {})

    def test_an_unreadable_config_folder_reads_as_empty(self):
        # older Pythons re-raise PermissionError from Path.exists() for a folder they cannot enter
        with mock.patch.object(Path, "exists", side_effect=PermissionError):
            self.assertEqual(ui_logic.load_settings(self.tmp / "ui.json", self.tmp / "none.json"), {})


class LockTests(unittest.TestCase):
    def test_only_the_owner_removes_the_lock(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        lock = tmp / "Groblje Čakovec" / ui_logic.LOCK_NAME
        lock.parent.mkdir()
        mine = ui_logic.new_lock_token()
        lock.write_text(mine, encoding="utf-8")
        ui_logic.release_lock(lock, "someone:else")
        self.assertTrue(lock.exists())
        ui_logic.release_lock(lock, mine)
        self.assertFalse(lock.exists())
        ui_logic.release_lock(lock, mine)          # already gone: no error

    def test_a_lock_file_that_is_not_text_is_left_in_place(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        lock = tmp / ui_logic.LOCK_NAME
        lock.write_bytes(b"\xff\xfe")
        ui_logic.release_lock(lock, ui_logic.new_lock_token())     # closing the window must not fail
        self.assertEqual(lock.read_bytes(), b"\xff\xfe")


class ProgressParsingTests(unittest.TestCase):
    def test_start_and_result_lines_even_with_awkward_names(self):
        parse = ui_logic.parse_progress
        self.assertEqual(parse("[3/10] Processing OK (west section).jpg ...\n"),
                         ("start", 3, 10, "OK (west section).jpg"))
        self.assertEqual(parse("[3/10] FAILED: OK (west section).jpg (API call failed)\n"),
                         ("result", 3, 10, "FAILED", None))
        self.assertEqual(parse("[4/10] OK: ploča Čakovec.jpg (2 records) — $0.0184 (total: $2.35)\n"),
                         ("result", 4, 10, "OK", 2.35))
        self.assertIsNone(parse("Resume: skipping 3 already-processed image(s).\n"))

    def test_exit_classification(self):
        classify = ui_logic.classify_exit
        self.assertEqual(classify(0, False, True, False), "done")
        self.assertEqual(classify(2, False, True, False), "done")
        self.assertEqual(classify(2, False, False, False), "failed")      # argparse error, no Done line
        self.assertEqual(classify(0, False, False, True), "done")         # dry run
        self.assertEqual(classify(130, True, False, False), "stopped")
        self.assertEqual(classify(1, True, False, False), "stopped")      # Windows force-kill
        self.assertEqual(classify(0, True, True, False), "done")          # Stop clicked after Done
        self.assertEqual(classify(1, True, True, False), "done")          # ... taskkill on the bootloader
        self.assertEqual(classify(130, True, True, False), "done")        # ... SIGINT just before exit
        self.assertEqual(classify(-9, True, True, False), "done")         # ... escalated to SIGKILL
        self.assertEqual(classify(-9, False, False, False), "interrupted")
        self.assertEqual(classify(130, False, False, False), "interrupted")
        self.assertEqual(classify(1, False, False, False), "failed")


class RunTextTests(unittest.TestCase):
    def test_fatal_tags_are_explained_in_croatian(self):
        self.assertIn("naplat", ui_logic.explain_failure("error: [api-402] API call failed: ..."))
        self.assertIn("zaključan", ui_logic.explain_failure("error: [csv-locked] Cannot write"))
        self.assertIsNone(ui_logic.explain_failure("Traceback (most recent call last):"))

    def test_every_fatal_tag_and_resume_problem_has_a_text(self):
        for tag in ("api-401", "api-402", "api-403", "api-404", "spend-cap", "api-down",
                    "csv-locked", "resume-refused", "input-is-byhand"):
            self.assertIn(tag, ui_logic.FATAL_EXPLANATIONS)
        for code in ("old-format", "no-processed", "missing-csv", "unreadable"):
            self.assertIn(code, ui_logic.RESUME_BLOCKERS)

    def test_the_row_count_survives_an_excel_saved_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / "output.csv"
        path.write_bytes("ID;Name;Surname\r\n1;Mišo;Kovač\r\n2;Ana;Babić\r\n".encode("cp1250"))
        self.assertEqual(ui_logic.csv_data_rows(path), 2)
        self.assertEqual(ui_logic.csv_data_rows(tmp / "missing.csv"), 0)


class SameDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "out" / "byhand").mkdir(parents=True)
        (self.tmp / "out" / "elsewhere").mkdir()

    def test_another_spelling_of_one_folder_is_the_same_folder(self):
        byhand = self.tmp / "out" / "byhand"
        self.assertTrue(ui_logic.same_dir(self.tmp / "out" / "elsewhere" / ".." / "byhand", byhand))
        self.assertFalse(ui_logic.same_dir(self.tmp / "out" / "elsewhere", byhand))

    def test_a_folder_that_does_not_exist_yet_is_compared_by_its_path(self):
        missing = self.tmp / "new" / "byhand"
        self.assertTrue(ui_logic.same_dir(self.tmp / "new" / "x" / ".." / "byhand", missing))
        self.assertFalse(ui_logic.same_dir(self.tmp / "new" / "other", missing))

    def test_the_filesystems_verdict_beats_the_spelling(self):
        # On macOS and Windows out/ByHand and out/byhand are one folder, yet resolve() keeps
        # the typed case: the filesystem's own answer has to decide.
        with mock.patch.object(ui_logic.os.path, "samefile", return_value=True):
            self.assertTrue(ui_logic.same_dir(self.tmp / "out" / "elsewhere", self.tmp / "out" / "byhand"))


class EstimateTests(unittest.TestCase):
    def test_the_fallback_counts_the_image_tokens(self):
        cost, secs, measured = ui_logic.estimate({}, "claude-sonnet-5", "high", 100)
        self.assertFalse(measured)
        self.assertGreater(cost / 100, 4_700 * 2 / 1_000_000)   # the photo alone on Sonnet 5
        self.assertEqual(secs, 100 * ui_logic.SECS_PER_IMAGE_GUESS)

    def test_the_fallback_price_of_a_photo_on_sonnet_5(self):
        # (4,700 image + 400 text) x $2/M + 4,500 cached prompt x $2/M x 0.10 + 1,500 output x $10/M
        self.assertAlmostEqual(ui_logic.fallback_cost_per_image("claude-sonnet-5"), 0.0261, places=4)

    def test_measured_runs_take_over(self):
        stats = ui_logic.record_run({}, "claude-opus-5-5", "high", cost=3.0, secs=600.0, n=60)
        self.assertEqual(ui_logic.estimate(stats, "claude-opus-5-5", "high", 10), (0.5, 100.0, True))

    def test_an_empty_run_teaches_nothing(self):
        self.assertEqual(ui_logic.record_run({}, "claude-sonnet-5", "high", 0.0, 5.0, 0), {})

    def test_hand_edited_stats_fall_back_quietly(self):
        stats = {"claude-sonnet-5|high": {"cost": "x", "secs": 1, "n": 2}}
        self.assertFalse(ui_logic.estimate(stats, "claude-sonnet-5", "high", 1)[2])

    def test_stats_that_are_not_finite_fall_back_quietly(self):
        # json reads a hand-edited NaN or Infinity, and either one would crash the duration text
        for entry in ({"cost": float("nan"), "secs": 5.0, "n": 2},
                      {"cost": 1.0, "secs": float("inf"), "n": 2}):
            with self.subTest(entry=entry):
                stats = {"claude-sonnet-5|high": entry}
                cost, secs, measured = ui_logic.estimate(stats, "claude-sonnet-5", "high", 1)
                self.assertFalse(measured)
                self.assertEqual((cost, secs), (ui_logic.fallback_cost_per_image("claude-sonnet-5"),
                                                ui_logic.SECS_PER_IMAGE_GUESS))

    def test_a_number_too_big_for_a_float_falls_back_quietly(self):
        # a 400-digit integer overflows the division, and estimate runs from __init__
        stats = {"claude-sonnet-5|high": {"cost": 10**400, "secs": 1, "n": 1}}
        self.assertFalse(ui_logic.estimate(stats, "claude-sonnet-5", "high", 1)[2])

    def test_a_saved_nan_or_infinity_is_started_over_not_added_to(self):
        for entry in ({"cost": float("nan"), "secs": 5.0, "n": 1},
                      {"cost": 1.0, "secs": float("inf"), "n": 1}):
            with self.subTest(entry=entry):
                stats = ui_logic.record_run({"claude-sonnet-5|high": entry}, "claude-sonnet-5", "high",
                                            cost=2.0, secs=30.0, n=3)
                self.assertEqual(stats["claude-sonnet-5|high"], {"cost": 2.0, "secs": 30.0, "n": 3})

    def test_a_saved_number_too_big_for_a_float_is_started_over(self):
        for entry in ({"cost": 10**400, "secs": 5.0, "n": 1},
                      {"cost": 1.0, "secs": 5.0, "n": float("inf")}):
            with self.subTest(entry=entry):
                stats = ui_logic.record_run({"claude-sonnet-5|high": entry}, "claude-sonnet-5", "high",
                                            cost=2.0, secs=30.0, n=3)
                self.assertEqual(stats["claude-sonnet-5|high"], {"cost": 2.0, "secs": 30.0, "n": 3})


class RetrySettingsTests(unittest.TestCase):
    def test_retry_never_steps_down(self):
        retry = ui_logic.retry_settings
        self.assertEqual(retry("claude-sonnet-5", "low"), ("claude-opus-5-5", "high"))
        self.assertEqual(retry("claude-fable-5-1", "max"), ("claude-fable-5-1", "max"))
        self.assertEqual(retry("claude-opus-5", "xhigh"), ("claude-opus-5-5", "xhigh"))
        self.assertEqual(retry("unknown", "weird"), ("claude-opus-5-5", "high"))


class TallyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_only_this_runs_flagged_photos_count(self):
        path = self.tmp / "output.csv"
        init_csv(path)
        append_rows(path, [["1", "A", "B", "", "", "bez god. smrti", "a.jpg"],
                           ["2", "", "B", "", "", "fali: ime; god. smrti nečitka", "b.jpg"],
                           ["3", "", "C", "", "", "fali: ime", "c.jpg"],
                           ["4", "", "D", "", "", "fali: ime", "old.jpg"]])
        self.assertEqual(ui_logic.tally_review_notes(path, {"b.jpg", "c.jpg"}),
                         [("fali: ime", 2), ("god. smrti nečitka", 1)])

    def test_a_csv_saved_in_the_windows_code_page_still_tallies(self):
        # The Name holds 0x9A (cp1250 "š"), which is not UTF-8: it is replaced, never raised on.
        path = self.tmp / "output.csv"
        path.write_bytes("ID,Name,Surname,Year of Birth,Year of Death,Notes,File\r\n"
                         "1,Mišo,B,,,fali: ime,a.jpg\r\n".encode("cp1250"))
        self.assertEqual(ui_logic.tally_review_notes(path, {"a.jpg"}), [("fali: ime", 1)])

    def test_a_missing_csv_has_no_reasons(self):
        self.assertEqual(ui_logic.tally_review_notes(self.tmp / "output.csv", {"a.jpg"}), [])


class WindowsHelperTests(unittest.TestCase):
    def test_scaled_geometry_stays_on_screen(self):
        # A scale above 1 only arrives once the process is DPI-aware, and Tk then reports physical
        # pixels: 150 % on a 1920x1080 panel asks for 1770x1050 and is held to 90 % of the screen.
        self.assertEqual(ui_logic.scaled_geometry(1180, 700, 1.5, 1920, 1080), (1728, 972))
        self.assertEqual(ui_logic.scaled_geometry(1180, 700, 1.0, 2560, 1440), (1180, 700))
