"""Inputs for docs/W11-SHOTS/capture.mjs (DAT-850): a drawn skyline JPEG whose EXIF names a Canon EOS R5,
so a caption that says iPhone 15 Pro gives the caption-context concern, and the M2 Gatlinburg screenshot.

    python docs/W11-SHOTS/make_inputs.py /tmp/second-look-w11-cap
"""
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
img = Image.new("RGB", (640, 480))
d = ImageDraw.Draw(img)
for y in range(480):  # a dusk sky, light at the bottom
    c = int(110 + 90 * y / 480)
    d.line([(0, y), (640, y)], fill=(c, c - 12, c - 30))
for i, (x, h) in enumerate([(10, 180), (90, 250), (170, 300), (250, 360), (330, 200), (410, 240), (490, 170), (560, 210)]):
    d.rectangle([x, 480 - h, x + 66, 480], fill=(60 + 12 * (i % 3), 52, 58))
    for wy in range(480 - h + 14, 470, 26):
        for wx in range(x + 8, x + 60, 18):
            d.rectangle([wx, wy, wx + 8, wy + 12], fill=(232, 214, 160))
ex = Image.Exif()
ex[0x010F] = "Canon"
ex[0x0110] = "Canon EOS R5"
img.save(out / "street-canon.jpg", "JPEG", quality=90, exif=ex)
shutil.copy(Path(__file__).resolve().parents[1] / "M2-SHOTS" / "shot-3-gatlinburg.png", out / "shot-3-gatlinburg.png")
print("wrote", sorted(p.name for p in out.iterdir() if p.is_file()))
