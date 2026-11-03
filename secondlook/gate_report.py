"""RESULTS.md for a gate run (DAT-852). Pure text from the run's own files; it decides nothing.

Wording is fixed by L4 PROTOCOL.md line 105 (part F) and the project lead's c014e175 and 9d763557 on DAT-849: every
rate carries N, the arm and its bound; the sentence per arm is the allowed one; there is no pooled figure
across arms; balanced accuracy is two class recalls with 95% Clopper-Pearson intervals plus a mean whose
interval carries the label gate_baseline.BA_LABEL; "content-based" is never said. PASS, FAIL and INVALID are
published as the harness computed them, and the shortcut label sits next to the verdict.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from gate import harness
from gate.stats import lower_bound, upper_bound

from . import gate_baseline

REAL_DESC = "unit-level real photographs (curated Wikimedia Commons, pre-2022)"
AI_DESC = "unit-level Stable Diffusion 1.x images from one August 2022 sample"
SYN_REAL, SYN_AI = "synthetic real-class images", "synthetic AI-class images"
GATE_NOTE = {"original": "gated", "format_matched": "gated"}
ARM_TITLE = {
    "original": "original (the fetched bytes, untouched)",
    "format_matched": "format_matched (JPEG q90, at most 1,536 px, shrink only)",
    "compressed_q70_1080": "compressed_q70_1080 (JPEG q70, at most 1,080 px; AI images are mostly 512 px and stay that size)",
    "size_matched_512": "size_matched_512 (diagnostic: centre square crop, 512 x 512, JPEG q90)",
}


def pct(x) -> str:
    return "n/a" if x is None else f"{x:.3%}"


def _rate(k: int, n: int, side: str) -> tuple[str, str]:
    if not n:
        return "no images", "n/a"
    bound = upper_bound(k, n) if side == "upper" else lower_bound(k, n)
    return f"{k}/{n} ({pct(k / n)})", pct(bound)


def class_table(arm: str, who: str, t: dict) -> list[str]:
    """Per-class recalls (95% two-sided exact) and the balanced accuracy with its conservative interval."""
    if t["balanced_accuracy"] is None:
        return [f"{who} in `{arm}`: balanced accuracy not computed (one class has no images).", ""]
    ba, cons = t["balanced_accuracy"], t["ba_interval_conservative"]
    rows = [("real (true-negative rate)", t["true_negative"], t["n_real"], t["tnr_interval_95"]),
            ("AI (true-positive rate)", t["true_positive"], t["n_ai"], t["tpr_interval_95"])]
    L = [f"{who} in `{arm}`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):", "",
         "| Class | Correct / n | Recall | 95% two-sided interval |", "|---|---|---|---|"]
    L += [f"| {c} | {k}/{n} | {pct(k / n)} | {pct(ci[0])} to {pct(ci[1])} |" for c, k, n, ci in rows]
    L += ["", f"Balanced accuracy, {who.lower()}, `{arm}`: {pct(ba)}, interval {pct(cons[0])} to {pct(cons[1])} "
              f"({gate_baseline.BA_LABEL}). The interval is the mean of the ends of two 97.5% two-sided "
              "Clopper-Pearson intervals, one per class (0.05 split over two classes). "
              "It makes no simultaneous claim across arms or topics.", ""]
    return L


def coverage(plan, failures: list[dict], partial: set) -> list[str]:
    """Diagnostic-arm derive failures: coverage and attrition for that arm; the gated population never changes."""
    if not failures:
        return ["Diagnostic arms: every accepted unit was derived into every arm, so each arm covers the same units.", ""]
    L = ["Diagnostic-arm coverage. A conversion failure in a diagnostic arm never changes the gated population "
         "(original and format_matched); it is reported here as that arm's coverage and attrition.", "",
         "| Arm | Split | Units not derived | Reason |", "|---|---|---|---|"]
    for f in failures:
        L.append(f"| {f['arm']} | {f['split']} | {f['item_id']} | {f['reason']} |")
    n_test = len(plan.ctx["test_rows"])
    for arm in sorted({f["arm"] for f in failures}):
        t = sum(f["arm"] == arm and f["split"] == "test" for f in failures)
        L.append("")
        L.append(f"`{arm}` covers {n_test - t} of {n_test} test units"
                 f"{' and is left out of the L2 harness input (the harness needs every arm to cover every test unit)' if arm in partial else ''}.")
    return L + [""]


def topic_rows(preds: list[dict], arm: str, topics: dict) -> list[dict]:
    cells = defaultdict(lambda: {"real": Counter(), "ai": Counter()})
    for r in preds:
        if r["arm"] == arm:
            cells[topics.get(r["item_id"], "(none)")][r["label"]][r["verdict"] == "ai"] += 1
    rows = []
    for topic in sorted(cells):
        c = cells[topic]
        rows.append({"topic": topic, "n_real": sum(c["real"].values()), "fp": c["real"][True],
                     "n_ai": sum(c["ai"].values()), "caught": c["ai"][True]})
    return rows


def attrition(plan, planned: Counter) -> list[str]:
    acc = plan.ctx["accepted"]
    rej = Counter((r.get("split"), r.get("label")) for r in acc["rejections"])
    lines = ["| Split | Label | Planned (primary units in the locked manifests) | Rejected | Reserve units used | Accepted and scored |",
             "|---|---|---|---|---|---|"]
    for split in ("dev", "test"):
        units = acc["units"][split]
        for label in ("real", "ai"):
            mine = [u for u in units if u.get("label") == label]
            reserve = sum(u.get("role") == "reserve" for u in mine)
            lines.append(f"| {split} | {label} | {planned.get((split, label), 0)} | {rej[(split, label)]} | "
                         f"{reserve} | {len(mine)} |")
    by_check = Counter(r.get("check") or "(not named)" for r in acc["rejections"])
    lines.append("")
    if by_check:
        lines.append("Rejections by check: " + ", ".join(f"{k}: {v}" for k, v in sorted(by_check.items())) + ".")
    else:
        lines.append("No unit was rejected.")
    chk = plan.ctx.get("reserve_check")
    if chk:
        lines.append("Replacements came only from the reserve order frozen in the manifests (`spare_for`, `spare_rank`), "
                     "checked against `groups-real.csv` and `groups-ai.csv` before anything was derived: "
                     f"{chk['used']} reserve unit(s) used, each the lowest unused `spare_rank` for its split and topic; "
                     f"{chk['skipped']} reserve unit(s) skipped because they were rejected themselves; "
                     f"{chk['unfilled']} rejected primary unit(s) had no reserve left in their split and topic, so that "
                     "count fell. Nothing was redrawn." +
                     (f" {chk['rejected_unneeded']} reserve unit(s) beyond the replacements owed were screened and "
                      "rejected, and were not used." if chk.get("rejected_unneeded") else ""))
    else:
        lines.append("The reserve order was not checked against the frozen manifests in this run.")
    return lines


def render(result: dict, preds: list[dict], answers: list[dict], baseline: dict, plan, provenance: dict,
           planned: Counter, exit_code: int, failures: list[dict] | None = None,
           partial: dict | None = None) -> str:
    """`partial`: {arm: metrics} for a diagnostic arm that covers fewer test units than the gated ones. The
    harness never saw it (it needs full coverage), so its metrics are computed here from the same predictions."""
    syn = plan.synthetic
    real_d, ai_d = (SYN_REAL, SYN_AI) if syn else (REAL_DESC, AI_DESC)
    failures, partial = failures or [], partial or {}
    arms = {**result["arms"], **partial}
    gate_arms = result["gate"]["gate_arms"]
    topics = plan.ctx["topics"]
    thr = result["threshold"]
    L: list[str] = ["# Results", ""]
    if syn:
        L += ["> **SYNTHETIC FIXTURE RUN. Generated images and a fake classifier, made to test the gate command. "
              "Nothing on this page is a measurement of any detector or of Second Look.**", ""]
    L += [f"Gate verdict from the harness: **{result['status']}** (exit code {exit_code}). It is published as it "
          "stands, with every arm, the bounds, the per-topic results, the attrition and the shortcut labels.", ""]
    if result["reasons"]:
        L += ["Reasons the harness gave:", ""] + [f"- {x}" for x in result["reasons"]] + [""]
    if result["warnings"]:
        L += ["Warnings the harness gave:", ""] + [f"- {x}" for x in result["warnings"]] + [""]

    L += ["## The run", "", "| Field | Value |", "|---|---|"]
    rows = [("Run id", result["run_id"]), ("Date (UTC)", result["date"]),
            ("Gate verdict, harness exit code", f"{result['status']}, {exit_code}"),
            ("Gated arms (both must pass)", ", ".join(gate_arms)),
            ("Secondlook commit", provenance["secondlook_commit"]),
            ("Classifier", f"{thr['classifier']['name']}, version {thr['classifier']['version']}"),
            ("Weights sha256", thr["classifier"]["weights_sha256"]),
            ("Threshold", f"{thr['value']} ({thr['rule']})"),
            ("Threshold file sha256", result["inputs"]["threshold"]["sha256"]),
            ("Threshold committed in git", f"{provenance['threshold_commit']['commit']} at "
                                           f"{provenance['threshold_commit']['committed_at']}"),
            ("Frozen at, frozen by", f"{thr['frozen_at']}, {thr['frozen_by']}"),
            ("Dev split sha256", result["inputs"]["dev_split"]["sha256"]),
            ("Test split sha256", result["inputs"]["test_split"]["sha256"]),
            ("L4 LOCK.json sha256", provenance["l4_lock_sha256"]),
            ("SCREEN-CONFIG.json sha256", provenance["screen_config_sha256"]),
            ("ACCEPTED.json sha256", provenance["accepted_sha256"]),
            ("Originals read from (gate_splits --file-base)", provenance.get("file_base") or "not recorded in SPLIT.json"),
            ("Predictions file sha256", provenance["predictions_sha256"]),
            ("Recipe R1 code (scripts/derive.py) sha256", provenance["derive_py_sha256"]),
            ("Derive environment", ", ".join(f"{k} {v}" for k, v in sorted(provenance["derive_env"].items())))]
    L += [f"| {k} | {v} |" for k, v in rows]
    tdata = plan.ctx["threshold"].get("training_data_statement", "")
    L += ["", f"Detector training data, as stated in the threshold file: {tdata}", ""]

    n_dev = Counter(r["label"] for r in plan.ctx["dev_rows"])
    n_test = Counter(r["label"] for r in plan.ctx["test_rows"])
    L += ["## What was measured", "", "| | Real | AI |", "|---|---|---|",
          f"| Dev split (threshold frozen here only), units | {n_dev['real']} | {n_dev['ai']} |",
          f"| Test split, independent units, one image each | {n_test['real']} | {n_test['ai']} |", "",
          f"Gate floors, set in advance: test at least {result['gate']['min_real']} real and "
          f"{result['gate']['min_ai']} AI per gated arm; dev at least 100 and 100. A run below a floor is INVALID. "
          "Pass condition in each gated arm: the one-sided 95% exact Clopper-Pearson upper bound on false "
          "positives is at most 2% and the lower bound on AI images caught is at least 40%.", ""]

    L += ["## Per arm", "",
          "One table per arm, each over the same test units unless its coverage line says otherwise. No figure is "
          "added up across arms. Rates are as observed; the bounds in the gate table and per-topic tables are "
          "one-sided 95% exact (Clopper-Pearson); the class tables give each recall a two-sided 95% exact interval.", ""]
    L += coverage(plan, failures, set(partial))
    for arm in harness.ordered_arms(arms):
        m = arms[arm]
        gated = arm in gate_arms
        fp, ai = m["false_positive"], m["ai_caught"]
        fp_txt, fp_ub = _rate(fp["k"], fp["n"], "upper")
        ai_txt, ai_lb = _rate(ai["k"], ai["n"], "lower")
        det = [r for r in preds if r["arm"] == arm]
        det_t = gate_baseline.detector_class_table(det)
        det_ba = det_t["balanced_accuracy"]
        base = baseline[arm]
        label = gate_baseline.label_arm(base["balanced_accuracy"], det_ba, base["ba_interval_conservative"],
                                        det_t["ba_interval_conservative"])
        L += [f"### {ARM_TITLE.get(arm, arm)}", "",
              f"Role: {'**gated**' if gated else 'reported, not gated'}.", "",
              f"On {m['n_real']} {real_d} and {m['n_ai']} {ai_d}, observed false-alarm rate {fp_txt}, "
              f"one-sided 95% upper bound {fp_ub}; observed catch rate {ai_txt}, lower bound {ai_lb}; arm: {arm}.", "",
              "| Arm | n real | Real flagged (observed) | Upper bound on false positives | n AI | AI flagged (observed) | Lower bound on AI flagged |",
              "|---|---|---|---|---|---|---|",
              f"| {arm} | {m['n_real']} | {fp_txt} | {fp_ub} | {m['n_ai']} | {ai_txt} | {ai_lb} |", ""]
        if gated:
            rule = m["rule"]
            L += [f"Gate conditions in this arm: false-positive upper bound at most 2%: "
                  f"{'met' if rule['fp_upper_ok'] else 'not met'}; AI-caught lower bound at least 40%: "
                  f"{'met' if rule['catch_lower_ok'] else 'not met'}; floors of "
                  f"{result['gate']['min_real']} real and {result['gate']['min_ai']} AI: "
                  f"{'met' if rule['denominators_ok'] else 'not met'}.", ""]
        L += class_table(arm, "Detector", det_t)
        L += [f"Shortcut baseline in `{arm}` (depth-3 tree on file facts, fitted on {base['n_dev_fit']} dev units, "
              f"scored on {base['n_real']} real and {base['n_ai']} AI test units). "
              f"Features the tree used: {', '.join(base['features_used_by_tree']) or 'none'}.", ""]
        L += class_table(arm, "Shortcut baseline", base)
        L += [f"**Shortcut label, {arm}: {label['text']}**", ""]
        tr = topic_rows(preds, arm, topics)
        L += [f"Per topic, `{arm}` (one-sided 95% bounds on small counts; not gate results):", "",
              "| Topic | n real | Real flagged | Upper bound on false positives | n AI | AI flagged | Lower bound on AI flagged |",
              "|---|---|---|---|---|---|---|"]
        for t in tr:
            a, b = _rate(t["fp"], t["n_real"], "upper")
            c, d = _rate(t["caught"], t["n_ai"], "lower")
            L.append(f"| {t['topic']} | {t['n_real']} | {a} | {b} | {t['n_ai']} | {c} | {d} |")
        L.append("")

    L += ["## Attrition", ""] + attrition(plan, planned) + [""]
    counts = Counter(r["answer"] for r in answers)
    L += ["## Ladder answers on the test split (`original` arm)", "",
          "The full ladder ran on each original. These counts are reported; they are not a gate input.", "",
          "| Answer | Images |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())] + [""]
    L += ["## Reading the result", "",
          "Only what the tables above support. The limits that apply:", ""]
    if syn:
        L += ["- This is a synthetic fixture: the images are generated and the classifier is a stand-in. No sentence "
              "above is about a real detector.", ""]
    else:
        L += ["- The AI side is one Stable Diffusion 1.x family from one August 2022 sample, mostly 512 px square; "
              "the real side is curated, pre-2022 Wikimedia Commons photographs. The test set's size sets how "
              "tight any bound can be.",
              "- The splits are by source unit, not by generator: a result here says nothing about other "
              "generators, newer versions of Stable Diffusion, or any Meta model.",
              "- Commons is widely crawled, so these photographs may be in the detector's or a generator's "
              "training data; the threshold file's training-data statement is quoted above.",
              "- In an arm labelled shortcut-exposed, file facts alone separate the classes; read the detector "
              "result there with that label. \"Exceeds this specified shortcut baseline\" compares the detector "
              "with one specified file-facts model; it is not a finding about what the detector reads.",
              "- The balanced-accuracy intervals are conservative, at least 95%, conditional on per-class binomial "
              "unit sampling (each test image one independent trial). They make no simultaneous claim across "
              "arms or topics.",
              "- People screening of the test images was OpenCV triage plus a Muse Glimmer check of flagged images "
              "only; not certified person-free.", ""]
    L += ["## Provenance of this page", "",
          f"Written by `python -m secondlook.gate_run` from `{result['inputs']['predictions']['path']}` "
          f"(sha256 {provenance['predictions_sha256']}), the L2 harness's `RESULT.md` and `result.json`, "
          "`baseline.json`, `ladder-answers.csv`, `hashes-derived.csv` and `gate-run.json` in the run folder.", ""]
    return "\n".join(L)


def render_invalid(plan, reasons: list[str], planned: Counter, exit_code: int, provenance: dict,
                   floors: dict) -> str:
    """RESULTS.md for a run that failed a floor before anything was derived or scored. No detector number exists."""
    (MIN_DEV_REAL, MIN_DEV_AI), (MIN_REAL, MIN_AI) = floors["dev"], floors["test"]
    syn = plan.synthetic
    n_dev = Counter(r["label"] for r in plan.ctx["dev_rows"])
    n_test = Counter(u["label"] for u in plan.ctx["accepted"]["units"]["test"])
    L: list[str] = ["# Results", ""]
    if syn:
        L += ["> **SYNTHETIC FIXTURE RUN. Generated images, made to test the gate command. "
              "Nothing on this page is a measurement of any detector or of Second Look.**", ""]
    L += [f"Gate verdict: **INVALID** (exit code {exit_code}). It is published as it stands. The floors were checked "
          "first, on the accepted units after attrition and the frozen reserves, so no image was derived, no "
          "image was scored and no predictions file was written. This run has no detector result, no balanced "
          "accuracy and no shortcut baseline.", "",
          "Reasons:", ""] + [f"- {x}" for x in reasons] + [""]
    gate_arms = ", ".join(provenance["gate_arms"])
    L += ["## Counts against the floors", "",
          "| Set | Real | AI | Floor (real, AI) | Met |", "|---|---|---|---|---|",
          f"| Dev split, accepted units | {n_dev['real']} | {n_dev['ai']} | {MIN_DEV_REAL}, {MIN_DEV_AI} | "
          f"{'yes' if n_dev['real'] >= MIN_DEV_REAL and n_dev['ai'] >= MIN_DEV_AI else 'no'} |",
          f"| Test split, accepted units (each gated arm: {gate_arms}) | {n_test['real']} | {n_test['ai']} | "
          f"{MIN_REAL}, {MIN_AI} | {'yes' if n_test['real'] >= MIN_REAL and n_test['ai'] >= MIN_AI else 'no'} |", "",
          "The floors were set in advance and are not lowered. Both gated arms are made from the same accepted "
          "originals, so each has the test counts above.", "",
          "## Attrition", ""] + attrition(plan, planned) + [""]
    L += ["## The run", "", "| Field | Value |", "|---|---|"]
    rows = [("Run id", provenance["run_id"]), ("Date (UTC)", provenance["finished_at"]),
            ("Gate verdict, exit code", f"INVALID, {exit_code}"),
            ("Secondlook commit", provenance["secondlook_commit"]),
            ("L4 LOCK.json sha256", provenance["l4_lock_sha256"]),
            ("SCREEN-CONFIG.json sha256", provenance["screen_config_sha256"]),
            ("ACCEPTED.json sha256", provenance["accepted_sha256"]),
            ("Originals read from (gate_splits --file-base)", provenance.get("file_base") or "not recorded in SPLIT.json"),
            ("Dev split sha256", plan.ctx["l4"]["dev_sha"]), ("Test split sha256", plan.ctx["l4"]["test_sha"])]
    L += [f"| {k} | {v} |" for k, v in rows] + [""]
    return "\n".join(L)
