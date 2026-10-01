#!/usr/bin/env python3
"""TRON-GRAVE: extract burial records from tombstone photographs."""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from extractor.csv_writer import (
    FILE_INDEX, append_rows, check_writable, csv_text, init_csv, init_processed,
    mark_processed, read_csv, read_processed, resume_problem, rewrite_rows,
)
from extractor.file_utils import (
    clear_byhand, copy_to_byhand, extract_id, is_heic, is_supported_image, remove_from_byhand,
)
from extractor.image_processor import ImageResult, failure_row, process_image
from extractor.pricing import MODEL_PRICING


DEFAULT_MODEL = "claude-sonnet-5"
# Consecutive API failures that end the run: an account-level problem (spend limit, billing,
# outage) fails every image the same way, and carrying on would only log blank rows.
MAX_CONSECUTIVE_API_FAILURES = 3
# How long to wait out a locked output.csv (Excel on Windows) before stopping: 15 x 2 s.
CSV_LOCK_RETRIES = 15
CSV_LOCK_POLL_SECS = 2


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
                        help="Reasoning/effort level (output_config.effort). Omit for the model's own default.")
    parser.add_argument("--resume", action="store_true",
                        help="Skip images listed in the output folder's .processed file and append instead of overwriting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print images that would be processed and exit")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-image progress to stdout")
    return parser.parse_args()


def discover_images(input_dir: Path) -> tuple[list[Path], int]:
    """Supported photos in the folder (sorted), and how many HEIC/HEIF files were skipped."""
    files = [p for p in input_dir.iterdir() if p.is_file()]
    return sorted(p for p in files if is_supported_image(p)), sum(1 for p in files if is_heic(p))


