import ast
import contextlib
import io
import csv
import hashlib
import json
import os
import pathlib
import socket
import tempfile
import unittest
from unittest import mock

from gate import ARM_COMPRESSED, ARM_FORMAT_MATCHED, ARM_ORIGINAL
from gate import harness

THRESHOLD = 0.5
BAND = {"rule": "score_band", "low": 0.4, "high": 0.6, "real_abstain_counts_as": "not_flagged"}
FROZEN_AT = "2026-10-20T12:00:00Z"
PREDICTED_AT = "2026-10-21T09:00:00Z"
PKG = pathlib.Path(__file__).resolve().parents[2] / "gate"


def arm_rows(arm, n_real, fp, n_ai, caught, abstain_real=0, abstain_ai=0, generators=None):
    """Synthetic predictions for one arm; ids are shared across arms.
    Scores: 0.9 false alarm, 0.45 abstain (inside BAND), 0.1 real, 0.8 caught, 0.2 missed."""
    rows = []
    for i in range(n_real):
        if i < fp:
            verdict, score = "ai", 0.9
        elif i < fp + abstain_real:
            verdict, score = "abstain", 0.45
        else:
            verdict, score = "real", 0.1
        rows.append({"item_id": f"r{i:04d}", "arm": arm, "label": "real",
                     "verdict": verdict, "score": score, "predicted_at": PREDICTED_AT,
                     "generator": ""})
    for i in range(n_ai):
        if i < caught:
            verdict, score = "ai", 0.8
        elif i < caught + abstain_ai:
            verdict, score = "abstain", 0.45
        else:
            verdict, score = "real", 0.2
        gen = generators[i % len(generators)] if generators else ""
        rows.append({"item_id": f"a{i:04d}", "arm": arm, "label": "ai",
                     "verdict": verdict, "score": score, "predicted_at": PREDICTED_AT,
                     "generator": gen})
    return rows


def three_arms(n_real=400, fp=0, n_ai=200, caught=120, **orig):
    """All three required arms; the gate arm (original) takes the arguments."""
    return (arm_rows(ARM_ORIGINAL, n_real, fp, n_ai, caught, **orig)
            + arm_rows(ARM_FORMAT_MATCHED, n_real, 1, n_ai, 110)
            + arm_rows(ARM_COMPRESSED, n_real, 5, n_ai, 90))


def write_csv(path, rows, cols=None):
    cols = cols or list(rows[0])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


class Fixture(unittest.TestCase):
    def quiet_main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return harness.main(argv)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.dev_rows = ([{"item_id": f"dev-r{i}", "label": "real"} for i in range(100)]
                         + [{"item_id": f"dev-a{i}", "label": "ai"} for i in range(100)])
        self.thr_over = {}
        self.test_rows = None   # None: test.csv holds exactly the ids in the predictions

    def tearDown(self):
        self.tmp.cleanup()

    def write_split(self, name, rows):
        return write_csv(os.path.join(self.dir, name), rows,
                         ["item_id", "label", "path", "group", "phash", "sha256", "cluster"])

    def write_threshold(self, dev, test):
        cfg = {"schema": "second-look-threshold/2", "threshold": THRESHOLD,
               "ai_if": "score>=threshold", "abstain": BAND,
               "classifier": {"name": "synthetic", "version": "0", "weights_sha256": "none"},
               "dev_split_sha256": harness.sha256_file(dev),
               "test_split_sha256": harness.sha256_file(test),
               "phash_distance": 8, "frozen_at": FROZEN_AT, "frozen_by": "test fixture"}
        cfg.update(self.thr_over)
        cfg = {k: v for k, v in cfg.items() if v is not DROP}
        path = os.path.join(self.dir, "threshold.json")
        with open(path, "w") as f:
            json.dump(cfg, f)
        return path

    def write_preds(self, rows, name="preds.csv"):
        return write_csv(os.path.join(self.dir, name), rows)

    def inputs(self, rows):
        """(predictions, threshold, dev, test) paths for these prediction rows."""
        test_rows = self.test_rows
        if test_rows is None:
            seen = {}
            for r in rows:
                seen.setdefault(r["item_id"], r["label"])
            test_rows = [{"item_id": i, "label": label} for i, label in seen.items()]
        # Every row gets its own file hash and source group unless a test sets them.
        for r in self.dev_rows + test_rows:
            r.setdefault("sha256", hashlib.sha256(r["item_id"].encode()).hexdigest())
        for r in self.dev_rows + test_rows:
            r.setdefault("group", "src-" + r["item_id"])
        dev = self.write_split("dev.csv", self.dev_rows)
        test = self.write_split("test.csv", test_rows)
        return self.write_preds(rows), self.write_threshold(dev, test), dev, test

    def run_gate(self, rows, **kw):
        return harness.evaluate(*self.inputs(rows), run_id=kw.get("run_id", "t"),
                                date="2026-10-30")

    def argv(self, rows, out=None):
        p, t, d, s = self.inputs(rows)
        return ["--predictions", p, "--threshold", t, "--dev-split", d, "--test-split", s,
                "--out", out or os.path.join(self.dir, "out")]


DROP = object()   # thr_over value that removes the key


