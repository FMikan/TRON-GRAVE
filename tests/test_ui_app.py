import ctypes
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import unittest
from pathlib import Path
from tkinter import ttk
from unittest import mock

import grave_ui
import ui_logic
from extractor.csv_writer import CSV_COLUMNS, append_rows, init_csv, init_processed, mark_processed
from tests.helpers import jpeg_bytes
from tests.ui_harness import (REAL_ASK_EXISTING_OUTPUT, REAL_DRAW_ATTENTION, REAL_LAUNCH_SUBPROCESS,
                              REAL_PRESENT, REAL_SHOW_SUMMARY, AppCase)


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two #rrggbb colours."""
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def all_widgets(root):
    """Every widget under root, root included."""
    found, stack = [], [root]
    while stack:
        widget = stack.pop()
        found.append(widget)
        stack.extend(widget.winfo_children())
    return found


class LabelTests(AppCase):
    def test_widgets_speak_croatian(self):
        self.assertEqual(self.app.btn_start.cget("text"), "▶  Pokreni")
        self.assertEqual(self.app.btn_retry_byhand.cget("text"), grave_ui.RETRY_LABEL)
        self.assertEqual(self.app.btn_dry.cget("text"), "Probni prolaz")
        self.assertEqual(self.app.btn_open.cget("text"), "Otvori")
        self.assertEqual(self.app.status_var.get(), "Odaberite ulaznu mapu sa slikama.")

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



