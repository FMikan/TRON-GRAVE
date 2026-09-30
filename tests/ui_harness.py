"""A hidden Tk root with dialogs and side effects stubbed, for testing grave_ui.App."""

import shutil
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import grave_ui

REAL_SHOW_SUMMARY = grave_ui.App._show_summary_popup


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
        self._patch(grave_ui.App, "_show_summary_popup", mock.Mock())
        self.launched = []
        self._patch(grave_ui.App, "_launch_subprocess", lambda app, cmd: self.launched.append(cmd))
        self._patch(grave_ui.ui_logic, "LEGACY_SETTINGS_PATH", self.tmp / "legacy" / "ui.json")
        self.app = grave_ui.App(self.root)

    def _patch(self, target, attribute, value):
        patcher = mock.patch.object(target, attribute, value)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked
