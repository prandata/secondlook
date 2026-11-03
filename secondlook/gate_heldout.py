"""S7: the held-out diagnostic arm `heldout_cf_publiceval` (L4 PROTOCOL.md part I6 to I8, DAT-852). Offline, free.

    python -m secondlook.gate_heldout --l4-dir <L4 test-set folder> --heldout <L4>/heldout-v2/ACCEPTED-heldout.json \\
        --threshold <frozen threshold.json> --v2-lock <L4>/LOCK.json --gate-run <the main v2 gate run folder> \\
        --dev-csv <splits>/dev.csv --original-root <L4>/images --out <new folder> [--dry-run]

Reported, never gated. It reads the gate's own code: scripts/derive.py through gate_run.derive_all (I6, the same derive
arms, recipe unchanged), the classifier rung through gate_run.score_test, the frozen threshold through gate_run's
threshold_commit. It adds only what I6 to I8 need: the pins, the held-out checks, the catch rate and its exact
one-sided 95% Clopper-Pearson lower bound per arm and per generator, and the I8 wording.

Any refusal stops the run before a prediction is written (exit 2). Nothing is re-picked, dropped or replaced: an
image whose sha256 differs from the manifest, or that cannot be derived, stops the run. Exit codes: 0 done (there is
no PASS or FAIL: the arm is not gated), 2 refused, 4 crashed.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from gate import harness
from gate.stats import lower_bound

from . import calibrate, gate_baseline, gate_report, gate_run
from .gate_run import Refused, iso, log, read_csv, sha256_file, utcnow, write_csv, write_json

HELDOUT_ARM = "heldout_cf_publiceval"
ACCEPTED_SCHEMA = "second-look-l4-heldout-accepted/1"
LOCK_SCHEMA = "second-look-heldout-lock/1"
HELDOUT_DERIVED = ("format_matched", "compressed_q70_1080")      # I6: the derive arms of the gated run, no diagnostic arm
HELDOUT_ARMS = ("original",) + HELDOUT_DERIVED
PRED_COLUMNS = gate_run.PRED_COLUMNS + ("generator",)
LOCK_FILES = ("candidates.csv", "publiceval-meta.csv", "manifest-heldout.csv", "heldout-provenance.csv",
              "ACCEPTED-heldout.json")                          # I6: what LOCK-heldout.json records
# Pre-registered in PROTOCOL.md I1 and I2 (fixed before any held-out image was fetched).
PREREGISTERED = {"candidates.csv": "76bd065c363ba65b85264f180f5f1cb04417278cab096b4bf7dda14fe56c23e0",
                 "publiceval-meta.csv": "38affc79de507dea11813090d5caeb91dcaec7e074a948d7e031cfc527541d3b"}
PARQUET_LFS_SHA256 = {
    "HFCF_PublicEval_0.parquet": "7a166249a52a476f3e3afcf0501a7722e5a0cec7c2bfc7f828166a1ba9d997f0",
    "HFCF_PublicEval_1.parquet": "5cfbded1555f726ebb4ec6704bb6644a7bdad8642afb672c454a6735eaa95e97",
    "HFCF_PublicEval_2.parquet": "eb051630683028618718d4f5d9a5450360248df39e5c42e865e5d71b7777b88c",
    "HFCF_PublicEval_3.parquet": "9e1b1ecfd2ba3d6446a1802e62dee8893113db0415b4e5322a7b0686b64482ea",
    "HFCF_PublicEval_4.parquet": "f8d710e4ee5a1598c0f2216d03c8270a176940cd926893064e8e4e9cdaed7fdd",
    "HFCF_PublicEval_5.parquet": "b9670124dbedc7268d6b78eebc8ad32eae7ea3e4a7ad45f74ab98097de401255",
}
CONFIDENCE = 0.95

# I8, word for word. The sentence is filled in with the counts; nothing else is added to it.
ALLOWED = ("Held-out diagnostic, reported, not gated: on {n_real} unit-level real photographs (curated Wikimedia "
           "Commons, pre-2022) and {m} images from {g} generators held out of the detector's training by its authors "
           "(their public evaluation set, Community Forensics PublicEval, CC BY 4.0), observed false-alarm rate "
           "{fa_k}/{n_real}, one-sided 95% upper bound {fa_ub}; observed catch rate {a}/{m}, lower bound {lb}; "
           "arm: {arm}. About {per_gen} images per generator; per-generator figures are descriptive.")
LIMIT_LINE = ("These generators and images were chosen and made by the detector's authors for their evaluation set, "
              "not collected from the web by us. Sizes run from 224 to 2,048 px and formats differ by generator "
              "(Firefly JPEG, the rest PNG), unlike the real side; the format_matched and compressed arms reduce "
              "the format difference only.")
AUTHORS_PHRASE = "held out of the detector's training by its authors"
BANNED = ("detects unseen generators", "generalises", "generalizes", "detects midjourney", "detects ai images",
          "works on meta ai", "passed the gate", "failed the gate", "gate: pass", "gate: fail")
PROVENANCE_RUNGS = ("c2pa", "exif_rung", "iptc_rung")


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.3%}"


def lower(k: int, n: int) -> float | None:
    return lower_bound(k, n, CONFIDENCE) if n else None


def _sha_field(value) -> bool:
    return calibrate._is_sha256(value)


def _load_json(path: Path, what: str) -> dict:
    return gate_run._load_json(path, what)


# ----------------------------------------------------------------------------- the plan: every read-only check

class HeldoutPlan:
    """The inputs of one held-out run and every read-only check on them (used by --dry-run and by the run).

    Each check is (name, method, names of the checks it needs). A dry run reports every check whose needs are met; a
    run stops at the first refusal."""

    # reused from gate_run.Plan unchanged: none of them reads more than the attributes set in __init__
    check_dependencies = gate_run.Plan.check_dependencies
    check_derive_code = gate_run.Plan.check_derive_code
    check_classifier = gate_run.Plan.check_classifier
    check_out = gate_run.Plan.check_out
    original_path = gate_run.Plan.original_path

    def __init__(self, a, rungs=None):
        self.a = a
        self.rungs = rungs
        self.l4 = Path(a.l4_dir)
        self.heldout_path = Path(a.heldout)
        self.heldout_dir = self.heldout_path.parent
        self.lock_heldout_path = Path(a.lock_heldout) if a.lock_heldout else self.heldout_dir / "LOCK-heldout.json"
        self.v2_lock_path = Path(a.v2_lock)
        self.paths = {"threshold": Path(a.threshold)}
        splits = Path(a.splits_dir) if a.splits_dir else self.l4 / "splits"
        self.dev_csv = Path(a.dev_csv) if a.dev_csv else splits / "dev.csv"
        self.original_root = Path(a.original_root) if a.original_root else self.l4 / "images"
        self.run_id = a.run_id or "heldout-" + utcnow().strftime("%Y%m%d-%H%M%S")
        self.out = Path(a.out) if a.out else self.l4 / "heldout-runs" / self.run_id
        self.derived_root = self.out / "derived"
        self.results_md = self.out / "RESULTS-heldout.md"      # check_out refuses a folder here
        self.gate_run_dir = Path(a.gate_run) if a.gate_run else None
        self.ctx: dict = {}

    def steps(self):
        return [
            ("dependencies (Pillow, scikit-learn)", self.check_dependencies, ()),
            ("v2 LOCK", self.check_v2_lock, ()),
            ("ACCEPTED-heldout.json", self.check_accepted, ("v2 LOCK",)),
            ("LOCK-heldout.json", self.check_lock_heldout, ("ACCEPTED-heldout.json",)),
            ("manifest-heldout.csv and the held-out images", self.check_images, ("ACCEPTED-heldout.json",)),
            ("frozen threshold.json", self.check_threshold, ("v2 LOCK",)),
            ("dev split for the shortcut baseline", self.check_dev, ("frozen threshold.json",)),
            ("main gate run (the false-alarm figure is quoted from it)", self.check_gate_run, ("frozen threshold.json",)),
            ("recipe R1 code (scripts/derive.py)", self.check_derive_code, ()),
            ("classifier rung and weights", self.check_classifier, ("frozen threshold.json",)),
            ("output folder", self.check_out, ()),
        ]

    def run_checks(self, stop_on_fail=True) -> list[tuple[str, str | None]]:
        results, failed = [], set()
        for name, fn, needs in self.steps():
            blocked = [n for n in needs if n in failed]
            if blocked:
                results.append((name, f"not checked (needs: {', '.join(blocked)})"))
                failed.add(name)
                continue
            try:
                fn()
                results.append((name, None))
            except Refused as exc:
                results.append((name, str(exc)))
                failed.add(name)
                if stop_on_fail:
                    raise
        return results

    # -- checks
    def check_v2_lock(self):
        p = self.v2_lock_path
        if not p.is_file():
            raise Refused(f"--v2-lock {p} is not a file")
        doc = _load_json(p, "the v2 LOCK")
        table = doc.get("sha256")
        if not isinstance(table, dict) or not table:
            raise Refused(f"the v2 LOCK {p} has no `sha256` table")
        self.ctx["v2_lock_sha"] = sha256_file(p)
        self.ctx["v2_lock_table"] = table

    def check_accepted(self):
        p = self.heldout_path
        if not p.is_file():
            raise Refused(f"held-out accepted file {p} not found")
        acc = _load_json(p, "ACCEPTED-heldout.json")
        if acc.get("schema") != ACCEPTED_SCHEMA or acc.get("arm") != HELDOUT_ARM:
            raise Refused(f"{p.name} is not {ACCEPTED_SCHEMA} for arm {HELDOUT_ARM} "
                          f"(schema {acc.get('schema')!r}, arm {acc.get('arm')!r})")
        if acc.get("lock_sha256") != self.ctx["v2_lock_sha"]:
            raise Refused(f"{p.name} was made under the lock with sha256 {acc.get('lock_sha256')}, but --v2-lock has "
                          f"sha256 {self.ctx['v2_lock_sha']}")
        # the locked files it names must be the v2 LOCK's own entries, and the ones on disk in the L4 folder
        locked = acc.get("locked_files_sha256")
        if not isinstance(locked, dict) or not locked:
            raise Refused(f"{p.name} has no locked_files_sha256")
        for name, want in locked.items():
            if self.ctx["v2_lock_table"].get(name) != want:
                raise Refused(f"{p.name} names {name} with sha256 {want}, but the v2 LOCK holds "
                              f"{self.ctx['v2_lock_table'].get(name)}")
            f = self.l4 / name
            if not f.is_file() or sha256_file(f) != want:
                raise Refused(f"{f} is missing or no longer has the sha256 {want} that the v2 LOCK locks")
        for key, name in (("protocol_sha256", "PROTOCOL.md"), ("screen_config_sha256", "SCREEN-CONFIG.json")):
            if acc.get(key) != locked.get(name):
                raise Refused(f"{p.name} {key} {acc.get(key)!r} is not the locked {name} ({locked.get(name)})")
        if acc.get("derive_script_sha256") != gate_run.DERIVE_SHA256:
            raise Refused(f"{p.name} was screened with derive.py sha256 {acc.get('derive_script_sha256')}, not the "
                          f"frozen recipe's {gate_run.DERIVE_SHA256}")
        for key, name in (("manifest_heldout_csv_sha256", "manifest-heldout.csv"),
                          ("candidates_csv_sha256", "candidates.csv")):
            f = self.heldout_dir / name
            if not f.is_file() or sha256_file(f) != acc.get(key):
                raise Refused(f"{f} is missing or is not the file {p.name} names ({key} {acc.get(key)})")
        if acc["candidates_csv_sha256"] != PREREGISTERED["candidates.csv"]:
            raise Refused("candidates.csv is not the list pre-registered in PROTOCOL.md I2 "
                          f"({PREREGISTERED['candidates.csv']})")
        entries = acc.get("accepted")
        if not isinstance(entries, list) or not entries:
            raise Refused(f"{p.name} accepts no image")
        if acc.get("n_accepted") != len(entries):
            raise Refused(f"{p.name} says n_accepted {acc.get('n_accepted')!r} but lists {len(entries)} images")
        ids = [e.get("id") for e in entries]
        if len(set(ids)) != len(ids):
            raise Refused(f"{p.name} lists an image twice")
        for e in entries:
            for key in ("id", "file", "generator"):
                if not isinstance(e.get(key), str) or not e[key]:
                    raise Refused(f"{p.name}: entry {e.get('id')!r} has no {key}")
            if not _sha_field(e.get("sha256")):
                raise Refused(f"{p.name}: entry {e['id']} has no sha256")
        self.ctx["accepted"] = acc
        self.ctx["accepted_sha"] = sha256_file(p)
        self.ctx["entries"] = entries

    def check_lock_heldout(self):
        """I6: before any score, LOCK-heldout.json records the sha256 of five files and the LFS sha256 of six parquet
        files. The five are re-hashed here; the six cannot be (only parts of them were read) and must equal the ones
        pre-registered in PROTOCOL.md I1."""
        p = self.lock_heldout_path
        if not p.is_file():
            raise Refused(f"{p} not found: PROTOCOL.md I6 requires LOCK-heldout.json to be written before any held-out "
                          "image is scored")
        doc = _load_json(p, "LOCK-heldout.json")
        files, parquet = doc.get("sha256"), doc.get("parquet_lfs_sha256")
        if not isinstance(files, dict) or not isinstance(parquet, dict):
            raise Refused(f"{p.name} needs `sha256` (the five files) and `parquet_lfs_sha256` (the six parquet files)")
        # The accepted file is the one --heldout names: under D3 (ERRATA-v2.md) that is ACCEPTED-heldout-D3.json, and
        # the lock must pin it, not the D1' ACCEPTED-heldout.json beside it.
        for name in LOCK_FILES[:-1] + (self.heldout_path.name,):
            f = self.heldout_dir / name
            if files.get(name) != (sha256_file(f) if f.is_file() else None):
                raise Refused(f"{p.name} does not record the current sha256 of {name} "
                              f"(it holds {files.get(name)!r}): the file changed after the lock, or was never locked")
            if name in PREREGISTERED and files[name] != PREREGISTERED[name]:
                raise Refused(f"{name} is not the file pre-registered in PROTOCOL.md I2 ({PREREGISTERED[name]})")
        if parquet != PARQUET_LFS_SHA256:
            raise Refused(f"{p.name} parquet_lfs_sha256 differs from the six pre-registered in PROTOCOL.md I1")
        self.ctx["lock_heldout_sha"] = sha256_file(p)

    def check_images(self):
        """Every accepted image is in manifest-heldout.csv with the same sha256 and generator, and the file on disk has
        it. Never a re-pick: any difference stops the run."""
        manifest = {r["id"]: r for r in read_csv(self.heldout_dir / "manifest-heldout.csv")}
        rows, nbytes = [], 0
        for e in self.ctx["entries"]:
            m = manifest.get(e["id"])
            if m is None:
                raise Refused(f"image {e['id']} is not in manifest-heldout.csv")
            if m["sha256"] != e["sha256"] or m["generator"] != e["generator"] or m["path"] != e["file"]:
                raise Refused(f"image {e['id']}: ACCEPTED-heldout.json and manifest-heldout.csv disagree on its "
                              "sha256, generator or file")
            rel = Path(e["file"])
            if rel.is_absolute() or ".." in rel.parts:
                raise Refused(f"image {e['id']}: file {e['file']!r} is not inside the L4 folder")
            p = self.l4 / rel
            if not p.is_file():
                raise Refused(f"image {e['id']} not found at {p}")
            if sha256_file(p) != e["sha256"]:
                raise Refused(f"image {e['id']}: {p} is not the file the manifest names (sha256 differs)")
            nbytes += p.stat().st_size
            rows.append({"item_id": e["id"], "label": "ai", "path": str(p), "sha256": e["sha256"],
                         "generator": e["generator"]})
        self.ctx["rows"], self.ctx["image_bytes"] = rows, nbytes

    def check_threshold(self):
        path = self.paths["threshold"]
        if not path.is_file():
            raise Refused(f"no frozen threshold at {path}: the dev calibration has not frozen one, and no held-out "
                          "image is scored without it (PROTOCOL.md I6)")
        try:
            cfg = harness.load_threshold(str(path))
        except (harness.InputError, OSError) as exc:
            raise Refused(f"threshold file {path}: {exc}") from None
        if sorted(cfg["gate_arms"]) != sorted(gate_run.GATE_ARMS):
            raise Refused(f"threshold.json gate_arms is {cfg['gate_arms']}, not {gate_run.GATE_ARMS}")
        stmt = cfg.get("training_data_statement")
        if not isinstance(stmt, str) or not stmt.strip():
            raise Refused("threshold.json has no training_data_statement (PROTOCOL.md F)")
        if cfg.get("v2_lock_sha256") != self.ctx["v2_lock_sha"]:
            raise Refused(f"threshold.json v2_lock_sha256 is {cfg.get('v2_lock_sha256')!r}, but --v2-lock has sha256 "
                          f"{self.ctx['v2_lock_sha']}: the threshold was not frozen under this v2 LOCK")
        cfg["_sha256"] = sha256_file(path)
        self.ctx["threshold"] = cfg
        # "frozen" is git order: tracked, clean, committed before now (the same proof gate_run uses)
        self.ctx["threshold_commit"] = gate_run.threshold_commit(path)

    def check_dev(self):
        """The part G baseline tree is fitted on the dev units the threshold was frozen against."""
        p = self.dev_csv
        if not p.is_file():
            raise Refused(f"dev split {p} not found (--dev-csv)")
        if sha256_file(p) != self.ctx["threshold"]["dev_split_sha256"]:
            raise Refused(f"{p} is not the dev split threshold.json was frozen against (sha256 differs)")
        rows = read_csv(p)
        need = {"item_id", "label", "path", "sha256"}
        if not rows or need - set(rows[0]):
            raise Refused(f"{p.name} lacks columns {sorted(need - set(rows[0] if rows else []))}")
        for r in rows:
            f = self.original_path(r)
            if not f.is_file():
                raise Refused(f"dev original {r['item_id']} not found at {f}")
            if sha256_file(f) != r["sha256"]:
                raise Refused(f"dev original {r['item_id']}: {f} is not the file dev.csv names (sha256 differs)")
        self.ctx["dev_rows"] = rows

    def check_gate_run(self):
        """The false-alarm figure beside the catch rate is the main run's, not recomputed (I6)."""
        d = self.gate_run_dir
        if d is None:
            raise Refused("--gate-run is required: the I8 sentence quotes the main run's false-alarm figure")
        rec, res = d / "gate-run.json", d / "result.json"
        if not rec.is_file() or not res.is_file():
            raise Refused(f"{d} holds no gate-run.json and result.json: the main v2 gate run has not been made")
        record, result = _load_json(rec, "gate-run.json"), _load_json(res, "result.json")
        if record.get("threshold_sha256") != self.ctx["threshold"]["_sha256"]:
            raise Refused("the main gate run used another threshold.json than this one (threshold_sha256 differs)")
        if record.get("v2_lock_sha256") != self.ctx["v2_lock_sha"]:
            raise Refused("the main gate run was not made under this v2 LOCK (v2_lock_sha256 differs)")
        fa = {}
        for arm in HELDOUT_ARMS:
            rate = ((result.get("arms") or {}).get(arm) or {}).get("false_positive")
            if not isinstance(rate, dict) or not isinstance(rate.get("k"), int) or not isinstance(rate.get("n"), int) \
                    or not isinstance(rate.get("upper_bound"), (int, float)) or rate["n"] <= 0:
                raise Refused(f"the main gate run's result.json has no false-alarm figure for arm {arm}")
            fa[arm] = {k: rate[k] for k in ("k", "n", "upper_bound")}
        self.ctx["main_false_alarm"] = fa
        self.ctx["main_run"] = {"gate_run_json_sha256": sha256_file(rec), "result_json_sha256": sha256_file(res),
                                "run_id": record.get("run_id")}


# ----------------------------------------------------------------------------- scoring and counting

class _DerivePlan:
    """What gate_run.derive_all and gate_run.score_test read from a Plan: dev rows (for the baseline), the held-out rows
    as the 'test' rows, and the threshold's context."""

    def __init__(self, plan: HeldoutPlan):
        self.ctx = {"dev_rows": plan.ctx["dev_rows"], "test_rows": plan.ctx["rows"],
                    "threshold": plan.ctx["threshold"], "threshold_commit": plan.ctx["threshold_commit"]}
        self.paths = plan.paths
        self.derived_root = plan.derived_root
        self.original_path = plan.original_path


def catch_counts(preds: list[dict], generators: dict) -> dict:
    """Per arm: caught / n overall and per generator. A catch is verdict `ai`; an abstention is never a catch."""
    out = {}
    for arm in HELDOUT_ARMS:
        rows = [r for r in preds if r["arm"] == arm]
        per = defaultdict(lambda: [0, 0])
        for r in rows:
            g = generators[r["item_id"]]
            per[g][0] += r["verdict"] == "ai"
            per[g][1] += 1
        out[arm] = {"a": sum(r["verdict"] == "ai" for r in rows), "n": len(rows),
                    "per_generator": {g: {"a": a, "n": n} for g, (a, n) in sorted(per.items())}}
    return out


