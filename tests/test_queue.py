"""Tests for the desk triage queue (J4, DAT-1367): fitting, banding, ranking, receipts, the frozen file, the CLI.

Every image is made at test time with Pillow. The detector is replaced by a fake rung that returns a score
chosen per file name, so no model is loaded. Nothing here starts a browser or needs headless-run.
"""
import csv
import hashlib
import importlib.util
import json
import math
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from secondlook import cli, queue  # noqa: E402
from secondlook.rungs import classifier, watermark  # noqa: E402
from secondlook.types import Evidence  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
XMP_AI = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
    b'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia">'
    b"<Iptc4xmpExt:AISystemUsed>ExampleGen</Iptc4xmpExt:AISystemUsed>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
)
BANDS = {"t_hi": 0.9, "t_lo": 0.01, "auto_ok": True, "fine_ok": True}
FAST = ("c2pa", "iptc", "exif", "classifier")


def picture(seed: int, size=(96, 64)) -> Image.Image:
    rnd = random.Random(seed)
    small = Image.new("L", (9, 8))
    small.putdata([rnd.randrange(256) for _ in range(72)])
    return small.resize(size, Image.Resampling.NEAREST).convert("RGB")


# --- fitting --------------------------------------------------------------------------------------

def test_allowed_errors_matches_prereg_counts():
    assert queue.allowed_errors(169) == 1 and queue.allowed_errors(174) == 1
    assert queue.allowed_errors(99) == 0 and queue.allowed_errors(200) == 2 and queue.allowed_errors(314) == 3


def test_t_hi_largest_catch_with_at_most_max_false_reals():
    real = [0.1, 0.2, 0.95, 0.5]
    ai = [0.6, 0.7, 0.96, 0.99, 0.3]
    # One real may be flagged: t = 0.6 flags real 0.95 only and catches 4 AI; t = 0.5 would flag two reals.
    assert queue.fit_t_hi(real, ai, max_false=1) == 0.6
    # None may be flagged: t must be above 0.95; 0.96 catches two.
    assert queue.fit_t_hi(real, ai, max_false=0) == 0.96


def test_t_hi_ties_go_to_the_highest_threshold_and_infinity_when_nothing_fits():
    # 0.6 and 0.65 (a real score) catch the same AI: the higher one wins.
    assert queue.fit_t_hi([0.1, 0.65], [0.7, 0.8], max_false=0) == 0.7
    assert queue.fit_t_hi([0.99, 0.99], [0.5], max_false=1) == math.inf


def test_t_lo_largest_threshold_with_at_most_max_missed_ai_below():
    real = [0.0001, 0.002, 0.05]
    ai = [0.001, 0.003, 0.5]
    # At most one AI below t: t = 0.003 has one (0.001) below; 0.05 would have two.
    assert queue.fit_t_lo(real, ai, max_missed=1) == 0.003
    assert queue.fit_t_lo(real, ai, max_missed=0) == 0.001
    assert queue.fit_t_lo([], [], 1) == -math.inf


