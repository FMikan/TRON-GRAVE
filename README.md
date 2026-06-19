# TRON-GRAVE

**Automated tombstone inscription extractor for genealogical research.**

TRON-GRAVE uses the Claude Vision AI to read photographs of gravestones and extract structured burial data — names, surnames, birth years, and death years — into a CSV file ready for import into spreadsheets or genealogical databases.

Designed for digitizing Croatian cemetery records, with full support for Croatian characters (č, ć, š, đ, ž) and automatic Cyrillic-to-Latin transliteration.

---

## Features

- **AI-powered OCR** — reads tombstone inscriptions using Claude Vision (Anthropic API)
- **Model selection** — choose between **Claude Sonnet 4.6** (fast, cheaper) and **Claude Opus 4.8** (most capable) from the GUI dropdown or the `--model` CLI flag
- **Multi-person tombstones** — extracts records for each person on a single stone
- **Conservative extraction** — leaves fields blank rather than guessing uncertain data
- **Smart year-of-death handling** — distinguishes a *certain* absence (person still living, only a birth year inscribed) from an *unreadable* year, so living people are not needlessly flagged for review
- **Batch processing** — processes entire folders of images automatically
- **Smart image compression** — auto-resizes oversized images to fit API requirements
- **Manual review queue** — copies images needing review to a `byhand/` folder for manual inspection
- **Notes column** — every edge case (uncertain year, missing field, non-standard filename) is explained inline in the CSV's `Notes` column
- **Real-time progress** — GUI shows progress bar, ETA, and running API cost estimate
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
| `bez godine smrti — …` | Model is certain there is no year of death (e.g. person still living) | No — counts as **OK** |
| `godina smrti nečitka` | A year of death may exist but could not be read | Yes — **PARTIAL** |
| `nedostaje: prezime` | A required field (name / surname / birth year) is illegible | Yes — **PARTIAL** |
| `sva polja nečitka`, `model nije vratio podatke` | Nothing could be extracted | Yes — **FAILED** |
| `… ID iz naziva datoteke` | Filename did not match the expected pattern; the stem was used as ID | No (appended to any note) |

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

**Estimated cost:** approximately **$0.005 per image** (~$5 per 1,000 photos) using the default
**Claude Sonnet 4.6** model. **Claude Opus 4.8** is more accurate on hard-to-read stones but costs
several times more per image — pick it from the model dropdown (GUI) or `--model claude-opus-4-8` (CLI)
when accuracy matters more than cost.

---

## Installation & Running

### Windows — Prebuilt Executable (recommended)

1. Go to the [Releases](../../releases) page
2. Download the latest `TRON-GRAVE.exe`
3. Double-click to run — no Python or installation required
4. Enter your Anthropic API key when prompted on first launch
5. (Optional) Pick a model from the **Model** dropdown — Sonnet 4.6 (default) or Opus 4.8
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
                     Common values: claude-sonnet-4-6, claude-opus-4-8
  --verbose          Show detailed per-image progress
  --dry-run          List discovered images without making any API calls
```

The model can also be set with the `CLAUDE_MODEL` environment variable; the `--model` flag takes precedence.

**Exit codes:**
- `0` — All images processed successfully
- `2` — Partial success (some images failed or had missing fields)
- `1` — Fatal error (likely invalid API key)
- `130` — Interrupted by user (Ctrl+C)

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
