import json
import os
import unittest
from pathlib import Path
from unittest import mock

import grave_ui
import ui_logic
from extractor.csv_writer import append_rows, init_csv, init_processed, mark_processed
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

    def test_a_dry_run_leaves_a_chosen_output_folder_alone(self):
        self.app.dry_run_var.set(True)
        self.app._on_start()
        self.assertIn("--dry-run", self.launched[-1])
        self.assertNotIn("--output", self.launched[-1])
        self.assertFalse(self.out.exists())
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


class ProgressTests(AppCase):
    def feed(self, *lines):
        for line in lines:
            self.app._handle_line("stdout", line + "\n")

    def test_status_names_the_photo_being_processed(self):
        self.app._reset_run_state()
        self.feed("[1/3] Processing p_1_x.jpg ...")
        self.assertTrue(self.app.status_var.get().startswith("Obrađujem p_1_x.jpg · 0/3"))
        self.feed("[1/3] OK: p_1_x.jpg (1 record) — $0.0200 (total: $0.02)")
        self.assertTrue(self.app.status_var.get().startswith("1/3 — OK: 1"))

    def test_a_stopped_run_shows_real_progress_not_100_percent(self):
        self.app._reset_run_state()
        self.feed("[1/4] Processing a.jpg ...", "[1/4] OK: a.jpg (1 record) — $0.0100 (total: $0.01)",
                  "[2/4] Processing b.jpg ...")
        self.app._stop_requested = True
        self.app._on_proc_exit(130)
        self.assertEqual(float(self.app.progress.cget("value")), 1.0)
        self.dialogs["showinfo"].assert_called_once()

    def test_a_stopped_run_says_where_it_was_saved_and_how_to_carry_on(self):
        for retry, where, again in ((False, "output.csv", "Pokreni"),
                                    (True, "byhand_retry/output.csv", "Ponovi byhand/")):
            with self.subTest(retry=retry):
                self.app._reset_run_state()
                self.app._is_retry_run = retry
                self.app._stop_requested = True
                self.app._on_proc_exit(130)
                title, body = self.dialogs["showinfo"].call_args[0]
                self.assertEqual(title, "Zaustavljeno")
                self.assertIn(f"spremljeno u {where}.", body)
                self.assertIn(f"kliknite {again} i odaberite Nastavi", body)

    def test_stop_clicked_after_the_run_finished_counts_as_finished(self):
        self.app._reset_run_state()
        self.feed("[1/1] Processing a.jpg ...", "[1/1] OK: a.jpg (1 record) — $0.0100 (total: $0.01)",
                  "Done. 1 images processed. 1 succeeded, 0 partial, 0 failed.")
        self.app._stop_requested = True
        self.app._on_proc_exit(0)
        self.dialogs["showinfo"].assert_not_called()
        self.assertTrue(self.app.status_var.get().startswith("Gotovo"))

    def test_a_dry_run_leaves_no_stuck_progress_block(self):
        self.app._reset_run_state()
        self.app._launched_dry_run = True
        self.app.progress.configure(mode="indeterminate")   # as the real launch leaves it (stubbed here)
        self.app._on_proc_exit(0)
        self.assertEqual(str(self.app.progress.cget("mode")), "determinate")
        self.assertEqual(float(self.app.progress.cget("value")), 0.0)

    def test_an_external_kill_is_not_reported_as_a_stop(self):
        self.app._reset_run_state()
        self.app._on_proc_exit(-9)
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Obrada prekinuta")

    def test_the_interrupted_dialog_also_points_to_nastavi(self):
        self.app._reset_run_state()
        self.app._on_proc_exit(-9)
        self.assertIn("Nastavi", self.dialogs["showerror"].call_args[0][1])

    def test_every_verdict_is_counted_and_review_photos_are_remembered_until_the_next_run(self):
        self.app._reset_run_state()
        self.feed("[1/4] Processing a.jpg ...", "[1/4] OK: a.jpg (1 record) — $0.0100 (total: $0.01)",
                  "[2/4] Processing b.jpg ...", "[2/4] PARTIAL: b.jpg (Name or surname could not be read)",
                  "[3/4] Processing c.jpg ...", "[3/4] PARTIAL: c.jpg (Birth year is after death year)",
                  "[4/4] Processing OK (west).jpg ...", "[4/4] FAILED: OK (west).jpg (API call failed)")
        self.assertEqual(self.app.counters, {"ok": 1, "partial": 2, "failed": 1})
        self.assertEqual(self.app._flagged, {"b.jpg", "c.jpg", "OK (west).jpg"})
        self.app._reset_run_state()
        self.assertEqual((self.app._flagged, self.app._run_files, self.app._done_count), (set(), {}, 0))

    def test_the_log_says_which_way_a_bad_run_ended(self):
        for rc, marker, other in ((-9, "[prekinuto, izlazni kod -9]", "neuspjelo"),
                                  (1, "[neuspjelo, izlazni kod 1]", "prekinuto")):
            with self.subTest(rc=rc):
                self.app._reset_run_state()
                self.app._on_proc_exit(rc)
                log = self.app.log.get("1.0", "end")
                self.assertIn(marker, log)
                self.assertNotIn(other, log)


