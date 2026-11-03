"""Freeze the classifier threshold on the L4 dev split only (DAT-848).

    python -m secondlook.calibrate score --arm original --split dev.csv --split-json SPLIT.json \
        --l4-dir <L4 folder> --root <images dir> --out dev_scores_original.csv
    python -m secondlook.calibrate score --arm format_matched --split dev.csv --split-json SPLIT.json \
        --l4-dir <L4 folder> --derived-root <L4 folder>/images-derived --out dev_scores_format_matched.csv
    python -m secondlook.calibrate freeze --scores dev_scores_original.csv dev_scores_format_matched.csv \
        --split-json SPLIT.json --dev-csv dev.csv --test-csv test.csv --l4-dir <L4 folder> \
        --frozen-by <name> --out secondlook/rungs/classifier_threshold.json

Arms (the project lead ruling, DAT-849 ec3ad415): the gate arms are `original` and `format_matched`.
`original` scores the fetched file dev.csv names (its sha256 checked when dev.csv has one).
`format_matched` scores `<derived-root>/format_matched/<item_id>.jpg`, the file L4
scripts/derive.py made from the original (L4 PROTOCOL.md part A, images-derived/<arm>/<id>.jpg;
the same path S7's gate_run reads). Each score row records the sha256 of the exact file scored.

Both take `--v2-lock PATH` for a v2 run (L4's v2 LOCK.json, proved against the gate folder; see docs/GATE-RUN.md); `freeze`
then records its sha256 as `v2_lock_sha256`.

Both commands refuse anything but the frozen L4 dev split: the L4 LOCK.json must exist and still
match the files it locks (PROTOCOL.md B1), and the CSVs must have the sha256 values SPLIT.json
(L2 gate.split) records. `score` refuses any CSV but dev.csv and leaves no partial file. `freeze`
refuses a scores file unless its item_ids are exactly the dev ids (no test id, none missing, none
repeated), its labels match dev.csv, every score is finite in [0, 1], and the dev set holds the
harness minimum (100 real + 100 AI).

`freeze` takes one scores file per gate arm and refuses a missing or repeated arm. It also refuses
unless TRAINING_DATA below states the detector's training data for the pinned weights; it writes that
statement, its overlap verdict and its sources into the threshold file (L4 PROTOCOL.md F).

The rule, fixed before any score is read: in each gate arm, the lowest threshold at which at
most floor(max_fpr * n_real) dev real images score at or above it (max_fpr 0.02); the frozen
threshold is the highest of the per-arm thresholds, so dev real FPR is at most max_fpr in every
gate arm. The file is the L2 harness's threshold.json (schema second-look-threshold/2, never
abstains, gate_arms ["original", "format_matched"]), so S7 can pass it to gate.harness unchanged.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:  # the L2 harness's floors when it is importable (S7 vendors it), else its values
    from gate import MIN_DEV_AI, MIN_DEV_REAL
except ImportError:
    MIN_DEV_REAL = MIN_DEV_AI = 100  # L2 gate/__init__.py, DAT-837 section 7.1

# The files L4's fetch.py --write-lock hashes into LOCK.json (L4 PROTOCOL.md B1).
L4_LOCKED = ("PROTOCOL.md", "manifest-real.csv", "manifest-ai.csv", "groups-real.csv", "groups-ai.csv",
             "scripts/fetch.py")
SCORE_FIELDS = ["item_id", "arm", "label", "score", "seconds", "device", "file_sha256"]
# The gate arms (the project lead ruling, DAT-849 ec3ad415). Both are scored on dev and both must pass.
GATE_ARMS = ("original", "format_matched")

# What the detector was trained on, and how that overlaps our test set (L4 PROTOCOL.md F: "Without
# that line the run is not accepted"; S7 gate_run refuses a threshold file without it). Written for
# the pinned weights only: freeze refuses if classifier.MODEL_SHA256 changes and this is not
# rewritten. Sources, quotes and the reproducible LAION check: docs/TRAINING-DATA.md (DAT-848).
TRAINING_OVERLAP_VERDICTS = ("none_found", "same_generator_family", "same_source")
TRAINING_DATA = {
    "for_weights_sha256": "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387",
    "overlap": "same_generator_family",
    "statement": (
        "Detector: Community Forensics ViT-S/16 384 (OwensLab/commfor-model-384, revision 6076002b, "
        "Park and Owens, CVPR 2025). Overlap verdict: same generator family on the AI side; no image-level "
        "overlap found on either side. AI side: the detector's training generators include the Stable "
        "Diffusion 1.x checkpoints themselves (CompVis/stable-diffusion-v1-1, v1-2, v1-3, v1-4 and "
        "runwayml/stable-diffusion-v1-5) among 4,763 Hugging Face latent diffusion models in the "
        "Systematic subset it trains on (systematic_set_metadata.csv; the dataset card: 'a vast majority "
        "of the generators we collect are derivatives of Stable Diffusion'). Our AI test images are "
        "Stable Diffusion 1.x output from DiffusionDB, exact version unrecorded, so the AI side is an "
        "in-family test, not a held-out-generator test. DiffusionDB is not named as a source in the "
        "paper, the dataset card or the code repository's README; the authors sampled the training images "
        "themselves from captions of real datasets or BLIP (paper section 3.5), so DiffusionDB's own "
        "images are not in it, and the released LAION training link list holds no DiffusionDB link. "
        "Real side: Wikimedia Commons is not a named real-image source. The real training images are "
        "LAION 40.30%, ImageNet 40.30%, CelebA 7.15%, COCO 4.39%, LandscapesHQ 3.34%, FFHQ 2.34%, "
        "IMD2020 1.17%, AFHQv2 0.59%, VISION 0.25%, Forchheim 0.13% and MetFaces 0.05% (dataset card). "
        "The released LAION link list (2,000,000 rows) holds 2,034 Commons file URLs and none of the "
        "1,088 Commons files in our manifest (matched by file name, 28 Oct 2026). Not checked: ImageNet "
        "and the other real sets image by image, copies of our photos at other URLs, and the backbone's "
        "CLIP pretraining on LAION-2B (paper section 4.1), which is web-crawled and may hold Commons "
        "photographs; Stable Diffusion 1.x was itself trained on subsets of LAION-2B(en) (its model card). "
        "Overlap with the real side's distribution is therefore possible but not measured."),
    "sources": [
        "https://arxiv.org/abs/2411.04125v2 (sections 3.1 to 3.5 and 4.1)",
        "https://huggingface.co/datasets/OwensLab/CommunityForensics/blob/f0b06f79d20389706bb76c626391fa870e0bf604/README.md",
        "https://huggingface.co/datasets/OwensLab/CommunityForensics/blob/f0b06f79d20389706bb76c626391fa870e0bf604/data/metadata/systematic_set_metadata.csv",
        "https://huggingface.co/datasets/OwensLab/CommunityForensics/blob/f0b06f79d20389706bb76c626391fa870e0bf604/data/Real/laion_commfor_train_subset_2M.csv",
        "https://github.com/JeongsooP/Community-Forensics/blob/ee5b71d43db0f3779e1edd64ee927b13f2dd6ad4/README.md",
        "https://huggingface.co/OwensLab/commfor-model-384/blob/6076002bf0d9dd37537f965ee2f06f826c333b61/README.md",
        "https://huggingface.co/datasets/poloclub/diffusiondb/blob/fb620fbe49fa4420e0734bd9c0df11f51176b61f/README.md",
        "https://huggingface.co/CompVis/stable-diffusion-v1-4/blob/133a221b8aa7292a167afc5127cb63fb5005638b/README.md",
    ],
}


def training_data(weights_sha256: str, td: dict | None = None) -> dict:
    """The training-data record for these weights, or Refused: a run without it is not accepted."""
    td = TRAINING_DATA if td is None else td
    stmt = td.get("statement")
    if not isinstance(stmt, str) or not stmt.strip():
        raise Refused("no training_data_statement: L4 PROTOCOL.md F requires one before a threshold is frozen")
    if td.get("for_weights_sha256") != weights_sha256:
        raise Refused(f"the training_data_statement was written for weights {td.get('for_weights_sha256')}, "
                      f"not the pinned {weights_sha256}: rewrite it for these weights (docs/TRAINING-DATA.md)")
    if td.get("overlap") not in TRAINING_OVERLAP_VERDICTS:
        raise Refused(f"training-data overlap {td.get('overlap')!r} is not one of {list(TRAINING_OVERLAP_VERDICTS)}")
    if not td.get("sources"):
        raise Refused("the training_data_statement names no sources")
    return td


class Refused(Exception):
    pass


def _sha(path) -> str:
    from .rungs.classifier import sha256_file
    return sha256_file(Path(path))


# The two real-class files a v2 gate folder rebuilds (with a `study` column), so its own LOCK.json and the v2 LOCK differ
# on them. The v2 LOCK's `gate_view` pins the hashes the gate folder's copies must have (L4 PROTOCOL.md part K).
V2_GATE_VIEW = ("manifest-real.csv", "groups-real.csv")
V2_LOCK_HELP = ("v2 run: L4's v2 LOCK.json (the gate folder's LOCK.json names its sha256 in v2_lock_sha256); "
                "verified against the gate folder, then recorded")


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def verify_v2_lock(l4_dir, v2_lock) -> dict:
    """The v2 arrangement's second lock, checked against the gate folder `l4_dir` (DAT-852). Two locks are in play:

    * the gate folder's own LOCK.json, which hashes the files the gate folder holds, and names the v2 LOCK in its
      `v2_lock_sha256`;
    * the v2 LOCK, L4's LOCK.json from the v2 join (PATH given as `v2_lock`), which the v2 accepted files carry the
      sha256 of in their `lock_sha256`.

    Accepted only when all of these hold, so the gate never takes the v2 LOCK on the gate folder's word alone:
    the sha256 of the v2 LOCK file is the gate folder LOCK's `v2_lock_sha256`; its `gate_view` hashes equal the gate
    folder's manifest-real.csv and groups-real.csv; each of its `context` files is in the gate folder with the sha256
    it records; and a file both locks hash (PROTOCOL.md, SCREEN-CONFIG.json, the AI manifest and groups, fetch.py) has
    the same hash in both. Returns {"sha256", "gate_view", "context"}; raises Refused otherwise."""
    l4 = Path(l4_dir)
    try:
        gate_lock = json.loads((l4 / "LOCK.json").read_text())
        gate_table = gate_lock["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"no usable L4 LOCK.json at {l4 / 'LOCK.json'} ({e}): the L4 set is not frozen")
    named = gate_lock.get("v2_lock_sha256") if isinstance(gate_lock, dict) else None
    if not _is_sha256(named):
        raise Refused(f"--v2-lock was given, but the gate folder's LOCK.json names no v2_lock_sha256 ({named!r}): "
                      "it was not made for a v2 run")
    path = Path(v2_lock)
    if not path.is_file():
        raise Refused(f"--v2-lock {path} is not a file")
    got = _sha(path)
    if got != named:
        raise Refused(f"--v2-lock {path} has sha256 {got}, but the gate folder's LOCK.json v2_lock_sha256 is {named}: "
                      "this is not the v2 LOCK the gate folder was made under")
    try:
        doc = json.loads(path.read_text())
        v2_table, view, context = doc["sha256"], doc["gate_view"], doc.get("context") or []
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"the v2 LOCK {path} is unusable ({e}): it needs `sha256` and `gate_view`")
    if not isinstance(v2_table, dict) or not isinstance(view, dict) or not isinstance(context, list):
        raise Refused("the v2 LOCK's `sha256`, `gate_view` or `context` has the wrong shape")
    absent = [n for n in V2_GATE_VIEW if n not in view]
    if absent:
        raise Refused(f"the v2 LOCK's gate_view does not hash {absent}: the gate folder's copies cannot be tied to it")
    for name, want in view.items():
        f = l4 / name
        if not _is_sha256(want) or not f.is_file() or _sha(f) != want:
            raise Refused(f"the gate folder's {name} does not match the v2 LOCK's gate_view: the gate folder was not "
                          "made from the v2 LOCK's files")
    for name, want in v2_table.items():
        if name in view or name not in gate_table:
            continue
        if gate_table[name] != want:
            raise Refused(f"{name} has sha256 {gate_table[name]} in the gate folder's LOCK.json but {want} in the v2 "
                          "LOCK: the two locks disagree on a file the gate folder holds unchanged")
    pins = []
    for ctx in context:
        name = ctx.get("file") if isinstance(ctx, dict) else None
        want = ctx.get("sha256") if isinstance(ctx, dict) else None
        if not isinstance(name, str) or not name or not _is_sha256(want):
            raise Refused(f"the v2 LOCK's context entry {ctx!r} has no file and sha256")
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise Refused(f"the v2 LOCK's context file {name!r} is not inside the gate folder")
        f = l4 / rel
        if not f.is_file() or _sha(f) != want:
            raise Refused(f"the gate folder's {name} is missing or is not the context file with sha256 {want} that the "
                          "v2 LOCK records")
        pins.append({"file": name, "sha256": want})
    return {"sha256": got, "gate_view": {n: view[n] for n in sorted(view)}, "context": pins}


def verify_l4(l4_dir: str, split_json: str, v2_lock: str | None = None) -> dict:
    """The L4 lock and the L2 split, checked. Returns SPLIT.json plus the hashes we checked.

    With `v2_lock` (a v2 run, DAT-852) the v2 LOCK is verified too (see verify_v2_lock) and the result carries
    `v2_lock_sha`, `v2_lock` and `accepted_lock_sha`, the sha256 an accepted file must name in its `lock_sha256`:
    the v2 LOCK's. Without it, `accepted_lock_sha` is the gate folder's own LOCK.json sha256, as before."""
    lock_path = Path(l4_dir) / "LOCK.json"
    try:
        lock = json.loads(lock_path.read_text())
        locked = lock["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"no usable L4 LOCK.json at {lock_path} ({e}): the L4 set is not frozen")
    if not isinstance(locked, dict) or set(L4_LOCKED) - set(locked):
        raise Refused(f"L4 LOCK.json does not lock all of {list(L4_LOCKED)}")
    for name, want in locked.items():
        f = Path(l4_dir) / name
        if not f.is_file() or _sha(f) != want:
            raise Refused(f"L4 {name} no longer matches LOCK.json: the L4 set changed after the lock")
    try:
        split = json.loads(Path(split_json).read_text())
        dev_sha, test_sha = split["dev"]["sha256"], split["test"]["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f"SPLIT.json {split_json} unusable ({e})")
    if not (isinstance(dev_sha, str) and len(dev_sha) == 64 and isinstance(test_sha, str) and len(test_sha) == 64):
        raise Refused("SPLIT.json lacks dev and test sha256 values")
    if dev_sha == test_sha:
        raise Refused("SPLIT.json gives the same sha256 for dev and test")
    out = {"split": split, "dev_sha": dev_sha, "test_sha": test_sha,
           "lock_sha": _sha(lock_path), "split_json_sha": _sha(split_json)}
    out["accepted_lock_sha"] = out["lock_sha"]
    out["gate_v2_named"] = lock.get("v2_lock_sha256") if isinstance(lock, dict) else None
    if v2_lock:
        v2 = verify_v2_lock(l4_dir, v2_lock)
        out.update({"v2_lock_sha": v2["sha256"], "v2_lock": v2, "accepted_lock_sha": v2["sha256"]})
        recorded = (split.get("l4") or {}).get("v2_lock_sha256") if isinstance(split, dict) else None
        if recorded not in (None, v2["sha256"]):
            raise Refused(f"SPLIT.json was written under the v2 LOCK with sha256 {recorded}, not {v2['sha256']}")
    return out


def require_dev_csv(path: str, l4: dict, what: str) -> None:
    got = _sha(path)
    if got == l4["test_sha"]:
        raise Refused(f"{what} {path} is the TEST split (sha256 {got}); only dev is used here")
    if got != l4["dev_sha"]:
        raise Refused(f"{what} {path} has sha256 {got}, not the dev sha256 {l4['dev_sha']} in SPLIT.json")


def pick_threshold(real_scores: list[float], max_fpr: float = 0.02) -> float:
    """Lowest t with #(real >= t) <= floor(max_fpr * n). Ties at the cut stay below t."""
    if not real_scores:
        raise ValueError("no dev real scores")
    if not all(math.isfinite(s) for s in real_scores):
        raise ValueError("a dev real score is not finite")
    ranked = sorted(real_scores, reverse=True)
    allowed = math.floor(max_fpr * len(ranked))
    if allowed >= len(ranked):
        return min(ranked)
    return math.nextafter(ranked[allowed], math.inf)