def baseline_recall(dev_facts: list[dict], held_facts: list[dict]) -> dict:
    """Part G's frozen tree, fitted on the DiffusionDB dev units, and its AI calls on the held-out files."""
    fit = gate_baseline.fit_and_score(dev_facts, [{**f, "label": "ai"} for f in held_facts])
    return {"a": fit["true_positive"], "n": fit["n_ai"], "model": fit["model"],
            "features_used_by_tree": fit["features_used_by_tree"], "sklearn_version": fit["sklearn_version"],
            "n_dev_fit": fit["n_dev_fit"]}


def provenance_by_generator(path: Path, ids: set) -> dict:
    """Provenance hits per generator over the accepted images (I5: recorded, not a filter)."""
    per = defaultdict(Counter)
    for r in read_csv(path):
        if r["id"] not in ids:
            continue
        c = per[r["generator"]]
        c["n"] += 1
        c["rung_hit"] += any(r.get(k, "none") not in ("none", "", "[]") for k in PROVENANCE_RUNGS) \
            or r.get("watermark_present") == "True"
        c["c2pa"] += r.get("c2pa", "none") not in ("none", "")
        c["exif_rung"] += r.get("exif_rung", "none") not in ("none", "")
        c["iptc_rung"] += r.get("iptc_rung", "none") not in ("none", "")
        c["watermark"] += r.get("watermark_present") == "True"
        c["any_metadata"] += r.get("all_empty") == "False"
    return {g: dict(c) for g, c in sorted(per.items())}


