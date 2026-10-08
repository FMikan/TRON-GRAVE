"""Tk-free helpers behind the desktop GUI, kept apart from grave_ui.py so they can be tested."""

import csv
import ctypes
import json
import math
import os
import re
import socket
import sys
import uuid
from pathlib import Path

from extractor.csv_writer import FILE_INDEX, NOTES_INDEX, read_csv
from extractor.file_utils import is_supported_image
from extractor.pricing import DEFAULT_PRICING, MODEL_PRICING

# Weakest to strongest: "Ponovi byhand/" never steps down this list.
MODELS = [
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-fable-5",
    "claude-fable-5-1",
]
MODEL_LABELS = {
    "claude-sonnet-5": "Claude Sonnet 5 (zadani)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-fable-5": "Claude Fable 5",
    "claude-fable-5-1": "Claude Fable 5.1 (najjači)",
}
DEFAULT_MODEL = "claude-sonnet-5"
RETRY_MODEL = "claude-opus-5-5"

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
EFFORT_LABELS = {
    "low": "nizak",
    "medium": "srednji",
    "high": "visok",
    "xhigh": "vrlo visok",
    "max": "maksimalan",
}
DEFAULT_EFFORT = "high"
RETRY_EFFORT = "high"
# Every offered model takes all five levels; the table stays per-model so a future model
# with a narrower range only needs an entry here.
EFFORT_BY_MODEL = {model: list(EFFORT_LEVELS) for model in MODELS}


def model_id(label: str) -> str:
    """The model id behind a dropdown label (an id passes through unchanged)."""
    return next((m for m, text in MODEL_LABELS.items() if text == label), label)


def effort_id(label: str) -> str:
    """The effort level behind a dropdown label (a level passes through unchanged)."""
    return next((e for e, text in EFFORT_LABELS.items() if text == label), label)


# ---- settings ------------------------------------------------------------------------

def settings_path(platform: str = sys.platform, env=os.environ, home: Path | None = None) -> Path:
    """Where the settings live: %APPDATA% on Windows, Application Support on macOS, XDG elsewhere."""
    home = home or Path.home()
    if platform == "win32":
        base = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "tron-grave" / "ui.json"


# Where versions up to 3.5.1 kept the settings, on every OS.
LEGACY_SETTINGS_PATH = Path.home() / ".config" / "tron-grave" / "ui.json"


def load_settings(path: Path, legacy: Path | None = None) -> dict:
    """The saved settings, moving a file older versions left in ~/.config on first use."""
    legacy = legacy or LEGACY_SETTINGS_PATH
    # os.path.exists never raises; older Pythons' Path.exists re-raises PermissionError for a
    # folder it cannot enter, and that must not keep the window from opening.
    if not os.path.exists(path) and legacy != path and os.path.exists(legacy):
        data = _read_json(legacy)
        if data:
            try:
                write_settings(path, data)
                legacy.unlink()
            except OSError:
                pass
            return data
    return _read_json(path)


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_settings(path: Path, data: dict) -> None:
    """Write the settings owner-only from the first byte: the file holds the API key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # O_CREAT's mode only applies when the file is new, so tighten a leftover one
            # before any of the key is written to it.
            os.chmod(tmp, 0o600)
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)   # never leave a half-written copy of the key behind
        raise


# ---- output-folder lock --------------------------------------------------------------

LOCK_NAME = ".tron-grave.lock"


def new_lock_token(pid: int | None = None) -> str:
    return f"{socket.gethostname()}:{os.getpid() if pid is None else pid}:{uuid.uuid4().hex}"


def pid_alive(pid: int) -> bool:
    """Whether a process with this id runs on this machine (leans towards yes when unsure)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)         # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87                   # ERROR_INVALID_PARAMETER: no such process
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259                               # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:              # PermissionError: it exists, owned by someone else
        return True
    return True