class GateDecisions(Fixture):
    def test_zero_fp_at_400_passes_with_bound(self):
        res = self.run_gate(three_arms())
        self.assertEqual(res["status"], "PASS", res["reasons"])
        fp = res["arms"][ARM_ORIGINAL]["false_positive"]
        self.assertEqual((fp["k"], fp["n"], fp["observed"]), (0, 400, 0.0))
        self.assertAlmostEqual(fp["upper_bound"], 1 - 0.05 ** (1 / 400), places=12)

    def test_zero_fp_at_600_passes_with_bound(self):
        res = self.run_gate(three_arms(n_real=600))
        self.assertEqual(res["status"], "PASS")
        self.assertAlmostEqual(res["arms"][ARM_ORIGINAL]["false_positive"]["upper_bound"],
                               1 - 0.05 ** (1 / 600), places=12)

    def test_zero_fp_at_200_meets_bound_but_run_is_invalid(self):
        res = self.run_gate(three_arms(n_real=200))
        fp = res["arms"][ARM_ORIGINAL]["false_positive"]
        self.assertAlmostEqual(fp["upper_bound"], 0.014867039231272054, places=12)
        self.assertTrue(res["arms"][ARM_ORIGINAL]["rule"]["fp_upper_ok"])
        self.assertEqual(res["status"], "INVALID")
        self.assertIn("below the locked 400 real", res["reasons"][0])

    def test_three_fp_at_400_passes_four_fails(self):
        self.assertEqual(self.run_gate(three_arms(fp=3))["status"], "PASS")
        res = self.run_gate(three_arms(fp=4))
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("false-positive upper bound 2.27", res["reasons"][0])

    def test_fails_on_ai_catch_bound_although_observed_is_above_40(self):
        # 85/200 = 42.5% observed, but the one-sided lower bound is 36.6%.
        res = self.run_gate(three_arms(caught=85))
        self.assertEqual(res["status"], "FAIL")
        tp = res["arms"][ARM_ORIGINAL]["ai_caught"]
        self.assertAlmostEqual(tp["observed"], 0.425)
        self.assertLess(tp["lower_bound"], 0.40)
        self.assertEqual(len(res["reasons"]), 1)
        self.assertIn("AI-caught lower bound", res["reasons"][0])

    def test_abstentions_count_as_missed_and_not_flagged(self):
        res = self.run_gate(three_arms(caught=80, abstain_ai=20, abstain_real=10))
        m = res["arms"][ARM_ORIGINAL]
        self.assertEqual(m["ai_caught"]["n"], 200)
        self.assertEqual(m["ai_caught_verdict_bearing"]["n"], 180)
        self.assertEqual(m["false_positive"]["n"], 400)
        self.assertEqual(m["false_positive"]["k"], 0)
        self.assertEqual(m["false_positive_verdict_bearing"]["n"], 390)
        self.assertEqual(m["confusion"]["ai"], {"ai": 80, "real": 100, "abstain": 20})


class CompressedArm(Fixture):
    def test_compressed_arm_is_reported_separately_and_not_gated(self):
        rows = (arm_rows(ARM_ORIGINAL, 400, 0, 200, 120)
                + arm_rows(ARM_FORMAT_MATCHED, 400, 0, 200, 120)
                + arm_rows(ARM_COMPRESSED, 400, 30, 200, 60))
        res = self.run_gate(rows)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(list(res["arms"]), [ARM_ORIGINAL, ARM_FORMAT_MATCHED, ARM_COMPRESSED])
        comp = res["arms"][ARM_COMPRESSED]
        self.assertEqual((comp["false_positive"]["k"], comp["false_positive"]["n"]), (30, 400))
        self.assertFalse(comp["rule"]["passes"])
        self.assertTrue(any("above 5%" in w for w in res["warnings"]))
        md = harness.render_markdown(res)
        self.assertIn(f"## Arm: {ARM_COMPRESSED} (reported, not gated)", md)
        self.assertIn("observed 7.500% (30/400)", md)
        self.assertEqual(res["n_test_items"], 600)

    def test_gating_the_compressed_arm_too(self):
        self.thr_over["gate_arms"] = [ARM_ORIGINAL, ARM_COMPRESSED]
        rows = (arm_rows(ARM_ORIGINAL, 400, 0, 200, 120)
                + arm_rows(ARM_FORMAT_MATCHED, 400, 0, 200, 120)
                + arm_rows(ARM_COMPRESSED, 400, 30, 200, 60))
        self.assertEqual(self.run_gate(rows)["status"], "FAIL")


