import os
import re
import shutil
import stat
import time
from pathlib import Path

FILENAME_PATTERN = re.compile(r'^[^_]+_([^_]+)_.+')
# A date or time stamp in the ID position is not a record ID: on IMG_20240513_142233 the
# pattern would take 20240513, and on photo_2024-05-13_14-22-33 it would take 2024-05-13,
# collapsing a whole day's shoot onto one ID. Six or more bare digits, or three or more
# digit groups joined by '-' or '.', is a stamp; a short plot number like 12-3 is not.
DATESTAMP_PATTERN = re.compile(r'^(\d{6,}|\d{1,4}(?:[-.]\d{1,4}){2,})$')
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
# Recognised only so they can be reported as skipped: the API cannot take HEIC/HEIF.
HEIC_EXTENSIONS = {'.heic', '.heif'}


def is_supported_image(path: Path) -> bool:
    # Dotfiles are never photos: macOS writes a ._<name>.jpg metadata twin next to every
    # file it copies onto a FAT/exFAT stick or a network share.
    return not path.name.startswith('.') and path.suffix.lower() in SUPPORTED_EXTENSIONS


def is_heic(path: Path) -> bool:
    return not path.name.startswith('.') and path.suffix.lower() in HEIC_EXTENSIONS


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


def copy_to_byhand(src: Path, byhand_dir: Path) -> None:
    """Copy a photo into byhand/ for manual review.

    copyfile rather than copy2: a photo marked read-only on the camera would otherwise
    leave a read-only copy that the next run cannot overwrite.
    """
    byhand_dir.mkdir(parents=True, exist_ok=True)
    dst = byhand_dir / src.name
    if dst.exists():
        if dst.resolve() == src.resolve():
            return
        _force_unlink(dst)
    shutil.copyfile(src, dst)


def remove_from_byhand(src: Path, byhand_dir: Path) -> None:
    """Drop a photo's review copy once it has been processed successfully."""
    dst = byhand_dir / src.name
    if dst.exists():
        _force_unlink(dst)


def clear_byhand(byhand_dir: Path) -> None:
    """Delete the photo copies an earlier run left in byhand/ (other files stay)."""
    if not byhand_dir.is_dir():
        return
    for f in byhand_dir.iterdir():
        if f.is_file() and is_supported_image(f):
            _force_unlink(f)


def _force_unlink(path: Path) -> None:
    """Delete a file even when it is marked read-only (Windows refuses otherwise)."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
        path.unlink()


# What a fresh start moves aside, and the backup name of each: (name, pattern, is a folder).
_BACKUPS = (("output.csv", "output.{}.bak.csv", False),
            ("byhand", "byhand.{}.bak", True),
            ("byhand_retry", "byhand_retry.{}.bak", True))


def back_up_outputs(out_dir: Path, stamp: str | None = None) -> list[Path]:
    """Move output.csv, byhand/ and byhand_retry/ aside under one timestamp: all of them or none.

    Raises OSError after putting back whatever had already moved (on Windows a photo open in a
    viewer keeps its folder from moving). A second backup within the same second gets "-2", "-3"...
    so it never lands on the first one.
    """
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    targets = [(out_dir / name, pattern, folder) for name, pattern, folder in _BACKUPS
               if ((out_dir / name).is_dir() if folder else (out_dir / name).is_file())]
    suffix, n = stamp, 1
    while any((out_dir / pattern.format(suffix)).exists() for _src, pattern, _folder in targets):
        n += 1
        suffix = f"{stamp}-{n}"
    moved = []
    try:
        for src, pattern, folder in targets:
            dst = out_dir / pattern.format(suffix)
            if folder:
                src.rename(dst)
            else:
                src.replace(dst)
            moved.append((src, dst, folder))
    except OSError:
        for src, dst, folder in reversed(moved):
            try:
                if folder:
                    dst.rename(src)
                else:
                    dst.replace(src)
            except OSError:
                pass
        raise
    return [dst for _src, dst, _folder in moved]