def test_fit_bands_reports_dev_error_rates_within_one_percent():
    rnd = random.Random(7)
    real = [rnd.random() * 0.5 for _ in range(169)]
    ai = [0.3 + rnd.random() * 0.7 for _ in range(174)]
    b = queue.fit_bands(real, ai)
    assert b["dev"]["real_auto_decided"] <= 1 and b["dev"]["ai_likely_fine"] <= 1
    assert b["dev_real_flag_rate"] <= 0.01 and b["dev_ai_clear_rate"] <= 0.01
    assert b["t_lo"] <= b["t_hi"]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_eval(tmp_path, held_score: float):
    """A tiny split.csv and scores.csv in the scale-eval layout: 4 dev rows and 2 held-out rows."""
    fit = _load_script("fit_queue_bands")
    rows = [("d1", "real", "dev", 0.01), ("d2", "real", "dev", 0.2), ("d3", "ai", "dev", 0.8),
            ("d4", "ai", "dev", 0.97), ("h1", "real", "heldout", held_score), ("h2", "ai", "heldout", held_score)]
    with (tmp_path / "split.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sha256", "label", "split"])
        for sha, lab, sp, _ in rows:
            w.writerow([sha, lab, sp])
    cols = ["sha256", "label", "licence"]
    for form in queue.FORMS:
        cols += [f"{form}_version", f"{form}_classifier_score", f"{form}_answer"]
    with (tmp_path / "scores.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for sha, lab, _, s in rows:
            row = [sha, lab, "CC BY 4.0"]
            for _form in queue.FORMS:
                row += [fit.CODE_COMMIT, s, "cant_tell"]
            w.writerow(row)
    return fit


def test_fit_reads_dev_rows_only(tmp_path):
    fit = _write_eval(tmp_path, held_score=0.5)
    a, seen = fit.dev_scores(tmp_path / "scores.csv", tmp_path / "split.csv")
    fit2 = _write_eval(tmp_path, held_score=0.999)  # change only the held-out scores
    b, _ = fit2.dev_scores(tmp_path / "scores.csv", tmp_path / "split.csv")
    assert a == b
    assert sorted(a["sq512"]["real"]) == [0.01, 0.2] and sorted(a["sq512"]["ai"]) == [0.8, 0.97]
    assert seen == {"dev_rows_in_split": 4, "dev_rows_scored": 4, "held_out_rows_ignored": 2}


# --- the frozen file ------------------------------------------------------------------------------

def test_committed_bands_match_their_sha256_and_stay_within_one_percent_on_dev():
    pinned = (ROOT / "secondlook" / "queue_bands.json.sha256").read_text().split()[0]
    assert hashlib.sha256((ROOT / "secondlook" / "queue_bands.json").read_bytes()).hexdigest() == pinned
    assert queue.BANDS_SHA256 == pinned   # the digest pinned in code is the committed file's
    doc = json.loads((ROOT / "secondlook" / "queue_bands.json").read_text())
    assert doc["fitted_on"]["licence"] == "CC BY 4.0" and doc["fitted_on"]["split"].startswith("dev")
    assert doc["fitted_on"]["rows"]["dev_rows_scored"] == 343
    for form in queue.FORMS:
        b = queue.load_bands(form=form)
        assert b["auto_ok"] and b["fine_ok"] and b["t_lo"] < b["t_hi"] and b["sha256"] == pinned
        assert doc["forms"][form]["dev"]["real"] == 169 and doc["forms"][form]["dev"]["ai"] == 174
        assert doc["forms"][form]["dev"]["real_auto_decided"] <= 1 and doc["forms"][form]["dev"]["ai_likely_fine"] <= 1


def test_the_failed_classifier_gate_is_untouched():
    gate = json.loads((ROOT / "secondlook" / "rungs" / "classifier_gate.json").read_text())
    assert gate["result"] == "FAIL"
    thr = json.loads((ROOT / "secondlook" / "rungs" / "classifier_threshold.json").read_text())
    assert thr["threshold"] == 0.4319957792758942


def _copy_bands(tmp_path) -> Path:
    for name in ("queue_bands.json", "queue_bands.json.sha256"):
        shutil.copy(ROOT / "secondlook" / name, tmp_path / name)
    return tmp_path / "queue_bands.json"


def test_an_altered_bands_file_is_refused(tmp_path):
    path = _copy_bands(tmp_path)
    doc = json.loads(path.read_text())
    doc["forms"]["original"]["t_hi"] = 0.5
    path.write_text(json.dumps(doc))
    with pytest.raises(queue.BandsError, match="does not match its sha256"):
        queue.load_bands(path)
    (tmp_path / "queue_bands.json.sha256").unlink()
    with pytest.raises(queue.BandsError, match="can't be read"):
        queue.load_bands(path)


def test_editing_the_frozen_file_and_its_sidecar_together_is_refused(monkeypatch, fake_detector, folder, tmp_path):
    """DAT-1404 (F3): the sidecar is not enough, because it can be edited with the file. The digest pinned in
    code refuses the pair. Point the frozen path at a copy so the committed file is never touched."""
    path = _copy_bands(tmp_path)
    monkeypatch.setattr(queue, "BANDS_PATH", path)
    assert queue.load_bands(form="original")["sha256"] == queue.BANDS_SHA256   # an untouched copy still loads
    doc = json.loads(path.read_text())
    doc["forms"]["original"]["t_hi"] = 0.5
    new_digest = queue.write_bands(doc, path)   # file and sidecar now agree with each other
    assert (tmp_path / "queue_bands.json.sha256").read_text().split()[0] == new_digest != queue.BANDS_SHA256
    with pytest.raises(queue.BandsError, match="pinned in secondlook/queue.py"):
        queue.load_bands(form="original")
    with pytest.raises(queue.BandsError, match="pinned in secondlook/queue.py"):
        queue.load_bands(path, "sq512")   # naming the frozen path explicitly gets the same refusal
    with pytest.raises(queue.BandsError, match="pinned in secondlook/queue.py"):
        queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST)
    assert not (tmp_path / "o").exists()


