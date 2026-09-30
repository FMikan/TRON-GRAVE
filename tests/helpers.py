"""Shared test helpers: image factories (and, from Task 5, a fake Anthropic client)."""

import io

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