class Reporting(Fixture):
    def test_writes_result_files_with_n_run_id_date_and_matrix(self):
        rows = (arm_rows(ARM_ORIGINAL, 400, 2, 200, 120, generators=["sdxl", "dbx"])
                + arm_rows(ARM_FORMAT_MATCHED, 400, 2, 200, 110)
                + arm_rows(ARM_COMPRESSED, 400, 4, 200, 100))
        out = os.path.join(self.dir, "out")
        code = self.quiet_main(self.argv(rows, out) + ["--run-id", "fri-30",
                                                       "--date", "2026-10-30"])
        self.assertEqual(code, 0)
        res = json.loads(pathlib.Path(out, "result.json").read_text())
        self.assertEqual((res["run_id"], res["date"]), ("fri-30", "2026-10-30"))
        self.assertEqual(res["arms"][ARM_ORIGINAL]["n_real"], 400)
        self.assertEqual(res["arms"][ARM_ORIGINAL]["confusion"]["real"],
                         {"ai": 2, "real": 398, "abstain": 0})
        self.assertEqual(res["arms"][ARM_ORIGINAL]["per_generator"],
                         {"dbx": {"n": 100, "caught": 60}, "sdxl": {"n": 100, "caught": 60}})
        self.assertEqual(res["threshold"]["frozen_at"], FROZEN_AT)
        self.assertEqual(res["predicted_at"]["first"], PREDICTED_AT)
        md = pathlib.Path(out, "RESULT.md").read_text()
        self.assertIn("Run id: `fri-30`", md)
        self.assertIn("observed 0.500% (2/400); one-sided 95% upper bound", md)
        self.assertIn("| real | 2 | 398 | 0 |", md)
        self.assertIn(f"Frozen at {FROZEN_AT} by test fixture", md)
        self.assertIn("abstain if 0.4 <= score < 0.6", md)

    def test_never_says_under_2_percent(self):
        for fp in (0, 3, 8):
            md = harness.render_markdown(self.run_gate(three_arms(fp=fp)))
            low = md.lower()
            for banned in ("under 2%", "< 2%", "<2%", "below 2%", "less than 2%"):
                self.assertNotIn(banned, low)
            self.assertIn("one-sided 95% upper bound", md)

    def test_deterministic(self):
        rows = three_arms(fp=1)
        a = json.dumps(self.run_gate(rows, run_id=None), sort_keys=True)
        b = json.dumps(self.run_gate(rows, run_id=None), sort_keys=True)
        self.assertEqual(a, b)
        self.assertTrue(json.loads(a)["run_id"].startswith("run-"))

    def test_exit_codes(self):
        self.assertEqual(self.quiet_main(self.argv(three_arms())), 0)
        self.assertEqual(self.quiet_main(self.argv(three_arms(fp=9))), 1)
        self.assertEqual(self.quiet_main(self.argv(three_arms(n_real=100))), 3)
        rows = three_arms()
        rows[0]["score"] = 0.7   # verdict real, but 0.7 >= 0.5 means ai
        self.assertEqual(self.quiet_main(self.argv(rows)), 2)


class InputGuards(Fixture):
    def test_verdict_must_follow_from_frozen_threshold(self):
        rows = three_arms()
        rows[0]["score"] = 0.7  # verdict real, but 0.7 >= 0.5 means ai
        with self.assertRaisesRegex(harness.InputError, "does not follow from score"):
            self.run_gate(rows)

    def test_strict_threshold_direction(self):
        self.thr_over.update(ai_if="score>threshold", abstain={"rule": "none"})
        rows = three_arms()
        rows[400]["score"] = THRESHOLD  # an AI item marked real; 0.5 > 0.5 is false
        rows[400]["verdict"] = "real"
        self.assertEqual(self.run_gate(rows)["status"], "PASS")

    def test_dev_items_in_test_are_refused(self):
        rows = three_arms()
        for r in rows:
            if r["item_id"] == "r0005":
                r["item_id"] = "dev-r3"
        # Its own test group, so the id check, not the group check, refuses it.
        self.test_rows = [{"item_id": i, "label": label, "group": "test-" + i}
                          for i, label in dict((r["item_id"], r["label"]) for r in rows).items()]
        with self.assertRaisesRegex(harness.InputError, "also in the dev split"):
            self.run_gate(rows)

    def test_dev_split_must_match_the_frozen_hash(self):
        self.thr_over["dev_split_sha256"] = "0" * 64
        with self.assertRaisesRegex(harness.InputError, "dev split .* was frozen with"):
            self.run_gate(three_arms())

    def test_duplicate_item_in_arm_and_bad_verdict(self):
        rows = three_arms()
        rows[1]["item_id"] = rows[0]["item_id"]
        rows[2]["verdict"] = "fake"
        self.test_rows = [{"item_id": f"r{i:04d}", "label": "real"} for i in range(400)] \
            + [{"item_id": f"a{i:04d}", "label": "ai"} for i in range(200)]
        with self.assertRaisesRegex(harness.InputError, "2 bad prediction row"):
            self.run_gate(rows)

    def test_threshold_file_is_checked(self):
        for over in ({"schema": "x"}, {"schema": "second-look-threshold/1"},
                     {"threshold": "0.5"}, {"ai_if": "score"},
                     {"classifier": {}}, {"dev_split_sha256": None}, {"gate_arms": []}):
            with self.subTest(over=over):
                self.thr_over = dict(over)
                with self.assertRaises(harness.InputError):
                    self.run_gate(three_arms())

    def test_threshold_file_that_is_not_an_object_is_bad_input(self):
        p, t, d, s = self.inputs(three_arms())
        with open(t, "w") as f:
            json.dump([0.5], f)
        with self.assertRaisesRegex(harness.InputError, "must be a JSON object"):
            harness.evaluate(p, t, d, s)


