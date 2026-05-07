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
4. If you are not 100% certain about a field, set it to null. Do NOT guess or estimate.
   - If only one complete 4-digit year is visible for a person, treat it as birth_year and set death_year to null.
   - If a second year is incomplete or partially obscured (e.g. "1950 - 20"), use the first as birth_year and set death_year to null.
5. If the image is completely unreadable, set records to [] and explain why in the error field in one short sentence (max ~10 words).
6. All text in the output (names, surnames, error messages) must be in Croatian.
7. If any text on the tombstone is written in Cyrillic script, transliterate it to Croatian Latin script."""


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
                    },
                    "required": ["name", "surname", "birth_year", "death_year"],
                },
            },
            "error": {"type": ["string", "null"]},
        },
        "required": ["records", "error"],
    },
}

_RETRY_DELAYS = [2, 4, 8]
_RECORD_FIELDS = ('name', 'surname', 'birth_year', 'death_year')


@dataclass
class ImageResult:
    status: str
    rows: list[list]
    reason: str | None
    fatal_api_error: bool = False


def _empty_row(record_id: str) -> list:
    return [record_id, "", "", "", ""]


def _record_to_row(record_id: str, rec: dict) -> list:
    return [
        record_id,
        rec.get("name") or "",
        rec.get("surname") or "",
        rec.get("birth_year") if rec.get("birth_year") is not None else "",
        rec.get("death_year") if rec.get("death_year") is not None else "",
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
            rows=[_empty_row(record_id)],
            reason="File could not be opened or decoded on disk",
        )

    mime = get_mime_type(path)

    if len(raw) > MAX_IMAGE_BYTES:
        raw = _recompress(raw, MAX_IMAGE_BYTES)
        if raw is None:
            return ImageResult(
                status='total_failure',
                rows=[_empty_row(record_id)],
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
                    rows=[_empty_row(record_id)],
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
            rows=[_empty_row(record_id)],
            reason=f"API call failed after retries: {last_error}",
        )

    data = response.content[0].input
    records = data.get("records", [])
    error = data.get("error")

    if error is not None and not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id)],
            reason=error,
        )

    if not records:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id)],
            reason="Model returned no records",
        )

    all_empty = all(
        all(rec.get(field) is None for field in _RECORD_FIELDS)
        for rec in records
    )
    if all_empty:
        return ImageResult(
            status='total_failure',
            rows=[_empty_row(record_id)],
            reason="All fields illegible",
        )

    any_missing = any(
        any(rec.get(field) is None for field in _RECORD_FIELDS)
        for rec in records
    )
    rows = [_record_to_row(record_id, rec) for rec in records]

    if any_missing:
        return ImageResult(
            status='partial_success',
            rows=rows,
            reason="One or more fields left empty due to illegibility",
        )

    return ImageResult(
        status='full_success',
        rows=rows,
        reason=None,
    )