def lock_owner_gone(token: str, host: str | None = None) -> bool:
    """True only for a lock this machine wrote whose process has ended (a crash or a kill)."""
    parts = token.strip().rsplit(":", 2)
    if len(parts) != 3 or not parts[1].isdigit():
        return False                      # 3.6.0's "<pid>:<uuid>" or someone else's file: ask
    lock_host, pid, _token = parts
    return lock_host == (host or socket.gethostname()) and not pid_alive(int(pid))


def release_lock(lock: Path, token: str) -> None:
    """Remove the lock only while it still holds our token: another window may have taken it."""
    try:
        if lock.read_text(encoding="utf-8") == token:
            lock.unlink()
    except (OSError, ValueError):     # ValueError: the file is not text, so it is not ours
        pass


# ---- extractor output --------------------------------------------------------------------

START_RE = re.compile(r"^\[(\d+)/(\d+)\] Processing (.+) \.\.\.$")
# Anchored to the "[k/N] " prefix, so a photo named "OK (west section).jpg" cannot make a
# FAILED image count as OK.
RESULT_RE = re.compile(r"^\[(\d+)/(\d+)\] (OK|PARTIAL|FAILED): ")
COST_RE = re.compile(r"\(total: \$([0-9.]+)\)$")
DONE_RE = re.compile(r"^Done\. \d+ images processed")


# ---- the log in Croatian -------------------------------------------------------------------

RESULT_LINE_RE = re.compile(r"^\[(\d+)/(\d+)\] (OK|PARTIAL|FAILED): (.*)$")
COST_SUFFIX_RE = re.compile(r" — \$([0-9.]+) \(total: \$[0-9.]+\)$")
_RECORDS_RE = re.compile(r"^(\d+) records?$")
_DONE_LINE_RE = re.compile(r"^Done\. (\d+) images processed\. (\d+) succeeded, (\d+) partial, (\d+) failed\.$")
_TOTAL_RE = re.compile(r"^Total cost: \$([0-9.]+)$")
_OUTPUT_RE = re.compile(r"^Output:\s+(.+)$")
_REVIEW_RE = re.compile(r"^Review:\s+(.+) \((\d+) images\)$")
_RESUME_RE = re.compile(r"^Resume: skipping (\d+) already-processed image\(s\)\.$")
_UNEXPECTED_RE = re.compile(r"^[A-Z]\w*(Error|Exception): ")
VERDICTS_HR = {"OK": ("OK", "ok"), "PARTIAL": ("ZA PREGLED", "review"), "FAILED": ("NEUSPJELO", "failed")}
API_FAILURE_HR = "greška API-ja (nije naplaćeno)"
_REASONS_HR = {
    "Name or surname could not be read": "nečitko ime ili prezime",
    "Model not certain whether a year of birth or death exists": "nesigurna godina rođenja ili smrti",
    "Birth year is after death year": "provjeri godine (rođenje nakon smrti)",
    "Nearby markers may belong to this grave and were left out": "provjeri: možda više oznaka",
    "Model declined to answer (refusal)": "odbijeno",
    "All fields illegible": "sve nečitko",
    "Model returned no records": "nema podataka",
    "output.csv is locked": "output.csv je zaključan",
}
_REASON_PREFIXES_HR = (
    ("Model reported a problem: ", "model javlja: {}"),
    ("Response hit the max_tokens ceiling", "odgovor prekinut"),
    ("Model returned no valid JSON answer", "neispravan odgovor"),
    ("File could not be read or decoded", "ne mogu otvoriti datoteku"),
    ("API call failed after retries", API_FAILURE_HR),
    ("Error code: ", API_FAILURE_HR),
    ("Connection error", API_FAILURE_HR),
)
_WARNINGS_HR = (
    (re.compile(r"^warning: skipping (\d+) \.heic/\.heif file\(s\)"),
     "Upozorenje: preskočeno HEIC/HEIF datoteka: {0} — pretvorite ih u JPG."),
    (re.compile(r"^warning: .*output\.csv is locked.*already paid for$"),
     "Upozorenje: output.csv je zaključan (otvoren u Excelu?) — zatvorite ga; obrada čeka da spremi "
     "rezultat već plaćene slike."),
    (re.compile(r"^warning: .*output\.csv is locked"),
     "Upozorenje: output.csv je zaključan (otvoren u Excelu?) — zatvorite ga; pokušavam još 30 s."),
    (re.compile(r"^warning: could not copy (.+) to byhand/"), "Upozorenje: ne mogu kopirati {0} u byhand/."),
    (re.compile(r"^warning: could not remove (.+) from byhand/"), "Upozorenje: ne mogu ukloniti {0} iz byhand/."),
    (re.compile(r"^warning: no price table entry for (\S+);"),
     "Upozorenje: model {0} nema cijenu u tablici — prikazani trošak je procjena."),
)