class ApiKeyUxTests(AppCase):
    def test_prikazi_reveals_and_hides_the_key(self):
        self.app.show_key_var.set(True)
        self.app._toggle_key_visibility()
        self.assertEqual(str(self.app.ent_key.cget("show")), "")
        self.app.show_key_var.set(False)
        self.app._toggle_key_visibility()
        self.assertEqual(str(self.app.ent_key.cget("show")), "•")

    def test_the_button_names_the_key_and_return_saves(self):
        self.assertEqual(self.app.btn_save_key.cget("text"), "Spremi ključ")
        self.assertTrue(self.app.ent_key.bind("<Return>"))

    def test_an_empty_field_says_where_the_key_comes_from(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env"}):
            self.app.api_key_var.set("")
            self.assertEqual(self.app.key_hint_var.get(),
                             "Koristi se ključ iz varijable okruženja ANTHROPIC_API_KEY.")
        (self.tmp / ".env").write_text("ANTHROPIC_API_KEY=sk-file\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.app._refresh_key_hint()
            self.assertEqual(self.app.key_hint_var.get(), "Koristi se ključ iz datoteke .env.")
            self.app.api_key_var.set("sk-typed")
            self.assertEqual(self.app.key_hint_var.get(), "")
            self.assertEqual(self.app._key_hint.winfo_manager(), "")

    def test_an_unreadable_env_file_gives_no_hint_and_no_error(self):
        with mock.patch.object(grave_ui, "dotenv_values", side_effect=PermissionError("denied")), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.app.api_key_var.set("")
            self.assertIsNone(self.app._key_source())
            self.assertEqual(self.app.key_hint_var.get(), "")
        self.dialogs["showerror"].assert_not_called()

    def test_saving_an_empty_field_says_the_key_was_removed(self):
        self.app.api_key_var.set("")
        self.app._on_save_key()
        self.assertEqual(self.app.status_var.get(), "API ključ uklonjen iz postavki.")

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


class ThemeTests(AppCase):
    def test_the_main_button_text_passes_wcag_aa_in_every_state(self):
        t = grave_ui.THEME
        for state in ("ACCENT", "ACCENT_HOVER", "ACCENT_DOWN"):
            with self.subTest(state=state):
                self.assertGreaterEqual(contrast(t["ON_ACCENT"], t[state]), 4.5)

    def test_keyboard_focus_shows_on_buttons_and_checkboxes(self):
        style = ttk.Style(self.root)
        t = grave_ui.THEME
        self.assertEqual(style.lookup("TButton", "focuscolor"), t["FOCUS"])
        self.assertEqual(style.lookup("TCheckbutton", "focuscolor"), t["FOCUS"])
        self.assertEqual(style.lookup("Accent.TButton", "focuscolor"), t["ON_ACCENT"])
        # WCAG 1.4.11: a focus ring needs 3:1 against what it is drawn on
        self.assertGreaterEqual(contrast(t["FOCUS"], t["INPUT"]), 3.0)
        self.assertGreaterEqual(contrast(t["FOCUS"], t["BG"]), 3.0)
        self.assertGreaterEqual(contrast(t["ON_ACCENT"], t["ACCENT"]), 3.0)

    def test_the_checkbox_box_and_the_log_border_follow_the_dark_theme(self):
        style = ttk.Style(self.root)
        t = grave_ui.THEME
        self.assertEqual(style.lookup("TCheckbutton", "indicatorbackground"), t["INPUT"])
        self.assertEqual(style.lookup("TCheckbutton", "indicatorbackground", ["selected"]), t["ACCENT"])
        self.assertEqual(str(self.app.log.cget("highlightbackground")), t["BORDER"])
        self.assertEqual(str(self.app.log.cget("highlightcolor")), t["ACCENT"])

    def test_the_version_label_is_readable(self):
        label = next(w for w in all_widgets(self.root)
                     if isinstance(w, ttk.Label) and str(w.cget("text")) == f"v{grave_ui.__version__}")
        self.assertEqual(str(label.cget("foreground")), grave_ui.THEME["MUTED"])
        self.assertGreaterEqual(contrast(grave_ui.THEME["MUTED"], grave_ui.THEME["BG"]), 4.5)

    def test_every_colour_comes_from_the_palette(self):
        source = Path(grave_ui.__file__).read_text(encoding="utf-8")
        after_palette = source.split("THEME = {", 1)[1].split("\n}\n", 1)[1]
        self.assertEqual(re.findall(r"""["']#[0-9a-fA-F]{6}["']""", after_palette), [])



class DialogPlacementTests(AppCase):
    def test_present_asks_tk_to_centre_the_dialog_on_the_main_window(self):
        placed = []
        self.root.tk.createcommand("record_placement", lambda *args: placed.append(args))
        self.root.tk.eval("rename ::tk::PlaceWindow ::tk::PlaceWindow_real\n"
                          "proc ::tk::PlaceWindow {args} {record_placement {*}$args}")
        self.addCleanup(self.root.tk.eval, "rename ::tk::PlaceWindow {}\n"
                                           "rename ::tk::PlaceWindow_real ::tk::PlaceWindow")
        win = self.app._dialog("Proba")
        self.addCleanup(win.destroy)
        REAL_PRESENT(self.app, win)
        self.assertEqual(placed, [(str(win), "widget", str(self.root))])

    def test_a_dialog_starts_hidden_themed_and_tied_to_the_main_window(self):
        win = self.app._dialog("Proba")
        self.addCleanup(win.destroy)
        self.assertEqual(win.state(), "withdrawn")
        self.assertEqual(win.title(), "Proba")
        self.assertEqual(str(win.cget("background")), grave_ui.THEME["BG"])
        self.assertEqual(str(win.transient()), str(self.root))

    def test_the_summary_and_the_resume_dialog_are_placed_before_they_show(self):
        csv_path = self.tmp / "output.csv"
        init_csv(csv_path)
        summary = REAL_SHOW_SUMMARY(self.app, csv_path, "Sažetak obrade")
        self.addCleanup(summary.destroy)
        self.app._present.assert_called_once_with(summary)
        self.app._present.reset_mock()
        shown = []

        def close(win):
            shown.append(win)
            win.destroy()

        with mock.patch.object(tk.Toplevel, "wait_visibility"), \
                mock.patch.object(tk.Toplevel, "grab_set"), \
                mock.patch.object(self.root, "wait_window", side_effect=close):
            REAL_ASK_EXISTING_OUTPUT(self.app, csv_path, 1, 1, 2, None)
        self.app._present.assert_called_once_with(shown[0])


class ChoiceDialogTests(AppCase):
    def build(self, default="cancel"):
        win, choice = self.app._build_choice_dialog(
            "Mapa je zauzeta", "X postoji.", [("use", "Svejedno koristi mapu"), ("cancel", "Odustani")], default)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        return win, choice

    def test_the_buttons_say_what_they_do_and_the_first_one_is_the_action(self):
        win, choice = self.build()
        self.assertEqual([str(b.cget("text")) for b in win.buttons.values()], ["Svejedno koristi mapu", "Odustani"])
        self.assertEqual(str(win.buttons["use"].cget("style")), "Accent.TButton")
        win.buttons["use"].invoke()
        self.assertEqual(choice["value"], "use")
        self.assertFalse(win.winfo_exists())

    def test_return_and_escape_are_bound_and_the_default_is_the_safe_choice(self):
        win, _choice = self.build(default="cancel")
        self.assertIs(win.default_button, win.buttons["cancel"])
        self.assertTrue(win.bind("<Return>"))
        self.assertTrue(win.bind("<Escape>"))

    def test_the_close_box_answers_none(self):
        win, choice = self.build()
        choice["value"] = "use"
        win.tk.call(win.protocol("WM_DELETE_WINDOW"))
        self.assertIsNone(choice["value"])

    def test_return_presses_the_focused_button_or_the_default(self):
        win, choice = self.build(default="cancel")
        buttons = list(win.buttons.values())
        with mock.patch.object(win, "focus_get", return_value=win.buttons["use"]):
            grave_ui.App._press_focused(win, buttons, win.default_button)
        self.assertEqual(choice["value"], "use")

    def test_no_yes_no_box_is_left(self):
        self.assertNotIn("askyesno", Path(grave_ui.__file__).read_text(encoding="utf-8"))


class ScalingTests(AppCase):
    def app_at(self, scale):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with mock.patch.object(grave_ui.App, "_dpi_scale", return_value=scale):
            return grave_ui.App(root)

    def test_paddings_arrows_and_boxes_grow_with_the_display_scale(self):
        app = self.app_at(1.75)
        style = ttk.Style(app.root)
        padding = [str(v) for v in app.root.tk.splitlist(style.lookup("TButton", "padding"))]
        self.assertEqual(padding, ["24", "14"])
        self.assertEqual(str(style.lookup("TProgressbar", "thickness")), "14")
        self.assertEqual(str(style.lookup("TScrollbar", "arrowsize")), "24")
        self.assertEqual(str(style.lookup("TCheckbutton", "indicatorsize")), "18")

    def test_at_100_percent_the_sizes_are_tks_own(self):
        style = ttk.Style(self.root)
        self.assertEqual(str(style.lookup("TScrollbar", "arrowsize")), "14")
        self.assertEqual(str(style.lookup("TCheckbutton", "indicatorsize")), "10")

    def test_the_window_keeps_four_log_lines_at_its_smallest(self):
        self.root.update_idletasks()
        _width, min_height = self.root.minsize()
        above = self.root.winfo_reqheight() - self.app.log.master.winfo_reqheight()
        line = tkfont.Font(root=self.root, font=self.app.log.cget("font")).metrics("linespace")
        self.assertGreaterEqual(min_height, above + 4 * line)

    def test_the_preview_and_status_lines_wrap_instead_of_running_off(self):
        self.app._preview_label.master.event_generate("<Configure>", width=600, height=80)
        self.assertLessEqual(int(str(self.app._preview_label.cget("wraplength"))), 600)
        self.app._status_label.master.event_generate("<Configure>", width=500, height=40)
        self.assertLessEqual(int(str(self.app._status_label.cget("wraplength"))), 500)


class LogViewTests(AppCase):
    def feed(self, *lines, kind="stdout"):
        for line in lines:
            self.app._handle_line(kind, line + "\n")

    def visible(self) -> str:
        return self.app.log.tk.call(self.app.log._w, "get", "-displaychars", "1.0", "end")

    def test_the_default_view_reads_in_croatian(self):
        self.app._reset_run_state()
        self.feed("[1/2] Processing a.jpg ...", "[1/2] OK: a.jpg (1 record) — $0.0200 (total: $0.02)",
                  "[2/2] Processing b.jpg ...",
                  "[2/2] PARTIAL: b.jpg (Name or surname could not be read) — $0.0100 (total: $0.03)")
        text = self.visible()
        self.assertIn("[1/2] OK         a.jpg · 1 osoba · $0.0200", text)
        self.assertIn("[2/2] ZA PREGLED b.jpg · nečitko ime ili prezime · $0.0100", text)
        self.assertNotIn("Processing", text)
        self.assertNotIn("Name or surname", text)

    def test_tehnicki_zapis_shows_the_extractors_own_lines_instead(self):
        self.app._reset_run_state()
        self.feed("[1/1] Processing a.jpg ...", "[1/1] OK: a.jpg (1 record) — $0.0200 (total: $0.02)")
        self.app.raw_log_var.set(True)
        self.app._toggle_raw_log()
        text = self.visible()
        self.assertIn("[1/1] Processing a.jpg ...", text)
        self.assertNotIn("osoba", text)

    def test_each_verdict_and_warning_has_its_colour(self):
        self.app._reset_run_state()
        self.feed("[1/3] Processing a.jpg ...", "[1/3] OK: a.jpg (1 record) — $0.0200 (total: $0.02)",
                  "[2/3] Processing b.jpg ...", "[2/3] PARTIAL: b.jpg (All fields illegible) — $0.0100 (total: $0.03)",
                  "[3/3] Processing c.jpg ...", "[3/3] FAILED: c.jpg (Model returned no records) — $0.0100 (total: $0.04)")
        self.feed("warning: skipping 2 .heic/.heif file(s); convert them to JPG first", kind="stderr")
        for tag, colour in (("ok", "OK"), ("review", "WARN"), ("failed", "ERR"), ("warn", "WARN")):
            with self.subTest(tag=tag):
                self.assertTrue(self.app.log.tag_ranges(tag))
                self.assertEqual(str(self.app.log.tag_cget(tag, "foreground")), grave_ui.THEME[colour])

    def test_the_exit_code_is_for_the_technical_view_only(self):
        self.app._reset_run_state()
        self.app._on_proc_exit(1)
        self.assertNotIn("izlazni kod", self.visible())
        self.assertIn("Obrada nije uspjela.", self.visible())
        self.assertIn("[neuspjelo, izlazni kod 1]", self.app.log.get("1.0", "end"))

    def test_the_status_line_is_full_text_colour_and_strong(self):
        self.assertEqual(str(self.app._status_label.cget("foreground")), grave_ui.THEME["TEXT"])
        self.assertEqual(self.root.tk.splitlist(str(self.app._status_label.cget("font"))),
                         tuple(str(part) for part in self.app._fonts["strong"]))

class FontTests(AppCase):
    def test_windows_fonts_are_used_where_installed(self):
        with mock.patch.object(grave_ui.tkfont, "families",
                               return_value=("Segoe UI", "Segoe UI Semibold", "Consolas")):
            fonts = self.app._pick_fonts()
        self.assertEqual((fonts["body"], fonts["strong"], fonts["mono"], fonts["app"]),
                         (("Segoe UI", 10), ("Segoe UI Semibold", 10), ("Consolas", 10),
                          ("Segoe UI Semibold", 17)))

    def test_elsewhere_the_headings_keep_their_weight(self):
        with mock.patch.object(grave_ui.tkfont, "families", return_value=("DejaVu Sans",)):
            fonts = self.app._pick_fonts()
        default = tkfont.nametofont("TkDefaultFont", root=self.root).actual("family")
        fixed = tkfont.nametofont("TkFixedFont", root=self.root).actual("family")
        self.assertEqual(fonts["body"], (default, 10))
        self.assertEqual(fonts["app"], (default, 17, "bold"))
        self.assertEqual(fonts["mono"], (fixed, 10))

    def test_no_font_is_named_outside_the_picker(self):
        source = Path(grave_ui.__file__).read_text(encoding="utf-8")
        picker = source.split("def _pick_fonts", 1)[1].split("\n    def ", 1)[0]
        for name in ('"Segoe UI"', '"Segoe UI Semibold"', '"Consolas"'):
            self.assertNotIn(name, source.replace(picker, ""))


class LockAndDryRunTests(RunCase):
    def test_a_dry_run_needs_no_output_folder_and_takes_no_lock(self):
        self.app.output_var.set("")
        self.app._on_dry_run()
        self.assertIn("--dry-run", self.launched[-1])
        self.assertNotIn("--output", self.launched[-1])
        self.assertEqual(list(self.tmp.rglob(".tron-grave.lock")), [])

    def test_a_dry_run_leaves_a_chosen_output_folder_alone(self):
        self.app._on_dry_run()
        self.assertIn("--dry-run", self.launched[-1])
        self.assertNotIn("--output", self.launched[-1])
        self.assertFalse(self.out.exists())
        self.assertEqual(list(self.tmp.rglob(".tron-grave.lock")), [])

    def test_a_run_takes_a_token_lock_and_releases_it_on_exit(self):
        self.app._on_start()
        self.assertTrue(self.lock().read_text(encoding="utf-8").startswith(f"{socket.gethostname()}:{os.getpid()}:"))
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


class LaunchFailureTests(RunCase):
    def test_a_launch_that_fails_leaves_the_buttons_as_the_disk_has_them(self):
        (self.out / "byhand").mkdir(parents=True)
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(jpeg_bytes())
        init_csv(self.out / "output.csv")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume"), \
                mock.patch.object(grave_ui.App, "_launch_subprocess", REAL_LAUNCH_SUBPROCESS), \
                mock.patch.object(grave_ui.subprocess, "Popen", side_effect=OSError("no interpreter")):
            self.app._on_start()                    # _set_running(True) greys the three buttons out
        self.assertEqual(self.app.status_var.get(), "Pokretanje nije uspjelo.")
        self.assertEqual([str(button.cget("state")) for button in (
            self.app.btn_open, self.app.btn_retry_byhand)], ["normal"] * 2)


class MissingKeyTests(RunCase):
    def test_both_missing_key_dialogs_name_the_save_button_as_it_reads(self):
        self.app.api_key_var.set("")
        (self.out / "byhand").mkdir(parents=True)
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(jpeg_bytes())
        with mock.patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.app._on_start()
            self.app._on_retry_byhand()
        calls = self.dialogs["showerror"].call_args_list
        self.assertEqual([c[0][0] for c in calls], ["Nedostaje API ključ"] * 2)
        for call in calls:
            self.assertIn(f"kliknite {self.app.btn_save_key.cget('text')}", call[0][1])
        self.assertEqual(self.launched, [])


class BackupGuardTests(RunCase):
    def test_prepisi_refuses_to_move_an_input_folder_inside_byhand_retry(self):
        nested = self.out / "byhand_retry" / "byhand"          # the stronger model's leftovers
        nested.mkdir(parents=True)
        (nested / "p_1_x.jpg").write_bytes(jpeg_bytes())
        init_csv(self.out / "output.csv")
        append_rows(self.out / "output.csv", [["1", "Ana", "Horvat", "1900", "1980", "", "p_1_x.jpg"]])
        self.app.input_var.set(str(nested))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"):
            self.app._on_start()
        self.assertEqual(self.launched, [])
        (title, body), _ = self.dialogs["showerror"].call_args
        self.assertEqual(title, "Neispravna ulazna mapa")
        self.assertIn("byhand_retry/", body)
        self.assertTrue((nested / "p_1_x.jpg").exists())
        self.assertEqual([p.name for p in self.out.iterdir() if ".bak" in p.name], [])
        self.assertFalse(self.lock().exists())


class OutcomeStatusTests(RunCase):
    def test_a_runs_outcome_stays_in_the_status_line_while_the_setup_stays_ready(self):
        self._patch(grave_ui.filedialog, "askdirectory", mock.Mock(return_value=str(self.inp)))
        for rc in (0, 1):
            with self.subTest(rc=rc):
                self.app._on_start()
                self.app._on_proc_exit(rc)
                outcome = self.app.status_var.get()
                self.app.api_key_var.set(f"sk-test-{rc}")      # typing in the key field
                self.app._pick_input()
                self.assertEqual(self.app.status_var.get(), outcome)

    def test_an_incomplete_setup_still_replaces_the_outcome_with_the_next_step(self):
        self.app._on_start()
        self.app._on_proc_exit(0)
        with mock.patch.dict(os.environ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.app.api_key_var.set("")
            self.assertEqual(self.app.status_var.get(), "Upišite API ključ i kliknite Spremi ključ.")
            self.app.api_key_var.set("sk-test")
            self.assertEqual(self.app.status_var.get(), "Spremno.")

    def test_a_launch_that_failed_keeps_saying_so(self):
        with mock.patch.object(grave_ui.App, "_launch_subprocess", REAL_LAUNCH_SUBPROCESS), \
                mock.patch.object(grave_ui.subprocess, "Popen", side_effect=OSError("no interpreter")):
            self.app._on_start()
        self.app.api_key_var.set("sk-test-2")
        self.assertEqual(self.app.status_var.get(), "Pokretanje nije uspjelo.")

    def test_saving_the_key_after_a_run_hands_the_status_back_to_the_setup_steps(self):
        self.app._on_start()
        self.app._on_proc_exit(0)
        self.app._on_save_key()
        self.app.api_key_var.set("sk-other")                  # a different key, not saved yet
        self.assertEqual(self.app.status_var.get(), "Spremno.")

    def test_the_excel_hint_after_a_run_gives_way_to_the_setup_steps_too(self):
        self.app._on_start()
        self.app._on_proc_exit(0)
        with mock.patch.object(grave_ui.App, "_open_path", return_value=True):
            self.app._open_csv(self.out / "output.csv")
        self.app.api_key_var.set("sk-other")
        self.assertEqual(self.app.status_var.get(), "Spremno.")



class CostCheckTests(RunCase):
    def priced_at(self, per_photo):
        self.app._settings["stats"] = {"claude-sonnet-5|high": {"cost": per_photo, "secs": 10.0, "n": 1}}

    def test_an_expensive_run_asks_first_with_count_model_and_estimate(self):
        self.priced_at(2.5)
        self.app._on_start()
        (title, body, choices), kwargs = self.choices.call_args
        self.assertEqual(title, "Potvrda troška")
        self.assertIn("Obraditi 2 slike?", body)
        self.assertIn("Model: Claude Sonnet 5 (zadani), napor: visok", body)
        self.assertIn("~$5.00", body)
        self.assertIn("prema prošlim obradama", body)
        self.assertEqual((choices, kwargs), ([("run", "Pokreni obradu"), ("cancel", "Odustani")], {"default": "run"}))
        self.assertTrue(self.launched)

    def test_declining_launches_nothing_moves_nothing_and_frees_the_lock(self):
        self.priced_at(2.5)
        self.out.mkdir(parents=True)
        init_csv(self.out / "output.csv")
        append_rows(self.out / "output.csv", [["1", "Ivan", "Horvat", 1920, 1999, "", "p_1_x.jpg"]])
        self.answer("cancel")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"):
            self.app._on_start()
        self.assertEqual(self.launched, [])
        self.assertTrue((self.out / "output.csv").exists())          # Prepiši had not run yet
        self.assertEqual(list(self.out.glob("*.bak*")), [])
        self.assertFalse(self.lock().exists())

    def test_a_cheap_run_starts_without_asking(self):
        self.app._on_start()
        self.choices.assert_not_called()
        self.assertTrue(self.launched)

    def test_nastavi_counts_only_the_photos_still_to_process(self):
        self.priced_at(2.0)
        self.out.mkdir(parents=True)
        init_csv(self.out / "output.csv")
        append_rows(self.out / "output.csv", [["1", "Ivan", "Horvat", 1920, 1999, "", "p_1_x.jpg"]])
        init_processed(self.out)
        mark_processed(self.out, Path("p_1_x.jpg"))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="resume"):
            self.app._on_start()
        self.assertIn("Obraditi 1 sliku?", self.choices.call_args[0][1])

    def test_a_retry_is_asked_once(self):
        self.app._settings["stats"] = {"claude-opus-5-5|high": {"cost": 9.0, "secs": 1.0, "n": 1}}
        (self.out / "byhand").mkdir(parents=True)
        for name in ("p_1_x.jpg", "p_2_x.jpg"):
            (self.out / "byhand" / name).write_bytes(jpeg_bytes())
        self.app._on_retry_byhand()
        self.assertEqual([c[0][0] for c in self.choices.call_args_list], ["Ponovna obrada"])


class RunLogFileTests(RunCase):
    def run_two_lines(self):
        self.app._on_start()
        self.app._handle_line("stdout", "[1/2] Processing p_1_x.jpg ...\n")
        self.app._handle_line("stdout", "[1/2] OK: p_1_x.jpg (1 record) — $0.0100 (total: $0.01)\n")

    def test_a_run_keeps_its_raw_log_in_the_output_folder(self):
        self.run_two_lines()
        self.app._on_proc_exit(0)
        logs = list((self.out / "logs").glob("tron-grave-*.log"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding="utf-8")
        self.assertIn("[1/2] Processing p_1_x.jpg ...", text)
        self.assertIn("[izlazni kod 0]", text)
        self.assertNotIn("osoba", text)                 # the Croatian view is not duplicated
        self.assertIsNone(self.app._log_file)           # closed with the run

    def test_a_dry_run_writes_no_log_file(self):
        self.app._on_dry_run()
        self.assertFalse((self.inp / "logs").exists())
        self.assertIsNone(self.app._log_file)

    def test_a_log_folder_that_cannot_be_made_does_not_stop_the_run(self):
        self.out.mkdir(parents=True)
        (self.out / "logs").write_text("a file, not a folder", encoding="utf-8")
        self.app._on_start()
        self.assertTrue(self.launched)
        self.assertIn("Zapisnik se ne može spremiti", self.app.log.get("1.0", "end"))

    def test_a_disk_that_fills_up_mid_run_closes_the_log_and_the_run_goes_on(self):
        self.run_two_lines()
        broken = mock.Mock()
        broken.write.side_effect = OSError(28, "No space left on device")
        self.app._log_file = broken
        self.app._handle_line("stdout", "[2/2] Processing p_2_x.jpg ...\n")
        self.assertIsNone(self.app._log_file)
        self.assertEqual(self.app.log.get("1.0", "end").count("Zapisnik se ne može spremiti"), 1)


class StaleLockTests(RunCase):
    def test_a_lock_left_by_a_crashed_run_on_this_machine_is_taken_over(self):
        self.out.mkdir(parents=True)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.lock().write_text(f"{socket.gethostname()}:{dead}:{'0' * 32}", encoding="utf-8")
        self.app._on_start()
        self.choices.assert_not_called()
        self.assertTrue(self.launched)
        self.assertIn("preuzimam je", self.app.log.get("1.0", "end"))
        self.assertTrue(self.lock().read_text(encoding="utf-8").startswith(
            f"{socket.gethostname()}:{os.getpid()}:"))

    def test_a_live_foreign_or_old_lock_still_asks(self):
        self.out.mkdir(parents=True)
        for token in (ui_logic.new_lock_token(), "other-host:1:abc", "123:abc"):
            with self.subTest(token=token):
                self.lock().write_text(token, encoding="utf-8")
                self.answer("cancel")
                self.app._on_start()
                self.assertEqual(self.choices.call_args[0][0], "Mapa je zauzeta")
                self.assertEqual(self.launched, [])

    def test_the_lock_names_the_extractor_once_it_runs(self):
        # The extractor can outlive a window that crashed, still finishing a photo: a new run
        # must find a live owner in the lock and ask, not take the folder over.
        pid = int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                                 capture_output=True, text=True, check=True).stdout)
        proc = mock.Mock(pid=pid, stdout=io.StringIO(""), stderr=io.StringIO(""))
        proc.wait.return_value = proc.poll.return_value = 0
        with mock.patch.object(grave_ui.App, "_launch_subprocess", REAL_LAUNCH_SUBPROCESS), \
                mock.patch.object(grave_ui.subprocess, "Popen", return_value=proc):
            self.app._on_start()
        self.assertTrue(self.lock().read_text(encoding="utf-8").startswith(f"{socket.gethostname()}:{pid}:"))
        self.app._on_proc_exit(0)
        self.assertFalse(self.lock().exists())      # the run still lets go of its own lock

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
                                    (True, "byhand_retry/output.csv", grave_ui.RETRY_LABEL)):
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

    def test_prepisi_sets_aside_the_csv_byhand_and_the_old_retry_results(self):
        self.make_output()
        (self.out / "byhand").mkdir()
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(b"x")
        (self.out / "byhand_retry").mkdir()
        init_csv(self.out / "byhand_retry" / "output.csv")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"):
            self.app._on_start()
        self.assertEqual(sorted(p.name.split(".")[0] for p in self.out.iterdir() if ".bak" in p.name),
                         ["byhand", "byhand_retry", "output"])
        self.assertFalse((self.out / "byhand_retry").exists())

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

    def test_an_ansi_resaved_csv_blocks_nastavi_with_a_reason(self):
        self.out.mkdir(parents=True)
        (self.out / "output.csv").write_bytes(
            (",".join(CSV_COLUMNS) + "\r\n1,Mišo,Čupić,1920,1999,,p_1_x.jpg\r\n").encode("cp1250"))
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value=None) as ask:
            self.app._on_start()
        self.assertIn("UTF-8", ask.call_args[0][4])
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_a_header_only_csv_with_processed_photos_blocks_nastavi(self):
        self.make_output()
        init_csv(self.out / "output.csv")          # the rows were deleted by hand
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value=None) as ask:
            self.app._on_start()
        self.assertIn("prazan", ask.call_args[0][4])

    def test_when_every_photo_is_done_nastavi_explains_and_otvori_csv_ends_it(self):
        self.make_output(processed=("p_1_x.jpg", "p_2_x.jpg"))
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="open") as ask, \
                mock.patch.object(grave_ui.App, "_open_csv") as open_csv:
            self.app._on_start()
        self.assertEqual(ask.call_args[0][2:], (2, 2, ui_logic.ALL_DONE))
        open_csv.assert_called_once_with(self.out / "output.csv")
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_the_all_done_dialog_greys_nastavi_and_offers_the_csv(self):
        win, choice = self.app._build_existing_output_dialog(Path("x/output.csv"), 2, 2, 2, ui_logic.ALL_DONE)
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        self.assertEqual(str(win.btn_resume.cget("state")), "disabled")
        self.assertIs(win.default_button, win.btn_cancel)
        win.btn_open.invoke()
        self.assertEqual(choice["value"], "open")

    def test_the_dialog_greys_out_nastavi_when_blocked(self):
        win, choice = self.app._build_existing_output_dialog(Path("x/output.csv"), 3, 1, 2, "razlog")
        self.assertEqual(str(win.btn_resume.cget("state")), "disabled")
        win.btn_fresh.invoke()
        self.assertEqual(choice["value"], "fresh")

    def open_dialog(self, csv_path=Path("x/output.csv")):
        """The Nastavi / Prepiši / Odustani dialog with nothing blocking it, never mapped."""
        win, choice = self.app._build_existing_output_dialog(csv_path, 3, 1, 2, None)
        win.withdraw()
        self.addCleanup(lambda: win.winfo_exists() and win.destroy())
        return win, choice

    def test_the_dialog_offers_nastavi_when_nothing_blocks_it(self):
        win, choice = self.open_dialog()
        self.assertEqual(str(win.btn_resume.cget("state")), "normal")
        win.btn_resume.invoke()
        self.assertEqual(choice["value"], "resume")
        self.assertFalse(win.winfo_exists())

    def test_a_long_output_path_wraps_instead_of_widening_the_dialog(self):
        win, _choice = self.open_dialog(Path("x" * 289, "output.csv"))      # 300 characters
        win.update_idletasks()
        self.assertLess(win.winfo_reqwidth(), 700)
        # the second label holds no path and fits at normal font sizes, so check its wrap directly
        labels = [w for w in win.winfo_children()[0].winfo_children() if isinstance(w, ttk.Label)]
        self.assertEqual([str(label.cget("wraplength")) for label in labels], ["520", "520"])

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
        self.answer("cancel")
        self.app._on_start()
        (title, body, choices), kwargs = self.choices.call_args
        self.assertEqual(title, "Nedostaje output.csv")
        self.assertIn("ponovno platiti", body)
        self.assertEqual(choices, [("fresh", "Kreni ispočetka"), ("cancel", "Odustani")])
        self.assertEqual(kwargs, {"default": "cancel"})          # Enter must not pay twice
        self.assertEqual(self.launched, [])
        self.assertFalse(self.lock().exists())

    def test_starting_over_after_a_missing_csv_is_a_fresh_run_that_sets_byhand_aside(self):
        self.out.mkdir(parents=True)
        (self.out / ".processed").write_text("p_1_x.jpg\n", encoding="utf-8")
        (self.out / "byhand").mkdir()
        (self.out / "byhand" / "p_1_x.jpg").write_bytes(b"x")
        self.app._on_start()                                   # the harness answers Kreni ispočetka
        self.assertEqual(self.choices.call_args[0][0], "Nedostaje output.csv")
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


    def test_a_rejected_key_names_the_fix_and_hides_the_raw_reply(self):
        self.app._reset_run_state()
        self.app._handle_line("stderr", "error: [api-401] API call failed: Error code: 401 - "
                                        "{'type': 'error', 'error': {'type': 'authentication_error'}}\n")
        self.app._on_proc_exit(1)
        body = self.dialogs["showerror"].call_args[0][1]
        self.assertIn("Upišite ispravan ključ", body)
        self.assertIn(ui_logic.RESUMED_FREE, body)
        self.assertNotIn("{'type'", body)

    def test_a_retry_points_back_to_its_own_button(self):
        self.app._reset_run_state()
        self.app._is_retry_run = True
        self.app._handle_line("stderr", "error: [api-down] 3 API calls in a row failed\n")
        self.app._on_proc_exit(1)
        self.assertIn(f"{grave_ui.RETRY_LABEL} → Nastavi", self.dialogs["showerror"].call_args[0][1])

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

    def test_unbilled_photos_teach_nothing(self):
        clock = [100.0]
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-sonnet-5", "high"
        with mock.patch.object(grave_ui.time, "monotonic", lambda: clock[0]):
            for t, line in ((100.0, "[1/3] Processing a.jpg ..."),
                            (110.0, "[1/3] OK: a.jpg (1 record) — $0.2500 (total: $0.25)"),
                            (110.0, "[2/3] Processing b.jpg ..."),
                            (140.0, "[2/3] FAILED: b.jpg (API call failed after retries: timeout)"),
                            (140.0, "[3/3] Processing c.jpg ..."),
                            (150.0, "[3/3] OK: c.jpg (1 record) — $0.2500 (total: $0.50)"),
                            (150.0, "Done. 3 images processed. 2 succeeded, 0 partial, 1 failed.")):
                clock[0] = t
                self.feed(line)
            self.app._on_proc_exit(2)
        entry = self.saved()["stats"]["claude-sonnet-5|high"]
        self.assertEqual((entry["n"], entry["secs"], entry["cost"]), (2, 20.0, 0.5))

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
        body = self.choices.call_args[0][1]
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

    def test_a_second_retry_can_start_over_and_sets_its_own_byhand_aside(self):
        # It reads out/byhand/ and starts over in out/byhand_retry/: nothing the backup moves
        # holds its input, so the input-folder guard must let it through.
        (self.retry_out / "byhand").mkdir(parents=True)
        (self.retry_out / "byhand" / "p_1_x.jpg").write_bytes(jpeg_bytes())
        init_csv(self.retry_out / "output.csv")
        append_rows(self.retry_out / "output.csv", [["1", "Ivan", "Horvat", 1920, 1999, "", "p_1_x.jpg"]])
        with mock.patch.object(grave_ui.App, "_ask_existing_output", return_value="fresh"):
            self.app._on_retry_byhand()
        self.assertTrue(self.launched)
        self.assertNotIn("--resume", self.launched[-1])
        self.assertEqual(sorted(p.name.split(".")[0] for p in self.retry_out.glob("*.bak*")), ["byhand", "output"])
        self.assertTrue((self.out / "byhand" / "p_1_x.jpg").exists())

    def test_a_stopped_retry_points_back_to_the_retry_button(self):
        self.app._on_retry_byhand()
        self.app._stop_requested = True
        self.app._on_proc_exit(130)
        self.assertIn(grave_ui.RETRY_LABEL, self.dialogs["showinfo"].call_args[0][1])


