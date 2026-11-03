"""Score one gate run from a predictions CSV, the frozen threshold file and the split.

    python -m gate.harness --predictions preds.csv --threshold threshold.json \\
        --dev-split splits/dev.csv --test-split splits/test.csv --out out/run-01 \\
        [--run-id ID] [--date YYYY-MM-DD]

Writes RESULT.md and result.json into --out. Exit codes: 0 PASS, 1 FAIL,
2 bad input (nothing is scored or written), 3 INVALID (a required arm or a
test image is missing, or a gate arm is below the locked denominators),
4 the harness crashed or could not write the result (not a gate outcome;
neither file is left behind).

Split CSV columns (dev.csv, test.csv): item_id, label, group (the source unit:
an author, an event or a near-duplicate cluster), and sha256 or phash on every
row. A group is one source identity across both splits and both labels: one
test row per group, no group in both dev and test, no group under both labels,
because the bound counts each test row as one independent trial.

Predictions CSV columns: item_id, arm, verdict (ai|real|abstain), score,
predicted_at (ISO 8601 with a time zone); optional: label, generator.
Labels and test-set membership come from test.csv, never from the
predictions: a label column, if present, must agree with test.csv.
Item ids name the underlying image, so each test.csv id appears once in
every arm.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sys
import traceback
import unicodedata
from collections import Counter, defaultdict

from gate import (
    ARM_COMPRESSED, ARM_ORIGINAL, CATCH_LOWER_MIN, CONFIDENCE,
    DEGRADED_WARN_UPPER, FP_UPPER_MAX, KNOWN_ARMS, MIN_AI, MIN_DEV_AI,
    MIN_DEV_REAL, MIN_REAL, REQUIRED_ARMS,
)
from gate.stats import lower_bound, upper_bound

THRESHOLD_SCHEMA = "second-look-threshold/2"
RESULT_SCHEMA = "second-look-result/2"
LABELS = ("real", "ai")
VERDICTS = ("ai", "real", "abstain")
AI_IF = {"score>=threshold": lambda s, t: s >= t,
         "score>threshold": lambda s, t: s > t}
ABSTAIN_RULES = ("none", "score_band")
REAL_ABSTAIN_COUNTS_AS = ("not_flagged", "flagged")
REQUIRED_COLUMNS = ("item_id", "arm", "verdict", "score", "predicted_at")

# Every RESULT.md and result.json carries this, whatever the status (DAT-795, Nova
# 69bd8dbb check 4 and ade1c711). See README, "Independence".
ASSUMPTIONS = {
    "statement": (
        "The bounds hold only under the documented sampling and independence "
        "assumptions: each test.csv row is an independent draw, one per source group, "
        "from the population the claim is about. They are bounds for this fixed "
        "benchmark, not for all images or for generators it does not contain."),
    "enforced": [
        "every dev.csv and test.csv row names a source group",
        "one test.csv row per source group",
        "no source group in both dev.csv and test.csv",
        "no source group under both labels, in either split",
        "no dev/test image overlap by id, file sha256 or phash",
    ],
    "not_checked": [
        "that the group values are right and alias-free (one author under two "
        "spellings counts as two groups)",
        "that the test rows were drawn at random",
        "that the test population matches the images the classifier will be used on",
    ],
    "bounds_by_label": (
        "The false-positive bound is computed over real test rows only and the "
        "AI-caught bound over AI test rows only, after the global group split; the "
        "labels are never pooled into one denominator."),
    "see": "README.md, Independence",
}

EXIT = {"PASS": 0, "FAIL": 1, "INVALID": 3}
EXIT_BAD_INPUT = 2
EXIT_CRASH = 4


class InputError(Exception):
    """The inputs break the protocol; no result is written."""


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _finite(v) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def parse_time(value, what: str) -> _dt.datetime:
    try:
        t = _dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise InputError(f"{what} {value!r} is not an ISO 8601 time") from None
    if t.utcoffset() is None:
        raise InputError(f"{what} {value!r} has no time zone; write UTC, "
                         "e.g. 2026-10-29T09:00:00Z")
    return t


def load_abstain_rule(rule) -> dict:
    """The frozen abstain rule. Abstaining is a verdict like the others, so it
    must follow from the score by a rule fixed before the test run."""
    if not isinstance(rule, dict) or rule.get("rule") not in ABSTAIN_RULES:
        raise InputError('abstain is required: {"rule": "none"} (the classifier never '
                         'abstains) or {"rule": "score_band", "low": L, "high": H, '
                         '"real_abstain_counts_as": "not_flagged" or "flagged"}')
    if rule["rule"] == "score_band":
        lo, hi = rule.get("low"), rule.get("high")
        if not (_finite(lo) and _finite(hi) and lo < hi):
            raise InputError("abstain score_band needs finite numbers low < high")
        if rule.get("real_abstain_counts_as") not in REAL_ABSTAIN_COUNTS_AS:
            raise InputError("abstain.real_abstain_counts_as must be one of "
                             f"{list(REAL_ABSTAIN_COUNTS_AS)}")
    return rule


def load_threshold(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise InputError(f"threshold file is not JSON: {e}") from None
    if not isinstance(cfg, dict):
        raise InputError("threshold file must be a JSON object")
    if cfg.get("schema") != THRESHOLD_SCHEMA:
        raise InputError(f"threshold file schema must be {THRESHOLD_SCHEMA!r}")
    if not _finite(cfg.get("threshold")):
        raise InputError("threshold must be a finite number")
    if cfg.get("ai_if") not in AI_IF:
        raise InputError(f"ai_if must be one of {sorted(AI_IF)}")
    clf = cfg.get("classifier")
    if not isinstance(clf, dict) or not all(_text(clf.get(k)) for k in
                                            ("name", "version", "weights_sha256")):
        raise InputError("classifier.name, classifier.version and classifier.weights_sha256 "
                         "are all required")
    for key in ("dev_split_sha256", "test_split_sha256"):
        if not _text(cfg.get(key)):
            raise InputError(f"{key} is required (copy it from SPLIT.json): the threshold "
                             "is frozen against one split")
    if not _text(cfg.get("frozen_by")):
        raise InputError("frozen_by is required: who froze the threshold")
    cfg["_frozen_at"] = parse_time(cfg.get("frozen_at"), "frozen_at")
    d = cfg.get("phash_distance")
    if isinstance(d, bool) or not isinstance(d, int) or d < 0:
        raise InputError("phash_distance must be a whole number of bits, 0 or more "
                         "(the --phash-distance the split used)")
    cfg["abstain"] = load_abstain_rule(cfg.get("abstain"))
    arms = cfg.setdefault("gate_arms", [ARM_ORIGINAL])
    if not isinstance(arms, list) or not arms or not all(_text(a) for a in arms):
        raise InputError("gate_arms must be a non-empty list of arm names")
    if ARM_ORIGINAL not in arms:
        raise InputError(f"gate_arms must include {ARM_ORIGINAL!r} (DAT-837 7.5, condition A); "
                         "other arms can be added to the gate, not put in its place")
    return cfg


def expected_verdict(score: float, cfg: dict) -> str:
    rule = cfg["abstain"]
    if rule["rule"] == "score_band" and rule["low"] <= score < rule["high"]:
        return "abstain"
    return "ai" if AI_IF[cfg["ai_if"]](score, float(cfg["threshold"])) else "real"


def describe_rule(cfg: dict) -> str:
    rule = cfg["abstain"]
    text = f"AI if {cfg['ai_if']} with threshold {cfg['threshold']}"
    if rule["rule"] == "score_band":
        text += f"; abstain if {rule['low']} <= score < {rule['high']}"
    else:
        text += "; never abstain"
    return text


def load_split(path: str, which: str) -> list[dict]:
    """dev.csv or test.csv as written by gate.split. Every row needs a sha256 or a
    phash, so overlap is never checked by id alone, and a group, so a source can be
    checked across both splits."""
    items, seen, problems = [], set(), []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for col in ("item_id", "label", "group"):
            if col not in (reader.fieldnames or []):
                raise InputError(f"{which} split has no {col} column")
        for line, raw in enumerate(reader, start=2):
            it = {k: (raw.get(k) or "").strip()
                  for k in ("item_id", "label", "group", "phash", "sha256")}
            it["label"] = it["label"].lower()
            if not it["item_id"]:
                problems.append(f"line {line}: empty item_id")
            elif it["item_id"] in seen:
                problems.append(f"line {line}: item_id {it['item_id']} appears twice")
            elif it["label"] not in LABELS:
                problems.append(f"line {line}: label must be real or ai")
            elif not it["phash"] and not it["sha256"]:
                problems.append(f"line {line} ({it['item_id']}): no sha256 and no phash, so "
                                "it could only be checked by id")
            elif not it["group"]:
                problems.append(f"line {line} ({it['item_id']}): empty group; every {which} "
                                "image needs its source unit")
            elif not group_key(it["group"]):
                problems.append(f"line {line} ({it['item_id']}): group {it['group']!r} is "
                                "only a label, not a source unit")
            else:
                if it["phash"]:
                    try:
                        it["_phash"] = int(it["phash"], 16)
                    except ValueError:
                        problems.append(f"line {line}: phash {it['phash']!r} is not hex")
                        continue
                seen.add(it["item_id"])
                items.append(it)
    if problems:
        raise InputError(f"bad {which} split rows:\n  " + "\n  ".join(problems[:20]))
    if not items:
        raise InputError(f"{which} split has no rows")
    return items


def hash_coverage(dev: list[dict], test: list[dict]) -> dict:
    rows = dev + test
    return {"rows": len(rows), "sha256": sum(1 for it in rows if it["sha256"]),
            "phash": sum(1 for it in rows if it["phash"])}


def check_hash_coverage(cov: dict) -> None:
    """Each row has a hash; one kind must be on every row, or a pair of rows with
    different kinds would be compared by id only."""
    if cov["sha256"] < cov["rows"] and cov["phash"] < cov["rows"]:
        raise InputError(f"of {cov['rows']} dev and test rows, {cov['sha256']} have a sha256 "
                         f"and {cov['phash']} a phash; one of the two must be on every row "
                         "(gate.split --hash-files gives every row a sha256)")


_LABEL_TOKEN = re.compile(r"^(?:real|ai)[\s:/|_.=-]+|[\s:/|_.=-]+(?:real|ai)$")


def group_key(raw: str) -> str:
    """The global source identity of a group value: NFKC, casefolded, trimmed, with a
    label token at the start or end removed (`real:ada`, `ai/ada`, `ada-ai` are all
    `ada`). A group is one source whatever the label, so a label written into the
    group must not make a real and an AI row from one source look like two sources
    (Nova, DAT-795 ade1c711)."""
    key = unicodedata.normalize("NFKC", raw).casefold().strip()
    while True:
        bare = _LABEL_TOKEN.sub("", key, count=1).strip()
        if bare == key:
            return "" if key in LABELS else key
        key = bare


def check_source_groups(dev: list[dict], test: list[dict]) -> None:
    """The scorer's group check, run before any prediction is read. A group is one
    source identity across both splits and both labels (DAT-795, Nova 69bd8dbb and
    ade1c711). Refused:
    - more than one test.csv row in a group: the exact bound counts each row as an
      independent trial, so two rows from one author, event or near-duplicate
      cluster would count one source twice (7c34d17e, option 1);
    - a group in both dev.csv and test.csv: the threshold was tuned on that source;
    - a group under both labels, in either split: a real/AI pair from one source is
      excluded or reconciled by the builder, never given two identities.
    Groups are compared by group_key."""
    rows = defaultdict(list)
    for which, split in (("dev", dev), ("test", test)):
        for it in split:
            rows[group_key(it["group"])].append((which, it))
    problems = []
    for key, members in rows.items():
        test_ids = [it["item_id"] for which, it in members if which == "test"]
        spellings = sorted({it["group"] for _, it in members})
        named = f"group {key!r}" + (f" (written {', '.join(map(repr, spellings[:5]))})"
                                    if spellings != [key] else "")
        if len(test_ids) > 1:
            problems.append(f"{named}: {len(test_ids)} test.csv rows ({', '.join(test_ids[:5])}); "
                            "the bound needs one row per source")
        if {"dev", "test"} <= {which for which, _ in members}:
            dev_ids = [it["item_id"] for which, it in members if which == "dev"]
            problems.append(f"{named}: in both dev.csv ({', '.join(dev_ids[:5])}) and test.csv "
                            f"({', '.join(test_ids[:5])})")
        if len({it["label"] for _, it in members}) > 1:
            by = ", ".join(f"{which} {it['item_id']} {it['label']}" for which, it in members[:6])
            problems.append(f"{named}: under both labels ({by})")
    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise InputError(f"{len(problems)} source group problem(s); a group is one source "
                         f"across dev, test and both labels:\n  {shown}{more}")


def _same_image(a: dict, b: dict, distance: int) -> str | None:
    if a["sha256"] and a["sha256"] == b["sha256"]:
        return "same file sha256"
    if "_phash" in a and "_phash" in b and (a["_phash"] ^ b["_phash"]).bit_count() <= distance:
        return f"phash within {distance} bits"
    return None


def check_independence(dev: list[dict], test: list[dict], distance: int) -> None:
    """Refuse a dev and a test row that are the same image (by id, file sha256 or
    phash), and two test images of the same label that are the same image."""
    lengths = {len(it["phash"]) for it in dev + test if it["phash"]}
    if len(lengths) > 1:
        raise InputError(f"phash values in the splits have different lengths {sorted(lengths)}")
    problems = []
    shared = sorted({it["item_id"] for it in dev} & {it["item_id"] for it in test})
    if shared:
        problems.append(f"{len(shared)} test item(s) are also in the dev split, "
                        f"e.g. {', '.join(shared[:5])}")
    for t in test:
        for d in dev:
            why = _same_image(t, d, distance)
            if why:
                problems.append(f"test {t['item_id']} and dev {d['item_id']}: {why}")
    for i, a in enumerate(test):
        for b in test[i + 1:]:
            if a["label"] == b["label"]:
                why = _same_image(a, b, distance)
                if why:
                    problems.append(f"test {a['item_id']} and test {b['item_id']} "
                                    f"({a['label']}): {why}, so they are not independent")
    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise InputError(f"dev/test overlap or duplicate test images:\n  {shown}{more}")


def load_predictions(path: str, cfg: dict, labels: dict[str, str]) -> list[dict]:
    rows, seen, problems = [], set(), []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise InputError(f"predictions CSV is missing columns: {', '.join(missing)}")
        for line, raw in enumerate(reader, start=2):
            row = {k: (raw.get(k) or "").strip()
                   for k in (*REQUIRED_COLUMNS, "label", "generator")}
            where = f"line {line} ({row['item_id'] or 'no id'})"
            if not row["item_id"] or not row["arm"]:
                problems.append(f"{where}: item_id and arm are required")
                continue
            if row["item_id"] not in labels:
                problems.append(f"{where}: not in test.csv; only test-split images are scored")
                continue
            key = (row["arm"], row["item_id"])
            if key in seen:
                problems.append(f"{where}: item appears twice in arm {row['arm']}")
                continue
            seen.add(key)
            truth, given = labels[row["item_id"]], row["label"].lower()
            if given and given != truth:
                problems.append(f"{where}: label {given!r} disagrees with test.csv ({truth!r})")
                continue
            row["label"], row["verdict"] = truth, row["verdict"].lower()
            if row["verdict"] not in VERDICTS:
                problems.append(f"{where}: verdict must be ai, real or abstain, got {row['verdict']!r}")
                continue
            try:
                row["score"] = float(row["score"])
            except ValueError:
                problems.append(f"{where}: score {row['score']!r} is not a number "
                                "(every row needs one, abstentions too)")
                continue
            if not math.isfinite(row["score"]):
                problems.append(f"{where}: score {row['score']} is not a finite number")
                continue
            try:
                row["_at"] = parse_time(row["predicted_at"], "predicted_at")
            except InputError as e:
                problems.append(f"{where}: {e}")
                continue
            want = expected_verdict(row["score"], cfg)
            if row["verdict"] != want:
                problems.append(f"{where}: verdict {row['verdict']} does not follow from score "
                                f"{row['score']} and the frozen rule ({describe_rule(cfg)}), "
                                f"which gives {want}")
                continue
            rows.append(row)
    if problems:
        shown = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise InputError(f"{len(problems)} bad prediction row(s):\n  {shown}{more}")
    if not rows:
        raise InputError("predictions CSV has no rows")
    return rows


def _rate(k: int, n: int, side: str) -> dict:
    out = {"k": k, "n": n, "observed": (k / n) if n else None}
    if n:
        bound = upper_bound(k, n, CONFIDENCE) if side == "upper" else lower_bound(k, n, CONFIDENCE)
        out[f"{side}_bound"] = bound
    else:
        out[f"{side}_bound"] = None
    return out


def arm_metrics(rows: list[dict], real_abstain_flagged: bool = False) -> dict:
    cm = {label: Counter() for label in LABELS}
    gens = defaultdict(Counter)
    for r in rows:
        cm[r["label"]][r["verdict"]] += 1
        if r["label"] == "ai":
            g = r.get("generator") or "(not given)"
            gens[g]["n"] += 1
            gens[g]["caught"] += r["verdict"] == "ai"
    n_real, n_ai = sum(cm["real"].values()), sum(cm["ai"].values())
    fp, tn = cm["real"]["ai"], cm["real"]["real"]
    tp, fn = cm["ai"]["ai"], cm["ai"]["real"]
    # Gate denominators (DAT-837 7.4): all real items, all AI items. An AI
    # abstention is never a catch; a real one counts as the frozen rule says.
    fp_gate = fp + (cm["real"]["abstain"] if real_abstain_flagged else 0)
    m = {
        "n_real": n_real,
        "n_ai": n_ai,
        "confusion": {label: {v: cm[label][v] for v in VERDICTS} for label in LABELS},
        "real_abstain_counts_as": "flagged" if real_abstain_flagged else "not_flagged",
        "false_positive": _rate(fp_gate, n_real, "upper"),
        "ai_caught": _rate(tp, n_ai, "lower"),
        # The verdict-bearing subset, reported alongside.
        "false_positive_verdict_bearing": _rate(fp, fp + tn, "upper"),
        "ai_caught_verdict_bearing": _rate(tp, tp + fn, "lower"),
        "abstain_real": _rate(cm["real"]["abstain"], n_real, "upper"),
        "abstain_ai": _rate(cm["ai"]["abstain"], n_ai, "upper"),
        "per_generator": {g: dict(c) for g, c in sorted(gens.items())},
    }
    m["rule"] = apply_rule(m)
    return m


def apply_rule(m: dict) -> dict:
    fp_ub = m["false_positive"]["upper_bound"]
    tp_lb = m["ai_caught"]["lower_bound"]
    fp_ok = fp_ub is not None and fp_ub <= FP_UPPER_MAX
    tp_ok = tp_lb is not None and tp_lb >= CATCH_LOWER_MIN
    big_enough = m["n_real"] >= MIN_REAL and m["n_ai"] >= MIN_AI
    return {"fp_upper_ok": fp_ok, "catch_lower_ok": tp_ok, "denominators_ok": big_enough,
            "passes": fp_ok and tp_ok}


def ordered_arms(arms) -> list[str]:
    known = [a for a in KNOWN_ARMS if a in arms]
    return known + sorted(a for a in arms if a not in KNOWN_ARMS)


def _iso(t: _dt.datetime) -> str:
    return t.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate(predictions: str, threshold: str, dev_split: str, test_split: str,
             run_id: str | None = None, date: str | None = None) -> dict:
    cfg = load_threshold(threshold)
    dev_sha, test_sha = sha256_file(dev_split), sha256_file(test_split)
    for which, path, got, want in (("dev", dev_split, dev_sha, cfg["dev_split_sha256"]),
                                   ("test", test_split, test_sha, cfg["test_split_sha256"])):
        if got != want:
            raise InputError(f"{which} split {path} has sha256 {got}, but the threshold "
                             f"was frozen with {want}")
    dev, test = load_split(dev_split, "dev"), load_split(test_split, "test")
    dev_n = Counter(it["label"] for it in dev)
    if dev_n["real"] < MIN_DEV_REAL or dev_n["ai"] < MIN_DEV_AI:
        raise InputError(f"dev split has {dev_n['real']} real and {dev_n['ai']} AI images; "
                         f"the threshold must be frozen on at least {MIN_DEV_REAL} + {MIN_DEV_AI}")
    coverage = hash_coverage(dev, test)
    check_hash_coverage(coverage)
    check_source_groups(dev, test)
    check_independence(dev, test, cfg["phash_distance"])
    labels = {it["item_id"]: it["label"] for it in test}
    rows = load_predictions(predictions, cfg, labels)

    frozen_at = cfg["_frozen_at"]
    first, last = min(r["_at"] for r in rows), max(r["_at"] for r in rows)
    if frozen_at >= first:
        raise InputError(f"frozen_at {_iso(frozen_at)} is not before the first test prediction "
                         f"({_iso(first)}): a threshold set after test scores exist is not frozen")
    now = _dt.datetime.now(_dt.timezone.utc)
    if last > now:
        raise InputError(f"a prediction is dated {_iso(last)}, which is in the future")

    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    required = ordered_arms(set(REQUIRED_ARMS) | set(cfg["gate_arms"]))
    pred_sha, thr_sha = sha256_file(predictions), sha256_file(threshold)
    run_id = run_id or "run-" + hashlib.sha256((pred_sha + thr_sha).encode()).hexdigest()[:12]
    date = date or now.date().isoformat()

    flagged = cfg["abstain"].get("real_abstain_counts_as") == "flagged"
    arms = {a: arm_metrics(by_arm[a], flagged) for a in ordered_arms(by_arm)}

    invalid, reasons, warnings = [], [], []
    for a in required:
        if a not in arms:
            invalid.append(f"{a}: required arm has no predictions")
    for a in arms:
        have = {r["item_id"] for r in by_arm[a]}
        lacking = [it["item_id"] for it in test if it["item_id"] not in have]
        if lacking:
            invalid.append(f"{a}: {len(lacking)} of the {len(test)} test.csv images have no "
                           f"prediction, e.g. {', '.join(lacking[:5])}")
    for kind in ("sha256", "phash"):
        if 0 < coverage[kind] < coverage["rows"]:
            warnings.append(f"{kind} is on {coverage[kind]} of {coverage['rows']} dev and test "
                            f"rows; rows without it are not checked by {kind}")
    comp = arms.get(ARM_COMPRESSED)
    if comp and comp["false_positive"]["upper_bound"] is not None \
            and comp["false_positive"]["upper_bound"] > DEGRADED_WARN_UPPER:
        warnings.append(f"{ARM_COMPRESSED}: false-positive upper bound "
                        f"{comp['false_positive']['upper_bound']:.2%} is above "
                        f"{DEGRADED_WARN_UPPER:.0%}, so the UI must say compressed or "
                        "reposted images are unreliable (DAT-837 7.5)")

    for a in (a for a in cfg["gate_arms"] if a in arms):
        rule = arms[a]["rule"]
        fp, tp = arms[a]["false_positive"], arms[a]["ai_caught"]
        if not rule["denominators_ok"]:
            invalid.append(f"{a}: {arms[a]['n_real']} real and {arms[a]['n_ai']} AI images, "
                           f"below the locked {MIN_REAL} real and {MIN_AI} AI")
        if not rule["fp_upper_ok"]:
            reasons.append(f"{a}: false-positive upper bound {fp['upper_bound']:.3%} "
                           f"is above {FP_UPPER_MAX:.0%}")
        if not rule["catch_lower_ok"]:
            reasons.append(f"{a}: AI-caught lower bound {tp['lower_bound']:.3%} "
                           f"is below {CATCH_LOWER_MIN:.0%}")
    if invalid:
        status = "INVALID"
    elif all(arms[a]["rule"]["passes"] for a in cfg["gate_arms"]):
        status = "PASS"
    else:
        status = "FAIL"

    return {
        "schema": RESULT_SCHEMA,
        "run_id": run_id,
        "date": date,
        "status": status,
        "reasons": invalid + reasons,
        "warnings": warnings,
        "gate": {
            "confidence_one_sided": CONFIDENCE,
            "method": "exact Clopper-Pearson",
            "fp_upper_max": FP_UPPER_MAX,
            "catch_lower_min": CATCH_LOWER_MIN,
            "min_real": MIN_REAL,
            "min_ai": MIN_AI,
            "gate_arms": cfg["gate_arms"],
            "required_arms": required,
        },
        "threshold": {"value": cfg["threshold"], "ai_if": cfg["ai_if"],
                      "abstain": cfg["abstain"], "rule": describe_rule(cfg),
                      "classifier": cfg["classifier"], "frozen_at": _iso(frozen_at),
                      "frozen_by": cfg["frozen_by"], "phash_distance": cfg["phash_distance"]},
        "predicted_at": {"first": _iso(first), "last": _iso(last)},
        "inputs": {
            "predictions": {"path": os.path.basename(predictions), "sha256": pred_sha},
            "threshold": {"path": os.path.basename(threshold), "sha256": thr_sha},
            "dev_split": {"path": os.path.basename(dev_split), "sha256": dev_sha},
            "test_split": {"path": os.path.basename(test_split), "sha256": test_sha},
        },
        "n_test_items": len(test),
        "assumptions": ASSUMPTIONS,
        "hash_coverage": coverage,
        "arms": arms,
    }


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.3%}"


def _line(rate: dict, bound_key: str, bound_name: str) -> str:
    if not rate["n"]:
        return "no items"
    return (f"observed {_pct(rate['observed'])} ({rate['k']}/{rate['n']}); "
            f"one-sided 95% {bound_name} bound {_pct(rate[bound_key])}")


def render_markdown(res: dict) -> str:
    g, thr = res["gate"], res["threshold"]
    out = [
        f"# Second Look gate result: {res['status']}",
        "",
        f"- Run id: `{res['run_id']}`",
        f"- Date: {res['date']}",
        f"- Classifier: {json.dumps(thr['classifier'], sort_keys=True)}",
        f"- Frozen rule: {thr['rule']}",
        f"- Frozen at {thr['frozen_at']} by {thr['frozen_by']}; test predictions "
        f"{res['predicted_at']['first']} to {res['predicted_at']['last']}",
        f"- Test items: {res['n_test_items']} images (test.csv), one per source group",
        f"- Hash coverage, dev and test rows: sha256 {res['hash_coverage']['sha256']} and "
        f"phash {res['hash_coverage']['phash']} of {res['hash_coverage']['rows']}",
        f"- Required arms: {', '.join(g['required_arms'])}",
        f"- Pass rule, on {', '.join(g['gate_arms'])}: false-positive upper bound at most "
        f"{g['fp_upper_max']:.0%} and AI-caught lower bound at least {g['catch_lower_min']:.0%} "
        f"({g['method']}, one-sided {g['confidence_one_sided']:.0%}), "
        f"with at least {g['min_real']} real and {g['min_ai']} AI images.",
        "- Every rate below is the observed rate with its bound. "
        "The bound, not the observed rate, decides the gate.",
        "",
        "## Sampling assumptions",
        "",
        f"- {res['assumptions']['statement']}",
        f"- {res['assumptions']['bounds_by_label']}",
        f"- The harness enforces: {'; '.join(res['assumptions']['enforced'])}.",
        f"- The harness does not check: {'; '.join(res['assumptions']['not_checked'])}. "
        f"See {res['assumptions']['see']}.",
        "",
    ]
    if res["reasons"]:
        out += ["## Why", ""] + [f"- {r}" for r in res["reasons"]] + [""]
    if res["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in res["warnings"]] + [""]
    for name, m in res["arms"].items():
        gate_arm = " (gate arm)" if name in g["gate_arms"] else " (reported, not gated)"
        rule = "meets" if m["rule"]["passes"] else "does not meet"
        abstain_note = ("counted as flagged" if m["real_abstain_counts_as"] == "flagged"
                        else "counted as not flagged")
        out += [
            f"## Arm: {name}{gate_arm}",
            "",
            f"{m['n_real']} real and {m['n_ai']} AI images. This arm {rule} the bound rule.",
            "",
            f"- False positives, all real images (abstentions {abstain_note}): "
            f"{_line(m['false_positive'], 'upper_bound', 'upper')}",
            f"- False positives, verdict-bearing real images: "
            f"{_line(m['false_positive_verdict_bearing'], 'upper_bound', 'upper')}",
            f"- AI images caught, all AI images: "
            f"{_line(m['ai_caught'], 'lower_bound', 'lower')}",
            f"- AI images caught, verdict-bearing AI images: "
            f"{_line(m['ai_caught_verdict_bearing'], 'lower_bound', 'lower')}",
            f"- Abstained: {m['abstain_real']['k']} real, {m['abstain_ai']['k']} AI",
            "",
            "| Label \\ verdict | ai | real | abstain |",
            "|---|---|---|---|",
        ]
        for label in ("real", "ai"):
            c = m["confusion"][label]
            out.append(f"| {label} | {c['ai']} | {c['real']} | {c['abstain']} |")
        if m["per_generator"]:
            out += ["", "| Generator | AI images | caught |", "|---|---|---|"]
            for gen, c in m["per_generator"].items():
                out.append(f"| {gen} | {c.get('n', 0)} | {c.get('caught', 0)} |")
        out.append("")
    i = res["inputs"]
    out += ["## Inputs", ""] + [f"- {k}: `{v['path']}` sha256 `{v['sha256']}`"
                                for k, v in i.items()] + [""]
    return "\n".join(out)


def write_result(res: dict, out_dir: str) -> None:
    """Write both files or neither. A stale result from an earlier run is removed
    first, both are written to temporary names, then moved into place with
    result.json last; on any failure every file this call made is removed."""
    texts = {"RESULT.md": render_markdown(res),
             "result.json": json.dumps(res, indent=2, sort_keys=True) + "\n"}
    os.makedirs(out_dir, exist_ok=True)
    final = {name: os.path.join(out_dir, name) for name in texts}
    made = []
    try:
        for name in ("result.json", "RESULT.md"):
            if os.path.lexists(final[name]):
                os.remove(final[name])
        for name, text in texts.items():
            tmp = final[name] + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                made.append(tmp)
                f.write(text)
        for name in texts:
            os.replace(final[name] + ".tmp", final[name])
            made.append(final[name])
    except BaseException:
        for path in made:
            with contextlib.suppress(OSError):
                os.remove(path)
        raise


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m gate.harness",
                                description="Score a Second Look gate run (offline).")
    p.add_argument("--predictions", required=True)
    p.add_argument("--threshold", required=True, help="frozen threshold JSON")
    p.add_argument("--dev-split", required=True, help="dev.csv the threshold was frozen on")
    p.add_argument("--test-split", required=True, help="test.csv from the same split")
    p.add_argument("--out", required=True, help="folder for RESULT.md and result.json")
    p.add_argument("--run-id")
    p.add_argument("--date", help="YYYY-MM-DD; default today (UTC)")
    a = p.parse_args(argv)
    try:
        res = evaluate(a.predictions, a.threshold, a.dev_split, a.test_split, a.run_id, a.date)
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        print(f"error: cannot read an input file: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except Exception:
        traceback.print_exc()
        print("error: the harness crashed; this is not a gate result", file=sys.stderr)
        return EXIT_CRASH
    try:
        write_result(res, a.out)
    except Exception as e:
        print(f"error: could not write the result to {a.out}: {e}; "
              "this is not a gate result", file=sys.stderr)
        return EXIT_CRASH
    print(f"{res['status']}: {os.path.join(a.out, 'RESULT.md')}")
    return EXIT[res["status"]]


if __name__ == "__main__":
    sys.exit(main())
