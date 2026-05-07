import re
import shutil
from pathlib import Path

FILENAME_PATTERN = re.compile(r'^[^_]+_([^_]+)_.+')
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

_MIME_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}


def is_supported_image(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def extract_id(path: Path) -> tuple[str, bool]:
    match = FILENAME_PATTERN.match(path.stem)
    if match:
        return match.group(1), True
    return path.stem, False


def get_mime_type(path: Path) -> str:
    return _MIME_TYPES[path.suffix.lower()]


def copy_to_byhand(src: Path, byhand_dir: Path) -> None:
    byhand_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, byhand_dir / src.name)
