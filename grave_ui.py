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
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dotenv import dotenv_values

import ui_logic
from extractor.csv_writer import check_writable, read_processed, resume_problem
from extractor.file_utils import is_heic, is_supported_image
from _version import __version__


if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle — exe lives next to all bundled files
    PROJECT_DIR = Path(sys.executable).parent
    _EXTRACTOR_CMD = [sys.executable, "--_run-extractor"]
else:
    PROJECT_DIR = Path(__file__).resolve().parent
    _EXTRACTOR_CMD = [sys.executable, "-u", str(Path(__file__).resolve().parent / "grave_extractor.py")]
SETTINGS_PATH = ui_logic.settings_path()

LOG_LINE_CAP = 5000
LOG_TRIM_BATCH = 500
DRAIN_CAP_PER_TICK = 200
MAX_LINE_CHARS = 4096


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"TRON-GRAVE {__version__}")
        # Wide enough for the full control row: Start/Stop, the dry-run checkbox, and the three
        # Open/Retry buttons. At 960 the last button was clipped off-screen.
        self.root.geometry("1180x700")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.model_var = tk.StringVar(value=ui_logic.MODEL_LABELS[ui_logic.DEFAULT_MODEL])
        self.effort_var = tk.StringVar(value=ui_logic.EFFORT_LABELS[ui_logic.DEFAULT_EFFORT])
        self.dry_run_var = tk.BooleanVar(value=False)
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
        self._search_index = "1.0"
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

        self._load_settings()
        self._apply_theme()
        self._build_ui()
        self.root.report_callback_exception = self._report_callback_exception
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if sys.platform == "darwin":
            # Cmd+Q bypasses WM_DELETE_WINDOW on macOS; route it through the same check.
            self.root.createcommand("tk::mac::Quit", self._on_close)
        atexit.register(self._atexit_kill)
        self.root.after(50, self._drain_queue)
        self._refresh_preview()
        self._refresh_output_buttons()

    # ----- UI construction --------------------------------------------------

    def _apply_theme(self):
        """Hand-crafted flat dark theme on the ttk 'clam' base — no extra dependencies."""
        BG = "#16181d"          # window background
        SURFACE = "#1e2127"     # popups / dropdown list
        INPUT = "#2a2e37"       # entries, buttons, troughs
        TEXT = "#e6e6e6"
        MUTED = "#9aa0a6"
        BORDER = "#333945"
        ACCENT = "#5b8def"
        ACCENT_HOVER = "#6f9bf2"
        ACCENT_DOWN = "#4a7ce0"
        HOVER = "#343a45"
        DISABLED_BG = "#23262d"
        DISABLED_FG = "#5b606b"

        self._bg = BG
        self.root.configure(background=BG)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = ("Segoe UI", 10)
        style.configure(".", background=BG, foreground=TEXT, font=base_font,
                        fieldbackground=INPUT, bordercolor=BORDER,
                        lightcolor=BG, darkcolor=BG)

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=base_font)

        style.configure("TButton", background=INPUT, foreground=TEXT,
                        borderwidth=0, padding=(14, 8), font=base_font)
        style.map("TButton",
                  background=[("disabled", DISABLED_BG), ("pressed", BORDER), ("active", HOVER)],
                  foreground=[("disabled", DISABLED_FG)])

        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        borderwidth=0, padding=(16, 8), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton",
                  background=[("disabled", DISABLED_BG), ("pressed", ACCENT_DOWN), ("active", ACCENT_HOVER)],
                  foreground=[("disabled", DISABLED_FG)])

        style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT,
                        bordercolor=BORDER, insertcolor=TEXT, padding=6)
        style.map("TEntry",
                  fieldbackground=[("readonly", INPUT)],
                  foreground=[("readonly", TEXT)],
                  bordercolor=[("focus", ACCENT)])

        style.configure("TCombobox", fieldbackground=INPUT, background=INPUT,
                        foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER, padding=5)
        style.map("TCombobox",
                  fieldbackground=[("disabled", DISABLED_BG), ("readonly", INPUT)],
                  foreground=[("disabled", DISABLED_FG), ("readonly", TEXT)],
                  background=[("disabled", DISABLED_BG), ("pressed", HOVER), ("active", HOVER)],
                  bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("disabled", DISABLED_FG)])
        self.root.option_add("*TCombobox*Listbox.background", SURFACE)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", DISABLED_FG)])

        style.configure("TProgressbar", troughcolor=INPUT, background=ACCENT,
                        bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)

        style.configure("TScrollbar", troughcolor=BG, background=INPUT,
                        bordercolor=BG, arrowcolor=MUTED)
        style.map("TScrollbar", background=[("active", BORDER)])

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(4, weight=1)

        header = ttk.Frame(self.root)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ttk.Label(header, text="TRON-GRAVE", font=("Segoe UI Semibold", 17)).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header, text="Izdvajanje podataka s nadgrobnih spomenika",
            font=("Segoe UI", 9), foreground="#9aa0a6",
        ).grid(row=1, column=0, sticky="w")

        top = ttk.Frame(self.root)
        top.grid(row=1, column=0, sticky="ew", padx=14, pady=6)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Ulazna mapa").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(top, textvariable=self.input_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=6, pady=6
        )
        self.btn_in = ttk.Button(top, text="Odaberi…", command=self._pick_input)
        self.btn_in.grid(row=0, column=2, padx=6, pady=6)

        ttk.Label(top, text="Izlazna mapa").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(top, textvariable=self.output_var, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=6, pady=6
        )
        self.btn_out = ttk.Button(top, text="Odaberi…", command=self._pick_output)
        self.btn_out.grid(row=1, column=2, padx=6, pady=6)

        ttk.Label(top, text="API ključ").grid(row=2, column=0, sticky="w", padx=6, pady=6)
        ttk.Entry(top, textvariable=self.api_key_var, show="•").grid(
            row=2, column=1, sticky="ew", padx=6, pady=6
        )
        ttk.Button(top, text="Spremi", command=self._on_save_key).grid(row=2, column=2, padx=6, pady=6)

        ttk.Label(top, text="Model").grid(row=3, column=0, sticky="w", padx=6, pady=6)
        self.model_combo = ttk.Combobox(
            top, textvariable=self.model_var, state="readonly", width=30,
            values=[ui_logic.MODEL_LABELS[m] for m in ui_logic.MODELS],
        )
        self.model_combo.grid(row=3, column=1, sticky="w", padx=6, pady=6)
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_change)

        ttk.Label(top, text="Napor").grid(row=4, column=0, sticky="w", padx=6, pady=6)
        self.effort_combo = ttk.Combobox(
            top, textvariable=self.effort_var, state="readonly", width=30,
        )
        self.effort_combo.grid(row=4, column=1, sticky="w", padx=6, pady=6)
        self.effort_combo.bind("<<ComboboxSelected>>", self._on_effort_change)
        self._refresh_effort_options()

        ttk.Label(top, textvariable=self.preview_var, foreground="#9aa0a6").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 0)
        )

        ctrl = ttk.Frame(self.root)
        ctrl.grid(row=2, column=0, sticky="ew", padx=14)
        ctrl.columnconfigure(3, weight=1)

        self.btn_start = ttk.Button(
            ctrl, text="▶  Pokreni", command=self._on_start, style="Accent.TButton"
        )
        self.btn_start.grid(row=0, column=0, padx=(0, 6), pady=4)
        self.btn_stop = ttk.Button(ctrl, text="Zaustavi", command=self._on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=6, pady=4)
        self.chk_dry = ttk.Checkbutton(ctrl, text="Probni prolaz (samo popis)", variable=self.dry_run_var)
        self.chk_dry.grid(row=0, column=2, padx=12)

        self.btn_open_csv = ttk.Button(
            ctrl, text="Otvori output.csv",
            command=lambda: self._open_path(Path(self.output_var.get()) / "output.csv"),
            state="disabled",
        )
        self.btn_open_csv.grid(row=0, column=4, padx=6)
        self.btn_open_byhand = ttk.Button(
            ctrl, text="Otvori byhand/",
            command=lambda: self._open_path(Path(self.output_var.get()) / "byhand"),
            state="disabled",
        )
        self.btn_open_byhand.grid(row=0, column=5, padx=6)
        self.btn_retry_byhand = ttk.Button(
            ctrl, text="Ponovi byhand/",
            command=self._on_retry_byhand,
            state="disabled",
        )
        self.btn_retry_byhand.grid(row=0, column=6, padx=(6, 0))

        prog = ttk.Frame(self.root)
        prog.grid(row=3, column=0, sticky="ew", padx=14, pady=8)
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(prog, textvariable=self.status_var, foreground="#9aa0a6").grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(prog, text=f"v{__version__}", foreground="#5b606b").grid(
            row=1, column=1, sticky="e", padx=(12, 0), pady=(4, 0)
        )

        log_frame = ttk.Frame(self.root)
        log_frame.grid(row=4, column=0, sticky="nsew", padx=14, pady=(6, 12))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log = tk.Text(
            log_frame, wrap="none", height=15, borderwidth=0, relief="flat",
            font=("Consolas", 10), state="disabled",
            background="#15171c", foreground="#e6e6e6", insertbackground="#e6e6e6",
            selectbackground="#3a4a6b",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.tag_config("stderr", foreground="#ff7b72")
        self.log.tag_config("info", foreground="#79b8ff")
        self.log.tag_config("done", foreground="#7ee787")
        self.log.tag_config("search", background="#4a3a00")

        # ttk scrollbars, so both follow the dark theme (ScrolledText's are classic light ones).
        vbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        self.log.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)

        self.search_frame = ttk.Frame(self.root)
        ttk.Label(self.search_frame, text="Traži:").pack(side="left", padx=(8, 4))
        self._search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var)
        self._search_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(self.search_frame, text="Sljedeće", command=self._search_next).pack(side="left", padx=4)
        ttk.Button(self.search_frame, text="Zatvori", command=self._hide_search).pack(side="left", padx=(4, 8))
        self._search_entry.bind("<Return>", lambda _e: self._search_next())

        # Caps Lock turns Ctrl+F into keysym F; macOS users press Cmd+F.
        for sequence in ("<Control-f>", "<Control-F>", "<Command-f>"):
            self.root.bind(sequence, lambda _e: self._show_search())
        self.root.bind("<Escape>", lambda _e: self._hide_search())

        # Never narrower than the control row: the Croatian labels are long and fonts differ
        # per OS and DPI, so a fixed minimum cut off the last button.
        self.root.update_idletasks()
        self.root.minsize(max(900, ctrl.winfo_reqwidth() + 28), 460)

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

    def _pick_input(self):
        d = filedialog.askdirectory(
            title="Odaberite ulaznu mapu",
            initialdir=self.input_var.get() or str(Path.home()),
        )
        if d:
            self.input_var.set(d)
            self._save_settings()
            self._refresh_preview()

    def _pick_output(self):
        d = filedialog.askdirectory(
            title="Odaberite izlaznu mapu",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if d:
            self.output_var.set(d)
            self._save_settings()
            self._refresh_preview()
            self._refresh_output_buttons()

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
            self.preview_var.set("Nema podržanih slika (.jpg/.jpeg/.png/.webp)." + heic_note)
            return
        cost, secs, measured = ui_logic.estimate(self._settings.get("stats"), self._model_id(),
                                                 self._effort_id(), count)
        basis = "prema prošlim obradama" if measured else "gruba procjena"
        self.preview_var.set(f"Pronađeno slika: {count}. Procjena: ~{self._fmt_duration(secs)}, "
                             f"~${cost:.2f} ({basis}).{heic_note}")

    def _refresh_retry_button(self):
        """Ground-truth check: enable "Ponovi byhand/" only if byhand/ actually has images."""
        out = self.output_var.get()
        if not out:
            self.btn_retry_byhand.configure(state="disabled")
            return
        byhand = Path(out) / "byhand"
        try:
            has_images = byhand.is_dir() and any(
                f.is_file() and is_supported_image(f) for f in byhand.iterdir()
            )
        except OSError:
            # An unreadable output folder must not take the whole app down: this runs
            # from __init__, so an uncaught OSError here means the window never opens.
            has_images = False
        self.btn_retry_byhand.configure(state="normal" if has_images else "disabled")

    def _refresh_output_buttons(self):
        """Enable Open and Retry from what is on disk, never from what the last click did."""
        if self.proc is not None:
            return                      # a run is in progress; _set_running disabled them
        out = self.output_var.get()
        base = Path(out) if out else None
        # os.path.isfile/isdir never raise; Path.is_file/is_dir re-raise PermissionError on
        # Python <= 3.12 for a folder under one it cannot enter, and this runs from __init__.
        self.btn_open_csv.configure(
            state="normal" if base and os.path.isfile(base / "output.csv") else "disabled")
        self.btn_open_byhand.configure(
            state="normal" if base and os.path.isdir(base / "byhand") else "disabled")
        self._refresh_retry_button()

    # ----- start / stop / lifecycle -----------------------------------------

    def _on_start(self):
        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()
        dry = self.dry_run_var.get()

        if not in_path or not (out_path or dry):
            messagebox.showwarning("Nedostaje mapa", "Odaberite ulaznu i izlaznu mapu.")
            return

        in_dir = Path(in_path)
        if not in_dir.is_dir():
            messagebox.showerror("Neispravna ulazna mapa", f"Ulazna mapa ne postoji:\n{in_dir}")
            return

        try:
            image_count = sum(1 for f in in_dir.iterdir() if f.is_file() and is_supported_image(f))
        except OSError as e:
            messagebox.showerror("Neispravna ulazna mapa", f"Ne mogu pročitati ulaznu mapu:\n{e}")
            return
        if image_count == 0:
            messagebox.showwarning(
                "Nema slika",
                f"{in_dir}\n\nnema podržanih slika (.jpg/.jpeg/.png/.webp).\n\n"
                "Nema se što obraditi — HEIC/HEIF fotografije treba prvo pretvoriti u JPG.",
            )
            return
        self._save_settings()

        # A dry run only lists the photos: no key, no output folder, no lock.
        if dry:
            self._launch_dry_run(in_dir)
            return

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
                    or (mode == "fresh" and not self._backup_outputs(out_dir))
                    or not self._csv_writable(out_dir)):
                self._release_lock()
                return
            self._launch(in_dir, out_dir, model, effort, resume=(mode == "resume"), retry=retry)
        except BaseException:
            self._release_lock()
            raise

    def _choose_output_mode(self, in_dir: Path, out_dir: Path) -> str | None:
        """'fresh', 'resume', or None when the user cancels, for a run into out_dir."""
        csv_path = out_dir / "output.csv"
        processed = read_processed(out_dir)
        if not csv_path.exists():
            if processed and not messagebox.askyesno(
                "Nedostaje output.csv",
                f"output.csv nedostaje, ali .processed bilježi obrađene slike (ukupno: {len(processed)}).\n\n"
                "Ako krenete ispočetka, te će se slike ponovno poslati API-ju i ponovno platiti.\n\n"
                "Svejedno krenuti ispočetka?",
            ):
                return None
            return "fresh"
        try:
            names = {f.name for f in in_dir.iterdir() if f.is_file() and is_supported_image(f)}
        except OSError:
            names = set()
        problem = resume_problem(out_dir)
        return self._ask_existing_output(
            csv_path, ui_logic.csv_data_rows(csv_path), len(names & processed), len(names),
            ui_logic.RESUME_BLOCKERS.get(problem) if problem else None,
        )

    def _ask_existing_output(self, csv_path: Path, rows: int, done: int, total: int,
                             blocker: str | None) -> str | None:
        win, choice = self._build_existing_output_dialog(csv_path, rows, done, total, blocker)
        win.wait_visibility()
        win.grab_set()
        self.root.wait_window(win)
        return choice["value"]

    def _build_existing_output_dialog(self, csv_path: Path, rows: int, done: int, total: int,
                                      blocker: str | None):
        win = tk.Toplevel(self.root)
        win.title("output.csv već postoji")
        win.configure(background=self._bg)
        win.transient(self.root)
        win.resizable(False, False)
        choice = {"value": None}

        def pick(value):
            choice["value"] = value
            win.destroy()

        frm = ttk.Frame(win, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frm, justify="left", wraplength=520,
            text=f"{csv_path} već postoji (redaka: {rows}).\n"
                 f"Već obrađeno: {done}/{total} slika iz ulazne mape.",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            frm, justify="left", foreground="#9aa0a6", wraplength=520,
            text="Nastavi — obradi samo preostale slike i dopiši ih.\n"
                 "Prepiši — spremi kopiju (output.<vrijeme>.bak.csv i byhand.<vrijeme>.bak) "
                 "i kreni ispočetka.",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        if blocker:
            ttk.Label(frm, text=f"Nastavak nije moguć: {blocker}.", foreground="#ff7b72",
                      wraplength=520, justify="left").grid(row=2, column=0, columnspan=3,
                                                           sticky="w", pady=(8, 0))
        win.btn_resume = ttk.Button(frm, text="Nastavi", style="Accent.TButton",
                                    command=lambda: pick("resume"),
                                    state="disabled" if blocker else "normal")
        win.btn_fresh = ttk.Button(frm, text="Prepiši", command=lambda: pick("fresh"))
        win.btn_cancel = ttk.Button(frm, text="Odustani", command=lambda: pick(None))
        win.btn_resume.grid(row=3, column=0, padx=(0, 6), pady=(14, 0))
        win.btn_fresh.grid(row=3, column=1, padx=6, pady=(14, 0))
        win.btn_cancel.grid(row=3, column=2, padx=(6, 0), pady=(14, 0))
        win.bind("<Escape>", lambda _e: pick(None))
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        (win.btn_fresh if blocker else win.btn_resume).focus_set()
        return win, choice

    def _backup_outputs(self, out_dir: Path) -> bool:
        """Move output.csv and byhand/ aside, with one timestamp, before a fresh run: both or neither."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        csv_path = out_dir / "output.csv"
        csv_backup = out_dir / f"output.{stamp}.bak.csv"
        csv_moved = False
        try:
            if csv_path.exists():
                csv_path.replace(csv_backup)
                csv_moved = True
            byhand = out_dir / "byhand"
            if byhand.is_dir():
                byhand.rename(out_dir / f"byhand.{stamp}.bak")
        except OSError as e:
            if csv_moved:       # byhand/ would not move (a photo open in a viewer): undo the first half
                try:
                    csv_backup.replace(csv_path)
                except OSError:
                    pass
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
        if lock.exists() and not messagebox.askyesno(
            "Mapa je zauzeta",
            f"{lock} postoji.\n\n"
            "Možda neka druga obrada već koristi ovu izlaznu mapu. Svejedno nastaviti?",
        ):
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
            key = (os.environ.get("ANTHROPIC_API_KEY")
                   or dotenv_values(PROJECT_DIR / ".env").get("ANTHROPIC_API_KEY") or "").strip()
        return key

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
        self._append_log(f"$ {' '.join(cmd)}\n", "info")

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
            # In the one-file PyInstaller build the process we spawned is the bootloader,
            # not the Python process doing the work -- terminating it alone can leave the
            # extractor running detached, still making paid API calls with no window to
            # stop it. taskkill /T takes down the whole tree.
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
                self._append_log(line, "done")
                return

        tag = "stderr" if kind == "stderr" else None
        prefix = "[stderr] " if kind == "stderr" else ""
        if kind == "stderr" and line.strip():
            self._last_stderr = line.strip()
        self._append_log(prefix + line, tag)

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

    def _append_log(self, text: str, tag: str | None = None):
        at_bottom = self.log.yview()[1] >= 0.999
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", text, tag)
        else:
            self.log.insert("end", text)
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
        self._set_running(False)
        self._release_lock()
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

        again = "Ponovi byhand/" if is_retry else "Pokreni"
        where = "byhand_retry/output.csv" if is_retry else "output.csv"
        fatal = ui_logic.FATAL_TAG_RE.match(self._last_stderr)
        tag = fatal.group(1) if fatal else None
        # A dry run saved nothing to resume, and Nastavi cannot cure these two errors.
        resumable = not is_dry and tag not in ("resume-refused", "input-is-byhand")
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
            self._append_log(f"\n[izlazni kod {rc}]\n", "info")
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
                )
        elif outcome == "interrupted":
            self._append_log(f"\n[prekinuto, izlazni kod {rc}]\n", "stderr")
            self.status_var.set(f"Prekinuto (izlazni kod {rc}).")
            messagebox.showerror(
                "Obrada prekinuta",
                f"Obrada je neočekivano prekinuta (izlazni kod {rc}).\n\n"
                f"{self._last_error_line()}{resume_hint}",
            )
        else:
            self._append_log(f"\n[neuspjelo, izlazni kod {rc}]\n", "stderr")
            self.status_var.set(f"Neuspjelo (izlazni kod {rc}).")
            lead = (ui_logic.explain_failure(self._last_stderr)
                    or f"Obrada je završila s izlaznim kodom {rc}.")
            messagebox.showerror("Obrada nije uspjela",
                                 f"{lead}\n\n{self._last_error_line()}{resume_hint}")

    def _show_summary_popup(self, csv_path: Path, title: str = "Sažetak obrade"):
        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        total = ok + partial + failed
        reasons = ui_logic.tally_review_notes(csv_path, self._flagged)

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(background=self._bg)
        win.transient(self.root)
        win.resizable(False, False)

        frm = ttk.Frame(win, padding=16)
        frm.grid(row=0, column=0, sticky="nsew")
        ttk.Label(frm, text=title, font=("Segoe UI Semibold", 13)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        ttk.Label(frm, text=f"Ukupno: {total}   ·   OK: {ok}   ·   Za pregled: {partial}   ·   "
                            f"Neuspjelo: {failed}").grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=f"Ukupni trošak: ${self.total_cost:.2f}").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(2, 10))

        row = 3
        if reasons:
            ttk.Label(frm, text="Najčešći razlozi za pregled:", font=("Segoe UI Semibold", 10)).grid(
                row=row, column=0, columnspan=2, sticky="w")
            row += 1
            for reason, count in reasons:
                ttk.Label(frm, text=f"  {count}×  {reason}", foreground="#9aa0a6").grid(
                    row=row, column=0, columnspan=2, sticky="w")
                row += 1

        ttk.Button(frm, text="Otvori CSV", command=lambda: self._open_path(csv_path)).grid(
            row=row, column=0, sticky="w", pady=(14, 0))
        close_btn = ttk.Button(frm, text="Zatvori", command=win.destroy)
        close_btn.grid(row=row, column=1, sticky="e", pady=(14, 0))
        win.bind("<Escape>", lambda _e: win.destroy())
        win.bind("<Return>", lambda _e: win.destroy())
        close_btn.focus_set()

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
        try:
            n = (sum(1 for f in byhand_dir.iterdir() if f.is_file() and is_supported_image(f))
                 if byhand_dir.is_dir() else 0)
        except OSError:
            n = 0
        if n == 0:
            messagebox.showinfo("Nema slika", "Nema slika u byhand/ za ponovnu obradu.")
            return

        model, effort = ui_logic.retry_settings(self._model_id(), self._effort_id())
        cost, _secs, _measured = ui_logic.estimate(self._settings.get("stats"), model, effort, n)
        retry_out = out_dir / "byhand_retry"
        if not messagebox.askyesno(
            "Ponovna obrada",
            f"Ponovno obraditi slike iz byhand/ (ukupno: {n})?\n\n"
            f"Model: {ui_logic.MODEL_LABELS.get(model, model)}, "
            f"napor: {ui_logic.EFFORT_LABELS.get(effort, effort)}\n"
            f"Procjena: ~${cost:.2f}\n\n"
            "Ovo su novi, plaćeni API pozivi. Rezultati idu u zasebnu mapu:\n"
            f"{retry_out}",
        ):
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
        if self.proc and self.proc.poll() is None:
            done = self.counters["ok"] + self.counters["partial"] + self.counters["failed"]
            if not messagebox.askyesno(
                "Obrada u tijeku",
                f"Obrada je u tijeku (gotovo: {done}).\nIzaći i zaustaviti obradu?",
            ):
                return
            # Stop on a worker thread and close once the extractor is gone: stopping can take
            # seconds (up to ~18 s on Windows), and a frozen window looks like a crash.
            self._closing = True
            self._stop_requested = True
            self.btn_stop.configure(state="disabled")
            self.status_var.set("Zaustavljam obradu…")
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
            self.btn_open_csv.configure(state="disabled")
            self.btn_open_byhand.configure(state="disabled")
            self.btn_retry_byhand.configure(state="disabled")
            self.model_combo.configure(state="disabled")
            self.effort_combo.configure(state="disabled")
            self.chk_dry.configure(state="disabled")
            self.status_var.set("Pokrećem…")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_in.configure(state="normal")
            self.btn_out.configure(state="normal")
            self.model_combo.configure(state="readonly")
            self.effort_combo.configure(state="readonly")
            self.chk_dry.configure(state="normal")

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
        self._search_index = "1.0"

    # ----- helpers ----------------------------------------------------------

    def _open_path(self, p: Path):
        if not p.exists():
            messagebox.showinfo("Nije pronađeno", f"{p} ne postoji.")
            return
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
        """Bring the window back when a run ends, even if it was minimized."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.bell()
        except tk.TclError:
            pass

    # ----- search bar -------------------------------------------------------

    def _show_search(self):
        self.search_frame.grid(row=5, column=0, sticky="ew", padx=14, pady=(0, 8))
        self._search_entry.focus_set()
        self._search_entry.select_range(0, "end")

    def _hide_search(self):
        self.search_frame.grid_forget()
        self.log.tag_remove("search", "1.0", "end")
        self._search_index = "1.0"

    def _search_next(self):
        q = self.search_var.get()
        if not q:
            return
        self.log.tag_remove("search", "1.0", "end")
        idx = self.log.search(q, self._search_index, nocase=True, stopindex="end")
        if not idx:
            idx = self.log.search(q, "1.0", nocase=True, stopindex="end")
            if not idx:
                return
        end_idx = f"{idx}+{len(q)}c"
        self.log.tag_add("search", idx, end_idx)
        self.log.see(idx)
        self._search_index = end_idx

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
            self.status_var.set("Postavke i API ključ spremljeni.")
        else:
            messagebox.showerror("Ne mogu spremiti postavke", f"Ne mogu pisati u {SETTINGS_PATH}.")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