def plural_hr(n: int, one: str, few: str, many: str) -> str:
    """The Croatian form for n: 1 and 21 take `one`, 2–4 and 22–24 `few`, the rest (11–14 too) `many`."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def reason_hr(reason: str) -> str:
    """The extractor's English reason in the CSV's Croatian words; the model's own text unchanged."""
    if reason in _REASONS_HR:
        return _REASONS_HR[reason]
    for prefix, text in _REASON_PREFIXES_HR:
        if reason.startswith(prefix):
            return text.format(reason[len(prefix):])
    if _UNEXPECTED_RE.match(reason):
        return "neočekivana greška (pojedinosti u tehničkom zapisu)"
    return reason


def describe_line(kind: str, line: str, files: dict[int, str]):
    """The default log view's line for one extractor line.

    None: show the raw line in both views. ("", None): technical view only.
    (text, tag): this Croatian text in the default view, the raw line in the technical one.
    `files` maps k to the file named by its "[k/N] Processing" line, so splitting the reason off
    a result line is exact even when the name holds parentheses.
    """
    text = line.rstrip("\r\n")
    if kind == "stderr":
        m = FATAL_TAG_RE.match(text)
        if m:
            return f"Greška: {FATAL_EXPLANATIONS.get(m.group(1), text[m.end():])}", "error"
        if text.startswith("error: "):
            return f"Greška: {text[len('error: '):]}", "error"
        for pattern, template in _WARNINGS_HR:
            m = pattern.match(text)
            if m:
                return template.format(*m.groups()), "warn"
        return None
    if START_RE.match(text):
        return "", None
    m = RESULT_LINE_RE.match(text)
    if m:
        k, total, verdict, rest = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        name = files.get(k)
        cost = COST_SUFFIX_RE.search(rest)
        body = rest[:cost.start()] if cost else rest
        if not name or not body.startswith(name + " (") or not body.endswith(")"):
            return None
        inner = body[len(name) + 2:-1]
        label, tag = VERDICTS_HR[verdict]
        records = _RECORDS_RE.match(inner) if verdict == "OK" else None
        if records:
            n = int(records.group(1))
            detail = f"{n} {plural_hr(n, 'osoba', 'osobe', 'osoba')}"
        else:
            detail = reason_hr(inner)
        price = f" · ${cost.group(1)}" if cost else ""
        return f"[{k}/{total}] {label:<10} {name} · {detail}{price}", tag
    m = _DONE_LINE_RE.match(text)
    if m:
        n, ok, partial, failed = m.groups()
        return f"Gotovo. Obrađeno slika: {n} (OK: {ok}, za pregled: {partial}, neuspjelo: {failed}).", "done"
    m = _TOTAL_RE.match(text)
    if m:
        return f"Ukupni trošak: ${m.group(1)}", "done"
    m = _REVIEW_RE.match(text)
    if m:
        return f"Za pregled: {m.group(1)} (slika: {m.group(2)})", "info"
    m = _OUTPUT_RE.match(text)
    if m:
        return f"Rezultati: {m.group(1)}", "info"
    m = _RESUME_RE.match(text)
    if m:
        return f"Nastavak: preskačem već obrađene slike ({m.group(1)}).", "info"
    return None


