#!/usr/bin/env python3
"""TRON-GRAVE desktop UI — wraps grave_extractor.py with a Tkinter front-end."""

import atexit
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dotenv import dotenv_values

import ui_logic
from extractor.csv_writer import check_writable, read_processed, resume_problem
from extractor.file_utils import back_up_outputs, is_heic, is_supported_image
from _version import __version__


if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle — exe lives next to all bundled files
    PROJECT_DIR = Path(sys.executable).parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))    # the one-file exe's unpacked folder
    _EXTRACTOR_CMD = [sys.executable, "--_run-extractor"]
else:
    PROJECT_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = PROJECT_DIR
    _EXTRACTOR_CMD = [sys.executable, "-u", str(Path(__file__).resolve().parent / "grave_extractor.py")]
SETTINGS_PATH = ui_logic.settings_path()
ICON_PATH = RESOURCE_DIR / "assets" / "tron-grave.png"

LOG_LINE_CAP = 10_000          # both views of the log are kept
LOG_TRIM_BATCH = 1_000
DRAIN_CAP_PER_TICK = 200
MAX_LINE_CHARS = 4096
BASE_WIDTH, BASE_HEIGHT = 1180, 700

# Every colour the window uses. White text on ACCENT is 4.7:1 and FOCUS is 6.5:1 against a button,
# so the main button and the focus ring both pass WCAG AA.
THEME = {
    "BG": "#16181d",            # window background
    "SURFACE": "#1e2127",       # popups, dropdown lists, tooltips
    "INPUT": "#2a2e37",         # entries, buttons, troughs
    "TEXT": "#e6e6e6",
    "MUTED": "#9aa0a6",
    "BORDER": "#333945",
    "ACCENT": "#3b6fd8",
    "ACCENT_HOVER": "#2f62cc",
    "ACCENT_DOWN": "#2a57b5",
    "ON_ACCENT": "#ffffff",
    "HOVER": "#343a45",
    "DISABLED_BG": "#23262d",
    "DISABLED_FG": "#5b606b",
    "FOCUS": "#8fb3ff",
    "LOG_BG": "#15171c",
    "SELECT": "#3a4a6b",
    "SEARCH": "#4a3a00",
    "OK": "#7ee787",
    "WARN": "#e3b341",
    "ERR": "#ff7b72",
    "INFO": "#79b8ff",
}

RETRY_LABEL = "Ponovno obradi jačim modelom…"
# Excel opens a double-clicked .csv with the system list separator, ";" under Croatian settings.
EXCEL_HINT = "U Excelu: Podaci → Iz teksta/CSV-a (Data → From Text/CSV), razdjelnik: zarez."
ONBOARDING = ("Kako započeti:\n"
              "1. Ulazna mapa — mapa sa slikama spomenika.\n"
              "2. Izlazna mapa — tu nastaju output.csv i byhand/.\n"
              "3. API ključ — upišite ga i kliknite Spremi ključ.\n"
              "4. Pokreni.\n")


