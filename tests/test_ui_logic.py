import unittest
from pathlib import Path

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
