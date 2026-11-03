"""Reproduce the training-overlap counts in docs/TRAINING-DATA.md (DAT-848). Run by hand, never in tests.

Downloads (about 323 MB, public, no login) from the Community Forensics dataset at the pinned revision:
    REV=f0b06f79d20389706bb76c626391fa870e0bf604
    BASE=https://huggingface.co/datasets/OwensLab/CommunityForensics/resolve/$REV/data
    curl -sL -o laion.csv $BASE/Real/laion_commfor_train_subset_2M.csv
    curl -sL -o systematic.csv $BASE/metadata/systematic_set_metadata.csv

    python scripts/training_overlap.py laion.csv systematic.csv <L4 folder>

Prints JSON: the Stable Diffusion 1.x checkpoints among the training generators, the Commons file
URLs in the LAION list, how many of them are files in our real manifest (by Commons file name, so
a thumbnail of one of our files counts), and any DiffusionDB link or AI image name in the list.
"""
import csv
import json
import re
import sys
import urllib.parse
from pathlib import Path

COMMONS = re.compile(r"upload\.wikimedia\.org/wikipedia/commons/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/([^/]+)")
SD1 = re.compile(r"^(CompVis/stable-diffusion-v1-\d|runwayml/stable-diffusion-v1-5)$")


def commons_name(url: str) -> str | None:
    m = COMMONS.search(urllib.parse.unquote(url.split("?")[0]))
    return m.group(1).replace(" ", "_") if m else None


def main(laion: str, systematic: str, l4: str) -> dict:
    csv.field_size_limit(sys.maxsize)
    gens = [r["Model"] for r in csv.DictReader(open(systematic, newline=""))]
    real = {commons_name(r["original_url"]) for r in csv.DictReader(open(Path(l4) / "manifest-real.csv", newline=""))}
    ai = {r["image_name"].lower() for r in csv.DictReader(open(Path(l4) / "manifest-ai.csv", newline=""))}
    rows = commons = 0
    hits, ddb, ai_hits = [], [], []
    for r in csv.DictReader(open(laion, newline="")):
        rows += 1
        url = r["url"]
        name = commons_name(url)
        if name:
            commons += 1
            if name in real:
                hits.append(url)
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if "diffusiondb" in url.lower() or host.endswith("poloclub.github.io") or "/datasets/poloclub/" in url:
            ddb.append(url)
        if url.rsplit("/", 1)[-1].lower() in ai:
            ai_hits.append(url)
    return {"generators": len(gens), "sd1_checkpoints": sorted(g for g in gens if SD1.match(g)),
            "laion_rows": rows, "laion_commons_urls": commons, "manifest_real_files": len(real - {None}),
            "manifest_real_in_laion": hits, "diffusiondb_links": ddb, "ai_names_in_laion": ai_hits}


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:4]), indent=1))
