"""S7: the Day-3 gate run as one command (DAT-852). Offline, free, no network.

    python -m secondlook.gate_run --l4-dir <L4 folder> [--dry-run]

The steps, in order. Any refusal stops the run before a prediction is written (exit 2).
  1. Check the L4 LOCK.json, SCREEN-CONFIG.json and ACCEPTED.json hashes, the split files, the
     frozen threshold.json, the vendored recipe R1 code and every original image's sha256.
  2. Refuse unless threshold.json is committed (tracked, clean, committed in the past) before the
     first test prediction. Git order is the proof (the project lead ruling ec3ad415, item 2).
  3. Derive every arm from the ORIGINAL (one generation, L4 PROTOCOL.md part A, scripts/derive.py).
  4. Score the test images with the classifier rung in every arm; the ladder runs in full on
     `original` (its answers go to ladder-answers.csv, never into a gate verdict).
  5. Write predictions.csv and run the L2 harness (gate arms: original and format_matched).
  6. Run the PROTOCOL part G depth-3 file-facts baseline per arm.
  7. Write RESULTS.md with PROTOCOL line 105's allowed wording only. PASS, FAIL and INVALID are
     published as they stand.

Exit codes follow the harness: 0 PASS, 1 FAIL, 3 INVALID, 2 refused (bad or missing input,
nothing written), 4 crashed (any unexpected exception, never 1: 1 means the gate failed).
The floors (dev 100 + 100; gated-arm test 400 real + 200 AI, after attrition and the frozen reserves) are
checked before anything is derived or scored; a shortfall is published as INVALID (exit 3) with no predictions.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import datetime as dt
import importlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from gate import MIN_AI, MIN_DEV_AI, MIN_DEV_REAL, MIN_REAL, harness

from . import calibrate, gate_baseline, gate_report, l4_accepted, ladder
from .types import Result

REPO = Path(__file__).resolve().parent.parent
DERIVE_PY = REPO / "scripts" / "derive.py"
# Recipe R1 (L4 PROTOCOL.md part A) is code: the vendored copy must be L4's file at L4 7cb1175. Since da81be6, L4 d25c3a1
# split decode_rgb() out of derive() for its screening code; the output is byte-identical (pinned by
# test_split_recipe_output_is_byte_identical_to_da81be6), and da81be6's file hashed 47e21848...752d.
DERIVE_SHA256 = "53d6ad997d914b93cb617c45eeb9b2b79786a9af737064482e99872f220bf6b1"

DERIVED_ARMS = ("format_matched", "compressed_q70_1080", "size_matched_512")
ARMS = ("original",) + DERIVED_ARMS
DIAGNOSTIC_ARMS = ("size_matched_512",)               # reported only; neither gated nor required by the harness
GATE_ARMS = ["original", "format_matched"]          # ruling ec3ad415, item 1
PRED_COLUMNS = ("item_id", "arm", "verdict", "score", "predicted_at", "label")
LADDER_COLUMNS = ("item_id", "arm", "answer", "rungs_fired", "classifier_score", "version")
DERIVED_COLUMNS = ("item_id", "split", "arm", "sha256", "bytes", "width", "height", "resized")
ACCEPTED_SCHEMA = l4_accepted.NATIVE_SCHEMA            # the gate's own; L4's real one is l4_accepted.L4_SCHEMA
STUDIES = ("v1", "v2")                                # L4 PROTOCOL.md part K: v2 is v1's units plus a top-up of new real units
DEFAULT_THRESHOLD = REPO / "secondlook" / "rungs" / "classifier_threshold.json"
LABELS = ("real", "ai")

Refused = calibrate.Refused


class GateRunError(Refused):
    """The run cannot produce a complete, honest predictions file."""


class Invalid(Refused):
    """A floor is not met. Not a refusal of the inputs: the run is published as INVALID (exit 3), nothing derived."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# What the run imports, besides the classifier rung (which check_classifier loads and the weights check covers).
DEPENDENCIES = (("PIL.Image", "Pillow"), ("PIL.ImageCms", "Pillow"), ("PIL.ImageOps", "Pillow"),
                ("PIL.features", "Pillow"), ("sklearn.tree", "scikit-learn"))
# Frozen by L4 fetch.py, which locks SCREEN-CONFIG.json beside the six files calibrate.L4_LOCKED names.
GATE_L4_LOCKED = tuple(calibrate.L4_LOCKED) + ("SCREEN-CONFIG.json",)


class RefusedAfterWrite(Refused):
    """Refused after the run folder was filled: the files written so far stay where they are, for inspection."""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def sha256_file(path) -> str:
    return calibrate._sha(path)


def read_csv(path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, columns, rows) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns))
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ----------------------------------------------------------------------------- verdicts and scores

def verdict_from_score(score: float, cfg: dict) -> str:
    """Apply the frozen rule exactly as the harness will check it."""
    if cfg.get("ai_if") != "score>=threshold":
        raise GateRunError(f"unsupported ai_if rule: {cfg.get('ai_if')!r}")
    ab = cfg.get("abstain") or {}
    if ab.get("rule") not in ("none", "score_band"):
        raise GateRunError(f"unsupported abstain rule: {ab.get('rule')!r}")
    if ab.get("rule") == "score_band" and ab["low"] <= score < ab["high"]:
        return "abstain"
    return "ai" if score >= cfg["threshold"] else "real"


def classifier_score(result: Result) -> float:
    """The classifier rung's score for one image, or raise. Never invents a number."""
    scores = [e.raw["score"] for e in result.evidence
              if e.rung == "classifier" and isinstance(e.raw.get("score"), (int, float))
              and not isinstance(e.raw.get("score"), bool)]
    if len(scores) != 1:
        raise GateRunError(f"expected exactly one classifier score, got {len(scores)}")
    s = float(scores[0])
    if not math.isfinite(s):
        raise GateRunError(f"classifier score is not finite: {s}")
    return s


# ----------------------------------------------------------------------------- git order of the threshold

