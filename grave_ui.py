#!/usr/bin/env python3
"""TRON-GRAVE desktop UI — wraps grave_extractor.py with a Tkinter front-end."""

import atexit
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from dotenv import load_dotenv

from extractor.file_utils import is_supported_image


if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle — exe lives next to all bundled files
    PROJECT_DIR = Path(sys.executable).parent
    _EXTRACTOR_CMD = [sys.executable, "--_run-extractor"]
else:
    PROJECT_DIR = Path(__file__).resolve().parent
    _EXTRACTOR_CMD = [sys.executable, "-u", str(Path(__file__).resolve().parent / "grave_extractor.py")]
SETTINGS_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    / "tron-grave"
    / "ui.json"
)

LOG_LINE_CAP = 5000
LOG_TRIM_BATCH = 500
DRAIN_CAP_PER_TICK = 200
MAX_LINE_CHARS = 4096
COST_PER_IMAGE = 0.005
SECS_PER_IMAGE_GUESS = 4

PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s")
RESULT_RE = re.compile(r"\b(OK|PARTIAL|FAILED)(?:\s|\()")
DONE_RE = re.compile(r"^Done\. \d+ images processed")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("TRON-GRAVE")
        self.root.geometry("960x640")
        self.root.minsize(720, 460)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.api_key_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready.")
        self.preview_var = tk.StringVar(value="")
        self.search_var = tk.StringVar()

        self.proc: subprocess.Popen | None = None
        self.pgid: int | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.line_count = 0
        self.run_start_time: float | None = None
        self.last_total: int | None = None
        self.counters = {"ok": 0, "partial": 0, "failed": 0}
        self.lockfile_path: Path | None = None
        self._search_index = "1.0"

        self._load_settings()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        atexit.register(self._atexit_kill)
        self.root.after(50, self._drain_queue)
        self._refresh_preview()

    # ----- UI construction --------------------------------------------------

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        top = ttk.Frame(self.root)
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Input folder").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(top, textvariable=self.input_var, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=4, pady=4
        )
        self.btn_in = ttk.Button(top, text="Browse…", command=self._pick_input)
        self.btn_in.grid(row=0, column=2, padx=4, pady=4)

        ttk.Label(top, text="Output folder").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(top, textvariable=self.output_var, state="readonly").grid(
            row=1, column=1, sticky="ew", padx=4, pady=4
        )
        self.btn_out = ttk.Button(top, text="Browse…", command=self._pick_output)
        self.btn_out.grid(row=1, column=2, padx=4, pady=4)

        ttk.Label(top, text="API Key").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(top, textvariable=self.api_key_var, show="*").grid(
            row=2, column=1, sticky="ew", padx=4, pady=4
        )
        ttk.Button(top, text="Save", command=self._save_settings).grid(row=2, column=2, padx=4, pady=4)

        ttk.Label(top, textvariable=self.preview_var, foreground="#555").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=4, pady=(2, 0)
        )

        ctrl = ttk.Frame(self.root)
        ctrl.grid(row=1, column=0, sticky="ew", padx=6)
        ctrl.columnconfigure(4, weight=1)

        self.btn_start = ttk.Button(ctrl, text="Start", command=self._on_start)
        self.btn_start.grid(row=0, column=0, padx=4, pady=4)
        self.btn_stop = ttk.Button(ctrl, text="Stop", command=self._on_stop, state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=4, pady=4)
        ttk.Checkbutton(ctrl, text="Dry run (list only)", variable=self.dry_run_var).grid(
            row=0, column=2, padx=8
        )

        self.btn_open_csv = ttk.Button(
            ctrl, text="Open output.csv",
            command=lambda: self._open_path(Path(self.output_var.get()) / "output.csv"),
            state="disabled",
        )
        self.btn_open_csv.grid(row=0, column=5, padx=4)
        self.btn_open_byhand = ttk.Button(
            ctrl, text="Open byhand/",
            command=lambda: self._open_path(Path(self.output_var.get()) / "byhand"),
            state="disabled",
        )
        self.btn_open_byhand.grid(row=0, column=6, padx=4)

        prog = ttk.Frame(self.root)
        prog.grid(row=2, column=0, sticky="ew", padx=6, pady=4)
        prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew", padx=4)
        ttk.Label(prog, textvariable=self.status_var).grid(
            row=1, column=0, sticky="w", padx=4, pady=(2, 0)
        )

        log_frame = ttk.Frame(self.root)
        log_frame.grid(row=3, column=0, sticky="nsew", padx=6, pady=6)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log = ScrolledText(
            log_frame, wrap="none", height=15,
            font=("Monospace", 9), state="disabled",
            background="#111", foreground="#ddd", insertbackground="#ddd",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log.tag_config("stderr", foreground="#ff8a8a")
        self.log.tag_config("info", foreground="#8acfff")
        self.log.tag_config("done", foreground="#8aff9a")
        self.log.tag_config("search", background="#5e4400")

        hbar = ttk.Scrollbar(log_frame, orient="horizontal", command=self.log.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        self.log.configure(xscrollcommand=hbar.set)

        self.search_frame = ttk.Frame(self.root)
        ttk.Label(self.search_frame, text="Find:").pack(side="left", padx=(8, 4))
        self._search_entry = ttk.Entry(self.search_frame, textvariable=self.search_var)
        self._search_entry.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(self.search_frame, text="Next", command=self._search_next).pack(side="left", padx=4)
        ttk.Button(self.search_frame, text="Close", command=self._hide_search).pack(side="left", padx=(4, 8))
        self._search_entry.bind("<Return>", lambda _e: self._search_next())

        self.root.bind("<Control-f>", lambda _e: self._show_search())
        self.root.bind("<Escape>", lambda _e: self._hide_search())

    # ----- folder picking + preview -----------------------------------------

    def _pick_input(self):
        d = filedialog.askdirectory(
            title="Pick input folder",
            initialdir=self.input_var.get() or str(Path.home()),
        )
        if d:
            self.input_var.set(d)
            self._save_settings()
            self._refresh_preview()

    def _pick_output(self):
        d = filedialog.askdirectory(
            title="Pick output folder",
            initialdir=self.output_var.get() or str(Path.home()),
        )
        if d:
            self.output_var.set(d)
            self._save_settings()
            self._refresh_preview()

    def _refresh_preview(self):
        self.btn_open_csv.configure(state="disabled")
        self.btn_open_byhand.configure(state="disabled")
        in_path = self.input_var.get()
        if not in_path:
            self.preview_var.set("")
            return
        p = Path(in_path)
        if not p.is_dir():
            self.preview_var.set("Input folder does not exist.")
            return
        try:
            count = sum(1 for f in p.iterdir() if f.is_file() and is_supported_image(f))
        except OSError as e:
            self.preview_var.set(f"Cannot read input folder: {e}")
            return
        if count == 0:
            self.preview_var.set("Found 0 supported images (.jpg/.jpeg/.png/.webp).")
            return
        est_min = max(1, round(count * SECS_PER_IMAGE_GUESS / 60))
        est_cost = count * COST_PER_IMAGE
        self.preview_var.set(
            f"Found {count} images.  Approx. ~{est_min} min, ~${est_cost:.2f} in API cost."
        )

    # ----- start / stop / lifecycle -----------------------------------------

    def _on_start(self):
        in_path = self.input_var.get().strip()
        out_path = self.output_var.get().strip()

        if not in_path or not out_path:
            messagebox.showwarning("Missing folder", "Pick both an input and output folder.")
            return

        in_dir = Path(in_path)
        out_dir = Path(out_path)
        if not in_dir.is_dir():
            messagebox.showerror("Bad input", f"Input folder does not exist:\n{in_dir}")
            return

        if not self.dry_run_var.get():
            api_key = self.api_key_var.get().strip()
            if not api_key:
                load_dotenv(PROJECT_DIR / ".env", override=False)
                api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                messagebox.showerror(
                    "Missing API key",
                    "Enter your Anthropic API key in the 'API Key' field above, then click Save.\n\n"
                    "Get a key at: console.anthropic.com",
                )
                return
            os.environ["ANTHROPIC_API_KEY"] = api_key
        self._save_settings()

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Cannot create output", str(e))
            return

        lock = out_dir / ".tron-grave.lock"
        if lock.exists():
            if not messagebox.askyesno(
                "Lockfile present",
                f"{lock} exists.\n\n"
                "Another run may already be using this output folder. Take the lock anyway?",
            ):
                return
        try:
            lock.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Cannot write lockfile", str(e))
            return
        self.lockfile_path = lock

        if not self.dry_run_var.get():
            existing = out_dir / "output.csv"
            if existing.exists():
                rows = self._csv_row_count(existing)
                choice = messagebox.askyesnocancel(
                    "output.csv exists",
                    f"{existing} already exists ({rows} data rows).\n\n"
                    "Yes — back up to output.csv.bak and overwrite\n"
                    "No  — overwrite without backup\n"
                    "Cancel — abort",
                )
                if choice is None:
                    self._release_lock()
                    return
                try:
                    if choice:
                        existing.replace(out_dir / "output.csv.bak")
                    errors_path = out_dir / "errors.txt"
                    if errors_path.exists():
                        errors_path.replace(out_dir / "errors.txt.bak")
                except OSError as e:
                    messagebox.showerror("Cannot rotate files", str(e))
                    self._release_lock()
                    return

        self._reset_run_state()
        self._set_running(True)

        cmd = [
            *_EXTRACTOR_CMD,
            "--input", str(in_dir),
            "--output", str(out_dir),
            "--verbose",
        ]
        if self.dry_run_var.get():
            cmd.append("--dry-run")

        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

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
            self._append_log(f"Failed to launch extractor: {e}\n", "stderr")
            self._set_running(False)
            self.status_var.set("Failed to launch.")
            self._release_lock()
            return

        if sys.platform != "win32":
            try:
                self.pgid = os.getpgid(self.proc.pid)
            except (OSError, ProcessLookupError):
                self.pgid = self.proc.pid
        else:
            self.pgid = self.proc.pid

        self.run_start_time = time.monotonic()
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
        if not self.proc:
            return
        self._append_log("[stop requested]\n", "info")
        self.btn_stop.configure(state="disabled")
        threading.Thread(target=self._terminate_run, daemon=True).start()

    def _terminate_run(self):
        proc = self.proc
        pgid = self.pgid
        if not proc or pgid is None or proc.poll() is not None:
            return
        if sys.platform == "win32":
            for kill_fn, wait_ticks in ((proc.terminate, 30), (proc.kill, 0)):
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

        m = PROGRESS_RE.match(line)
        if m:
            done = int(m.group(1))
            total = int(m.group(2))
            rm = RESULT_RE.search(line)
            if rm:
                tag = rm.group(1)
                if tag == "OK":
                    self.counters["ok"] += 1
                elif tag == "PARTIAL":
                    self.counters["partial"] += 1
                else:
                    self.counters["failed"] += 1
            self._update_progress(done, total)

        if DONE_RE.match(line):
            self._append_log(line, "done")
            return

        tag = "stderr" if kind == "stderr" else None
        prefix = "[stderr] " if kind == "stderr" else ""
        self._append_log(prefix + line, tag)

    def _update_progress(self, done: int, total: int):
        if self.last_total != total:
            self.last_total = total
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total, value=done)
        else:
            self.progress.configure(value=done)
        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        eta_str = ""
        if self.run_start_time and 0 < done < total:
            elapsed = time.monotonic() - self.run_start_time
            remaining = (total - done) * (elapsed / done)
            eta_str = f" — ETA {self._fmt_duration(remaining)}"
        self.status_var.set(
            f"{done}/{total} — {ok} OK · {partial} manual · {failed} failed{eta_str}"
        )

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
        if self.last_total:
            self.progress.configure(mode="determinate", value=self.last_total)

        out_path = self.output_var.get()
        is_dry = self.dry_run_var.get()
        was_stopped = (rc == 130 or rc < 0)

        self._set_running(False)
        self._release_lock()

        ok = self.counters["ok"]
        partial = self.counters["partial"]
        failed = self.counters["failed"]
        saved = ok + partial + failed

        if was_stopped:
            self.status_var.set(f"Stopped — {saved} record(s) saved.")
            messagebox.showinfo(
                "Stopped",
                f"Stopped. {saved} record(s) saved to output.csv.\n\n"
                "Re-running on the same folder will reprocess from the start "
                "(already-processed images will be re-sent to the API).",
            )
        elif rc in (0, 2):
            self._append_log(f"\n[exit code {rc}]\n", "info")
            if is_dry:
                self.status_var.set("Dry run complete.")
            else:
                self.status_var.set(
                    f"Done — {ok} OK · {partial} manual · {failed} failed."
                )
                if (Path(out_path) / "output.csv").exists():
                    self.btn_open_csv.configure(state="normal")
                if (Path(out_path) / "byhand").is_dir():
                    self.btn_open_byhand.configure(state="normal")
                self._notify_done()
        else:
            self.status_var.set(f"Failed (exit code {rc}).")
            self._append_log(f"\n[failed, exit code {rc}]\n", "stderr")
            messagebox.showerror(
                "Run failed",
                f"Extractor exited with code {rc}. See log for details.",
            )

        self.proc = None
        self.pgid = None

    def _on_close(self):
        if self.proc and self.proc.poll() is None:
            done = self.counters["ok"] + self.counters["partial"] + self.counters["failed"]
            if not messagebox.askyesno(
                "Run in progress",
                f"A run is in progress ({done} done).\nQuit and stop the run?",
            ):
                return
            self._terminate_run()
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
        if self.lockfile_path:
            try:
                self.lockfile_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _release_lock(self):
        if self.lockfile_path:
            try:
                self.lockfile_path.unlink(missing_ok=True)
            except OSError:
                pass
            self.lockfile_path = None

    # ----- run state --------------------------------------------------------

    def _set_running(self, running: bool):
        if running:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
            self.btn_in.configure(state="disabled")
            self.btn_out.configure(state="disabled")
            self.btn_open_csv.configure(state="disabled")
            self.btn_open_byhand.configure(state="disabled")
            self.status_var.set("Starting…")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            self.btn_in.configure(state="normal")
            self.btn_out.configure(state="normal")

    def _reset_run_state(self):
        self.counters = {"ok": 0, "partial": 0, "failed": 0}
        self.last_total = None
        self.run_start_time = None
        self.progress.configure(mode="determinate", value=0, maximum=100)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.line_count = 0
        self._search_index = "1.0"

    # ----- helpers ----------------------------------------------------------

    @staticmethod
    def _csv_row_count(p: Path) -> int:
        try:
            with p.open(encoding="utf-8-sig") as f:
                return max(0, sum(1 for _ in f) - 1)
        except OSError:
            return 0

    def _open_path(self, p: Path):
        if not p.exists():
            messagebox.showinfo("Not found", f"{p} does not exist.")
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
            messagebox.showerror("Cannot open", str(e))

    def _notify_done(self):
        if not sys.platform.startswith("linux"):
            return
        try:
            subprocess.Popen(
                [
                    "notify-send", "TRON-GRAVE",
                    f"Done — {self.counters['ok']} OK, "
                    f"{self.counters['partial']} manual, "
                    f"{self.counters['failed']} failed",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass

    # ----- search bar -------------------------------------------------------

    def _show_search(self):
        self.search_frame.grid(row=4, column=0, sticky="ew")
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
        try:
            with SETTINGS_PATH.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("input"), str):
                self.input_var.set(data["input"])
            if isinstance(data.get("output"), str):
                self.output_var.set(data["output"])
            if isinstance(data.get("api_key"), str):
                self.api_key_var.set(data["api_key"])
        except (OSError, json.JSONDecodeError):
            pass

    def _save_settings(self):
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with SETTINGS_PATH.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "input": self.input_var.get(),
                        "output": self.output_var.get(),
                        "api_key": self.api_key_var.get(),
                    },
                    f,
                )
        except OSError:
            pass


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
