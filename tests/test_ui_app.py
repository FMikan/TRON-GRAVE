import json
import os
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

import grave_ui
import ui_logic
from extractor.csv_writer import append_rows, init_csv, init_processed, mark_processed
from tests.helpers import jpeg_bytes
from tests.ui_harness import REAL_SHOW_SUMMARY, AppCase


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

    def test_a_stopped_dry_run_claims_no_save(self):
        self.app._reset_run_state()
        self.app._launched_dry_run = True
        self.app._stop_requested = True
        self.app._on_proc_exit(130)
        self.dialogs["showinfo"].assert_not_called()
        self.assertEqual(self.app.status_var.get(), "Probni prolaz zaustavljen.")

    def test_a_dry_run_that_ends_badly_has_no_resume_hint(self):
        for rc in (1, -9):          # failed, and killed without a Stop click
            with self.subTest(rc=rc):
                self.app._reset_run_state()
                self.app._launched_dry_run = True
                self.app._on_proc_exit(rc)
                body = self.dialogs["showerror"].call_args[0][1]
                self.assertNotIn("Nastavi", body)
                self.assertEqual(body, body.rstrip())        # no dangling blank line

    def test_the_resume_hint_is_left_out_where_nastavi_cannot_help(self):
        for line, hint in (
                ("error: [resume-refused] output.csv has different columns, so this run can't be resumed.", False),
                ("error: [input-is-byhand] The input folder is this output folder's byhand/ folder.", False),
                ("error: [api-402] API call failed: Error code: 402", True)):
            with self.subTest(line=line):
                self.app._reset_run_state()
                self.app._handle_line("stderr", line + "\n")
                self.app._on_proc_exit(1)
                body = self.dialogs["showerror"].call_args[0][1]
                self.assertEqual("Nastavi" in body, hint)
                if not hint:
                    self.assertEqual(body, body.rstrip())    # no dangling blank line

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

    def test_a_byhand_that_will_not_move_puts_the_csv_back(self):
        # Windows: a photo in byhand/ open in a viewer. output.csv was already moved aside by then.
        self.make_output()
        (self.out / "byhand").mkdir()
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(b"x")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"), \
                mock.patch.object(Path, "rename", side_effect=PermissionError("locked")):
            self.app._on_start()
        title, body = self.dialogs["showerror"].call_args[0]
        self.assertEqual(title, "Ne mogu spremiti kopiju")
        self.assertIn("output.csv i slike iz byhand/", body)     # the hint names both causes
        self.assertTrue((self.out / "output.csv").exists())
        self.assertEqual(list(self.out.glob("output.*.bak.csv")), [])
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

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

    def open_dialog(self):
        """The Nastavi / Prepiši / Odustani dialog with nothing blocking it, never mapped."""
        win, choice = self.app._build_existing_output_dialog(Path("x/output.csv"), 3, 1, 2, None)
        win.withdraw()
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        return win, choice

    def test_the_dialog_offers_nastavi_when_nothing_blocks_it(self):
        win, choice = self.open_dialog()
        self.assertEqual(str(win.btn_resume.cget("state")), "normal")
        win.btn_resume.invoke()
        self.assertEqual(choice["value"], "resume")
        self.assertFalse(win.winfo_exists())

    # Escape is bound to the same pick(None), but Tk delivers no key event to a window that is
    # never mapped, so it is left untested rather than flashing the dialog on screen.
    def test_odustani_and_closing_the_window_both_answer_none(self):
        for how, close in (("Odustani", lambda win: win.btn_cancel.invoke()),
                           ("window close", lambda win: win.tk.call(win.protocol("WM_DELETE_WINDOW")))):
            with self.subTest(how):
                win, choice = self.open_dialog()
                choice["value"] = "resume"        # preset, so the None can only come from the close
                close(win)
                self.assertIsNone(choice["value"])
                self.assertFalse(win.winfo_exists())

    def test_a_missing_csv_with_a_processed_list_warns_first(self):
        self.out.mkdir(parents=True)
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        self.dialogs["askyesno"].return_value = False
        self.app._on_start()
        title, body = self.dialogs["askyesno"].call_args[0]
        self.assertEqual(title, "Nedostaje output.csv")
        self.assertIn("Svejedno krenuti ispočetka?", body)       # says what Da does
        self.assertNotIn("Nastaviti?", body)
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_starting_over_after_a_missing_csv_is_a_fresh_run_that_sets_byhand_aside(self):
        self.out.mkdir(parents=True)
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        (self.out / "byhand").mkdir()
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(b"x")
        self.app._on_start()                                   # the harness answers Da
        self.assertEqual(self.dialogs["askyesno"].call_args[0][0], "Nedostaje output.csv")
        self.assertNotIn("--resume", self.launched[-1])
        self.assertEqual(len(list(self.out.glob("byhand.*.bak"))), 1)
        self.assertFalse((self.out / "byhand").exists())

    def test_an_empty_output_csv_is_called_empty_not_missing(self):
        self.out.mkdir(parents=True)
        (self.out / "output.csv").write_bytes(b"")
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value=None) as ask:
            self.app._on_start()
        self.assertIn("prazan", ask.call_args[0][4])

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions as a normal user")
    def test_a_locked_csv_is_caught_before_launch(self):
        self.make_output()
        os.chmod(self.out / "output.csv", 0o444)
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume"):
            self.app._on_start()
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Datoteka je zaključana")
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_the_lock_check_is_the_extractors_own(self):
        self.make_output()
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume"), \
                mock.patch.object(grave_ui, "check_writable", side_effect=PermissionError("locked")) as check:
            self.app._on_start()
        check.assert_called_once_with(self.out / "output.csv")
        self.assertEqual(self.dialogs["showerror"].call_args[0][0], "Datoteka je zaključana")
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

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


