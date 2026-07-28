import csv
from pathlib import Path

CSV_COLUMNS = ['ID', 'Name', 'Surname', 'Year of Birth', 'Year of Death', 'Notes']

# Which images a run got through, one filename per line. Resume cannot key on the ID
# column: several photos of one grave legitimately share an ID, so a processed ID does
# not mean every photo carrying it was processed.
PROCESSED_FILE = '.processed'


def init_csv(output_path: Path) -> None:
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_COLUMNS)


def append_rows(output_path: Path, rows: list[list]) -> None:
    with open(output_path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            writer.writerow(['' if v is None else v for v in row])


def init_processed(output_dir: Path) -> None:
    (output_dir / PROCESSED_FILE).write_text('', encoding='utf-8')


def mark_processed(output_dir: Path, image: Path) -> None:
    with open(output_dir / PROCESSED_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{image.name}\n')


def read_processed(output_dir: Path) -> set[str]:
    """Image filenames a previous run finished, for --resume.

    Missing on an output folder written before the sidecar existed, in which case
    nothing is skipped and the whole batch is re-sent.
    """
    try:
        text = (output_dir / PROCESSED_FILE).read_text(encoding='utf-8')
    except OSError:
        return set()
    return {line.strip() for line in text.splitlines() if line.strip()}
