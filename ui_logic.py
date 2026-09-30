"""Tk-free helpers behind the desktop GUI, kept apart from grave_ui.py so they can be tested."""

import json
import os
import sys
from pathlib import Path

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
