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

    def test_a_damaged_file_reads_as_empty(self):
        path = self.tmp / "ui.json"
        path.write_text("[1, 2", encoding="utf-8")
        self.assertEqual(ui_logic.load_settings(path, self.tmp / "none.json"), {})