class AbstainIsFrozen(Fixture):
    """B1: an abstain verdict follows from the score by the frozen rule."""

    def test_high_score_real_images_cannot_hide_as_abstain(self):
        # The review's probe: 40 false alarms at 0.9 in 400 real fail honestly ...
        honest = three_arms(fp=40)
        self.assertEqual(self.run_gate(honest)["status"], "FAIL")
        # ... and marking those 40 rows abstain used to give PASS.
        hidden = three_arms(fp=40)
        for r in hidden:
            if r["arm"] == ARM_ORIGINAL and r["verdict"] == "ai" and r["label"] == "real":
                r["verdict"] = "abstain"
        with self.assertRaisesRegex(harness.InputError,
                                    "(?s)40 bad prediction row.*verdict abstain does not follow"):
            self.run_gate(hidden)

    def test_blank_score_abstain_is_refused(self):
        rows = three_arms(abstain_real=1)
        rows[0]["score"] = ""
        with self.assertRaisesRegex(harness.InputError, "abstentions too"):
            self.run_gate(rows)

    def test_score_in_band_must_be_abstain(self):
        rows = three_arms()
        rows[0]["score"] = 0.45   # inside [0.4, 0.6) but verdict real
        with self.assertRaisesRegex(harness.InputError, "which gives abstain"):
            self.run_gate(rows)

    def test_rule_none_forbids_abstain(self):
        self.thr_over["abstain"] = {"rule": "none"}
        with self.assertRaisesRegex(harness.InputError, "never abstain"):
            self.run_gate(three_arms(abstain_real=1))

    def test_abstain_rule_is_required_and_checked(self):
        for rule in (DROP, {}, {"rule": "sometimes"},
                     {"rule": "score_band", "low": 0.6, "high": 0.4,
                      "real_abstain_counts_as": "not_flagged"},
                     {"rule": "score_band", "low": 0.4, "high": 0.6}):
            with self.subTest(rule=rule):
                self.thr_over["abstain"] = rule
                with self.assertRaisesRegex(harness.InputError, "abstain"):
                    self.run_gate(three_arms())

    def test_real_abstain_counts_as_stated_in_the_frozen_rule(self):
        rows = three_arms(abstain_real=10)
        self.assertEqual(self.run_gate(rows)["status"], "PASS")
        self.thr_over["abstain"] = dict(BAND, real_abstain_counts_as="flagged")
        res = self.run_gate(rows)
        fp = res["arms"][ARM_ORIGINAL]["false_positive"]
        self.assertEqual((fp["k"], fp["n"]), (10, 400))
        self.assertEqual(res["status"], "FAIL")


class ScoredAgainstTestSplit(Fixture):
    """B2: labels and membership come from test.csv; overlap is checked by content."""

    def full_test(self):
        return ([{"item_id": f"r{i:04d}", "label": "real"} for i in range(400)]
                + [{"item_id": f"a{i:04d}", "label": "ai"} for i in range(200)])

    def test_relabelled_false_alarms_are_refused(self):
        self.test_rows = self.full_test() + [{"item_id": f"x{i:03d}", "label": "real"}
                                             for i in range(40)]
        rows = three_arms(fp=0)
        for arm in (ARM_ORIGINAL, ARM_FORMAT_MATCHED, ARM_COMPRESSED):
            for i in range(40):   # 40 real images flagged, relabelled ai in the predictions
                rows.append({"item_id": f"x{i:03d}", "arm": arm, "label": "ai",
                             "verdict": "ai", "score": 0.9, "predicted_at": PREDICTED_AT,
                             "generator": ""})
        with self.assertRaisesRegex(harness.InputError, "label 'ai' disagrees with test.csv"):
            self.run_gate(rows)

    def test_labels_come_from_test_csv_when_predictions_have_none(self):
        self.test_rows = self.full_test()
        rows = three_arms(fp=4)
        for r in rows:
            del r["label"]
        res = self.run_gate(rows)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["arms"][ARM_ORIGINAL]["false_positive"]["k"], 4)

    def test_rows_not_in_test_csv_are_refused(self):
        self.test_rows = self.full_test()
        rows = three_arms() + arm_rows(ARM_ORIGINAL, 401, 0, 0, 0)[400:]
        rows[-1]["item_id"] = "new-0001"
        with self.assertRaisesRegex(harness.InputError, "not in test.csv"):
            self.run_gate(rows)

    def test_dropped_rows_make_the_run_invalid(self):
        self.test_rows = self.full_test()
        rows = [r for r in three_arms(fp=4)
                if not (r["arm"] == ARM_ORIGINAL and r["item_id"] in
                        {"r0000", "r0001", "r0002", "r0003"})]
        res = self.run_gate(rows)
        self.assertEqual(res["status"], "INVALID")
        self.assertIn("original: 4 of the 600 test.csv images have no prediction", res["reasons"][0])

    def test_test_split_must_match_the_frozen_hash(self):
        self.thr_over["test_split_sha256"] = "0" * 64
        with self.assertRaisesRegex(harness.InputError, "test split .* was frozen with"):
            self.run_gate(three_arms())

    def test_dev_image_under_a_new_id_is_refused(self):
        self.test_rows = self.full_test()
        self.test_rows[7]["sha256"] = self.dev_rows[3]["sha256"] = "ab" * 32
        with self.assertRaisesRegex(harness.InputError, "test r0007 and dev dev-r3: same file"):
            self.run_gate(three_arms())

    def test_dev_near_duplicate_by_phash_is_refused(self):
        self.test_rows = self.full_test()
        base = 0x8F3C_0A51_77E2_19B4
        self.dev_rows[3]["phash"] = f"{base:016x}"
        self.test_rows[7]["phash"] = f"{base ^ 0xFF:016x}"     # 8 bits apart
        with self.assertRaisesRegex(harness.InputError, "phash within 8 bits"):
            self.run_gate(three_arms())
        self.test_rows[7]["phash"] = f"{base ^ 0x1FF:016x}"    # 9 bits apart
        self.assertEqual(self.run_gate(three_arms())["status"], "PASS")

    def test_two_real_test_images_that_are_one_image_are_refused(self):
        self.test_rows = self.full_test()
        self.test_rows[1]["sha256"] = self.test_rows[2]["sha256"] = "cd" * 32
        with self.assertRaisesRegex(harness.InputError, "not independent"):
            self.run_gate(three_arms())

    def test_a_real_photo_and_its_ai_twin_may_both_be_tested(self):
        self.test_rows = self.full_test()
        self.test_rows[0]["phash"] = self.test_rows[400]["phash"] = "00" * 8
        self.assertEqual(self.run_gate(three_arms())["status"], "PASS")

    def test_dev_split_below_100_plus_100_is_refused(self):
        self.dev_rows = self.dev_rows[:2]
        with self.assertRaisesRegex(harness.InputError, "at least 100 \\+ 100"):
            self.run_gate(three_arms())


