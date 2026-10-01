import csv
from pathlib import Path

CSV_COLUMNS = ['ID', 'Name', 'Surname', 'Year of Birth', 'Year of Death', 'Notes', 'File']
NOTES_INDEX = CSV_COLUMNS.index('Notes')
FILE_INDEX = CSV_COLUMNS.index('File')

# Which images a run got through, one filename per line. Resume cannot key on the ID
# column: several photos of one grave legitimately share an ID, so a processed ID does
# not mean every photo carrying it was processed.
PROCESSED_FILE = '.processed'


def csv_text(value):
    """A cell value that always encodes: undecodable bytes in a Linux filename show as
    U+FFFD instead of crashing the write after the image was already paid for."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value.encode('utf-8', 'surrogateescape').decode('utf-8', 'replace')
    return value


def init_csv(output_path: Path) -> None:
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_COLUMNS)


def append_rows(output_path: Path, rows: list[list]) -> None:
    with open(output_path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            writer.writerow([csv_text(v) for v in row])


def read_csv(output_path: Path) -> tuple[list[str] | None, list[list[str]]]:
    """Header and data rows of output.csv, or (None, []) when there is none.

    Undecodable bytes are replaced, not raised: a file re-saved from Excel in the Windows
    code page must still be countable and inspectable.
    """
    try:
        with open(output_path, encoding='utf-8-sig', errors='replace', newline='') as f:
            rows = list(csv.reader(f))
    except FileNotFoundError:
        return None, []
    return (rows[0], rows[1:]) if rows else ([], [])


def rewrite_rows(output_path: Path, rows: list[list]) -> None:
    """Replace output.csv's data rows, via a temp file so a crash can't leave half a CSV."""
    tmp = output_path.with_name(output_path.name + '.tmp')
    init_csv(tmp)
    append_rows(tmp, rows)
    tmp.replace(output_path)


def check_writable(output_path: Path) -> None:
    """Raise OSError when output.csv can't be opened for appending (e.g. open in Excel)."""
    with open(output_path, 'a', encoding='utf-8'):
        pass


def init_processed(output_dir: Path) -> None:
    (output_dir / PROCESSED_FILE).write_text('', encoding='utf-8')


def mark_processed(output_dir: Path, image: Path) -> None:
    # surrogateescape round-trips a Linux filename that is not valid UTF-8 back to the
    # exact name read_processed() compares against.
    with open(output_dir / PROCESSED_FILE, 'a', encoding='utf-8', errors='surrogateescape') as f:
        f.write(f'{image.name}\n')


def read_processed(output_dir: Path) -> set[str]:
    """Image filenames a previous run finished, for --resume."""
    try:
        text = (output_dir / PROCESSED_FILE).read_text(encoding='utf-8', errors='surrogateescape')
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}


def resume_problem(output_dir: Path) -> str | None:
    """Why resuming into output_dir would go wrong, or None when it is safe.

    'old-format'   output.csv has other columns (written by an older version)
    'no-processed' output.csv has rows but there is no .processed file
    'missing-csv'  .processed lists images but output.csv is gone
    'unreadable'   output.csv could not be parsed
    'not-utf8'     output.csv is no longer UTF-8 (re-saved as ANSI by Excel or an editor)
    """
    csv_path = output_dir / 'output.csv'
    try:
        header, rows = read_csv(csv_path)
    except (OSError, csv.Error):
        return 'unreadable'
    processed = read_processed(output_dir)
    if not header:
        return 'missing-csv' if processed else None
    if header != CSV_COLUMNS:
        return 'old-format'
    if rows and not (output_dir / PROCESSED_FILE).exists():
        return 'no-processed'
    # Last, so an Excel re-save in a ';' locale keeps its 'old-format' reason (other columns).
    try:
        csv_path.read_bytes().decode('utf-8')   # a BOM decodes fine; an ANSI re-save does not
    except UnicodeDecodeError:
        return 'not-utf8'
    except OSError:
        return 'unreadable'
    return None