class OutputButtonTests(AppCase):
    def results_on_disk(self) -> Path:
        out = self.tmp / "out"
        (out / "byhand").mkdir(parents=True)
        init_csv(out / "output.csv")
        return out

    def open_states(self, app=None) -> tuple[str, str, str]:
        app = app or self.app
        app._refresh_open_menu()
        return (str(app.btn_open.cget("state")),
                str(app.open_menu.entrycget(0, "state")), str(app.open_menu.entrycget(1, "state")))

    def test_the_open_buttons_follow_what_is_on_disk(self):
        self.app.output_var.set(str(self.results_on_disk()))
        self.app._refresh_output_buttons()
        self.app._on_model_change()                 # this used to disable them
        self.assertEqual(self.open_states(), ("normal", "normal", "normal"))

    def test_the_open_buttons_survive_an_effort_change(self):
        self.app.output_var.set(str(self.results_on_disk()))
        self.app._refresh_output_buttons()
        self.app._on_effort_change()                # so did this, once the estimate learned from runs
        self.assertEqual(self.open_states(), ("normal", "normal", "normal"))

    def test_the_open_buttons_are_right_from_the_first_moment(self):
        self.settings_path.parent.mkdir(parents=True)
        self.settings_path.write_text(json.dumps({"output": str(self.results_on_disk())}), encoding="utf-8")
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        self.assertEqual(self.open_states(grave_ui.App(root)), ("normal", "normal", "normal"))

    def test_picking_an_output_folder_sets_the_open_buttons_from_it(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        pick = self._patch(grave_ui.filedialog, "askdirectory",
                           mock.Mock(return_value=str(self.results_on_disk())))
        self.app._pick_output()
        self.assertEqual(self.open_states(), ("normal", "normal", "normal"))
        pick.return_value = str(empty)
        self.app._pick_output()
        self.assertEqual(self.open_states(), ("normal", "disabled", "disabled"))

    def test_an_output_folder_that_cannot_be_checked_reads_as_empty(self):
        # Path.is_file()/is_dir() raise PermissionError on Python <= 3.12 for a path under an
        # unreadable parent, and the buttons refresh from __init__: that must not keep the
        # window from opening.
        self.app.output_var.set(str(self.tmp / "locked" / "out"))
        with mock.patch.object(Path, "is_file", side_effect=PermissionError(13, "denied")), \
                mock.patch.object(Path, "is_dir", side_effect=PermissionError(13, "denied")):
            self.app._refresh_output_buttons()
        self.assertEqual(self.open_states(), ("disabled", "disabled", "disabled"))

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



class OpenMenuTests(AppCase):
    def results_on_disk(self) -> Path:
        out = self.tmp / "out"
        (out / "byhand").mkdir(parents=True)
        init_csv(out / "output.csv")
        return out

    def states(self):
        self.app._refresh_open_menu()
        return [str(self.app.open_menu.entrycget(i, "state")) for i in (0, 1, 2, 4)]

    def test_each_entry_follows_the_disk_when_the_menu_opens(self):
        out = self.results_on_disk()
        self.app.output_var.set(str(out))
        self.assertEqual(self.states(), ["normal", "normal", "disabled", "normal"])
        (out / "byhand_retry").mkdir()
        init_csv(out / "byhand_retry" / "output.csv")
        self.assertEqual(self.states(), ["normal"] * 4)

    def test_a_deleted_output_folder_disables_every_entry(self):
        out = self.results_on_disk()
        self.app.output_var.set(str(out))
        shutil.rmtree(out)
        self.assertEqual(self.states(), ["disabled"] * 4)

    def test_the_entries_open_what_they_name(self):
        out = self.results_on_disk()
        (out / "byhand_retry").mkdir()
        init_csv(out / "byhand_retry" / "output.csv")
        self.app.output_var.set(str(out))
        self.app._refresh_open_menu()
        with mock.patch.object(grave_ui.App, "_open_path") as open_path:
            for index in (0, 1, 2, 4):
                self.app.open_menu.invoke(index)
        self.assertEqual([c[0][0] for c in open_path.call_args_list],
                         [out / "output.csv", out / "byhand", out / "byhand_retry" / "output.csv", out])

    def test_the_menu_button_follows_the_output_folder(self):
        self.app.output_var.set(str(self.results_on_disk()))
        self.app._refresh_output_buttons()
        self.assertEqual(str(self.app.btn_open.cget("state")), "normal")
        self.app.output_var.set(str(self.tmp / "missing"))
        self.app._refresh_output_buttons()
        self.assertEqual(str(self.app.btn_open.cget("state")), "disabled")

    def test_the_menu_button_shows_keyboard_focus(self):
        self.assertEqual(ttk.Style(self.root).lookup("TMenubutton", "focuscolor"), grave_ui.THEME["FOCUS"])





class ReadinessTests(AppCase):
    def test_the_status_names_the_next_setup_step(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            steps = [self.app._refresh_readiness()]
            self.app.input_var.set(str(self.tmp))
            steps.append(self.app._refresh_readiness())
            self.app.output_var.set(str(self.tmp))
            steps.append(self.app._refresh_readiness())
            self.app.api_key_var.set("sk-x")
            steps.append(self.app._refresh_readiness())
        self.assertEqual(steps, ["Odaberite ulaznu mapu sa slikama.", "Odaberite izlaznu mapu.",
                                 "Upišite API ključ i kliknite Spremi ključ.", "Spremno."])
        self.assertEqual(self.app.status_var.get(), "Spremno.")

    def test_a_first_launch_shows_how_to_start_and_a_run_clears_it(self):
        self.assertIn("Kako započeti:", self.app.log.get("1.0", "end"))
        self.app._reset_run_state()
        self.assertNotIn("Kako započeti:", self.app.log.get("1.0", "end"))

    def test_a_run_in_progress_keeps_its_status(self):
        self.app.proc = mock.Mock()
        self.app.status_var.set("Obrađujem a.jpg")
        self.assertIsNone(self.app._refresh_readiness())
        self.assertEqual(self.app.status_var.get(), "Obrađujem a.jpg")
        self.app.proc = None

    def test_an_unreadable_env_file_is_no_key_and_no_crash(self):
        with mock.patch.object(grave_ui, "dotenv_values", side_effect=PermissionError("denied")), \
                mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            self.assertFalse(self.app._key_available())
            self.assertEqual(self.app._resolve_api_key(), "")
            root = tk.Tk()
            root.withdraw()
            self.addCleanup(root.destroy)
            grave_ui.App(root)                       # the window still opens



class IconTests(AppCase):
    def test_the_window_gets_the_app_icon(self):
        with mock.patch.object(self.root, "iconphoto") as iconphoto:
            self.app._set_icon()
        iconphoto.assert_called_once_with(True, self.app._icon)
        self.assertEqual((self.app._icon.width(), self.app._icon.height()), (256, 256))

    def test_a_missing_icon_file_is_not_an_error(self):
        with mock.patch.object(grave_ui, "ICON_PATH", self.tmp / "none.png"):
            self.app._set_icon()

class PathFieldTests(AppCase):
    def test_a_long_path_scrolls_to_its_folder_name(self):
        self.app.input_var.set(str(self.tmp / ("dugi_naziv_" * 20) / "Groblje Sv. Marka"))
        self.app._show_path_end(self.app.ent_in)
        self.assertGreater(self.app.ent_in.xview()[0], 0.5)

    def test_hovering_shows_the_full_path(self):
        self.app.input_var.set("/x/y/Groblje")
        tip = self.app._path_tips[0]
        tip.show()
        self.addCleanup(tip._hide)
        self.assertEqual(tip.window.winfo_children()[0].cget("text"), "/x/y/Groblje")
        tip._hide()
        self.assertIsNone(tip.window)

    def test_an_empty_field_shows_no_tooltip(self):
        tip = self.app._path_tips[1]
        tip.show()
        self.assertIsNone(tip.window)

class SearchTests(AppCase):
    def fill(self):
        self.app._append_log("Ivan Horvat\nMarija Horvat\nPetar Kovač\n")

    def test_the_bar_counts_matches_wraps_and_says_when_there_are_none(self):
        self.fill()
        self.app.search_var.set("horvat")
        for expected in ("1/2", "2/2", "1/2"):
            self.app._search_next()
            self.assertEqual(self.app.search_info_var.get(), expected)
        self.app.search_var.set("Babić")
        self.app._search_next()
        self.assertEqual(self.app.search_info_var.get(), "Nema rezultata")
        self.assertEqual(self.app.log.tag_ranges("search"), ())

    def test_shift_return_goes_back(self):
        self.fill()
        self.app.search_var.set("horvat")
        self.app._search_prev()
        self.assertEqual(self.app.search_info_var.get(), "2/2")
        self.assertTrue(self.app._search_entry.bind("<Shift-Return>"))

    def test_a_button_by_the_status_line_opens_the_search(self):
        self.app.btn_search.invoke()
        self.assertEqual(self.app.search_frame.winfo_manager(), "grid")

    def test_hidden_technical_lines_are_not_found(self):
        self.app._log_line("[1/1] Processing a.jpg ...\n", None, ("", None))
        self.app.search_var.set("Processing")
        self.app._search_next()
        self.assertEqual(self.app.search_info_var.get(), "Nema rezultata")

class SubfolderHintTests(AppCase):
    def nested(self) -> Path:
        inp = self.tmp / "groblje"
        (inp / "parcela_A").mkdir(parents=True)
        (inp / "parcela_A" / "p_1_x.jpg").write_bytes(jpeg_bytes())
        self.app.input_var.set(str(inp))
        return inp

    def test_the_preview_points_to_the_subfolders(self):
        self.nested()
        self.app._refresh_preview()
        self.assertIn(ui_logic.SUBFOLDER_HINT.format(n=1), self.app.preview_var.get())

    def test_the_no_photos_warning_points_to_them_too(self):
        self.nested()
        self.app._on_dry_run()
        title, body = self.dialogs["showwarning"].call_args[0]
        self.assertEqual(title, "Nema slika")
        self.assertIn(ui_logic.SUBFOLDER_HINT.format(n=1), body)

class ExcelHintTests(AppCase):
    def test_opening_a_csv_says_how_to_import_it_into_excel(self):
        with mock.patch.object(grave_ui.App, "_open_path", return_value=True):
            self.app._open_csv(self.tmp / "output.csv")
        self.assertEqual(self.app.status_var.get(), grave_ui.EXCEL_HINT)
        self.assertIn("Iz teksta/CSV-a", grave_ui.EXCEL_HINT)

    def test_a_csv_that_did_not_open_gets_no_hint(self):
        self.app.status_var.set("prije")
        with mock.patch.object(grave_ui.App, "_open_path", return_value=False):
            self.app._open_csv(self.tmp / "output.csv")
        self.assertEqual(self.app.status_var.get(), "prije")

    def test_the_menu_and_the_summary_open_csvs_with_the_hint(self):
        out = self.tmp / "out"
        out.mkdir()
        init_csv(out / "output.csv")
        self.app.output_var.set(str(out))
        self.app._refresh_open_menu()
        with mock.patch.object(grave_ui.App, "_open_csv") as open_csv:
            self.app.open_menu.invoke(0)
            win = REAL_SHOW_SUMMARY(self.app, out / "output.csv", "Sažetak obrade")
            self.addCleanup(win.destroy)
            next(w for w in all_widgets(win)
                 if isinstance(w, ttk.Button) and w.cget("text") == "Otvori CSV").invoke()
        self.assertEqual([c[0][0] for c in open_csv.call_args_list], [out / "output.csv"] * 2)
        texts = [str(w.cget("text")) for w in all_widgets(win) if isinstance(w, ttk.Label)]
        self.assertIn(grave_ui.EXCEL_HINT, texts)

class SummaryTests(AppCase):
    def pending_timers(self) -> set[str]:
        return set(self.root.tk.splitlist(self.root.tk.call("after", "info")))

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

    def summary(self, flagged=2, byhand_photos=2, retry=False):
        out = self.tmp / "out"
        (out / "byhand").mkdir(parents=True, exist_ok=True)
        for i in range(byhand_photos):
            (out / "byhand" / f"p_{i}.jpg").write_bytes(jpeg_bytes())
        init_csv(out / "output.csv")
        self.app.counters = {"ok": 1, "partial": flagged, "failed": 0}
        win = REAL_SHOW_SUMMARY(self.app, out / "output.csv", "Sažetak obrade", retry)
        self.addCleanup(win.destroy)
        widgets = all_widgets(win)
        texts = [str(w.cget("text")) for w in widgets if isinstance(w, (ttk.Label, ttk.Button))]
        buttons = {str(w.cget("text")): w for w in widgets if isinstance(w, ttk.Button)}
        return win, texts, buttons

    def test_photos_to_review_are_offered_with_a_priced_retry(self):
        _win, texts, buttons = self.summary()
        self.assertIn("Otvori slike za pregled", buttons)
        self.assertIn(grave_ui.RETRY_LABEL, buttons)
        self.assertTrue(any(t.startswith("Ponovna obrada jačim modelom (Claude Opus 5.5, visok): ~$")
                            and t.endswith(" za 2 slike.") for t in texts), texts)

    def test_the_retry_button_closes_the_summary_and_starts_the_retry(self):
        win, _texts, buttons = self.summary()
        with mock.patch.object(grave_ui.App, "_on_retry_byhand") as retry:
            buttons[grave_ui.RETRY_LABEL].invoke()
        retry.assert_called_once_with()
        self.assertFalse(win.winfo_exists())

    def test_a_clean_run_or_a_retry_run_offers_no_retry(self):
        for flagged, retry in ((0, False), (2, True)):
            with self.subTest(flagged=flagged, retry=retry):
                _win, texts, buttons = self.summary(flagged=flagged, retry=retry)
                self.assertNotIn(grave_ui.RETRY_LABEL, buttons)
                self.assertFalse(any(t.startswith("Ponovna obrada") for t in texts))

    def test_the_summary_says_how_long_the_run_took(self):
        self.app.run_start_time, self.app._last_result_time = 100.0, 600.0
        _win, texts, _buttons = self.summary(flagged=0)
        self.assertIn("Ukupni trošak: $0.00   ·   Trajanje: 8m20s", texts)

    def test_a_summary_closed_at_once_leaves_no_timer_on_a_deleted_command(self):
        # The grab timer waits 100 ms. A timer the popup owns is deleted with it, and when it then
        # fires Tk opens its own "Application Error" window. So close the popup and fire its timers
        # by hand, as Tcl does when they expire: no waiting, and no event loop that would show a
        # window or run the timers other tests left behind.
        csv_path = self.tmp / "output.csv"
        init_csv(csv_path)
        before = self.pending_timers()
        win = REAL_SHOW_SUMMARY(self.app, csv_path, "Sažetak obrade")
        win.withdraw()
        timers = self.pending_timers() - before
        next(w for w in all_widgets(win)
             if isinstance(w, ttk.Button) and w.cget("text") == "Zatvori").invoke()
        for timer in timers:
            script, kind = self.root.tk.splitlist(self.root.tk.call("after", "info", timer))
            if kind == "timer":
                try:
                    self.root.tk.eval(script)
                except tk.TclError as e:
                    self.fail(f"the popup's timer fires on a deleted command: {e}")


class LayoutTests(AppCase):
    def test_the_window_cannot_shrink_below_the_control_row(self):
        self.root.update_idletasks()        # until the widgets are laid out, every requested width reads 1
        min_width, _ = self.root.minsize()
        self.assertGreaterEqual(min_width, self.app._ctrl.winfo_reqwidth() + 28)      # and the row's 14 px margins

    def test_the_window_is_laid_out_after_the_folder_checks_not_before(self):
        # The layout pass can map the window, and the checks may wait on a slow network folder:
        # the window must not appear, blank, before they are done.
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        order = []
        with mock.patch.object(root, "update_idletasks", lambda: order.append("layout")), \
                mock.patch.object(grave_ui.App, "_refresh_preview", lambda app: order.append("preview")), \
                mock.patch.object(grave_ui.App, "_refresh_output_buttons", lambda app: order.append("buttons")):
            grave_ui.App(root)
        self.assertEqual(order, ["preview", "buttons", "layout"])

    def test_ctrl_f_opens_search_even_with_caps_lock(self):
        for sequence in ("<Control-f>", "<Control-F>"):
            self.assertTrue(self.root.bind(sequence), sequence)

    def test_cmd_f_opens_search_on_a_mac_only(self):
        # Off macOS, Tk reads Command as Mod1, which Windows sets while Num Lock is on: with a binding
        # there, every "f" typed in the window (the API key field too) would open the search.
        self.assertEqual(bool(self.root.bind("<Command-f>")), self.root.tk.call("tk", "windowingsystem") == "aqua")
        mac = tk.Tk()
        mac.withdraw()
        self.addCleanup(mac.destroy)
        mac.tk.eval("rename tk ::tk_real; proc tk {args} {if {$args eq {windowingsystem}} {return aqua}; "
                    "uplevel 1 [list ::tk_real {*}$args]}")         # this Tk now answers as it does on a Mac
        grave_ui.App(mac)
        self.assertTrue(mac.bind("<Command-f>"))

    def test_cmd_q_on_a_mac_goes_through_the_close_check(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with mock.patch.object(grave_ui.sys, "platform", "darwin"), \
                mock.patch.object(grave_ui.App, "_on_close") as on_close:
            grave_ui.App(root)
        root.tk.call("tk::mac::Quit")               # the command Tk runs on Cmd+Q
        on_close.assert_called_once()

    def test_the_log_uses_themed_scrollbars(self):
        log = self.app.log
        bars = {str(w.cget("orient")): w for w in log.master.winfo_children() if isinstance(w, ttk.Scrollbar)}
        self.assertEqual(sorted(bars), ["horizontal", "vertical"])
        for i in range(300):
            self.app._append_log(f"line {i} " + "x" * (i % 7 * 40) + "\n")       # more than fits either way
        self.root.update_idletasks()
        axes = (("vertical", log.yview, log.yview_moveto, "yscrollcommand"),
                ("horizontal", log.xview, log.xview_moveto, "xscrollcommand"))
        for orient, view, move_view_to, scroll_option in axes:
            with self.subTest(orient):
                # the log reports its view to this bar ...
                log.tk.call(str(log.cget(scroll_option)), 0.25, 0.75)
                self.assertEqual([round(v, 2) for v in bars[orient].get()], [0.25, 0.75])
                # ... and dragging the bar moves the log along its own axis
                move_view_to(0)
                log.tk.call(str(bars[orient].cget("command")), "moveto", "0.5")
                self.assertGreater(view()[0], 0)

    def test_disabled_controls_look_disabled(self):
        style = ttk.Style(self.root)
        for widget_style in ("TCombobox", "TCheckbutton"):
            self.assertEqual(style.lookup(widget_style, "foreground", ["disabled"]), "#5b606b")

    def test_the_combobox_field_and_button_follow_its_state(self):
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup("TCombobox", "fieldbackground", ["disabled"]), "#23262d")
        self.assertEqual(style.lookup("TCombobox", "background", ["disabled"]), "#23262d")
        self.assertEqual(style.lookup("TCombobox", "background", ["pressed"]), "#343a45")

    def test_the_unused_label_styles_are_gone(self):
        source = Path(grave_ui.__file__).read_text(encoding="utf-8")
        for name in ("Title.TLabel", "Subtitle.TLabel", "Muted.TLabel"):
            self.assertNotIn(name, source)


class CloseTests(AppCase):
    def test_closing_during_a_run_does_not_freeze_the_window(self):
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.App, "_terminate_run"), \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_close()
            destroy.assert_not_called()           # waits for the extractor instead of blocking
            self.app.proc = None                  # what _on_proc_exit does once the last line is handled
            self.app._close_when_stopped(float("inf"))
            destroy.assert_called_once()

    def test_the_window_waits_for_the_exit_to_be_handled_not_just_for_the_process_to_end(self):
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = 130         # the extractor is gone, but its last line is still queued
        with mock.patch.object(self.root, "destroy") as destroy:
            self.app._close_when_stopped(float("inf"))
            destroy.assert_not_called()               # _on_proc_exit has not recorded the run yet
            self.app.proc = None                      # and now it has
            self.app._close_when_stopped(float("inf"))
            destroy.assert_called_once()

    def test_closing_asks_once_and_marks_the_coming_exit_as_a_stop(self):
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.App, "_terminate_run"), \
                mock.patch.object(self.root, "destroy"):
            self.app._on_close()
            self.app._on_close()                      # a second click while the extractor stops
        self.choices.assert_called_once()
        self.assertTrue(self.app._closing)
        self.assertTrue(self.app._stop_requested)     # so the exit counts as a stop, and its stats are kept
        self.app.proc = None

    def test_an_extractor_still_there_at_the_deadline_is_killed_before_the_lock_is_let_go(self):
        order = []
        self.app.proc = mock.Mock()                   # its exit is never handled
        with mock.patch.object(grave_ui.App, "_atexit_kill", lambda app: order.append("kill")), \
                mock.patch.object(grave_ui.App, "_release_lock", lambda app: order.append("release")), \
                mock.patch.object(self.root, "destroy", lambda: order.append("destroy")):
            self.app._close_when_stopped(time.monotonic() + 60)
            self.assertEqual(order, [])               # inside the deadline it only waits
            self.app._close_when_stopped(time.monotonic() - 1)
        self.assertEqual(order, ["kill", "release", "destroy"])
        self.app.proc = None

    def test_a_run_stopped_by_closing_still_teaches_the_estimate_but_shows_nothing(self):
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-sonnet-5", "high"
        for line in ("[1/3] Processing a.jpg ...", "[1/3] OK: a.jpg (1 record) — $0.2500 (total: $0.25)",
                     "[2/3] Processing b.jpg ..."):
            self.app._handle_line("stdout", line + "\n")
        self.app._closing = True
        self.app._stop_requested = True
        self.app._on_proc_exit(130)
        stats = json.loads(self.settings_path.read_text(encoding="utf-8"))["stats"]
        self.assertEqual(stats["claude-sonnet-5|high"]["n"], 1)
        self.app._draw_attention.assert_not_called()
        for name, dialog in self.dialogs.items():
            with self.subTest(dialog=name):
                dialog.assert_not_called()

    def test_closing_during_a_run_closes_once_the_stopped_run_is_recorded_and_says_nothing(self):
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-sonnet-5", "high"
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.App, "_terminate_run"), \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_close()                      # the user confirms; the stop is under way
            for line in ("[1/3] Processing a.jpg ...", "[1/3] OK: a.jpg (1 record) — $0.2500 (total: $0.25)",
                         "[2/3] Processing b.jpg ..."):
                self.app._handle_line("stdout", line + "\n")
            self.app._on_proc_exit(130)               # the extractor's last line is handled
            self.app._close_when_stopped(float("inf"))
        destroy.assert_called_once()
        stats = json.loads(self.settings_path.read_text(encoding="utf-8"))["stats"]
        self.assertEqual(stats["claude-sonnet-5|high"]["n"], 1)
        self.choices.assert_called_once()             # only the close question
        for name in self.dialogs:
            with self.subTest(dialog=name):
                self.dialogs[name].assert_not_called()
        self.app._draw_attention.assert_not_called()

    def test_closing_switches_stop_off_and_a_stop_after_that_starts_nothing(self):
        self.app._set_running(True)                   # as during a run: Stop is on
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.threading, "Thread") as thread, \
                mock.patch.object(self.root, "destroy"):
            self.app._on_close()
            self.assertEqual(str(self.app.btn_stop.cget("state")), "disabled")
            self.app._on_stop()                       # a click that was already on its way
        thread.assert_called_once()                   # the stopper _on_close started, and no second one
        self.app.proc = None

    def test_closing_while_a_stop_is_under_way_stops_no_second_time(self):
        self.app._set_running(True)
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.threading, "Thread") as thread, \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_stop()                       # Stop was pressed and the extractor is still stopping
            self.app._on_close()
        thread.assert_called_once()                   # the Stop click's stopper, and no second one
        self.assertTrue(self.app._closing)
        destroy.assert_not_called()                   # the window still waits for the exit
        self.app.proc = None

    def test_a_closing_window_keeps_its_controls_off_when_the_run_ends(self):
        out = self.tmp / "out"
        (out / "byhand").mkdir(parents=True)
        (out / "byhand" / "p_1_x.jpg").write_bytes(jpeg_bytes())      # so the retry button would come back on
        self.app.output_var.set(str(out))
        self.app._set_running(True)
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        with mock.patch.object(grave_ui.App, "_terminate_run"), \
                mock.patch.object(self.root, "destroy"):
            self.app._on_close()
            self.app._on_proc_exit(130)               # the extractor is gone; the window goes on its next tick
        for name in ("btn_start", "btn_in", "btn_out", "btn_open", "btn_dry", "btn_retry_byhand",
                     "model_combo", "effort_combo"):
            with self.subTest(control=name):
                self.assertEqual(str(getattr(self.app, name).cget("state")), "disabled")

    def test_closing_after_the_extractor_ended_asks_nothing_and_waits_for_its_exit_to_be_recorded(self):
        self.app._reset_run_state()
        self.app._run_model, self.app._run_effort = "claude-sonnet-5", "high"
        for line in ("[1/1] Processing a.jpg ...", "[1/1] OK: a.jpg (1 record) — $0.2500 (total: $0.25)",
                     "Done. 1 images processed. 1 succeeded, 0 partial, 0 failed."):
            self.app._handle_line("stdout", line + "\n")
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = 0           # it finished, and its exit is still queued
        with mock.patch.object(grave_ui.threading, "Thread") as thread, \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_close()
            self.choices.assert_not_called()          # nothing left to stop, so nothing to ask
            thread.assert_not_called()
            destroy.assert_not_called()               # _on_proc_exit has not recorded the run yet
            self.app._on_proc_exit(0)
            self.app._close_when_stopped(float("inf"))
        destroy.assert_called_once()
        self.assertFalse(self.app._stop_requested)    # it ended on its own: a failure must not pass as a stop
        stats = json.loads(self.settings_path.read_text(encoding="utf-8"))["stats"]
        self.assertEqual(stats["claude-sonnet-5|high"]["n"], 1)
        for name, dialog in self.dialogs.items():
            with self.subTest(dialog=name):
                dialog.assert_not_called()
        for shown in (self.app._draw_attention, self.app._notify_done, self.app._show_summary_popup):
            shown.assert_not_called()

    def test_declining_the_close_question_leaves_the_run_alone(self):
        self.app._set_running(True)
        self.app.proc = mock.Mock()
        self.app.proc.poll.return_value = None
        self.answer("cancel")
        with mock.patch.object(grave_ui.threading, "Thread") as thread, \
                mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_close()
        self.assertFalse(self.app._closing)
        self.assertFalse(self.app._stop_requested)
        self.assertEqual(str(self.app.btn_stop.cget("state")), "normal")
        thread.assert_not_called()
        destroy.assert_not_called()
        self.app.proc = None

    def test_closing_with_no_run_closes_at_once_without_asking(self):
        with mock.patch.object(self.root, "destroy") as destroy:
            self.app._on_close()
        destroy.assert_called_once()
        self.choices.assert_not_called()


