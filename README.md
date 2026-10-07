# TRON-GRAVE

**Automated tombstone inscription extractor for genealogical research.**

TRON-GRAVE uses the Claude Vision AI to read photographs of gravestones and extract structured burial data — names, surnames, birth years, and death years — into a CSV file ready for import into spreadsheets or genealogical databases.

Designed for digitizing Croatian cemetery records, with full support for Croatian characters (č, ć, š, đ, ž) and automatic Cyrillic-to-Latin transliteration.

---

## Features

- **AI-powered OCR** — reads tombstone inscriptions using Claude Vision (Anthropic API)
- **Model selection** — pick Claude **Sonnet 5** (the default), Sonnet 5.5, Opus 5, Opus 5.5, Fable 5 or Fable 5.1 from the GUI dropdown, or any model that supports structured outputs via `--model`
- **Effort control** — pick how hard the model works per image from the **Napor** dropdown (*nizak*, *srednji*, *visok*, *vrlo visok*, *maksimalan*) or with `--effort` (`low` → `max`); the choices adapt to the selected model
- **Multi-person tombstones** — extracts records for each person on a single stone
- **Multi-marker graves** — reads *all* plaques, headstones and crosses that belong to one grave, instead of stopping at the first one. Markers count as one grave only when they share a physical structure (common frame, border, curb, foundation or base, or are touching); a shared surname or carving style is corroborating evidence but never sufficient on its own, since whole rows of same-surname family graves are common. Ambiguous neighbours are flagged for manual review rather than guessed
- **Conservative extraction** — leaves fields blank rather than guessing uncertain data
- **Smart year handling** — for both birth and death years, distinguishes a *certain* absence (e.g. person still living, or no birth date inscribed) from an *unreadable* year, so records with a legitimately missing year are passed as **OK** instead of being needlessly flagged for review
- **Prompt caching** — the shared instructions are cached after the first image and reused for the rest of the run, cutting the per-image system-prompt cost by ~90% for the whole batch. The prompt is well above the cache minimum on all six offered models (1024 tokens on Sonnet 5, 512 on the other offered models), so this applies whichever you pick
- **Batch processing** — processes entire folders of images automatically
- **Photo normalisation** — every photo is fully decoded (truncated files fail cleanly), rotated by its EXIF tag (the API ignores image metadata), shrunk to the 2576 px the model actually uses (so 48–200 MP phone photos work) and sent in its real format; a photo that needs none of that goes out untouched
- **Manual review queue** — copies images needing review to a `byhand/` folder for manual inspection (emptied when a fresh run starts; photos whose API call failed are not copied — resume retries them)
- **Notes column** — every edge case (uncertain year, missing field, non-standard filename) is explained inline in the CSV's `Notes` column
- **Real-time progress & live cost counter** — GUI shows progress bar, the photo being processed, ETA, and the *real* running API cost (computed from each response's actual token usage, not an estimate). The log reads in Croatian, coloured by verdict (OK / za pregled / neuspjelo); **Tehnički zapis** switches it to the extractor's own lines
- **End-of-run summary** — a popup with OK / for-review / failed counts, the most common review reasons among this run's flagged photos, the total cost, and a button to open the CSV; the window comes back to the front (taskbar flash on Windows)
- **Resume interrupted runs** — when the output folder already has an output.csv, **Pokreni** asks **Nastavi** (resume) / **Prepiši** (move output.csv, byhand/ and byhand_retry/ aside and start over) / **Odustani**; a resume never re-sends a photo the model already answered
- **Ponovi byhand/** — re-runs just the `byhand/` photos with Claude Opus 5.5 at high effort, or your selected model/effort if stronger, into `byhand_retry/`; it asks before touching earlier retry results and can be resumed
- **Automatic retries** — retries failed API calls with exponential backoff; the run stops (and can be resumed later) on a billing error, the monthly spend cap, or three API failures in a row
- **Settings persistence** — remembers folders, model and effort; the API key is stored only when you click **Spremi**, in an owner-only file (`%APPDATA%\tron-grave` on Windows, `~/Library/Application Support/tron-grave` on macOS, `~/.config/tron-grave` on Linux — or `$XDG_CONFIG_HOME/tron-grave` when that is set)
- **Dry-run mode** — preview image discovery without making any API calls (**Probni prolaz (samo popis)** in the GUI, `--dry-run` on the CLI)
- **Croatian & Cyrillic support** — outputs in Croatian with automatic Cyrillic transliteration
- **Croatian GUI** — every label, dialog and status message

**Supported image formats:** `.jpg`, `.jpeg`, `.png`, `.webp`
> Note: iPhone HEIC/HEIF photos must be converted to JPG first (the app reports how many it skipped).

---

## Output

For each processed folder, TRON-GRAVE creates:

**`output.csv`** — UTF-8 with BOM (Excel-compatible)
```
ID,Name,Surname,Year of Birth,Year of Death,Notes,File
305,Ivan,Horvat,1921,1987,,pokojnici-ploca_305_22-07-2026_11-29-39.jpg
305,Marija,Horvat,1925,,bez god. smrti,pokojnici-ploca_305_22-07-2026_11-29-39.jpg
306,Petar,Kovač,1940,,god. smrti nečitka,pokojnici-ploca_306_22-07-2026_11-31-02.jpg
```

The **`ID`** is the second underscore-separated part of the filename
(`pokojnici-ploca_305_22-07-2026_11-29-39.jpg` → `305`). A grave photographed more than
once yields several rows sharing that ID, which is what keeps a grave's markers grouped
together in the output. When the name does not fit that pattern, or the ID position holds a
date/time stamp instead of an ID, the whole filename (without extension) becomes the ID and the
row gets the `ID iz naziva` note.

**`File`** is the photo a row came from, so every row can be traced to its photo in `byhand/` and
matched against the retry CSV.

Opening in Excel: the file is comma-separated UTF-8. If Excel puts everything into column A
(Croatian regional settings expect `;`), open it with **Data → From Text/CSV** and choose *Comma*.

The **`Notes`** column (Croatian) is filled only for edge cases and is the single place
to look when reviewing results. Typical notes:

| Note | Meaning | Sent to `byhand/`? |
|------|---------|--------------------|
| *(empty)* | Full record, nothing to review | No |
| `bez god. smrti` | Model is certain there is no year of death (e.g. person still living) | No — counts as **OK** |
| `bez god. rođenja` | Model is certain there is no year of birth inscribed | No — counts as **OK** |
| `bez god. rođ. i smrti` | Model is certain neither a birth nor a death year exists | No — counts as **OK** |
| `god. rođ. izvedena` / `god. smrti izvedena` | The year was not legible as carved but was derived with high confidence (e.g. death year minus age at death), so it may be off by ±1 | No — counts as **OK** |
| `god. smrti nečitka` / `god. rođenja nečitka` | A year may exist but could not be read confidently | Yes — **PARTIAL** |
| `fali: ime` / `fali: prezime` | Name or surname is illegible | Yes — **PARTIAL** |
| `provjeri godine` | The birth year is after the death year; both are kept | Yes — **PARTIAL** |
| `provjeri: možda više oznaka` | Nearby plaques/crosses might belong to the same grave; the model left them out to be safe — check for missed people | Yes — **PARTIAL** |
| the model's own sentence, e.g. `natpis djelomično oštećen` | The model reported a problem with the photo | Yes — **PARTIAL** (**FAILED** if it read nothing) |
| `sve nečitko`, `nema podataka` | Nothing could be extracted | Yes — **FAILED** |
| `odgovor prekinut` | The model's reply hit the token ceiling before it finished; nothing from it is used (the call is billed, the photo counts as processed and goes to `byhand/`) | Yes — **FAILED** |
| `odbijeno` | The model declined to answer | Yes — **FAILED** |
| `neispravan odgovor` | The model's answer was not valid JSON | Yes — **FAILED** |
| `neočekivana greška` | An unexpected error on this photo; the run carried on with the next one | Yes — **FAILED** |
| `ne mogu otvoriti` | The file could not be read or decoded (truncated, corrupt, a HEIC renamed to .jpg) | Yes — **FAILED** |
| `greška API-ja` | The API call failed (not billed) | No — resume retries it (**FAILED**) |
| `… ID iz naziva` | The ID came from the whole filename stem rather than the expected `<prefix>_<ID>_<rest>` pattern — either the filename did not match it at all, or the ID position held a date/time stamp instead of an ID (`IMG_20240513_142233`, `photo_2024-05-13_14-22-33`… — taking `20240513` or `2024-05-13` as the ID would put a whole day's shoot under one ID) | No (appended to any note) |

System tags come first and are never shortened; the model's own note comes last, capped at 120 characters.

**`byhand/`** — copies of images flagged for manual review (PARTIAL or FAILED rows above, except
photos whose API call failed — resume retries those)

**`.processed`** — bookkeeping for resume: one filename per line for every photo the model
answered, whatever the verdict, so resume never pays for it twice. Rewritten by every fresh run.
If it is missing while output.csv has rows, resume refuses to run rather than re-send everything.

**`byhand_retry/`** — results of **Ponovi byhand/**, with its own `output.csv`, `.processed` and `byhand/`

**`output.<time>.bak.csv`**, **`byhand.<time>.bak/`**, **`byhand_retry.<time>.bak/`** — what **Prepiši** (or `--overwrite` on the CLI) set aside (`<time>` is `YYYYMMDD-HHMMSS`)

> There is no longer a separate `errors.txt`; all review information now lives in the `Notes` column.

---

## Requirements

- An **Anthropic API key** (see [Getting an API Key](#getting-an-api-key) below)
- Images of tombstones in JPG, PNG, or WebP format

---

## Getting an API Key

TRON-GRAVE uses the **Anthropic Claude API** to analyze tombstone images. You need an API key to use it.

> **Note:** every photograph you process is uploaded to Anthropic's API for analysis. Nothing is
> sent anywhere else, and the extracted CSV stays on your machine, but the images themselves do
> leave your computer. See Anthropic's [privacy policy](https://www.anthropic.com/legal/privacy)
> for how they handle API data.

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Add a payment method (pay-as-you-go — no subscription required)
3. Navigate to **API Keys** in the left sidebar
4. Click **Create Key**, give it a name, and copy the key
5. Paste the key into the **API ključ** field of TRON-GRAVE and click **Spremi** (GUI), or into your `.env` file (CLI)

**Estimated cost per image** (pre-run preview shown in the GUI):

| Model | Price per MTok (input / output) | Est. cost/image (first run) |
|---|---|---|
| Claude Sonnet 5 (`claude-sonnet-5`) | $2 / $10 | ~$0.03 |
| Claude Sonnet 5.5 (`claude-sonnet-5-5`) | $2 / $10 | ~$0.03 |
| Claude Opus 5 (`claude-opus-5`) | $5 / $25 | ~$0.07 |
| Claude Opus 5.5 (`claude-opus-5-5`) | $4 / $20 | ~$0.05 |
| Claude Fable 5 (`claude-fable-5`) | $10 / $50 | ~$0.13 |
| Claude Fable 5.1 (`claude-fable-5-1`) | $10 / $50 | ~$0.13 |

First-run guesses assume ~4,700 input tokens per photo (after the API's own downscale) and ~1,500
output tokens; after a run the GUI estimates from your real cost and time per photo for that model
and effort. The GUI's preview line labels the first-run guess *gruba procjena* (rough estimate)
and counts 20 s per photo; once a finished or stopped run with that model and effort has taught
it, it uses those averages instead and reads *prema prošlim obradama* (from past runs). It also
says how many HEIC/HEIF files it will skip.

These are only for the *before-you-start* estimate. Once a run is going, the status bar and the
end-of-run summary show the **real** cost, computed from each API response's actual token usage
(including prompt-cache discounts) — not an estimate.

All six offered models accept all five **effort** levels. The GUI always sends the level you pick in
**Napor** (**Ponovi byhand/** raises a lower one to `high`); the CLI sends none unless you pass
`--effort`, and the model then uses its own default. Higher levels (`high` → `xhigh` → `max`) make
the model reason harder per image at higher token cost; drop to `low`/`medium` for cheaper, faster
runs.

For Sonnet 5.5, Opus 5.5 and the Fable models the request leaves out the written "reasoning" step
(the model's unsaved working notes before it fills in the records), because those models decline a
prompt that asks them to write out their reasoning; Sonnet 5 and Opus 5 keep it.

---

## Installation & Running

### Windows — Prebuilt Executable (recommended)

1. Go to the [Releases](../../releases) page
2. Download the latest `TRON-GRAVE.exe`
3. Double-click to run — no Python or installation required. Windows may show *Windows protected your PC* for the downloaded exe (it is not code-signed): click **More info → Run anyway**.
4. Paste your Anthropic API key into **API ključ** and click **Spremi**
5. (Optional) Pick a **Model** (Sonnet 5 is the default) and a **Napor** (effort) level
6. Select your input folder (photos) and output folder, then click **▶ Pokreni**

---

### Linux

**1. Install system dependencies**
```bash
sudo apt install python3-tk python3-venv   # Debian/Ubuntu
# or
sudo dnf install python3-tkinter           # Fedora
```

**2. Clone the repository**
```bash
git clone https://github.com/FMikan/TRON-GRAVE.git
cd TRON-GRAVE
```

**3. Create a virtual environment and install dependencies**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**4. Configure your API key**
```bash
cp .env.example .env
nano .env
```
Set the value:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**5. Run the GUI**
```bash
python grave_ui.py
```

Or use the CLI for batch processing:
```bash
python grave_extractor.py --input /path/to/photos --output /path/to/results
```

---

### macOS

**1. Install Python 3.10+**

Download from [python.org](https://www.python.org/downloads/) or use Homebrew:
```bash
brew install python
```

**2. Clone the repository**
```bash
git clone https://github.com/FMikan/TRON-GRAVE.git
cd TRON-GRAVE
```

**3. Create a virtual environment and install dependencies**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**4. Configure your API key**
```bash
cp .env.example .env
nano .env
```
Set the value:
```
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**5. Run the GUI**
```bash
python grave_ui.py
```

> On macOS, Tkinter is bundled with the official Python installer from python.org. If you installed Python via Homebrew and Tkinter is missing, install `python-tk` via Homebrew: `brew install python-tk`.

---

## CLI Reference

```
python grave_extractor.py [OPTIONS]

Options:
  --input   PATH     Folder containing tombstone images (required)
  --output  PATH     Folder where results will be saved (default: ./output)
  --model   NAME     Claude model to use (default: CLAUDE_MODEL env, else claude-sonnet-5).
                     Accepts any model id, not just the ones in the GUI dropdown, but needs a
                     model that supports structured outputs.
                     GUI values: claude-sonnet-5, claude-sonnet-5-5, claude-opus-5,
                     claude-opus-5-5, claude-fable-5, claude-fable-5-1
  --effort  LEVEL    Reasoning effort: low | medium | high | xhigh | max (default: model's own).
                     All six GUI models accept all five levels.
  --resume           Skip photos listed in the output folder's .processed file and append to
                     output.csv. Refuses (exit 1, error: [resume-refused]) when that would
                     re-send finished photos: an output.csv with other columns (from an older
                     version, or re-saved from Excel), output.csv rows without .processed, or
                     .processed without output.csv (or with an output.csv emptied to its
                     header); an unreadable output.csv, or one that is no longer UTF-8
                     (re-saved as ANSI by Excel or an editor), is refused too.
                     With --dry-run, lists only the photos that would run.
  --overwrite        Start fresh even though output.csv has rows: output.csv, byhand/ and
                     byhand_retry/ are first moved aside as output.<time>.bak.csv,
                     byhand.<time>.bak and byhand_retry.<time>.bak. Without it (or --resume) a
                     run refuses an output folder whose output.csv has rows.
  --verbose          Show detailed per-image progress
  --dry-run          List discovered images without making any API calls
```

The model can also be set with the `CLAUDE_MODEL` environment variable; the `--model` flag takes precedence.

Warnings go to stderr and do not change the exit code, e.g.
`warning: skipping N .heic/.heif file(s); convert them to JPG first` (iPhone photos the API cannot
take) and `warning: no price table entry for <model>` (a `--model` with no known price: the costs
shown assume a default of $3 input / $15 output per million tokens, so treat them as a guess).

Verbose output prints two lines per image, one when it starts and one with its verdict (`OK`,
`PARTIAL` or `FAILED`). The verdict line of every photo the model answered also shows the real
cost of that call and the running total, e.g.:
```
[12/300] Processing img012.jpg ...
[12/300] OK: img012.jpg (1 record) — $0.0184 (total: $2.35)
```
and the closing lines report the run's total: `Total cost: $2.35`.

**Exit codes:**
- `0` — Every image processed cleanly, nothing flagged
- `2` — Finished, but with something worth looking at: an image failed, a field was missing,
  **or** an ID had to be taken from the filename (the `ID iz naziva` note). `2` means
  "run completed, check the Notes column" — not that the run broke.
- `1` — Fatal error: the run stopped early (missing/invalid API key, billing error, monthly spend
  cap, no access to the model or no such model, three API failures in a row, output.csv locked
  (e.g. open in Excel), resume refused, input folder is the output's `byhand/`, no supported
  images in the input folder, an output.csv with rows and neither `--resume` nor `--overwrite`,
  unwritable output folder); stderr then reads `error: [tag] …`, where the tag is `api-401`,
  `api-402`, `api-403`, `api-404`, `spend-cap`, `api-down`, `csv-locked`, `resume-refused`,
  `input-is-byhand`, `no-images` or `output-exists` (a missing key or input folder, or an
  unwritable output folder, prints a plain `error: …`)
- `130` — Interrupted by user (Ctrl+C)

---

## Resuming & Retrying

**Resume an interrupted run.** If a run is stopped (Ctrl+C, the GUI's **Zaustavi** button, closing
the window, or a crash), start it again on the same output folder. In the GUI, when that folder
already has an `output.csv`, **Pokreni** asks **Nastavi** (resume) / **Prepiši** (move `output.csv`,
`byhand/` and `byhand_retry/` aside and start over) / **Odustani**; on the CLI, pass `--resume`. Resume reads the
`.processed` file, skips the photos listed there, and appends the rest to `output.csv` instead of
overwriting it, so you don't pay to reprocess photos the model already answered. Photos that got
no answer (for example the API call failed or the file could not be read) are left out of
`.processed` deliberately, so a resume retries them rather than writing them off. **Nastavi** is
greyed out, with the reason, when a resume would be refused (see `--resume` in the CLI reference).

Closing the window during a run asks first, then stops the run the same way as **Zaustavi**; the
window closes once the run has stopped. Rows already written are kept, and a later
**Pokreni → Nastavi** continues.

Three API failures in a row stop the run, and a resume starts with the same photos, so a photo that
keeps failing with `greška API-ja` must be moved out of the input folder and handled by hand.

**Retry hard images with a stronger model.** Whenever the output folder's `byhand/` has images and
no run is in progress, the **Ponovi byhand/** button is available. It re-runs just those images
with Claude Opus 5.5 at `high` effort. If your selected model is stronger than Opus 5.5 (Fable 5,
Fable 5.1) or your selected effort is higher than `high` (`xhigh`, `max`), that setting is kept
instead, so a retry is never weaker than the run you chose. Before starting, it shows the model,
effort and an estimated cost and asks you to confirm. Results go into a separate `byhand_retry/`
subfolder (its own `output.csv`, `.processed` and, if anything is still unreadable, its own
`byhand/`) — the original `output.csv` and `byhand/` are left untouched, so you can compare the two
runs or merge the improved rows in by hand. A retry can be resumed like any run: if `byhand_retry/`
already has an `output.csv`, **Ponovi byhand/** asks **Nastavi** / **Prepiši** / **Odustani** before
touching it. While a retry runs, its `.tron-grave.lock` sits in `byhand_retry/`, and the app asks
before using a folder that is already locked.

---

## Project Structure

```
TRON-GRAVE/
├── main.py                 # Entry point for PyInstaller executable
├── grave_ui.py             # Desktop GUI (Tkinter)
├── ui_logic.py             # Tk-free GUI helpers (models, settings, estimates, texts)
├── grave_extractor.py      # CLI batch processor
├── _version.py             # Single source of the version string
├── extractor/
│   ├── image_processor.py  # Claude Vision API integration + result classification
│   ├── csv_writer.py       # CSV output (UTF-8 with BOM)
│   ├── file_utils.py       # File validation, ID assignment and byhand/ copies
│   └── pricing.py          # per-model prices and real cost
├── tests/                  # unittest suite
├── requirements.txt        # Python dependencies
├── build.bat               # One-click Windows build script
├── TRON-GRAVE.spec         # PyInstaller build config
└── .env.example            # API key template
```

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.10+ |
| AI / Vision | Anthropic Claude API (`anthropic` SDK) |
| GUI | Tkinter (stdlib) |
| Image processing | Pillow |
| Environment config | python-dotenv |
| Packaging | PyInstaller |

---

## Tests

Run the test suite from the repo root, inside the virtual environment:

```bash
python -m unittest discover -s tests -t . -v
```

GUI tests need a display and are skipped without one; no test calls the real API.

---

## Building from Source (Windows .exe)

Double-click **`build.bat`** (or run it from a terminal). It creates an isolated build
virtualenv, installs the dependencies plus PyInstaller, and packs everything into one file.
Building the exe needs **Python 3.11+** — running from source only needs 3.10+.

To build by hand instead (PyInstaller 6.9 or newer is needed: the GUI's **Zaustavi** relies on how
it starts the extractor):

```bash
pip install "pyinstaller>=6.9"
pyinstaller TRON-GRAVE.spec
```

Either way the executable lands in `dist/TRON-GRAVE.exe`.

---

## License

MIT — see [LICENSE](LICENSE)
