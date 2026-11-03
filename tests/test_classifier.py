import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

from secondlook.calibrate import main as calibrate_main, pick_threshold, rates
from secondlook.ladder import decide
from secondlook.rungs import classifier

HAVE_WEIGHTS = classifier.weights_path().exists()
needs_model = pytest.mark.skipif(not HAVE_WEIGHTS, reason="weights not fetched (python -m secondlook.rungs.classifier --fetch)")


def _frozen(t, weights=classifier.MODEL_SHA256):
    return {"schema": "second-look-threshold/2", "threshold": t, "ai_if": "score>=threshold",
            "abstain": {"rule": "none"}, "classifier": {"name": "x", "version": "y", "weights_sha256": weights},
            "dev_split_sha256": "d" * 64, "test_split_sha256": "e" * 64, "phash_distance": 8,
            "gate_arms": ["original"], "frozen_at": "2026-10-27T09:00:00Z", "frozen_by": "test"}


def _threshold_file(tmp_path, t, weights=classifier.MODEL_SHA256, name="thr.json", **change):
    cfg = _frozen(t, weights)
    for k, v in change.items():
        if v is _DROP:
            cfg.pop(k)
        else:
            cfg[k] = v
    p = tmp_path / name
    p.write_text(json.dumps(cfg))
    return p


_DROP = object()


def _sha(p):
    return classifier.sha256_file(Path(p))


def _l4(tmp_path, n_real=100, n_ai=100, n_test=10):
    """A frozen L4 folder (LOCK.json over the six locked files) and an L2 split of it."""
    from secondlook.calibrate import L4_LOCKED
    l4 = tmp_path / "L4"
    (l4 / "scripts").mkdir(parents=True)
    for name in L4_LOCKED:
        (l4 / name).write_text(f"locked {name}\n")
    (l4 / "LOCK.json").write_text(json.dumps({"sha256": {n: _sha(l4 / n) for n in L4_LOCKED}}))
    dev = [(f"r{i}", "real") for i in range(n_real)] + [(f"a{i}", "ai") for i in range(n_ai)]
    test = [(f"t{i}", "real" if i % 2 else "ai") for i in range(n_test)]
    paths = {}
    for name, rows in (("dev", dev), ("test", test)):
        f = tmp_path / f"{name}.csv"
        f.write_text("item_id,label,path\n" + "".join(f"{i},{lab},{i}.png\n" for i, lab in rows))
        paths[name] = f
    split = tmp_path / "SPLIT.json"
    split.write_text(json.dumps({"dev": {"sha256": _sha(paths["dev"])}, "test": {"sha256": _sha(paths["test"])},
                                 "params": {"phash_distance": 8}}))
    return {"l4": l4, "split": split, "dev": paths["dev"], "test": paths["test"], "dev_rows": dev,
            "test_rows": test}


def _scores(tmp_path, rows, arm="original", name=None, real_scale=1.0, file_sha=None):
    """Dev real scores (i % 100) / 100 * real_scale, AI scores from 0.5 up."""
    f = tmp_path / (name or f"dev_scores_{arm}.csv")
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "arm", "label", "score", "seconds", "device", "file_sha256"])
        for i, (item, lab) in enumerate(rows):
            s = (i % 100) / 100 * real_scale if lab == "real" else 0.5 + (i % 100) / 200
            w.writerow([item, arm, lab, repr(s), "0.1", "mps", file_sha or hashlib.sha256(f"{arm}/{item}".encode()).hexdigest()])
    return f


def _freeze(fx, scores, out):
    """Freeze from these scores files. One file alone is the original arm; a good
    format_matched file is added so the refusal tested is the one in that file."""
    if not isinstance(scores, (list, tuple)):
        scores = [scores, _scores(fx["l4"].parent, fx["dev_rows"], arm="format_matched")]
    return calibrate_main(["freeze", "--scores", *map(str, scores), "--split-json", str(fx["split"]),
                           "--dev-csv", str(fx["dev"]), "--test-csv", str(fx["test"]),
                           "--l4-dir", str(fx["l4"]), "--frozen-by", "test", "--out", str(out)])


def _image(tmp_path, name="g.png"):
    from PIL import Image
    im = Image.new("RGB", (600, 500))
    im.putdata([(x % 256, y % 256, (x * y) % 256) for y in range(500) for x in range(600)])
    p = tmp_path / name
    im.save(p)
    return p