class FreezeIsDeclared(Fixture):
    """B3: frozen_at, frozen_by and the classifier identity are required, and the
    freeze must come before the first test prediction."""

    def test_freeze_fields_are_required(self):
        for over in ({"frozen_at": DROP}, {"frozen_at": ""}, {"frozen_by": DROP},
                     {"frozen_by": "  "}, {"frozen_at": "2026-10-20T12:00:00"},
                     {"classifier": {"name": "x", "version": "", "weights_sha256": "h"}},
                     {"classifier": {"name": "x", "version": "1"}},
                     {"phash_distance": DROP}, {"phash_distance": -1}):
            with self.subTest(over=over):
                self.thr_over = dict(over)
                with self.assertRaises(harness.InputError):
                    self.run_gate(three_arms())

    def test_threshold_moved_after_test_scores_is_refused(self):
        # Codex 1: look at the test scores, then move the threshold. The file now
        # has to say when; a freeze dated after the predictions is refused.
        self.thr_over["frozen_at"] = "2026-10-21T10:00:00Z"
        with self.assertRaisesRegex(harness.InputError, "is not before the first test prediction"):
            self.run_gate(three_arms())

    def test_earliest_prediction_decides(self):
        rows = three_arms()
        rows[123]["predicted_at"] = "2026-10-20T11:59:59Z"
        with self.assertRaisesRegex(harness.InputError, "2026-10-20T11:59:59Z"):
            self.run_gate(rows)

    def test_prediction_times_are_required_and_zoned(self):
        for bad in ("", "yesterday", "2026-10-21T09:00:00", "2999-01-01T00:00:00Z"):
            with self.subTest(bad=bad):
                rows = three_arms()
                rows[5]["predicted_at"] = bad
                with self.assertRaises(harness.InputError):
                    self.run_gate(rows)


class RequiredArms(Fixture):
    """B4: original, format-matched and compressed must all be present."""

    def test_each_missing_arm_makes_the_run_invalid(self):
        for drop in (ARM_FORMAT_MATCHED, ARM_COMPRESSED, ARM_ORIGINAL):
            with self.subTest(drop=drop):
                rows = [r for r in three_arms() if r["arm"] != drop]
                res = self.run_gate(rows)
                self.assertEqual(res["status"], "INVALID")
                self.assertIn(f"{drop}: required arm has no predictions", res["reasons"])

    def test_original_only_run_exits_3(self):
        # Codex 3: 400 real, 0 false flags, 120/200 caught, original only, was PASS.
        code = self.quiet_main(self.argv(arm_rows(ARM_ORIGINAL, 400, 0, 200, 120)))
        self.assertEqual(code, 3)

    def test_a_custom_gate_arm_is_required_too(self):
        self.thr_over["gate_arms"] = [ARM_ORIGINAL, "screenshot"]
        res = self.run_gate(three_arms())
        self.assertEqual(res["status"], "INVALID")
        self.assertIn("screenshot: required arm has no predictions", res["reasons"])


