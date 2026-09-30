"""Shared test helpers: image factories (and, from Task 5, a fake Anthropic client)."""

import io
import json
from types import SimpleNamespace

import anthropic
import httpx
from PIL import Image


def jpeg_bytes(w=400, h=300, orientation=None, quality=90) -> bytes:
    """A grey JPEG with a black block top-left (so rotation is visible)."""
    img = Image.new("RGB", (w, h), (200, 200, 200))
    img.paste((0, 0, 0), (0, 0, max(1, w // 2), max(1, h // 4)))
    buf = io.BytesIO()
    kwargs = {}
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        kwargs["exif"] = exif
    img.save(buf, "JPEG", quality=quality, **kwargs)
    return buf.getvalue()


def png16_bytes(w=64, h=64, value=30000) -> bytes:
    buf = io.BytesIO()
    Image.new("I;16", (w, h), value).save(buf, "PNG")
    return buf.getvalue()


def webp_bytes(w=50, h=40) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 20, 30)).save(buf, "WEBP")
    return buf.getvalue()


def decode(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


# ---- API responses -------------------------------------------------------------------

def usage(input_tokens=1000, output_tokens=500, cache_creation=0, cache_read=0):
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_creation_input_tokens=cache_creation,
                           cache_read_input_tokens=cache_read)


def record(name="Ivan", surname="Horvat", birth=1920, death=1999,
           birth_status="present", death_status="present", note=None) -> dict:
    return {"name": name, "surname": surname, "birth_year": birth, "birth_year_status": birth_status,
            "death_year": death, "death_year_status": death_status, "note": note}


def answer(*records, error=None, ambiguous=False) -> dict:
    return {"raw_text": "…", "reasoning": "…", "records": list(records), "error": error,
            "ambiguous_multiple_markers": ambiguous}


def message(answer_=None, stop_reason="end_turn", text=None, usage_=None):
    """A fake Message: an (empty) thinking block, then the answer as one JSON text block."""
    if text is None:
        text = json.dumps(answer_)
    content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage_ or usage())


def api_error(status, body=None, headers=None):
    """The exact anthropic exception the SDK raises for an HTTP `status` response."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, headers=headers or {})
    if body is None:
        body = {"type": "error", "error": {"type": "api_error", "message": "boom"}}
    return anthropic.Anthropic(api_key="test")._make_status_error(
        f"Error code: {status} - {body}", body=body, response=response)


class MidStream:
    """An outcome that fails while the stream is read, after a 200 response."""

    def __init__(self, error):
        self.error = error


class _Stream:
    def __init__(self, outcome):
        self._outcome = outcome

    def __enter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        if isinstance(self._outcome, MidStream):
            raise self._outcome.error
        return self._outcome


class FakeClient:
    """Stands in for anthropic.Anthropic. Each messages.stream() call records its kwargs and
    consumes the next outcome: a message, an exception to raise, or a MidStream failure."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("unexpected extra API call")
        return _Stream(self.outcomes.pop(0))
