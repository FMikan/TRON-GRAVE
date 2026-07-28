import re
import shutil
from collections import Counter
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


def assign_ids(paths: list[Path]) -> dict[Path, tuple[str, bool]]:
    """Record ID per image, de-colliding against the whole discovered set.

    The pattern takes the second underscore-separated token, which on date-stamped phone
    filenames is the date: a morning's shoot (IMG_20240513_142233, IMG_20240513_142401, …)
    would collapse to one ID, making rows untraceable back to the stone and letting
    --resume skip all but the first. Where an extracted ID is not unique, fall back to the
    full (unique) stem and report it as unmatched so the row is tagged in the Notes column.
    """
    extracted = {p: extract_id(p) for p in paths}
    counts = Counter(record_id for record_id, _ in extracted.values())
    return {
        p: (record_id, matched) if counts[record_id] == 1 else (p.stem, False)
        for p, (record_id, matched) in extracted.items()
    }


def get_mime_type(path: Path) -> str:
    return _MIME_TYPES[path.suffix.lower()]


def copy_to_byhand(src: Path, byhand_dir: Path) -> None:
    byhand_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, byhand_dir / src.name)
