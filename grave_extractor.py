#!/usr/bin/env python3
"""TRON-GRAVE: extract burial records from tombstone photographs."""

import argparse
import os
import signal
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from extractor.csv_writer import append_rows, init_csv, read_processed_ids
from extractor.file_utils import assign_ids, copy_to_byhand, is_supported_image
from extractor.image_processor import append_note, process_image


DEFAULT_MODEL = "claude-sonnet-5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="grave_extractor",
        description="Extract burial records from tombstone photographs using Claude Vision.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Folder containing image files")
    parser.add_argument("--output", type=Path, default=Path("./output"),
                        help="Directory for output.csv and byhand/ (default: ./output)")
    parser.add_argument("--model", default=None,
                        help=f"Claude model (default: CLAUDE_MODEL env, else {DEFAULT_MODEL})")
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "xhigh", "max"],
                        help="Reasoning/effort level (output_config.effort). Omit for the model default (high).")
    parser.add_argument("--resume", action="store_true",
                        help="Skip images whose ID is already in output.csv and append instead of overwriting")
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
    byhand_dir = output_dir / "byhand"

    # IDs are assigned over the whole discovered set so a resume run derives the same ID
    # for a given photo as the original run did.
    ids = assign_ids(images)

    resuming = args.resume and output_csv.exists()
    if resuming:
        processed_ids = read_processed_ids(output_csv)
        before = len(images)
        images = [img for img in images if ids[img][0] not in processed_ids]
        skipped = before - len(images)
        if skipped and args.verbose:
            print(f"Resume: skipping {skipped} already-processed image(s).")
    else:
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
    total_cost = 0.0

    for idx, img in enumerate(images, start=1):
        record_id, matched = ids[img]

        if args.verbose:
            print(f"[{idx}/{total}] Processing {img.name} ... ", end="", flush=True)

        result = process_image(client, model, img, record_id, args.effort)
        total_cost += result.cost
        cost_suffix = f" — ${result.cost:.4f} (total: ${total_cost:.2f})" if result.cost else ""

        # An account-level failure (revoked key, no access, unknown model) dooms every
        # remaining image, so stop at whatever index it surfaces -- carrying on would just
        # append blank rows for the rest of the batch and report the run as finished.
        if result.fatal_api_error:
            if args.verbose:
                print(f"FAILED ({result.reason})")
            print(f"error: API call failed: {result.reason}", file=sys.stderr)
            return 1

        if not matched:
            # No errors.txt anymore: flag the non-standard filename in the Notes cell.
            for row in result.rows:
                append_note(row, "ID iz naziva")
            had_any_issue = True

        append_rows(output_csv, result.rows)

        if result.status == "full_success":
            succeeded += 1
            if args.verbose:
                n = len(result.rows)
                print(f"OK ({n} record{'s' if n != 1 else ''}){cost_suffix}")
        elif result.status == "partial_success":
            copy_to_byhand(img, byhand_dir)
            byhand_count += 1
            partial += 1
            had_any_issue = True
            if args.verbose:
                print(f"PARTIAL ({result.reason}){cost_suffix}")
        else:
            copy_to_byhand(img, byhand_dir)
            byhand_count += 1
            failed += 1
            had_any_issue = True
            if args.verbose:
                print(f"FAILED ({result.reason}){cost_suffix}")

    print(f"Done. {total} images processed. {succeeded} succeeded, {partial} partial, {failed} failed.")
    print(f"Total cost: ${total_cost:.2f}")
    print(f"Output:  {output_csv}")
    if byhand_count > 0:
        print(f"Review:  {byhand_dir}/ ({byhand_count} images)")

    return 2 if had_any_issue else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