class WindowsStopTests(AppCase):
    def test_stop_in_the_frozen_build_runs_taskkill_at_once(self):
        # PyInstaller 6.9+ runs the extractor as a worker of the GUI's own unpacked folder: there is
        # no bootloader child to end first, so nothing may be waited for before taskkill.
        order = []
        proc = mock.Mock(pid=4321)
        proc.poll.side_effect = [None, 0]             # gone as soon as taskkill has run
        self.app.proc, self.app.pgid = proc, 4321
        with mock.patch.object(grave_ui.sys, "platform", "win32"), \
                mock.patch.object(grave_ui.sys, "frozen", True, create=True), \
                mock.patch.object(grave_ui.subprocess, "CREATE_NO_WINDOW", 0, create=True), \
                mock.patch.object(grave_ui.subprocess, "run", lambda cmd, **kwargs: order.append(cmd)), \
                mock.patch("time.sleep", lambda seconds: order.append("sleep")):
            self.app._terminate_run()
        self.assertEqual(order, [["taskkill", "/T", "/F", "/PID", "4321"]])      # no sleep before or after it
        proc.terminate.assert_not_called()            # it returned right after taskkill
        proc.kill.assert_not_called()
        self.app.proc = None

    def test_the_window_is_not_scaled_off_windows(self):
        self.assertEqual(self.app._dpi_scale(), 1.0)