def parse_progress(line: str):
    """("start", k, n, filename), ("result", k, n, verdict, total cost or None), or None."""
    line = line.rstrip("\r\n")
    m = START_RE.match(line)
    if m:
        return ("start", int(m.group(1)), int(m.group(2)), m.group(3))
    m = RESULT_RE.match(line)
    if m:
        cost = COST_RE.search(line)
        return ("result", int(m.group(1)), int(m.group(2)), m.group(3),
                float(cost.group(1)) if cost else None)
    return None


def classify_exit(rc: int, stop_requested: bool, saw_done: bool, is_dry: bool) -> str:
    """How a run ended: "done", "stopped" (the user's Stop), "interrupted" (killed without a
    Stop click) or "failed"."""
    # Windows has no signal exit codes -- a killed child reports 1 -- so a Stop click is the
    # only reliable sign a non-zero exit was deliberate. The Done line outranks it, whatever
    # code the kill left (1 from taskkill on Windows, 130 from a SIGINT just before exit):
    # Stop clicked after the last photo did not stop anything.
    if stop_requested:
        return "done" if saw_done else "stopped"
    # Exit code 2 means "finished with issues", but argparse and the CPython launcher also
    # exit 2 on failures that never processed anything -- so require the extractor's own
    # completion line before believing it.
    if rc == 0 or (rc == 2 and (is_dry or saw_done)):
        return "done"
    if rc == 130 or rc < 0:
        return "interrupted"
    return "failed"


# ---- run checks and texts ------------------------------------------------------------

def same_dir(a: Path, b: Path) -> bool:
    """True when a and b are the same folder (samefile when both exist, else resolved paths)."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


SUBFOLDER_HINT = "Slike su u podmapama (ukupno: {n}) — odaberite podmapu; podmape se ne pretražuju."


def images_in_subfolders(folder: Path) -> int:
    """Supported photos one level down (dot folders skipped); 0 when the folder can't be read."""
    total = 0
    try:
        subfolders = [p for p in Path(folder).iterdir() if not p.name.startswith(".") and p.is_dir()]
    except OSError:
        return 0
    for sub in subfolders:
        try:
            total += sum(1 for f in sub.iterdir() if f.is_file() and is_supported_image(f))
        except OSError:
            continue
    return total


def csv_data_rows(path: Path) -> int:
    """Data rows in output.csv; 0 when it is missing or unreadable (never raises)."""
    try:
        return len(read_csv(path)[1])
    except (OSError, ValueError, csv.Error):
        return 0


# Why "Nastavi" is greyed out, per extractor.csv_writer.resume_problem() code.
RESUME_BLOCKERS = {
    "old-format": "output.csv ima drugačije stupce — izradila ga je starija verzija programa "
                  "ili je ponovno spremljen iz Excela",
    "no-processed": "nedostaje popis obrađenih slika (.processed), pa bi se sve slike ponovno poslale",
    "missing-csv": "output.csv je prazan ili nedostaje",
    "unreadable": "output.csv ili .processed se ne može pročitati — ako je mapa na mrežnom disku "
                  "ili OneDriveu, provjerite vezu i pokušajte ponovno",
    "not-utf8": "output.csv više nije u UTF-8 zapisu (ponovno je spremljen iz Excela ili uređivača "
                "kao ANSI) — spremite ga kao UTF-8 ili krenite ispočetka",
}
# Shown the same way as a RESUME_BLOCKERS reason: Nastavi has nothing left to do.
ALL_DONE = "sve slike iz ulazne mape već su obrađene"

