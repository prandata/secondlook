"""DAT-850: the gate gates the product. A classifier score raises a concern only with a frozen threshold AND a
PASS gate status for that exact threshold file's sha256 (secondlook/rungs/classifier_gate.json). Anything else
(no status, FAIL, INVALID, a status for another threshold, an unreadable or malformed file) is score only."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from secondlook import batch, ladder, receipt
from secondlook.explain import TEMPLATES, banned_words, explain
from secondlook.rungs import classifier
from secondlook.types import Result

ROOT = Path(__file__).resolve().parent.parent
COMMITTED_THRESHOLD = ROOT / "secondlook" / "rungs" / "classifier_threshold.json"
COMMITTED_GATE = ROOT / "secondlook" / "rungs" / "classifier_gate.json"
V2_THRESHOLD_SHA = "686e0e6379be42271501c7016e4c16b1cdfef759904818f6382c5f37479eb53c"


def _frozen(t):
    return {"schema": "second-look-threshold/2", "threshold": t, "ai_if": "score>=threshold",
            "abstain": {"rule": "none"},
            "classifier": {"name": "x", "version": "y", "weights_sha256": classifier.MODEL_SHA256},
            "dev_split_sha256": "d" * 64, "test_split_sha256": "e" * 64, "phash_distance": 8,
            "gate_arms": ["original"], "frozen_at": "2026-10-27T09:00:00Z", "frozen_by": "test"}


def _threshold(tmp_path, t=0.5, name="thr.json"):
    p = tmp_path / name
    p.write_text(json.dumps(_frozen(t)))
    return p


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _status(sha, result="PASS", **change):
    st = {"schema": "second-look-gate-status/1", "result": result, "run_id": "gate-test",
          "threshold_sha256": sha, "results": "docs/RESULTS-test.md", "reason": "test status"}
    st.update(change)
    return st


def _gate(tmp_path, body, name="gate.json"):
    p = tmp_path / name
    p.write_text(body if isinstance(body, str) else json.dumps(body))
    return p


@pytest.fixture
def scored(monkeypatch, tmp_path):
    """check() on any path, with the model replaced by a fixed score; the threshold and gate files by env."""
    def run(score, threshold, gate):
        monkeypatch.setenv("SECONDLOOK_CLASSIFIER_THRESHOLD", str(threshold))
        monkeypatch.setenv("SECONDLOOK_CLASSIFIER_GATE", str(gate))
        monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
        monkeypatch.setattr(classifier, "score_images", lambda paths: [score])
        monkeypatch.setattr(classifier, "weights_path", lambda: threshold)  # exists, so check() goes on to score
        [e] = classifier.check(str(tmp_path / "any.png"))
        return e
    return run


# --- the committed files ----------------------------------------------------------------------

def test_the_committed_threshold_is_the_one_results_v2_pins_byte_for_byte():
    assert _sha(COMMITTED_THRESHOLD) == V2_THRESHOLD_SHA
    assert V2_THRESHOLD_SHA in (ROOT / "docs" / "RESULTS-v2.md").read_text(encoding="utf-8")


def test_the_committed_gate_status_is_v2s_fail_for_that_threshold():
    st = json.loads(COMMITTED_GATE.read_text(encoding="utf-8"))
    assert st["schema"] == classifier.GATE_SCHEMA and st["result"] == "FAIL"
    assert st["threshold_sha256"] == V2_THRESHOLD_SHA and st["run_id"] == "gate-20261029-040012"
    assert (ROOT / st["results"]).is_file() and (ROOT / st["run_record"]).is_file()
    results = (ROOT / st["results"]).read_text(encoding="utf-8")
    assert "Gate verdict from the harness: **FAIL**" in results and f"| Run id | {st['run_id']} |" in results
    assert f"- {st['reason']}" in results  # the harness's own reason line, word for word
    m = re.search(r"original: false-positive upper bound ([\d.]+)% is above (\d+)%", results)
    assert st["failed_arm"] == "original"
    assert st["false_positive_upper_bound"] == pytest.approx(float(m.group(1)) / 100, abs=1e-9)
    assert st["false_positive_limit"] == pytest.approx(float(m.group(2)) / 100, abs=1e-9)
    record = json.loads((ROOT / st["run_record"]).read_text(encoding="utf-8"))
    assert record["run_id"] == st["run_id"] and record["threshold_sha256"] == V2_THRESHOLD_SHA
    assert record["harness_exit_code"] == 1


def test_with_the_committed_files_no_score_raises_a_concern(monkeypatch, tmp_path):
    monkeypatch.delenv("SECONDLOOK_CLASSIFIER_THRESHOLD", raising=False)
    monkeypatch.delenv("SECONDLOOK_CLASSIFIER_GATE", raising=False)
    monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
    monkeypatch.setattr(classifier, "weights_path", lambda: COMMITTED_THRESHOLD)
    for score in (0.4319957792758942, 0.71, 0.999):
        monkeypatch.setattr(classifier, "score_images", lambda paths, s=score: [s])
        [e] = classifier.check(str(tmp_path / "any.png"))
        assert e.raw["above_threshold"] is True and e.raw["raises_concern"] is False
        assert e.raw["gate_result"] == "FAIL" and e.raw["threshold_file_sha256"] == V2_THRESHOLD_SHA
        assert ladder.decide([e])[0] == "cant_tell"


# --- PASS for this exact threshold: a concern is possible --------------------------------------

def test_pass_for_this_threshold_lets_a_score_at_or_above_it_raise_a_concern(scored, tmp_path):
    thr = _threshold(tmp_path)
    gate = _gate(tmp_path, _status(_sha(thr), "PASS"))
    hi = scored(0.9, thr, gate)
    assert hi.raw["raises_concern"] is True and hi.raw["gate_result"] == "PASS"
    assert hi.raw["gate"]["run_id"] == "gate-test" and hi.raw["gate"]["gate_file_sha256"] == _sha(gate)
    assert ladder.decide([hi])[0] == "concerns" and hi.strength == "signal"
    lo = scored(0.1, thr, gate)
    assert lo.raw["raises_concern"] is False and lo.strength == "none" and ladder.decide([lo])[0] == "cant_tell"


# --- FAIL: no concern, and the card says why ---------------------------------------------------

def test_fail_means_no_concern_and_the_card_says_why(scored, tmp_path):
    thr = _threshold(tmp_path, 0.4319957792758942)
    gate = _gate(tmp_path, _status(_sha(thr), "FAIL", results="docs/RESULTS-v2.md", failed_arm="original",
                                   false_positive_upper_bound=0.09718, false_positive_limit=0.02))
    e = scored(0.71, thr, gate)
    assert e.raw["above_threshold"] is True and e.raw["raises_concern"] is False
    assert ladder.decide([e])[0] == "cant_tell" and e.strength == "signal"
    assert e.finding == "Community Forensics detector score 0.710\u00a0out\u00a0of\u00a01. This score raises no concern on its own."
    assert e.proves == ("Nothing on its own. A threshold of 0.432 was frozen in advance, and our test of it failed. "
                        "In that test, the upper bound on false flags for photos in their original format was 9.7%, "
                        "above the 2%\u00a0limit. See docs/RESULTS-v2.md.")
    assert e.does_not_prove == classifier.DOES_NOT_PROVE
    assert "\u2014" not in e.finding + e.proves
    low = scored(0.1, thr, gate)   # below the failed cut-off: folded with the checks that found nothing
    assert low.strength == "none" and low.raw["raises_concern"] is False
    # DAT-850 (a reviewer's FIX 4 on PR #33): a low score keeps a short Proves line, not the false-flag story.
    assert low.proves == LOW_PROVES
    assert low.does_not_prove == classifier.DOES_NOT_PROVE_LOW


LOW_PROVES = "Only that this detector did not raise a concern, and its threshold has not passed its test."


@pytest.mark.parametrize("kind", ["fail", "invalid", "none"])
def test_a_low_score_without_a_pass_keeps_a_short_proves_line(scored, tmp_path, kind):
    thr = _threshold(tmp_path, 0.4319957792758942)
    st = {"fail": _status(_sha(thr), "FAIL", results="docs/RESULTS-v2.md", failed_arm="original",
                          false_positive_upper_bound=0.09718, false_positive_limit=0.02),
          "invalid": _status(_sha(thr), "INVALID"), "none": None}[kind]
    low = scored(0.033, thr, _gate(tmp_path, st) if st else tmp_path / "no-gate.json")
    assert low.raw["above_threshold"] is False and low.raw["raises_concern"] is False and low.strength == "none"
    assert low.proves == LOW_PROVES
    for story in ("9.7%", "2%", "docs/RESULTS", "frozen in advance", "invalid", "on record"):
        assert story not in low.proves, story
    ex = explain(Result("cant_tell", [low], "s", "v"))
    assert ex.summary_template == "cant_tell", ex.summary   # a low score is not named in the summary


@pytest.mark.parametrize("kind,key", [
    ("fail", "cant_tell.gate_failed"), ("invalid", "cant_tell.untested"), ("missing", "cant_tell.untested"),
    ("pass_for_another_threshold", "cant_tell.untested"), ("malformed", "cant_tell.untested"),
])
def test_a_high_score_without_a_usable_pass_is_named_in_the_summary_end_to_end(scored, tmp_path, kind, key):
    """Codex and a reviewer, non-blocking on PR #39: from the real rung's card through the ladder to the
    summary, for every gate that is not a PASS for this threshold (the rung was covered, the summary was not)."""
    thr = _threshold(tmp_path, 0.4319957792758942)
    gate = {"fail": lambda: _gate(tmp_path, _status(_sha(thr), "FAIL")),
            "invalid": lambda: _gate(tmp_path, _status(_sha(thr), "INVALID")),
            "missing": lambda: tmp_path / "no-gate.json",
            "pass_for_another_threshold": lambda: _gate(tmp_path, _status("f" * 64, "PASS")),
            "malformed": lambda: _gate(tmp_path, "{not json")}[kind]()
    e = scored(0.71, thr, gate)
    assert e.raw["above_threshold"] is True and e.raw["raises_concern"] is False
    answer, line = ladder.decide([e])
    assert answer == "cant_tell"
    ex = explain(Result(answer, [e], line, "v"))
    assert ex.summary_template == key, ex.summary
    assert "A detector scored it 0.71 out of 1, at or above our cut-off of 0.432." in ex.summary
    assert ex.summary.endswith("so we do not count the score as evidence either\u00a0way.")


def test_fail_without_numbers_still_names_the_results_file(scored, tmp_path):
    thr = _threshold(tmp_path)
    e = scored(0.9, thr, _gate(tmp_path, _status(_sha(thr), "FAIL", false_positive_upper_bound="9.7")))
    assert e.raw["raises_concern"] is False
    assert "our test of it failed. See docs/RESULTS-test.md." in e.proves


def test_the_plain_language_line_for_a_failed_cutoff(scored, tmp_path):
    thr = _threshold(tmp_path, 0.4319957792758942)
    e = scored(0.71, thr, _gate(tmp_path, _status(_sha(thr), "FAIL")))
    line = explain(Result("cant_tell", [e], "s", "v")).per_card[0]
    assert line.template == "classifier.gate_failed"
    assert line.sentence == ("A detector scored this 0.71 out of 1, but our cut-off of 0.432 failed its test, so it "
                             "is no concern.")
    assert not banned_words(line.sentence)


# --- anything else: score only -----------------------------------------------------------------

SCORE_ONLY = {
    "missing": None,
    "garbled": "{",
    "not_an_object": "[]",
    "empty": "",
    "wrong_schema": "SCHEMA",
    "unknown_result": "MAYBE",
    "lowercase_pass": "pass",
    "invalid": "INVALID",
    "other_threshold": "OTHER",
    "no_threshold_sha": "NOSHA",
    "short_threshold_sha": "SHORTSHA",
    "no_run_id": "NORUN",
    "no_results": "NORESULTS",
}


def _score_only_gate(tmp_path, kind, sha):
    if kind == "missing":
        return tmp_path / "none.json"
    body = {"garbled": "{", "not_an_object": "[]", "empty": ""}.get(kind)
    if body is not None:
        return _gate(tmp_path, body)
    st = {"wrong_schema": _status(sha, schema="second-look-gate-status/0"),
          "unknown_result": _status(sha, "MAYBE"), "lowercase_pass": _status(sha, "pass"),
          "invalid": _status(sha, "INVALID"), "other_threshold": _status("0" * 64),
          "no_threshold_sha": _status(None), "short_threshold_sha": _status(sha[:63]),
          "no_run_id": _status(sha, run_id=" "), "no_results": _status(sha, results=None)}[kind]
    return _gate(tmp_path, st)


@pytest.mark.parametrize("kind", sorted(SCORE_ONLY))
def test_anything_but_a_pass_for_this_threshold_is_score_only(scored, tmp_path, kind):
    thr = _threshold(tmp_path)
    e = scored(0.99, thr, _score_only_gate(tmp_path, kind, _sha(thr)))
    assert e.raw["above_threshold"] is True and e.raw["raises_concern"] is False
    assert ladder.decide([e])[0] == "cant_tell"
    assert e.finding.endswith("This score raises no concern on its own.")
    assert e.raw["gate_result"] == ("INVALID" if kind == "invalid" else None)
    if kind == "invalid":
        assert "its test was invalid (see docs/RESULTS-test.md)." in e.proves
    else:
        assert "no passing test of it is on record" in e.proves
    line = explain(Result("cant_tell", [e], "s", "v")).per_card[0]
    assert line.template == "classifier.untested", line


def test_a_gate_path_that_is_a_folder_does_not_raise(scored, tmp_path):
    thr = _threshold(tmp_path)
    (tmp_path / "gatedir").mkdir()
    e = scored(0.99, thr, tmp_path / "gatedir")
    assert e.raw["raises_concern"] is False and ladder.decide([e])[0] == "cant_tell"


def test_check_never_raises_if_load_gate_does(scored, tmp_path, monkeypatch):
    thr = _threshold(tmp_path)

    def boom(*a, **k):
        raise AttributeError("boom")

    monkeypatch.setattr(classifier, "load_gate", boom)
    e = scored(0.99, thr, _gate(tmp_path, _status(_sha(thr))))
    assert e.raw["raises_concern"] is False and ladder.decide([e])[0] == "cant_tell"


def test_a_pass_recorded_for_the_threshold_before_an_edit_no_longer_counts(scored, tmp_path):
    thr = _threshold(tmp_path, 0.5)
    gate = _gate(tmp_path, _status(_sha(thr), "PASS"))
    thr.write_text(json.dumps(_frozen(0.3)))   # a lower threshold, never tested
    e = scored(0.4, thr, gate)
    assert e.raw["above_threshold"] is True and e.raw["raises_concern"] is False
    assert ladder.decide([e])[0] == "cant_tell"


# --- the ladder reads both flags, as booleans ---------------------------------------------------

@pytest.mark.parametrize("raw, concern", [
    ({"above_threshold": True, "raises_concern": True}, True),
    ({"above_threshold": True}, False),
    ({"above_threshold": True, "raises_concern": False}, False),
    ({"above_threshold": True, "raises_concern": "true"}, False),
    ({"above_threshold": True, "raises_concern": 1}, False),
    ({"above_threshold": False, "raises_concern": True}, False),
    ({"above_threshold": "yes", "raises_concern": True}, False),
])
def test_the_ladder_needs_above_threshold_and_raises_concern(raw, concern):
    from secondlook.types import Evidence
    e = Evidence(rung="classifier", finding="f", proves="p", does_not_prove="d", strength="signal", raw=raw)
    assert ladder._is_concern(e) is concern
    assert ladder.decide([e])[0] == ("concerns" if concern else "cant_tell")


# --- receipts and batch output follow the same rule --------------------------------------------

def test_receipt_and_batch_row_with_a_failed_gate(scored, tmp_path, monkeypatch):
    thr = _threshold(tmp_path)
    gate = _gate(tmp_path, _status(_sha(thr), "FAIL"))
    e = scored(0.9, thr, gate)   # sets the env and the model stand-in for the calls below
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    rungs = [("classifier", classifier.check)]
    result = ladder.run(str(img), rungs=rungs)
    assert result.answer == "cant_tell"
    rec = receipt.build(result, img)
    assert rec["answer"] == "cant_tell"
    [card] = [c for c in rec["evidence"] if c["rung"] == "classifier"]
    assert card["raw"]["gate_result"] == "FAIL" and card["raw"]["raises_concern"] is False
    assert card["finding"] == e.finding and "raises no concern on its own" in card["finding"]
    monkeypatch.setitem(batch._W, "rungs", rungs)
    row = batch._process((0, "x.png", str(img), str(tmp_path / "out"), False))
    assert row["answer"] == "cant_tell" and row["reasons"] == []


def test_receipt_and_batch_row_with_a_passing_gate(scored, tmp_path, monkeypatch):
    thr = _threshold(tmp_path)
    e = scored(0.9, thr, _gate(tmp_path, _status(_sha(thr), "PASS")))
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    rungs = [("classifier", classifier.check)]
    result = ladder.run(str(img), rungs=rungs)
    assert result.answer == "concerns" and receipt.build(result, img)["answer"] == "concerns"
    monkeypatch.setitem(batch._W, "rungs", rungs)
    row = batch._process((0, "x.png", str(img), str(tmp_path / "out"), False))
    assert row["answer"] == "concerns" and row["reasons"] == [e.finding]


def test_new_templates_are_in_the_library():
    assert {"classifier.gate_failed", "classifier.untested"} <= set(TEMPLATES)


def test_the_page_keeps_a_docs_path_whole_on_the_proves_line():
    # PR #33 design check: "docs/RESULTS-v2.md" broke after its hyphen on desktop. The page wraps each docs/*.md
    # path in a nowrap span, built from text nodes (no innerHTML).
    js = (Path(__file__).resolve().parents[1] / "secondlook" / "web" / "static" / "app.js").read_text()
    assert 'withWholePaths("dd", ev.proves' in js and 'style.whiteSpace = "nowrap"' in js
    helper = js[js.index("const withWhole = "):js.index("// One row per clue")]   # A272: the shared helper
    assert "innerHTML" not in helper and "createTextNode" in helper
