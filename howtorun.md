# How to Run TRON-GRAVE

## Prerequisites

- Python 3.10 or later — [python.org](https://www.python.org/downloads/)
- An Anthropic API key — [console.anthropic.com](https://console.anthropic.com/)

---

## Windows

### 1. Open PowerShell in the project folder

```powershell
cd D:\apk\folder\trongUI
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv .venv\Scripts\Activate.ps1
```

If you get a script execution error, run this once and retry:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Set up your API key

The `.env` file already exists in the project. Open it in Notepad and make sure it contains:

```
ANTHROPIC_API_KEY=your_api_key_here
```

If it does not exist, copy the example:

```powershell
Copy-Item .env.example .env
```

### 5. Run

**Desktop UI (recommended):**

```powershell
python grave_ui.py
```

**CLI (command line):**

```powershell
python grave_extractor.py --input C:\path\to\photos --output C:\path\to\results
```

**CLI with verbose output:**

```powershell
python grave_extractor.py --input C:\path\to\photos --output C:\path\to\results --verbose
```

**Dry run (lists images without calling the API):**

```powershell
python grave_extractor.py --input C:\path\to\photos --dry-run
```

---

## Linux

### 1. Install system packages

`tkinter` is not bundled with Python on most Linux distros. Install it before creating the venv:

**Debian / Ubuntu:**

```bash
sudo apt install python3-tk python3-venv
```

**Fedora:**

```bash
sudo dnf install python3-tkinter
```

**Arch:**

```bash
sudo pacman -S tk
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up your API key

```bash
cp .env.example .env
nano .env   # or use any text editor
```

Set the value:

```
ANTHROPIC_API_KEY=your_api_key_here
```

### 5. Run

**Desktop UI:**

```bash
python grave_ui.py
```

**CLI:**

```bash
python grave_extractor.py --input /path/to/photos --output /path/to/results
```

**CLI with verbose output:**

```bash
python grave_extractor.py --input /path/to/photos --output /path/to/results --verbose
```

**Dry run (lists images without calling the API):**

```bash
python grave_extractor.py --input /path/to/photos --dry-run
```

---

## Dependencies

All pip packages are listed in `requirements.txt`:

| Package | Purpose |
|---|---|
| `anthropic` | Anthropic Claude Vision API client |
| `python-dotenv` | Loads `ANTHROPIC_API_KEY` from the `.env` file |
| `Pillow` | Recompresses oversized images before sending to the API |

`tkinter` (used by the desktop UI) is part of the Python standard library on Windows and macOS. On Linux it must be installed via the OS package manager as shown above — it is not available through pip.

---

## Supported image formats

`.jpg` · `.jpeg` · `.png` · `.webp`

HEIC/HEIF (iPhone default) must be converted to JPG first.

---

## Output

Results are written to the `--output` folder:

| File | Contents |
|---|---|
| `output.csv` | Extracted burial records (UTF-8 with BOM, Excel-compatible) |
| `errors.txt` | Images with missing fields or processing errors |
| `byhand/` | Copies of fully unreadable images for manual review |