# The extractor's fatal errors read "error: [tag] ...".
FATAL_TAG_RE = re.compile(r"^error: \[([a-z0-9-]+)\] ")
FATAL_EXPLANATIONS = {
    "api-401": "API ključ nije ispravan ili je opozvan.",
    "api-402": "Problem s naplatom — provjerite plaćanje i stanje računa na console.anthropic.com.",
    "api-403": "Ovaj API ključ nema pristup odabranom modelu.",
    "api-404": "Odabrani model ne postoji.",
    "spend-cap": "Dosegnut je mjesečni limit potrošnje za API (ili je API privremeno odbio zahtjev "
                 "zbog ograničenja brzine — pričekajte minutu pa kliknite Pokreni → Nastavi).",
    "api-down": "API tri puta zaredom nije uspio. Provjerite internetsku vezu, stanje računa i limite potrošnje.",
    "csv-locked": "output.csv je zaključan — zatvorite ga (npr. u Excelu).",
    "resume-refused": "Nastavak nije moguć za ovu izlaznu mapu.",
    "input-is-byhand": "Ulazna mapa ne smije biti byhand/ mapa izlazne mape.",
    "no-images": "Ulazna mapa nema podržanih slika.",
    "output-exists": "U izlaznoj mapi već postoji output.csv s rezultatima.",
}


def explain_failure(stderr_line: str) -> str | None:
    """A Croatian explanation of the extractor's tagged fatal error, if the line has a tag."""
    m = FATAL_TAG_RE.match(stderr_line or "")
    return FATAL_EXPLANATIONS.get(m.group(1)) if m else None


# What to do about each fatal error; {again} is the button that resumes the run.
FATAL_ACTIONS = {
    "api-401": "Upišite ispravan ključ u polje API ključ i kliknite Spremi ključ, zatim {again} → Nastavi.",
    "api-402": "Provjerite plaćanje i stanje računa na console.anthropic.com, zatim {again} → Nastavi.",
    "api-403": "Odaberite drugi model ili na console.anthropic.com provjerite dozvole ključa, "
               "zatim {again} → Nastavi.",
    "api-404": "Odaberite drugi model, zatim {again} → Nastavi.",
    "spend-cap": "Povećajte limit na console.anthropic.com ili pričekajte minutu, zatim {again} → Nastavi.",
    "api-down": "Provjerite internetsku vezu, zatim {again} → Nastavi.",
    "csv-locked": "Zatvorite output.csv (npr. u Excelu), zatim {again} → Nastavi.",
    "resume-refused": "Kliknite {again} i odaberite Prepiši; stare datoteke spremaju se kao kopija.",
    "input-is-byhand": "Odaberite drugu izlaznu mapu.",
    "no-images": "Odaberite mapu sa slikama .jpg, .jpeg, .png ili .webp.",
    "output-exists": "Kliknite {again} i odaberite Nastavi ili Prepiši.",
}
RESUMED_FREE = "Već obrađene slike neće se ponovno slati ni plaćati."


def failure_text(stderr_line: str, again: str, resumable: bool) -> str | None:
    """Explanation and next step for a tagged fatal error, without the raw API reply."""
    explanation = explain_failure(stderr_line)
    if explanation is None:
        return None
    tag = FATAL_TAG_RE.match(stderr_line).group(1)
    parts = [explanation, FATAL_ACTIONS[tag].format(again=again)]
    if resumable:
        parts.append(RESUMED_FREE)
    return "\n\n".join(parts)


# ---- estimates -----------------------------------------------------------------------

IMAGE_TOKENS_GUESS = 4_700      # a phone photo after the API's own downscale (<=4,784 tokens)
TEXT_TOKENS_GUESS = 400
PROMPT_TOKENS_GUESS = 4_500     # the cached system prompt, read at the cache-read rate
OUTPUT_TOKENS_GUESS = 1_500
SECS_PER_IMAGE_GUESS = 20
COST_CONFIRM_USD = 2.0          # Pokreni asks first from this estimate up


