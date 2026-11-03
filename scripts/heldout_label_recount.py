"""Recount the exif rung's generator labels on a folder of eval images (DAT-848).

    .venv/bin/python scripts/heldout_label_recount.py <images-dir>

Prints, per sub-folder (one generator each), how many files give an AI-label
card and which (field, generator) pairs. Reads files only; prints no field text.
"""

import collections
import os
import sys

from secondlook.rungs import exif


def main(root: str) -> None:
    per = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    for d, _, files in os.walk(root):
        for f in files:
            row = per[os.path.relpath(d, root)]
            row[0] += 1
            cards = [e for e in exif.check(os.path.join(d, f)) if e.raw.get("ai_label")]
            row[1] += bool(cards)
            row[2].update((e.raw["field"], e.raw["generator"]) for e in cards)
    total = hits = 0
    for name, (n, h, pairs) in sorted(per.items()):
        total, hits = total + n, hits + h
        if h:
            print(f"{name}: {h}/{n} {dict(pairs)}")
    print(f"total {hits}/{total} files with a label card")


if __name__ == "__main__":
    main(sys.argv[1])