def rates(scores: list[tuple[str, float]], t: float) -> dict:
    real = [s for lab, s in scores if lab == "real"]
    ai = [s for lab, s in scores if lab == "ai"]
    fp = sum(s >= t for s in real)
    tp = sum(s >= t for s in ai)
    return {"n_real": len(real), "n_ai": len(ai), "false_positives": fp, "true_positives": tp,
            "fpr": fp / len(real) if real else None, "tpr": tp / len(ai) if ai else None}


def derived_path(derived_root: Path, arm: str, item_id: str) -> Path:
    """<derived-root>/<arm>/<item_id>.jpg, refusing an item_id that would leave that folder."""
    if not item_id or Path(item_id).name != item_id or item_id in (".", ".."):
        raise Refused(f"item_id {item_id!r} cannot name a file in {derived_root / arm}")
    return derived_root / arm / f"{item_id}.jpg"


def cmd_score(a) -> int:
    from .rungs import classifier

    if a.arm not in GATE_ARMS:
        raise Refused(f"--arm {a.arm!r} is not a gate arm {list(GATE_ARMS)}")
    derived = a.arm != "original"
    if derived and not a.derived_root:
        raise Refused(f"--arm {a.arm} needs --derived-root (the L4 images-derived folder)")
    l4 = verify_l4(a.l4_dir, a.split_json, a.v2_lock)
    require_dev_csv(a.split, l4, "--split")
    rows = list(csv.DictReader(open(a.split, newline="", encoding="utf-8")))
    root = Path(a.root) if a.root else None
    out = Path(a.out)
    part = out.with_name(out.name + ".part")
    times = []
    try:
        with open(part, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
            w.writeheader()
            for r in rows:
                if derived:
                    path = derived_path(Path(a.derived_root), a.arm, r["item_id"])
                    if not path.is_file():
                        raise Refused(f"{r['item_id']}: no derived {a.arm} file at {path}")
                    file_sha = classifier.sha256_file(path)
                else:
                    path = Path(r["path"]) if r.get("path") else None
                    if root is not None and (path is None or not path.is_absolute()):
                        path = root / (path or r["item_id"])
                    file_sha = classifier.sha256_file(path)
                    if r.get("sha256") and file_sha != r["sha256"]:
                        raise Refused(f"{r['item_id']}: {path} is not the file dev.csv names (sha256 differs)")
                t0 = time.perf_counter()
                s = classifier.score_images([str(path)])[0]
                dt = time.perf_counter() - t0
                times.append(dt)
                w.writerow({"item_id": r["item_id"], "arm": a.arm, "label": r["label"], "score": repr(s),
                            "seconds": f"{dt:.4f}", "device": classifier._device, "file_sha256": file_sha})
    except Exception as e:  # one unreadable dev image fails the run: a partial file must never be frozen
        part.unlink(missing_ok=True)
        if isinstance(e, Refused):
            raise
        raise Refused(f"scoring stopped at item {len(times) + 1} of {len(rows)} ({e}); no scores written") from e
    part.replace(out)
    warm = times[1:] or times
    print(json.dumps({"arm": a.arm, "scored": len(times), "first_s": round(times[0], 3) if times else None,
                      "median_s": round(statistics.median(warm), 4) if warm else None,
                      "mean_s": round(statistics.fmean(warm), 4) if warm else None}))
    return 0


def read_dev_scores(scores_path: str, dev_csv: str, test_csv: str) -> tuple[str, list[dict]]:
    """(arm, the scored rows), refused unless they are exactly the dev items with dev's labels,
    all from one gate arm."""
    dev = {r["item_id"]: r["label"] for r in csv.DictReader(open(dev_csv, newline="", encoding="utf-8"))}
    test_ids = {r["item_id"] for r in csv.DictReader(open(test_csv, newline="", encoding="utf-8"))}
    with open(scores_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != SCORE_FIELDS:
            raise Refused(f"scores header is {reader.fieldnames}, not {SCORE_FIELDS}")
        rows = list(reader)
    arms = {r["arm"] for r in rows}
    if len(arms) != 1 or not arms <= set(GATE_ARMS):
        raise Refused(f"{scores_path} must hold one gate arm {list(GATE_ARMS)}, not {sorted(arms)}")
    arm = arms.pop()
    ids = [r["item_id"] for r in rows]
    leaked = sorted(set(ids) & test_ids)
    if leaked:
        raise Refused(f"{len(leaked)} TEST item(s) in the scores, e.g. {leaked[:3]}: refusing test leakage")
    if len(ids) != len(set(ids)):
        raise Refused("an item_id appears twice in the scores")
    extra, missing = sorted(set(ids) - set(dev)), sorted(set(dev) - set(ids))
    if extra or missing:
        raise Refused(f"scores do not cover exactly the dev split: {len(missing)} missing "
                      f"(e.g. {missing[:3]}), {len(extra)} not in dev (e.g. {extra[:3]})")
    for r in rows:
        if r["label"] != dev[r["item_id"]]:
            raise Refused(f"{r['item_id']} is labelled {r['label']!r} in the scores but {dev[r['item_id']]!r} in dev.csv")
        try:
            v = float(r["score"])
        except ValueError:
            v = math.nan
        if not (math.isfinite(v) and 0.0 <= v <= 1.0):
            raise Refused(f"{r['item_id']} has score {r['score']!r}, not a finite number in [0, 1]")
        r["score"] = v
        fs = r["file_sha256"]
        if len(fs) != 64 or any(c not in "0123456789abcdef" for c in fs):
            raise Refused(f"{r['item_id']} has file_sha256 {fs!r}, not a sha256")
    n_real = sum(r["label"] == "real" for r in rows)
    n_ai = sum(r["label"] == "ai" for r in rows)
    if n_real + n_ai != len(rows):
        raise Refused("a label is neither 'real' nor 'ai'")
    if n_real < MIN_DEV_REAL or n_ai < MIN_DEV_AI:
        raise Refused(f"dev has {n_real} real + {n_ai} AI; the harness needs at least {MIN_DEV_REAL} + {MIN_DEV_AI}")
    return arm, rows


def cmd_freeze(a) -> int:
    from .rungs import classifier

    td = training_data(classifier.MODEL_SHA256)
    l4 = verify_l4(a.l4_dir, a.split_json, a.v2_lock)
    require_dev_csv(a.dev_csv, l4, "--dev-csv")
    if _sha(a.test_csv) != l4["test_sha"]:
        raise Refused(f"--test-csv {a.test_csv} does not have the test sha256 in SPLIT.json")
    by_arm = {}
    for path in a.scores:
        arm, rows = read_dev_scores(path, a.dev_csv, a.test_csv)
        if arm in by_arm:
            raise Refused(f"two scores files for arm {arm} ({by_arm[arm]['path']} and {path})")
        by_arm[arm] = {"path": path, "rows": rows}
    missing = [arm for arm in GATE_ARMS if arm not in by_arm]
    if missing:
        raise Refused(f"no dev scores for gate arm(s) {missing}: every gate arm {list(GATE_ARMS)} is calibrated")
    same = ({r["file_sha256"] for r in by_arm["original"]["rows"]}
            & {r["file_sha256"] for arm in GATE_ARMS if arm != "original" for r in by_arm[arm]["rows"]})
    if same:  # derive.py re-encodes every file without metadata, so a derived file never equals its original
        raise Refused(f"{len(same)} file(s) scored in a derived arm are byte-identical to original-arm files "
                      f"(e.g. {sorted(same)[:1]}): the derived arm did not read the derived images")
    for arm, d in by_arm.items():
        d["scores"] = [(r["label"], r["score"]) for r in d["rows"]]
        d["threshold"] = pick_threshold([s for lab, s in d["scores"] if lab == "real"], a.max_fpr)
        if not 0.0 <= d["threshold"] <= 1.0:
            raise Refused(f"{arm} threshold {d['threshold']!r} falls outside [0, 1]")
    t = max(d["threshold"] for d in by_arm.values())
    phash = (l4["split"].get("params") or {}).get("phash_distance")
    if isinstance(phash, bool) or not isinstance(phash, int) or phash < 0:
        raise Refused("SPLIT.json params.phash_distance is missing or not a whole number")

    cfg = {
        "schema": "second-look-threshold/2",
        "threshold": t,
        "ai_if": "score>=threshold",
        "abstain": {"rule": "none"},
        "classifier": {"name": classifier.MODEL_NAME, "version": classifier.MODEL_REVISION,
                       "weights_sha256": classifier.MODEL_SHA256},
        "dev_split_sha256": l4["dev_sha"],
        "test_split_sha256": l4["test_sha"],
        "split_json_sha256": l4["split_json_sha"],
        "l4_lock_sha256": l4["lock_sha"],
        **({"v2_lock_sha256": l4["v2_lock_sha"]} if "v2_lock_sha" in l4 else {}),
        "phash_distance": phash,
        "gate_arms": list(GATE_ARMS),
        "training_data_statement": td["statement"],
        "training_data_overlap": td["overlap"],
        "training_data_sources": list(td["sources"]),
        "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frozen_by": a.frozen_by,
        "rule": (f"in each gate arm, the lowest threshold with dev real FPR <= {a.max_fpr} (ties at the cut "
                 f"stay below); the highest of these is frozen (wsai-lead ruling, DAT-849 ec3ad415)"),
        "threshold_from_arms": [arm for arm in GATE_ARMS if by_arm[arm]["threshold"] == t],
        "arms": {arm: {"threshold": by_arm[arm]["threshold"],
                       "dev_scores_sha256": classifier.sha256_file(Path(by_arm[arm]["path"])),
                       "dev_metrics_at_arm_threshold": rates(by_arm[arm]["scores"], by_arm[arm]["threshold"]),
                       "dev_metrics": rates(by_arm[arm]["scores"], t),
                       "devices": sorted({r.get("device") or "" for r in by_arm[arm]["rows"]})}
                 for arm in GATE_ARMS},
    }
    Path(a.out).write_text(json.dumps(cfg, indent=2) + "\n")
    print(json.dumps({"out": a.out, "sha256": classifier.sha256_file(Path(a.out)), "threshold": t,
                      "arms": {arm: {"threshold": v["threshold"], **v["dev_metrics"]}
                               for arm, v in cfg["arms"].items()}}))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m secondlook.calibrate")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score the L4 dev split CSV (item_id,label[,path][,sha256]) in one arm")
    s.add_argument("--arm", required=True, choices=GATE_ARMS,
                   help="original: the files dev.csv names; format_matched: <derived-root>/format_matched/<item_id>.jpg")
    s.add_argument("--split", required=True, help="dev.csv from L2 gate.split; refused unless SPLIT.json's dev")
    s.add_argument("--split-json", required=True, help="L2 gate.split SPLIT.json (dev/test sha256)")
    s.add_argument("--l4-dir", required=True, help="the L4 test-set folder holding LOCK.json")
    s.add_argument("--v2-lock", help=V2_LOCK_HELP)
    s.add_argument("--root", help="original arm: folder holding the images when path is relative or absent")
    s.add_argument("--derived-root", help="derived arms: the L4 images-derived folder (<arm>/<item_id>.jpg)")
    s.add_argument("--out", required=True)
    f = sub.add_parser("freeze", help="write the threshold file from dev scores")
    f.add_argument("--scores", required=True, nargs="+", help="one dev scores file per gate arm")
    f.add_argument("--split-json", required=True, help="L2 gate.split SPLIT.json (dev/test sha256)")
    f.add_argument("--dev-csv", required=True)
    f.add_argument("--test-csv", required=True, help="read only to refuse test ids in the scores")
    f.add_argument("--l4-dir", required=True, help="the L4 test-set folder holding LOCK.json")
    f.add_argument("--v2-lock", help=V2_LOCK_HELP)
    f.add_argument("--max-fpr", type=float, default=0.02)
    f.add_argument("--frozen-by", required=True)
    f.add_argument("--out", required=True)
    a = p.parse_args(argv)
    try:
        return cmd_score(a) if a.cmd == "score" else cmd_freeze(a)
    except (Refused, OSError, csv.Error, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
