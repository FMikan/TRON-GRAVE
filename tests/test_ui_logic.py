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