class Tooltip:
    """A small hint that appears after hovering over a widget for a moment."""

    DELAY_MS = 600

    def __init__(self, widget, text_fn, font=None):
        self.widget, self.text_fn, self.font = widget, text_fn, font
        self.window = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.DELAY_MS, self.show)

    def _cancel(self):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def show(self):
        self._after = None
        text = self.text_fn()
        if not text or self.window is not None:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{self.widget.winfo_rootx() + 8}"
                                f"+{self.widget.winfo_rooty() + self.widget.winfo_height() + 4}")
        tk.Label(self.window, text=text, background=THEME["SURFACE"], foreground=THEME["TEXT"],
                 borderwidth=1, relief="solid", padx=6, pady=3, justify="left",
                 font=self.font).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"TRON-GRAVE {__version__}")
        # Wide enough for the full control row: Start/Stop, the dry-run checkbox, and the three
        # Open/Retry buttons. At 960 the last button was clipped off-screen. Scaled for DPI and
        # kept on screen.
        self._scale = self._dpi_scale()
        width, height = ui_logic.scaled_geometry(
            BASE_WIDTH, BASE_HEIGHT, self._scale,
            self.root.winfo_screenwidth(), self.root.winfo_screenheight(),
        )
        self.root.geometry(f"{width}x{height}")
        self._set_icon()

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.model_var = tk.StringVar(value=ui_logic.MODEL_LABELS[ui_logic.DEFAULT_MODEL])
        self.effort_var = tk.StringVar(value=ui_logic.EFFORT_LABELS[ui_logic.DEFAULT_EFFORT])
        self.status_var = tk.StringVar(value="Spremno.")
        self.preview_var = tk.StringVar(value="")
        self.search_var = tk.StringVar()

        self.proc: subprocess.Popen | None = None
        self.pgid: int | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.line_count = 0
        self.run_start_time: float | None = None
        self._last_result_time: float | None = None
        self.last_total: int | None = None
        self.counters = {"ok": 0, "partial": 0, "failed": 0}
        self.total_cost = 0.0
        self.lock_path: Path | None = None
        self.lock_token: str | None = None
        self._search_index = None
        self._is_retry_run = False
        self._run_out_dir: Path | None = None
        self._run_model = ui_logic.DEFAULT_MODEL
        self._run_effort = ui_logic.DEFAULT_EFFORT
        self._stop_requested = False
        self._closing = False
        self._launched_dry_run = False
        self._saw_done_line = False
        self._last_stderr = ""
        self._run_files: dict[int, str] = {}
        self._flagged: set[str] = set()
        self._done_count = 0
        self._api_key: str = ""
        self._settings: dict = {}
        self._log_file = None

        self._load_settings()
        self._fonts = self._pick_fonts()
        self._apply_theme()
        self._build_ui()
        self._show_path_end(self.ent_in)
        self._show_path_end(self.ent_out)
        self.root.report_callback_exception = self._report_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            # Cmd+Q bypasses WM_DELETE_WINDOW on macOS; route it through the same check.
            self.root.createcommand("tk::mac::Quit", self._on_close)
        atexit.register(self._atexit_kill)
        self.root.after(50, self._drain_queue)
        self._refresh_preview()
        self._refresh_output_buttons()
        if self._refresh_readiness() != "Spremno.":     # the steps only while setup is incomplete
            self._append_log(ONBOARDING, "info")
        self._refresh_key_hint()
        self.api_key_var.trace_add("write", self._on_key_edit)
        # Last of all: the layout pass can map the window, and the folder checks above may wait on
        # a slow network share. Never narrower than the control row: the Croatian labels are long
        # and fonts differ per OS and DPI, so a fixed minimum cut off the last button, and at 175 %
        # a fixed height hid the buttons and the log: keep everything above the log plus 4 lines.
        self.root.update_idletasks()
        line = tkfont.Font(root=self.root, font=self.log.cget("font")).metrics("linespace")
        above_log = self.root.winfo_reqheight() - self._log_frame.winfo_reqheight()
        self.root.minsize(max(900, self._ctrl.winfo_reqwidth() + 28),
                          above_log + 4 * line + self._hbar.winfo_reqheight())

    def _set_icon(self):
        """The headstone in the title bar and the taskbar instead of Tk's feather."""
        try:
            self._icon = tk.PhotoImage(master=self.root, file=str(ICON_PATH))
            self.root.iconphoto(True, self._icon)
        except tk.TclError:
            pass        # a missing or unreadable icon must not keep the window from opening

    def _dpi_scale(self) -> float:
        """How much denser than 96 dpi the screen is (Windows, once DPI-aware); 1.0 elsewhere."""
        if sys.platform != "win32":
            return 1.0
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96)
        except tk.TclError:
            return 1.0

    def _px(self, n: int) -> int:
        """n pixels at 96 dpi, in this display's pixels (Tk scales fonts, not pixel sizes)."""
        return max(1, round(n * self._scale))

    # ----- UI construction --------------------------------------------------

    def _pick_fonts(self) -> dict:
        """Segoe UI and Consolas where Windows has them, else Tk's own default and fixed fonts.

        "Segoe UI Semibold" is its own family on Windows; elsewhere Tk would quietly swap in a
        regular face, so the headings ask for bold instead.
        """
        families = set(tkfont.families(self.root))
        ui = ("Segoe UI" if "Segoe UI" in families
              else tkfont.nametofont("TkDefaultFont", root=self.root).actual("family"))
        mono = ("Consolas" if "Consolas" in families
                else tkfont.nametofont("TkFixedFont", root=self.root).actual("family"))
        semibold = "Segoe UI Semibold" in families

        def heading(size):
            return ("Segoe UI Semibold", size) if semibold else (ui, size, "bold")

        return {"body": (ui, 10), "small": (ui, 9), "mono": (mono, 10),
                "strong": heading(10), "title": heading(13), "app": heading(17)}

    def _apply_theme(self):
        """Hand-crafted flat dark theme on the ttk 'clam' base — no extra dependencies."""
        t = THEME
        self._bg = t["BG"]
        self.root.configure(background=t["BG"])

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = self._fonts["body"]
        style.configure(".", background=t["BG"], foreground=t["TEXT"], font=base_font,
                        fieldbackground=t["INPUT"], bordercolor=t["BORDER"],
                        lightcolor=t["BG"], darkcolor=t["BG"])

        style.configure("TFrame", background=t["BG"])
        style.configure("TLabel", background=t["BG"], foreground=t["TEXT"], font=base_font)

        style.configure("TButton", background=t["INPUT"], foreground=t["TEXT"], borderwidth=0,
                        padding=(self._px(14), self._px(8)), font=base_font, focuscolor=t["FOCUS"])
        style.map("TButton",
                  background=[("disabled", t["DISABLED_BG"]), ("pressed", t["BORDER"]), ("active", t["HOVER"])],
                  foreground=[("disabled", t["DISABLED_FG"])])

        style.configure("TMenubutton", background=t["INPUT"], foreground=t["TEXT"], borderwidth=0,
                        padding=(self._px(14), self._px(8)), arrowcolor=t["TEXT"], focuscolor=t["FOCUS"])
        style.map("TMenubutton",
                  background=[("disabled", t["DISABLED_BG"]), ("pressed", t["BORDER"]), ("active", t["HOVER"])],
                  foreground=[("disabled", t["DISABLED_FG"])], arrowcolor=[("disabled", t["DISABLED_FG"])])

        style.configure("Accent.TButton", background=t["ACCENT"], foreground=t["ON_ACCENT"],
                        borderwidth=0, padding=(self._px(16), self._px(8)), font=self._fonts["strong"],
                        focuscolor=t["ON_ACCENT"])
        style.map("Accent.TButton",
                  background=[("disabled", t["DISABLED_BG"]), ("pressed", t["ACCENT_DOWN"]),
                              ("active", t["ACCENT_HOVER"])],
                  foreground=[("disabled", t["DISABLED_FG"])])

        style.configure("TEntry", fieldbackground=t["INPUT"], foreground=t["TEXT"],
                        bordercolor=t["BORDER"], insertcolor=t["TEXT"], padding=self._px(6))
        style.map("TEntry",
                  fieldbackground=[("readonly", t["INPUT"])],
                  foreground=[("readonly", t["TEXT"])],
                  bordercolor=[("focus", t["ACCENT"])])

        style.configure("TCombobox", fieldbackground=t["INPUT"], background=t["INPUT"],
                        foreground=t["TEXT"], arrowcolor=t["TEXT"], bordercolor=t["BORDER"],
                        padding=self._px(5), arrowsize=self._px(14))
        style.map("TCombobox",
                  fieldbackground=[("disabled", t["DISABLED_BG"]), ("readonly", t["INPUT"])],
                  foreground=[("disabled", t["DISABLED_FG"]), ("readonly", t["TEXT"])],
                  background=[("disabled", t["DISABLED_BG"]), ("pressed", t["HOVER"]), ("active", t["HOVER"])],
                  bordercolor=[("focus", t["ACCENT"])],
                  arrowcolor=[("disabled", t["DISABLED_FG"])])
        self.root.option_add("*TCombobox*Listbox.background", t["SURFACE"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["TEXT"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["ACCENT"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["ON_ACCENT"])

        style.configure("TCheckbutton", background=t["BG"], foreground=t["TEXT"], focuscolor=t["FOCUS"],
                        indicatorbackground=t["INPUT"], indicatorforeground=t["TEXT"],
                        indicatorsize=self._px(10))
        style.map("TCheckbutton", background=[("active", t["BG"])],
                  foreground=[("disabled", t["DISABLED_FG"])],
                  indicatorbackground=[("disabled", t["DISABLED_BG"]), ("selected", t["ACCENT"])])

        style.configure("TProgressbar", troughcolor=t["INPUT"], background=t["ACCENT"],
                        bordercolor=t["BG"], lightcolor=t["ACCENT"], darkcolor=t["ACCENT"],
                        thickness=self._px(8))

        style.configure("TScrollbar", troughcolor=t["BG"], background=t["INPUT"],
                        bordercolor=t["BG"], arrowcolor=t["MUTED"], arrowsize=self._px(14))
        style.map("TScrollbar", background=[("active", t["BORDER"])])

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        header = ttk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ttk.Label(header, text="TRON-GRAVE", font=self._fonts["app"]).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header, text="Izdvajanje podataka s nadgrobnih spomenika",
            font=self._fonts["small"], foreground=THEME["MUTED"],
        ).grid(row=1, column=0, sticky="w")

        top = ttk.Frame(self.root)
        top.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Ulazna mapa").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.ent_in = ttk.Entry(top, textvariable=self.input_var, state="readonly")
        self.ent_in.grid(row=0, column=1, sticky="ew", padx=6, pady=6)
        self.btn_in = ttk.Button(top, text="Odaberi…", command=self._pick_input)
        self.btn_in.grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(top, text="Izlazna mapa").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.ent_out = ttk.Entry(top, textvariable=self.output_var, state="readonly")
        self.ent_out.grid(row=1, column=1, sticky="ew", padx=6, pady=6)
        self.btn_out = ttk.Button(top, text="Odaberi…", command=self._pick_output)
        self.btn_out.grid(row=1, column=2, padx=6, pady=6)
        self._path_tips = []
        for entry, var in ((self.ent_in, self.input_var), (self.ent_out, self.output_var)):
            entry.bind("<Configure>", lambda _e, en=entry: self._show_path_end(en), add="+")
            self._path_tips.append(Tooltip(entry, var.get, font=self._fonts["small"]))

        ttk.Label(top, text="API ključ").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        key_row = ttk.Frame(top)
        key_row.grid(row=2, column=1, sticky="ew", padx=6, pady=6)
        key_row.columnconfigure(0, weight=1)
        self.ent_key = ttk.Entry(key_row, textvariable=self.api_key_var, show="•")
        self.ent_key.grid(row=0, column=0, sticky="ew")
        self.ent_key.bind("<Return>", lambda _e: self._on_save_key())
        self.show_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(key_row, text="Prikaži", variable=self.show_key_var,
                        command=self._toggle_key_visibility).grid(row=0, column=1, padx=(10, 0))
        self.btn_save_key = ttk.Button(top, text="Spremi ključ", command=self._on_save_key)
        self.btn_save_key.grid(row=2, column=2, padx=6, pady=6)
        self.key_hint_var = tk.StringVar()
        self._key_hint = ttk.Label(top, textvariable=self.key_hint_var, foreground=THEME["MUTED"],
                                   font=self._fonts["small"])
        self._key_hint.grid(row=3, column=1, sticky="w", padx=6, pady=(0, 4))
        self._key_hint.grid_remove()

        ttk.Label(top, text="Model").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        self.model_combo = ttk.Combobox(
            top, textvariable=self.model_var, state="readonly", width=30,
            values=[ui_logic.MODEL_LABELS[m] for m in ui_logic.MODELS],
        )
        self.model_combo.grid(row=4, column=1, sticky="w", padx=6, pady=6)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        ttk.Label(top, text="Napor").grid(row=5, column=0, sticky="w", padx=6, pady=6)
        self.effort_combo = ttk.Combobox(
            top, textvariable=self.effort_var, state="readonly", width=30,
        )
        self.effort_combo.grid(row=5, column=1, sticky="w", padx=6, pady=6)
        self.effort_combo.bind("<<ComboboxSelected>>", self._on_effort_change)
        self._refresh_effort_options()

        self._preview_label = ttk.Label(top, textvariable=self.preview_var, foreground=THEME["MUTED"])
        self._preview_label.grid(row=6, column=0, columnspan=3, sticky="ew", padx=6, pady=(2, 0))
        top.bind("<Configure>", lambda e: self._preview_label.configure(
            wraplength=max(self._px(200), e.width - self._px(12))))

        ctrl = ttk.Frame(self.root)
        ctrl.grid(row=2, column=0, sticky="ew", padx=14)
        ctrl.columnconfigure(3, weight=1)
        self._ctrl = ctrl                       # __init__ sizes the window's minimum from its width

        self.btn_start = ttk.Button(
            ctrl, text="▶  Pokreni", command=self._on_start, style="Accent.TButton"
        )
        self.btn_start.grid(row=0, column=0, padx=(0, 6), pady=4)
        self.btn_stop = ttk.Button(ctrl, text="Zaustavi", command=self._on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=6, pady=4)
        self.btn_dry = ttk.Button(ctrl, text="Probni prolaz", command=self._on_dry_run)
        self.btn_dry.grid(row=0, column=2, padx=6, pady=4)

        self.btn_open = ttk.Menubutton(ctrl, text="Otvori", state="disabled")
        self.open_menu = tk.Menu(self.btn_open, tearoff=False, postcommand=self._refresh_open_menu,
                                 background=THEME["SURFACE"], foreground=THEME["TEXT"],
                                 activebackground=THEME["ACCENT"], activeforeground=THEME["ON_ACCENT"],
                                 disabledforeground=THEME["DISABLED_FG"], borderwidth=0)
        self.open_menu.add_command(label="output.csv",
                                   command=lambda: self._open_csv(self._out_path() / "output.csv"))
        self.open_menu.add_command(label="Slike za pregled (byhand/)",
                                   command=lambda: self._open_path(self._out_path() / "byhand"))
        self.open_menu.add_command(label="Rezultati ponovne obrade (byhand_retry/output.csv)",
                                   command=lambda: self._open_csv(self._out_path() / "byhand_retry" / "output.csv"))
        self.open_menu.add_separator()
        self.open_menu.add_command(label="Izlazna mapa", command=lambda: self._open_path(self._out_path()))
        self.btn_open.configure(menu=self.open_menu)
        self.btn_open.grid(row=0, column=4, padx=6)
        self.btn_retry_byhand = ttk.Button(ctrl, text=RETRY_LABEL, command=self._on_retry_byhand,
                                           state="disabled")
        self.btn_retry_byhand.grid(row=0, column=5, padx=(6, 0))

        prog = ttk.Frame(self.root)
        prog.grid(row=3, column=0, sticky="ew", padx=14, pady=8)
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._status_label = ttk.Label(prog, textvariable=self.status_var, foreground=THEME["TEXT"],
                                       font=self._fonts["strong"])
        self._status_label.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._status_right = ttk.Frame(prog)       # Tehnički zapis, Traži… and the version
        self._status_right.grid(row=1, column=1, sticky="ne", padx=(12, 0), pady=(4, 0))
        ttk.Label(self._status_right, text=f"v{__version__}", foreground=THEME["MUTED"]).pack(side="right")
        self.raw_log_var = tk.BooleanVar(value=False)
        self.chk_raw = ttk.Checkbutton(self._status_right, text="Tehnički zapis",
                                       variable=self.raw_log_var, command=self._toggle_raw_log)
        self.chk_raw.pack(side="right", padx=(0, 12))
        self.btn_search = ttk.Button(self._status_right, text="Traži… (Ctrl+F)", command=self._show_search)
        self.btn_search.pack(side="right", padx=(0, 12), before=self.chk_raw)
        prog.bind("<Configure>", lambda e: self._status_label.configure(
            wraplength=max(self._px(200), e.width - self._status_right.winfo_reqwidth() - self._px(24))))

        log_frame = ttk.Frame(self.root)
        self._log_frame = log_frame
        log_frame.grid(row=4, column=0, sticky="nsew", padx=14, pady=(6, 12))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        t = THEME
        self.log = tk.Text(
            log_frame, wrap="none", height=15, borderwidth=0, relief="flat",
            font=self._fonts["mono"], state="disabled",
            background=t["LOG_BG"], foreground=t["TEXT"], insertbackground=t["TEXT"],
            selectbackground=t["SELECT"], highlightthickness=1,
            highlightbackground=t["BORDER"], highlightcolor=t["ACCENT"],
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        for tag, colour in (("stderr", t["ERR"]), ("error", t["ERR"]), ("failed", t["ERR"]),
                            ("info", t["INFO"]), ("done", t["OK"]), ("ok", t["OK"]),
                            ("review", t["WARN"]), ("warn", t["WARN"])):
            self.log.tag_config(tag, foreground=colour)
        self.log.tag_config("search", background=t["SEARCH"])
        self.log.tag_config("raw", elide=True)      # the extractor's own lines: Tehnički zapis
        self.log.tag_config("nice", elide=False)    # the same lines in Croatian

        # ttk scrollbars, so both follow the dark theme (ScrolledText's are classic light ones).
        vbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        self._hbar = hbar
        hbar.grid(row=1, column=0, sticky="ew")
        self.log.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        self.search_frame = ttk.Frame(self.root)
        ttk.Label(self.search_frame, text="Traži:").pack(side="left", padx=(8, 4))
        self._search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var)
        self._search_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.search_info_var = tk.StringVar()
        ttk.Label(self.search_frame, textvariable=self.search_info_var, foreground=THEME["MUTED"]).pack(
            side="left", padx=4)
        ttk.Button(self.search_frame, text="Sljedeće", command=self._search_next).pack(side="left", padx=4)
        ttk.Button(self.search_frame, text="Zatvori", command=self._hide_search).pack(side="left", padx=(4, 8))
        self._search_entry.bind("<Return>", lambda _e: self._search_next())
        self._search_entry.bind("<Shift-Return>", lambda _e: self._search_prev())

        # Caps Lock turns Ctrl+F into keysym F; macOS users press Cmd+F. Cmd is bound on Aqua only:
        # elsewhere Tk reads it as Mod1, which Windows sets while Num Lock is on, and every "f"
        # typed in the window (the API key field too) would open the search.
        sequences = ["<Control-f>", "<Control-F>"]
        if self.root.tk.call("tk", "windowingsystem") == "aqua":
            sequences.append("<Command-f>")
        for sequence in sequences:
            self.root.bind(sequence, lambda _e: self._show_search())
        self.root.bind("<Escape>", lambda _e: self._hide_search())

    # ----- model / effort ---------------------------------------------------

    def _refresh_effort_options(self):
        """Show only the effort levels the selected model accepts, clamping if needed."""
        valid = ui_logic.EFFORT_BY_MODEL.get(self._model_id(), ui_logic.EFFORT_LEVELS)
        self.effort_combo.configure(values=[ui_logic.EFFORT_LABELS[e] for e in valid])
        if self._effort_id() not in valid:
            fallback = ui_logic.DEFAULT_EFFORT if ui_logic.DEFAULT_EFFORT in valid else valid[-1]
            self.effort_var.set(ui_logic.EFFORT_LABELS[fallback])

    def _model_id(self) -> str:
        return ui_logic.model_id(self.model_var.get())

    def _effort_id(self) -> str:
        return ui_logic.effort_id(self.effort_var.get())

    def _on_effort_change(self, _event=None):
        self._save_settings()
        self._refresh_preview()

    def _on_model_change(self, _event=None):
        self._refresh_effort_options()
        self._save_settings()
        self._refresh_preview()

    # ----- folder picking + preview -----------------------------------------

    @staticmethod
    def _show_path_end(entry) -> None:
        """Scroll a path field to its end: the folder name matters more than the drive."""
        entry.xview_moveto(1.0)

    def _pick_input(self):
        d = filedialog.askdirectory(
            title="Odaberite ulaznu mapu",
            initialdir=self.input_var.get() or str(Path.home()),
        )
        if d:
            self.input_var.set(d)
            self._show_path_end(self.ent_in)
            self._save_settings()
            self._refresh_preview()
            self._refresh_readiness()

    def _pick_output(self):
        d = filedialog.askdirectory(
            title="Odaberite izlaznu mapu",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if d:
            self.output_var.set(d)
            self._show_path_end(self.ent_out)
            self._save_settings()
            self._refresh_preview()
            self._refresh_output_buttons()
            self._refresh_readiness()

    def _refresh_preview(self):
        in_path = self.input_var.get()
        if not in_path:
            self.preview_var.set("")
            return
        p = Path(in_path)
        # os.path.isdir never raises; Path.is_dir re-raises PermissionError on Python <= 3.12
        # for a folder under one it cannot enter, and this runs from __init__.
        if not os.path.isdir(p):
            self.preview_var.set("Ulazna mapa ne postoji.")
            return
        try:
            files = [f for f in p.iterdir() if f.is_file()]
        except OSError as e:
            self.preview_var.set(f"Ne mogu pročitati ulaznu mapu: {e}")
            return
        count = sum(1 for f in files if is_supported_image(f))
        heic = sum(1 for f in files if is_heic(f))
        heic_note = f" Preskočeno HEIC/HEIF datoteka: {heic} (pretvorite ih u JPG)." if heic else ""
        if count == 0:
            sub = ui_logic.images_in_subfolders(p)
            hint = " " + ui_logic.SUBFOLDER_HINT.format(n=sub) if sub else ""
            self.preview_var.set("Nema podržanih slika (.jpg/.jpeg/.png/.webp)." + hint + heic_note)
            return
        cost, secs, measured = ui_logic.estimate(self._settings.get("stats"), self._model_id(),
                                                 self._effort_id(), count)
        basis = "prema prošlim obradama" if measured else "gruba procjena"
        self.preview_var.set(f"Pronađeno slika: {count}. Procjena: ~{self._fmt_duration(secs)}, "
                             f"~${cost:.2f} ({basis}).{heic_note}")

    def _out_path(self) -> Path:
        return Path(self.output_var.get())

    @staticmethod
    def _byhand_images(out_dir: Path) -> int:
        """Photos waiting in out_dir/byhand (0 when there is none or it can't be read)."""
        byhand = out_dir / "byhand"
        try:
            if not byhand.is_dir():
                return 0
            return sum(1 for f in byhand.iterdir() if f.is_file() and is_supported_image(f))
        except OSError:
            # An unreadable output folder must not take the whole app down: this runs
            # from __init__, so an uncaught OSError here means the window never opens.
            return 0

    def _refresh_retry_button(self):
        """Ground-truth check: enable the retry only if byhand/ actually has images."""
        out = self.output_var.get()
        has_images = bool(out) and self._byhand_images(Path(out)) > 0
        self.btn_retry_byhand.configure(state="normal" if has_images else "disabled")

    def _refresh_output_buttons(self):
        """Enable Otvori and the retry from what is on disk, never from what the last click did."""
        if self.proc is not None:
            return                      # a run is in progress; _set_running disabled them
        out = self.output_var.get()
        # os.path.isdir never raises; Path.is_dir re-raises PermissionError on Python <= 3.12
        # for a folder under one it cannot enter, and this runs from __init__.
        self.btn_open.configure(state="normal" if out and os.path.isdir(out) else "disabled")
        self._refresh_retry_button()

    def _refresh_open_menu(self):
        """Enable each Otvori entry from what is on disk when the menu opens."""
        out = self.output_var.get()
        base = Path(out) if out else None
        checks = ((0, lambda b: os.path.isfile(b / "output.csv")),
                  (1, lambda b: os.path.isdir(b / "byhand")),
                  (2, lambda b: os.path.isfile(b / "byhand_retry" / "output.csv")),
                  (4, os.path.isdir))
        for index, exists in checks:
            self.open_menu.entryconfigure(index, state="normal" if base and exists(base) else "disabled")

    # ----- start / stop / lifecycle -----------------------------------------

    def _checked_input(self) -> Path | None:
        """The input folder when it exists and holds photos; otherwise say why and return None."""
        in_path = self.input_var.get().strip()
        if not in_path:
            messagebox.showwarning("Nedostaje mapa", "Odaberite ulaznu mapu.")
            return None
        in_dir = Path(in_path)
        if not in_dir.is_dir():
            messagebox.showerror("Neispravna ulazna mapa", f"Ulazna mapa ne postoji:\n{in_dir}")
            return None
        try:
            image_count = sum(1 for f in in_dir.iterdir() if f.is_file() and is_supported_image(f))
        except OSError as e:
            messagebox.showerror("Neispravna ulazna mapa", f"Ne mogu pročitati ulaznu mapu:\n{e}")
            return None
        if image_count == 0:
            sub = ui_logic.images_in_subfolders(in_dir)
            reason = (ui_logic.SUBFOLDER_HINT.format(n=sub) if sub else
                      "Nema se što obraditi — HEIC/HEIF fotografije treba prvo pretvoriti u JPG.")
            messagebox.showwarning("Nema slika",
                                   f"{in_dir}\n\nnema podržanih slika (.jpg/.jpeg/.png/.webp).\n\n{reason}")
            return None
        return in_dir

    def _on_dry_run(self):
        """Probni prolaz: list the photos a run would send. Needs only the input folder."""
        in_dir = self._checked_input()
        if in_dir is None:
            return
        self._save_settings()
        self._launch_dry_run(in_dir)

    def _on_start(self):
        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()
        if not in_path or not out_path:
            messagebox.showwarning("Nedostaje mapa", "Odaberite ulaznu i izlaznu mapu.")
            return
        in_dir = self._checked_input()
        if in_dir is None:
            return
        self._save_settings()

        api_key = self._resolve_api_key()
        if not api_key:
            messagebox.showerror(
                "Nedostaje API ključ",
                "Upišite svoj Anthropic API ključ u polje „API ključ” iznad pa kliknite Spremi.\n\n"
                "Ključ možete dobiti na: console.anthropic.com",
            )
            return
        self._api_key = api_key
        out_dir = Path(out_path)
        if ui_logic.same_dir(in_dir, out_dir / "byhand"):
            messagebox.showerror(
                "Neispravna ulazna mapa",
                "Ulazna mapa ne smije biti byhand/ mapa ove izlazne mape.\nOdaberite drugu izlaznu mapu.",
            )
            return
        self._start_in(in_dir, out_dir, self._model_id(), self._effort_id())

    def _start_in(self, in_dir: Path, out_dir: Path, model: str, effort: str,
                  retry: bool = False) -> None:
        """Lock the output folder, settle fresh run vs resume, then launch the extractor."""
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Ne mogu stvoriti izlaznu mapu", str(e))
            return
        if not self._take_lock(out_dir):
            return
        try:
            mode = self._choose_output_mode(in_dir, out_dir)
            if (mode is None
                    or (not retry and not self._cost_confirmed(in_dir, out_dir, model, effort, mode))
                    or (mode == "fresh" and not self._backup_outputs(out_dir))
                    or not self._csv_writable(out_dir)):
                self._release_lock()
                return
            self._launch(in_dir, out_dir, model, effort, resume=(mode == "resume"), retry=retry)
        except BaseException:
            self._release_lock()
            raise

    def _cost_confirmed(self, in_dir: Path, out_dir: Path, model: str, effort: str, mode: str) -> bool:
        """Ask before a run whose estimate reaches COST_CONFIRM_USD; True to go ahead."""
        try:
            names = {f.name for f in in_dir.iterdir() if f.is_file() and is_supported_image(f)}
        except OSError:
            return True                 # the extractor reports an unreadable folder itself
        if mode == "resume":
            names -= read_processed(out_dir)
        count = len(names)
        cost, secs, measured = ui_logic.estimate(self._settings.get("stats"), model, effort, count)
        if count == 0 or cost < ui_logic.COST_CONFIRM_USD:
            return True
        basis = "prema prošlim obradama" if measured else "gruba procjena"
        return self._ask_choice(
            "Potvrda troška",
            f"Obraditi {count} {ui_logic.plural_hr(count, 'sliku', 'slike', 'slika')}?\n\n"
            f"Model: {ui_logic.MODEL_LABELS.get(model, model)}, "
            f"napor: {ui_logic.EFFORT_LABELS.get(effort, effort)}\n"
            f"Procjena: ~${cost:.2f}, ~{self._fmt_duration(secs)} ({basis}).",
            [("run", "Pokreni obradu"), ("cancel", "Odustani")], default="run",
        ) == "run"

    def _choose_output_mode(self, in_dir: Path, out_dir: Path) -> str | None:
        """'fresh', 'resume', or None when the user cancels, for a run into out_dir."""
        csv_path = out_dir / "output.csv"
        processed = read_processed(out_dir)
        if not csv_path.exists():
            if processed and self._ask_choice(
                "Nedostaje output.csv",
                f"output.csv nedostaje, ali .processed bilježi obrađene slike (ukupno: {len(processed)}).\n\n"
                "Ako krenete ispočetka, te će se slike ponovno poslati API-ju i ponovno platiti.",
                [("fresh", "Kreni ispočetka"), ("cancel", "Odustani")], default="cancel",
            ) != "fresh":
                return None
            return "fresh"
        try:
            names = {f.name for f in in_dir.iterdir() if f.is_file() and is_supported_image(f)}
        except OSError:
            names = set()
        done, total = len(names & processed), len(names)
        problem = resume_problem(out_dir)
        blocker = ui_logic.RESUME_BLOCKERS.get(problem) if problem else None
        if blocker is None and total and done == total:
            blocker = ui_logic.ALL_DONE
        answer = self._ask_existing_output(csv_path, ui_logic.csv_data_rows(csv_path), done, total, blocker)
        if answer == "open":
            self._open_csv(csv_path)
            return None
        return answer

    def _ask_existing_output(self, csv_path: Path, rows: int, done: int, total: int,
                             blocker: str | None) -> str | None:
        win, choice = self._build_existing_output_dialog(csv_path, rows, done, total, blocker)
        self._present(win)
        win.wait_visibility()
        win.grab_set()
        self.root.wait_window(win)
        return choice["value"]

    def _dialog(self, title: str) -> tk.Toplevel:
        """A themed dialog tied to the main window, hidden until _present places it."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title(title)
        win.configure(background=self._bg)
        win.transient(self.root)
        win.resizable(False, False)
        return win

    def _present(self, win: tk.Toplevel) -> None:
        """Show a dialog centred on the main window: Tk centres only its own message boxes, and a
        bare Toplevel opens wherever the window manager puts it (top-left on Windows)."""
        self.root.tk.call("tk::PlaceWindow", str(win), "widget", str(self.root))

    @staticmethod
    def _press_focused(win, buttons, fallback=None) -> None:
        """Return presses the focused button, as Space already does (ttk binds only Space)."""
        try:
            focused = win.focus_get()
        except KeyError:            # focus sits in a widget tkinter did not create
            focused = None
        target = focused if focused in buttons else fallback
        if target is not None and str(target.cget("state")) != "disabled":
            target.invoke()

    def _build_choice_dialog(self, title: str, text: str, choices: list[tuple[str, str]],
                             default: str):
        """A question answered by verb buttons; the first choice is the action."""
        win = self._dialog(title)
        choice = {"value": None}

        def pick(value):
            choice["value"] = value
            win.destroy()

        frm = ttk.Frame(win, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frm, text=text, justify="left", wraplength=520).grid(
            row=0, column=0, columnspan=len(choices), sticky="w")
        win.buttons = {}
        for col, (key, label) in enumerate(choices):
            button = ttk.Button(frm, text=label, command=lambda k=key: pick(k),
                                style="Accent.TButton" if col == 0 else "TButton")
            button.grid(row=1, column=col, padx=(0 if col == 0 else 6, 0), pady=(14, 0), sticky="w")
            win.buttons[key] = button
        win.default_button = win.buttons[default]
        win.default_button.focus_set()
        win.bind("<Return>", lambda _e: self._press_focused(win, list(win.buttons.values()),
                                                            win.default_button))
        win.bind("<Escape>", lambda _e: pick(None))
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        return win, choice

    def _ask_choice(self, title: str, text: str, choices: list[tuple[str, str]],
                    default: str) -> str | None:
        """Ask with verb buttons and wait: the chosen key, or None for Esc and the close box."""
        win, choice = self._build_choice_dialog(title, text, choices, default)
        self._present(win)
        win.wait_visibility()
        win.grab_set()
        self.root.wait_window(win)
        return choice["value"]

    def _build_existing_output_dialog(self, csv_path: Path, rows: int, done: int, total: int,
                                      blocker: str | None):
        win = self._dialog("output.csv već postoji")
        choice = {"value": None}

        def pick(value):
            choice["value"] = value
            win.destroy()

        all_done = blocker == ui_logic.ALL_DONE
        frm = ttk.Frame(win, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frm, justify="left", wraplength=520,
            text=f"{csv_path} već postoji (redaka: {rows}).\n"
                 f"Već obrađeno: {done}/{total} slika iz ulazne mape.",
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            frm, justify="left", foreground=THEME["MUTED"], wraplength=520,
            text="Nastavi — obradi samo preostale slike i dopiši ih.\n"
                 "Prepiši — premjesti output.csv, byhand/ i byhand_retry/ u kopije "
                 "(*.<vrijeme>.bak) i kreni ispočetka.",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))
        if blocker:
            ttk.Label(frm, text=f"Nastavak nije moguć: {blocker}.", foreground=THEME["ERR"],
                      wraplength=520, justify="left").grid(row=2, column=0, columnspan=4,
                                                           sticky="w", pady=(8, 0))
        win.btn_resume = ttk.Button(frm, text="Nastavi", style="Accent.TButton",
                                    command=lambda: pick("resume"),
                                    state="disabled" if blocker else "normal")
        win.btn_fresh = ttk.Button(frm, text="Prepiši", command=lambda: pick("fresh"))
        win.btn_cancel = ttk.Button(frm, text="Odustani", command=lambda: pick(None))
        win.btn_open = ttk.Button(frm, text="Otvori CSV", command=lambda: pick("open")) if all_done else None
        buttons = [b for b in (win.btn_resume, win.btn_fresh, win.btn_open, win.btn_cancel) if b is not None]
        for col, button in enumerate(buttons):
            button.grid(row=3, column=col, padx=(0 if col == 0 else 6, 0), pady=(14, 0))
        win.default_button = win.btn_cancel if all_done else win.btn_fresh if blocker else win.btn_resume
        win.default_button.focus_set()
        win.bind("<Return>", lambda _e: self._press_focused(win, buttons))
        win.bind("<Escape>", lambda _e: pick(None))
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        return win, choice

    def _backup_outputs(self, out_dir: Path) -> bool:
        """Move output.csv, byhand/ and byhand_retry/ aside before a fresh run: all of them or none."""
        try:
            back_up_outputs(out_dir)
        except OSError as e:
            messagebox.showerror(
                "Ne mogu spremiti kopiju",
                f"{e}\n\nZatvorite output.csv i slike iz byhand/ ako su negdje otvoreni "
                "(npr. u Excelu ili pregledniku slika) pa pokušajte ponovno.",
            )
            return False
        return True

    def _csv_writable(self, out_dir: Path) -> bool:
        """Excel on Windows locks output.csv while it is open: catch that before paying."""
        csv_path = out_dir / "output.csv"
        if not csv_path.exists():
            return True
        try:
            check_writable(csv_path)
        except OSError:
            messagebox.showerror("Datoteka je zaključana",
                                 f"Ne mogu pisati u {csv_path}.\n\nZatvorite je (npr. u Excelu) pa pokušajte ponovno.")
            return False
        return True

    def _take_lock(self, out_dir: Path) -> bool:
        lock = out_dir / ui_logic.LOCK_NAME
        if lock.exists() and self._ask_choice(
            "Mapa je zauzeta",
            f"{lock} postoji.\n\nMožda neka druga obrada upravo koristi ovu izlaznu mapu.",
            [("use", "Svejedno koristi mapu"), ("cancel", "Odustani")], default="cancel",
        ) != "use":
            return False
        token = ui_logic.new_lock_token()
        try:
            lock.write_text(token, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Ne mogu zaključati izlaznu mapu", str(e))
            return False
        self.lock_path, self.lock_token = lock, token
        return True

    def _launch(self, in_dir: Path, out_dir: Path, model: str, effort: str,
                resume: bool, retry: bool) -> None:
        self._reset_run_state()
        self._set_running(True)
        self._open_run_log(out_dir)
        cmd = [
            *_EXTRACTOR_CMD,
            "--input", str(in_dir),
            "--output", str(out_dir),
            "--verbose",
            "--model", model,
            "--effort", effort,
        ]
        if resume:
            cmd.append("--resume")
        self._is_retry_run = retry
        self._run_out_dir = out_dir
        self._run_model, self._run_effort = model, effort
        self._launched_dry_run = False
        self._launch_subprocess(cmd)

    def _launch_dry_run(self, in_dir: Path) -> None:
        self._reset_run_state()
        self._set_running(True)
        self._is_retry_run = False
        self._run_out_dir = None
        self._launched_dry_run = True
        self._launch_subprocess([*_EXTRACTOR_CMD, "--input", str(in_dir), "--dry-run"])

    def _resolve_api_key(self) -> str:
        key = self.api_key_var.get().strip()
        if not key:
            # An exported variable beats .env, as it does for the command-line extractor. .env is
            # read without loading it into os.environ, which every child process inherits.
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            try:
                key = (dotenv_values(PROJECT_DIR / ".env").get("ANTHROPIC_API_KEY") or "").strip()
            except (OSError, ValueError):       # an unreadable .env holds no usable key
                key = ""
        return key

    def _key_available(self) -> bool:
        return bool(self.api_key_var.get().strip()) or self._key_source() is not None

    def _key_source(self) -> str | None:
        """Where a run's key comes from when the field is empty: "env", ".env" or None."""
        if os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return "env"
        try:
            if (dotenv_values(PROJECT_DIR / ".env").get("ANTHROPIC_API_KEY") or "").strip():
                return ".env"
        except (OSError, ValueError):       # an unreadable .env holds no usable key
            pass
        return None

    def _refresh_key_hint(self) -> None:
        source = None if self.api_key_var.get().strip() else self._key_source()
        text = {"env": "Koristi se ključ iz varijable okruženja ANTHROPIC_API_KEY.",
                ".env": "Koristi se ključ iz datoteke .env."}.get(source, "")
        self.key_hint_var.set(text)
        if text:
            self._key_hint.grid()
        else:
            self._key_hint.grid_remove()

    def _on_key_edit(self, *_):
        self._refresh_key_hint()
        self._refresh_readiness()

    def _toggle_key_visibility(self):
        self.ent_key.configure(show="" if self.show_key_var.get() else "•")

    def _refresh_readiness(self) -> str | None:
        """While no run is in progress, the status line names the next setup step."""
        if self.proc is not None:
            return None
        if not self.input_var.get():
            text = "Odaberite ulaznu mapu sa slikama."
        elif not self.output_var.get():
            text = "Odaberite izlaznu mapu."
        elif not self._key_available():
            text = "Upišite API ključ i kliknite Spremi ključ."
        else:
            text = "Spremno."
        self.status_var.set(text)
        return text

    def _launch_subprocess(self, cmd: list[str]):
        # The key goes only into the extractor's environment -- putting it in os.environ
        # would leak it to every other child too (xdg-open, notify-send, open, startfile).
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        if self._api_key:
            env["ANTHROPIC_API_KEY"] = self._api_key

        if sys.platform == "win32":
            popen_extra = {"creationflags": subprocess.CREATE_NO_WINDOW}
        else:
            popen_extra = {"start_new_session": True}

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(PROJECT_DIR),
                env=env,
                **popen_extra,
            )
        except OSError as e:
            self._append_log(f"Ne mogu pokrenuti obradu: {e}\n", "stderr")
            self._set_running(False)
            self._refresh_output_buttons()      # _set_running(False) leaves Open and Retry off
            self.status_var.set("Pokretanje nije uspjelo.")
            self._release_lock()
            self._close_run_log()
            return

        if sys.platform != "win32":
            try:
                self.pgid = os.getpgid(self.proc.pid)
            except (OSError, ProcessLookupError):
                self.pgid = self.proc.pid
        else:
            self.pgid = self.proc.pid

        self.progress.configure(mode="indeterminate", maximum=100)
        self.progress.start(80)
        self._append_log(f"$ {' '.join(cmd)}\n", "raw", "info")

        t_out = threading.Thread(target=self._reader, args=(self.proc.stdout, "stdout"), daemon=True)
        t_err = threading.Thread(target=self._reader, args=(self.proc.stderr, "stderr"), daemon=True)
        t_out.start()
        t_err.start()
        threading.Thread(
            target=self._waiter, args=(self.proc, t_out, t_err), daemon=True
        ).start()

    def _on_stop(self):
        if not self.proc or self._stop_requested:
            return
        self._append_log("[zaustavljanje…]\n", "info")
        self._stop_requested = True
        self.btn_stop.configure(state="disabled")
        threading.Thread(target=self._terminate_run, daemon=True).start()

    def _terminate_run(self):
        proc = self.proc
        pgid = self.pgid
        if not proc or pgid is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            # taskkill /T ends the extractor and anything it started. The one-file exe runs it as a
            # worker of its own unpacked folder (PyInstaller 6.9+, pinned in build.bat), so there is
            # no second bootloader or temp folder to clean up.
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                pass
            for kill_fn, wait_ticks in ((proc.terminate, 30), (proc.kill, 0)):
                if proc.poll() is not None:
                    return
                try:
                    kill_fn()
                except (OSError, ProcessLookupError):
                    return
                for _ in range(wait_ticks):
                    if proc.poll() is not None:
                        return
                    time.sleep(0.1)
        else:
            for sig, wait_ticks in ((signal.SIGINT, 30), (signal.SIGTERM, 20), (signal.SIGKILL, 0)):
                try:
                    os.killpg(pgid, sig)
                except (OSError, ProcessLookupError):
                    return
                for _ in range(wait_ticks):
                    if proc.poll() is not None:
                        return
                    time.sleep(0.1)

    # ----- subprocess reader / waiter ---------------------------------------

    def _reader(self, stream, kind: str):
        try:
            for line in iter(stream.readline, ""):
                self.log_queue.put((kind, line))
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _waiter(self, proc, t_out: threading.Thread, t_err: threading.Thread):
        rc = proc.wait()
        # ensure all output lines have been queued before we report exit
        t_out.join()
        t_err.join()
        self.log_queue.put(("__exit__", rc))

    # ----- queue drain + line handling --------------------------------------

    def _drain_queue(self):
        try:
            for _ in range(DRAIN_CAP_PER_TICK):
                kind, payload = self.log_queue.get_nowait()
                if kind == "__exit__":
                    self._on_proc_exit(payload)
                    continue
                self._handle_line(kind, payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(50, self._drain_queue)

    def _handle_line(self, kind: str, line: str):
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…[truncated]\n"

        raw_tag = "stderr" if kind == "stderr" else None
        if kind == "stdout":
            event = ui_logic.parse_progress(line)
            if event and event[0] == "start":
                _, k, total, name = event
                self._run_files[k] = name
                if self.run_start_time is None:
                    self.run_start_time = time.monotonic()
                self._update_progress(k - 1, total, current=name)
            elif event:
                _, k, total, verdict, total_cost = event
                self._last_result_time = time.monotonic()
                if total_cost is not None:
                    self.total_cost = total_cost
                self.counters[{"OK": "ok", "PARTIAL": "partial"}.get(verdict, "failed")] += 1
                if verdict != "OK":
                    self._flagged.add(self._run_files.get(k, ""))
                self._done_count = k
                self._update_progress(k, total)
            if ui_logic.DONE_RE.match(line):
                self._saw_done_line = True
                raw_tag = "done"
        elif line.strip():
            self._last_stderr = line.strip()

        prefix = "[stderr] " if kind == "stderr" else ""
        self._log_line(prefix + line, raw_tag, ui_logic.describe_line(kind, line, self._run_files))

    def _log_line(self, raw: str, raw_tag: str | None, nice) -> None:
        """Log one extractor line: raw for the technical view, in Croatian for the default one."""
        if nice is None:                      # nothing to translate: the same line in both views
            self._append_log(raw, raw_tag)
            return
        text, tag = nice
        self._append_log(raw, "raw", raw_tag)
        if text:
            self._append_log(text + "\n", "nice", tag)

    def _toggle_raw_log(self):
        """Tehnički zapis: the extractor's own lines instead of the Croatian ones."""
        raw = self.raw_log_var.get()
        self.log.tag_config("raw", elide=not raw)
        self.log.tag_config("nice", elide=raw)
        self.log.see("end")

    def _last_error_line(self) -> str:
        return self._last_stderr or "Pojedinosti su u zapisniku ispod."

    def _record_stats(self):
        """Teach the estimate: add this run's cost and time to its model/effort sums."""
        n = sum(self.counters.values())
        if n <= 0 or self.run_start_time is None or self._last_result_time is None:
            return
        # Timed to the last result line, not to the process exit: a Stop in the middle of a
        # photo, or the wait for the process to end, is not photo time.
        self._settings["stats"] = ui_logic.record_run(
            self._settings.get("stats"), self._run_model, self._run_effort,
            self.total_cost, self._last_result_time - self.run_start_time, n,
        )
        self._save_settings()
        self._refresh_preview()

    def _report_callback_exception(self, exc_type, exc, tb):
        """Show errors from Tk callbacks: the windowed exe has no console to print them to."""
        try:
            self._append_log("".join(traceback.format_exception(exc_type, exc, tb)), "stderr")
            messagebox.showerror("Neočekivana greška",
                                 f"{exc_type.__name__}: {exc}\n\nPojedinosti su u zapisniku.")
        except tk.TclError:
            pass

    def _update_progress(self, done: int, total: int, current: str | None = None):
        if self.last_total != total:
            self.last_total = total
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(total, 1))
        self.progress.configure(value=done)
        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        parts = [f"{done}/{total}", f"OK: {ok} · za pregled: {partial} · neuspjelo: {failed}",
                 f"potrošeno: ${self.total_cost:.2f}"]
        if self.run_start_time and 0 < done < total:
            elapsed = time.monotonic() - self.run_start_time
            parts.append(f"preostalo ~{self._fmt_duration((total - done) * elapsed / done)}")
        status = " — ".join(parts)
        self.status_var.set(f"Obrađujem {current} · {status}" if current else status)

    @staticmethod
    def _fmt_duration(secs: float) -> str:
        secs = int(secs)
        if secs < 60:
            return f"{secs}s"
        m, s = divmod(secs, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"

    # ----- log widget -------------------------------------------------------

    def _append_log(self, text: str, *tags: str | None):
        at_bottom = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        self.log.insert("end", text, tuple(tag for tag in tags if tag))
        if "nice" not in tags:              # the file keeps the raw lines, not their Croatian copies
            self._write_run_log(text)
        self.line_count += text.count("\n")
        if self.line_count > LOG_LINE_CAP + LOG_TRIM_BATCH:
            trim_to = self.line_count - LOG_LINE_CAP
            self.log.delete("1.0", f"{trim_to + 1}.0")
            self.line_count -= trim_to
        self.log.configure(state="disabled")
        if at_bottom:
            self.log.see("end")

    # ----- exit handling ----------------------------------------------------

    def _on_proc_exit(self, rc: int):
        self.progress.stop()
        outcome = ui_logic.classify_exit(rc, self._stop_requested, self._saw_done_line,
                                         self._launched_dry_run)
        self._log_exit(outcome, rc)
        self._close_run_log()
        total = self.last_total or 0
        # Real progress: a stopped or failed run must not look finished.
        self.progress.configure(mode="determinate", maximum=max(total, 1),
                                value=total if outcome == "done" else self._done_count)

        is_retry = self._is_retry_run
        is_dry = self._launched_dry_run
        out_dir = self._run_out_dir or Path(self.output_var.get())
        self.proc = None
        self.pgid = None
        self._is_retry_run = False
        self._run_out_dir = None
        self._release_lock()
        if not self._closing:
            # A closing window keeps every control off until it is gone: a click in its last
            # 100 ms (Pokreni, or the retry once the results are on disk) could launch a run.
            self._set_running(False)
            # The results exist whatever the exit code was; never leave them behind dead buttons.
            self._refresh_output_buttons()
        # Only a finished or a stopped run teaches the estimate: a failed or killed one ends on
        # errors (an api-down run, on three retried unbilled photos) that skew both averages.
        if not is_dry and outcome in ("done", "stopped"):
            self._record_stats()
        if self._closing:
            return                      # the window is closing: no attention, no dialogs
        if not is_dry:
            self._draw_attention()

        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        saved = ok + partial + failed

        again = RETRY_LABEL if is_retry else "Pokreni"
        where = "byhand_retry/output.csv" if is_retry else "output.csv"
        fatal = ui_logic.FATAL_TAG_RE.match(self._last_stderr)
        tag = fatal.group(1) if fatal else None
        # A dry run saved nothing to resume, and Nastavi cannot cure these errors.
        resumable = not is_dry and tag not in ("resume-refused", "input-is-byhand", "no-images", "output-exists")
        # Carries its own blank line, so a dialog without it does not end on one.
        resume_hint = (f"\n\nZa nastavak kliknite {again} i odaberite Nastavi — već obrađene slike "
                       "neće se ponovno slati (ni plaćati).") if resumable else ""
        if outcome == "stopped" and is_dry:
            self.status_var.set("Probni prolaz zaustavljen.")
        elif outcome == "stopped":
            self.status_var.set(f"Zaustavljeno — obrađeno slika: {saved}.")
            messagebox.showinfo("Zaustavljeno",
                                f"Zaustavljeno. Obrađeno slika: {saved}; spremljeno u {where}.{resume_hint}")
        elif outcome == "done":
            if is_dry:
                self.status_var.set("Probni prolaz završen.")
            else:
                self.status_var.set(
                    f"Gotovo — OK: {ok} · za pregled: {partial} · neuspjelo: {failed} · ${self.total_cost:.2f}"
                )
                self._notify_done()
                self._show_summary_popup(
                    out_dir / "output.csv",
                    title="Sažetak ponovne obrade" if is_retry else "Sažetak obrade",
                    retry=is_retry,
                )
        elif outcome == "interrupted":
            self.status_var.set(f"Prekinuto (izlazni kod {rc}).")
            messagebox.showerror(
                "Obrada prekinuta",
                f"Obrada je neočekivano prekinuta (izlazni kod {rc}).\n\n"
                f"{self._last_error_line()}{resume_hint}",
            )
        else:
            self.status_var.set(f"Neuspjelo (izlazni kod {rc}).")
            body = ui_logic.failure_text(self._last_stderr, again, resumable)
            if body is None:
                body = (f"Obrada je završila s izlaznim kodom {rc}.\n\n"
                        f"{self._last_error_line()}{resume_hint}")
            messagebox.showerror("Obrada nije uspjela", body)

    def _open_run_log(self, out_dir: Path) -> None:
        """Keep this run's raw log in <izlazna mapa>/logs/, so it outlives the window."""
        self._log_file = None
        try:
            folder = out_dir / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"tron-grave-{time.strftime('%Y%m%d-%H%M%S')}.log"
            self._log_file = open(path, "a", encoding="utf-8", buffering=1)
        except OSError as e:
            self._append_log(f"Zapisnik se ne može spremiti: {e}\n", "warn")

    def _write_run_log(self, text: str) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.write(text)
        except (OSError, ValueError) as e:      # ValueError: the file was closed under us
            self._close_run_log()
            self._append_log(f"Zapisnik se ne može spremiti: {e}\n", "warn")

    def _close_run_log(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def _log_exit(self, outcome: str, rc: int) -> None:
        """How the run ended: the exit code for the technical view, a sentence for the default one."""
        if outcome == "done":
            self._append_log(f"\n[izlazni kod {rc}]\n", "raw", "info")
        elif outcome == "interrupted":
            self._append_log(f"\n[prekinuto, izlazni kod {rc}]\n", "raw", "stderr")
            self._append_log("Obrada je prekinuta.\n", "nice", "error")
        elif outcome == "failed":
            self._append_log(f"\n[neuspjelo, izlazni kod {rc}]\n", "raw", "stderr")
            self._append_log("Obrada nije uspjela.\n", "nice", "error")

    def _show_summary_popup(self, csv_path: Path, title: str = "Sažetak obrade", retry: bool = False):
        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        total = ok + partial + failed
        reasons = ui_logic.tally_review_notes(csv_path, self._flagged)
        to_review = self._byhand_images(csv_path.parent) if partial + failed else 0

        win = self._dialog(title)
        frm = ttk.Frame(win, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frm, text=title, font=self._fonts["title"]).grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Label(frm, text=f"Ukupno: {total}   ·   OK: {ok}   ·   Za pregled: {partial}   ·   "
                            f"Neuspjelo: {failed}").grid(row=1, column=0, sticky="w")
        cost_line = f"Ukupni trošak: ${self.total_cost:.2f}"
        if self.run_start_time is not None and self._last_result_time is not None:
            cost_line += f"   ·   Trajanje: {self._fmt_duration(self._last_result_time - self.run_start_time)}"
        ttk.Label(frm, text=cost_line).grid(row=2, column=0, sticky="w", pady=(2, 10))

        row = 3
        if reasons:
            ttk.Label(frm, text="Najčešći razlozi za pregled:", font=self._fonts["strong"]).grid(
                row=row, column=0, sticky="w")
            row += 1
            for reason, count in reasons:
                ttk.Label(frm, text=f"  {count}×  {reason}", foreground=THEME["MUTED"]).grid(
                    row=row, column=0, sticky="w")
                row += 1

        offer_retry = to_review > 0 and not retry
        if offer_retry:
            model, effort = ui_logic.retry_settings(self._model_id(), self._effort_id())
            cost, _secs, _measured = ui_logic.estimate(self._settings.get("stats"), model, effort, to_review)
            ttk.Label(frm, foreground=THEME["MUTED"], wraplength=self._px(460), justify="left",
                      text=f"Ponovna obrada jačim modelom ({ui_logic.MODEL_LABELS.get(model, model)}, "
                           f"{ui_logic.EFFORT_LABELS.get(effort, effort)}): ~${cost:.2f} za {to_review} "
                           f"{ui_logic.plural_hr(to_review, 'sliku', 'slike', 'slika')}.").grid(
                row=row, column=0, sticky="w", pady=(10, 0))
            row += 1

        def retry_now():
            win.destroy()
            self._on_retry_byhand()

        buttons = ttk.Frame(frm)
        buttons.grid(row=row, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(buttons, text="Otvori CSV", command=lambda: self._open_csv(csv_path)).pack(side="left")
        if to_review:
            ttk.Button(buttons, text="Otvori slike za pregled",
                       command=lambda: self._open_path(csv_path.parent / "byhand")).pack(side="left", padx=(6, 0))
        if offer_retry:
            ttk.Button(buttons, text=RETRY_LABEL, command=retry_now).pack(side="left", padx=(6, 0))
        close_btn = ttk.Button(buttons, text="Zatvori", command=win.destroy)
        close_btn.pack(side="right", padx=(12, 0))
        ttk.Label(frm, text=EXCEL_HINT, foreground=THEME["MUTED"], font=self._fonts["small"],
                  wraplength=self._px(460), justify="left").grid(row=row + 1, column=0, sticky="w", pady=(12, 0))

        win.bind("<Escape>", lambda _e: win.destroy())
        win.bind("<Return>", lambda _e: win.destroy())
        close_btn.focus_set()
        self._present(win)

        def grab():
            try:
                win.grab_set()
            except tk.TclError:
                pass   # not mapped yet, or already closed: the popup works without a grab
        # On the root, not the popup: a timer the popup owns dies with it (Esc within 100 ms),
        # and Tk would then open its own error window for the deleted command.
        self.root.after(100, grab)
        return win

    def _on_retry_byhand(self):
        out_dir = Path(self.output_var.get())
        byhand_dir = out_dir / "byhand"
        n = self._byhand_images(out_dir)
        if n == 0:
            messagebox.showinfo("Nema slika", "Nema slika u byhand/ za ponovnu obradu.")
            return

        model, effort = ui_logic.retry_settings(self._model_id(), self._effort_id())
        cost, _secs, _measured = ui_logic.estimate(self._settings.get("stats"), model, effort, n)
        retry_out = out_dir / "byhand_retry"
        if self._ask_choice(
            "Ponovna obrada",
            f"Ponovno obraditi slike iz byhand/ (ukupno: {n})?\n\n"
            f"Model: {ui_logic.MODEL_LABELS.get(model, model)}, "
            f"napor: {ui_logic.EFFORT_LABELS.get(effort, effort)}\n"
            f"Procjena: ~${cost:.2f}\n\n"
            "Ovo su novi, plaćeni API pozivi. Rezultati idu u zasebnu mapu:\n"
            f"{retry_out}",
            [("run", "Pokreni ponovnu obradu"), ("cancel", "Odustani")], default="run",
        ) != "run":
            return

        api_key = self._resolve_api_key()
        if not api_key:
            messagebox.showerror(
                "Nedostaje API ključ",
                "Upišite svoj Anthropic API ključ u polje „API ključ” iznad pa kliknite Spremi.",
            )
            return
        self._api_key = api_key
        self._start_in(byhand_dir, retry_out, model, effort, retry=True)

    def _on_close(self):
        if self._closing:
            return
        if self.proc:
            running = self.proc.poll() is None
            if running:
                done = self.counters["ok"] + self.counters["partial"] + self.counters["failed"]
                if self._ask_choice(
                    "Obrada u tijeku",
                    f"Obrada je u tijeku (gotovo: {done}).\n"
                    "Zatvaranjem se obrada zaustavlja; već obrađene slike ostaju spremljene.",
                    [("stop", "Zaustavi i zatvori"), ("cancel", "Nastavi obradu")], default="cancel",
                ) != "stop":
                    return
            # Close only once the run's exit is handled (its stats saved), even if the extractor
            # has already ended and only that is left. A running one is stopped on a worker thread
            # meanwhile: stopping can take seconds (up to ~18 s on Windows), and a frozen window
            # looks like a crash.
            self._closing = True
            self.btn_stop.configure(state="disabled")
            if running:
                self.status_var.set("Zaustavljam obradu…")
                if not self._stop_requested:    # Stop may be stopping it already: never a second SIGINT
                    self._stop_requested = True
                    threading.Thread(target=self._terminate_run, daemon=True).start()
            self._close_when_stopped(time.monotonic() + 20)
            return
        self._release_lock()
        self.root.destroy()

    def _close_when_stopped(self, deadline: float):
        # _on_proc_exit clears self.proc once the extractor's last line is handled (stats saved).
        if self.proc is not None:
            if time.monotonic() <= deadline:
                self.root.after(100, lambda: self._close_when_stopped(deadline))
                return
            self._atexit_kill()     # still there at the deadline: kill it before letting go of the lock
        self._release_lock()
        self.root.destroy()

    def _atexit_kill(self):
        if self.proc and self.proc.poll() is None:
            try:
                if sys.platform == "win32":
                    self.proc.kill()
                elif self.pgid is not None:
                    os.killpg(self.pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        self._release_lock()

    def _release_lock(self):
        if self.lock_path and self.lock_token:
            ui_logic.release_lock(self.lock_path, self.lock_token)
        self.lock_path = self.lock_token = None

    # ----- run state --------------------------------------------------------

    def _set_running(self, running: bool):
        if running:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.btn_in.configure(state="disabled")
            self.btn_out.configure(state="disabled")
            self.btn_open.configure(state="disabled")
            self.btn_retry_byhand.configure(state="disabled")
            self.model_combo.configure(state="disabled")
            self.effort_combo.configure(state="disabled")
            self.btn_dry.configure(state="disabled")
            self.status_var.set("Pokrećem…")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_in.configure(state="normal")
            self.btn_out.configure(state="normal")
            self.model_combo.configure(state="readonly")
            self.effort_combo.configure(state="readonly")
            self.btn_dry.configure(state="normal")

    def _reset_run_state(self):
        self.counters = {"ok": 0, "partial": 0, "failed": 0}
        self.total_cost = 0.0
        self.last_total = None
        self.run_start_time = None
        self._last_result_time = None
        self._stop_requested = False
        self._saw_done_line = False
        self._last_stderr = ""
        self._run_files = {}
        self._flagged = set()
        self._done_count = 0
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.line_count = 0
        self._search_index = None

    # ----- helpers ----------------------------------------------------------

    def _open_path(self, p: Path) -> bool:
        """Open a file or folder with the system's own app; False when it could not be opened."""
        if not p.exists():
            messagebox.showinfo("Nije pronađeno", f"{p} ne postoji.")
            return False
        try:
            if sys.platform.startswith("linux"):
                subprocess.Popen(
                    ["xdg-open", str(p)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            elif sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
        except OSError as e:
            messagebox.showerror("Ne mogu otvoriti", str(e))
            return False
        return True

    def _open_csv(self, path: Path) -> None:
        """Open a CSV, and say how to import it into Excel set to Croatian (it expects ;)."""
        if self._open_path(path):
            self.status_var.set(EXCEL_HINT)

    def _notify_done(self):
        if not sys.platform.startswith("linux"):
            return
        try:
            subprocess.Popen(
                [
                    "notify-send", "TRON-GRAVE",
                    f"Gotovo — OK: {self.counters['ok']}, "
                    f"za pregled: {self.counters['partial']}, "
                    f"neuspjelo: {self.counters['failed']}",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    def _draw_attention(self):
        """Bring the window back when a run ends, even if it was minimized, and flash its taskbar button."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.bell()
        except tk.TclError:
            pass
        _flash_taskbar(self.root)

    # ----- search bar -------------------------------------------------------

    def _show_search(self):
        self.search_frame.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._search_entry.focus_set()
        self._search_entry.select_range(0, "end")

    def _hide_search(self):
        self.search_frame.grid_forget()
        self.log.tag_remove("search", "1.0", "end")
        self._search_index = None
        self.search_info_var.set("")

    def _search_matches(self, q: str) -> list[str]:
        """Where q starts in the visible log, top to bottom (Tk skips the hidden view's lines)."""
        found, start = [], "1.0"
        while True:
            idx = self.log.search(q, start, nocase=True, stopindex="end")
            if not idx:
                return found
            found.append(idx)
            start = f"{idx}+{len(q)}c"

    def _search_step(self, backwards: bool) -> None:
        q = self.search_var.get()
        self.log.tag_remove("search", "1.0", "end")
        matches = self._search_matches(q) if q else []
        if not matches:
            self.search_info_var.set("Nema rezultata" if q else "")
            return
        cur = self._search_index
        if backwards:
            earlier = [i for i in matches if cur is None or self.log.compare(i, "<", cur)]
            idx = earlier[-1] if earlier else matches[-1]
        else:
            later = [i for i in matches if cur is None or self.log.compare(i, ">", cur)]
            idx = later[0] if later else matches[0]
        self.log.tag_add("search", idx, f"{idx}+{len(q)}c")
        self.log.see(idx)
        self._search_index = idx
        self.search_info_var.set(f"{matches.index(idx) + 1}/{len(matches)}")

    def _search_next(self):
        self._search_step(backwards=False)

    def _search_prev(self):
        self._search_step(backwards=True)

    # ----- settings ---------------------------------------------------------

    def _load_settings(self):
        data = ui_logic.load_settings(SETTINGS_PATH)
        self._settings = data
        if isinstance(data.get("input"), str):
            self.input_var.set(data["input"])
        if isinstance(data.get("output"), str):
            self.output_var.set(data["output"])
        if isinstance(data.get("api_key"), str):
            self.api_key_var.set(data["api_key"])
        # Ignore a model that is no longer offered -- a saved setting naming a
        # retired model would otherwise stick in the readonly combobox and get used.
        if data.get("model") in ui_logic.MODELS:
            self.model_var.set(ui_logic.MODEL_LABELS[data["model"]])
        if data.get("effort") in ui_logic.EFFORT_LEVELS:
            self.effort_var.set(ui_logic.EFFORT_LABELS[data["effort"]])

    def _save_settings(self, include_key: bool = False) -> bool:
        """Save folders, model and effort; the API key only when the user clicked Spremi."""
        data = dict(self._settings)
        data.update(input=self.input_var.get(), output=self.output_var.get(),
                    model=self._model_id(), effort=self._effort_id())
        if include_key:
            data["api_key"] = self.api_key_var.get().strip()
        try:
            ui_logic.write_settings(SETTINGS_PATH, data)
        except OSError:
            return False
        self._settings = data
        return True

    def _on_save_key(self):
        if self._save_settings(include_key=True):
            self.status_var.set("API ključ spremljen." if self.api_key_var.get().strip()
                                else "API ključ uklonjen iz postavki.")
        else:
            messagebox.showerror("Ne mogu spremiti postavke", f"Ne mogu pisati u {SETTINGS_PATH}.")


def _enable_dpi_awareness():
    """Render crisply on scaled Windows displays instead of being bitmap-stretched."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # system DPI aware (8.1+)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _flash_taskbar(root: tk.Tk) -> None:
    """Flash the taskbar button until the window gets focus (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                        ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                        ("dwTimeout", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32")
        user32.GetParent.restype = wintypes.HWND
        user32.GetParent.argtypes = [wintypes.HWND]
        hwnd = user32.GetParent(root.winfo_id())                 # Tk's frame window
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)  # FLASHW_ALL|TIMERNOFG
        user32.FlashWindowEx(ctypes.byref(info))
    except (AttributeError, OSError, tk.TclError):
        pass


def main():
    if sys.platform == "win32":
        _enable_dpi_awareness()     # must happen before Tk creates its first window
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