class Robustness(Fixture):
    def test_non_finite_scores_are_refused(self):
        for bad in ("nan", "NaN", "inf", "-inf"):
            with self.subTest(bad=bad):
                rows = three_arms()
                rows[0]["score"] = bad    # a real image, verdict real
                with self.assertRaisesRegex(harness.InputError, "not a finite number"):
                    self.run_gate(rows)

    def test_crash_exits_4_not_1(self):
        with mock.patch.object(harness, "arm_metrics", side_effect=RuntimeError("boom")):
            self.assertEqual(self.quiet_main(self.argv(three_arms())), 4)

    def test_unwritable_out_exits_4(self):
        blocker = os.path.join(self.dir, "file")
        pathlib.Path(blocker).write_text("x")
        self.assertEqual(self.quiet_main(self.argv(three_arms(),
                                                   out=os.path.join(blocker, "out"))), 4)

    def test_bad_encoding_and_non_object_threshold_exit_2(self):
        argv = self.argv(three_arms())
        preds = argv[argv.index("--predictions") + 1]
        with open(preds, "ab") as f:
            f.write(b"\xff\xfe\xfa,original,real,0.1\n")
        self.assertEqual(self.quiet_main(argv), 2)
        argv = self.argv(three_arms())
        with open(argv[argv.index("--threshold") + 1], "w") as f:
            f.write("[]")
        self.assertEqual(self.quiet_main(argv), 2)


class GateArmsKeepOriginal(Fixture):
    """Re-review N1: gate_arms can add arms to the gate, never drop original."""

    def test_gate_arms_without_original_are_refused(self):
        # The review's probe: the original arm flags all 400 real images, the
        # format-matched arm is clean, gate_arms ["format_matched"] gave PASS.
        rows = (arm_rows(ARM_ORIGINAL, 400, 400, 200, 120)
                + arm_rows(ARM_FORMAT_MATCHED, 400, 0, 200, 120)
                + arm_rows(ARM_COMPRESSED, 400, 5, 200, 90))
        for arms in ([ARM_FORMAT_MATCHED], [ARM_COMPRESSED], ["screenshot"],
                     [ARM_FORMAT_MATCHED, ARM_COMPRESSED], ["Original"]):
            with self.subTest(gate_arms=arms):
                self.thr_over["gate_arms"] = arms
                with self.assertRaisesRegex(harness.InputError, "must include 'original'"):
                    self.run_gate(rows)
                self.assertEqual(self.quiet_main(self.argv(rows)), 2)
        self.thr_over["gate_arms"] = [ARM_FORMAT_MATCHED, ARM_ORIGINAL]
        self.assertEqual(self.run_gate(rows)["status"], "FAIL")


class OneRowPerSourceGroup(Fixture):
    """DAT-795 7c34d17e, option 1: the bound counts each test row as one independent
    trial, so a source group (author, event, near-duplicate cluster) gives one row."""

    def full_test(self):
        return ([{"item_id": f"r{i:04d}", "label": "real"} for i in range(400)]
                + [{"item_id": f"a{i:04d}", "label": "ai"} for i in range(200)])

    def test_three_rows_from_one_author_are_refused(self):
        self.test_rows = self.full_test()
        for i, spelling in enumerate(("author:ada", "author:ada", " Author:Ada ")):
            self.test_rows[i]["group"] = spelling
        with self.assertRaisesRegex(harness.InputError,
                                    "(?s)1 source group problem.*group 'author:ada' .*: "
                                    "3 test.csv rows \\(r0000, r0001, r0002\\)"):
            self.run_gate(three_arms())
        self.assertEqual(self.quiet_main(self.argv(three_arms())), 2)

    def test_a_real_and_an_ai_row_in_one_group_are_refused(self):
        self.test_rows = self.full_test()
        self.test_rows[0]["group"] = self.test_rows[400]["group"] = "event:fire-2026"
        with self.assertRaisesRegex(harness.InputError,
                                    "(?s)2 test.csv rows.*under both labels"):
            self.run_gate(three_arms())

    def test_group_column_is_required(self):
        self.test_rows = self.full_test()
        p, t, d, s = self.inputs(three_arms())
        write_csv(s, self.test_rows, ["item_id", "label", "sha256"])
        t = self.write_threshold(d, s)
        with self.assertRaisesRegex(harness.InputError, "test split has no group column"):
            harness.evaluate(p, t, d, s)

    def test_empty_group_is_refused(self):
        self.test_rows = self.full_test()
        self.test_rows[9]["group"] = "  "
        with self.assertRaisesRegex(harness.InputError, "r0009.*empty group"):
            self.run_gate(three_arms())

    def test_one_row_per_group_passes(self):
        self.test_rows = self.full_test()
        self.assertEqual(self.run_gate(three_arms())["status"], "PASS")


