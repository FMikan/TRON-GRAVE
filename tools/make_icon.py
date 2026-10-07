"""Draw the app icon, an arched headstone in the app's accent blue. Run from the repo root:

    python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

STONE, BASE, LINES = (59, 111, 216, 255), (42, 87, 181, 255), (255, 255, 255, 235)
SIZES = (16, 24, 32, 48, 64, 128, 256)
MASTER = 1024


def master(lines: bool) -> Image.Image:
    """The icon at 1024 px; small sizes are scaled down from it, so edges stay smooth."""
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = MASTER / 256
    left, right, top, bottom = 56 * s, 200 * s, 20 * s, 214 * s
    d.ellipse((left, top, right, top + (right - left)), fill=STONE)              # the round top
    d.rectangle((left, top + (right - left) / 2, right, bottom), fill=STONE)
    d.rounded_rectangle((32 * s, 206 * s, 224 * s, 240 * s), radius=6 * s, fill=BASE)
    if lines:                       # the inscription blurs into noise below 32 px
        for i, (x0, x1) in enumerate(((92, 164), (84, 172), (100, 156))):
            y = (104 + i * 30) * s
            d.rounded_rectangle((x0 * s, y, x1 * s, y + 10 * s), radius=5 * s, fill=LINES)
    return img


def icon(size: int) -> Image.Image:
    return master(lines=size >= 32).resize((size, size), Image.LANCZOS)


def main() -> None:
    out = Path("assets")
    out.mkdir(exist_ok=True)
    icon(256).save(out / "tron-grave.png")
    icon(256).save(out / "tron-grave.ico", sizes=[(n, n) for n in SIZES],
                   append_images=[icon(n) for n in SIZES[:-1]])


if __name__ == "__main__":
    main()