def test_a_band_over_one_percent_on_dev_is_switched_off(tmp_path):
    path = _copy_bands(tmp_path)
    doc = json.loads(path.read_text())
    doc["forms"]["original"]["dev_real_flag_rate"] = 0.02
    queue.write_bands(doc, path)
    b = queue.load_bands(path, "original")
    assert not b["auto_ok"] and b["fine_ok"]
    band, _ = queue.band_for("cant_tell", 0.9999, b)
    assert band == queue.UNCERTAIN


# --- banding --------------------------------------------------------------------------------------

@pytest.mark.parametrize("answer,score,band", [
    ("signed_ai", 0.0, queue.AUTO),          # f = 1 wins over any score
    ("concerns", None, queue.AUTO),
    ("cant_tell", 0.95, queue.AUTO),
    ("cant_tell", 0.9, queue.AUTO),          # at t_hi counts
    ("cant_tell", 0.5, queue.UNCERTAIN),
    ("cant_tell", 0.01, queue.UNCERTAIN),    # at t_lo is not below it
    ("cant_tell", 0.005, queue.FINE),
    ("cant_tell", None, queue.UNCERTAIN),    # no score: never cleared
    ("signed_camera", 0.005, queue.FINE),
    ("signed_camera", 0.95, queue.UNCERTAIN),  # a signed camera record and a high score disagree
])
def test_band_for(answer, score, band):
    got, reason = queue.band_for(answer, score, BANDS)
    assert got == band and reason


def test_reason_shows_the_score_with_enough_digits_for_the_comparison():
    """Scores used to print with 3 decimals: 0.0000004 against the sq512 t_lo 0.000235 read "0.000 is below 0.000235"."""
    sq = queue.load_bands(form="sq512")
    assert sq["t_lo"] == 0.000235
    band, reason = queue.band_for("cant_tell", 0.0000004, sq)
    assert band == queue.FINE and "Detector score 0.0000004 is below 0.000235 " in reason
    assert "0.000 " not in reason
    # A score and a threshold that differ past the sixth figure still print differently.
    shown, limit = queue.show_pair(0.1234567, 0.1234568)
    assert shown != limit and (shown, limit) == ("0.1234567", "0.1234568")
    assert queue.show_pair(0.9, 0.9) == ("0.9", "0.9")
    # Plain notation, no exponent, and the middle band says the score the same way.
    assert queue.show_pair(1e-9, 0.000235) == ("0.000000001", "0.000235")
    assert "Detector score 0.4 is between" in queue.band_for("cant_tell", 0.4, BANDS)[1]
    # Whatever the two numbers are, the text keeps their order.
    for score, t in [(0.0, 0.000235), (0.0002349999, 0.000235), (0.99999995, 0.99999996), (2.5e-7, 2.6e-7)]:
        a, b = queue.show_pair(score, t)
        assert (float(a) < float(b)) == (score < t) and (a == b) == (score == t), (a, b)


def test_likely_fine_never_says_real():
    _, reason = queue.band_for("cant_tell", 0.001, BANDS)
    assert "Not proof that the image is real" in reason
    assert "not proof" in queue.BAND_LABELS[queue.FINE]


def test_overlapping_thresholds_send_the_overlap_to_a_person():
    b = {"t_hi": 0.2, "t_lo": 0.4, "auto_ok": True, "fine_ok": True}
    assert queue.band_for("cant_tell", 0.3, b)[0] == queue.UNCERTAIN
    assert queue.band_for("cant_tell", 0.5, b)[0] == queue.AUTO
    assert queue.band_for("cant_tell", 0.1, b)[0] == queue.FINE


# --- ranking --------------------------------------------------------------------------------------

def test_rank_orders_by_band_then_score_then_file():
    rows = [
        {"file": "f.jpg", "band": queue.FINE, "detector_score": 0.001},
        {"file": "a.jpg", "band": queue.AUTO, "detector_score": 0.95},
        {"file": "u2.jpg", "band": queue.UNCERTAIN, "detector_score": 0.4},
        {"file": "u0.jpg", "band": queue.UNCERTAIN, "detector_score": None},
        {"file": "u1.jpg", "band": queue.UNCERTAIN, "detector_score": 0.7},
        {"file": "u3.jpg", "band": queue.UNCERTAIN, "detector_score": 0.4},
        {"file": "b.jpg", "band": queue.AUTO, "detector_score": 0.99},
    ]
    ranked = queue.rank(rows)
    assert [r["file"] for r in ranked] == ["u1.jpg", "u2.jpg", "u3.jpg", "u0.jpg", "b.jpg", "a.jpg", "f.jpg"]
    assert [r["rank"] for r in ranked] == list(range(1, 8))