def fallback_cost_per_image(model: str) -> float:
    input_rate, output_rate, cache_read_rate = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return ((IMAGE_TOKENS_GUESS + TEXT_TOKENS_GUESS) * input_rate
            + PROMPT_TOKENS_GUESS * input_rate * cache_read_rate
            + OUTPUT_TOKENS_GUESS * output_rate) / 1_000_000


def estimate(stats, model: str, effort: str, count: int) -> tuple[float, float, bool]:
    """(USD, seconds, measured) for `count` photos: past runs' averages when there are any."""
    entry = stats.get(f"{model}|{effort}") if isinstance(stats, dict) else None
    try:
        if entry and entry["n"] > 0:
            cost = count * entry["cost"] / entry["n"]
            secs = count * entry["secs"] / entry["n"]
            if math.isfinite(cost) and math.isfinite(secs):   # json reads a hand-edited NaN/Infinity
                return cost, secs, True
    except (KeyError, TypeError, ArithmeticError):
        pass   # a hand-edited settings file, even a 400-digit number: fall back to the rough guess
    return count * fallback_cost_per_image(model), count * SECS_PER_IMAGE_GUESS, False


def record_run(stats, model: str, effort: str, cost: float, secs: float, n: int) -> dict:
    """Stats with one run of n photos added to its model/effort running sums."""
    stats = dict(stats) if isinstance(stats, dict) else {}
    if n <= 0:
        return stats
    key = f"{model}|{effort}"
    entry = stats.get(key) if isinstance(stats.get(key), dict) else {}
    fresh = {"cost": cost, "secs": secs, "n": n}
    try:
        total = {"cost": float(entry.get("cost", 0.0)) + cost,
                 "secs": float(entry.get("secs", 0.0)) + secs,
                 "n": int(entry.get("n", 0)) + n}
    except (TypeError, ValueError, OverflowError):
        total = fresh       # unreadable: start the entry over
    if not (math.isfinite(total["cost"]) and math.isfinite(total["secs"])):
        total = fresh       # a hand-edited NaN or Infinity never heals by adding to it
    stats[key] = total
    return stats


# ---- retry ------------------------------------------------------------------------------

def retry_settings(model: str, effort: str) -> tuple[str, str]:
    """Model and effort for "Ponovi byhand/": Opus 5.5 at high, or the selection if stronger."""
    rank = {m: i for i, m in enumerate(MODELS)}
    retry_model = model if rank.get(model, -1) > rank[RETRY_MODEL] else RETRY_MODEL
    stronger_effort = (effort in EFFORT_LEVELS
                       and EFFORT_LEVELS.index(effort) > EFFORT_LEVELS.index(RETRY_EFFORT))
    return retry_model, effort if stronger_effort else RETRY_EFFORT


# ---- summary ---------------------------------------------------------------------------

def tally_review_notes(csv_path: Path, flagged_files: set[str], limit: int = 5) -> list[tuple[str, int]]:
    """The most common Notes fragments among this run's PARTIAL/FAILED photos."""
    try:
        _header, rows = read_csv(csv_path)
    except (OSError, ValueError, csv.Error):
        return []
    counts: dict[str, int] = {}
    for row in rows:
        if len(row) <= FILE_INDEX or row[FILE_INDEX] not in flagged_files:
            continue
        for part in row[NOTES_INDEX].split("; "):
            part = part.strip()
            if part:
                counts[part] = counts.get(part, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:limit]


# ---- Windows ---------------------------------------------------------------------------

def scaled_geometry(width: int, height: int, scale: float, screen_w: int, screen_h: int) -> tuple[int, int]:
    """Window size for a DPI scale factor, never more than 90% of the screen."""
    return (min(round(width * scale), int(screen_w * 0.9)),
            min(round(height * scale), int(screen_h * 0.9)))
