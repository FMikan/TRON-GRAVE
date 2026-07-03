import base64
import io
import random
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
from PIL import Image

from .file_utils import get_mime_type


MAX_IMAGE_BYTES = 3_750_000


SYSTEM_PROMPT = """You are a genealogical data extraction assistant. You will be shown a photograph of a tombstone.

Before anything else, fill in "raw_text": transcribe verbatim everything legible on every marker that
belongs to this grave — names, years, and any other inscribed text — exactly as carved. Do this
transcription first and use it as your working reference for every field below; do not decide a name,
surname, or year status until you have transcribed the relevant text.

Your task:
1. The grave in the foreground may consist of MULTIPLE markers placed together — several plaques,
   headstones and/or crosses. Read and extract EVERY person from ALL markers that belong to this
   one grave; do not stop at a single plaque or cross. Treat markers as the SAME grave when they
   share any of: a common grave frame, border, curb or foundation; the same surname; close physical
   grouping (side by side, touching, or on the same base); or the same orientation, material and
   carving style.
2. IGNORE graves that are clearly separate — in the background, out of focus, or in a different
   plot separated by a gap or path.
3. If it is genuinely unclear whether nearby markers belong to this grave or to a separate
   neighbouring grave, be CONSERVATIVE: do NOT include the uncertain markers, and set
   "ambiguous_multiple_markers" to true so the photo is flagged for manual review. Otherwise set it
   to false.
4. Extract the following fields for each person commemorated on this grave:
   - First name (Name)
   - Family name (Surname)
   - Year of birth (4-digit year only)
   - Year of death (4-digit year only)
5. Return one record per person (a grave with several people yields several records).
6. If you are not 100% certain about name or surname, set that field to null. Do NOT guess or estimate.
7. For the year of birth you MUST set "birth_year_status" for each person:
   - "present": a birth year is clearly legible. Put the 4-digit year in birth_year.
   - "absent_certain": you are 100% certain NO birth year is inscribed. Use this ONLY with concrete
     visual evidence — for example only a death year is shown, or the marker records no birth date.
     Set birth_year to null.
   - "unreadable": a birth year may be present but you cannot read it confidently (worn, obscured,
     partially hidden, ambiguous). Set birth_year to null.
   When in ANY doubt, choose "unreadable" rather than "absent_certain".
8. For the year of death you MUST set "death_year_status" for each person:
   - "present": a death year is clearly legible. Put the 4-digit year in death_year.
   - "absent_certain": you are 100% certain NO death year exists. Use this ONLY with concrete
     visual evidence — for example only a birth year is inscribed, or a dash / blank space follows
     the birth year ("1950 -", "1950 - 20"), indicating the person is most likely still alive.
     Set death_year to null.
   - "unreadable": a death year may be present but you cannot read it confidently (worn, obscured,
     partially hidden, ambiguous). Set death_year to null.
   When in ANY doubt, choose "unreadable" rather than "absent_certain".
9. "note": usually null. Only for a genuine edge case, a Croatian note of at most ~6 words
   (e.g. "osoba živa", "prezime nečitko"). Keep it as short as possible.
10. If the image is completely unreadable, set records to [] and explain why in the error field in
   one short sentence (max ~10 words).
11. All text in the output (names, surnames, notes, error messages) must be in Croatian.
12. If any text on the tombstone is written in Cyrillic script, transliterate it to Croatian Latin script.

Worked examples:
- "IVAN HORVAT 1950 – 2010" (both years clear): birth_year_status="present" (1950),
  death_year_status="present" (2010).
- "MARIJA HORVAT 1955 –" (a dash with nothing after it): birth_year_status="present" (1955),
  death_year_status="absent_certain" — the dash is concrete evidence of no death year yet.
- "1950 – 20" (second date cut off or worn to two digits): death_year_status="unreadable" —
  a year was clearly started but cannot be confirmed, so treat it as unreadable, not absent.
- Only a birth year is carved, with no dash and no visible space left for a second date:
  birth_year_status="present", death_year_status="absent_certain".
- A worn, chipped corner obscures what would be the birth year, but the death year is crisp:
  birth_year_status="unreadable", death_year_status="present".
- Two crosses on the same concrete base, both carved "HORVAT", touching each other: they are the
  SAME grave — extract one record per cross.
- A second, unrelated headstone is visible, blurred, in the background: ignore it entirely — it is
  a separate grave, not part of this one.
- A plaque commemorates "IVAN HORVAT 1920–1999" and "MARIJA HORVAT r. KOVAČ 1925–2015" on the same
  stone: two people, two records, same surname family — this is one grave with two people, not two
  graves.
- Text is carved in Cyrillic, e.g. "ХОРВАТ" for the surname: transliterate to Croatian Latin script
  as "Horvat" before writing the output field; do not leave any field in Cyrillic characters."""