def fatal(msg: str, tag: str | None = None) -> None:
    # One line, so the GUI (which explains the last stderr line) never sees the tag cut off.
    msg = " ".join(msg.split())
    print(f"error: [{tag}] {msg}" if tag else f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def write_rows(output_csv: Path, rows: list[list]) -> None:
    """Append rows, waiting out a lock (Excel on Windows) for up to 30 s before giving up."""
    for attempt in range(CSV_LOCK_RETRIES + 1):
        try:
            append_rows(output_csv, rows)
            return
        except OSError as e:
            if attempt == CSV_LOCK_RETRIES:
                fatal(f"Cannot write to {output_csv} ({e}). Close it (e.g. in Excel) and resume.", "csv-locked")
            if attempt == 0:
                print(f"warning: {output_csv} is locked (open in Excel?) — close it; retrying for "
                      f"{CSV_LOCK_RETRIES * CSV_LOCK_POLL_SECS} s", file=sys.stderr, flush=True)
            time.sleep(CSV_LOCK_POLL_SECS)


_RESUME_REFUSALS = {
    "old-format": "output.csv has different columns (written by an older TRON-GRAVE version, or "
                  "re-saved from Excel), so this run can't be resumed. Start a fresh run; back up "
                  "the old file first if you need it.",
    "no-processed": "output.csv has rows but .processed is missing, so resuming would re-send "
                    "(and re-pay for) every image. Start a fresh run instead.",
    "missing-csv": ".processed lists finished images but output.csv is missing or empty. Restore "
                   "output.csv, or delete .processed to start over.",
    "unreadable": "output.csv can't be read, so this run can't be resumed.",
    "not-utf8": "output.csv is no longer UTF-8 (re-saved from Excel or an editor as ANSI), so this "
                "run can't be resumed: appending would mix encodings and a rewrite would damage "
                "the old rows. Re-save it as UTF-8 and resume, or back it up and start a fresh run.",
}


def resume_filter(output_dir: Path) -> set[str]:
    """Filenames --resume skips; exits with [resume-refused] instead of silently re-sending."""
    problem = resume_problem(output_dir)
    if problem:
        fatal(_RESUME_REFUSALS[problem], "resume-refused")
    return read_processed(output_dir)


def drop_rows_being_rerun(output_csv: Path, images: list[Path]) -> None:
    """Remove the rows of the photos about to be re-run, so none appears twice.

    Every other row stays (other photos, rows typed in by hand), and a missing header
    (an emptied output.csv) is put back.
    """
    header, rows = read_csv(output_csv)
    rerun = {csv_text(img.name) for img in images}
    keep = [row for row in rows if not (len(row) > FILE_INDEX and row[FILE_INDEX] in rerun)]
    if not header or len(keep) != len(rows):
        rewrite_rows(output_csv, keep)


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

    output_dir: Path = args.output
    output_csv = output_dir / "output.csv"
    byhand_dir = output_dir / "byhand"

    # samefile, not resolve(): on a case-insensitive filesystem out/ByHand is byhand/ too.
    if os.path.exists(byhand_dir) and os.path.samefile(input_dir, byhand_dir):
        fatal("The input folder is this output folder's byhand/ folder. Choose a different "
              "output folder.", "input-is-byhand")

    images, heic_count = discover_images(input_dir)
    if heic_count:
        print(f"warning: skipping {heic_count} .heic/.heif file(s); convert them to JPG first",
              file=sys.stderr)

    if args.resume:
        processed = resume_filter(output_dir)
        before = len(images)
        images = [img for img in images if img.name not in processed]
        skipped = before - len(images)
        if skipped and args.verbose:
            print(f"Resume: skipping {skipped} already-processed image(s).")

    if args.dry_run:
        for img in images:
            print(img)
        return 0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        fatal("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")

    model = args.model or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL
    if model not in MODEL_PRICING:
        print(f"warning: no price table entry for {model}; costs shown assume $3/$15 per "
              "million tokens", file=sys.stderr)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        fatal(f"Cannot create output directory {output_dir}: {e}")

    try:
        if args.resume and output_csv.exists():
            drop_rows_being_rerun(output_csv, images)
        else:
            init_csv(output_csv)
            init_processed(output_dir)
            clear_byhand(byhand_dir)
    except OSError as e:
        fatal(f"Cannot write to {output_csv}: {e}")

    client = anthropic.Anthropic(api_key=api_key)

    total = len(images)
    succeeded = 0
    partial = 0
    failed = 0
    had_any_issue = False
    total_cost = 0.0
    api_failures_in_row = 0

    for idx, img in enumerate(images, start=1):
        record_id, matched = extract_id(img)
        extra_tags = () if matched else ("ID iz naziva",)

        if args.verbose:
            print(f"[{idx}/{total}] Processing {img.name} ...", flush=True)

        # Excel on Windows locks output.csv while it is open: find out before paying for a call.
        try:
            check_writable(output_csv)
        except OSError as e:
            if args.verbose:
                print(f"[{idx}/{total}] FAILED: {img.name} (output.csv is locked)", flush=True)
            fatal(f"Cannot write to {output_csv} ({e}). Close it (e.g. in Excel) and resume.", "csv-locked")

        try:
            result = process_image(client, model, img, record_id, args.effort, extra_tags)
        except Exception as e:  # one bad photo must not end the whole batch
            result = ImageResult(
                status="total_failure",
                rows=[failure_row(record_id, "neočekivana greška", extra_tags, img.name)],
                reason=f"{type(e).__name__}: {e}",
            )
        total_cost += result.cost
        cost_suffix = f" — ${result.cost:.4f} (total: ${total_cost:.2f})" if result.billed else ""
        # A result stays on one line, cost suffix last: the GUI reads the running total from its
        # end, and the reason can carry model or server text with line breaks.
        reason = " ".join((result.reason or "").split())

        # An account-level failure (revoked key, billing, spend cap, no access, unknown model)
        # dooms every remaining image, and so do three API failures in a row (spend limit,
        # outage): stop at once instead of logging blank rows for the rest of the batch.
        if result.api_failure:
            api_failures_in_row += 1
        elif result.billed:
            api_failures_in_row = 0
        if result.fatal_tag or api_failures_in_row >= MAX_CONSECUTIVE_API_FAILURES:
            if args.verbose:
                print(f"[{idx}/{total}] FAILED: {img.name} ({reason})", flush=True)
            if result.fatal_tag:
                fatal(f"API call failed: {result.reason}", result.fatal_tag)
            fatal(f"{MAX_CONSECUTIVE_API_FAILURES} API calls in a row failed; stopping so the rest "
                  f"can be resumed later. Last error: {result.reason}", "api-down")

        if not matched:
            had_any_issue = True

        write_rows(output_csv, result.rows)

        if result.status == "full_success":
            # A resumed photo that now reads fine no longer needs its review copy.
            try:
                remove_from_byhand(img, byhand_dir)
            except OSError as e:
                print(f"warning: could not remove {img.name} from byhand/: {e}", file=sys.stderr)
        # An API failure is not a review case: resume retries it, and a copy here would
        # have the retry button pay a stronger model to re-send it.
        elif not result.api_failure:
            try:
                copy_to_byhand(img, byhand_dir)
            except OSError as e:
                print(f"warning: could not copy {img.name} to byhand/: {e}", file=sys.stderr)

        # Anything the model answered is done, whatever the verdict: resuming must never pay
        # for it again (review cases wait in byhand/). API and read failures stay out of
        # .processed, so a resume retries them.
        if result.billed:
            mark_processed(output_dir, img)

        if result.status == "full_success":
            succeeded += 1
            n = len(result.rows)
            verdict = f"OK: {img.name} ({n} record{'s' if n != 1 else ''}){cost_suffix}"
        elif result.status == "partial_success":
            partial += 1
            had_any_issue = True
            verdict = f"PARTIAL: {img.name} ({reason}){cost_suffix}"
        else:
            failed += 1
            had_any_issue = True
            verdict = f"FAILED: {img.name} ({reason}){cost_suffix}"
        if args.verbose:
            print(f"[{idx}/{total}] {verdict}", flush=True)

    print(f"Done. {total} images processed. {succeeded} succeeded, {partial} partial, {failed} failed.")
    print(f"Total cost: ${total_cost:.2f}")
    print(f"Output:  {output_csv}")
    byhand_count = (sum(1 for f in byhand_dir.iterdir() if f.is_file() and is_supported_image(f))
                    if byhand_dir.is_dir() else 0)
    if byhand_count > 0:
        print(f"Review:  {byhand_dir}/ ({byhand_count} images)")

    return 2 if had_any_issue else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