# --- the freeze rule -------------------------------------------------------------------------

def test_pick_threshold_allows_floor_of_two_percent():
    real = [i / 114 for i in range(114)]
    t = pick_threshold(real, 0.02)
    assert sum(s >= t for s in real) == 2          # floor(0.02 * 114) = 2
    assert sum(s >= real[-3] for s in real) == 3   # one step lower would allow 3


def test_pick_threshold_ties_stay_below():
    real = [0.1] * 50 + [0.9] * 50
    t = pick_threshold(real, 0.02)
    assert sum(s >= t for s in real) == 0 and t > 0.9


def test_pick_threshold_refuses_empty_and_nan():
    with pytest.raises(ValueError):
        pick_threshold([])
    with pytest.raises(ValueError):
        pick_threshold([0.1, math.nan])


def test_rates():
    r = rates([("real", 0.1), ("real", 0.8), ("ai", 0.9), ("ai", 0.2)], 0.5)
    assert r == {"n_real": 2, "n_ai": 2, "false_positives": 1, "true_positives": 1, "fpr": 0.5, "tpr": 0.5}


def test_freeze_writes_a_file_the_l2_harness_accepts(tmp_path):
    fx = _l4(tmp_path)
    out = tmp_path / "threshold.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 0
    cfg = json.loads(out.read_text())
    assert cfg["gate_arms"] == ["original", "format_matched"]
    assert cfg["arms"]["original"]["dev_metrics"]["false_positives"] == 2
    assert cfg["dev_split_sha256"] == _sha(fx["dev"])
    assert cfg["test_split_sha256"] == _sha(fx["test"]) and cfg["l4_lock_sha256"] == _sha(fx["l4"] / "LOCK.json")
    assert classifier.load_threshold(out)["threshold"] == cfg["threshold"]
    from gate.harness import load_threshold as l2_load
    l2 = l2_load(str(out))
    assert l2["threshold"] == cfg["threshold"] and l2["gate_arms"] == ["original", "format_matched"]


# --- per-arm calibration (the project lead ruling, DAT-849 ec3ad415) ----------------------------------

def test_freeze_picks_a_threshold_per_arm_and_freezes_the_higher(tmp_path):
    fx = _l4(tmp_path)
    orig = _scores(tmp_path, fx["dev_rows"], arm="original")
    fm = _scores(tmp_path, fx["dev_rows"], arm="format_matched", real_scale=0.5)  # real scores halved
    out = tmp_path / "threshold.json"
    assert _freeze(fx, [fm, orig], out) == 0  # file order does not matter
    cfg = json.loads(out.read_text())
    t_orig, t_fm = cfg["arms"]["original"]["threshold"], cfg["arms"]["format_matched"]["threshold"]
    assert t_orig == pick_threshold([(i % 100) / 100 for i in range(100)])
    assert t_fm == pick_threshold([(i % 100) / 100 * 0.5 for i in range(100)])
    assert t_orig > t_fm and cfg["threshold"] == t_orig and cfg["threshold_from_arms"] == ["original"]
    assert cfg["arms"]["original"]["dev_scores_sha256"] == _sha(orig)
    assert cfg["arms"]["format_matched"]["dev_scores_sha256"] == _sha(fm)
    for arm in ("original", "format_matched"):  # at the frozen threshold, FPR <= 2% in both arms
        assert cfg["arms"][arm]["dev_metrics"]["fpr"] <= 0.02
    assert cfg["arms"]["format_matched"]["dev_metrics"]["false_positives"] == 0
    assert cfg["arms"]["format_matched"]["dev_metrics_at_arm_threshold"]["false_positives"] == 2
    assert classifier.load_threshold(out)["threshold"] == cfg["threshold"]

    out2 = tmp_path / "threshold2.json"  # the other way round: format_matched sets the number
    orig_half = _scores(tmp_path, fx["dev_rows"], arm="original", name="orig_half.csv", real_scale=0.5)
    fm_full = _scores(tmp_path, fx["dev_rows"], arm="format_matched", name="fm_full.csv")
    assert _freeze(fx, [orig_half, fm_full], out2) == 0
    cfg2 = json.loads(out2.read_text())
    assert cfg2["threshold"] == t_orig and cfg2["threshold_from_arms"] == ["format_matched"]