_EXTRACT_TOOL = {
    "name": "extract_burial_records",
    "description": "Record the burial data extracted from the tombstone photograph.",
    "input_schema": {
        "type": "object",
        "properties": {
            # Internal scratchpad only: forces the model to read the whole stone before
            # classifying, which improves the structured fields. Not written to the CSV.
            "raw_text": {
                "type": "string",
                "description": "Verbatim transcription of everything legible on every marker belonging "
                                "to this grave, written before any other field. This is your working "
                                "transcript — read carefully first, then extract the structured fields "
                                "below from it.",
            },
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":       {"type": ["string", "null"]},
                        "surname":    {"type": ["string", "null"]},
                        "birth_year": {"type": ["integer", "null"]},
                        "birth_year_status": {
                            "type": "string",
                            "enum": ["present", "absent_certain", "unreadable"],
                            "description": "present = legible; absent_certain = surely none (concrete evidence); unreadable = cannot read.",
                        },
                        "death_year": {"type": ["integer", "null"]},
                        "death_year_status": {
                            "type": "string",
                            "enum": ["present", "absent_certain", "unreadable"],
                            "description": "present = legible; absent_certain = surely none (concrete evidence); unreadable = cannot read.",
                        },
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["name", "surname", "birth_year", "birth_year_status", "death_year", "death_year_status", "note"],
                },
            },
            "error": {"type": ["string", "null"]},
            "ambiguous_multiple_markers": {
                "type": "boolean",
                "description": "true if nearby plaques/crosses might belong to this grave but "
                               "could not be included confidently (needs manual review); else false.",
            },
        },
        "required": ["raw_text", "records", "error", "ambiguous_multiple_markers"],
    },
}

# Cache the system prompt + tool definition (stable across every image in a run).
# Below the model's minimum cacheable prefix (~2048 tok on Sonnet, ~4096 on Opus)
# this silently has no effect and no extra cost -- see shared/prompt-caching.md.
_SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

# USD per million tokens: (input, output). Cache write = input_rate*1.25, cache read = input_rate*0.1.
MODEL_PRICING = {
    "claude-sonnet-5":   (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8":   (5.00, 25.00),
}
_DEFAULT_PRICING = (3.00, 15.00)


def _compute_cost(model: str, usage) -> float:
    """Real USD cost of one API call, from the response's actual token usage."""
    input_rate, output_rate = MODEL_PRICING.get(model, _DEFAULT_PRICING)
    base_in = getattr(usage, "input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (
        base_in * input_rate
        + cache_write * input_rate * 1.25
        + cache_read * input_rate * 0.1
        + out * output_rate
    ) / 1_000_000


_RETRY_DELAYS = [2, 4, 8]
_RECORD_FIELDS = ('name', 'surname', 'birth_year', 'death_year')
# Only a missing name/surname forces manual review; birth/death years get a
# certainty status so a *certain* absence can still pass as OK.
_CORE_FIELDS = ('name', 'surname')
_CORE_LABELS_HR = {'name': 'ime', 'surname': 'prezime'}

# Index of the Notes cell in a row, so callers can append to it.
NOTE_INDEX = 5
_MAX_NOTE_CHARS = 60


@dataclass
class ImageResult:
    status: str
    rows: list[list]
    reason: str | None
    fatal_api_error: bool = False
    cost: float = 0.0


def _empty_row(record_id: str, note: str = "") -> list:
    return [record_id, "", "", "", "", note]


def _year_state(rec: dict, year_field: str, status_field: str) -> str:
    """Resolve a year's situation defensively, never trusting a lone flag.

    A concrete year always wins. A null only counts as a certain absence when the
    model explicitly says so; anything else (incl. a 'present' flag with no year)
    falls back to 'unreadable' so the image is sent to byhand, not passed as OK.
    """
    if rec.get(year_field) is not None:
        return "present"
    if rec.get(status_field) == "absent_certain":
        return "absent_certain"
    return "unreadable"


def _birth_state(rec: dict) -> str:
    return _year_state(rec, "birth_year", "birth_year_status")


def _death_state(rec: dict) -> str:
    return _year_state(rec, "death_year", "death_year_status")


def _build_note(rec: dict, birth_state: str, death_state: str) -> str:
    """Short Croatian note for the CSV Notes column (edge cases only)."""
    bits = []
    missing = [_CORE_LABELS_HR[f] for f in _CORE_FIELDS if rec.get(f) is None]
    if missing:
        bits.append("fali: " + ", ".join(missing))

    # Combine the two "certain absence" cases for brevity.
    if birth_state == "absent_certain" and death_state == "absent_certain":
        bits.append("bez god. rođ. i smrti")
    else:
        if birth_state == "absent_certain":
            bits.append("bez god. rođenja")
        if death_state == "absent_certain":
            bits.append("bez god. smrti")

    if birth_state == "unreadable":
        bits.append("god. rođenja nečitka")
    if death_state == "unreadable":
        bits.append("god. smrti nečitka")
    base = "; ".join(bits)

    model_note = (rec.get("note") or "").strip()
    if model_note and base:
        note = f"{base} — {model_note}"
    elif model_note:
        note = model_note
    else:
        note = base
    return note[:_MAX_NOTE_CHARS]


def _record_to_row(record_id: str, rec: dict, note: str = "") -> list:
    return [
        record_id,
        rec.get("name") or "",
        rec.get("surname") or "",
        rec.get("birth_year") if rec.get("birth_year") is not None else "",
        rec.get("death_year") if rec.get("death_year") is not None else "",
        note,
    ]


def _recompress(raw: bytes, max_bytes: int) -> bytes | None:
    """Return JPEG bytes <= max_bytes by reducing quality then resolution, or None."""
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:
        return None
    if img.mode not in ('RGB',):
        img = img.convert('RGB')
    for scale in (1.0, 0.5, 0.25):
        w, h = img.size
        sized = img if scale == 1.0 else img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS
        )
        for quality in (85, 70, 55, 40, 25, 10):
            buf = io.BytesIO()
            sized.save(buf, format='JPEG', quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                return data
    return None


def _jittered(delay: float) -> float:
    return delay * (1 + random.uniform(-0.25, 0.25))


def _call_api(client, model: str, mime: str, b64: str, effort: str | None = None):
    # temperature is only accepted on the Sonnet 4.x family. Sonnet 5, Opus 4.7/4.8
    # and Fable reject it with 400 "temperature is deprecated for this model".
    params: dict = {"temperature": 0} if "sonnet-4" in model else {}
    if effort:
        params["output_config"] = {"effort": effort}
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM_BLOCKS,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_burial_records"},
        **params,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime,
                        "data": b64,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract the burial records from this tombstone.",
                },
            ],
        }],
    )


