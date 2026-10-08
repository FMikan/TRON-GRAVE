import base64
import copy
import datetime
import io
import json
import math
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic
import httpx
from PIL import Image, ImageOps

from .pricing import compute_cost


# 200 MP phone photos (16320x12240) are real input; Pillow's default decompression-bomb
# guard (~179 MP) would refuse them. JPEGs that large are decoded shrunk via draft().
Image.MAX_IMAGE_PIXELS = 250_000_000

# What the vision models actually use (long edge 2576 px, 4,784 visual tokens): anything
# larger is downscaled server-side, so sending more only costs upload time, and anything
# over 8000 px is rejected outright. 3.6 MP keeps the ceil-rounded token count under 4,784.
MAX_LONG_EDGE = 2576
MAX_PIXELS = 3_600_000
# The API caps each image at 5 MiB of base64 (the SDK's agent toolset uses the same limit),
# so at most 3.75 MiB of raw bytes go out as they are; anything larger is re-encoded.
MAX_SEND_BYTES = (5 * 1024 * 1024 // 4) * 3
_JPEG_QUALITY = 90
_MEDIA_TYPES = {"JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_SENDABLE_MODES = {"RGB", "RGBA", "L", "LA", "P"}
_EXIF_ORIENTATION = 0x0112


SYSTEM_PROMPT = """You are a genealogical data extraction assistant. You will be shown a photograph of a tombstone.

Work in this exact order, filling BOTH scratchpad fields before any structured field:
1. "raw_text": transcribe verbatim everything legible on every marker that belongs to this grave —
   names, years, and any other inscribed text — exactly as carved.
2. "reasoning": think, in words, about what the transcription means before you commit to any value.
   This is where you work out the hard cases: which markers form one grave; how to reduce each name
   to the pure first name and surname (dropping titles like "dr." and maiden names like "r. Kovač");
   how to convert names from an oblique case back to the nominative ("Vječni dom Ivana Horvata" →
   Ivan Horvat); and any year you can derive with HIGH confidence (a birth year from a death year plus an age
   at death, or a worn digit resolved by a spouse's clearly-legible year). State your conclusion for
   each person here. Do NOT decide a name, surname, or year status until you have reasoned it through.
Then fill the structured fields below from your reasoning.

Your task:
1. The grave in the foreground may consist of MULTIPLE markers placed together — several plaques,
   headstones and/or crosses. Read and extract every person from the markers you have CONFIRMED
   belong to this one grave; do not stop at a single plaque or cross. Treat markers as the SAME grave
   ONLY when they share a physical structure — a common grave frame, border, curb, foundation or base,
   or are directly touching. A shared surname, orientation, material or carving style is corroborating
   evidence but is NEVER sufficient on its own: markers that share only those, with any visible gap or
   path between them, are SEPARATE graves (a whole row of same-surname family graves is common).
2. IGNORE graves that are clearly separate — in the background, out of focus, or in a different
   plot separated by a gap or path.
3. If it is genuinely unclear whether nearby markers belong to this grave or to a separate
   neighbouring grave, be CONSERVATIVE: do NOT include the uncertain markers, and set
   "ambiguous_multiple_markers" to true so the grave is flagged in the output notes. Otherwise set
   it to false.
4. Extract these four pure values for each person BURIED in this grave — nothing more. Do NOT create
   a record for the living: names in a mourning / dedication block (the people who erected the stone
   or who grieve) are not buried here. Such blocks are introduced by phrases like "Ožalošćeni",
   "Tugujući", "S ljubavlju" / "S tugom", "Uspomenu čuvaju", "Podigli / Podigla / Podigoše",
   "Sjećaju se", and usually continue with relationship words (supruga, suprug, sinovi, kćeri,
   unučad, obitelj) and carry NO years. Treat a name as a mourner ONLY when it sits inside such a
   block; do NOT drop a genuinely buried person just because their year is worn off. The four values:
   - First name (Name) — the given name ONLY. Strip every title or honorific (e.g. "dr.", "prof.",
     "mr. sc.", "ing.", "akad.", "vlč.", "fra", "don", "gđa"); a title is never part of the name.
     A compound/double given name is ONE name — keep both parts (e.g. "Ana Marija", "Josip Juraj");
     do not drop the second part or mistake it for the surname.
   - Family name (Surname) — the person's OWN family name only. If a maiden/birth name is also given
     (Croatian "r." or "rođ." = née, e.g. "HORVAT r. KOVAČ"), keep ONLY the primary carved surname
     ("Horvat") and DROP the maiden name together with the "r."/"rođ." marker. A patronymic — "pok."
     (pokojni = the late) plus the father's name in the genitive, e.g. "IVAN HORVAT pok. PETRA" (Ivan
     Horvat, son of the late Petar) — is NOT a surname; drop "pok. Petra" (the surname stays "Horvat").
     A surname carved only ONCE for the whole grave — a large header across the top, or "OBITELJ …" /
     "PORODICA …" (family of…) — is the family name of EVERY first name listed below it; assign it to
     each such person, do NOT leave surname null (a person with a DIFFERENT surname carved beside their
     own name keeps that one). The words "obitelj" / "porodica" are themselves NEVER a first name; a
     stone showing only a family surname with no individual names or years yields exactly ONE record —
     surname = that family name, name = null — do not invent a first name or return an empty list.
   - Year of birth (4-digit year only)
   - Year of death (4-digit year only)
   Give the name and surname in their base NOMINATIVE (dictionary) form. Croatian dedications often
   inscribe names in an oblique case — most commonly the genitive after possessive phrases such as
   "Vječni dom …", "Grob …", "Počivalište …" or "U spomen …" — so convert them back to nominative,
   using the surname's own case ending to fix the case: in "Vječni dom Ivana Horvata" the surname
   "Horvata" is genitive (nominative "Horvat"), so the whole phrase is genitive and the person is
   Ivan Horvat, not "Ivana"/"Horvata". Normalise only when the construction makes the case clear —
   in particular, do NOT force a bare first name whose genitive form is also a real nominative name
   (e.g. "Mare", "Pave"); if you are unsure whether a form is already nominative, keep it as carved.
5. Return one record per PERSON — the person is the unit, not the marker. A grave with several people
   yields several records; but if the SAME individual (same name and years) is inscribed on more than
   one marker of this grave (e.g. a photo-plaque and an engraved cross), record them ONCE, not once
   per marker.
6. Accuracy over completeness — NEVER guess. If you are not highly confident about a name or surname,
   set it to null rather than invent one. You MAY INFER a value the stone does not state outright when
   the transcription lets you conclude it with HIGH confidence — reasoning, not guessing. The clearest
   case is arithmetic: if a death year and an age at death are inscribed but the birth year is not,
   compute it (e.g. "umrla 1987. u 48. godini" → rođ. 1939); likewise derive a missing death year
   from a birth year plus a stated age. Croatian "u N. godini (života)" = the N-th year of life (N−1
   completed years), so a computed year may be off by ±1 — acceptable, because it is tagged as
   inferred (see below). Only commit an inferred value when you are confident it is essentially
   correct; otherwise leave the year null with status "unreadable" (or "absent_certain" only if you are
   100% sure none is inscribed). Finally, sanity-check the pair: a birth year
   must not be later than the death year (equal is allowed for an infant), and the lifespan should be
   broadly plausible — a birth after death, or an obviously impossible age, means a digit was misread,
   so re-examine it. If you still cannot make the pair consistent, set the less certain year's status
   to "unreadable" (its value null, so the photo is reviewed) and add a short note "provjeri godine".
7. For the year of birth you MUST set "birth_year_status" for each person:
   - "present": a full 4-digit birth year is clearly legible. Put the 4-digit year in birth_year.
   - "inferred": the birth year is not fully legible as carved — not inscribed at all, OR inscribed
     with a worn/damaged digit — but you derived it with HIGH confidence from other inscribed facts
     (e.g. death year minus age at death, or a worn digit resolved from a spouse's clearly-legible
     year). Put the computed 4-digit year in birth_year. Use only when confident; if unsure, do not infer.
   - "absent_certain": no birth year to record. Use this when you are 100% certain none is
     inscribed (for example only a death year is shown, or the marker records no birth date), OR
     when a year is INCOMPLETE — fewer than four digit positions are carved or remain (the year was
     cut off, or only one or two digits were ever there, e.g. "19" or "20"). A single worn digit in
     an otherwise four-position year is NOT "incomplete" — that is "unreadable" (or "inferred" if you
     can resolve it). An incomplete year is treated as missing, not as something to re-read: set
     birth_year to null.
   - "unreadable": a full year is clearly present (four digit positions) but you cannot read the
     digits confidently (worn, obscured, partially hidden, ambiguous). Set birth_year to null.
   When in ANY doubt, choose "unreadable" rather than "absent_certain" — EXCEPT that an incomplete
   year (fewer than four digit positions, such as "20") is ALWAYS "absent_certain", never "unreadable".
   A "present" or "inferred" status counts only if you actually put the 4-digit number in the year
   field — a status with a null year is treated as "unreadable" and sent for manual review.
8. For the year of death you MUST set "death_year_status" for each person:
   - "present": a full 4-digit death year is clearly legible. Put the 4-digit year in death_year.
   - "inferred": the death year is not fully legible as carved — not inscribed at all, OR inscribed
     with a worn/damaged digit — but you derived it with HIGH confidence from other inscribed facts
     (e.g. birth year plus a stated age at death, or a worn digit resolved from a spouse's year). Put
     the computed 4-digit year in death_year. Use only when confident; if unsure, do not infer.
   - "absent_certain": no death year to record. Use this when you are 100% certain none exists (for
     example only a birth year is inscribed, or a dash / blank space follows the birth year, e.g.
     "1950 -", indicating the person is most likely still alive), OR when a year is INCOMPLETE —
     fewer than four digit positions are carved or remain, e.g. "1950 - 20" (a dash then a cut-off
     second date). A single worn digit in an otherwise four-position year is NOT "incomplete" — that
     is "unreadable" (or "inferred" if you can resolve it). An incomplete year is treated as missing,
     not as something to re-read: set death_year to null.
   - "unreadable": a full year is clearly present (four digit positions) but you cannot read the
     digits confidently (worn, obscured, partially hidden, ambiguous). Set death_year to null.
   When in ANY doubt, choose "unreadable" rather than "absent_certain" — EXCEPT that an incomplete
   year (fewer than four digit positions, such as "20") is ALWAYS "absent_certain", never "unreadable".
   A "present" or "inferred" status counts only if you actually put the 4-digit number in the year
   field — a status with a null year is treated as "unreadable" and sent for manual review.
9. "note": usually null. The system already records missing name/surname and each year's status
   automatically in the same note cell, and your note is appended after that — so keep it to a
   few words and add ONLY what those fields cannot convey (e.g. "osoba živa"
   to mark a living person, "spomenik oštećen", "dvije obitelji"). Do NOT restate what a null
   name/surname or a year status already says.
10. If the image is completely unreadable, set records to [] and explain why in the error field in
   one short sentence (max ~10 words).
11. All text in the output (names, surnames, notes, error messages) must be in Croatian.
12. If any text on the tombstone is written in Cyrillic script, transliterate it to Croatian Latin script.

Worked examples:
- "IVAN HORVAT 1950 – 2010" (both years clear): birth_year_status="present" (1950),
  death_year_status="present" (2010).
- "MARIJA HORVAT 1955 –" (a dash with nothing after it): birth_year_status="present" (1955),
  death_year_status="absent_certain" — the dash is concrete evidence of no death year yet.
- "1950 – 20" (the second date was cut off / never completed — only two digit positions, an
  INCOMPLETE year): birth_year_status="present" (1950), death_year_status="absent_certain" — fewer
  than four digit positions are present, so the death year is treated as missing. Do NOT mark it
  "unreadable"; an incomplete year must not be sent for manual review.
- Only a birth year is carved, with no dash and no visible space left for a second date:
  birth_year_status="present", death_year_status="absent_certain".
- A worn, chipped corner obscures what would be the birth year, but the death year is crisp:
  birth_year_status="unreadable", death_year_status="present".
- Two crosses on the same concrete base, both carved "HORVAT", touching each other: they are the
  SAME grave — extract every person from both crosses (here two different Horvats → two records, one
  per person).
- A second, unrelated headstone is visible, blurred, in the background: ignore it entirely — it is
  a separate grave, not part of this one.
- A plaque commemorates "IVAN HORVAT 1920–1999" and "dr. MARIJA HORVAT r. KOVAČ 1925–2015" on the
  same stone: two people, two records, one grave (not two). Record Marija's surname as just "Horvat"
  — DROP the maiden name "r. Kovač" and the title "dr."; names carry no titles or maiden names.
- Header "OBITELJ HORVAT" (or just "HORVAT" large across the top) over "IVAN 1920–1999" and
  "MARA 1925–2015": two records — Ivan Horvat and Mara Horvat; the shared header surname applies to
  both, surname must NOT be null.
- "IVAN HORVAT 1940–2010. Uspomenu čuvaju supruga Marija i sin Petar": ONE record — Ivan Horvat.
  Marija and Petar are living mourners in the dedication block and get NO record, even though their
  full names appear.
- "IVAN HORVAT pok. PETRA 1930–2000": name "Ivan", surname "Horvat" — "pok. Petra" is a patronymic
  (son of the late Petar), not a surname; drop it.
- "VJEČNI DOM IVANA HORVATA" (a possessive dedication in the genitive): the surname "Horvata" is the
  genitive of "Horvat", which fixes the whole phrase as genitive — so the person is name "Ivan",
  surname "Horvat", not "Ivana"/"Horvata". Do NOT convert a bare first name whose genitive doubles as
  a real nominative name (e.g. "Mare", "Pave") unless an accompanying surname fixes the case; if it
  does not, keep the name as carved.
- "MARIJA HORVAT umrla 1987. u 48. godini", no birth year carved: death_year_status="present" (1987),
  and birth_year_status="inferred" with birth_year 1939 (1987 minus 48). Had the age been missing or
  unclear, do NOT infer — leave the birth year absent/unreadable.
- Two spouses share one grave: "IVAN HORVAT 1936–2001" (crisp) and "MARIJA HORVAT 19?8–2010", where
  the tens digit of Marija's birth year is worn and its faint strokes fit either "3" or "4". Spouses
  are usually born within a few years of each other, so "1938" (2 years from Ivan) is far more likely
  than "1948" (12 years) — read Marija's birth year as 1938 with birth_year_status="inferred". Use
  spouse proximity ONLY to choose between digit readings a partly-legible year already allows; NEVER
  to invent a year that is fully illegible (that stays "unreadable").
- Text is carved in Cyrillic, e.g. "ХОРВАТ" for the surname: transliterate to Croatian Latin script
  as "Horvat" before writing the output field; do not leave any field in Cyrillic characters."""

# The prompt for the models that decline to write out their reasoning (see _writes_reasoning): the
# same text without step 2, and with the two lines that pointed at it reworded. index() raises at
# import if a later edit of the prompt loses either end of step 2; the tests pin the wording changes.
_STEP_2_START = SYSTEM_PROMPT.index('2. "reasoning":')
_STEP_2_END = SYSTEM_PROMPT.index("Then fill the structured fields below")
SYSTEM_PROMPT_LEAN = (
    (SYSTEM_PROMPT[:_STEP_2_START] + SYSTEM_PROMPT[_STEP_2_END:])
    .replace("filling BOTH scratchpad fields", "filling the scratchpad field")
    .replace("from your reasoning.", "from your transcription.")
)


_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_YEAR = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_YEAR_STATUSES = ["present", "inferred", "absent_certain", "unreadable"]

# The JSON the model must answer with (structured output). Structured outputs require every
# object to list all its properties as required and to allow no others; a field that may be
# empty is anyOf ... null.
RESULT_SCHEMA = {
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
        # Internal scratchpad only (not written to the CSV): forces the model to reason through
        # grouping, name normalisation and any inference in words before it fills `records`.
        # Sent only to the models that accept it (see _writes_reasoning); the rest get
        # RESULT_SCHEMA_LEAN, which leaves it out.
        "reasoning": {
            "type": "string",
            "description": "Written after raw_text and before the structured fields, and NOT saved "
                           "to the CSV. Reason briefly, in words, through the hard cases: which "
                           "markers form one grave; reducing each name to the pure first name and "
                           "surname (drop titles and maiden names); converting names to the "
                           "nominative; and any high-confidence inferred year (age arithmetic, "
                           "spouse proximity). Decide each person's fields here before filling "
                           "`records`. A few focused lines, not an essay.",
        },
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": _NULLABLE_STRING,
                    "surname": _NULLABLE_STRING,
                    "birth_year": _NULLABLE_YEAR,
                    "birth_year_status": {
                        "type": "string",
                        "enum": _YEAR_STATUSES,
                        "description": "present = legible; inferred = not fully legible (missing, or a worn digit) but derived with high confidence (e.g. death year minus age, or a worn digit fixed from a spouse's year); absent_certain = no full year (none inscribed, or only an incomplete short year); unreadable = cannot read at all.",
                    },
                    "death_year": _NULLABLE_YEAR,
                    "death_year_status": {
                        "type": "string",
                        "enum": _YEAR_STATUSES,
                        "description": "present = legible; inferred = not fully legible (missing, or a worn digit) but derived with high confidence (e.g. birth year plus age, or a worn digit fixed from a spouse's year); absent_certain = no full year (none inscribed, or only an incomplete short year); unreadable = cannot read at all.",
                    },
                    "note": _NULLABLE_STRING,
                },
                "required": ["name", "surname", "birth_year", "birth_year_status",
                             "death_year", "death_year_status", "note"],
                "additionalProperties": False,
            },
        },
        "error": _NULLABLE_STRING,
        "ambiguous_multiple_markers": {
            "type": "boolean",
            "description": "true if nearby plaques/crosses might belong to this grave but "
                           "could not be included confidently (flagged in the notes); else false.",
        },
    },
    "required": ["raw_text", "reasoning", "records", "error", "ambiguous_multiple_markers"],
    "additionalProperties": False,
}