# --- the training-data statement (L4 PROTOCOL.md F, DAT-848) ------------------------------------

def test_freeze_writes_the_training_data_statement(tmp_path):
    from secondlook.calibrate import TRAINING_DATA
    fx = _l4(tmp_path)
    out = tmp_path / "threshold.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 0
    cfg = json.loads(out.read_text())
    assert cfg["training_data_statement"] == TRAINING_DATA["statement"]
    assert cfg["training_data_overlap"] == "same_generator_family"
    assert cfg["training_data_sources"] == TRAINING_DATA["sources"]
    assert isinstance(cfg["training_data_statement"], str) and cfg["training_data_statement"].strip()  # S7 gate_run


def test_training_data_statement_is_for_the_pinned_weights_and_names_the_overlap():
    from secondlook.calibrate import TRAINING_DATA, training_data
    assert training_data(classifier.MODEL_SHA256) is TRAINING_DATA
    stmt = TRAINING_DATA["statement"]
    for must in ("CompVis/stable-diffusion-v1-1", "runwayml/stable-diffusion-v1-5", "DiffusionDB", "Commons",
                 "not a held-out-generator test", "Not checked"):
        assert must in stmt
    assert "\u2014" not in stmt and "\u2013" not in stmt  # no em or en dashes
    assert all(s.startswith("https://") for s in TRAINING_DATA["sources"])


@pytest.mark.parametrize("change", [{"statement": ""}, {"statement": "  "}, {"for_weights_sha256": "0" * 64},
                                    {"overlap": "some"}, {"sources": []}])
def test_freeze_refuses_without_a_statement_for_these_weights(tmp_path, monkeypatch, capsys, change):
    from secondlook import calibrate
    monkeypatch.setattr(calibrate, "TRAINING_DATA", {**calibrate.TRAINING_DATA, **change})
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 2 and not out.exists()
    assert "training" in capsys.readouterr().err


def test_freeze_refuses_a_missing_arm(tmp_path, capsys):
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    for arm, other in (("original", "format_matched"), ("format_matched", "original")):
        assert _freeze(fx, [_scores(tmp_path, fx["dev_rows"], arm=arm)], out) == 2 and not out.exists()
        assert f"no dev scores for gate arm(s) ['{other}']" in capsys.readouterr().err


def test_freeze_refuses_a_repeated_or_unknown_arm(tmp_path):
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    a = _scores(tmp_path, fx["dev_rows"], arm="original", name="a.csv")
    b = _scores(tmp_path, fx["dev_rows"], arm="original", name="b.csv")
    fm = _scores(tmp_path, fx["dev_rows"], arm="format_matched")
    assert _freeze(fx, [a, b, fm], out) == 2 and not out.exists()
    c = _scores(tmp_path, fx["dev_rows"], arm="compressed_q70_1080")
    assert _freeze(fx, [a, fm, c], out) == 2 and not out.exists()


def test_freeze_refuses_mixed_arms_in_one_file(tmp_path):
    fx = _l4(tmp_path)
    mixed = _scores(tmp_path, fx["dev_rows"], arm="original")
    mixed.write_text(mixed.read_text().replace("r0,original,", "r0,format_matched,"))
    out = tmp_path / "t.json"
    assert _freeze(fx, mixed, out) == 2 and not out.exists()


def test_freeze_refuses_an_arm_whose_ids_are_not_dev(tmp_path):
    fx = _l4(tmp_path)
    orig = _scores(tmp_path, fx["dev_rows"], arm="original")
    out = tmp_path / "t.json"
    for rows in (fx["dev_rows"][1:], fx["dev_rows"] + [fx["test_rows"][0]], fx["dev_rows"] + [("x9", "ai")]):
        fm = _scores(tmp_path, rows, arm="format_matched")
        assert _freeze(fx, [orig, fm], out) == 2 and not out.exists()


def test_freeze_refuses_a_derived_arm_that_scored_the_originals(tmp_path):
    fx = _l4(tmp_path)
    orig = _scores(tmp_path, fx["dev_rows"], arm="original", file_sha="f" * 64)
    fm = _scores(tmp_path, fx["dev_rows"], arm="format_matched", file_sha="f" * 64)
    out = tmp_path / "t.json"
    assert _freeze(fx, [orig, fm], out) == 2 and not out.exists()