class WindowsDisplayTests(AppCase):
    def test_the_window_scales_with_a_windows_display_but_never_shrinks(self):
        for dpi, scale in ((96.0, 1.0), (144.0, 1.5), (192.0, 2.0), (72.0, 1.0)):
            with self.subTest(dpi=dpi), \
                    mock.patch.object(grave_ui.sys, "platform", "win32"), \
                    mock.patch.object(self.root, "winfo_fpixels", return_value=dpi) as fpixels:
                self.assertEqual(self.app._dpi_scale(), scale)
                fpixels.assert_called_once_with("1i")
        with mock.patch.object(grave_ui.sys, "platform", "win32"), \
                mock.patch.object(self.root, "winfo_fpixels", side_effect=tk.TclError):
            self.assertEqual(self.app._dpi_scale(), 1.0)

    def test_a_dense_display_off_windows_does_not_scale_the_window(self):
        with mock.patch.object(grave_ui.sys, "platform", "linux"), \
                mock.patch.object(self.root, "winfo_fpixels", return_value=192.0):
            self.assertEqual(self.app._dpi_scale(), 1.0)

    def test_the_window_is_sized_for_the_display_and_kept_on_it(self):
        for scale, screen, size in ((1.5, (3840, 2160), "1770x1050"),      # room to spare: scaled
                                    (1.5, (1280, 720), "1152x648"),        # no room: kept on screen
                                    (1.0, (2560, 1440), "1180x700")):
            with self.subTest(scale=scale, screen=screen):
                root = tk.Tk()
                root.withdraw()
                self.addCleanup(root.destroy)
                with mock.patch.object(grave_ui.App, "_dpi_scale", return_value=scale), \
                        mock.patch.object(root, "winfo_screenwidth", return_value=screen[0]), \
                        mock.patch.object(root, "winfo_screenheight", return_value=screen[1]), \
                        mock.patch.object(root, "geometry") as geometry:
                    grave_ui.App(root)
                geometry.assert_called_once_with(size)

    def test_main_makes_the_process_dpi_aware_before_the_first_window_on_windows_only(self):
        for platform, expected in (("win32", ["aware", "tk", "app", "tk().mainloop"]),
                                   ("linux", ["tk", "app", "tk().mainloop"])):
            with self.subTest(platform=platform):
                calls = mock.Mock()
                with mock.patch.object(grave_ui.sys, "platform", platform), \
                        mock.patch.object(grave_ui, "_enable_dpi_awareness", calls.aware), \
                        mock.patch.object(grave_ui.tk, "Tk", calls.tk), \
                        mock.patch.object(grave_ui, "App", calls.app):
                    grave_ui.main()
                self.assertEqual([call[0] for call in calls.mock_calls], expected)

    def test_dpi_awareness_asks_for_the_system_setting(self):
        windll = mock.Mock()
        with mock.patch.object(ctypes, "windll", windll, create=True):
            grave_ui._enable_dpi_awareness()
        windll.shcore.SetProcessDpiAwareness.assert_called_once_with(1)
        windll.user32.SetProcessDPIAware.assert_not_called()

    def test_dpi_awareness_falls_back_to_the_old_call_and_never_raises(self):
        for error in (OSError, AttributeError):     # Windows 7 has no shcore.dll; 8.0 has it, without this call
            with self.subTest(error=error.__name__):
                windll = mock.Mock()
                windll.shcore.SetProcessDpiAwareness.side_effect = error
                with mock.patch.object(ctypes, "windll", windll, create=True):
                    grave_ui._enable_dpi_awareness()
                windll.user32.SetProcessDPIAware.assert_called_once_with()
        with mock.patch.object(ctypes, "windll", mock.Mock(spec=[]), create=True):      # no windll at all
            grave_ui._enable_dpi_awareness()


class AttentionTests(AppCase):
    def test_a_run_that_ends_flashes_the_taskbar_button(self):
        with mock.patch.object(grave_ui.sys, "platform", "win32"), \
                mock.patch.object(grave_ui, "_flash_taskbar") as flash, \
                mock.patch.object(self.root, "deiconify"), \
                mock.patch.object(self.root, "lift"), \
                mock.patch.object(self.root, "bell"):       # the real method, on a root that is never shown
            REAL_DRAW_ATTENTION(self.app)
        flash.assert_called_once_with(self.root)