def threshold_commit(path: Path, now: dt.datetime | None = None) -> dict:
    """The commit that holds threshold.json, or Refused. Tracked, clean, committed in the past."""
    now = now or utcnow()
    path = Path(path).resolve()

    cwd = path.parent

    def git(*args):
        try:
            return subprocess.run(["git", "--no-optional-locks", "-C", str(cwd), *args],
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateRunError(f"git could not be run to check {path.name}: {exc}") from None

    top = git("rev-parse", "--show-toplevel")
    if top.returncode:
        raise GateRunError(f"{path} is not inside a git repository: threshold.json must be committed "
                           "before any test image is scored")
    cwd = Path(top.stdout.strip()).resolve()     # git reads a pathspec from where it runs, and rel is from the top
    rel = str(path.relative_to(cwd))
    if git("ls-files", "--error-unmatch", "--", rel).returncode:
        raise GateRunError(f"{rel} is not tracked by git: commit it before any test image is scored")
    st = git("status", "--porcelain", "--", rel)
    if st.returncode or st.stdout.strip():
        raise GateRunError(f"{rel} differs from its last commit ({st.stdout.strip()!r}): commit the frozen "
                           "file as it is before any test image is scored")
    lg = git("log", "-1", "--format=%H%n%cI", "--", rel)
    parts = lg.stdout.split()
    if lg.returncode or len(parts) != 2:
        raise GateRunError(f"{rel} has no commit in git history")
    sha, committed = parts
    committed_at = dt.datetime.fromisoformat(committed)
    if committed_at >= now:
        raise GateRunError(f"{rel} was committed at {committed}, not before now ({iso(now)}): the commit must "
                           "come before the first test prediction")
    return {"commit": sha, "committed_at": iso(committed_at), "path": rel}


# ----------------------------------------------------------------------------- plan: every read-only check

def _load_json(path: Path, what: str) -> dict:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{what} {path} is missing or not JSON ({exc})") from None
    if not isinstance(obj, dict):
        raise Refused(f"{what} {path} is not a JSON object")
    return obj


def planned_counts(l4_dir: Path) -> dict:
    """Primary units per (split, label) in the LOCKED manifests: what the fetch was meant to deliver."""
    out: Counter = Counter()
    for name, label in (("manifest-real.csv", "real"), ("manifest-ai.csv", "ai")):
        for r in read_csv(l4_dir / name):
            if r.get("role") == "primary":
                out[(r["split"], label)] += 1
    return out


def row_study(row: dict, what: str) -> str:
    """The study of a groups or manifest row. A folder with no `study` column is the original v1 layout: every row is v1."""
    st = row.get("study")
    if st is None:
        return "v1"
    if st not in STUDIES:
        raise Refused(f"{what}: study {st!r} is not one of {list(STUDIES)}")
    return st


def provisional_text(ctx: dict) -> str:
    """Why the accepted set is not final, or an empty string."""
    parts = []
    if ctx["accepted"].get("provisional"):
        parts.append("`complete` is not true in " + ", ".join(ctx["accepted"]["provisional"]))
    n = (ctx.get("reserve_check") or {}).get("awaiting_fetch", 0)
    if n:
        parts.append(f"{n} replacement(s) are still owed from reserves L4 had not fetched")
    return ("the accepted set is PROVISIONAL (" + "; ".join(parts) + ")") if parts else ""


class Plan:
    """The inputs of one run and every read-only check on them. Used by --dry-run and by the real run."""

    def __init__(self, a, rungs=None):
        self.a = a
        self.rungs = rungs                      # injected rungs (tests); None means the installed rungs
        l4 = Path(a.l4_dir)
        self.l4 = l4
        self.splits_dir = Path(a.splits_dir) if a.splits_dir else l4 / "splits"
        self.paths = {
            "dev": Path(a.dev_csv) if a.dev_csv else self.splits_dir / "dev.csv",
            "test": Path(a.test_csv) if a.test_csv else self.splits_dir / "test.csv",
            "split_json": Path(a.split_json) if a.split_json else self.splits_dir / "SPLIT.json",
            "accepted": Path(a.accepted[0]) if a.accepted else l4 / "ACCEPTED.json",
            "screen": Path(a.screen_config) if a.screen_config else l4 / "SCREEN-CONFIG.json",
            "ledger": Path(a.ledger) if a.ledger else l4 / "LEDGER.md",
            "threshold": Path(a.threshold) if a.threshold else DEFAULT_THRESHOLD,
        }
        self.derived_root = Path(a.derived_root) if a.derived_root else l4 / "images-derived"
        self.original_root = Path(a.original_root) if a.original_root else l4 / "images" / "original"
        self.run_id = a.run_id or "gate-" + utcnow().strftime("%Y%m%d-%H%M%S")
        self.out = Path(a.out) if a.out else l4 / "gate-runs" / self.run_id
        self.accepted_paths = [Path(p) for p in a.accepted] if a.accepted else [l4 / "ACCEPTED.json"]
        self.synthetic = bool(a.synthetic)
        self.hash_originals = not a.dry_run     # a dry run only checks that each original exists
        self.results_md = Path(a.results_md) if a.results_md else (
            self.out / "RESULTS.md" if self.synthetic else REPO / "docs" / "RESULTS.md")
        self.ctx: dict = {}
        self.invalid: Invalid | None = None     # set by a failed floor check

    # each check: (name, method). A check stores what later checks need in self.ctx.
    def steps(self):
        return [
            ("dependencies (Pillow, scikit-learn)", self.check_dependencies),
            ("L4 LOCK.json and the split files", self.check_lock),
            ("SCREEN-CONFIG.json", self.check_screen),
            ("ACCEPTED.json", self.check_accepted),
            ("accepted units equal the dev and test splits", self.check_units),
            ("replacements follow the frozen reserve order", self.check_reserves),
            ("floors: dev 100 + 100, gated-arm test 400 real + 200 AI", self.check_floors),
            ("frozen threshold.json", self.check_threshold),
            ("split files pass the harness input checks", self.check_split_inputs),
            ("threshold.json committed before any test prediction", self.check_committed),
            ("recipe R1 code (scripts/derive.py)", self.check_derive_code),
            ("original images", self.check_originals),
            ("classifier rung and weights", self.check_classifier),
            ("output folder", self.check_out),
        ]

    def run_checks(self, stop_on_fail=True) -> list[tuple[str, str | None]]:
        results = []
        failed = False
        for name, fn in self.steps():
            if failed:
                results.append((name, "not checked (an earlier check failed)"))
                continue
            try:
                fn()
                results.append((name, None))
            except Refused as exc:
                if isinstance(exc, Invalid):
                    self.invalid = exc
                results.append((name, str(exc)))
                failed = True
                if stop_on_fail:
                    raise
        return results

    # -- checks
    def check_dependencies(self):
        missing = []
        for mod, dist in DEPENDENCIES:
            try:
                importlib.import_module(mod)
            except Exception as exc:                      # a broken install raises more than ImportError
                missing.append(f"{mod} ({dist}: {type(exc).__name__}: {exc})")
        if missing:
            raise Refused("a package the run needs cannot be imported, so nothing was derived or scored: "
                          + "; ".join(missing) + ". Install the gate extra: "
                          "uv pip install -p .venv/bin/python -e '.[test,provenance,web,classifier,gate]'")

    def check_lock(self):
        # a v2 run (--v2-lock): the accepted files carry the v2 LOCK's sha256, which verify_l4 proves against this folder
        l4 = calibrate.verify_l4(str(self.l4), str(self.paths["split_json"]), self.a.v2_lock)
        locked = json.loads((self.l4 / "LOCK.json").read_text(encoding="utf-8"))["sha256"]
        absent = [n for n in GATE_L4_LOCKED if n not in locked]
        if absent:
            raise Refused(f"L4 LOCK.json does not lock {absent}: the gate run needs every file of "
                          f"{list(GATE_L4_LOCKED)} locked, or an edit after the lock would go unseen")
        for which in ("dev", "test"):
            got = sha256_file(self.paths[which]) if self.paths[which].is_file() else None
            if got != l4[f"{which}_sha"]:
                raise Refused(f"{self.paths[which]} has sha256 {got}, not the {which} sha256 "
                              f"{l4[which + '_sha']} in SPLIT.json")
        self.ctx["l4"] = l4
        self.ctx["locked"] = locked
        self.ctx["dev_rows"] = read_csv(self.paths["dev"])
        self.ctx["test_rows"] = read_csv(self.paths["test"])
        for which in ("dev", "test"):
            need = {"item_id", "label", "path", "sha256", "group"}
            rows = self.ctx[f"{which}_rows"]
            missing = need - set(rows[0]) if rows else need
            if missing:
                raise Refused(f"{self.paths[which].name} lacks columns {sorted(missing)}")
            labels = {r["label"] for r in rows}
            if not labels <= set(LABELS):
                raise Refused(f"{self.paths[which].name} has labels {sorted(labels)}, not real/ai")

    def check_screen(self):
        cfg = _load_json(self.paths["screen"], "SCREEN-CONFIG.json")
        d = l4_accepted.screen_phash_distance(cfg)
        want = ((self.ctx["l4"]["split"].get("params") or {}).get("phash_distance"))
        if isinstance(d, bool) or not isinstance(d, int) or d != want:
            raise Refused(f"SCREEN-CONFIG.json phash_distance {d!r} is not the split's {want!r}")
        self.ctx["screen_sha"] = sha256_file(self.paths["screen"])

    def check_accepted(self):
        """Reads the accepted file or files (the gate's own schema, or L4's real one: see l4_accepted), proves each is
        the one the lock, protocol and screening name, and has its sha256 in LEDGER.md. The sha256 of every file read
        is pinned in the run record."""
        acc = l4_accepted.load(self.accepted_paths, self.l4)
        # PROTOCOL.md is read from the LOCK's own entry (check_lock has just proved the file on disk matches it), so a
        # v2 accepted file that carries v1's PROTOCOL.md hash, or any other, is refused against the lock (PROTOCOL.md K9)
        want = {"protocol_sha256": self.ctx["locked"]["PROTOCOL.md"],
                "lock_sha256": self.ctx["l4"]["accepted_lock_sha"],
                "screen_config_sha256": self.ctx["screen_sha"]}
        try:
            ledger = self.paths["ledger"].read_text(encoding="utf-8")
        except OSError as exc:
            raise Refused(f"LEDGER.md {self.paths['ledger']} unreadable ({exc})") from None
        for f in acc["files"]:
            for key, value in want.items():
                if f.get(key) != value:
                    src = "the LOCK.json entry for PROTOCOL.md" if key == "protocol_sha256" else "the file it names"
                    if key == "lock_sha256" and self.a.v2_lock:
                        src = "the v2 LOCK (--v2-lock, verified against the gate folder)"
                    hint = ""
                    if key == "lock_sha256" and not self.a.v2_lock and f.get(key) == self.ctx["l4"].get("gate_v2_named"):
                        hint = " (it names the v2 LOCK the gate folder's LOCK.json lists: pass --v2-lock PATH)"
                    raise Refused(f"{f['name']} {key} is {f.get(key)!r}, but {src} has sha256 "
                                  f"{value}: the accepted set was not made under this protocol, lock and screening{hint}")
            if f["sha256"] not in ledger:
                raise Refused(f"the sha256 of {f['name']} ({f['sha256']}) is not recorded in LEDGER.md: PROTOCOL.md "
                              "part D requires it there before the detector is run")
        for key in ("units", "rejections"):
            if key not in acc:
                raise Refused(f"{acc['files'][0]['name']} has no {key!r}")
        self.ctx["accepted"] = acc
        self.ctx["accepted_files"] = [{k: f.get(k) for k in ("name", "sha256", "schema", "splits", "complete")}
                                      for f in acc["files"]]
        # one value for the report's single-file rows: the sha256 itself for one file, "name sha256" pairs for several
        files = self.ctx["accepted_files"]
        self.ctx["accepted_sha"] = files[0]["sha256"] if len(files) == 1 else "; ".join(
            f"{f['name']} {f['sha256']}" for f in files)

    def check_units(self):
        acc = self.ctx["accepted"]
        topics = {}
        for which in ("dev", "test"):
            units = (acc["units"] or {}).get(which)
            if not isinstance(units, list):
                raise Refused(f"ACCEPTED.json units.{which} is missing")
            by_id = {u.get("unit_id"): u for u in units}
            if len(by_id) != len(units):
                raise Refused(f"ACCEPTED.json units.{which} repeats a unit")
            rows = {r["item_id"]: r for r in self.ctx[f"{which}_rows"]}
            if set(by_id) != set(rows):
                extra, gone = sorted(set(by_id) - set(rows)), sorted(set(rows) - set(by_id))
                raise Refused(f"ACCEPTED.json and {which}.csv name different units: {len(gone)} only in the csv "
                              f"(e.g. {gone[:3]}), {len(extra)} only in ACCEPTED (e.g. {extra[:3]})")
            for uid, u in by_id.items():
                if u.get("label") != rows[uid]["label"] or u.get("sha256") != rows[uid]["sha256"]:
                    raise Refused(f"unit {uid}: ACCEPTED.json label or original sha256 differs from {which}.csv")
                topics[uid] = u.get("topic") or "(none)"
        self.ctx["topics"] = topics

    def check_reserves(self):
        """PROTOCOL.md part D: a rejected unit in split S and topic T is replaced by the unused reserve with
        spare_for = S, topic T and the lowest spare_rank; a reserve that is itself rejected is skipped in order;
        if none is left the count falls. Refuses a unit in neither manifest, a reserve out of order and a
        reserve nobody was owed. Reads groups-*.csv and manifest-*.csv, whose hashes the lock has just verified.

        v2 (PROTOCOL.md K5, K9): groups-real.csv and manifest-real.csv hold v1's rows and the top-up rows with a `study`
        column. A top-up spare replaces only a rejected top-up unit and a v1 reserve only a v1 unit, so the reserve queue
        and the owed count are kept per (split, topic, study): v1 and v2 reserves may share a spare_rank. With no
        `study` column every row is v1 and the check is the one it always was."""
        acc = self.ctx["accepted"]
        used = skipped = unfilled = unneeded = awaiting = 0
        pending = acc.get("unfetched") or set()
        by_study = {st: {"used": 0, "skipped": 0, "unfilled": 0} for st in STUDIES}
        v2_layout = any("study" in rows[0] for rows in (read_csv(self.l4 / f"groups-{lb}.csv") for lb in LABELS) if rows)
        for label in LABELS:
            groups, man = {}, {}
            for r in read_csv(self.l4 / f"groups-{label}.csv"):
                if r["unit_id"] in groups:
                    raise Refused(f"groups-{label}.csv lists unit {r['unit_id']} twice")
                groups[r["unit_id"]] = r
            for r in read_csv(self.l4 / f"manifest-{label}.csv"):
                if r.get("role") in ("primary", "reserve"):
                    if r["unit_id"] in man:
                        raise Refused(f"manifest-{label}.csv has two {man[r['unit_id']]['role']}/{r['role']} rows "
                                      f"for unit {r['unit_id']}: one image per unit")
                    man[r["unit_id"]] = r
            info = {}                                  # unit -> (target split, is_reserve, rank, topic)
            for uid, g in groups.items():
                is_res = g["split"] == "spare"
                m = man.get(uid)
                if m is None or m["role"] != ("reserve" if is_res else "primary") or m["split"] != g["split"] \
                        or m.get("spare_for", "") != g.get("spare_for", "") \
                        or m.get("spare_rank", "") != g.get("spare_rank", ""):
                    raise Refused(f"unit {uid}: groups-{label}.csv and manifest-{label}.csv disagree on its role, "
                                  "split, spare_for or spare_rank")
                try:
                    rank = int(g["spare_rank"]) if is_res else None
                except ValueError:
                    raise Refused(f"unit {uid}: spare_rank {g['spare_rank']!r} is not a whole number") from None
                study = row_study(g, f"unit {uid} in groups-{label}.csv")
                if study != row_study(m, f"unit {uid} in manifest-{label}.csv"):
                    raise Refused(f"unit {uid}: groups-{label}.csv and manifest-{label}.csv disagree on its study")
                if study == "v2" and label == "ai":
                    raise Refused(f"unit {uid}: groups-ai.csv marks an AI unit study v2, but v2 adds no AI unit "
                                  "(PROTOCOL.md K2)")
                info[uid] = (g["spare_for"] if is_res else g["split"], is_res, rank, g["topic"], study)
                if is_res and info[uid][0] not in ("dev", "test"):
                    raise Refused(f"reserve {uid}: spare_for {info[uid][0]!r} is not dev or test")
            accepted = {u["unit_id"]: (s, u) for s in ("dev", "test") for u in acc["units"][s] if u.get("label") == label}
            rejected = {}
            for r in acc["rejections"]:
                if r.get("label") == label:
                    if r.get("unit_id") in rejected:
                        raise Refused(f"ACCEPTED.json rejects unit {r.get('unit_id')} twice")
                    rejected[r.get("unit_id")] = r
            for uid in list(accepted) + list(rejected):
                if uid not in info:
                    raise Refused(f"unit {uid} ({label}) is in neither manifest-{label}.csv nor groups-{label}.csv "
                                  "(frozen and locked): only units the frozen manifests name can be accepted or rejected")
            for uid in set(accepted) & set(rejected):
                raise Refused(f"unit {uid} is both accepted and rejected in ACCEPTED.json")
            for uid, (split, u) in accepted.items():
                tgt, is_res, _, topic, study = info[uid]
                self._check_study(uid, u, study, v2_layout)
                if tgt != split or (u.get("role") == "reserve") != is_res:
                    raise Refused(f"unit {uid} is accepted into {split} as {u.get('role')!r}, but the frozen manifests "
                                  f"make it a {'reserve for ' if is_res else 'primary unit of '}{tgt}")
                if u.get("topic") not in (None, topic):
                    raise Refused(f"unit {uid}: ACCEPTED.json topic {u.get('topic')!r} is not the frozen topic {topic!r}")
            for uid, r in rejected.items():
                tgt, is_res, _, _, study = info[uid]
                self._check_study(uid, r, study, v2_layout)
                if r.get("split") not in ((tgt, "spare") if is_res else (tgt,)):
                    raise Refused(f"rejection of {uid} names split {r.get('split')!r}, but the frozen manifests put it "
                                  f"in {tgt}")
            for split in ("dev", "test"):
                for topic, study in sorted({(t, st) for (s, _, _, t, st) in info.values() if s == split}):
                    tag = f" / study {study}" if v2_layout else ""
                    owed = sum(1 for uid in rejected if info[uid][:2] == (split, False) and info[uid][3:] == (topic, study))
                    queue = sorted((info[uid][2], uid) for uid in info
                                   if info[uid][:2] == (split, True) and info[uid][3:] == (topic, study))
                    ranks = [r for r, _ in queue]
                    if len(set(ranks)) != len(ranks):
                        raise Refused(f"{label} reserves for {split} / {topic}{tag} share a spare_rank: the order is not frozen")
                    done = 0
                    stopped_at = None                    # the first reserve L4 had not fetched when the file was written
                    for pos, (rank, uid) in enumerate(queue):
                        touched = uid in accepted or uid in rejected
                        if stopped_at is not None:
                            if touched:
                                raise Refused(f"replacement out of order: {label} reserve {uid} (spare_rank {rank}, "
                                              f"{split} / {topic}{tag}) was {'accepted' if uid in accepted else 'rejected'}"
                                              f", but the lower-ranked reserve {stopped_at} had not been fetched")
                        elif done < owed:
                            if not touched:
                                if (label, uid) in pending:
                                    # L4's walk stops at a reserve not yet fetched ("no reserve is skipped unseen"): the
                                    # place is still owed, not lost. The file is provisional until it is fetched.
                                    stopped_at = uid
                                    continue
                                raise Refused(f"replacement out of order: {label} reserve {uid} (spare_rank {rank}, "
                                              f"{split} / {topic}{tag}) is next in the frozen order and was not used, "
                                              f"but {owed - done} replacement(s) were still owed")
                            if uid in accepted:
                                done += 1
                                used += 1
                                by_study[study]["used"] += 1
                            else:
                                skipped += 1
                                by_study[study]["skipped"] += 1
                        elif uid in rejected and uid not in accepted:
                            # L4's screen.py screens every reserve that was fetched, so a reserve past the replacements
                            # owed can be rejected (a C1 format check, say). A rejection adds no unit to the set, so it is
                            # counted and reported, not refused; an ACCEPTED reserve past the owed count still is.
                            unneeded += 1
                        elif touched:
                            why = ("no rejected primary unit there is owed a replacement" if not owed else
                                   f"the {owed} rejected primary unit(s) there were already replaced by lower-ranked "
                                   "reserves")
                            raise Refused(f"replacement out of order: {label} reserve {uid} (spare_rank {rank}, "
                                          f"{split} / {topic}{tag}) was {'accepted' if uid in accepted else 'rejected'}, "
                                          f"but {why}")
                    if stopped_at is not None:
                        awaiting += owed - done
                    else:
                        unfilled += owed - done
                        by_study[study]["unfilled"] += owed - done
        self.ctx["reserve_check"] = {"used": used, "skipped": skipped, "unfilled": unfilled, "rejected_unneeded": unneeded,
                                     "awaiting_fetch": awaiting}
        if v2_layout:
            self.ctx["reserve_check_by_study"] = by_study

    @staticmethod
    def _check_study(uid: str, entry: dict, frozen: str, v2_layout: bool) -> None:
        """An accepted unit or a rejection names the study the frozen manifests put the unit in (v2 files must say it)."""
        got = entry.get("study")
        if got is None and v2_layout:
            raise Refused(f"unit {uid}: ACCEPTED.json does not say which study it is from, but the frozen manifests "
                          "hold v1 and v2 units: every unit and rejection needs a `study`")
        if got is not None and got != frozen:
            raise Refused(f"unit {uid}: ACCEPTED.json marks it study {got!r}, but the frozen manifests make it "
                          f"study {frozen!r}")

    def check_floors(self):
        """Before anything is derived or scored: the floors on the accepted units, after attrition and reserves. A
        PROVISIONAL accepted set that clears them is refused here (see the end of this check)."""
        dev = Counter(r["label"] for r in self.ctx["dev_rows"])
        test = Counter(u["label"] for u in self.ctx["accepted"]["units"]["test"])
        reasons = []
        if dev["real"] < MIN_DEV_REAL or dev["ai"] < MIN_DEV_AI:
            reasons.append(f"dev split: {dev['real']} real and {dev['ai']} AI accepted units, below the floor of "
                           f"{MIN_DEV_REAL} real and {MIN_DEV_AI} AI the threshold must be frozen on")
        if test["real"] < MIN_REAL or test["ai"] < MIN_AI:
            for arm in GATE_ARMS:
                reasons.append(f"{arm}: {test['real']} real and {test['ai']} AI accepted test units, below the "
                               f"locked {MIN_REAL} real and {MIN_AI} AI")
        why = provisional_text(self.ctx)
        if reasons:
            if why:
                reasons.append(f"note: {why}; a count of a split that is not finished may still change, a floor missed "
                               "on a finished split cannot")
            raise Invalid(reasons)
        # Past the floors: a PASS or FAIL needs the final accepted set. A provisional one lacks the units of a split not
        # yet screened, or its reserves, so a verdict is never read from part of the set.
        if why:
            raise Refused(why + ": the run needs the accepted file L4 writes from a screening of dev and test together, "
                          "with every reserve a replacement is owed fetched and screened")

    def check_threshold(self):
        path = self.paths["threshold"]
        try:
            cfg = harness.load_threshold(str(path))
        except (harness.InputError, OSError) as exc:
            raise Refused(f"threshold file {path}: {exc}") from None
        l4 = self.ctx["l4"]
        if cfg["dev_split_sha256"] != l4["dev_sha"] or cfg["test_split_sha256"] != l4["test_sha"]:
            raise Refused("threshold.json was frozen against other dev or test split files than these")
        if sorted(cfg["gate_arms"]) != sorted(GATE_ARMS):
            raise Refused(f"threshold.json gate_arms is {cfg['gate_arms']}, not {GATE_ARMS} (ruling ec3ad415 item 1)")
        stmt = cfg.get("training_data_statement")
        if not isinstance(stmt, str) or not stmt.strip():
            raise Refused("threshold.json has no training_data_statement (PROTOCOL.md F: the detector's training "
                          "data, including known overlap with Commons or DiffusionDB; without it the run is not accepted)")
        if cfg.get("l4_lock_sha256") not in (None, l4["lock_sha"]):
            raise Refused("threshold.json l4_lock_sha256 is not this LOCK.json's sha256")
        if cfg.get("v2_lock_sha256") not in (None, l4.get("v2_lock_sha")):
            raise Refused("threshold.json v2_lock_sha256 is not the sha256 of the v2 LOCK this run verified"
                          + ("" if l4.get("v2_lock_sha") else " (no --v2-lock was given)"))
        if cfg["phash_distance"] != (l4["split"].get("params") or {}).get("phash_distance"):
            raise Refused("threshold.json phash_distance differs from the split's")
        cfg["_sha256"] = sha256_file(path)
        self.ctx["threshold"] = cfg

    def check_split_inputs(self):
        """The harness's own input checks (hash coverage, source groups, independence), run now so that a problem
        stops the run before anything is derived or scored, not after predictions exist."""
        try:
            dev = harness.load_split(str(self.paths["dev"]), "dev")
            test = harness.load_split(str(self.paths["test"]), "test")
            harness.check_hash_coverage(harness.hash_coverage(dev, test))
            harness.check_source_groups(dev, test)
            harness.check_independence(dev, test, self.ctx["threshold"]["phash_distance"])
        except harness.InputError as exc:
            raise Refused(f"the L2 harness would refuse these split files: {exc}") from None

    def check_committed(self):
        self.ctx["threshold_commit"] = threshold_commit(self.paths["threshold"])

    def check_derive_code(self):
        got = sha256_file(DERIVE_PY) if DERIVE_PY.is_file() else None
        if got != DERIVE_SHA256:
            raise Refused(f"{DERIVE_PY} has sha256 {got}, not the frozen L4 recipe's {DERIVE_SHA256}")

    def original_path(self, row: dict) -> Path:
        p = Path(row["path"])
        return p if p.is_absolute() else self.original_root / p

    def check_originals(self):
        total = found = 0
        for which in ("dev", "test"):
            for r in self.ctx[f"{which}_rows"]:
                p = self.original_path(r)
                if not p.is_file():
                    raise Refused(f"{which} original {r['item_id']} not found at {p}")
                found += 1
                total += p.stat().st_size
                if self.hash_originals and sha256_file(p) != r["sha256"]:
                    raise Refused(f"{r['item_id']}: {p} is not the file {which}.csv names (sha256 differs)")
                if self.derived_root in p.parents:
                    raise Refused(f"{p} is inside the derived folder: arms are made from originals only")
        self.ctx["original_bytes"], self.ctx["original_files"] = total, found

    def check_classifier(self):
        """Fail now, not after deriving thousands of files: the rung must load and its weights must be the frozen ones."""
        rungs = require_classifier(self.rungs)
        if self.rungs is not None:
            return
        from .rungs import classifier
        w = classifier.weights_path()
        if not w.is_file():
            raise Refused(f"classifier weights not found at {w}: run python -m secondlook.rungs.classifier --fetch")
        want = self.ctx["threshold"]["classifier"]["weights_sha256"]
        if want != classifier.MODEL_SHA256 or sha256_file(w) != want:
            raise Refused(f"the weights at {w} are not the ones threshold.json was frozen for ({want})")
        self.ctx["rungs"] = rungs

    def check_out(self):
        if self.out.exists() and any(self.out.iterdir()):
            raise Refused(f"{self.out} already holds files: use a new --out for each run")
        if self.results_md.exists() and self.results_md.is_dir():
            raise Refused(f"{self.results_md} is a folder")


# ----------------------------------------------------------------------------- dry run

def dry_run(plan: Plan) -> int:
    results = plan.run_checks(stop_on_fail=False)
    ctx = plan.ctx
    dev, test = ctx.get("dev_rows"), ctx.get("test_rows")
    out = ["Second Look gate run: DRY RUN. Nothing is derived, scored or written.", "", "Steps:"]
    for i, (name, _) in enumerate(plan.steps(), 1):
        out.append(f"  check {i}. {name}")
    out += ["  then: derive every arm from the originals; score the test images in every arm; write "
            "predictions.csv; run the L2 harness; run the part G baseline per arm; write RESULTS.md", ""]
    out.append(f"Arms: {', '.join(ARMS)} (gated: {', '.join(GATE_ARMS)}; the others are reported)")
    if dev is not None and test is not None:
        c = {w: Counter(r["label"] for r in rows) for w, rows in (("dev", dev), ("test", test))}
        n_img = len(dev) + len(test)
        out += ["", "Sizes:",
                f"  dev:  {c['dev']['real']} real + {c['dev']['ai']} AI = {len(dev)} units",
                f"  test: {c['test']['real']} real + {c['test']['ai']} AI = {len(test)} units",
                f"  derived files to make (dev and test, each from its original): {n_img} x {len(DERIVED_ARMS)} arms = "
                f"{n_img * len(DERIVED_ARMS)}",
                f"  test images scored: {len(test)} x {len(ARMS)} arms = {len(test) * len(ARMS)} classifier passes "
                f"(predictions.csv rows)"]
        if "original_bytes" in ctx:
            out.append(f"  originals on disk: {ctx['original_files']} files, {ctx['original_bytes'] / 1e9:.2f} GB "
                       "(sha256 of each is checked in a real run)")
        out.append("  derived arms: about 0.5 GB for dev + test (L4 PROTOCOL.md B4 estimate, not a measurement)")
    if ctx.get("accepted_files"):
        out += ["", "Accepted files read (sha256 is pinned in the run record):"]
        for f in ctx["accepted_files"]:
            out.append(f"  {f['name']}  {f['sha256']}  schema {f['schema']}  splits {','.join(f['splits'] or [])}  "
                       f"complete {f['complete']}")
    free = shutil.disk_usage(plan.out.parent if plan.out.parent.exists() else plan.l4).free
    out += [f"  free disk: {free / 2**30:.1f} GiB", "",
            f"Outputs: {plan.out}/ (predictions.csv, ladder-answers.csv, baseline.json, hashes-derived.csv, "
            f"derive-env.json, gate-run.json, RESULT.md, result.json, RESULTS.md), and {plan.results_md}",
            f"Derived images: {plan.derived_root}/<arm>/<id>.jpg", "", "Checks:"]
    bad = 0
    invalid = plan.invalid is not None
    for name, problem in results:
        tag = "ok" if problem is None else ("INVALID" if invalid and problem == str(plan.invalid) else "REFUSED")
        out.append(f"  [{tag}] {name}" + (f": {problem}" if problem else ""))
        bad += problem is not None
    out.append("")
    if invalid:
        out.append("The floors are not met: a real run would publish INVALID with no predictions (exit 3).")
    else:
        out.append("All checks passed: a real run would start." if not bad else
                   f"{bad} check(s) would stop a real run (exit 2).")
    print("\n".join(out))
    return 3 if invalid else (0 if not bad else 2)


# ----------------------------------------------------------------------------- derive

def load_derive():
    spec = importlib.util.spec_from_file_location("l4_derive", DERIVE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def derive_all(plan: Plan, arms=DERIVED_ARMS) -> dict:
    """Make every derived file from its ORIGINAL; check any file already on disk is byte-identical.
    `arms`: the derived arms to make (default all of them); gate_heldout passes the three arms PROTOCOL I6 names.

    Returns {"files": {(arm, item_id): Path}, "records": [...], "facts": {(split, arm): [feature rows]},
    "env": {...}, "failures": [...]}. A file that cannot be derived in the original, format_matched or
    compressed arm stops the run: no row is dropped. In a diagnostic arm (size_matched_512) the failure is recorded
    as that arm's coverage and attrition and the unit stays in the gated population (the project lead c014e175 B3).
    """
    mod = load_derive()
    files, records, failures = {}, [], []
    facts: dict = {}
    for split in ("dev", "test"):
        rows = plan.ctx[f"{split}_rows"]
        for n, r in enumerate(rows, 1):
            src = plan.original_path(r)
            data = src.read_bytes()
            src_facts = gate_baseline.file_facts(src)
            facts.setdefault((split, "original"), []).append(
                {"item_id": r["item_id"], "label": r["label"], **src_facts})
            for arm in arms:
                try:
                    out, rec = mod.derive(data, arm)
                except Exception as exc:
                    if arm in DIAGNOSTIC_ARMS:
                        failures.append({"item_id": r["item_id"], "split": split, "arm": arm,
                                         "label": r["label"], "reason": f"{type(exc).__name__}: {exc}"})
                        continue
                    raise GateRunError(f"{r['item_id']} [{arm}]: cannot derive: {exc}") from None
                dest = plan.derived_root / arm / f"{r['item_id']}.jpg"
                if dest.exists():
                    if dest.read_bytes() != out:
                        raise GateRunError(f"{dest} exists but is not what recipe R1 makes from the original: "
                                           "refusing to overwrite or use it")
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_name(dest.name + ".part")
                    tmp.write_bytes(out)
                    tmp.replace(dest)
                files[(arm, r["item_id"])] = dest
                facts.setdefault((split, arm), []).append(
                    {"item_id": r["item_id"], "label": r["label"], **gate_baseline.file_facts(dest, src_facts["source_jpeg_quality"])})
                records.append({"item_id": r["item_id"], "split": split, "arm": arm, "sha256": rec["out_sha256"],
                                "bytes": len(out), "width": rec["out_size"][0], "height": rec["out_size"][1],
                                "resized": int(rec["resized"])})
            if n % 100 == 0:
                log(f"derived {split} {n}/{len(rows)}")
    return {"files": files, "records": records, "facts": facts, "env": mod.env(), "failures": failures}


# ----------------------------------------------------------------------------- score

def arm_rungs(arm: str, rungs):
    """The full ladder on `original`; the classifier rung alone in a derived arm (its metadata is gone)."""
    if arm == "original":
        return rungs
    return [(n, c) for n, c in rungs if n == "classifier"]


def require_classifier(rungs) -> list:
    """The rung list to use, refused unless it holds the classifier rung: no test image can be scored without it."""
    rungs = list(rungs if rungs is not None else _default_rungs())
    if not any(n == "classifier" for n, _ in rungs):
        raise GateRunError("the classifier rung is not available (missing weights, torch or timm): "
                           "no test image can be scored")
    return rungs


def _default_rungs():
    from . import rungs as registry
    return registry.load()


@contextlib.contextmanager
def frozen_threshold_env(path: Path):
    """Let the classifier rung's own above_threshold flag follow the frozen file, for the ladder answers."""
    old = os.environ.get("SECONDLOOK_CLASSIFIER_THRESHOLD")
    os.environ["SECONDLOOK_CLASSIFIER_THRESHOLD"] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("SECONDLOOK_CLASSIFIER_THRESHOLD", None)
        else:
            os.environ["SECONDLOOK_CLASSIFIER_THRESHOLD"] = old


def score_test(plan: Plan, derived: dict, rungs=None, now=None, arms=ARMS) -> tuple[list[dict], list[dict]]:
    """(predictions, ladder answers). Raises before returning anything partial. `arms`: the arms to score (default all)."""
    cfg = plan.ctx["threshold"]
    rungs = require_classifier(rungs)
    now = now or (lambda: iso(utcnow()))
    preds, answers = [], []
    test = plan.ctx["test_rows"]
    # Last look at the git order, immediately before the first test image is scored: the same file, the same commit.
    if sha256_file(plan.paths["threshold"]) != cfg["_sha256"]:
        raise GateRunError("threshold.json changed since the checks: refusing to score a test image")
    now_commit = threshold_commit(plan.paths["threshold"])
    if now_commit["commit"] != plan.ctx["threshold_commit"]["commit"]:
        raise GateRunError("threshold.json has a new commit since the checks: refusing to score a test image")
    for arm in arms:
        use = arm_rungs(arm, rungs)
        for n, row in enumerate(test, 1):
            if arm != "original" and (arm, row["item_id"]) not in derived["files"]:
                continue                                  # a diagnostic arm's recorded failure: not scored, not guessed
            path = plan.original_path(row) if arm == "original" else derived["files"][(arm, row["item_id"])]
            result = ladder.run(str(path), rungs=use)
            try:
                score = classifier_score(result)
            except GateRunError as exc:
                raise GateRunError(f"{row['item_id']} [{arm}]: {exc}") from None
            preds.append({"item_id": row["item_id"], "arm": arm, "verdict": verdict_from_score(score, cfg),
                          "score": repr(score), "predicted_at": now(), "label": row["label"]})
            if arm == "original":
                answers.append({"item_id": row["item_id"], "arm": arm, "answer": result.answer,
                                "rungs_fired": "+".join(sorted({e.rung for e in result.evidence})),
                                "classifier_score": repr(score), "version": result.version})
            if n % 100 == 0:
                log(f"scored {arm} {n}/{len(test)}")
    return preds, answers


# ----------------------------------------------------------------------------- the run

def secondlook_version() -> str:
    try:
        out = subprocess.run(["git", "--no-optional-locks", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def v2_provenance(ctx) -> dict:
    """What a v2 run adds to its record: the v2 LOCK it verified (sha256, the gate_view and context files it was tied by)."""
    v2 = ctx["l4"].get("v2_lock")
    return {} if not v2 else {"v2_lock_sha256": v2["sha256"], "v2_lock_gate_view": v2["gate_view"],
                              "v2_lock_context": v2["context"]}


def file_base_provenance(ctx) -> dict:
    """The folder the split's originals were read from (gate_splits --file-base), as SPLIT.json recorded it; None for a
    SPLIT.json written before it was recorded."""
    return {"file_base": (ctx["l4"].get("split") or {}).get("file_base")}


def publish_invalid(plan: Plan, exc: Invalid, started) -> int:
    """A floor is not met: publish RESULTS.md as INVALID with the counts and reasons, exit 3, write no predictions."""
    plan.check_out()
    ctx = plan.ctx
    provenance = {"run_id": plan.run_id, "started_at": iso(started), "finished_at": iso(utcnow()),
                  "secondlook_commit": secondlook_version(), "l4_lock_sha256": ctx["l4"]["lock_sha"],
                  **v2_provenance(ctx), **file_base_provenance(ctx),
                  "screen_config_sha256": ctx["screen_sha"], "accepted_sha256": ctx["accepted_sha"],
                  "accepted_files": ctx["accepted_files"], "gate_arms": GATE_ARMS}
    md = gate_report.render_invalid(plan, exc.reasons, planned_counts(plan.l4), harness.EXIT["INVALID"], provenance,
                                   {"dev": (MIN_DEV_REAL, MIN_DEV_AI), "test": (MIN_REAL, MIN_AI)})
    plan.out.mkdir(parents=True, exist_ok=True)
    write_json(plan.out / "gate-run.json", {**provenance, "status": "INVALID", "reasons": exc.reasons,
                                             "harness_exit_code": harness.EXIT["INVALID"], "synthetic": plan.synthetic,
                                             "predictions_written": False})
    for target in {plan.out / "RESULTS.md", plan.results_md}:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(target)
    print(f"INVALID: {plan.out}/RESULTS.md, {plan.results_md} (no image derived or scored, no predictions written)")
    return harness.EXIT["INVALID"]


def run(a, rungs=None) -> int:
    plan = Plan(a, rungs=rungs)
    started = utcnow()
    try:
        plan.run_checks(stop_on_fail=True)
    except Invalid as exc:
        return publish_invalid(plan, exc, started)
    ctx = plan.ctx
    log(f"checks passed; run {plan.run_id}")
    rungs = require_classifier(rungs)
    derived = derive_all(plan)
    log(f"derived {len(derived['files'])} files")
    with frozen_threshold_env(plan.paths["threshold"]):
        preds, answers = score_test(plan, derived, rungs=rungs)
    log(f"scored {len(preds)} predictions")

    baseline = {}
    for arm in ARMS:
        baseline[arm] = gate_baseline.fit_and_score(derived["facts"][("dev", arm)], derived["facts"][("test", arm)])

    out = plan.out
    out.mkdir(parents=True, exist_ok=True)
    n_test = len(ctx["test_rows"])
    covered = Counter(r["arm"] for r in preds)
    partial_arms = sorted(a for a in ARMS if covered[a] < n_test)
    if any(a not in DIAGNOSTIC_ARMS for a in partial_arms):
        raise GateRunError(f"a gated or required arm covers fewer than {n_test} test units: {partial_arms}")
    # The harness needs every arm to cover every test unit, so a partly covered diagnostic arm is kept out of its
    # input and reported from the same predictions (predictions-partial-arms.csv) with its coverage.
    write_csv(out / "predictions.csv", PRED_COLUMNS, [r for r in preds if r["arm"] not in partial_arms])
    if partial_arms:
        write_csv(out / "predictions-partial-arms.csv", PRED_COLUMNS, [r for r in preds if r["arm"] in partial_arms])
    write_json(out / "derive-failures.json", derived["failures"])
    write_csv(out / "ladder-answers.csv", LADDER_COLUMNS, answers)
    write_csv(out / "hashes-derived.csv", DERIVED_COLUMNS, derived["records"])
    write_json(out / "derive-env.json", derived["env"])
    write_json(out / "baseline.json", baseline)

    code = harness.main(["--predictions", str(out / "predictions.csv"), "--threshold", str(plan.paths["threshold"]),
                         "--dev-split", str(plan.paths["dev"]), "--test-split", str(plan.paths["test"]),
                         "--out", str(out), "--run-id", plan.run_id])
    if code not in harness.EXIT.values():
        if code != harness.EXIT_BAD_INPUT:
            raise RuntimeError(f"the L2 harness crashed (exit code {code}); predictions.csv is in {out}")
        raise RefusedAfterWrite(f"the L2 harness refused its inputs (exit code {code}) and wrote no RESULT.md. "
                                f"predictions.csv and the other files are in {out}")
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))

    provenance = {
        "run_id": plan.run_id, "started_at": iso(started), "finished_at": iso(utcnow()), "harness_exit_code": code,
        "secondlook_commit": secondlook_version(), "derive_py_sha256": DERIVE_SHA256, "derive_env": derived["env"],
        "l4_lock_sha256": ctx["l4"]["lock_sha"], **v2_provenance(ctx),
        **file_base_provenance(ctx), "screen_config_sha256": ctx["screen_sha"],
        "accepted_sha256": ctx["accepted_sha"], "accepted_files": ctx["accepted_files"],
        "split_json_sha256": ctx["l4"]["split_json_sha"],
        "threshold_sha256": ctx["threshold"]["_sha256"], "threshold_commit": ctx["threshold_commit"],
        "predictions_sha256": sha256_file(out / "predictions.csv"),
        "synthetic": plan.synthetic,
    }
    write_json(out / "gate-run.json", provenance)
    flagged = plan.ctx["threshold"]["abstain"].get("real_abstain_counts_as") == "flagged"
    partial = {a: harness.arm_metrics([r for r in preds if r["arm"] == a], flagged) for a in partial_arms}
    md = gate_report.render(result=result, preds=preds, answers=answers, baseline=baseline, plan=plan,
                            provenance=provenance, planned=planned_counts(plan.l4), exit_code=code,
                            failures=derived["failures"], partial=partial)
    for target in {out / "RESULTS.md", plan.results_md}:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(md, encoding="utf-8")
        tmp.replace(target)
    print(f"{result['status']}: {out}/RESULT.md, {plan.results_md}")
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m secondlook.gate_run", description=__doc__.split("\n")[0])
    p.add_argument("--l4-dir", required=True, help="the L4 test-set folder (LOCK.json, ACCEPTED.json, SCREEN-CONFIG.json, LEDGER.md)")
    p.add_argument("--threshold", help=f"frozen threshold.json (default {DEFAULT_THRESHOLD})")
    p.add_argument("--splits-dir", help="folder with dev.csv, test.csv and SPLIT.json (default <l4>/splits)")
    p.add_argument("--dev-csv")
    p.add_argument("--test-csv")
    p.add_argument("--split-json")
    p.add_argument("--accepted", action="append", metavar="FILE",
                   help="an accepted file; repeat it for L4's one-file-per-split output (default <l4>/ACCEPTED.json)")
    p.add_argument("--v2-lock", metavar="PATH", help=calibrate.V2_LOCK_HELP)
    p.add_argument("--screen-config")
    p.add_argument("--ledger")
    p.add_argument("--original-root", help="folder for relative `path` values (default <l4>/images/original)")
    p.add_argument("--derived-root", help="where the arms are written (default <l4>/images-derived)")
    p.add_argument("--out", help="a new folder for this run (default <l4>/gate-runs/<run-id>)")
    p.add_argument("--run-id")
    p.add_argument("--results-md", help="where RESULTS.md goes (default docs/RESULTS.md in this repo)")
    p.add_argument("--synthetic", action="store_true", help="a test fixture run: banner, no real dataset wording")
    p.add_argument("--dry-run", action="store_true", help="print the sizes, steps and checks; do nothing else")
    return p


def main(argv=None, rungs=None) -> int:
    a = build_parser().parse_args(argv)
    if a.dry_run:
        try:
            return dry_run(Plan(a, rungs=rungs))
        except Refused as exc:
            print(f"gate_run: refused: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"gate_run: crashed: {exc!r}", file=sys.stderr)
            return 4
    try:
        return run(a, rungs=rungs)
    except RefusedAfterWrite as exc:
        print(f"gate_run: refused after files were written: {exc}", file=sys.stderr)
        return 2
    except Refused as exc:
        print(f"gate_run: refused, no predictions written: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                       # 1 means the gate FAILED; a crash must never look like that
        print(f"gate_run: crashed: {exc!r}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
