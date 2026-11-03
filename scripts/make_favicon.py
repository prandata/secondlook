"""Make the page's favicon (DAT-850 A249): the WSAI desk brand kit's logo recipe (design/brand/brand.py, logo.png:
a cream Playfair Display Bold capital on the brand burnt orange) with an "S" for Second Look. Colours are the kit's
brand.json `accent` #B4532F and `bg` #F4F0E6; the font is the vendored copy (SIL OFL 1.1, static/fonts/OFL.txt).
Drawn once at 256 px and scaled down, so the small sizes stay smooth.

    .venv/bin/python scripts/make_favicon.py secondlook/web/static/favicon.ico
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "secondlook/web/static/fonts/PlayfairDisplay.ttf"
ACCENT, CREAM = (0xB4, 0x53, 0x2F), (0xF4, 0xF0, 0xE6)
SIZES = [(16, 16), (32, 32), (48, 48), (64, 64)]


def mark(size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size), ACCENT)
    font = ImageFont.truetype(str(FONT), int(size * 0.96))
    font.set_variation_by_name("Bold")  # the logo's weight 700
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw.textbbox((0, 0), "S", font=font)
    # Centre the glyph's own box (its ink), not the line box, as the logo looks centred.
    draw.text(((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top), "S", font=font, fill=CREAM)
    return img


def main(out: str) -> None:
    mark().save(out, format="ICO", sizes=SIZES)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "secondlook/web/static/favicon.ico"))