# The same answer without the "reasoning" scratchpad, for the models that decline it.
RESULT_SCHEMA_LEAN = copy.deepcopy(RESULT_SCHEMA)
del RESULT_SCHEMA_LEAN["properties"]["reasoning"]
RESULT_SCHEMA_LEAN["required"].remove("reasoning")

# Cache the system prompt (stable across every image in a run). Below the model's minimum
# cacheable prefix this silently has no effect and costs nothing extra. Minimums: 1024 tok
# on Sonnet 5, 512 on the other offered models -- this prompt runs ~4k tokens. The full prompt,
# with its written-reasoning step, goes only to the models that accept it (see _writes_reasoning);
# the rest get the lean one. Module-level constants, so every call of a model sends the same bytes.
_SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
_SYSTEM_BLOCKS_LEAN = [{"type": "text", "text": SYSTEM_PROMPT_LEAN, "cache_control": {"type": "ephemeral"}}]

_RETRY_DELAYS = [2, 4, 8]
# Status codes that doom the whole run (bad key, billing, no access, unknown model) rather
# than just this one image -- only these abort the batch outright.
_FATAL_STATUS_CODES = frozenset({401, 402, 403, 404})
_RECORD_FIELDS = ('name', 'surname', 'birth_year', 'death_year')
# Only a missing name/surname forces manual review; birth/death years get a
# certainty status so a *certain* absence can still pass as OK.
_CORE_FIELDS = ('name', 'surname')
_CORE_LABELS_HR = {'name': 'ime', 'surname': 'prezime'}
# Cap on the model's own free text (its note or error sentence) in the Notes cell. The
# system's tags are never cut: they are what a reviewer filters on.
_MAX_MODEL_TEXT = 120
_FOUR_DIGITS = re.compile(r'\d{4}')