# --- a run over a folder --------------------------------------------------------------------------

SCORES = {"high.png": 0.995, "mid.jpg": 0.4, "low.png": 0.00001, "mid2.webp": 0.6, "labelled.jpg": 0.0001}


@pytest.fixture
def fake_detector(monkeypatch, tmp_path):
    """The classifier rung returns SCORES[name]; no model is loaded. The watermark rung finds nothing."""
    def check(path):
        s = SCORES.get(Path(path).name)
        if s is None:
            return []
        return [Evidence(rung="classifier", finding=f"score {s}", proves="test", does_not_prove="test",
                         strength="none", raw={"score": s, "seconds": 0.1})]
    monkeypatch.setattr(classifier, "check", check)
    monkeypatch.setattr(classifier, "weights_path", lambda: tmp_path / "no-weights")
    monkeypatch.setattr(watermark, "check", lambda path: [])
    return check


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "desk"
    (d / "sub").mkdir(parents=True)
    picture(1).save(d / "high.png")
    picture(2).save(d / "mid.jpg", quality=92)
    picture(3).save(d / "low.png")
    picture(4).save(d / "sub" / "mid2.webp")
    picture(5).save(d / "labelled.jpg", xmp=XMP_AI)   # an IPTC AI label: the ladder says concerns
    picture(6).save(d / "unscored.png")                # no detector score
    (d / "notes.txt").write_text("not an image")
    return d


def test_queue_bands_ranks_and_writes_receipts(fake_detector, folder, tmp_path):
    out = tmp_path / "out"
    q = queue.run_queue(folder, out, jobs=1, rung_names=FAST)
    b = queue.load_bands(form="original")
    assert q["counts"] == {"images": 6, "checked": 6, "skipped": 0, "failed": 0,
                           queue.UNCERTAIN: 3, queue.AUTO: 2, queue.FINE: 1}
    order = [(r["file"], r["band"]) for r in q["queue"]]
    assert order == [("sub/mid2.webp", queue.UNCERTAIN), ("mid.jpg", queue.UNCERTAIN),
                     ("unscored.png", queue.UNCERTAIN), ("high.png", queue.AUTO),
                     ("labelled.jpg", queue.AUTO), ("low.png", queue.FINE)]
    assert SCORES["high.png"] >= b["t_hi"] and SCORES["low.png"] < b["t_lo"]
    assert q["bands"]["sha256"] == b["sha256"] and q["bands"]["form"] == "original"

    by_file = {r["file"]: r for r in q["queue"]}
    assert by_file["labelled.jpg"]["answer"] == "concerns"
    for r in q["queue"]:
        rc_path = out / r["receipt_json"]
        data = rc_path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == r["receipt_sha256"]
        assert (out / (r["receipt_json"] + ".sha256")).read_text().split()[0] == r["receipt_sha256"]
        rc = json.loads(data)
        assert rc["schema"] == "second-look-receipt/1"
        assert rc["file"]["sha256"] == r["sha256"]
        assert rc["answer"] == r["answer"]             # the ladder's answer, unchanged
        assert "band" not in rc and "queue" not in json.dumps(rc["inputs"])
        assert r["receipt_pdf"] is None and r["receipt_error"] is None

    with (out / "queue.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["file"] for r in rows] == [r["file"] for r in q["queue"]]
    assert rows[0]["rank"] == "1" and rows[2]["detector_score"] == ""
    on_disk = json.loads((out / "queue.json").read_text())
    assert on_disk["schema"] == queue.SCHEMA and on_disk["queue"] == q["queue"]


PINNED = "2026-11-02T00:00:00Z"


def test_queue_files_are_deterministic(fake_detector, folder, tmp_path):
    """Same folder, code, bands file and run time: the same bytes. The receipts' created_at is the one input
    that changes between seconds, and queue.json holds each receipt's sha256, so the run time is pinned."""
    queue.run_queue(folder, tmp_path / "a", jobs=1, rung_names=FAST, run_time=PINNED)
    queue.run_queue(folder, tmp_path / "b", jobs=1, rung_names=FAST, run_time=PINNED)
    for name in ("queue.json", "queue.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    for ra, rb in zip(sorted((tmp_path / "a" / "receipts").glob("*.json")), sorted((tmp_path / "b" / "receipts").glob("*.json"))):
        assert ra.read_bytes() == rb.read_bytes()


def test_a_different_run_time_changes_the_receipt_hashes_and_nothing_else(fake_detector, folder, tmp_path):
    """The pin is not a mask: the receipts record the time they were given, and queue.json follows them."""
    a = queue.run_queue(folder, tmp_path / "a", jobs=1, rung_names=FAST, run_time=PINNED)
    b = queue.run_queue(folder, tmp_path / "b", jobs=1, rung_names=FAST, run_time="2026-11-02T00:00:01Z")
    for path in (tmp_path / "a" / "receipts").glob("*.json"):
        assert json.loads(path.read_text())["created_at"] == PINNED
    assert a["run_time"] == PINNED and a["run"]["run_time"] == PINNED
    strip = lambda q: [{k: v for k, v in r.items() if k != "receipt_sha256"} for r in q["queue"]]
    assert strip(a) == strip(b)
    assert [r["receipt_sha256"] for r in a["queue"]] != [r["receipt_sha256"] for r in b["queue"]]
    assert all(r["receipt_sha256"] for r in a["queue"])


def test_without_a_run_time_receipts_record_the_real_time(fake_detector, folder, tmp_path):
    q = queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST)
    assert q["run_time"] is None
    created = json.loads((tmp_path / "o" / q["queue"][0]["receipt_json"]).read_text())["created_at"]
    assert created != PINNED and abs(datetime.now(timezone.utc) - datetime.strptime(
        created, queue.RUN_TIME_FORMAT).replace(tzinfo=timezone.utc)).total_seconds() < 120


@pytest.mark.parametrize("bad", ["", "now", "2026-11-02", "2026-11-02T00:00:00", "2026-11-02T00:00:00.5Z",
                                 "2026-11-02T00:00:00+00:00", "2026-13-02T00:00:00Z", "2026-1-2T0:0:0Z", 20261102])
def test_run_time_must_be_a_receipt_timestamp(fake_detector, folder, tmp_path, bad):
    with pytest.raises(ValueError, match="run_time"):
        queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST, run_time=bad)
    assert not (tmp_path / "o").exists()


