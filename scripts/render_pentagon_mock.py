"""Render the mock post for the recorded Pentagon case (DAT-850): a grey circle, the header "Mock post made by us",
then the caption from the M1 test (the first caption in scripts/glimmer_smoke.py), which is our own wording. It
carries no PolitiFact text (licence check A190 and the project lead's ruling, DAT-850). Pillow's built-in font is Aileron
Regular (CC0), as in scripts/investigate_smoke.py. Same layout as the hero case's mock post.

    .venv/bin/python scripts/render_pentagon_mock.py samples/recorded/pentagon-mock-post.png
"""
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

TEXT = ("BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. Pentagon is under attack. "
        "Stay safe everyone")


def main(out: str) -> None:
    body, small = ImageFont.load_default(size=52), ImageFont.load_default(size=34)
    lines = textwrap.wrap(TEXT, 34)
    img = Image.new("RGB", (1080, 260 + 70 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((60, 50, 140, 130), fill=(190, 200, 215))
    d.text((165, 62), "Mock post made by us", font=small, fill=(90, 90, 90))
    for i, line in enumerate(lines):
        d.text((60, 170 + 70 * i), line, font=body, fill=(15, 15, 15))
    img.save(out, optimize=True)


if __name__ == "__main__":
    main(sys.argv[1])