@dataclass
class ImageResult:
    status: str
    rows: list[list]
    reason: str | None
    cost: float = 0.0
    fatal_tag: str | None = None   # set when the whole run must stop, e.g. "api-402"
    api_failure: bool = False      # the API call itself failed: nothing billed, resume retries it
    billed: bool = False           # the model answered, whatever the verdict


def failure_row(record_id: str, note: str, extra_tags=(), file_name: str = "") -> list:
    """The one row written for a photo that yielded no records."""
    return [record_id, "", "", "", "", _join_notes([note, *extra_tags]), file_name]


def _join_notes(tags: list[str], model_note: str = "") -> str:
    """System tags first and never cut; the model's own note last, capped."""
    parts = [t for t in tags if t]
    model_note = model_note[:_MAX_MODEL_TEXT]
    if model_note and model_note not in parts:
        parts.append(model_note)
    return "; ".join(parts)


def _clean_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_year(value) -> int | None:
    """A plausible 4-digit year as an int, else None ('19?8', 20, 2999, True...)."""
    if isinstance(value, str) and _FOUR_DIGITS.fullmatch(value.strip()):
        value = int(value.strip())
    if (isinstance(value, int) and not isinstance(value, bool)
            and 1000 <= value <= datetime.date.today().year):
        return value
    return None


