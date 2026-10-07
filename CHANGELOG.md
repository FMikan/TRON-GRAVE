# Changelog

All notable changes to TRON-GRAVE. Versions follow the git tags.

## Unreleased

Fixes from the 7 October 2026 QA & UI/UX review.

### Fixed
- CLI: a run without `--resume` no longer wipes an `output.csv` that has rows (`[output-exists]`), and an input folder without photos stops before anything is written (`[no-images]`) (QA-01).
- Pokreni asks before a run estimated at $2 or more (QA-02).
- The status line and the summary say how to open the comma-separated CSV in a Croatian Excel (QA-03).
- Pokreni's text passes WCAG AA, and keyboard focus is visible on every button and checkbox (QA-04).
- The log reads in Croatian, coloured by verdict; Tehnički zapis shows the extractor's own lines (QA-05).
- Fatal errors name their fix instead of showing the raw API reply (QA-06).
- Decisions are answered with verb buttons instead of Yes/No (QA-07).
- The summary offers the photos to review and a priced retry with a stronger model (QA-08).
- The window stays usable at 150–175 % scaling: sizes scale, labels wrap, and the minimum height keeps the buttons and four log lines (QA-09).
- Nastavi is greyed out, with Otvori CSV offered, when every photo is already done (QA-10).
- Prepiši also sets the old `byhand_retry/` aside (QA-11).
- A folder whose photos are in subfolders says so (QA-12).
- Log search shows "3/12" or "Nema rezultata" and goes back with Shift+Enter (QA-13).
- First launch names the next setup step (QA-14).
- Path fields show the folder name and the full path on hover (QA-18).
- Custom dialogs open centred on the main window (QA-19).
- Learned estimates count only billed photos and their own time (QA-24).

### Changed
- One Otvori menu replaces the two open buttons; Probni prolaz is its own button; the retry button reads "Ponovno obradi jačim modelom…" (QA-15).
- The checkbox, the log border and the version label follow the dark theme (QA-16).
- Fonts fall back to Tk's own, in bold, where Segoe UI is missing (QA-17).
- The API key field has Prikaži, Spremi ključ, Enter to save, and says where an empty field's key comes from (QA-21).
- A lock left on this computer by a run that no longer exists is taken over (QA-23).

### Added
- `--overwrite` on the CLI (QA-01).
- `logs/tron-grave-<time>.log` for every GUI run (QA-22).
- An app icon and Windows version details for the exe (QA-20).
- This changelog and a CI workflow; the 3.6.0 tag (QA-25).

## 3.6.0 — 2026-10-01

Fixes from the 29 September 2026 review (35 findings).

- Extraction: structured JSON output with streaming; every photo normalised before upload (rotation, size, real format); the written reasoning step left out for models that decline it; Sonnet 5.5, Opus 5.5 and Fable 5.1 priced and offered.
- Results: a File column; review tags never cut from Notes; doubtful answers sent to review.
- Resume: a safe `--resume` (no re-payment, no duplicate rows, no stale byhand copies) that refuses unsafe folders with a reason; Nastavi / Prepiši / Odustani in the GUI.
- Robustness: the run stops on billing, spend-cap and repeated API failures; a bad photo or a locked output.csv no longer ends the batch.
- GUI: Croatian interface; settings in the OS config folder; cost and time estimates learned from real runs; Ponovi byhand/ with a model never weaker than yours; DPI awareness and a clean Stop on Windows.
- Build: Python version check, broken-venv rebuild, no UPX packing.

Earlier versions: see the git tags 3.2.0 – 3.5.1.