class GroupIsOneSourceIdentity(Fixture):
    """Re-review R3-1 and R3-2, Nova 69bd8dbb and ade1c711: a group is one source
    across dev, test and both labels, enforced by harness.check_source_groups."""

    def full_test(self):
        return ([{"item_id": f"r{i:04d}", "label": "real"} for i in range(400)]
                + [{"item_id": f"a{i:04d}", "label": "ai"} for i in range(200)])

    def refused(self, pattern):
        with self.assertRaisesRegex(harness.InputError, pattern):
            self.run_gate(three_arms())
        self.assertEqual(self.quiet_main(self.argv(three_arms())), 2)

    def test_a_group_in_both_dev_and_test_is_refused(self):
        # The re-review's probe: this was PASS, exit 0, at 18eaf3c1.
        for dev_spelling in ("dg-real-0", "DG-REAL-0", " Dg-Real-0 "):
            with self.subTest(dev=dev_spelling):
                self.tearDown()
                self.setUp()
                self.test_rows = self.full_test()
                self.dev_rows[0]["group"] = dev_spelling
                self.test_rows[0]["group"] = "dg-real-0"
                self.refused("(?s)group 'dg-real-0'.*in both dev.csv \\(dev-r0\\) "
                             "and test.csv \\(r0000\\)")

    def test_unicode_spellings_of_one_group_are_one_group(self):
        self.test_rows = self.full_test()
        self.dev_rows[0]["group"] = "author:Zo\u00eb"      # composed
        self.test_rows[0]["group"] = "author:Zoe\u0308"    # decomposed
        self.refused("in both dev.csv")

    def test_a_cross_label_collision_is_refused_in_dev_and_across_splits(self):
        self.test_rows = self.full_test()
        self.dev_rows[0]["group"] = self.dev_rows[100]["group"] = "event:fire-2026"
        self.refused("(?s)group 'event:fire-2026': under both labels "
                     "\\(dev dev-r0 real, dev dev-a0 ai\\)")
        self.tearDown()
        self.setUp()
        self.test_rows = self.full_test()
        self.dev_rows[0]["group"] = self.test_rows[400]["group"] = "event:fire-2026"
        self.refused("(?s)in both dev.csv.*under both labels")

    def test_a_label_prefixed_collision_is_refused(self):
        # Writing the label into the group must not turn one source into two.
        for real_g, ai_g in (("real:author:ada", "ai:author:ada"),
                             ("REAL/author:ada", "AI - author:ada"),
                             ("author:ada_real", "author:ada.ai"),
                             ("author:ada", "ai:author:ada")):
            with self.subTest(real=real_g, ai=ai_g):
                self.tearDown()
                self.setUp()
                self.test_rows = self.full_test()
                self.test_rows[0]["group"], self.test_rows[400]["group"] = real_g, ai_g
                self.refused("(?s)group 'author:ada' \\(written .*\\): 2 test.csv rows"
                             ".*under both labels")
        self.tearDown()
        self.setUp()
        self.test_rows = self.full_test()
        self.dev_rows[0]["group"], self.test_rows[400]["group"] = "real-ada", "ai-ada"
        self.refused("(?s)group 'ada'.*in both dev.csv.*under both labels")

    def test_a_group_that_is_only_a_label_is_refused(self):
        for value in ("ai", "Real", "real:"):
            with self.subTest(value=value):
                self.tearDown()
                self.setUp()
                self.test_rows = self.full_test()
                self.test_rows[3]["group"] = value
                self.refused("r0003.*is only a label")

    def test_dev_needs_a_group_column_and_a_group_on_every_row(self):
        self.test_rows = self.full_test()
        p, t, d, s = self.inputs(three_arms())
        write_csv(d, self.dev_rows, ["item_id", "label", "sha256"])
        t = self.write_threshold(d, s)
        with self.assertRaisesRegex(harness.InputError, "dev split has no group column"):
            harness.evaluate(p, t, d, s)
        self.tearDown()
        self.setUp()
        self.test_rows = self.full_test()
        self.dev_rows[4]["group"] = " "
        self.refused("(?s)bad dev split.*dev-r4.*empty group")

    def test_dev_rows_may_share_a_group_within_one_label(self):
        # Dev only tunes the threshold; it is not a bound, so one source may give
        # several dev rows of one label, as long as it stays out of test.
        self.test_rows = self.full_test()
        for r in self.dev_rows[:5]:
            r["group"] = "author:bob"
        self.assertEqual(self.run_gate(three_arms())["status"], "PASS")

    def test_the_check_runs_before_any_prediction_is_read(self):
        self.test_rows = self.full_test()
        self.dev_rows[0]["group"] = self.test_rows[0]["group"] = "dg-real-0"
        rows = three_arms()
        rows[0]["score"] = "not a number"
        with self.assertRaisesRegex(harness.InputError, "source group problem"):
            self.run_gate(rows)


class AssumptionsAreStated(Fixture):
    """Re-review R3-3, Nova 69bd8dbb check 4: every result says the bound is
    conditional on the sampling and independence assumptions."""

    def test_every_status_states_the_assumptions(self):
        for status, rows, code in (("PASS", three_arms(), 0),
                                   ("FAIL", three_arms(fp=9), 1),
                                   ("INVALID", three_arms(n_real=100), 3)):
            with self.subTest(status=status):
                out = os.path.join(self.dir, "out-" + status)
                self.assertEqual(self.quiet_main(self.argv(rows, out)), code)
                res = json.loads(pathlib.Path(out, "result.json").read_text())
                md = pathlib.Path(out, "RESULT.md").read_text()
                self.assertEqual(res["status"], status)
                self.assertEqual(res["assumptions"], harness.ASSUMPTIONS)
                self.assertIn("## Sampling assumptions", md)
                self.assertIn("The bounds hold only under the documented sampling and "
                              "independence assumptions", md)
                self.assertIn("not for all images or for generators it does not contain", md)
                self.assertIn("no source group in both dev.csv and test.csv", md)
                self.assertIn("that the test rows were drawn at random", md)
                self.assertIn("README.md, Independence", md)

    def test_real_and_ai_bounds_are_separate(self):
        res = self.run_gate(three_arms(fp=2, caught=120))
        orig = res["arms"][ARM_ORIGINAL]
        self.assertEqual((orig["false_positive"]["k"], orig["false_positive"]["n"]), (2, 400))
        self.assertEqual((orig["ai_caught"]["k"], orig["ai_caught"]["n"]), (120, 200))
        self.assertIn("never pooled", res["assumptions"]["bounds_by_label"])
        self.assertIn("never pooled", harness.render_markdown(res))


