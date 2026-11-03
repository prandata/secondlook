"""Deterministic dev/test split with a duplicate and near-duplicate guard.

    python -m gate.split --manifest manifest.csv --seed 30 --out splits \\
        [--dev-real 100] [--dev-ai 100] [--phash-distance 8] [--hash-files]

Manifest CSV columns: item_id, label (real|ai); optional: path, group, phash, sha256.

Items are joined into one cluster when they share a `group` (a burst, a
session, a source page), the same file sha256, or perceptual hashes within
--phash-distance bits. A cluster goes wholly to dev or wholly to test, and
only one item per cluster and label is kept, so the test set holds
independent images. The rest go to excluded.csv with the reason.

The order is sha256(seed:cluster key), so the same manifest and seed always
give byte-identical files, on any Python version.

Perceptual hashes are not computed here: decoding JPEG, PNG or WebP needs an
image library (Pillow), which the standard library lacks. Compute the `phash`
column in the data-prep step that already opens the images (the step that
makes the q70 / 1,080 px copies) and pass it in. --hash-files adds exact
duplicate detection by file sha256 with the standard library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys

from gate import MIN_DEV_AI, MIN_DEV_REAL

SPLIT_SCHEMA = "second-look-split/1"
LABELS = ("real", "ai")
OUT_COLUMNS = ("item_id", "label", "path", "group", "phash", "sha256", "cluster")


class SplitError(Exception):
    pass


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: str, hash_files: bool = False) -> list[dict]:
    base = os.path.dirname(os.path.abspath(path))
    items, seen, problems = [], set(), []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for col in ("item_id", "label"):
            if col not in (reader.fieldnames or []):
                raise SplitError(f"manifest needs a {col} column")
        for line, raw in enumerate(reader, start=2):
            it = {c: (raw.get(c) or "").strip() for c in OUT_COLUMNS if c != "cluster"}
            it["label"] = it["label"].lower()
            if not it["item_id"]:
                problems.append(f"line {line}: empty item_id")
            elif it["item_id"] in seen:
                problems.append(f"line {line}: item_id {it['item_id']} appears twice")
            elif it["label"] not in LABELS:
                problems.append(f"line {line}: label must be real or ai")
            else:
                if it["phash"]:
                    try:
                        it["_phash"] = int(it["phash"], 16)
                    except ValueError:
                        problems.append(f"line {line}: phash {it['phash']!r} is not hex")
                        continue
                if hash_files and it["path"] and not it["sha256"]:
                    it["sha256"] = _sha256_file(os.path.join(base, it["path"]))
                seen.add(it["item_id"])
                items.append(it)
    if problems:
        raise SplitError("bad manifest rows:\n  " + "\n  ".join(problems[:20]))
    lengths = {len(it["phash"]) for it in items if it["phash"]}
    if len(lengths) > 1:
        raise SplitError(f"phash values have different lengths {sorted(lengths)}; "
                         "use one hash function for every image")
    return items


def cluster(items: list[dict], phash_distance: int) -> dict[int, list[int]]:
    """Map each cluster root to its member indices."""
    uf = _UnionFind(len(items))
    for key in ("group", "sha256"):
        first = {}
        for i, it in enumerate(items):
            v = it[key]
            if v:
                if v in first:
                    uf.union(first[v], i)
                else:
                    first[v] = i
    hashed = [i for i, it in enumerate(items) if "_phash" in it]
    # O(n^2) over hashed items: about 0.5 M comparisons for 1,000 images.
    for a_pos, a in enumerate(hashed):
        ha = items[a]["_phash"]
        for b in hashed[a_pos + 1:]:
            if (ha ^ items[b]["_phash"]).bit_count() <= phash_distance:
                uf.union(a, b)
    clusters: dict[int, list[int]] = {}
    for i in range(len(items)):
        clusters.setdefault(uf.find(i), []).append(i)
    return clusters


def _rank(seed: int, key: str) -> str:
    return hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()


def check_distance(items: list[dict], phash_distance: int) -> None:
    """A negative distance turns the near-duplicate check off; one at or above the
    hash width joins every hashed image. Both are refused."""
    if phash_distance < 0:
        raise SplitError(f"--phash-distance must be 0 or more, got {phash_distance}")
    widths = {4 * len(it["phash"]) for it in items if it["phash"]}
    if widths and phash_distance >= min(widths):
        raise SplitError(f"--phash-distance {phash_distance} is not below the "
                         f"{min(widths)}-bit hash width, so every image would match")


def coverage_warnings(items: list[dict]) -> list[str]:
    n = len(items)
    hashed = sum(1 for it in items if it["phash"])
    filed = sum(1 for it in items if it["sha256"])
    out = []
    if hashed == 0:
        out.append("no phash given: near-duplicates are not guarded")
    elif hashed < n:
        out.append(f"phash given for {hashed} of {n} items: the other {n - hashed} "
                   "are not checked for near-duplicates")
    if filed < n:
        out.append(f"file sha256 known for {filed} of {n} items: exact duplicates are "
                   "guarded only for those (use --hash-files or a sha256 column)")
    return out


def split(items: list[dict], seed: int, dev_real: int, dev_ai: int,
          phash_distance: int) -> tuple[list[dict], list[dict], list[dict]]:
    check_distance(items, phash_distance)
    clusters = cluster(items, phash_distance)
    keyed = []
    for members in clusters.values():
        key = min(items[i]["item_id"] for i in members)
        keyed.append((_rank(seed, key), key, members))
    keyed.sort()

    dev, test, excluded = [], [], []
    need = {"real": dev_real, "ai": dev_ai}
    for _, key, members in keyed:
        # One representative per label: the member whose own rank is lowest.
        reps = {}
        for i in sorted(members, key=lambda i: _rank(seed, items[i]["item_id"])):
            it = dict(items[i], cluster=key)
            if it["label"] not in reps:
                reps[it["label"]] = it
            else:
                it["reason"] = (f"same cluster as {reps[it['label']]['item_id']} "
                                "(shared group, file hash or near-duplicate phash)")
                excluded.append(it)
        to_dev = any(need[label] > 0 for label in reps)
        for label, it in reps.items():
            (dev if to_dev else test).append(it)
            if to_dev:
                need[label] -= 1
    for side in (dev, test, excluded):
        side.sort(key=lambda it: it["item_id"])
    dev_ids, test_ids = {it["item_id"] for it in dev}, {it["item_id"] for it in test}
    if dev_ids & test_ids:  # cannot happen by construction; checked anyway
        raise SplitError(f"items in both sets: {sorted(dev_ids & test_ids)[:5]}")
    return dev, test, excluded


def _write_csv(path: str, rows: list[dict], extra=()) -> str:
    cols = list(OUT_COLUMNS) + list(extra)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return _sha256_file(path)


def _counts(rows):
    return {label: sum(r["label"] == label for r in rows) for label in LABELS}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m gate.split",
                                description="Deterministic dev/test split (offline).")
    p.add_argument("--manifest", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--dev-real", type=int, default=MIN_DEV_REAL)
    p.add_argument("--dev-ai", type=int, default=MIN_DEV_AI)
    p.add_argument("--phash-distance", type=int, default=8,
                   help="max Hamming distance for near-duplicates (default 8 bits)")
    p.add_argument("--hash-files", action="store_true",
                   help="read each `path` and add its sha256 for exact-duplicate detection")
    a = p.parse_args(argv)
    try:
        items = load_manifest(a.manifest, a.hash_files)
        dev, test, excluded = split(items, a.seed, a.dev_real, a.dev_ai, a.phash_distance)
    except (SplitError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    os.makedirs(a.out, exist_ok=True)
    report = {
        "schema": SPLIT_SCHEMA,
        "seed": a.seed,
        "manifest": {"path": os.path.basename(a.manifest), "sha256": _sha256_file(a.manifest)},
        "params": {"dev_real": a.dev_real, "dev_ai": a.dev_ai,
                   "phash_distance": a.phash_distance, "hash_files": a.hash_files},
        "dev": {"sha256": _write_csv(os.path.join(a.out, "dev.csv"), dev), **_counts(dev)},
        "test": {"sha256": _write_csv(os.path.join(a.out, "test.csv"), test), **_counts(test)},
        "excluded": {"sha256": _write_csv(os.path.join(a.out, "excluded.csv"), excluded,
                                          extra=("reason",)), **_counts(excluded)},
        "phash_checked": sum(1 for it in items if it["phash"]),
    }
    short = [f"dev {label} {report['dev'][label]} < {want}"
             for label, want in (("real", a.dev_real), ("ai", a.dev_ai))
             if report["dev"][label] < want]
    report["warnings"] = short + coverage_warnings(items)
    with open(os.path.join(a.out, "SPLIT.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({k: report[k] for k in ("dev", "test", "excluded", "warnings")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
