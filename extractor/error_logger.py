from pathlib import Path


def log_error(errors_path: Path, record_id: str, reason: str) -> None:
    with open(errors_path, 'a', encoding='utf-8') as f:
        f.write(f"{record_id} -> {reason}\n")