class HashesRequired(Fixture):
    """Re-review N3: overlap is never checked by id alone."""

    def full_test(self):
        return ([{"item_id": f"r{i:04d}", "label": "real"} for i in range(400)]
                + [{"item_id": f"a{i:04d}", "label": "ai"} for i in range(200)])

    def test_a_row_with_neither_hash_is_refused(self):
        for which in ("dev", "test"):
            with self.subTest(which=which):
                self.tearDown()
                self.setUp()
                self.test_rows = self.full_test()
                rows = self.dev_rows if which == "dev" else self.test_rows
                rows[5]["sha256"] = ""
                with self.assertRaisesRegex(harness.InputError,
                                            f"(?s)bad {which} split.*no sha256 and no phash"):
                    self.run_gate(three_arms())

    def test_splits_with_no_hashes_at_all_are_refused(self):
        # The review's probe: no phash and no sha256 anywhere used to PASS silently.
        self.test_rows = self.full_test()
        for r in self.dev_rows + self.test_rows:
            r["sha256"] = ""
        self.assertEqual(self.quiet_main(self.argv(three_arms())), 2)

    def test_mixed_hash_kinds_are_refused(self):
        # Dev rows carry only phash and test rows only sha256: no pair is comparable.
        self.test_rows = self.full_test()
        for i, r in enumerate(self.dev_rows):
            r["sha256"], r["phash"] = "", f"{i:016x}"
        with self.assertRaisesRegex(harness.InputError, "one of the two must be on every row"):
            self.run_gate(three_arms())

    def test_partial_phash_coverage_is_reported_and_warned(self):
        self.test_rows = self.full_test()
        self.test_rows[0]["phash"] = "00" * 8
        res = self.run_gate(three_arms())
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["hash_coverage"], {"rows": 800, "sha256": 800, "phash": 1})
        self.assertTrue(any("phash is on 1 of 800" in w for w in res["warnings"]))
        self.assertIn("sha256 800 and phash 1 of 800", harness.render_markdown(res))


class WriteIsAllOrNothing(Fixture):
    """Re-review N2: a failed write leaves no result.json behind."""

    def test_failed_write_leaves_no_result_json(self):
        out = os.path.join(self.dir, "out")
        os.makedirs(os.path.join(out, "RESULT.md"))     # opening RESULT.md fails
        stale = os.path.join(out, "result.json")
        pathlib.Path(stale).write_text('{"status": "PASS"}\n')
        self.assertEqual(self.quiet_main(self.argv(three_arms(), out=out)), 4)
        self.assertEqual(sorted(os.listdir(out)), ["RESULT.md"])
        self.assertTrue(os.path.isdir(os.path.join(out, "RESULT.md")))

    def test_failure_moving_result_json_removes_both_files(self):
        out = os.path.join(self.dir, "out")
        real_replace = os.replace

        def replace(src, dst):
            if dst.endswith("result.json"):
                raise OSError("disk full")
            return real_replace(src, dst)
        with mock.patch.object(harness.os, "replace", replace):
            self.assertEqual(self.quiet_main(self.argv(three_arms(), out=out)), 4)
        self.assertEqual(os.listdir(out), [])

    def test_a_new_run_replaces_an_old_result(self):
        out = os.path.join(self.dir, "out")
        self.assertEqual(self.quiet_main(self.argv(three_arms(fp=4), out=out)), 1)
        self.assertEqual(self.quiet_main(self.argv(three_arms(), out=out)), 0)
        self.assertEqual(sorted(os.listdir(out)), ["RESULT.md", "result.json"])
        with open(os.path.join(out, "result.json")) as f:
            self.assertEqual(json.load(f)["status"], "PASS")


class Offline(Fixture):
    def test_runs_with_the_network_cut(self):
        def refuse(*a, **k):
            raise AssertionError("network use attempted")
        rows = three_arms()
        with mock.patch.object(socket, "socket", refuse), \
                mock.patch.object(socket, "create_connection", refuse), \
                mock.patch.object(socket, "getaddrinfo", refuse):
            self.assertEqual(self.run_gate(rows)["status"], "PASS")

    def test_no_network_process_or_model_imports(self):
        banned = {"socket", "urllib", "http", "requests", "httpx", "subprocess",
                  "ssl", "ftplib", "smtplib", "torch", "transformers", "anthropic",
                  "openai", "multiprocessing", "asyncio"}
        for path in sorted(PKG.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    with self.subTest(file=path.name, name=name):
                        self.assertNotIn(name.split(".")[0], banned)


if __name__ == "__main__":
    unittest.main()