def test_sq512_form_uses_its_own_thresholds(fake_detector, folder, tmp_path):
    q = queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST, form="sq512")
    b = queue.load_bands(form="sq512")
    assert q["bands"]["form"] == "sq512" and q["bands"]["t_hi"] == b["t_hi"]


def test_queue_refuses_before_checking_anything_when_bands_are_altered(fake_detector, folder, tmp_path):
    path = _copy_bands(tmp_path)
    path.write_text(path.read_text().replace('"max_error": 0.01', '"max_error": 0.5'))
    with pytest.raises(queue.BandsError):
        queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST, bands_path=path)
    assert not (tmp_path / "o").exists()


# --- the CLI --------------------------------------------------------------------------------------

def test_cli_queue(fake_detector, folder, tmp_path, capsys):
    out = tmp_path / "cli-out"
    assert cli.main(["queue", str(folder), "--out", str(out), "--jobs", "1"]) == 0
    text = capsys.readouterr().out
    assert "Queued 6 of 6 images" in text and "Needs a person: 3" in text and "classifier gate stays FAIL" in text
    assert (out / "queue.csv").exists() and (out / "queue.json").exists()
    assert len(list((out / "receipts").glob("*.json"))) == 6


def test_cli_queue_errors(fake_detector, tmp_path, capsys):
    assert cli.main(["queue", str(tmp_path / "missing"), "--out", str(tmp_path / "o")]) == 2
    assert cli.main(["queue", str(tmp_path), "--out", str(tmp_path / "o"), "--jobs", "0"]) == 2
    assert "secondlook queue:" in capsys.readouterr().err


def test_no_browser_is_needed(monkeypatch, fake_detector, folder, tmp_path):
    """The queue runs with headless-run not on PATH (DAT-1325)."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert shutil.which("headless-run") is None
    q = queue.run_queue(folder, tmp_path / "o", jobs=1, rung_names=FAST)
    assert q["counts"]["checked"] == 6