def test_freeze_refuses_an_old_scores_file_without_arm(tmp_path):
    fx = _l4(tmp_path)
    old = tmp_path / "old.csv"
    with open(old, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "label", "score", "seconds", "device"])
        for item, lab in fx["dev_rows"]:
            w.writerow([item, lab, "0.1", "0.1", "mps"])
    out = tmp_path / "t.json"
    assert _freeze(fx, old, out) == 2 and not out.exists()


def test_freeze_refuses_a_bad_file_sha256(tmp_path):
    fx = _l4(tmp_path)
    scores = _scores(tmp_path, fx["dev_rows"], file_sha="f" * 64)
    scores.write_text(scores.read_text().replace(",mps," + "f" * 64 + "\n", ",mps,nothex\n", 1))
    out = tmp_path / "t.json"
    assert _freeze(fx, scores, out) == 2 and not out.exists()


def _derived(tmp_path, fx, arm="format_matched", skip=()):
    root = tmp_path / "images-derived"
    (root / arm).mkdir(parents=True, exist_ok=True)
    for item, _ in fx["dev_rows"]:
        if item not in skip:
            (root / arm / f"{item}.jpg").write_bytes(f"derived {arm} {item}".encode())
    return root


def _score_cmd(fx, arm, out, *extra):
    return calibrate_main(["score", "--arm", arm, "--split", str(fx["dev"]), "--split-json", str(fx["split"]),
                           "--l4-dir", str(fx["l4"]), "--out", str(out), *extra])


def test_score_format_matched_reads_the_derived_files(tmp_path, monkeypatch):
    fx = _l4(tmp_path)
    root = _derived(tmp_path, fx)
    seen = []
    monkeypatch.setattr(classifier, "score_images", lambda paths: seen.extend(paths) or [0.25])
    out = tmp_path / "fm.csv"
    assert _score_cmd(fx, "format_matched", out, "--derived-root", str(root)) == 0
    assert seen == [str(root / "format_matched" / f"{i}.jpg") for i, _ in fx["dev_rows"]]
    rows = list(csv.DictReader(open(out, newline="")))
    assert [r["arm"] for r in rows] == ["format_matched"] * len(fx["dev_rows"])
    assert rows[0]["file_sha256"] == _sha(root / "format_matched" / f"{fx['dev_rows'][0][0]}.jpg")


def test_score_original_reads_the_dev_csv_files(tmp_path, monkeypatch):
    fx = _l4(tmp_path)
    for item, _ in fx["dev_rows"]:
        (tmp_path / f"{item}.png").write_bytes(f"original {item}".encode())
    seen = []
    monkeypatch.setattr(classifier, "score_images", lambda paths: seen.extend(paths) or [0.25])
    out = tmp_path / "o.csv"
    assert _score_cmd(fx, "original", out, "--root", str(tmp_path)) == 0
    assert seen == [str(tmp_path / f"{i}.png") for i, _ in fx["dev_rows"]]
    rows = list(csv.DictReader(open(out, newline="")))
    assert {r["arm"] for r in rows} == {"original"} and rows[0]["file_sha256"] == _sha(tmp_path / "r0.png")


def test_score_refuses_a_derived_arm_without_its_files(tmp_path, monkeypatch):
    fx = _l4(tmp_path)
    monkeypatch.setattr(classifier, "score_images", lambda paths: [0.25])
    out = tmp_path / "fm.csv"
    assert _score_cmd(fx, "format_matched", out) == 2 and not out.exists()  # no --derived-root
    root = _derived(tmp_path, fx, skip={"a7"})
    assert _score_cmd(fx, "format_matched", out, "--derived-root", str(root)) == 2
    assert not out.exists() and not list(tmp_path.glob("*.part"))
    with pytest.raises(SystemExit):  # not a gate arm
        _score_cmd(fx, "compressed_q70_1080", out, "--derived-root", str(root))
    with pytest.raises(SystemExit):  # --arm is required
        calibrate_main(["score", "--split", str(fx["dev"]), "--split-json", str(fx["split"]),
                        "--l4-dir", str(fx["l4"]), "--out", str(out)])


