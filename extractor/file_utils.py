import re
import shutil
from pathlib import Path

FILENAME_PATTERN = re.compile(r'^[^_]+_([^_]+)_.+')
# A long run of digits in the ID position is a date or time stamp, not a record ID:
# on IMG_20240513_142233 the pattern would take 20240513 and collapse a whole day's
# shoot onto one ID. Record IDs are short; six or more digits is a stamp.
DATESTAMP_PATTERN = re.compile(r'^\d{6,}$')
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
    """Record ID from a <prefix>_<id>_<rest> filename, else the stem flagged as unmatched.

    IDs are deliberately not de-duplicated: one grave is often photographed several
    times (pokojnici-ploca_30_…_11-18-49, …_11-19-08), and those rows *should* share
    the grave's ID. Uniqueness per image comes from the .processed sidecar instead.
    """
    match = FILENAME_PATTERN.match(path.stem)
    if match and not DATESTAMP_PATTERN.match(match.group(1)):
        return match.group(1), True
    return path.stem, False


def get_mime_type(path: Path) -> str:
    return _MIME_TYPES[path.suffix.lower()]


def copy_to_byhand(src: Path, byhand_dir: Path) -> None:
    byhand_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, byhand_dir / src.name)