def process_image(client, model: str, path: Path, record_id: str,
                  effort: str | None = None) -> ImageResult:
    try:
        raw = path.read_bytes()
    except (IOError, OSError):
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "ne mogu otvoriti")],
            reason="File could not be opened or decoded on disk",
        )

    mime = get_mime_type(path)

    if len(raw) > MAX_IMAGE_BYTES:
        raw = _recompress(raw, MAX_IMAGE_BYTES)
        if raw is None:
            return ImageResult(
                status='total_failure',
                rows=[_empty_row(record_id, "slika prevelika")],
                reason="Image too large and could not be recompressed to fit API limit",
            )
        mime = "image/jpeg"

    b64 = base64.b64encode(raw).decode("ascii")

    last_error: Exception | None = None
    response = None
    for attempt in range(4):
        try:
            response = _call_api(client, model, mime, b64, effort)
            break
        except anthropic.APIStatusError as e:
            status_code = getattr(e, 'status_code', None)
            if status_code is not None and status_code >= 500:
                last_error = e
            else:
                return ImageResult(
                    status='total_failure',
                    rows=[_empty_row(record_id, "greška API-ja")],
                    reason=str(e),
                    fatal_api_error=True,
                )
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
            last_error = e

        if attempt < 3:
            time.sleep(_jittered(_RETRY_DELAYS[attempt]))

    if response is None:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "greška API-ja")],
            reason=f"API call failed after retries: {last_error}",
        )

    # A response was received, so this call is billed regardless of how well the
    # model extracted the data -- attach the real cost to every return from here on.
    cost = _compute_cost(model, response.usage)

    data = response.content[0].input
    records = data.get("records", [])
    error = data.get("error")

    if error is not None and not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, (error or "").strip()[:_MAX_NOTE_CHARS])],
            reason=error,
            cost=cost,
        )

    if not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "nema podataka")],
            reason="Model returned no records",
            cost=cost,
        )

    all_empty = all(
        all(rec.get(field) is None for field in _RECORD_FIELDS)
        for rec in records
    )
    if all_empty:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "sve nečitko")],
            reason="All fields illegible",
            cost=cost,
        )

    # Per-record: resolve the birth/death-year states and build the Croatian note.
    birth_states = [_birth_state(rec) for rec in records]
    death_states = [_death_state(rec) for rec in records]
    rows = [
        _record_to_row(record_id, rec, _build_note(rec, bs, ds))
        for rec, bs, ds in zip(records, birth_states, death_states)
    ]

    # The model left out nearby markers it wasn't sure belong to this grave.
    # Flag every row so the photo can be checked manually for missed people.
    ambiguous = bool(data.get("ambiguous_multiple_markers"))
    if ambiguous:
        for row in rows:
            tag = "provjeri: možda više oznaka"
            row[NOTE_INDEX] = f"{row[NOTE_INDEX]}; {tag}" if row[NOTE_INDEX] else tag

    has_missing_core = any(
        any(rec.get(field) is None for field in _CORE_FIELDS)
        for rec in records
    )
    has_uncertain_year = any(s == "unreadable" for s in birth_states + death_states)

    # Priority: a missing name/surname always wins, then an unreadable birth/death
    # year, then an ambiguous multi-marker grave. A *certain* absence of a birth or
    # death year needs no review -> OK, the note explains it.
    if has_missing_core:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="Name or surname could not be read",
            cost=cost,
        )

    if has_uncertain_year:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="Model not certain whether a year of birth or death exists",
            cost=cost,
        )

    if ambiguous:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="Multiple nearby markers — verify they all belong to this grave",
            cost=cost,
        )

    return ImageResult(
        status='full_success',
        rows=rows,
        reason=None,
        cost=cost,
    )