class EstimateAppTests(AppCase):
    def saved(self) -> dict:
        return json.loads(self.settings_path.read_text(encoding="utf-8")) if self.settings_path.exists() else {}

    def feed(self, *lines):
        for line in lines:
            self.app._handle_line("stdout", line + "\n")

    def test_the_preview_counts_skipped_heic_and_labels_its_estimate(self):
        inp = self.tmp / "in"
        inp.mkdir()
        for name in ("a.jpg", "b.HEIC", "._c.jpg"):
            (inp / name).write_bytes(b"x")
        self.app.input_var.set(str(inp))
        self.app._refresh_preview()
        text = self.app.preview_var.get()
        self.assertIn("Pronađeno slika: 1.", text)
        self.assertIn("Preskočeno HEIC/HEIF datoteka: 1", text)
        self.assertIn("gruba procjena", text)

    def test_the_preview_uses_what_past_runs_learned_for_that_model_and_effort(self):
        inp = self.tmp / "in"
        inp.mkdir()
        for name in ("a.jpg", "b.jpg"):
            (inp / name).write_bytes(b"x")
        self.app.input_var.set(str(inp))
        self.app._settings["stats"] = {"claude-sonnet-5|high": {"cost": 1.0, "secs": 40.0, "n": 4}}
        self.app.model_var.set(ui_logic.MODEL_LABELS["claude-sonnet-5"])
        self.app.effort_var.set(ui_logic.EFFORT_LABELS["high"])
        self.app._refresh_preview()
        text = self.app.preview_var.get()
        self.assertIn("prema prošlim obradama", text)
        self.assertIn("~$0.50", text)
        self.assertIn("~20s", text)
        self.app.effort_var.set(ui_logic.EFFORT_LABELS["low"])      # nothing was learned at this effort
        self.app._on_effort_change()
        self.assertIn("gruba procjena", self.app.preview_var.get())

    def test_a_finished_run_teaches_the_estimate(self):
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-sonnet-5", "high"
        for line in ("[1/2] Processing a.jpg ...", "[1/2] OK: a.jpg (1 record) — $0.2500 (total: $0.25)",
                     "[2/2] Processing b.jpg ...", "[2/2] OK: b.jpg (1 record) — $0.2500 (total: $0.50)",
                     "Done. 2 images processed. 2 succeeded, 0 partial, 0 failed."):
            self.app._handle_line("stdout", line + "\n")
        self.app._on_proc_exit(0)
        entry = json.loads(self.settings_path.read_text(encoding="utf-8"))["stats"]["claude-sonnet-5|high"]
        self.assertEqual(entry["n"], 2)
        self.assertAlmostEqual(entry["cost"], 0.50)

    def test_changing_the_effort_refreshes_the_preview(self):
        with mock.patch.object(grave_ui.App, "_refresh_preview") as refresh:
            self.app._on_effort_change()
        refresh.assert_called_once()

    def test_a_folder_that_cannot_be_checked_reads_as_missing(self):
        # Path.is_dir() raises PermissionError on Python <= 3.12 for a path under an unreadable
        # parent, and the preview refreshes from __init__: that must not keep the window from opening.
        self.app.input_var.set(str(self.tmp / "locked" / "in"))
        with mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "denied")):
            self.app._refresh_preview()
        self.assertEqual(self.app.preview_var.get(), "Ulazna mapa ne postoji.")

    def test_a_run_that_ended_on_an_error_teaches_nothing(self):
        for rc in (1, -9):          # failed, and killed without a Stop click
            with self.subTest(rc=rc):
                self.app._reset_run_state()
                self.feed("[1/9] Processing a.jpg ...", "[1/9] FAILED: a.jpg (API call failed)")
                self.app._on_proc_exit(rc)
                self.assertNotIn("stats", self.saved())
                self.assertNotIn("stats", self.app._settings)

    def test_a_stopped_run_is_timed_to_its_last_result_not_to_the_exit(self):
        clock = [100.0]
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-opus-5-5", "max"
        with mock.patch.object(grave_ui.time, "monotonic", lambda: clock[0]):
            self.feed("[1/3] Processing a.jpg ...")
            clock[0] = 110.0
            self.feed("[1/3] OK: a.jpg (1 record) — $0.2500 (total: $0.25)")
            self.app._stop_requested = True
            clock[0] = 500.0
            self.app._on_proc_exit(130)
        entry = self.saved()["stats"]["claude-opus-5-5|max"]
        self.assertEqual((entry["n"], entry["secs"]), (1, 10.0))

    def test_saved_stats_that_are_not_finite_leave_the_preview_working(self):
        # json reads a hand-edited NaN back, and the preview refreshes from __init__
        inp = self.tmp / "in"
        inp.mkdir()
        (inp / "a.jpg").write_bytes(b"x")
        self.settings_path.parent.mkdir(parents=True)
        nan = float("nan")
        self.settings_path.write_text(
            json.dumps({"stats": {"claude-sonnet-5|high": {"cost": nan, "secs": nan, "n": 2}}}),
            encoding="utf-8")
        self.app._load_settings()
        self.app.input_var.set(str(inp))
        self.app._refresh_preview()
        self.assertIn("gruba procjena", self.app.preview_var.get())


