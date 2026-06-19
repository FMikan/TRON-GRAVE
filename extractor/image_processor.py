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

Your task:
1. Read ONLY the tombstone that is in the foreground and in focus. Ignore any other graves,
   monuments, or text visible in the background.
2. Extract the following fields for each person commemorated on this tombstone:
   - First name (Name)
   - Family name (Surname)
   - Year of birth (4-digit year only)
   - Year of death (4-digit year only)
3. If a tombstone commemorates more than one person, return one record per person.
4. If you are not 100% certain about name, surname or birth_year, set that field to null.
   Do NOT guess or estimate.
5. For the year of death you MUST set "death_year_status" for each person:
   - "present": a death year is clearly legible. Put the 4-digit year in death_year.
   - "absent_certain": you are 100% certain NO death year exists. Use this ONLY with concrete
     visual evidence — for example only a birth year is inscribed, or a dash / blank space follows
     the birth year ("1950 -", "1950 - 20"), indicating the person is most likely still alive.
     Set death_year to null.
   - "unreadable": a death year may be present but you cannot read it confidently (worn, obscured,
     partially hidden, ambiguous). Set death_year to null.
   When in ANY doubt, choose "unreadable" rather than "absent_certain".
6. "note": a SHORT note in Croatian (max ~10 words) ONLY for edge cases — e.g. why there is no
   year of death, or which detail is illegible. Otherwise set it to null.
7. If the image is completely unreadable, set records to [] and explain why in the error field in
   one short sentence (max ~10 words).
8. All text in the output (names, surnames, notes, error messages) must be in Croatian.
9. If any text on the tombstone is written in Cyrillic script, transliterate it to Croatian Latin script."""


_EXTRACT_TOOL = {
    "name": "extract_burial_records",
    "description": "Record the burial data extracted from the tombstone photograph.",
    "input_schema": {
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name":       {"type": ["string", "null"]},
                        "surname":    {"type": ["string", "null"]},
                        "birth_year": {"type": ["integer", "null"]},
                        "death_year": {"type": ["integer", "null"]},
                        "death_year_status": {
                            "type": "string",
                            "enum": ["present", "absent_certain", "unreadable"],
                            "description": "present = legible; absent_certain = surely none (concrete evidence); unreadable = cannot read.",
                        },
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["name", "surname", "birth_year", "death_year", "death_year_status", "note"],
                },
            },
            "error": {"type": ["string", "null"]},
        },
        "required": ["records", "error"],
    },
}

_RETRY_DELAYS = [2, 4, 8]
_RECORD_FIELDS = ('name', 'surname', 'birth_year', 'death_year')
# name/surname/birth_year missing -> always needs manual review (byhand).
_CORE_FIELDS = ('name', 'surname', 'birth_year')
_CORE_LABELS_HR = {'name': 'ime', 'surname': 'prezime', 'birth_year': 'godina rođenja'}

# Index of the Notes cell in a row, so callers can append to it.
NOTE_INDEX = 5
_MAX_NOTE_CHARS = 120


@dataclass
class ImageResult:
    status: str
    rows: list[list]
    reason: str | None
    fatal_api_error: bool = False


def _empty_row(record_id: str, note: str = "") -> list:
    return [record_id, "", "", "", "", note]


def _death_state(rec: dict) -> str:
    """Resolve the death-year situation defensively, never trusting a lone flag.

    A concrete year always wins. A null only counts as a certain absence when the
    model explicitly says so; anything else (incl. a 'present' flag with no year)
    falls back to 'unreadable' so the image is sent to byhand, not passed as OK.
    """
    if rec.get("death_year") is not None:
        return "present"
    if rec.get("death_year_status") == "absent_certain":
        return "absent_certain"
    return "unreadable"


def _build_note(rec: dict, death_state: str) -> str:
    """Short Croatian note for the CSV Notes column (edge cases only)."""
    bits = []
    missing = [_CORE_LABELS_HR[f] for f in _CORE_FIELDS if rec.get(f) is None]
    if missing:
        bits.append("nedostaje: " + ", ".join(missing))
    if death_state == "absent_certain":
        bits.append("bez godine smrti")
    elif death_state == "unreadable":
        bits.append("godina smrti nečitka")
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


def _call_api(client, model: str, mime: str, b64: str):
    extra = {} if "opus" in model else {"temperature": 0}
    return client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        **extra,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_burial_records"},
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


def process_image(client, model: str, path: Path, record_id: str) -> ImageResult:
    try:
        raw = path.read_bytes()
    except (IOError, OSError):
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "datoteka se ne može otvoriti")],
            reason="File could not be opened or decoded on disk",
        )

    mime = get_mime_type(path)

    if len(raw) > MAX_IMAGE_BYTES:
        raw = _recompress(raw, MAX_IMAGE_BYTES)
        if raw is None:
            return ImageResult(
                status='total_failure',
                rows=[_empty_row(record_id, "slika prevelika za obradu")],
                reason="Image too large and could not be recompressed to fit API limit",
            )
        mime = "image/jpeg"

    b64 = base64.b64encode(raw).decode("ascii")

    last_error: Exception | None = None
    response = None
    for attempt in range(4):
        try:
            response = _call_api(client, model, mime, b64)
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
            rows=[_empty_row(record_id, "greška API-ja nakon ponovnih pokušaja")],
            reason=f"API call failed after retries: {last_error}",
        )

    data = response.content[0].input
    records = data.get("records", [])
    error = data.get("error")

    if error is not None and not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, (error or "").strip()[:_MAX_NOTE_CHARS])],
            reason=error,
        )

    if not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "model nije vratio podatke")],
            reason="Model returned no records",
        )

    all_empty = all(
        all(rec.get(field) is None for field in _RECORD_FIELDS)
        for rec in records
    )
    if all_empty:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id, "sva polja nečitka")],
            reason="All fields illegible",
        )

    # Per-record: resolve the death-year state and build the Croatian note.
    death_states = [_death_state(rec) for rec in records]
    rows = [
        _record_to_row(record_id, rec, _build_note(rec, ds))
        for rec, ds in zip(records, death_states)
    ]

    has_missing_core = any(
        any(rec.get(field) is None for field in _CORE_FIELDS)
        for rec in records
    )
    has_uncertain_death = any(ds == "unreadable" for ds in death_states)

    # Priority: a missing core field always wins, then an unreadable death year.
    # A certain-absent death year needs no review -> OK, the note explains it.
    if has_missing_core:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="One or more fields left empty due to illegibility",
        )

    if has_uncertain_death:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="Model not certain whether a year of death exists",
        )

    return ImageResult(
        status='full_success',
        rows=rows,
        reason=None,
    )
