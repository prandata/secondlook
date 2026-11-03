"""Write the gate's split files from L4's accepted file or files (DAT-852). Offline, free, no image decoded.

    python -m secondlook.gate_splits --l4-dir <L4 folder> --accepted <file> [--accepted <file>] --out <new folder>
        [--v2-lock <L4's v2 LOCK.json>]

L4 fixes dev and test itself, by unit, in the frozen manifests; the accepted file says which units survived screening.
This step takes both as given: **no re-split**, no seed, no re-draw, no re-clustering. A unit's split is the split of the
accepted list it is in. It writes, in the formats `calibrate.py` and the L2 harness check:

    dev.csv, test.csv   item_id, label, path, group, phash, sha256     one row per unit, sorted by item_id;
                                                                       item_id = group = the L4 unit id
    SPLIT.json          schema second-look-split/1, params.phash_distance (from SCREEN-CONFIG.json), the sha256 and the
                        real/AI counts of dev.csv and test.csv, and the sha256 of every file it was made from

A unit's `file` is read relative to `--file-base` (default the L4 folder given) and must resolve inside it: `..`, an absolute
path outside it and a symlink that leads outside it are refused (before anything is written). SPLIT.json records the resolved
base as `file_base`, and gate_run carries it into gate-run.json and the RESULTS run table. `path` is relative to `--original-root` (default `<l4>/images/original`) when the original is under it, else absolute.
`phash` is L4's own pHash of the unit's image, written only when every unit has one (the harness refuses mixed lengths);
the harness then also checks dev against test by it. The files carry no clock and no seed: the same inputs give the same
bytes.

v2 (DAT-852): the v2 accepted files carry the sha256 of L4's v2 LOCK.json, not of the gate folder's own LOCK.json (which
hashes the rebuilt manifest-real.csv and groups-real.csv and names the v2 LOCK in `v2_lock_sha256`). `--v2-lock PATH` gives
the step that v2 LOCK; it is accepted only when calibrate.verify_v2_lock proves it (its sha256 is the folder's
`v2_lock_sha256`, its gate_view equals the folder's two real-class files, its context files match, the two locks agree on
every file both hash), and then accepted files must name it. SPLIT.json records it under l4.v2_lock_sha256.

Refuses (exit 2, nothing written) when: the L4 lock does not hold or does not name the files the gate needs; an accepted
file was not made under this lock, protocol and screening; dev or test has no accepted units; a unit is in both splits or
in a split twice; an original is missing; `--out` already holds files or is inside the L4 folder (a screening may be running
there) unless `--allow-inside-l4`. `--hash-originals` also reads each original and checks its sha256 (the gate does that
itself in a real run).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from . import calibrate, l4_accepted
from .calibrate import Refused
from .gate_run import GATE_L4_LOCKED
from .l4_accepted import SPLITS, sha256_file

SPLIT_SCHEMA = "second-look-split/1"                 # as gate.split writes
COLUMNS = ("item_id", "label", "path", "group", "phash", "sha256")


def verify_lock(l4: Path, v2_lock=None) -> tuple[dict, dict]:
    """The locked files of the L4 folder, each still equal to its LOCK.json entry. Returns (the entries, hashes).
    With `v2_lock`, the v2 LOCK is verified as well and `hashes` gains `v2_lock_sha256` and `v2_lock`."""
    try:
        lock = json.loads((l4 / "LOCK.json").read_text(encoding="utf-8"))
        locked = lock["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f"no usable L4 LOCK.json at {l4 / 'LOCK.json'} ({exc}): the L4 set is not frozen") from None
    if not isinstance(locked, dict):
        raise Refused("L4 LOCK.json has no sha256 table")
    absent = [n for n in GATE_L4_LOCKED if n not in locked]
    if absent:
        raise Refused(f"L4 LOCK.json does not lock {absent}")
    for name, want in locked.items():
        f = l4 / name
        if not f.is_file() or sha256_file(f) != want:
            raise Refused(f"L4 {name} no longer matches LOCK.json: the L4 set changed after the lock")
    hashes = {"lock_sha256": sha256_file(l4 / "LOCK.json"), "screen_config_sha256": sha256_file(l4 / "SCREEN-CONFIG.json"),
              "gate_v2_named": lock.get("v2_lock_sha256") if isinstance(lock, dict) else None}
    if v2_lock:
        v2 = calibrate.verify_v2_lock(l4, v2_lock)
        hashes.update({"v2_lock_sha256": v2["sha256"], "v2_lock": v2})
    return locked, hashes


def build(l4_dir, accepted_paths, out_dir, original_root=None, allow_inside_l4=False, hash_originals=False,
          v2_lock=None, file_base=None) -> dict:
    """Writes dev.csv, test.csv and SPLIT.json into `out_dir`. Returns the SPLIT.json content."""
    l4 = Path(l4_dir).resolve()
    out = Path(out_dir).resolve()
    root = Path(original_root).resolve() if original_root else l4 / "images" / "original"
    base = Path(file_base).resolve() if file_base else l4          # what a unit's relative `file` is relative to
    if (out == l4 or l4 in out.parents) and not allow_inside_l4:
        raise Refused(f"--out {out} is inside the L4 folder, where a screening may be running; name another folder "
                      "(or pass --allow-inside-l4 once nothing is running there)")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused(f"{out} already holds files: write the split files to a new folder")

    locked, hashes = verify_lock(l4, v2_lock)
    cfg = l4_accepted.load_json(l4 / "SCREEN-CONFIG.json", "SCREEN-CONFIG.json")
    distance = l4_accepted.screen_phash_distance(cfg)
    if isinstance(distance, bool) or not isinstance(distance, int):
        raise Refused(f"SCREEN-CONFIG.json names no whole-number pHash distance (phash_distance, or perceptual_hash.same_unit_if_distance_at_most): {distance!r}")
    acc = l4_accepted.load([Path(p) for p in accepted_paths], l4)
    want = {"protocol_sha256": locked["PROTOCOL.md"], "lock_sha256": hashes.get("v2_lock_sha256", hashes["lock_sha256"]),
            "screen_config_sha256": hashes["screen_config_sha256"]}
    for f in acc["files"]:
        for key, value in want.items():
            if f.get(key) != value:
                hint = ""
                if key == "lock_sha256" and not v2_lock and f.get(key) == hashes["gate_v2_named"]:
                    hint = " (it names the v2 LOCK the folder's LOCK.json lists: pass --v2-lock PATH)"
                raise Refused(f"{f['name']} {key} is {f.get(key)!r}, but the L4 folder's lock gives {value}: the accepted "
                              f"set was not made under this protocol, lock and screening{hint}")
    if acc["schema"] != l4_accepted.L4_SCHEMA:
        raise Refused(f"the files are {acc['schema']}: they carry no original's path, so this step cannot write split "
                      f"files from them (L4's {l4_accepted.L4_SCHEMA} does)")

    seen: dict = {}
    rows = {}
    for split in SPLITS:
        units = acc["units"].get(split)
        if not units:
            raise Refused(f"the accepted files hold no accepted units for {split}: both dev and test are needed")
        rows[split] = []
        for u in units:
            if u["unit_id"] in seen:
                raise Refused(f"unit {u['unit_id']} is accepted in {seen[u['unit_id']]} and in {split}")
            seen[u["unit_id"]] = split
            if not u.get("file"):
                raise Refused(f"unit {u['unit_id']} has no `file`: the path of its original is not known")
            p = (base / u["file"]).resolve()                 # an absolute `file` replaces base; symlinks are followed
            try:
                p.relative_to(base)
            except ValueError:
                raise Refused(f"unit {u['unit_id']}: its `file` {u['file']!r} resolves to {p}, outside --file-base {base}: "
                              "an accepted file may not point at an original outside the base") from None
            if not p.is_file():
                raise Refused(f"the original of unit {u['unit_id']} is not at {p}")
            if hash_originals and sha256_file(p) != u["sha256"]:
                raise Refused(f"unit {u['unit_id']}: {p} is not the file whose sha256 the accepted file gives")
            try:
                path = str(p.relative_to(root))
            except ValueError:
                path = str(p)
            rows[split].append({"item_id": u["unit_id"], "label": u["label"], "path": path, "group": u["unit_id"],
                                "phash": u.get("phash") or "", "sha256": u["sha256"]})
        rows[split].sort(key=lambda r: r["item_id"])
    every = [r for s in SPLITS for r in rows[s]]
    if not all(r["phash"] for r in every):                       # a harness input check refuses mixed lengths: all or none
        for r in every:
            r["phash"] = ""

    out.mkdir(parents=True, exist_ok=True)
    report = {"schema": SPLIT_SCHEMA,
              "source": "L4's accepted files, taken as given: no re-split, no seed, no re-draw (DAT-852)",
              "resplit": False,
              "l4": {"lock_sha256": hashes["lock_sha256"], "screen_config_sha256": hashes["screen_config_sha256"],
                     "protocol_sha256": locked["PROTOCOL.md"],
                     **({"v2_lock_sha256": hashes["v2_lock_sha256"], "v2_lock_gate_view": hashes["v2_lock"]["gate_view"],
                         "v2_lock_context": hashes["v2_lock"]["context"]} if "v2_lock" in hashes else {})},
              "accepted_files": [{k: f.get(k) for k in ("name", "sha256", "schema", "splits", "complete")}
                                 for f in acc["files"]],
              "file_base": str(base),
              "params": {"phash_distance": distance, "hash_files": True},
              "phash_checked": sum(1 for r in every if r["phash"])}
    for split in SPLITS:
        path = out / f"{split}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(COLUMNS), lineterminator="\n")
            w.writeheader()
            w.writerows(rows[split])
        n = Counter(r["label"] for r in rows[split])
        report[split] = {"sha256": sha256_file(path), "real": n["real"], "ai": n["ai"]}
    if report["dev"]["sha256"] == report["test"]["sha256"]:
        raise Refused("dev.csv and test.csv are the same file")
    (out / "SPLIT.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m secondlook.gate_splits", description=__doc__.split("\n")[0])
    p.add_argument("--l4-dir", required=True)
    p.add_argument("--accepted", action="append", required=True, metavar="FILE",
                   help="an accepted file (L4's second-look-l4-accepted/1); repeat it for one file per split")
    p.add_argument("--out", required=True, help="a new folder for dev.csv, test.csv and SPLIT.json")
    p.add_argument("--original-root", help="paths in the csv are relative to this (default <l4>/images/original)")
    p.add_argument("--allow-inside-l4", action="store_true")
    p.add_argument("--hash-originals", action="store_true")
    p.add_argument("--v2-lock", metavar="PATH", help=calibrate.V2_LOCK_HELP)
    p.add_argument("--file-base", metavar="DIR", help="the folder each unit's relative `file` is relative to (default "
                   "<l4>; the real L4 folder when the gate folder holds no images)")
    a = p.parse_args(argv)
    try:
        r = build(a.l4_dir, a.accepted, a.out, a.original_root, a.allow_inside_l4, a.hash_originals, a.v2_lock, a.file_base)
    except Refused as exc:
        print(f"gate_splits: refused, nothing written: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"gate_splits: crashed: {exc!r}", file=sys.stderr)
        return 4
    print(json.dumps({k: r[k] for k in ("dev", "test", "phash_checked")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