class RunModelTests(RunCase):
    """The estimate files a run under _run_model/_run_effort, so every launch must set them."""

    def test_a_run_is_filed_under_the_model_and_effort_it_launched_with(self):
        self.app.model_var.set(ui_logic.MODEL_LABELS["claude-opus-5"])
        self.app.effort_var.set(ui_logic.EFFORT_LABELS["max"])
        self.app._on_start()
        self.assertEqual((self.app._run_model, self.app._run_effort), ("claude-opus-5", "max"))

    def test_a_byhand_retry_is_filed_under_the_retry_model(self):
        byhand = self.out / "byhand"
        byhand.mkdir(parents=True)
        (byhand / "p_1_x.jpg").write_bytes(jpeg_bytes())
        self.app._on_retry_byhand()
        self.assertEqual((self.app._run_model, self.app._run_effort),
                         (ui_logic.RETRY_MODEL, ui_logic.RETRY_EFFORT))

    def test_a_stronger_selection_is_what_a_byhand_retry_is_filed_under(self):
        byhand = self.out / "byhand"
        byhand.mkdir(parents=True)
        (byhand / "p_1_x.jpg").write_bytes(jpeg_bytes())
        self.app.model_var.set(ui_logic.MODEL_LABELS["claude-fable-5-1"])
        self.app.effort_var.set(ui_logic.EFFORT_LABELS["max"])
        self.app._on_retry_byhand()
        self.assertEqual((self.app._run_model, self.app._run_effort), ("claude-fable-5-1", "max"))


class RetryTests(RunCase):
    def setUp(self):
        super().setUp()
        byhand = self.out / "byhand"
        byhand.mkdir(parents=True)
        for name in ("p_1_x.jpg", "p_2_x.jpg"):
            (byhand / name).write_bytes(jpeg_bytes())
        self.retry_out = self.out / "byhand_retry"

    def test_retry_shows_model_and_estimate_then_runs_into_byhand_retry(self):
        self.app.model_var.set(ui_logic.MODEL_LABELS["claude-fable-5-1"])
        self.app._on_retry_byhand()
        body = self.dialogs["askyesno"].call_args[0][1]
        self.assertIn("Claude Fable 5.1", body)
        self.assertIn("Procjena: ~$", body)
        cmd = self.launched[-1]
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-fable-5-1")
        self.assertEqual(Path(cmd[cmd.index("--output") + 1]), self.retry_out)
        self.assertTrue((self.retry_out / ".tron-grave.lock").exists())

    def test_a_second_retry_asks_before_touching_earlier_results(self):
        self.retry_out.mkdir()
        init_csv(self.retry_out / "output.csv")
        append_rows(self.retry_out / "output.csv", [["1", "Ivan", "Horvat", 1920, 1999, "", "p_1_x.jpg"]])
        init_processed(self.retry_out)
        mark_processed(self.retry_out, Path("p_1_x.jpg"))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume") as ask:
            self.app._on_retry_byhand()
        ask.assert_called_once()
        self.assertIn("--resume", self.launched[-1])

    def test_a_stopped_retry_points_back_to_the_retry_button(self):
        self.app._on_retry_byhand()
        self.app._stop_requested = True
        self.app._on_proc_exit(130)
        self.assertIn("Ponovi byhand/", self.dialogs["showinfo"].call_args[0][1])


