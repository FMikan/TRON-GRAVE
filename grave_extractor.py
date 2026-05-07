#!/usr/bin/env python3
"""TRON-GRAVE: extract burial records from tombstone photographs."""

import argparse
import os
import signal
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from extractor.csv_writer import append_rows, init_csv
from extractor.error_logger import log_error
from extractor.file_utils import copy_to_byhand, extract_id, is_supported_image
from extractor.image_processor import process_image


DEFAULT_MODEL = "claude-sonnet-4-6"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="grave_extractor",
        description="Extract burial records from tombstone photographs using Claude Vision.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Folder containing image files")
    parser.add_argument("--output", type=Path, default=Path("./output"),
                        help="Directory for output.csv, errors.txt, and byhand/ (default: ./output)")
    parser.add_argument("--model", default=None,
                        help=f"Claude model (default: CLAUDE_MODEL env, else {DEFAULT_MODEL})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print images that would be processed and exit")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-image progress to stdout")
    return parser.parse_args()


def discover_images(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.iterdir() if p.is_file() and is_supported_image(p))


def fatal(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    for _s in (sys.stdout, sys.stderr):
        if _s is not None and hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    load_dotenv()

    input_dir: Path = args.input
    if not input_dir.is_dir():
        fatal(f"Input folder not found: {input_dir}")

    images = discover_images(input_dir)

    if args.dry_run:
        for img in images:
            print(img)
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        fatal("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")

    model = args.model or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL

    output_dir: Path = args.output
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fatal(f"Cannot create output directory {output_dir}: {e}")

    output_csv = output_dir / "output.csv"
    errors_path = output_dir / "errors.txt"
    byhand_dir = output_dir / "byhand"

    try:
        init_csv(output_csv)
    except OSError as e:
        fatal(f"Cannot write to {output_csv}: {e}")

    client = anthropic.Anthropic(api_key=api_key)

    total = len(images)
    succeeded = 0
    partial = 0
    failed = 0
    byhand_count = 0
    had_any_issue = False
    first_api_call_done = False

    for idx, img in enumerate(images, start=1):
        record_id, matched = extract_id(img)

        if not matched:
            log_error(errors_path, record_id, "Filename did not match expected pattern; stem used as ID")
            had_any_issue = True

        if args.verbose:
            print(f"[{idx}/{total}] Processing {img.name} ... ", end="", flush=True)

        result = process_image(client, model, img, record_id)

        if result.fatal_api_error and not first_api_call_done:
            if args.verbose:
                print(f"FAILED ({result.reason})")
            print(f"error: first API call failed: {result.reason}", file=sys.stderr)
            return 1

        first_api_call_done = True

        append_rows(output_csv, result.rows)

        if result.status == "full_success":
            succeeded += 1
            if args.verbose:
                n = len(result.rows)
                print(f"OK ({n} record{'s' if n != 1 else ''})")
        elif result.status == "partial_success":
            log_error(errors_path, record_id, result.reason)
            copy_to_byhand(img, byhand_dir)
            byhand_count += 1
            partial += 1
            had_any_issue = True
            if args.verbose:
                print(f"PARTIAL ({result.reason})")
        else:
            log_error(errors_path, record_id, result.reason)
            copy_to_byhand(img, byhand_dir)
            byhand_count += 1
            failed += 1
            had_any_issue = True
            if args.verbose:
                print(f"FAILED ({result.reason})")

    print(f"Done. {total} images processed. {succeeded} succeeded, {partial} partial, {failed} failed.")
    print(f"Output:  {output_csv}")
    if errors_path.exists():
        print(f"Errors:  {errors_path}")
    if byhand_count > 0:
        print(f"Review:  {byhand_dir}/ ({byhand_count} images)")

    return 2 if had_any_issue else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