def test_derived_path_refuses_an_id_that_leaves_the_folder(tmp_path):
    from secondlook.calibrate import Refused, derived_path
    assert derived_path(tmp_path, "format_matched", "r1") == tmp_path / "format_matched" / "r1.jpg"
    for bad in ("../x", "a/b", "..", ".", ""):
        with pytest.raises(Refused):
            derived_path(tmp_path, "format_matched", bad)


# --- finding 1: only the frozen L4 dev split -------------------------------------------------

def test_freeze_refuses_without_split_or_lock(tmp_path):
    fx = _l4(tmp_path)
    scores = _scores(tmp_path, fx["dev_rows"])
    out = tmp_path / "t.json"
    with pytest.raises(SystemExit):  # --split-json, --dev-csv, --test-csv and --l4-dir are required
        calibrate_main(["freeze", "--scores", str(scores), "--frozen-by", "t", "--out", str(out)])
    (fx["l4"] / "LOCK.json").unlink()
    assert _freeze(fx, scores, out) == 2 and not out.exists()


def test_freeze_refuses_when_l4_changed_after_lock(tmp_path):
    fx = _l4(tmp_path)
    (fx["l4"] / "manifest-real.csv").write_text("edited after the lock\n")
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 2 and not out.exists()


def test_freeze_refuses_dev_csv_not_in_split(tmp_path):
    fx = _l4(tmp_path)
    fx["dev"].write_text(fx["dev"].read_text() + "r999,real,r999.png\n")
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 2 and not out.exists()


def test_freeze_refuses_test_scores(tmp_path):
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["test_rows"]), out) == 2 and not out.exists()


def test_freeze_refuses_a_test_id_among_dev_rows(tmp_path):
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"] + [fx["test_rows"][0]]), out) == 2 and not out.exists()


def test_freeze_refuses_a_missing_row(tmp_path):
    fx = _l4(tmp_path)
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"][1:]), out) == 2 and not out.exists()


def test_freeze_refuses_a_flipped_label(tmp_path):
    fx = _l4(tmp_path)
    rows = list(fx["dev_rows"])
    rows[0] = (rows[0][0], "ai")
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, rows), out) == 2 and not out.exists()


def test_freeze_refuses_a_bad_score(tmp_path):
    fx = _l4(tmp_path)
    scores = _scores(tmp_path, fx["dev_rows"])
    scores.write_text(scores.read_text().replace("r0,original,real,0.0,", "r0,original,real,nan,"))
    out = tmp_path / "t.json"
    assert _freeze(fx, scores, out) == 2 and not out.exists()


def test_freeze_refuses_below_harness_minimum(tmp_path):
    fx = _l4(tmp_path, n_real=3, n_ai=3)
    out = tmp_path / "t.json"
    assert _freeze(fx, _scores(tmp_path, fx["dev_rows"]), out) == 2 and not out.exists()


def test_score_refuses_the_test_split_and_unknown_csvs(tmp_path):
    fx = _l4(tmp_path)
    other = tmp_path / "other.csv"
    other.write_text("item_id,label,path\nx,real,x.png\n")
    for split in (fx["test"], other):
        out = tmp_path / "s.csv"
        assert calibrate_main(["score", "--arm", "original", "--split", str(split), "--split-json", str(fx["split"]),
                               "--l4-dir", str(fx["l4"]), "--root", str(tmp_path), "--out", str(out)]) == 2
        assert not out.exists()