class OutputButtonTests(AppCase):
    def results_on_disk(self) -> Path:
        out = self.tmp / "out"
        (out / "byhand").mkdir(parents=True)
        init_csv(out / "output.csv")
        return out

    def open_states(self, app=None) -> tuple[str, str]:
        app = app or self.app
        return str(app.btn_open_csv.cget("state")), str(app.btn_open_byhand.cget("state"))

    def test_the_open_buttons_follow_what_is_on_disk(self):
        self.app.output_var.set(str(self.results_on_disk()))
        self.app._refresh_output_buttons()
        self.app._on_model_change()                 # this used to disable them
        self.assertEqual(self.open_states(), ("normal", "normal"))

    def test_the_open_buttons_survive_an_effort_change(self):
        self.app.output_var.set(str(self.results_on_disk()))
        self.app._refresh_output_buttons()
        self.app._on_effort_change()                # so did this, once the estimate learned from runs
        self.assertEqual(self.open_states(), ("normal", "normal"))

    def test_the_open_buttons_are_right_from_the_first_moment(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"output": str(self.results_on_disk())}), encoding="utf-8")
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        self.assertEqual(self.open_states(grave_ui.App(root)), ("normal", "normal"))

    def test_picking_an_output_folder_sets_the_open_buttons_from_it(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        pick = self._patch(grave_ui.filedialog, "askdirectory",
                           mock.Mock(return_value=str(self.results_on_disk())))
        self.app._pick_output()
        self.assertEqual(self.open_states(), ("normal", "normal"))
        pick.return_value = str(empty)
        self.app._pick_output()
        self.assertEqual(self.open_states(), ("disabled", "disabled"))

    def test_an_output_folder_that_cannot_be_checked_reads_as_empty(self):
        # Path.is_file()/is_dir() raise PermissionError on Python <= 3.12 for a path under an
        # unreadable parent, and the buttons refresh from __init__: that must not keep the
        # window from opening.
        self.app.output_var.set(str(self.tmp / "locked" / "out"))
        with mock.patch.object(Path, "is_file", side_effect=PermissionError(13, "denied")), \
                mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "denied")):
            self.app._refresh_output_buttons()
        self.assertEqual(self.open_states(), ("disabled", "disabled"))

    def test_a_finished_run_draws_attention(self):
        self.app._reset_run_state()
        self.app._on_proc_exit(0)
        self.app._draw_attention.assert_called_once()

    def test_every_other_way_a_real_run_ends_draws_attention_but_a_dry_run_does_not(self):
        for rc, stopped, dry in ((1, False, False), (-9, False, False), (130, True, False), (0, False, True)):
            with self.subTest(rc=rc, stopped=stopped, dry=dry):
                self.app._draw_attention.reset_mock()
                self.app._reset_run_state()
                self.app._launched_dry_run = dry
                self.app._stop_requested = stopped
                self.app._on_proc_exit(rc)
                self.assertEqual(self.app._draw_attention.call_count, 0 if dry else 1)


class SummaryTests(AppCase):
    def test_the_summary_lists_this_runs_reasons_and_opens_its_csv(self):
        csv_path = self.tmp / "output.csv"
        init_csv(csv_path)
        append_rows(csv_path, [["1", "A", "B", "", "", "bez god. smrti", "a.jpg"],
                               ["2", "", "B", "", "", "fali: ime; god. smrti nečitka", "b.jpg"],
                               ["3", "", "C", "", "", "fali: ime", "old.jpg"]])
        self.app._flagged = {"b.jpg"}
        win = REAL_SHOW_SUMMARY(self.app, csv_path, "Sažetak obrade")
        win.withdraw()                              # never mapped: a test must not flash a window
        self.addCleanup(win.destroy)
        widgets, stack = [], [win]
        while stack:
            widget = stack.pop()
            widgets.append(widget)
            stack.extend(widget.winfo_children())
        texts = [str(w.cget("text")) for w in widgets if isinstance(w, (ttk.Label, ttk.Button))]
        self.assertIn("  1×  fali: ime", texts)
        self.assertIn("  1×  god. smrti nečitka", texts)
        self.assertNotIn("  1×  bez god. smrti", texts)
        # _flagged is every non-OK photo, including API failures that never reach byhand/
        self.assertEqual([text for text in texts if "byhand" in text], [])
        with mock.patch.object(grave_ui.App, "_open_path") as open_path:
            next(w for w in widgets if isinstance(w, ttk.Button) and w.cget("text") == "Otvori CSV").invoke()
        open_path.assert_called_once_with(csv_path)
        self.assertTrue(win.bind("<Escape>"))
        self.assertTrue(win.bind("<Return>"))
