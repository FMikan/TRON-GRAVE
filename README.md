# TRON-GRAVE

*A CLI tool for extracting burial records from tombstone photographs using AI vision.*

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey) ![License](https://img.shields.io/badge/License-MIT-green)

---

## What It Does

TRON-GRAVE processes a folder of tombstone photographs and uses the Anthropic Claude Vision API to extract burial records — names, surnames, birth years, and death years — into a structured CSV file. It is designed for genealogical research and graveyard digitisation projects. Images that cannot be read are flagged, logged, and copied into a review folder so no data is ever silently lost.

---

## Features

- Batch-processes entire folders of images (JPG, PNG, WEBP)
- Extracts Name, Surname, Year of Birth, and Year of Death per buried person
- Handles tombstones with multiple people — one CSV row per person, shared ID
- Derives a record ID from the image filename automatically (provided the filename follows the naming convention)
- Leaves fields empty rather than guessing when AI confidence is low
- Instructs the model to read only the foreground tombstone and ignore graves in the background
- Writes an `errors.txt` log with human-readable failure reasons
- Copies fully unreadable images to a `byhand/` folder for manual review
- Cross-platform: Windows 10+, Linux, and macOS
- API key loaded from a `.env` file — never hard-coded
- Configurable output directory, model selection, and verbosity via CLI flags
- Incremental CSV writing — partial results survive interruption (Ctrl-C, crash)

---

## Quick Start

> Quick Start uses Unix-style shell commands. On Windows, use WSL or Git Bash to run them as-is, or follow the native-PowerShell steps in [Installation](#installation).

```bash
# 1. Clone and install
git clone https://github.com/your-username/TRON-GRAVE.git
cd TRON-GRAVE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Add your API key
cp .env.example .env
# Open .env and paste your Anthropic API key

# 3. Run
python grave_extractor.py --input ./photos --output ./results
```

---

## File Naming Convention and ID Extraction

Images should follow this naming pattern; filenames that do not match are still processed via the fallback described below.

```
<GraveyardName>_<ID>_<Date>.<ext>
```

- Each of the three segments must be non-empty and must not contain an underscore
- `<ext>` must be one of `.jpg`, `.jpeg`, `.png`, or `.webp` (case-insensitive — e.g. `.JPG` is accepted)

**Examples:**

```
StMary_0042_20230815.jpg
OldChurch_0001_20240101.png
Riverside_0123_20231205.webp
```

The middle segment (`0042`, `0001`, `0123`) becomes the `ID` column in the CSV. Leading zeros are preserved — IDs are treated as strings, not integers.

**Regex used internally:**

```python
FILENAME_PATTERN = re.compile(r'^[^_]+_([^_]+)_[^_]+$')
```

**Fallback:** If a supported image's filename does not match the pattern, the full filename stem (everything before the extension) is used as the ID and a warning line is written to `errors.txt`. The image is still processed.

**Rejected cases:**

- **Non-matching filenames fall back to using the stem as the ID.** This includes filenames that contain the wrong number of underscores (e.g. `St_Mary_0042_20230815.jpg` has three underscores instead of two; `0042.jpg` and `StMary_0042.jpg` have fewer than two) and filenames with an empty segment (e.g. `StMary__20230815.jpg`). If your graveyard name naturally contains spaces or underscores, replace them with a different character (e.g. `St-Mary` or `StMary`) to get proper ID extraction.
- **Files with an unsupported extension** (anything other than `.jpg`, `.jpeg`, `.png`, `.webp`) are skipped entirely — they do not appear in the CSV or `errors.txt`.

---

## Output Formats

### `output.csv`

Columns in exact order:

| Column | Type | Notes |
|---|---|---|
| `ID` | string | Middle segment from filename, or full stem if the pattern did not match |
| `Name` | string | First name; empty if uncertain |
| `Surname` | string | Family name; empty if uncertain |
| `Year of Birth` | integer or empty | 4-digit year; empty if uncertain |
| `Year of Death` | integer or empty | 4-digit year; empty if uncertain |

The JSON produced by the model uses lower-snake-case keys (`name`, `surname`, `birth_year`, `death_year`) which map 1-to-1 onto these columns in the order above.

**Sample output:**

```csv
ID,Name,Surname,Year of Birth,Year of Death
0042,Maria,Kowalski,1881,1954
0043,Jan,Nowak,1902,1967
0043,Anna,Nowak,1905,1971
0044,,,,
0045,Thomas,Black,,1899
```

- Row `0043` appears twice — one tombstone, two people buried together
- Row `0044` is all-empty — the image was unreadable, but the ID is still recorded
- Row `0045` has birth year empty — it was illegible; death year was clear
- **File encoding: UTF-8 with BOM** (`utf-8-sig`), so Excel on Windows displays accented and non-Latin characters correctly. When re-reading in Python, pass `encoding='utf-8-sig'` to have the BOM stripped automatically; plain `encoding='utf-8'` leaves the BOM (decoded as `U+FEFF`) attached to the first column name.
- **Line endings:** the file is written with `newline=''` and Python's `csv.writer`, which emits `\r\n` terminators on every platform — consistent with RFC 4180 and what Excel expects.
- **Quoting:** `csv.QUOTE_MINIMAL` is used, so fields containing a comma, double-quote, or newline (e.g. `Smith, Jr.` or `O"Brien`) are double-quoted and embedded quotes are escaped as `""`. Plain ASCII and accented Latin names are written unquoted.

### `errors.txt`

One line per problematic image (not per person, not per field). Format:

```
<ID> -> <reason>
```

**Sample lines:**

```
0044 -> Image too dark to read any text
0045 -> Birth year illegible due to weathering; left empty
0046 -> File appears corrupted or truncated
0047 -> Tombstone partially obscured by vegetation; no names legible
0049 -> Filename did not match expected pattern; stem used as ID
```

These five lines correspond to: 3 total failures (`0044`, `0046`, `0047` — copied to `byhand/`), 1 partial read (`0045` — not copied), and 1 filename-only warning on an otherwise fully successful image (`0049`). The same run is summarised in the [Sample verbose output](#sample-verbose-output) below.

Rules:

- Any image with at least one empty field on any returned record produces exactly one error line summarising the issue.
- A filename-pattern fallback adds its own error line *in addition to* whatever line the image's read outcome produces. A fully-readable image with a malformed filename therefore appears once in `errors.txt` (for the filename) and has a complete row in `output.csv`.
- Images where every returned record is fully populated and whose filename matched the pattern do NOT appear in `errors.txt`.
- **File encoding: plain UTF-8 (no BOM).** Unlike `output.csv`, the error log is a text file consumed by humans and scripts — a BOM would get re-appended on every run and corrupt the first few bytes.
- Opened in append mode, so the file survives mid-run interruption and entries accumulate across runs (see [Re-running on the Same Output Directory](#re-running-on-the-same-output-directory)).
- `errors.txt` is only created when the first error or warning needs to be written. A clean run in which every image is fully read and every filename matches the pattern does not produce the file.
- **Parsing note:** if a `<reason>` string itself contains ` -> `, split on the *first* occurrence only.

### `byhand/` Folder

Contains copies (not moves) of images the tool could not process into any burial data. Files keep their original filenames so you can cross-reference them against the CSV and error log.

The copy is triggered by several distinct conditions — see [byhand/ Copy Logic](#byhand-copy-logic) for the exhaustive list. Broadly: the API returned no records, the API returned an explicit `error`, every returned record had every field null, the file was rejected locally before the API call (oversize or undecodable), a non-retryable API error occurred, or retries were exhausted. An image where even one field on one person was extracted counts as a partial read and is NOT copied — only logged in `errors.txt`.

---

## AI Confidence and Empty-Field Policy

TRON-GRAVE follows a **"better empty than wrong"** principle, which is critical for genealogical accuracy. **These are prompt-level instructions to the model — not enforced guarantees. Every CSV must be reviewed by a human before being treated as authoritative.**

**What the AI is instructed to do:**

- Read ONLY the tombstone that is in the foreground and in focus
- Ignore any other graves, monuments, or text visible in the background
- If a name is partially legible but one or more letters are ambiguous → leave the field empty
- If a year could plausibly be two different values → leave the field empty
- Never guess or estimate

**When a field is left empty:**

- The CSV cell is blank (not `"unknown"`, not `0`, not `null`)
- A corresponding line appears in `errors.txt` explaining what was unclear
- The image is copied to `byhand/` only if the whole image was unreadable (see [byhand/ Copy Logic](#byhand-copy-logic) for the exhaustive list of triggers)

**Why this matters:** Incorrect birth or death years propagate into family trees and cited genealogical records. A blank cell signals "needs review"; a wrong year looks like verified data.

---

## Installation

### Prerequisites (all platforms)

- Python 3.10 or later — [python.org](https://www.python.org/downloads/). On macOS, the system `python3` may be older than 3.10; prefer `brew install python` or a pyenv-managed version.
- `git` — [git-scm.com](https://git-scm.com/downloads)
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com/)

### Linux / macOS

```bash
git clone https://github.com/your-username/TRON-GRAVE.git
cd TRON-GRAVE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and paste your API key
```

### Windows (PowerShell)

```powershell
git clone https://github.com/your-username/TRON-GRAVE.git
cd TRON-GRAVE
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Open .env in Notepad and paste your API key
```

If `Activate.ps1` is blocked by execution policy, run once:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Verify the installation

```bash
python grave_extractor.py --help
```

This should print the CLI usage and exit with code 0. It verifies the environment (venv active, dependencies installed, script importable); it does **not** verify that your API key is valid — the key is only exercised on a real run. If you see `ModuleNotFoundError`, confirm that the virtual environment is activated.

### Optional: PyInstaller single-file bundle

```bash
pip install pyinstaller
pyinstaller --onefile grave_extractor.py
# Produces: dist/grave_extractor (Linux/macOS) or dist/grave_extractor.exe (Windows)
```

The `.env` file must be present in the current working directory when you invoke the bundled executable.

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```
ANTHROPIC_API_KEY=your_api_key_here
CLAUDE_MODEL=claude-sonnet-4-6
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Your Anthropic API key (starts with `sk-ant-`) |
| `CLAUDE_MODEL` | No | `claude-sonnet-4-6` | Model to use; `claude-opus-4-7` is more accurate but slower and costs more |

Model-selection precedence, highest first: `--model` CLI flag > `CLAUDE_MODEL` in `.env` > built-in default (`claude-sonnet-4-6`).

`.env` is listed in `.gitignore` and must never be committed. Do not pass the API key as a CLI argument — it would be recorded in shell history and visible in `ps`.

---

## Usage

### Basic

```bash
python grave_extractor.py --input ./photos --output ./results
```

### All CLI flags

| Flag | Required | Default | Description |
|---|---|---|---|
| `--input PATH` | Yes | — | Path to folder containing image files |
| `--output PATH` | No | `./output` (resolved relative to the current working directory, not `--input`) | Directory for `output.csv`, `errors.txt`, and `byhand/` |
| `--model MODEL` | No | `CLAUDE_MODEL` from `.env`, else `claude-sonnet-4-6` | Override the Claude model for this run |
| `--dry-run` | No | off | Print the list of images that would be processed (one per line) to stdout and exit. No API calls, no output files created, no images copied to `byhand/`. Files with unsupported extensions are omitted from the list. |
| `--verbose` | No | off | Print per-image progress to stdout (one line per image). Progress lines go to stdout; errors go to `errors.txt` regardless of this flag. |

`argparse` also provides `--help` automatically.

### Examples

List all images without processing:

```bash
python grave_extractor.py --input ./photos --dry-run
```

Use the more powerful model for a difficult batch:

```bash
python grave_extractor.py --input ./photos --output ./results --model claude-opus-4-7
```

Verbose progress:

```bash
python grave_extractor.py --input ./photos --output ./results --verbose
```

### Sample verbose output

```
[1/47] Processing StMary_0042_20230815.jpg ... OK (2 records)
[2/47] Processing StMary_0043_20230901.jpg ... OK (1 record)
[3/47] Processing StMary_0044_20231010.jpg ... FAILED (image too dark)
[4/47] Processing StMary_0045_20231010.jpg ... PARTIAL (birth year empty)
...
Done. 47 images processed. 43 succeeded, 1 partial, 3 failed.
Output:  ./results/output.csv
Errors:  ./results/errors.txt
Review:  ./results/byhand/ (3 images)
```

### Re-running on the Same Output Directory

- `output.csv` is **overwritten** on every run. Copy it elsewhere first if you need to preserve the previous CSV.
- `errors.txt` is opened in **append mode**, so entries from prior runs persist. This is intentional — it makes the log resilient to mid-run interruption. Between distinct runs, delete or rename the file if you want a clean slate.
- `byhand/` is not cleared between runs. `shutil.copy2` overwrites files with the same name, but images from prior runs that no longer fail remain.

**To re-run only failed images**, don't re-run the whole folder. Instead, copy the contents of the previous `byhand/` (plus any partially-failed filenames found in `errors.txt`) into a new input folder and run the tool against that folder with a fresh `--output`.

The simplest way to avoid stale state for a clean re-run is to use a fresh `--output` directory.

---

## Project Structure

Committed to the repository:

```
TRON-GRAVE/
├── README.md                  # This file — documentation and full application spec
├── LICENSE                    # MIT
├── requirements.txt           # anthropic, python-dotenv
├── .env.example               # Template for API key configuration
├── .gitignore                 # Excludes .env, .venv/, dist/, __pycache__/
├── grave_extractor.py         # CLI entry point; orchestrates the pipeline
└── extractor/
    ├── __init__.py
    ├── image_processor.py     # Sends images to Claude Vision API, parses JSON response
    ├── csv_writer.py          # Incremental CSV writing (utf-8-sig)
    ├── file_utils.py          # Filename regex, byhand/ copying, MIME type detection
    └── error_logger.py        # Append-mode errors.txt writer
```

Created at runtime inside the `--output` directory:

```
<output>/
├── output.csv                 # Created at the start of every run (overwrites existing)
├── errors.txt                 # Created only if at least one image produces a warning or failure
└── byhand/                    # Created only when the first image needs to be copied
```

---

## Technical Specification

This section is the complete developer reference for implementing TRON-GRAVE from scratch.

### Image Encoding for the Anthropic API

Claude Vision accepts images as base64-encoded data inside a user-message content block:

1. Read file bytes from disk
2. Detect MIME type from extension: `image/jpeg` (`.jpg`, `.jpeg`), `image/png` (`.png`), `image/webp` (`.webp`)
3. Base64-encode the bytes
4. Embed the result in a `user` message alongside the task text (the full call uses `client.messages.create(model=..., max_tokens=..., system=<system prompt>, messages=[{"role": "user", "content": [<image block>, <text block>]}])`):

```python
{
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "<base64-encoded bytes>"
    }
}
```

**Size limit:** The Anthropic Vision API enforces a 5 MB per-image limit **measured on the base64-encoded payload**. Because base64 expands bytes by roughly 4/3, any file whose on-disk size is greater than ~3.75 MB may exceed the API limit after encoding. The tool checks the on-disk file size (`os.path.getsize`) before the API call and rejects files over **3.75 MB on disk** locally: they are logged to `errors.txt` with reason `"Image file too large for API (>5MB)"` and copied to `byhand/`. An empty row (ID only) is written to `output.csv`. (The API may also downsize accepted images server-side; this is transparent to the tool and does not require local action.)

### AI Prompt Design

Send the instructional text as the **system prompt** and the image plus a short trigger line as the **user message**. Keeping the instructions in the system role reduces the chance that the model treats them as part of the image context and makes them easier to version.

System prompt:

```
You are a genealogical data extraction assistant. You will be shown a photograph of a tombstone.

Your task:
1. Read ONLY the tombstone that is in the foreground and in focus. Ignore any other graves,
   monuments, or text visible in the background.
2. Extract the following fields for each person commemorated on this tombstone:
   - First name (Name)
   - Family name (Surname)
   - Year of birth (4-digit year only)
   - Year of death (4-digit year only)
3. If a tombstone commemorates more than one person, return one record per person.
4. If you are not 100% certain about a field, set it to null. Do NOT guess or estimate.
5. If the image is completely unreadable, return an empty records array and explain why.

Respond ONLY with valid JSON in this exact format — no markdown, no explanation:
{
  "records": [
    {"name": "Maria", "surname": "Kowalski", "birth_year": 1881, "death_year": 1954},
    {"name": "Jan",   "surname": "Kowalski", "birth_year": null, "death_year": 1960}
  ],
  "error": null
}

If the image cannot be read at all, respond with:
{
  "records": [],
  "error": "Brief plain-English description of why the image could not be read"
}
```

User message: the base64 image block followed by a text block such as `"Extract the burial records from this tombstone."`.

Because models occasionally wrap JSON in a ```` ```json ```` fence despite the instruction, the parser strips any surrounding code fence before decoding. A more robust production alternative is to use Anthropic's tool-use feature: define a single `record_extraction` tool with a strict JSON schema, and force the model to call it — the SDK then returns structured `input` with no fence-stripping needed. The MVP uses text-JSON parsing for simplicity.

### Response Parsing

```python
import json
import re

_FENCE_RE = re.compile(
    r'\A\s*```(?:json)?\s*\n(.*?)\n\s*```\s*\Z',
    re.DOTALL,
)

def parse_response(text: str) -> tuple[list[dict], str | None]:
    m = _FENCE_RE.match(text)
    cleaned = m.group(1) if m else text.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return [], "Model returned malformed JSON"
    records = data.get("records", [])
    if not isinstance(records, list):
        return [], "Model returned non-list 'records' field"
    return records, data.get("error")
```

The fence regex is anchored to the full string (`\A` / `\Z`) so it only fires when the *entire* response is wrapped, not when backticks appear in a legitimate field value.

Result classification:

| Condition | Action |
|---|---|
| `records == []` and `error is None` | Total failure; log "Model returned no records" and copy to `byhand/` |
| `error` is non-null | Total failure; log the `error` string and copy to `byhand/` |
| Every record has every field `null` (or the key missing) | Total failure; log "All fields illegible" and copy to `byhand/` |
| Any record has any `null` / missing field but not all | Partial success; write CSV rows (null or missing → empty cell) and log to `errors.txt` |
| All records fully populated | Full success; write CSV rows, no error log entry |
| `records` present but not a list | Total failure; log "Model returned non-list 'records' field" and copy to `byhand/` |
| `json.JSONDecodeError` | Total failure; log "Model returned malformed JSON" and copy to `byhand/` |

Missing keys inside a record and explicit `null` are treated identically: `record.get("birth_year")` returns `None` in both cases, which becomes an empty CSV cell.

Error handling for network and API failures:

| Exception | Action |
|---|---|
| `anthropic.RateLimitError` (429) | Retry with exponential backoff |
| `anthropic.APIConnectionError` (transient network) | Retry with exponential backoff |
| `anthropic.APITimeoutError` | Retry with exponential backoff |
| `anthropic.APIStatusError` with 5xx status | Retry with exponential backoff |
| `anthropic.APIStatusError` with other 4xx (e.g. 400, 401, 403) | Non-retryable; log reason and copy to `byhand/` |
| All retries exhausted | Log the last error and copy to `byhand/` |

**Retry schedule:** 3 retries on top of the initial call (4 total attempts). Base delays `2s, 4s, 8s`; add ±25% random jitter to each delay to avoid thundering-herd retries against the API when a batch of images hits the same rate-limit window.

### CSV Writing

```python
import csv

# Run setup (ONCE per run — not inside the image loop):
with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(['ID', 'Name', 'Surname', 'Year of Birth', 'Year of Death'])

# Per-image append (ONE open() per image, inside the main loop):
with open(output_path, 'a', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    for row in rows_for_image:
        writer.writerow(row)
# The file is flushed and closed at the end of the `with` block,
# so a Ctrl-C between images preserves everything written so far.
```

- **Encoding: `utf-8-sig`** — the UTF-8 BOM variant. Excel on Windows requires this to correctly display accented and non-Latin characters (e.g. Ą, Ž, Ó).
- `null` / `None` / missing field values → written as an empty string (blank cell in CSV).
- `csv.QUOTE_MINIMAL` quotes fields that contain a delimiter, quote character, or newline, and escapes embedded quotes as `""` — safe for names like `Smith, Jr.` or `O'Brien`.
- Opening once per image (rather than once per row) keeps I/O bounded while preserving crash safety between images.

### Filename Parsing (`file_utils.py`)

```python
import re
from pathlib import Path

# Kept in sync with the regex shown in the "File Naming Convention" section above.
FILENAME_PATTERN = re.compile(r'^[^_]+_([^_]+)_[^_]+$')
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS

def extract_id(path: Path) -> tuple[str, bool]:
    """Return (record_id, matched).

    matched=False means the filename did not fit the expected pattern and the
    full stem was used as a fallback. The orchestration (step 1 in Pipeline
    Orchestration) is responsible for logging the warning in that case.
    Precondition: is_supported_image(path) is True.
    """
    match = FILENAME_PATTERN.match(path.stem)
    if match:
        return match.group(1), True
    return path.stem, False
```

Leading zeros are always preserved — IDs are never cast to `int`.

Unsupported extensions are filtered out before `extract_id` is called and never appear in the CSV or error log.

### Error Logger (`error_logger.py`)

```python
def log_error(errors_path: Path, record_id: str, reason: str) -> None:
    with open(errors_path, 'a', encoding='utf-8') as f:
        f.write(f"{record_id} -> {reason}\n")
```

Opened in append mode (`'a'`) so the file survives script interruption within a run. No timestamps in the MVP; if you need them, prefix the `reason` in the caller.

### byhand/ Copy Logic

```python
import shutil

def copy_to_byhand(src: Path, byhand_dir: Path) -> None:
    byhand_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, byhand_dir / src.name)
```

**Copy, not move** — originals remain in the input folder.

**When to copy to byhand/ (any one of these):**

- The API returned no records (`records: []`)
- The API returned a non-null `error`
- Every returned record had every field `null`
- The API returned a non-list `records` field
- File is over ~3.75 MB on disk (would exceed the 5 MB base64 limit; detected locally before the API call)
- File could not be opened or decoded on disk
- A non-retryable API error (e.g. 400, 401, 403) was returned
- All retries on a retryable error were exhausted

**When NOT to copy to byhand/:**

- Partial success: at least one field on at least one record was extracted (logged to `errors.txt` only)

### Pipeline Orchestration (`grave_extractor.py`)

```
for each file in --input:
    0. if not is_supported_image(file): skip silently (no CSV row, no error)
    1. record_id, matched = extract_id(file)
       if not matched: log warning to errors.txt
    2. if os.path.getsize(file) > 3_750_000:  # ~5 MB after base64 encoding
           write empty CSV row (id only)
           log "Image file too large for API (>5MB)" to errors.txt
           copy to byhand/
           continue
    3. encode image as base64
    4. call Claude Vision API with retry on:
       - anthropic.RateLimitError
       - anthropic.APIConnectionError
       - anthropic.APITimeoutError
       - anthropic.APIStatusError with 5xx status
    5. parse JSON response
    6. classify result (see Response Parsing table) and dispatch:
       - total failure    → write empty CSV row, log to errors.txt, copy to byhand/
       - partial success  → write CSV rows with null fields as empty, log to errors.txt
       - full success     → write CSV rows (do not log to errors.txt)
```

Processing is **sequential** (one image at a time). This keeps the implementation simple and avoids rate-limit complexity. At roughly 3–6 seconds per image, a 1,000-image batch takes on the order of 1–2 hours end to end. A future version may use `concurrent.futures.ThreadPoolExecutor` for parallel API calls.

### Runtime Behavior

- **Model parameters:** calls use `temperature=0` for the most deterministic output the API offers, and no fixed seed (seeds are not exposed by the Messages API). Runs are near-reproducible but not bit-for-bit; small wording differences in `error` strings and borderline field decisions are expected.
- **Model fallback:** an invalid `CLAUDE_MODEL` or `--model` value is passed to the API unchanged and will cause `anthropic.APIStatusError` with a 404 on the first image. This is treated as a fatal error (exit code `1`) rather than a per-image failure.
- **Streams:** verbose progress and the end-of-run summary go to **stdout**; unexpected Python exceptions and the one-line fatal reason go to **stderr**; per-image errors go to `errors.txt`.
- **Signals:** SIGINT (Ctrl-C) aborts the current image and exits after the active `with open(...)` block completes; already-written CSV rows and error lines are preserved. The conventional SIGINT exit code `130` is used.

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Completed successfully with zero problematic images |
| `2` | Completed, but one or more images failed or had empty fields — see `errors.txt` |
| `1` | Fatal error — input folder not found, API key missing, output directory not writable, or first API call returned an unrecoverable error (e.g. invalid model, 401) |
| `130` | Interrupted by SIGINT (conventional) |

### `requirements.txt`

```
anthropic>=1.0.0,<2.0.0
python-dotenv>=1.0.0
```

All other modules (`csv`, `re`, `pathlib`, `base64`, `json`, `argparse`, `shutil`, `os`) are Python standard library.

### `.env.example`

Identical to the template shown in [Configuration](#configuration).

---

## Privacy and Security

- **Images are sent to Anthropic.** Every processed photograph is uploaded to the Anthropic API. If your dataset contains personally identifiable information beyond what is carved on the stone, or if you operate under a jurisdiction with strict data-handling rules (e.g. GDPR for EU cemeteries), review Anthropic's [usage policies](https://www.anthropic.com/legal) before processing. As of 2026, Anthropic does not train models on API inputs by default; see their current policies for authoritative detail.
- **API key handling.** The key is loaded from `.env` at runtime and is never written to `output.csv`, `errors.txt`, or stdout (including under `--verbose`). Keep `.env` out of version control (the provided `.gitignore` excludes it). Avoid passing the key as a CLI argument, as it would be recorded in shell history and visible to other users via `ps`.
- **Output files contain burial data.** Treat `output.csv`, `errors.txt`, and the contents of `byhand/` with whatever confidentiality your project requires.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'anthropic'`**
The virtual environment is not activated, or `pip install -r requirements.txt` was not run inside it. Re-activate and re-install.

**`anthropic.AuthenticationError` or HTTP 401**
`ANTHROPIC_API_KEY` is missing, malformed, or revoked. Confirm `.env` is in the directory you are running from and that the key starts with `sk-ant-`.

**PowerShell: `.venv\Scripts\Activate.ps1 cannot be loaded because running scripts is disabled`**
Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once in PowerShell.

**CSV opens with garbled characters in Excel**
Confirm the file starts with a BOM (the tool writes `utf-8-sig` by default). Older Excel versions may still mis-detect; open via *Data → From Text/CSV* and choose UTF-8 explicitly.

**Many images fail with "Model returned malformed JSON"**
Capture a failing response with `--verbose` and inspect it. If the model is wrapping JSON in an unusual way the fence-stripper can't handle, tighten `_FENCE_RE` to match the observed pattern or migrate the implementation to the tool-use alternative mentioned in [AI Prompt Design](#ai-prompt-design). Switching to `--model claude-opus-4-7` sometimes helps on ambiguous images but is not a fix for malformed JSON specifically — it's a more expensive model making the same structural mistake less often.

**Persistent HTTP 429 (rate-limit) errors**
The built-in backoff handles short bursts on a single sequential run. For sustained limits: slow the average request rate (increase the base retry delay in the source), process the input in several runs spaced hours apart, or upgrade your Anthropic account tier. Do **not** run multiple copies of the tool against the same API key in parallel — the tool is deliberately sequential specifically to avoid compounding rate-limit pressure.

**iPhone photos (`.heic`) are not processed**
HEIC/HEIF is not supported by TRON-GRAVE. Convert to JPG first. On macOS, open in Preview and *File → Export*. On Linux, the `heif-convert` binary ships in a package whose name varies by distro (`libheif-examples` on Debian/Ubuntu, `libheif-tools` on some older Ubuntus, `libheif` on Fedora/Arch) — `ImageMagick` (`convert in.heic out.jpg`) or `ffmpeg` are distro-agnostic alternatives.

---

## Known Limitations

- **API costs:** order-of-magnitude $0.003–$0.015 per image depending on model and image resolution, as of 2026. Check [Anthropic pricing](https://www.anthropic.com/pricing) for current per-token rates before large runs.
- **Image size:** files over ~3.75 MB on disk (which would exceed the API's 5 MB base64 limit after encoding) are not sent to the API and are routed to `byhand/`.
- **Image formats:** only JPG, PNG, and WEBP are accepted. HEIC/HEIF (default iPhone format), TIFF, BMP, and GIF must be converted first.
- **Non-Latin scripts:** Cyrillic, Hebrew, Arabic, and other scripts are supported by Claude Vision but may have lower accuracy on weathered stone.
- **Malformed filenames:** the regex requires exactly two underscores separating three non-empty segments; anything else (too few, too many, or empty segments) falls back to using the full stem as the ID.
- **No deduplication:** if the same image appears twice in the input folder, duplicate rows will be written to the CSV.
- **Re-running output:** `output.csv` is overwritten each run; `errors.txt` and `byhand/` accumulate. Use a fresh `--output` directory for clean runs.
- **No concurrent runs against one output directory:** two processes writing `output.csv` or copying into `byhand/` at the same time will corrupt the output. Give each run its own `--output`.
- **Disk usage:** `byhand/` holds full copies of every unreadable image, so a batch with many failures roughly doubles disk usage. Budget accordingly.
- **No image pre-processing:** the tool sends images as-is; heavy JPEG compression, very low resolution, or photos captured sideways (the raw pixels are sent without applying EXIF orientation) can reduce accuracy.
- **Sequential only:** one image at a time; plan for ~3–6 seconds per image (e.g. a 1,000-image batch takes on the order of 1–2 hours).
- **Model non-determinism:** `temperature=0` gets close but the Messages API does not expose a seed, so runs are not bit-for-bit reproducible. Human review is required before treating the CSV as authoritative.

---

## Contributing

Contributions are welcome via pull request. For bug reports, open a GitHub Issue.

**Development setup:**

```bash
pip install -r requirements.txt
```

No test runner is configured in the initial release. `pytest` is the recommended framework; tests should cover at minimum:

- Filename regex extraction — valid pattern, fallback for any non-matching shape (wrong underscore count, empty segments, leading dots, Unicode stems), unsupported extensions
- JSON response parsing — valid records, null fields, missing keys, empty records, non-list `records`, malformed JSON, code-fenced responses
- CSV row writing — multi-person tombstone, all-empty row, UTF-8 special characters, BOM round-trip, names containing commas and quotes
- Retry handling for `RateLimitError` (429), `APIConnectionError`, `APITimeoutError`, and `APIStatusError` with 5xx; non-retryability of other 4xx errors

---

## License

MIT License — see [LICENSE](LICENSE) for details.