class ExistingOutputTests(RunCase):
    def make_output(self, processed=("p_1_x.jpg",)):
        self.out.mkdir(parents=True)
        init_csv(self.out / "output.csv")
        append_rows(self.out / "output.csv", [["1", "Ivan", "Horvat", 1920, 1999, "", "p_1_x.jpg"]])
        init_processed(self.out)
        for name in processed:
            mark_processed(self.out, Path(name))

    def test_nastavi_resumes_and_the_dialog_shows_what_is_done(self):
        self.make_output()
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume") as ask:
            self.app._on_start()
        self.assertEqual(ask.call_args[0][1:], (1, 1, 2, None))
        self.assertIn("--resume", self.launched[-1])

    def test_prepisi_backs_up_the_csv_and_byhand_together(self):
        self.make_output()
        (self.out / "byhand").mkdir()
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(b"x")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"):
            self.app._on_start()
        backups = sorted(p.name for p in self.out.iterdir() if ".bak" in p.name)
        self.assertEqual(len(backups), 2)
        self.assertTrue(backups[0].startswith("byhand.") and backups[1].startswith("output."))
        self.assertNotIn("--resume", self.launched[-1])

    def test_the_two_backups_share_one_timestamp(self):
        self.make_output()
        (self.out / "byhand").mkdir()
        # a second strftime call would hand out the second stamp
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"), \
                mock.patch.object(grave_ui.time, "strftime", side_effect=["20260101-000001", "20260101-000002"]):
            self.app._on_start()
        self.assertEqual(sorted(p.name for p in self.out.iterdir() if ".bak" in p.name),
                         ["byhand.20260101-000001.bak", "output.20260101-000001.bak.csv"])

    def test_photos_done_from_another_folder_do_not_count_as_done_here(self):
        self.make_output(processed=("p_1_x.jpg", "p_7_elsewhere.jpg"))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value=None) as ask:
            self.app._on_start()
        self.assertEqual(ask.call_args[0][2:4], (1, 2))          # done, total

    def test_a_backup_that_fails_stops_before_anything_is_launched(self):
        self.make_output()
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"), \
                mock.patch.object(Path, "replace", side_effect=PermissionError("locked")):
            self.app._on_start()
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Ne mogu spremiti kopiju")
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())
        self.assertTrue((self.out / "output.csv").exists())

    def test_an_excel_saved_csv_blocks_nastavi_with_a_reason_and_frees_the_lock(self):
        self.out.mkdir(parents=True)
        (self.out / "output.csv").write_bytes("ID;Name\r\n1;Mišo\r\n".encode("cp1250"))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value=None) as ask:
            self.app._on_start()
        self.assertIn("starija verzija", ask.call_args[0][4])
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_the_dialog_greys_out_nastavi_when_blocked(self):
        win, choice = self.app._build_existing_output_dialog(Path("x/output.csv"), 3, 1, 2, "razlog")
        self.assertEqual(str(win.btn_resume.cget("state")), "disabled")
        win.btn_fresh.invoke()
        self.assertEqual(choice["value"], "fresh")

    def test_a_missing_csv_with_a_processed_list_warns_first(self):
        self.out.mkdir(parents=True)
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        self.dialogs["askyesno"].return_value = False
        self.app._on_start()
        self.assertEqual(self.launched, [])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions as a normal user")
    def test_a_locked_csv_is_caught_before_launch(self):
        self.make_output()
        os.chmod(self.out / "output.csv", 0o444)
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume"):
            self.app._on_start()
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Datoteka je zaključana")
        self.assertEqual(self.launched, [])

    def test_the_outputs_byhand_folder_is_refused_as_input(self):
        byhand = self.out / "byhand"
        byhand.mkdir(parents=True)
        (byhand / "p_1_x.jpg").write_bytes(jpeg_bytes())
        self.app.input_var.set(str(byhand))
        self.app._on_start()
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Neispravna ulazna mapa")
        self.assertEqual(self.launched, [])


class CallbackErrorTests(AppCase):
    def test_errors_in_callbacks_reach_the_log_and_a_dialog(self):
        try:
            raise ValueError("boom")
        except ValueError as e:
            self.app._report_callback_exception(type(e), e, e.__traceback__)
        self.assertIn("ValueError: boom", self.app.log.get("1.0", "end"))
        self.dialogs["showerror"].assert_called_once()

    def test_tk_hands_the_errors_of_its_callbacks_to_the_app(self):
        try:
            raise ValueError("boom")
        except ValueError:
            self.root._report_exception()        # what Tk calls when a callback raises
        self.assertIn("ValueError: boom", self.app.log.get("1.0", "end"))
        self.dialogs["showerror"].assert_called_once()

    def test_the_failure_dialog_explains_and_points_to_nastavi(self):
        self.app._reset_run_state()
        self.app._handle_line("stderr", "error: [api-402] API call failed: Error code: 402\n")
        self.app._on_proc_exit(1)
        title, body = self.dialogs["showerror"].call_args[0]
        self.assertEqual(title, "Obrada nije uspjela")
        self.assertIn("naplat", body)
        self.assertIn("Nastavi", body)
