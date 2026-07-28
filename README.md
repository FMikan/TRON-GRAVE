# TRON-GRAVE

**Automated tombstone inscription extractor for genealogical research.**

TRON-GRAVE uses the Claude Vision AI to read photographs of gravestones and extract structured burial data — names, surnames, birth years, and death years — into a CSV file ready for import into spreadsheets or genealogical databases.

Designed for digitizing Croatian cemetery records, with full support for Croatian characters (č, ć, š, đ, ž) and automatic Cyrillic-to-Latin transliteration.

---

## Features

- **AI-powered OCR** — reads tombstone inscriptions using Claude Vision (Anthropic API)
- **Model selection** — choose between **Claude Sonnet 5** (cheapest), **Claude Opus 5** and **Claude Fable 5** from the GUI dropdown, or any model via the `--model` CLI flag
- **Effort control** — pick how hard the model works per image (`low` → `max`) from the GUI dropdown or `--effort`; the choices adapt to the selected model
- **Multi-person tombstones** — extracts records for each person on a single stone
- **Multi-marker graves** — reads *all* plaques, headstones and crosses that belong to one grave (shared frame, surname, grouping or style), instead of stopping at the first one; ambiguous neighbours are flagged for manual review rather than guessed
- **Conservative extraction** — leaves fields blank rather than guessing uncertain data
- **Smart year handling** — for both birth and death years, distinguishes a *certain* absence (e.g. person still living, or no birth date inscribed) from an *unreadable* year, so records with a legitimately missing year are passed as **OK** instead of being needlessly flagged for review
- **Prompt caching** — the shared instructions are cached after the first image and reused for the rest of the run, cutting the per-image system-prompt cost by ~90% for the whole batch. The prompt is well above the cache minimum on all three offered models (1024 tokens on Sonnet 5, 512 on Opus 5 and Fable 5), so this applies whichever you pick
- **Batch processing** — processes entire folders of images automatically
- **Smart image compression** — auto-resizes oversized images to fit API requirements
- **Manual review queue** — copies images needing review to a `byhand/` folder for manual inspection
- **Notes column** — every edge case (uncertain year, missing field, non-standard filename) is explained inline in the CSV's `Notes` column
- **Real-time progress & live cost counter** — GUI shows progress bar, ETA, and the *real* running API cost (computed from each response's actual token usage, not an estimate)
- **End-of-run summary** — a popup with OK/manual/failed counts, the most common review reasons, and total cost, shown as soon as a run finishes
- **Resume interrupted runs** — check "Nastavi (preskoči obrađene)" to skip images already in `output.csv` and append only the new ones, instead of reprocessing (and re-paying for) everything
- **Retry byhand with Opus** — after a run finishes, a button lets you re-run just the `byhand/` images through Opus 5 at high effort into a separate `byhand_retry/` folder, without touching the original `output.csv`
- **Automatic retries** — retries failed API calls with exponential backoff
- **Settings persistence** — remembers your folders, API key, and chosen model between sessions
- **Dry-run mode** — preview image discovery without making any API calls
- **Croatian & Cyrillic support** — outputs in Croatian with automatic Cyrillic transliteration

**Supported image formats:** `.jpg`, `.jpeg`, `.png`, `.webp`
> Note: iPhone HEIC/HEIF photos must be converted to JPG first.

---

## Output

For each processed folder, TRON-GRAVE creates:

**`output.csv`** — UTF-8 with BOM (Excel-compatible)
```
ID,Name,Surname,Year of Birth,Year of Death,Notes
img001,Ivan,Horvat,1921,1987,
img002,Marija,Horvat,1925,,bez godine smrti — osoba vjerojatno živa
img003,Petar,Kovač,1940,,godina smrti nečitka
```

The **`Notes`** column (Croatian) is filled only for edge cases and is the single place
to look when reviewing results. Typical notes:

| Note | Meaning | Sent to `byhand/`? |
|------|---------|--------------------|
| *(empty)* | Full record, nothing to review | No |
| `bez god. smrti` | Model is certain there is no year of death (e.g. person still living) | No — counts as **OK** |
| `bez god. rođenja` | Model is certain there is no year of birth inscribed | No — counts as **OK** |
| `bez god. rođ. i smrti` | Model is certain neither a birth nor a death year exists | No — counts as **OK** |
| `god. smrti nečitka` / `god. rođenja nečitka` | A year may exist but could not be read confidently | Yes — **PARTIAL** |
| `fali: prezime` | Name or surname is illegible | Yes — **PARTIAL** |
| `provjeri: možda više oznaka` | Nearby plaques/crosses might belong to the same grave; the model left them out to be safe — check for missed people | Yes — **PARTIAL** |
| `sve nečitko`, `nema podataka` | Nothing could be extracted | Yes — **FAILED** |
| `… ID iz naziva` | Filename did not match the expected pattern; the stem was used as ID | No (appended to any note) |

The notes are kept intentionally terse to minimise token/CSV size while preserving meaning.

**`byhand/`** — copies of images flagged for manual review (PARTIAL or FAILED rows above)

> There is no longer a separate `errors.txt`; all review information now lives in the `Notes` column.

---

## Requirements

- An **Anthropic API key** (see [Getting an API Key](#getting-an-api-key) below)
- Images of tombstones in JPG, PNG, or WebP format

---

## Getting an API Key

TRON-GRAVE uses the **Anthropic Claude API** to analyze tombstone images. You need an API key to use it.

1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account
2. Add a payment method (pay-as-you-go — no subscription required)
3. Navigate to **API Keys** in the left sidebar
4. Click **Create Key**, give it a name, and copy the key
5. Paste the key into TRON-GRAVE when prompted (GUI) or into your `.env` file (CLI)

**Estimated cost per image** (pre-run preview shown in the GUI):

| Model | Est. cost/image | Basis |
|---|---|---|
| Claude Sonnet 5 (`claude-sonnet-5`) | ~$0.006 | estimate; ~$3/$15 per MTok, intro $2/$10 until Aug 31 2026 |
| Claude Opus 5 (`claude-opus-5`) | ~$0.025 | scaled from a measured Opus average; $5/$25 per MTok |
| Claude Fable 5 (`claude-fable-5`) | ~$0.050 | estimate; $10/$50 per MTok — 2× Opus |

These are only for the *before-you-start* estimate. Once a run is going, the status bar and the
end-of-run summary show the **real** cost, computed from each API response's actual token usage
(including prompt-cache discounts) — not an estimate.

Higher **effort** levels (`high` → `max`, and `xhigh` on Sonnet 5 / Opus) make the model reason
harder per image at higher token cost; drop to `low`/`medium` for cheaper, faster runs.

---

## Installation & Running

### Windows — Prebuilt Executable (recommended)

1. Go to the [Releases](../../releases) page
2. Download the latest `TRON-GRAVE.exe`
3. Double-click to run — no Python or installation required
4. Enter your Anthropic API key when prompted on first launch
5. (Optional) Pick a **Model** (Sonnet 5 default · Opus 5 · Fable 5) and an **Effort** level
6. Select your input folder (photos) and output folder, then click **Start**

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
git clone https://github.com/your-username/TRON-GRAVE.git
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
git clone https://github.com/your-username/TRON-GRAVE.git
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
  --output  PATH     Folder where results will be saved (required)
  --model   NAME     Claude model to use (default: claude-sonnet-4-6).
                     Accepts any model id, not just the ones in the GUI dropdown.
                     GUI values: claude-sonnet-5, claude-opus-5, claude-fable-5
  --effort  LEVEL    Reasoning effort: low | medium | high | xhigh | max (default: model's own).
                     Note: xhigh is not supported on Sonnet 4.6.
  --resume           Skip images whose ID is already in output.csv; append instead of overwriting
  --verbose          Show detailed per-image progress
  --dry-run          List discovered images without making any API calls
```

The model can also be set with the `CLAUDE_MODEL` environment variable; the `--model` flag takes precedence.

Verbose per-image lines include the real cost of that call and the running total, e.g.:
```
[12/300] Processing img012.jpg ... OK (1 record) — $0.0184 (total: $2.35)
```
and the final line before exit reports the run's total: `Total cost: $2.35`.

**Exit codes:**
- `0` — All images processed successfully
- `2` — Partial success (some images failed or had missing fields)
- `1` — Fatal error (likely invalid API key)
- `130` — Interrupted by user (Ctrl+C)

---

## Resuming & Retrying

**Resume an interrupted run.** If a run is stopped (Ctrl+C, the GUI's Stop button, or a crash),
check **"Nastavi (preskoči obrađene)"** in the GUI (or pass `--resume` on the CLI) before starting
again on the same output folder. Every processed image already has a row in `output.csv` — resume
reads that file, skips any image whose ID is already there, and appends only the new ones instead
of overwriting the file. This also means you don't get asked the "output.csv exists" backup/overwrite
question, and you don't pay to reprocess images you've already paid for once.

**Retry hard images with a stronger model.** Once a run finishes, if `byhand/` has any images, the
**"Retry byhand (Opus)"** button becomes available. Clicking it re-runs just those images through
Claude Opus 5 at `high` effort, writing results into a separate `byhand_retry/` subfolder (its own
`output.csv` and, if anything is still unreadable, its own `byhand/`) — the original `output.csv` and
`byhand/` are left untouched, so you can compare the two runs or merge the improved rows in by hand.

---

## Project Structure

```
TRON-GRAVE/
├── main.py                 # Entry point for PyInstaller executable
├── grave_ui.py             # Desktop GUI (Tkinter)
├── grave_extractor.py      # CLI batch processor
├── extractor/
│   ├── image_processor.py  # Claude Vision API integration + result classification
│   ├── csv_writer.py       # CSV output (UTF-8 with BOM)
│   └── file_utils.py       # File validation and MIME detection
├── requirements.txt        # Python dependencies
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

## Building from Source (Windows .exe)

```bash
pip install pyinstaller
pyinstaller TRON-GRAVE.spec
```

The executable will be in `dist/TRON-GRAVE.exe`.

---

## License

MIT — see [LICENSE](LICENSE)
