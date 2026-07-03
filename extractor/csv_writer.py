import csv
from pathlib import Path

CSV_COLUMNS = ['ID', 'Name', 'Surname', 'Year of Birth', 'Year of Death', 'Notes']


def init_csv(output_path: Path) -> None:
    with open(output_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_COLUMNS)


def append_rows(output_path: Path, rows: list[list]) -> None:
    with open(output_path, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for row in rows:
            writer.writerow(['' if v is None else v for v in row])


def read_processed_ids(output_path: Path) -> set[str]:
    """IDs already present in an existing output.csv (first column), for --resume."""
    ids: set[str] = set()
    try:
        with open(output_path, encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for row in reader:
                if row:
                    ids.add(row[0])
    except OSError:
        pass
    return ids