def _year_state(raw_value, value: int | None, status) -> str:
    """Resolve a year's situation defensively, never trusting a lone flag.

    A usable year always wins: 'inferred' when the model flagged it as derived, otherwise
    'present'. A value that was given but is not a plausible year is 'unreadable'. A null
    only counts as a certain absence when the model explicitly says so; anything else
    (incl. a 'present' flag with no year) falls back to 'unreadable' so the image is sent
    to byhand, not passed as OK.
    """
    if value is not None:
        return "inferred" if status == "inferred" else "present"
    if raw_value is None and status == "absent_certain":
        return "absent_certain"
    return "unreadable"


def _impossible_pair(rec: dict) -> bool:
    return rec["birth_year"] is not None and rec["death_year"] is not None and rec["birth_year"] > rec["death_year"]


def _record_tags(rec: dict, birth_state: str, death_state: str) -> list[str]:
    """Short Croatian tags for the CSV Notes column (edge cases only)."""
    tags = []
    missing = [_CORE_LABELS_HR[f] for f in _CORE_FIELDS if rec[f] is None]
    if missing:
        tags.append("fali: " + ", ".join(missing))

    # Combine the two "certain absence" cases for brevity.
    if birth_state == "absent_certain" and death_state == "absent_certain":
        tags.append("bez god. rođ. i smrti")
    else:
        if birth_state == "absent_certain":
            tags.append("bez god. rođenja")
        if death_state == "absent_certain":
            tags.append("bez god. smrti")

    if birth_state == "unreadable":
        tags.append("god. rođenja nečitka")
    if death_state == "unreadable":
        tags.append("god. smrti nečitka")

    if birth_state == "inferred":
        tags.append("god. rođ. izvedena")
    if death_state == "inferred":
        tags.append("god. smrti izvedena")
    if _impossible_pair(rec):
        tags.append("provjeri godine")
    return tags


