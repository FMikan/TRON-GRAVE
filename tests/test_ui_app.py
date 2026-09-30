import json
import os
from unittest import mock

import grave_ui
import ui_logic
from tests.helpers import jpeg_bytes
from tests.ui_harness import AppCase


class LabelTests(AppCase):
    def test_widgets_speak_croatian(self):
        self.assertEqual(self.app.btn_start.cget("text"), "▶  Pokreni")
        self.assertEqual(self.app.btn_retry_byhand.cget("text"), "Ponovi byhand/")
        self.assertEqual(self.app.status_var.get(), "Spremno.")

    def test_model_dropdown_shows_names_and_maps_back_to_ids(self):
        self.assertEqual(list(self.app.model_combo.cget("values")),
                         [ui_logic.MODEL_LABELS[m] for m in ui_logic.MODELS])
        self.app.model_var.set(ui_logic.MODEL_LABELS["claude-opus-5-5"])
        self.app.effort_var.set("maksimalan")
        self.assertEqual((self.app._model_id(), self.app._effort_id()), ("claude-opus-5-5", "max"))

    def test_a_retired_saved_model_falls_back_to_the_default(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"model": "claude-opus-4-8", "effort": "ultra"}))
        self.app._load_settings()
        self.assertEqual((self.app._model_id(), self.app._effort_id()), ("claude-sonnet-5", "high"))


class ApiKeyTests(AppCase):
    def saved(self) -> dict:
        return json.loads(self.settings_path.read_text(encoding="utf-8")) if self.settings_path.exists() else {}

    def test_other_changes_do_not_save_the_key(self):
        self.app.api_key_var.set("sk-typed")
        self.app._on_model_change()
        self.assertNotIn("api_key", self.saved())

    def test_spremi_saves_the_key_and_says_so(self):
        self.app.api_key_var.set("  sk-saved  ")
        self.app._on_save_key()
        self.assertEqual(self.saved()["api_key"], "sk-saved")
        self.assertIn("spremljen", self.app.status_var.get())

    def test_a_saved_key_survives_later_changes(self):
        self.app.api_key_var.set("sk-saved")
        self.app._on_save_key()
        self.app._on_model_change()
        self.assertEqual(self.saved()["api_key"], "sk-saved")

    def test_a_saved_key_survives_a_restart_and_an_unsaved_edit(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"api_key": "sk-old"}), encoding="utf-8")
        self.app._load_settings()
        self.app.api_key_var.set("sk-typed")
        self.app._on_model_change()
        saved = self.saved()
        self.assertEqual((saved["api_key"], saved["model"]), ("sk-old", "claude-sonnet-5"))

    def test_a_failed_save_is_reported(self):
        self._patch(grave_ui.ui_logic, "write_settings", mock.Mock(side_effect=OSError("disk full")))
        self.app._on_save_key()
        self.dialogs["showerror"].assert_called_once()
        self.assertNotIn("spremljen", self.app.status_var.get())

    def test_env_file_is_read_without_touching_the_environment(self):
        (self.tmp / ".env").write_text("ANTHROPIC_API_KEY=sk-env\n", encoding="utf-8")
        self._patch(grave_ui, "PROJECT_DIR", self.tmp)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.app._resolve_api_key(), "sk-env")
            self.assertNotIn("ANTHROPIC_API_KEY", os.environ)

    def test_key_order_is_field_then_exported_variable_then_env_file(self):
        (self.tmp / ".env").write_text("ANTHROPIC_API_KEY=sk-old\n", encoding="utf-8")
        self._patch(grave_ui, "PROJECT_DIR", self.tmp)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-new"}, clear=True):
            self.assertEqual(self.app._resolve_api_key(), "sk-new")
            self.app.api_key_var.set("sk-typed")
            self.assertEqual(self.app._resolve_api_key(), "sk-typed")


class RunCase(AppCase):
    """An App with an input folder of two photos, an output folder and a key."""

    def setUp(self):
        super().setUp()
        self.inp = self.tmp / "slike"
        self.inp.mkdir()
        for name in ("p_1_x.jpg", "p_2_x.jpg"):
            (self.inp / name).write_bytes(jpeg_bytes())
        self.out = self.tmp / "Groblje Čakovec" / "izlaz"
        self.app.input_var.set(str(self.inp))
        self.app.output_var.set(str(self.out))
        self.app.api_key_var.set("sk-test")

    def lock(self):
        return self.out / ".tron-grave.lock"


class LockAndDryRunTests(RunCase):
    def test_a_dry_run_needs_no_output_folder_and_takes_no_lock(self):
        self.app.output_var.set("")
        self.app.dry_run_var.set(True)
        self.app._on_start()
        self.assertIn("--dry-run", self.launched[-1])
        self.assertNotIn("--output", self.launched[-1])
        self.assertEqual(list(self.tmp.rglob(".tron-grave.lock")), [])

    def test_a_run_takes_a_token_lock_and_releases_it_on_exit(self):
        self.app._on_start()
        self.assertTrue(self.lock().read_text(encoding="utf-8").startswith(f"{os.getpid()}:"))
        # the command carries model and effort ids, not the dropdown labels
        cmd = self.launched[-1]
        pairs = list(zip(cmd, cmd[1:]))
        self.assertIn(("--model", "claude-sonnet-5"), pairs)
        self.assertIn(("--effort", "high"), pairs)
        self.app._on_proc_exit(0)
        self.assertFalse(self.lock().exists())

    def test_exit_leaves_a_lock_someone_else_took(self):
        self.app._on_start()
        self.lock().write_text("other:token", encoding="utf-8")
        self.app._on_proc_exit(0)
        self.assertTrue(self.lock().exists())