# ----------------------------------------------------------------------------- RESULTS-heldout.md

def per_generator_counts(entries: list[dict]) -> dict:
    return dict(sorted(Counter(e["generator"] for e in entries).items()))


def render(acc: dict, entries: list[dict], counts: dict, baseline: dict, main_fa: dict, prov: dict,
           provenance: dict) -> str:
    gens = per_generator_counts(entries)
    m = len(entries)
    per_gen = round(m / len(gens))
    L = ["# Diagnostic arm `heldout_cf_publiceval`: results (reported, not gated)", "",
         "This arm never changes the gate result, the threshold, the floors or the accepted population of the gated run "
         "(L4 PROTOCOL.md part I). Every figure below is for one arm on its own; nothing is added up across arms or "
         "with the gated arms. The image is the unit: the set records no prompt, user or source to group by.", ""]
    L += ["## The run", "", "| Item | Value |", "|---|---|"]
    for k, v in provenance.items():
        L.append(f"| {k} | {v} |")
    L.append("")
    for arm in HELDOUT_ARMS:
        c, fa, b = counts[arm], main_fa[arm], baseline[arm]
        L += [f"## Arm `{arm}`", "", f"{gate_report.ARM_TITLE[arm]}", "",
              ALLOWED.format(n_real=fa["n"], m=c["n"], g=len(gens), fa_k=fa["k"], fa_ub=pct(fa["upper_bound"]),
                             a=c["a"], lb=pct(lower(c["a"], c["n"])), arm=arm, per_gen=per_gen), "",
              f"The false-alarm figure is the main run's, quoted from its `result.json`, not recomputed here. "
              f"The catch rate counts a verdict of `ai`; an abstention is never a catch. Bound: exact one-sided "
              f"{CONFIDENCE:.0%} Clopper-Pearson lower bound on the catch rate.", "",
              "| Generator | Caught / n | Catch rate | One-sided 95% lower bound |", "|---|---|---|---|"]
        for g, v in c["per_generator"].items():
            L.append(f"| {g} | {v['a']}/{v['n']} | {pct(v['a'] / v['n'])} | {pct(lower(v['a'], v['n']))} |")
        L += ["", "Per-generator rows are descriptive: about "
              f"{per_gen} images each.", "", LIMIT_LINE, "",
              f"File-facts shortcut baseline (part G, tree fitted on DiffusionDB dev; it reads no pixels), AI calls on "
              f"these files in `{arm}`: {b['a']}/{b['n']} ({pct(b['a'] / b['n'])}), one-sided 95% lower bound "
              f"{pct(lower(b['a'], b['n']))}. Model: {b['model']}, fitted on {b['n_dev_fit']} dev units, features used "
              f"by the tree: {', '.join(b['features_used_by_tree']) or 'none'}.", ""]
    L += ["## Attrition per generator", "",
          "Screening is the one in PROTOCOL.md I4 (C1, C3, C4), run before any score. Candidates are the first 34 rows "
          "per generator in I2 order.", "",
          "| Generator | Candidates | Cleared screening | Rejected | Rejected by | Accepted into the arm |",
          "|---|---|---|---|---|---|"]
    for g, v in sorted(acc["per_generator"].items()):
        by = ", ".join(f"{k} {n}" for k, n in sorted((v.get("rejected_by_check") or {}).items())) or "none"
        L.append(f"| {g} | {v['candidates']} | {v['passed']} | {v['rejected']} | {by} | {gens.get(g, 0)} |")
    total = sum(v["candidates"] for v in acc["per_generator"].values())
    L += ["", f"Accepted: {m} of {total} candidates; rejected in screening: {len(acc['rejected'])}.", "",
          "## Provenance hits per generator (accepted images)", "",
          "PROTOCOL.md I5: recorded, not a filter. A file with a hit stays in the arm; the classifier reads pixels only "
          "and the derived arms strip metadata again. The `watermark` rung reads only Second Look's own Video Seal mark, "
          "not any vendor's watermark.", "",
          "| Generator | Images | Any rung hit | C2PA | EXIF rung | IPTC rung | Video Seal mark | Any file metadata |",
          "|---|---|---|---|---|---|---|---|"]
    for g, c in prov.items():
        L.append(f"| {g} | {c['n']} | {c['rung_hit']} | {c['c2pa']} | {c['exif_rung']} | {c['iptc_rung']} | "
                 f"{c['watermark']} | {c['any_metadata']} |")
    L += ["", "## What this does not say", "",
          "It does not say how the detector does on any generator outside these 18, on any named generator, or on any "
          "service the product might meet. It has no same-prompt comparison and no per-topic table: the files record no "
          "prompt and no topic. See `docs/TRAINING-DATA.md` for the detector's training data.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------- dry run and run

def dry_run(plan: HeldoutPlan) -> int:
    results = plan.run_checks(stop_on_fail=False)
    ctx = plan.ctx
    out = ["Second Look held-out arm: DRY RUN. Nothing is derived, scored or written.", "",
           f"Arm: {HELDOUT_ARM} (reported, not gated). Arms: {', '.join(HELDOUT_ARMS)}"]
    if "rows" in ctx:
        gens = Counter(r["generator"] for r in ctx["rows"])
        out += [f"Images: {len(ctx['rows'])} from {len(gens)} generators, {ctx['image_bytes'] / 1e6:.0f} MB, sha256 of "
                f"each checked against manifest-heldout.csv and ACCEPTED-heldout.json",
                f"Classifier passes a real run makes: {len(ctx['rows'])} x {len(HELDOUT_ARMS)} arms = "
                f"{len(ctx['rows']) * len(HELDOUT_ARMS)}"]
    if "accepted_sha" in ctx:
        out.append(f"ACCEPTED-heldout.json sha256 {ctx['accepted_sha']}")
    if "v2_lock_sha" in ctx:
        out.append(f"v2 LOCK sha256 {ctx['v2_lock_sha']}")
    out += ["", "Checks:"]
    bad = skipped = 0
    for name, problem in results:
        tag = "ok" if problem is None else ("not checked" if problem.startswith("not checked") else "REFUSED")
        out.append(f"  [{tag}] {name}" + (f": {problem}" if problem else ""))
        bad += tag == "REFUSED"
        skipped += tag == "not checked"
    out += ["", "All checks passed: a real run would start." if not (bad or skipped) else
            f"{bad} check(s) refused and {skipped} not checked (they need a refused one): a real run would stop "
            "(exit 2). No image was scored."]
    print("\n".join(out))
    return 0 if not bad else 2


def run(a, rungs=None) -> int:
    plan = HeldoutPlan(a, rungs=rungs)
    started = utcnow()
    plan.run_checks(stop_on_fail=True)
    ctx = plan.ctx
    log(f"checks passed; run {plan.run_id}")
    rungs = gate_run.require_classifier(rungs)
    classifier_only = [(n, c) for n, c in rungs if n == "classifier"]
    dp = _DerivePlan(plan)
    derived = gate_run.derive_all(dp, arms=HELDOUT_DERIVED)
    log(f"derived {len(derived['files'])} files")
    with gate_run.frozen_threshold_env(plan.paths["threshold"]):
        preds, _ = gate_run.score_test(dp, derived, rungs=classifier_only, arms=HELDOUT_ARMS)
    generators = {r["item_id"]: r["generator"] for r in ctx["rows"]}
    covered = Counter(r["arm"] for r in preds)
    short = {arm: covered[arm] for arm in HELDOUT_ARMS if covered[arm] != len(ctx["rows"])}
    if short:
        raise gate_run.GateRunError(f"an arm scored fewer than {len(ctx['rows'])} images: {short}")
    for r in preds:
        r["generator"] = generators[r["item_id"]]
    counts = catch_counts(preds, generators)
    baseline = {arm: baseline_recall(derived["facts"][("dev", arm)], derived["facts"][("test", arm)])
                for arm in HELDOUT_ARMS}
    prov = provenance_by_generator(plan.heldout_dir / "heldout-provenance.csv", set(generators))

    out = plan.out
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "predictions-heldout.csv", PRED_COLUMNS, preds)
    write_csv(out / "hashes-derived.csv", gate_run.DERIVED_COLUMNS, derived["records"])
    write_json(out / "derive-env.json", derived["env"])
    write_json(out / "baseline.json", baseline)
    write_json(out / "counts.json", {"counts": counts, "main_false_alarm": ctx["main_false_alarm"]})
    record = {
        "arm": HELDOUT_ARM, "gated": False, "run_id": plan.run_id, "started_at": iso(started),
        "finished_at": iso(utcnow()), "secondlook_commit": gate_run.secondlook_version(),
        "n_images": len(ctx["rows"]), "arms": list(HELDOUT_ARMS),
        "pins": {"heldout_accepted_sha256": ctx["accepted_sha"], "lock_heldout_sha256": ctx["lock_heldout_sha"],
                 "threshold_sha256": ctx["threshold"]["_sha256"], "threshold_commit": ctx["threshold_commit"],
                 "derive_py_sha256": gate_run.DERIVE_SHA256,
                 "classifier_weights_sha256": ctx["threshold"]["classifier"]["weights_sha256"],
                 "v2_lock_sha256": ctx["v2_lock_sha"], "dev_csv_sha256": ctx["threshold"]["dev_split_sha256"]},
        "main_gate_run": ctx["main_run"], "derive_env": derived["env"],
        "predictions_sha256": sha256_file(out / "predictions-heldout.csv"),
    }
    write_json(out / "heldout-run.json", record)
    pins = record["pins"]
    table = {"Run id": plan.run_id, "Started": iso(started), "Second Look commit": record["secondlook_commit"],
             "ACCEPTED-heldout.json sha256": pins["heldout_accepted_sha256"],
             "LOCK-heldout.json sha256": pins["lock_heldout_sha256"],
             "v2 LOCK sha256": pins["v2_lock_sha256"], "threshold.json sha256": pins["threshold_sha256"],
             "threshold.json commit": pins["threshold_commit"]["commit"],
             "derive.py sha256": pins["derive_py_sha256"],
             "classifier weights sha256": pins["classifier_weights_sha256"],
             "predictions-heldout.csv sha256": record["predictions_sha256"],
             "Main gate run quoted for false alarms": ctx["main_run"]["run_id"]}
    md = render(ctx["accepted"], ctx["entries"], counts, baseline, ctx["main_false_alarm"], prov, table)
    lowered = md.lower()
    for phrase in BANNED:
        if phrase in lowered:
            raise gate_run.GateRunError(f"the report holds banned wording {phrase!r} (PROTOCOL.md I8): not written")
    tmp = plan.results_md.with_name(plan.results_md.name + ".tmp")
    tmp.write_text(md, encoding="utf-8")
    tmp.replace(plan.results_md)
    print(f"done: {plan.results_md} (reported, not gated; there is no PASS or FAIL)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m secondlook.gate_heldout", description=__doc__.split("\n")[0])
    p.add_argument("--l4-dir", required=True, help="the L4 test-set folder (the images are read from it)")
    p.add_argument("--heldout", required=True, help="ACCEPTED-heldout.json")
    p.add_argument("--threshold", required=True, help="the frozen threshold.json (committed before this run)")
    p.add_argument("--v2-lock", required=True, metavar="PATH", help="L4's v2 LOCK.json")
    p.add_argument("--gate-run", help="the main v2 gate run folder (gate-run.json, result.json): the false-alarm figure "
                                      "is quoted from it, not recomputed")
    p.add_argument("--lock-heldout", help="LOCK-heldout.json (default: beside --heldout)")
    p.add_argument("--splits-dir", help="folder with dev.csv (default <l4>/splits)")
    p.add_argument("--dev-csv", help="the dev split the threshold was frozen against (baseline fit)")
    p.add_argument("--original-root", help="folder for the dev split's relative `path` values (default <l4>/images)")
    p.add_argument("--out", help="a new folder for this run (default <l4>/heldout-runs/<run-id>)")
    p.add_argument("--run-id")
    p.add_argument("--dry-run", action="store_true", help="read and check everything, score nothing, write nothing")
    return p


def main(argv=None, rungs=None) -> int:
    a = build_parser().parse_args(argv)
    if a.dry_run:
        try:
            return dry_run(HeldoutPlan(a, rungs=rungs))
        except Refused as exc:
            print(f"gate_heldout: refused: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"gate_heldout: crashed: {exc!r}", file=sys.stderr)
            return 4
    try:
        return run(a, rungs=rungs)
    except Refused as exc:
        print(f"gate_heldout: refused, no held-out image scored or report written: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"gate_heldout: crashed: {exc!r}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