def test_score_leaves_no_partial_file(tmp_path, monkeypatch):
    fx = _l4(tmp_path)
    calls = []

    def fake(paths):
        calls.append(paths)
        if len(calls) == 3:
            raise OSError("unreadable dev image")
        return [0.5]

    monkeypatch.setattr(classifier, "score_images", fake)
    out = tmp_path / "s.csv"
    for item, _ in fx["dev_rows"]:
        (tmp_path / f"{item}.png").write_bytes(item.encode())
    assert calibrate_main(["score", "--arm", "original", "--split", str(fx["dev"]), "--split-json", str(fx["split"]),
                           "--l4-dir", str(fx["l4"]), "--root", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists() and not list(tmp_path.glob("*.part")) and len(calls) == 3


# --- the threshold file ----------------------------------------------------------------------

def test_threshold_missing_or_for_other_weights_is_ignored(tmp_path):
    assert classifier.load_threshold(tmp_path / "nope.json") is None
    assert classifier.load_threshold(_threshold_file(tmp_path, 0.5, weights="0" * 64)) is None
    assert classifier.load_threshold(_threshold_file(tmp_path, 0.5))["threshold"] == 0.5


# --- findings 3 and 4: a malformed or unfrozen file is ignored, never raised -----------------

BAD_THRESHOLD_FILES = {
    "list": "[]",
    "null": "null",
    "classifier_null": json.dumps({**_frozen(0.5), "classifier": None}),
    "classifier_list": json.dumps({**_frozen(0.5), "classifier": []}),
    "not_json": "{",
    "neg_inf": json.dumps(_frozen(0.5)).replace("0.5", "-Infinity"),
    "nan": json.dumps(_frozen(0.5)).replace("0.5", "NaN"),
    "bool": json.dumps({**_frozen(0.5), "threshold": True}),
    "string": json.dumps({**_frozen(0.5), "threshold": "0.5"}),
    "above_one": json.dumps(_frozen(1.5)),
    "below_zero": json.dumps(_frozen(-0.1)),
    "score_band": json.dumps({**_frozen(0.5), "abstain": {"rule": "score_band", "low": 0.4, "high": 0.6}}),
    **{f"no_{k}": json.dumps({x: v for x, v in _frozen(0.3).items() if x != k})
       for k in ("dev_split_sha256", "test_split_sha256", "frozen_at", "frozen_by", "phash_distance")},
    **{f"empty_{k}": json.dumps({**_frozen(0.3), k: " "}) for k in ("dev_split_sha256", "frozen_by")},
    "frozen_at_no_zone": json.dumps({**_frozen(0.3), "frozen_at": "2026-10-28T09:00:00"}),
    "frozen_at_future": json.dumps({**_frozen(0.3), "frozen_at": "2999-01-01T00:00:00Z"}),
    "frozen_at_garbage": json.dumps({**_frozen(0.3), "frozen_at": "yesterday"}),
}


@pytest.mark.parametrize("name", sorted(BAD_THRESHOLD_FILES))
def test_bad_threshold_file_is_ignored(tmp_path, name):
    p = tmp_path / f"{name}.json"
    p.write_text(BAD_THRESHOLD_FILES[name])
    assert classifier.load_threshold(p) is None


@pytest.mark.parametrize("name", sorted(BAD_THRESHOLD_FILES))
def test_check_never_raises_and_never_flags_with_a_bad_threshold_file(tmp_path, monkeypatch, name):
    p = tmp_path / f"{name}.json"
    p.write_text(BAD_THRESHOLD_FILES[name])
    monkeypatch.setenv("SECONDLOOK_CLASSIFIER_THRESHOLD", str(p))
    monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
    monkeypatch.setattr(classifier, "score_images", lambda paths: [0.99])
    monkeypatch.setattr(classifier, "weights_path", lambda: p)  # exists, so check() goes on to score
    [e] = classifier.check(str(tmp_path / "any.png"))
    assert e.raw["above_threshold"] is False and e.raw["threshold"] is None
    assert decide([e])[0] == "cant_tell"


def test_check_never_raises_if_load_threshold_does(tmp_path, monkeypatch):
    monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
    monkeypatch.setattr(classifier, "score_images", lambda paths: [0.99])
    monkeypatch.setattr(classifier, "weights_path", lambda: tmp_path)

    def boom(path=None):
        raise AttributeError("boom")

    monkeypatch.setattr(classifier, "load_threshold", boom)
    [e] = classifier.check("x.png")
    assert e.raw["above_threshold"] is False


# --- the rung --------------------------------------------------------------------------------

def test_check_never_raises_on_bad_input(tmp_path):
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"not an image")
    assert classifier.check(str(bad)) == []
    assert classifier.check(str(tmp_path / "missing.jpg")) == []


# --- finding 2: extreme images are refused before any resize --------------------------------

@pytest.mark.parametrize("size", [(30000, 3), (3, 30000), (100000, 1), (1, 100000), (900, 100)])
def test_extreme_aspect_is_refused_before_decoding(tmp_path, monkeypatch, size):
    from PIL import Image
    p = tmp_path / "strip.png"
    Image.new("RGB", size, (1, 2, 3)).save(p)
    if size == (30000, 3):
        assert p.stat().st_size < 1000  # the reviewer's 356-byte file
    monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
    monkeypatch.setattr(classifier, "_transform", lambda: (lambda im: pytest.fail("resize reached")))
    with pytest.raises(classifier.CantAnalyse):
        classifier.score_images([str(p)])
    monkeypatch.setattr(classifier, "weights_path", lambda: p)
    assert classifier.check(str(p)) == []


def test_too_many_pixels_is_refused_before_decoding(monkeypatch):
    with pytest.raises(classifier.CantAnalyse):
        classifier._check_size(12000, 9000)        # 108 M pixels
    classifier._check_size(8000, 6000)             # a 48 MP phone photo is fine
    classifier._check_size(440 * 8, 440)           # exactly 8:1 is allowed


def test_fifo_is_refused_without_blocking(tmp_path, monkeypatch):
    import os
    fifo = tmp_path / "pipe.jpg"
    os.mkfifo(fifo)
    monkeypatch.setattr(classifier, "load_model", lambda: (None, "cpu"))
    monkeypatch.setattr(classifier, "weights_path", lambda: fifo)
    assert classifier.check(str(fifo)) == []


@needs_model
def test_thirty_thousand_by_three_png_through_check_is_fast(tmp_path):
    import resource
    import time
    from PIL import Image
    p = tmp_path / "strip.png"
    Image.new("RGB", (30000, 3), (1, 2, 3)).save(p)
    classifier.load_model()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    assert classifier.check(str(p)) == []
    assert time.perf_counter() - t0 < 1.0
    assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before < 200 * 1024 * 1024  # bytes on macOS


def test_check_without_weights_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("SECONDLOOK_COMMFOR_WEIGHTS", str(tmp_path / "none.safetensors"))
    assert classifier.check(str(_image(tmp_path))) == []


@needs_model
def test_check_reports_score_and_no_verdict_without_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("SECONDLOOK_CLASSIFIER_THRESHOLD", str(tmp_path / "none.json"))
    [e] = classifier.check(str(_image(tmp_path)))
    assert e.rung == "classifier" and e.strength == "signal"
    assert 0.0 <= e.raw["score"] <= 1.0 and e.raw["above_threshold"] is False
    assert e.raw["weights_sha256"] == classifier.MODEL_SHA256 and e.does_not_prove
    assert decide([e])[0] == "cant_tell"


@needs_model
def test_check_above_and_below_frozen_threshold(tmp_path, monkeypatch):
    img = str(_image(tmp_path))
    gate = tmp_path / "gate.json"

    def passed(thr):  # DAT-850: a concern needs a PASS for this exact threshold file
        gate.write_text(json.dumps({"schema": "second-look-gate-status/1", "result": "PASS", "run_id": "t",
                                    "threshold_sha256": _sha(thr), "results": "docs/RESULTS-test.md"}))
        return thr

    monkeypatch.setenv("SECONDLOOK_CLASSIFIER_GATE", str(gate))
    monkeypatch.setenv("SECONDLOOK_CLASSIFIER_THRESHOLD", str(passed(_threshold_file(tmp_path, 0.0))))
    [hi] = classifier.check(img)
    assert hi.raw["above_threshold"] is True and decide([hi])[0] == "concerns"
    assert hi.raw["threshold_file_sha256"]
    monkeypatch.setenv("SECONDLOOK_CLASSIFIER_THRESHOLD", str(passed(_threshold_file(tmp_path, 1.0, name="hi.json"))))
    [lo] = classifier.check(img)
    assert lo.raw["above_threshold"] is False and lo.strength == "none" and decide([lo])[0] == "cant_tell"


@needs_model
def test_handles_alpha_and_grey_images(tmp_path):
    from PIL import Image
    Image.new("RGBA", (300, 200), (10, 20, 30, 128)).save(tmp_path / "a.png")
    Image.new("L", (300, 200), 77).save(tmp_path / "l.jpg")
    for name in ("a.png", "l.jpg"):
        assert len(classifier.check(str(tmp_path / name))) == 1


def test_wording_never_claims_a_verdict():
    text = " ".join([classifier.PROVES, classifier.DOES_NOT_PROVE, classifier.DOES_NOT_PROVE_LOW]).lower()
    for banned in ("this is real", "detects ai", "accurate", "genuine", "authentic"):
        assert banned not in text
