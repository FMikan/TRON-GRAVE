"""A hidden Tk root with dialogs and side effects stubbed, for testing grave_ui.App."""

import shutil
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import grave_ui

REAL_SHOW_SUMMARY = grave_ui.App._show_summary_popup
REAL_LAUNCH_SUBPROCESS = grave_ui.App._launch_subprocess
REAL_DRAW_ATTENTION = grave_ui.App._draw_attention
REAL_PRESENT = grave_ui.App._present
REAL_ASK_EXISTING_OUTPUT = grave_ui.App._ask_existing_output


class AppCase(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest(f"no display: {e}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings_path = self.tmp / "config" / "ui.json"
        self._patch(grave_ui, "SETTINGS_PATH", self.settings_path)
        self._patch(grave_ui.atexit, "register", mock.Mock())
        self.dialogs = {name: self._patch(grave_ui.messagebox, name, mock.Mock(return_value=True))
                        for name in ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel")}
        self._patch(grave_ui.App, "_notify_done", mock.Mock())
        self._patch(grave_ui.App, "_draw_attention", mock.Mock())
        self._patch(grave_ui.App, "_show_summary_popup", mock.Mock())
        self._patch(grave_ui.App, "_ask_existing_output", mock.Mock(side_effect=AssertionError(
            "unexpected Nastavi/Prepiši/Odustani dialog: patch _ask_existing_output in this test")))
        # Placing a dialog maps it; a test must never flash a window.
        self._patch(grave_ui.App, "_present", mock.Mock())
        # Every _ask_choice question answers with its action (the first choice) unless a test
        # calls self.answer(...): the old askyesno -> True.
        self.choices = self._patch(grave_ui.App, "_ask_choice", mock.Mock(
            side_effect=lambda title, text, choices, default=None: choices[0][0]))
        self.launched = []
        self._patch(grave_ui.App, "_launch_subprocess", lambda app, cmd: self.launched.append(cmd))
        self._patch(grave_ui.ui_logic, "LEGACY_SETTINGS_PATH", self.tmp / "legacy" / "ui.json")
        self._patch(grave_ui, "PROJECT_DIR", self.tmp)          # never read the developer's real .env
        self.app = grave_ui.App(self.root)

    def answer(self, key):
        """Make every _ask_choice question answer `key` (None is Esc or the close box)."""
        self.choices.side_effect = None
        self.choices.return_value = key

    def _patch(self, target, attribute, value):
        patcher = mock.patch.object(target, attribute, value)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked
