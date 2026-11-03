"""Fit and freeze the desk triage queue's band thresholds (J4, DAT-1367) on the TrueFake dev split only.

    python scripts/fit_queue_bands.py --scores scale-eval/scores.csv --split arena/split.csv \
        --prereg arena/PREREG.md --out secondlook/queue_bands.json

Reads the detector scores already in scores.csv (both forms) for the rows that split.csv marks `dev`, and
nothing else: a held-out row is never read into the fit. Applies rule BAND (arena/PREREG.md section 5):
t_hi by rule OP (largest dev catch with at most floor(0.01 x 169) = 1 dev real flagged, ties to the highest
threshold), t_lo the largest dev score with at most floor(0.01 x 174) = 1 dev AI image below it. Writes the
bands file as canonical JSON and its sha256 beside it. The data files stay outside this repository.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from secondlook import queue  # noqa: E402

CODE_COMMIT = "b5953956ffa2d047c16715334b90d851e2d76454"   # the ladder that wrote scores.csv (PREREG section 1)
TRUEFAKE_LICENCE = "CC BY 4.0"


def dev_scores(scores_path: Path, split_path: Path) -> tuple[dict, dict]:
    """{form: {"real": [...], "ai": [...]}} for dev rows only, and a count of what was read and skipped."""
    with split_path.open(newline="") as f:
        split = {r["sha256"]: r for r in csv.DictReader(f)}
    dev = {sha: r for sha, r in split.items() if r["split"] == "dev"}
    out = {form: {"real": [], "ai": []} for form in queue.FORMS}
    seen = {"dev_rows_in_split": len(dev), "dev_rows_scored": 0, "held_out_rows_ignored": 0}
    with scores_path.open(newline="") as f:
        for r in csv.DictReader(f):
            if r["sha256"] not in dev:
                if r["sha256"] in split:
                    seen["held_out_rows_ignored"] += 1
                continue
            if r["licence"] != TRUEFAKE_LICENCE or dev[r["sha256"]]["label"] != r["label"]:
                raise SystemExit(f"row {r['sha256']} is not a TrueFake row with the split's label")
            for form in queue.FORMS:
                if r[f"{form}_version"] != CODE_COMMIT:
                    raise SystemExit(f"row {r['sha256']} was scored by {r[f'{form}_version']}, not {CODE_COMMIT}")
                s = r[f"{form}_classifier_score"]
                if s == "":
                    raise SystemExit(f"row {r['sha256']} has no {form} detector score")
                if r[f"{form}_answer"] in queue.FLAG_ANSWERS:
                    raise SystemExit("a TrueFake dev row has f = 1; the fit assumes f = 0 as PREREG section 3 states")
                out[form][r["label"]].append(float(s))
            seen["dev_rows_scored"] += 1
    if seen["dev_rows_scored"] != len(dev):
        raise SystemExit(f"{len(dev) - seen['dev_rows_scored']} dev rows have no scores")
    return out, seen


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scores", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--prereg", type=Path, required=True)
    p.add_argument("--out", type=Path, default=queue.BANDS_PATH)
    a = p.parse_args(argv)
    scores, seen = dev_scores(a.scores, a.split)
    bands = {
        "schema": queue.BANDS_SCHEMA,
        "rule": ("Rule BAND, arena/PREREG.md section 5 (DAT-1366 J3): auto_decided when f = 1 or d >= t_hi "
                 "(t_hi by rule OP, at most floor(0.01 x dev reals) dev reals flagged, ties to the highest); "
                 "likely_fine when f = 0 and d < t_lo (t_lo the largest dev score with at most "
                 "floor(0.01 x dev AI) dev AI images below it); everything else to a person."),
        "max_error": queue.MAX_ERROR,
        "fitted_on": {
            "dataset": "TrueFake (Dell'Anna et al., IJCNN 2025, arXiv 2504.20658), Facebook-shared subset",
            "licence": TRUEFAKE_LICENCE,
            "split": "dev (35% of groups by sha256(SEED:group), PREREG section 2); held-out rows not read",
            "rows": seen,
            "scores_file": a.scores.name, "scores_sha256": queue.file_sha256(a.scores),
            "split_file": a.split.name, "split_sha256": queue.file_sha256(a.split),
            "prereg_file": a.prereg.name, "prereg_sha256": queue.file_sha256(a.prereg),
            "code_commit": CODE_COMMIT,
            "detector": "Community Forensics ViT-S/16 384 (classifier rung), score as the rung reports it (6 dp)",
        },
        "forms": {form: queue.fit_bands(s["real"], s["ai"]) for form, s in scores.items()},
        "gate_note": ("The 29 Oct classifier gate (classifier_gate.json) stays FAIL and is not retuned. These "
                      "thresholds only route images in the desk queue; they never raise a ladder concern."),
    }
    digest = queue.write_bands(bands, a.out)
    print(f"wrote {a.out} sha256 {digest}")
    for form, b in bands["forms"].items():
        print(form, {k: b[k] for k in ("t_hi", "t_lo", "dev_real_flag_rate", "dev_ai_clear_rate")}, b["dev"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
