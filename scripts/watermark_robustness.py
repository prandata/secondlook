"""Measure the watermark rung on real photos: unmarked copies, and marked copies after JPEG and resizing.
Usage: python scripts/watermark_robustness.py <image> [<image> ...]  (prints one JSON line per case)."""
import io
import json
import sys
from pathlib import Path

from PIL import Image

from secondlook.rungs import watermark as wm


def jpeg(img, q):
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=q)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def fit(img, side):
    out = img.copy(); out.thumbnail((side, side), Image.BICUBIC)
    return out


def crop(img, frac):
    w, h = img.size; cw, ch = int(w * frac), int(h * frac)
    return img.crop(((w - cw) // 2, (h - ch) // 2, (w - cw) // 2 + cw, (h - ch) // 2 + ch))


CASES = {
    "as saved (PNG)": lambda m: m,
    "JPEG q90": lambda m: jpeg(m, 90),
    "JPEG q70": lambda m: jpeg(m, 70),
    "JPEG q50": lambda m: jpeg(m, 50),
    "resize long side 1080": lambda m: fit(m, 1080),
    "resize 1080 then JPEG q70": lambda m: jpeg(fit(m, 1080), 70),
    "resize long side 512": lambda m: fit(m, 512),
    "resize long side 256": lambda m: fit(m, 256),
    "centre crop 50%": lambda m: crop(m, 0.5),
    "rotate 90": lambda m: m.rotate(90, expand=True),
}


def main(paths):
    for p in paths:
        img = wm._open_rgb(p)
        name = Path(p).name
        for label, f in (("unmarked original", lambda i: i), ("unmarked JPEG q70", lambda i: jpeg(i, 70)),
                         ("unmarked resize 1080", lambda i: fit(i, 1080))):
            print(json.dumps({"file": name, "marked": False, "case": label, "matched": wm.matches(f(img))}))
        marked = wm.embed(img)
        buf = io.BytesIO(); marked.save(buf, "PNG"); marked = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
        for label, f in CASES.items():
            print(json.dumps({"file": name, "marked": True, "case": label, "matched": wm.matches(f(marked))}))
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1:])