def _record_to_row(record_id: str, rec: dict, note: str, file_name: str) -> list:
    return [
        record_id,
        rec["name"] or "",
        rec["surname"] or "",
        rec["birth_year"] if rec["birth_year"] is not None else "",
        rec["death_year"] if rec["death_year"] is not None else "",
        note,
        file_name,
    ]


class ImageUnreadable(Exception):
    """Pillow could not fully decode the file (truncated, corrupt, not an image, HEIC...)."""


def prepare_image(raw: bytes) -> tuple[bytes, str]:
    """The bytes and media type to send for one photo.

    Decodes the whole image first, so a truncated file fails here instead of at the API.
    The media type comes from the content, not the extension. EXIF rotation is baked into
    the pixels (the API ignores image metadata) and the image is shrunk to what the model
    uses. When none of that changes anything, the original bytes go out untouched.
    """
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        width, height = img.size
        scale = min(1.0, MAX_LONG_EDGE / max(width, height), math.sqrt(MAX_PIXELS / (width * height)))
        if fmt in ("JPEG", "MPO") and scale < 0.5:
            img.draft("RGB", (math.ceil(width * scale), math.ceil(height * scale)))
        img.load()
    except Exception as e:  # Pillow raises many different types for bad input
        raise ImageUnreadable(str(e) or type(e).__name__) from e

    try:
        orientation = img.getexif().get(_EXIF_ORIENTATION, 1)
    except Exception:  # a broken EXIF block says nothing about the pixels, which decoded fine
        orientation = 1

    if (scale == 1.0 and orientation in (None, 1) and img.mode in _SENDABLE_MODES
            and fmt in _MEDIA_TYPES and len(raw) <= MAX_SEND_BYTES):
        return raw, _MEDIA_TYPES[fmt]

    try:
        img = _to_rgb(ImageOps.exif_transpose(img))
        # draft() may already have shrunk the image by a power of two; finish the job.
        factor = scale * max(width, height) / max(img.size)
        if factor < 1.0:
            img = img.resize(
                (max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    except Exception as e:
        raise ImageUnreadable(str(e) or type(e).__name__) from e
    return buf.getvalue(), "image/jpeg"


def _to_rgb(img: Image.Image) -> Image.Image:
    """RGB for JPEG encoding. 16-bit greyscale is scaled to 8 bits instead of clipped white."""
    if img.mode.startswith("I;16"):
        img = img.convert("I")
    if img.mode == "I":
        img = img.point(lambda v: v * (1 / 256)).convert("L")
    return img if img.mode == "RGB" else img.convert("RGB")


def _jittered(delay: float) -> float:
    return delay * (1 + random.uniform(-0.25, 0.25))


def _fatal_tag(e: anthropic.APIStatusError) -> str | None:
    """The tag for an error that dooms the whole run, else None."""
    if e.type == "billing_error":
        return "api-402"
    if e.status_code in _FATAL_STATUS_CODES:
        return f"api-{e.status_code}"
    # A 429 without retry-after is the usage tier's monthly spend cap, which keeps failing
    # until access resumes; a real rate limit says when to come back.
    if e.status_code == 429 and not e.response.headers.get("retry-after"):
        return "spend-cap"
    return None


def _is_retryable(e: anthropic.APIStatusError) -> bool:
    # Below 400: an error event mid-stream after a 200 (e.g. overloaded_error), which the
    # SDK does not retry itself.
    return e.status_code == 429 or e.status_code >= 500 or e.status_code < 400


def _max_tokens(effort: str | None) -> int:
    # Thinking shares this budget, and the top two effort levels can think well past 16k.
    return 64000 if effort in ("xhigh", "max") else 16000


def _writes_reasoning(model: str) -> bool:
    """Whether the request may ask for the written "reasoning" scratchpad.

    Sonnet 5.5, Opus 5.5 and the Fable models decline a prompt that asks the model to write out its
    reasoning (refusal category "reasoning_extraction"), and a fallback model does not retry that
    decline. Sonnet 5, Opus 5 and the 4.x models predate that check and keep the scratchpad; any
    other model gets the lean request, which every model accepts.
    """
    return model in ("claude-sonnet-5", "claude-opus-5") or "-4-" in model


def _request_parts(model: str) -> tuple[list, dict]:
    """(system blocks, answer schema) for this model's request."""
    if _writes_reasoning(model):
        return _SYSTEM_BLOCKS, RESULT_SCHEMA
    return _SYSTEM_BLOCKS_LEAN, RESULT_SCHEMA_LEAN


def _call_api(client, model: str, mime: str, b64: str, effort: str | None = None):
    # temperature is only accepted on the Sonnet 4.x family. Sonnet 5, Opus 4.7/4.8,
    # Opus 5 and Fable 5 reject it with 400 "temperature is deprecated for this model".
    params: dict = {"temperature": 0} if "sonnet-4" in model else {}
    system, schema = _request_parts(model)
    # Structured output, not a forced tool call: Sonnet 5.5, Opus 5.5 and Fable 5.1 reject
    # tool_choice "tool" with a 400. The answer arrives as one JSON text block.
    output_config: dict = {"format": {"type": "json_schema", "schema": schema}}
    if effort:
        output_config["effort"] = effort
    # Streamed, so the larger max_tokens ceilings stay clear of the SDK's timeout guard for
    # non-streaming requests.
    with client.messages.stream(
        model=model,
        max_tokens=_max_tokens(effort),
        system=system,
        output_config=output_config,
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
    ) as stream:
        return stream.get_final_message()


def _parse_answer(response) -> dict | None:
    """The answer JSON as a dict with a list of record dicts, or None if it isn't one."""
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    records = data.get("records")
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        return None
    return data


def process_image(client, model: str, path: Path, record_id: str,
                  effort: str | None = None, extra_tags: tuple[str, ...] = ()) -> ImageResult:
    file_name = path.name
    try:
        image_bytes, mime = prepare_image(path.read_bytes())
    except (OSError, ImageUnreadable) as e:
        return ImageResult(
            status='total_failure',
            rows=[failure_row(record_id, "ne mogu otvoriti", extra_tags, file_name)],
            reason=f"File could not be read or decoded: {e}",
        )

    b64 = base64.b64encode(image_bytes).decode("ascii")

    last_error: Exception | None = None
    response = None
    for attempt in range(4):
        try:
            response = _call_api(client, model, mime, b64, effort)
            break
        except anthropic.APIStatusError as e:
            fatal_tag = _fatal_tag(e)
            if fatal_tag or not _is_retryable(e):
                return ImageResult(
                    status='total_failure',
                    rows=[failure_row(record_id, "greška API-ja", extra_tags, file_name)],
                    reason=str(e),
                    fatal_tag=fatal_tag,
                    api_failure=True,
                )
            last_error = e
        # Dropped connections and timeouts, including a stream cut off mid-read, which
        # surfaces as a raw httpx error. (The SDK has already retried with backoff.)
        except (anthropic.APIConnectionError, httpx.TransportError) as e:
            last_error = e

        if attempt < 3:
            time.sleep(_jittered(_RETRY_DELAYS[attempt]))

    if response is None:
        return ImageResult(
            status='total_failure',
            rows=[failure_row(record_id, "greška API-ja", extra_tags, file_name)],
            reason=f"API call failed after retries: {last_error}",
            api_failure=True,
        )

    # A response was received, so this call is billed regardless of how well the
    # model extracted the data -- attach the real cost to every return from here on.
    cost = compute_cost(model, response.usage)

    result = _classify(response, cost, record_id, extra_tags, file_name)
    result.billed = True
    return result


def _classify(response, cost: float, record_id: str, extra_tags, file_name: str) -> ImageResult:
    """Turn one answered (and billed) API call into CSV rows and a verdict."""
    def failure(note: str, reason: str) -> ImageResult:
        return ImageResult(
            status='total_failure',
            rows=[failure_row(record_id, note, extra_tags, file_name)],
            reason=reason,
            cost=cost,
        )

    if response.stop_reason == "refusal":
        return failure("odbijeno", "Model declined to answer (refusal)")
    # A turn cut off at max_tokens leaves half-written JSON, which would parse into silently
    # missing people if it parsed at all. Bail before looking at it.
    if response.stop_reason == "max_tokens":
        return failure("odgovor prekinut", "Response hit the max_tokens ceiling before the answer finished")
    data = _parse_answer(response)
    if data is None:
        return failure("neispravan odgovor",
                       f"Model returned no valid JSON answer (stop_reason: {response.stop_reason})")

    records = data["records"]
    error = _clean_text(data.get("error"))
    if not records:
        return failure(error[:_MAX_MODEL_TEXT] or "nema podataka", error or "Model returned no records")

    # Blank names count as missing, and a year only counts as a plausible 4-digit year: the
    # schema cannot stop "", "19?8" or 20.
    cleaned = [
        {
            "name": _clean_text(r.get("name")) or None,
            "surname": _clean_text(r.get("surname")) or None,
            "birth_year": _clean_year(r.get("birth_year")),
            "death_year": _clean_year(r.get("death_year")),
        }
        for r in records
    ]
    if all(all(rec[f] is None for f in _RECORD_FIELDS) for rec in cleaned):
        return failure("sve nečitko", "All fields illegible")

    # Tags every row of this photo shares: the model left out nearby markers it wasn't sure
    # belong to this grave, or complained about the photo while still reading records.
    ambiguous = bool(data.get("ambiguous_multiple_markers"))
    shared = (["provjeri: možda više oznaka"] if ambiguous else []) \
        + ([error[:_MAX_MODEL_TEXT]] if error else []) + list(extra_tags)

    rows, states = [], []
    for raw, rec in zip(records, cleaned):
        birth = _year_state(raw.get("birth_year"), rec["birth_year"], raw.get("birth_year_status"))
        death = _year_state(raw.get("death_year"), rec["death_year"], raw.get("death_year_status"))
        states += [birth, death]
        note = _join_notes(_record_tags(rec, birth, death) + shared, _clean_text(raw.get("note")))
        rows.append(_record_to_row(record_id, rec, note, file_name))

    # Priority: a missing name/surname, an unreadable year, an impossible pair of years, the
    # model's own complaint, and last an ambiguous multi-marker grave -- the model saying it
    # may have missed people is exactly the case a human should look at. A *certain*
    # absence of a year needs no review and still passes as OK.
    if any(rec[f] is None for rec in cleaned for f in _CORE_FIELDS):
        reason = "Name or surname could not be read"
    elif "unreadable" in states:
        reason = "Model not certain whether a year of birth or death exists"
    elif any(_impossible_pair(rec) for rec in cleaned):
        reason = "Birth year is after death year"
    elif error:
        reason = f"Model reported a problem: {error}"
    elif ambiguous:
        reason = "Nearby markers may belong to this grave and were left out"
    else:
        return ImageResult(status='full_success', rows=rows, reason=None, cost=cost)
    return ImageResult(status='partial_success', rows=rows, reason=reason, cost=cost)
