"""The one-command gate run (DAT-852), end to end on a synthetic fixture, offline.

The fixture proves wiring, order of operations, refusals and wording. It is not a measurement.
"""
import csv
import datetime as dt
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from secondlook import gate_baseline, gate_run
from tests import gate_fixture as fx

REPO = Path(__file__).resolve().parent.parent
FORBIDDEN = ("detects AI images", "works on Meta AI", "pooled", "under 2%", "content-based", "content based",
             "may be called", "is not called", "approximate range")
BA_LABEL = "conservative, at least 95%, conditional on per-class binomial unit sampling"
ALLOWED = re.compile(
    r"On (\d+) synthetic real-class images and (\d+) synthetic AI-class images, observed false-alarm rate "
    r"(\d+)/\1 \([\d.]+%\), one-sided 95% upper bound [\d.]+%; observed catch rate (\d+)/\2 \([\d.]+%\), "
    r"lower bound [\d.]+%; arm: (\w+)\.")


def run(fixture, *extra, rungs=None, run_id="fx1"):
    return gate_run.main(fixture.argv(*extra, run_id=run_id), rungs=rungs)


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def ids(split, label, n, start=0):
    return [f"{split}-{label}-{i}" for i in range(start, start + n)]


# ------------------------------------------------------------------ baseline and label units

def test_jpeg_quality_estimate_round_trips():
    for q in (30, 50, 70, 90, 95):
        b = io.BytesIO()
        Image.effect_noise((64, 64), 40).convert("RGB").save(b, "JPEG", quality=q)
        assert gate_baseline.estimate_jpeg_quality(Image.open(io.BytesIO(b.getvalue())).quantization[0]) == q


def test_label_boundaries_follow_the_ruling():
    lab = gate_baseline.label_arm
    assert lab(0.75, 0.84)["label"] == "shortcut-exposed" and not lab(0.75, 0.84)["exceeds_baseline_by_10"]
    assert lab(0.75, 0.86)["exceeds_baseline_by_10"]              # beats it by at least 10 points
    assert lab(0.75, 0.85)["exceeds_baseline_by_10"]              # exactly 10 points counts
    assert lab(0.7499, 0.9)["label"] == "not shortcut-exposed"
    assert lab(None, 0.9)["label"] == "not computed"
    assert "does not exceed this specified shortcut baseline by at least 10 points" in lab(0.9, 0.9)["text"]
    assert "exceeds this specified shortcut baseline by at least 10 points" in lab(0.75, 0.86)["text"]
    for args in ((0.75, 0.84), (0.75, 0.9), (0.5, 0.9), (None, 0.9)):
        assert "content" not in lab(*args)["text"]              # never "content-based" (c014e175 B4)


def test_label_text_never_carries_a_bare_balanced_accuracy():
    """c014e175: a balanced accuracy is printed with its interval and the interval's label, or not at all."""
    lab = gate_baseline.label_arm
    bi, di = [0.78, 0.86], [0.9, 0.97]
    for b, d in ((0.80, 0.95), (0.80, 0.85), (0.60, 0.90)):
        t = lab(b, d, bi, di)["text"]
        assert t.count("(interval") == (2 if b >= 0.75 else 1)
        assert t.count(BA_LABEL) == t.count("(interval")
        assert len(re.findall(r"\d+\.\d%", t)) == 3 * t.count("(interval")           # each rate and both ends of its interval
        for bad in FORBIDDEN:
            assert bad not in t
    assert "exceeds this specified shortcut baseline by at least 10 points" in lab(0.80, 0.95, bi, di)["text"]
    assert "does not exceed this specified shortcut baseline by at least 10 points" in lab(0.80, 0.85, bi, di)["text"]
    for b, d in ((0.80, 0.95), (0.80, 0.85), (0.60, 0.90)):          # no intervals given: no rate at all
        t = lab(b, d)["text"]
        assert not re.search(r"\d+\.\d+%", t) and "0.9" not in t and "0.8" not in t, t


def test_balanced_accuracy_and_intervals():
    ba = gate_baseline.balanced_accuracy(["real"] * 10 + ["ai"] * 10, ["real"] * 9 + ["ai"] + ["ai"] * 5 + ["real"] * 5)
    assert ba["balanced_accuracy"] == pytest.approx((0.9 + 0.5) / 2)
    assert ba["tnr_interval_95"][0] < 0.9 < ba["tnr_interval_95"][1]
    assert gate_baseline.balanced_accuracy(["real"], ["real"])["balanced_accuracy"] is None   # one class only
    assert gate_baseline.balanced_accuracy(["real"], ["real"])["ba_interval_conservative"] is None


# Reference values: scipy 1.18.1, scipy.stats.beta.ppf, computed 28 Oct 2026. Clopper-Pearson two-sided at confidence c:
# lower = beta.ppf((1-c)/2, k, n-k+1), upper = beta.ppf(1-(1-c)/2, k+1, n-k); 0 and 1 at k = 0 and k = n.
REF = {  # (k, n): (95% two-sided, 97.5% two-sided)
    (90, 100): ((0.8237774022599773, 0.9509953107785141), (0.8122254594268596, 0.956164277041731)),
    (50, 100): ((0.398321129503301, 0.601678870496699), (0.3847793573021616, 0.6152206426978385)),
    (0, 40): ((0.0, 0.08809730287880237), (0.0, 0.10376324540031584)),
    (40, 40): ((0.9119026971211976, 1.0), (0.8962367545996842, 1.0)),
}


def counts(tn, n_real, tp, n_ai):
    truth = ["real"] * n_real + ["ai"] * n_ai
    pred = ["real"] * tn + ["ai"] * (n_real - tn) + ["ai"] * tp + ["real"] * (n_ai - tp)
    return gate_baseline.balanced_accuracy(truth, pred)


def test_class_intervals_are_95_and_the_ba_interval_averages_two_97_5_intervals():
    ba = counts(90, 100, 50, 100)
    assert ba["tnr_interval_95"] == pytest.approx(REF[(90, 100)][0], abs=1e-9)
    assert ba["tpr_interval_95"] == pytest.approx(REF[(50, 100)][0], abs=1e-9)
    assert ba["tnr_interval_97_5"] == pytest.approx(REF[(90, 100)][1], abs=1e-9)
    assert ba["tpr_interval_97_5"] == pytest.approx(REF[(50, 100)][1], abs=1e-9)
    lo = (REF[(90, 100)][1][0] + REF[(50, 100)][1][0]) / 2
    hi = (REF[(90, 100)][1][1] + REF[(50, 100)][1][1]) / 2
    assert ba["ba_interval_conservative"] == pytest.approx([lo, hi], abs=1e-9)
    assert (lo, hi) == pytest.approx((0.5985024083645106, 0.7856924598697848))
    # wider than the unadjusted mean of the two 95% intervals, which carries no coverage argument
    naive = [(ba["tnr_interval_95"][i] + ba["tpr_interval_95"][i]) / 2 for i in (0, 1)]
    assert ba["ba_interval_conservative"][0] < naive[0] and ba["ba_interval_conservative"][1] > naive[1]
    assert ba["ba_interval_label"] == BA_LABEL


def test_ba_interval_at_the_edges():
    ba = counts(0, 40, 40, 40)                        # recalls 0 and 1: the open ends stay at 0 and 1
    assert ba["tnr_interval_97_5"] == pytest.approx(REF[(0, 40)][1], abs=1e-9)
    assert ba["tpr_interval_97_5"] == pytest.approx(REF[(40, 40)][1], abs=1e-9)
    assert ba["ba_interval_conservative"] == pytest.approx([0.4481183772998421, 0.551881622700158], abs=1e-9)


def test_intervals_agree_with_scipy_when_installed():
    beta = pytest.importorskip("scipy.stats").beta
    for k, n in ((90, 100), (50, 100), (0, 40), (40, 40), (380, 400), (1, 7), (6, 7), (123, 321)):
        for conf in (0.95, 0.975):
            a = 1 - conf
            want = [0.0 if k == 0 else beta.ppf(a / 2, k, n - k + 1), 1.0 if k == n else beta.ppf(1 - a / 2, k + 1, n - k)]
            assert gate_baseline._two_sided(k, n, conf) == pytest.approx(want, abs=1e-9), (k, n, conf)
    ba = counts(380, 400, 70, 200)
    parts = [gate_baseline._two_sided(380, 400, 0.975), gate_baseline._two_sided(70, 200, 0.975)]
    assert ba["ba_interval_conservative"] == pytest.approx(
        [(parts[0][0] + parts[1][0]) / 2, (parts[0][1] + parts[1][1]) / 2], abs=1e-12)
    for k, n in ((380, 400), (70, 200)):
        assert gate_baseline._two_sided(k, n, 0.975)[0] == pytest.approx(
            beta.ppf(0.0125, k, n - k + 1), abs=1e-9)


def test_tree_is_the_frozen_model_and_deterministic():
    feats = lambda w, label: {"label": label, "is_jpeg": 1, "width": w, "height": 512, "megapixels": w * 512 / 1e6,
                              "aspect_ratio": w / 512, "file_bytes": w * 50, "bytes_per_pixel": 0.1,
                              "source_jpeg_quality": 90, "camera_exif_present": 0, "camera_make_present": 0}
    dev = [feats(768 + i, "real") for i in range(20)] + [feats(512, "ai") for _ in range(20)]
    test = [feats(790 + i, "real") for i in range(10)] + [feats(512, "ai") for _ in range(10)]
    a, b = gate_baseline.fit_and_score(dev, test), gate_baseline.fit_and_score(dev, test)
    assert a == b and a["balanced_accuracy"] == 1.0
    assert "max_depth=3, random_state=0" in a["model"] and a["features_used_by_tree"]


def test_verdict_follows_the_frozen_rule():
    cfg = {"ai_if": "score>=threshold", "threshold": 0.5, "abstain": {"rule": "none"}}
    assert gate_run.verdict_from_score(0.5, cfg) == "ai" and gate_run.verdict_from_score(0.4999, cfg) == "real"
    band = {**cfg, "abstain": {"rule": "score_band", "low": 0.3, "high": 0.5}}
    assert gate_run.verdict_from_score(0.4, band) == "abstain"
    with pytest.raises(gate_run.GateRunError):
        gate_run.verdict_from_score(0.5, {**cfg, "ai_if": "other"})


def test_vendored_recipe_is_the_frozen_l4_file():
    assert gate_run.sha256_file(gate_run.DERIVE_PY) == gate_run.DERIVE_SHA256


# Recorded by running L4's da81be6 derive.py (hash 47e21848...752d, before decode_rgb() was split out of derive())
# on the synthetic inputs below. The L4 head's file (7cb1175, hash 53d6ad99...f6b1) must give the same bytes.
# JPEG bytes depend on the encoder, so the comparison is made only where Pillow, libjpeg, zlib and Python are the
# versions the digests were recorded with; elsewhere the test is skipped, not loosened.
DA81BE6_ENV = {"libjpeg": "6.2", "pillow": "13.0.0", "python": "3.12.13", "zlib": "1.3.1.zlib-ng"}
DA81BE6_DERIVE_OUTPUT = {
    "palette_256x160_png/compressed_q70_1080": "87e72f9cb9c34686040ab330ffd1f79aadd854828ce86bb813d25851f8728d9d",
    "palette_256x160_png/format_matched": "edd47c82f96702d6fddf34cea57701f4f48b9e3813095244c55ec88aa5e26a4f",
    "palette_256x160_png/size_matched_512": "ValueError: shorter side under 512: refused, never upscaled",
    "rgb_1500x900_png/compressed_q70_1080": "99909c641ea1e6c92631b1f2350b46ad2680968d39279e51cfb037a9eca6a1d0",
    "rgb_1500x900_png/format_matched": "8092fa68f7949f17dde390bc5d734d85041c37afc8993df98191c975371048da",
    "rgb_1500x900_png/size_matched_512": "7e88e711f959da9ec05a4a38e30e3c4b16e4c5827532e252a73b6ff15a6f548f",
    "rgb_oriented6_400x240_png/compressed_q70_1080": "132e27c24fc34653ca5198e05798c34ee38814c2f6fb6fac61c9b01e40150c52",
    "rgb_oriented6_400x240_png/format_matched": "79c7a6803e6f3a3447a670c52162359a3d5657bc9a7b39f5fffbc24b181c4204",
    "rgb_oriented6_400x240_png/size_matched_512": "ValueError: shorter side under 512: refused, never upscaled",
    "rgba_opaque_300x200_png/compressed_q70_1080": "6d99eecd1df6f19cbf7d7945424a47ba157f67afe8d9124f150269a73d60b272",
    "rgba_opaque_300x200_png/format_matched": "d2c98fe95e4d37681cafc5b5d27c31d0de2aa4d063c89202832a5c903d0ee94c",
    "rgba_opaque_300x200_png/size_matched_512": "ValueError: shorter side under 512: refused, never upscaled",
}


def _recipe_inputs():
    """Four small synthetic originals, built in memory: a 1500x900 RGB (the resize path), an opaque RGBA, a palette
    image, and an RGB with EXIF orientation 6 (the transpose path)."""
    def png(im, **kw):
        b = io.BytesIO()
        im.save(b, "PNG", **kw)
        return b.getvalue()
    out = {}
    im = Image.new("RGB", (1500, 900))
    im.putdata([((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256) for y in range(900) for x in range(1500)])
    out["rgb_1500x900_png"] = png(im)
    im = Image.new("RGBA", (300, 200))
    im.putdata([((x * 3) % 256, (y * 4) % 256, (x ^ y) % 256, 255) for y in range(200) for x in range(300)])
    out["rgba_opaque_300x200_png"] = png(im)
    im = Image.new("P", (256, 160))
    im.putpalette([(i * 5) % 256 if j == 0 else (i * 11) % 256 if j == 1 else (i * 17) % 256
                   for i in range(256) for j in range(3)])
    im.putdata([(x + y) % 256 for y in range(160) for x in range(256)])
    out["palette_256x160_png"] = png(im)
    im = Image.new("RGB", (400, 240))
    im.putdata([((x * 2) % 256, (y * 3) % 256, 90) for y in range(240) for x in range(400)])
    ex = Image.Exif()
    ex[0x0112] = 6
    out["rgb_oriented6_400x240_png"] = png(im, exif=ex)
    return out


def _load_recipe():
    spec = importlib.util.spec_from_file_location("secondlook_vendored_derive", gate_run.DERIVE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_recipe_decode_rgb_is_the_shared_first_step_of_derive():
    """L4 d25c3a1 split decode_rgb() out so derive() and the screening code see the same pixels. The vendored
    recipe must have it, apply orientation, and return 8-bit RGB with the source format and size."""
    mod = _load_recipe()
    inputs = _recipe_inputs()
    im, fmt, src, icc = mod.decode_rgb(inputs["rgb_oriented6_400x240_png"])
    assert (fmt, src, icc) == ("PNG", (400, 240), False)
    assert im.mode == "RGB" and im.size == (240, 400)            # orientation 6 turns it upright
    im, fmt, src, icc = mod.decode_rgb(inputs["rgba_opaque_300x200_png"])
    assert im.mode == "RGB" and im.size == (300, 200) and src == (300, 200)
    im, fmt, src, icc = mod.decode_rgb(inputs["palette_256x160_png"])
    assert im.mode == "RGB"
    _, meta = mod.derive(inputs["rgb_oriented6_400x240_png"], "format_matched")
    assert meta["out_size"] == [240, 400] and meta["source_size"] == [400, 240]


def test_split_recipe_output_is_byte_identical_to_da81be6():
    mod = _load_recipe()
    if mod.env() != DA81BE6_ENV:
        pytest.skip(f"digests were recorded with {DA81BE6_ENV}; this environment has {mod.env()}")
    got = {}
    for name, data in _recipe_inputs().items():
        for arm in mod.ARMS:
            key = f"{name}/{arm}"
            try:
                got[key] = mod.derive(data, arm)[1]["out_sha256"]
            except Exception as e:      # refusals are part of the recipe's output too
                got[key] = f"{type(e).__name__}: {e}"
    assert got == DA81BE6_DERIVE_OUTPUT


# ------------------------------------------------------------------ refusals (tiny fixture, nothing written)

@pytest.fixture
def real_floors():
    """Ask for the locked floors (dev 100 + 100, test 400 + 200). Without it the small fixtures run under floors of 1."""


@pytest.fixture(autouse=True)
def low_floors(request, monkeypatch):
    """The small fixtures hold a dozen units a side; the floors themselves are tested with `real_floors`."""
    if "real_floors" not in request.fixturenames:
        for name in ("MIN_DEV_REAL", "MIN_DEV_AI", "MIN_REAL", "MIN_AI"):
            monkeypatch.setattr(gate_run, name, 1)


@pytest.fixture
def tiny(tmp_path):
    return fx.build(tmp_path)


def assert_refused(fixture, capsys, needle, run_id="fx1", rungs=None, *extra):
    code = run(fixture, *extra, rungs=rungs if rungs is not None else fx.fake_classifier(), run_id=run_id)
    err = capsys.readouterr().err
    assert code == 2, err
    assert needle in err, err
    assert not (fixture.root / "runs" / run_id / "predictions.csv").exists()
    return err


def test_refuses_when_the_lock_no_longer_matches(tiny, capsys):
    (tiny.l4 / "PROTOCOL.md").write_text("edited after the lock\n")
    assert_refused(tiny, capsys, "no longer matches LOCK.json")


def test_refuses_when_screen_config_changed_after_the_lock(tiny, capsys):
    (tiny.l4 / "SCREEN-CONFIG.json").write_text(json.dumps({"phash_distance": 8, "tools": ["swapped"]}))
    assert_refused(tiny, capsys, "SCREEN-CONFIG.json no longer matches LOCK.json")


def rehash_accepted(l4):
    """Re-point ACCEPTED.json at the files as they are now, and put its hash in the ledger."""
    acc = json.loads((l4 / "ACCEPTED.json").read_text())
    acc["screen_config_sha256"] = fx.sha(l4 / "SCREEN-CONFIG.json")
    acc["lock_sha256"] = fx.sha(l4 / "LOCK.json")
    (l4 / "ACCEPTED.json").write_text(json.dumps(acc, indent=1))
    (l4 / "LEDGER.md").write_text(f"# ledger\n\nACCEPTED.json sha256 {fx.sha(l4 / 'ACCEPTED.json')}\n")


def test_screen_config_must_be_in_the_lock(tmp_path, capsys):
    """DAT-852 review finding 5. A lock written without SCREEN-CONFIG.json (six names, as calibrate.L4_LOCKED has them)
    let the file be edited after the lock: ACCEPTED.json and LEDGER.md re-hashed to match and every check passed."""
    f = fx.build(tmp_path, lock_screen=False)
    (f.l4 / "SCREEN-CONFIG.json").write_text(json.dumps({"phash_distance": 8, "tools": ["swapped after the lock"]}))
    rehash_accepted(f.l4)
    err = assert_refused(f, capsys, "does not lock ['SCREEN-CONFIG.json']")
    assert "an edit after the lock would go unseen" in err


def test_a_lock_without_screen_config_is_refused_even_when_nothing_was_edited(tmp_path, capsys):
    f = fx.build(tmp_path, lock_screen=False)
    assert_refused(f, capsys, "does not lock ['SCREEN-CONFIG.json']")


def test_the_dry_run_names_the_missing_screen_config_lock(tmp_path, capsys):
    f = fx.build(tmp_path, lock_screen=False)
    code = gate_run.main(f.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "[REFUSED] L4 LOCK.json and the split files: L4 LOCK.json does not lock" in out


def test_refuses_screen_config_with_another_phash_distance(tiny, capsys):
    """A config edited AND re-locked (a new LOCK.json, ACCEPTED.json and ledger to match) still has to agree with the split."""
    (tiny.l4 / "SCREEN-CONFIG.json").write_text(json.dumps({"phash_distance": 6}))
    lock = json.loads((tiny.l4 / "LOCK.json").read_text())
    (tiny.l4 / "LOCK.json").write_text(json.dumps({"sha256": {n: fx.sha(tiny.l4 / n) for n in lock["sha256"]}}))
    rehash_accepted(tiny.l4)
    assert_refused(tiny, capsys, "phash_distance")


def test_refuses_when_accepted_hash_is_not_in_the_ledger(tiny, capsys):
    (tiny.l4 / "LEDGER.md").write_text("nothing recorded\n")
    assert_refused(tiny, capsys, "not recorded in LEDGER.md")


def test_refuses_when_accepted_units_differ_from_the_split(tiny, capsys):
    acc = json.loads((tiny.l4 / "ACCEPTED.json").read_text())
    acc["units"]["test"].pop()
    (tiny.l4 / "ACCEPTED.json").write_text(json.dumps(acc))
    (tiny.l4 / "LEDGER.md").write_text(f"sha256 {fx.sha(tiny.l4 / 'ACCEPTED.json')}\n")
    assert_refused(tiny, capsys, "name different units")


def test_refuses_a_tampered_original(tiny, capsys):
    p = tiny.l4 / "images" / "original" / "real" / "test-real-0.jpg"
    fx.make_real(p, 999)
    assert_refused(tiny, capsys, "sha256 differs")


def test_refuses_threshold_without_both_gated_arms(tmp_path, capsys):
    f = fx.build(tmp_path, gate_arms=("original",))
    assert_refused(f, capsys, "gate_arms")


def test_refuses_threshold_without_a_training_data_statement(tmp_path, capsys):
    f = fx.build(tmp_path, training_data="")
    assert_refused(f, capsys, "training_data_statement")


def test_refuses_a_threshold_that_was_never_committed(tmp_path, capsys):
    f = fx.build(tmp_path, commit=False)
    assert_refused(f, capsys, "not tracked by git")


def test_refuses_a_threshold_edited_after_its_commit(tiny, capsys):
    tiny.threshold.write_text(tiny.threshold.read_text() + "\n")
    assert_refused(tiny, capsys, "differs from its last commit")


def test_refuses_a_threshold_committed_in_the_future(tmp_path, capsys):
    f = fx.build(tmp_path, commit=False)
    fx.git(f.repo, "add", "threshold.json")
    fx.git(f.repo, "commit", "-q", "-m", "late", date="2099-01-01T00:00:00Z")
    assert_refused(f, capsys, "not before now")


def test_refuses_a_recipe_file_that_is_not_the_frozen_one(tiny, capsys, monkeypatch):
    monkeypatch.setattr(gate_run, "DERIVE_SHA256", "0" * 64)
    assert_refused(tiny, capsys, "not the frozen L4 recipe")


def test_refuses_an_existing_derived_file_that_is_not_recipe_r1(tiny, capsys):
    d = tiny.l4 / "images-derived" / "format_matched"
    d.mkdir(parents=True)
    fx.make_real(d / "dev-real-0.jpg", 5)               # not what R1 makes from the original
    assert_refused(tiny, capsys, "not what recipe R1 makes")


def test_refuses_when_a_test_image_gets_no_classifier_score(tiny, capsys):
    assert_refused(tiny, capsys, "expected exactly one classifier score", rungs=[("classifier", lambda p: [])])


def test_refuses_when_there_is_no_classifier_rung(tiny, capsys):
    assert_refused(tiny, capsys, "classifier rung is not available", rungs=[("exif", lambda p: [])])


def test_refuses_before_deriving_when_the_real_classifier_weights_are_missing(tiny, capsys, monkeypatch):
    monkeypatch.setenv("SECONDLOOK_COMMFOR_WEIGHTS", str(tiny.root / "no-such-weights.safetensors"))
    code = gate_run.main(tiny.argv())                            # no injected rungs: the installed ones
    err = capsys.readouterr().err
    assert code == 2 and "classifier weights not found" in err
    assert not (tiny.l4 / "images-derived").exists()


def test_refuses_weights_other_than_the_ones_the_threshold_was_frozen_for(tiny, capsys, monkeypatch):
    w = tiny.root / "weights.safetensors"
    w.write_bytes(b"not the pinned weights")
    monkeypatch.setenv("SECONDLOOK_COMMFOR_WEIGHTS", str(w))
    code = gate_run.main(tiny.argv())
    err = capsys.readouterr().err
    assert code == 2 and "not the ones threshold.json was frozen for" in err
    assert not (tiny.l4 / "images-derived").exists()


def test_refuses_a_run_folder_that_already_holds_files(tiny, capsys):
    out = tiny.root / "runs" / "fx1"
    out.mkdir(parents=True)
    (out / "old.txt").write_text("an earlier run")
    code = run(tiny, rungs=fx.fake_classifier())
    assert code == 2 and "already holds files" in capsys.readouterr().err


def test_refuses_when_threshold_changes_between_the_checks_and_scoring(tiny, capsys, monkeypatch):
    real = gate_run.derive_all

    def derive_then_edit(plan):
        out = real(plan)
        tiny.threshold.write_text(tiny.threshold.read_text().replace('"threshold": 0.5', '"threshold": 0.2'))
        return out
    monkeypatch.setattr(gate_run, "derive_all", derive_then_edit)
    calls = []
    assert_refused(tiny, capsys, "changed since the checks", rungs=fx.fake_classifier(calls=calls))
    assert calls == []


def test_an_unrelated_commit_between_the_checks_and_scoring_does_not_stop_the_run(tiny, capsys, monkeypatch):
    real = gate_run.derive_all

    def derive_then_commit_elsewhere(plan):
        out = real(plan)
        (tiny.repo / "other.txt").write_text("x")
        fx.git(tiny.repo, "add", "other.txt")
        fx.git(tiny.repo, "commit", "-q", "-m", "unrelated")
        return out
    monkeypatch.setattr(gate_run, "derive_all", derive_then_commit_elsewhere)
    assert run(tiny, rungs=fx.fake_classifier()) == 2                  # on to the harness, which needs a bigger dev split
    assert "L2 harness refused its inputs" in capsys.readouterr().err


def test_a_refusal_before_scoring_never_calls_the_classifier(tiny, capsys):
    calls = []
    (tiny.l4 / "PROTOCOL.md").write_text("edited\n")
    assert_refused(tiny, capsys, "LOCK.json", rungs=fx.fake_classifier(calls=calls))
    assert calls == []


# ------------------------------------------------------------------ dry run

def test_dry_run_prints_sizes_and_steps_and_writes_nothing(tiny, capsys):
    code = gate_run.main(tiny.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 0, out
    assert "DRY RUN" in out and "dev:  6 real + 6 AI = 12 units" in out and "test: 6 real + 6 AI = 12 units" in out
    assert "24 x 3 arms = 72" in out
    assert "12 x 4 arms = 48" in out and "free disk" in out
    for step in ("L4 LOCK.json", "SCREEN-CONFIG.json", "ACCEPTED.json", "committed before any test prediction"):
        assert step in out
    assert out.count("[ok]") == 14 and "classifier rung and weights" in out and "REFUSED" not in out
    assert "dependencies (Pillow, scikit-learn)" in out and "replacements follow the frozen reserve order" in out
    assert "floors: dev 100 + 100, gated-arm test 400 real + 200 AI" in out
    assert not (tiny.root / "runs").exists() and not (tiny.l4 / "images-derived").exists()


def test_dry_run_reports_a_failing_check_and_exits_2(tiny, capsys):
    (tiny.l4 / "LEDGER.md").write_text("empty\n")
    code = gate_run.main(tiny.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "[REFUSED] ACCEPTED.json" in out and "not checked" in out


# ------------------------------------------------------------------ the whole command

@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """Dev 100 + 100, test 400 real + 200 AI: the harness floors, so PASS and FAIL are both reachable."""
    return fx.build(tmp_path_factory.mktemp("big"), dev_real=100, dev_ai=100, test_real=400, test_ai=200)


@pytest.fixture(scope="module")
def passed(big):
    flag = ids("test", "real", 2)                                   # 2 of 400 false alarms: upper bound under 2%
    calls = []
    code = run(big, rungs=fx.fake_classifier(flag_ids=flag, calls=calls))
    return {"code": code, "out": big.root / "runs" / "fx1", "fixture": big, "calls": calls}


def test_full_run_passes_and_writes_everything(passed):
    out = passed["out"]
    assert passed["code"] == 0
    for name in ("predictions.csv", "ladder-answers.csv", "baseline.json", "hashes-derived.csv", "derive-env.json",
                 "gate-run.json", "RESULT.md", "result.json", "RESULTS.md"):
        assert (out / name).is_file(), name
    res = json.loads((out / "result.json").read_text())
    assert res["status"] == "PASS" and res["gate"]["gate_arms"] == ["original", "format_matched"]
    preds = read_csv(out / "predictions.csv")
    assert len(preds) == 600 * 4
    assert {r["arm"] for r in preds} == set(gate_run.ARMS)
    assert all(r["verdict"] in ("ai", "real") for r in preds)
    assert len(read_csv(out / "ladder-answers.csv")) == 600


def test_every_derived_arm_is_one_generation_from_the_original(passed):
    f = passed["fixture"]
    spec = importlib.util.spec_from_file_location("l4d", gate_run.DERIVE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for split, uid, src in (("dev", "dev-real-3", "real/dev-real-3.jpg"), ("test", "test-ai-5", "ai/test-ai-5.png"),
                            ("test", "test-real-9", "real/test-real-9.jpg")):
        data = (f.l4 / "images" / "original" / src).read_bytes()
        for arm in gate_run.DERIVED_ARMS:
            made = (f.l4 / "images-derived" / arm / f"{uid}.jpg").read_bytes()
            assert made == mod.derive(data, arm)[0]
            im = Image.open(io.BytesIO(made))
            assert im.format == "JPEG" and not im.getexif() and "icc_profile" not in im.info
    rec = read_csv(passed["out"] / "hashes-derived.csv")
    assert len(rec) == (200 + 600) * 3
    sizes = {(r["arm"], r["item_id"]): (int(r["width"]), int(r["height"])) for r in rec}
    assert sizes[("format_matched", "test-real-9")] == (768, 512)         # shrink only: never upscaled
    assert sizes[("size_matched_512", "test-real-9")] == (512, 512)


def test_derived_arms_are_what_the_classifier_scored(passed):
    paths = [Path(p) for p in passed["calls"]]
    f = passed["fixture"]
    arm_dirs = {p.parent.name for p in paths}
    assert {"format_matched", "compressed_q70_1080", "size_matched_512"} <= arm_dirs
    assert any(f.l4 / "images" / "original" in p.parents for p in paths)
    assert not any(p.parent.name in ("original",) for p in paths)


def test_the_threshold_commit_comes_before_the_first_test_prediction(passed):
    prov = json.loads((passed["out"] / "gate-run.json").read_text())
    first = min(dt.datetime.fromisoformat(r["predicted_at"].replace("Z", "+00:00"))
                for r in read_csv(passed["out"] / "predictions.csv"))
    committed = dt.datetime.fromisoformat(prov["threshold_commit"]["committed_at"].replace("Z", "+00:00"))
    assert committed < first
    assert prov["threshold_commit"]["commit"] == subprocess.run(
        ["git", "-C", str(passed["fixture"].repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for key in ("l4_lock_sha256", "screen_config_sha256", "accepted_sha256", "threshold_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", prov[key])


def test_results_md_uses_only_the_allowed_wording(passed):
    md = (passed["out"] / "RESULTS.md").read_text()
    assert md.startswith("# Results") and "SYNTHETIC FIXTURE RUN" in md
    assert "Gate verdict from the harness: **PASS**" in md
    sentences = ALLOWED.findall(md)
    assert [s[4] for s in sentences] == ["original", "format_matched", "compressed_q70_1080", "size_matched_512"]
    for s in sentences:                                    # N and M, never a pooled count
        assert (s[0], s[1]) == (("400", "200"))
    orig = sentences[0]
    assert (orig[2], orig[3]) == ("2", "200")              # 2 of 400 false alarms; the 200 AI images all caught
    for bad in FORBIDDEN:
        assert bad not in md
    assert md.count("### ") == 4                          # one section, with one table, per arm
    for head in ("## Attrition", "## Ladder answers", "### original", "Per topic, `format_matched`"):
        assert head in md
    assert re.search(r"\| landscape \| \d+ \|", md)       # per-topic rows
    assert "reported, not gated" in md and md.count("**gated**") == 2


def test_results_md_has_class_tables_and_the_labelled_ba_interval(passed):
    md = (passed["out"] / "RESULTS.md").read_text()
    base = json.loads((passed["out"] / "baseline.json").read_text())
    for arm in gate_run.ARMS:
        assert f"Detector in `{arm}`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval)" in md
        assert f"Shortcut baseline in `{arm}`, by class" in md
        assert f"Balanced accuracy, detector, `{arm}`:" in md and f"Balanced accuracy, shortcut baseline, `{arm}`:" in md
    # one labelled interval per model per arm, each from two 97.5% intervals; the exact label, verbatim
    assert md.count(f"({BA_LABEL}).") == 8
    assert md.count("two 97.5% two-sided Clopper-Pearson intervals, one per class") == 8
    b = base["original"]["ba_interval_conservative"]
    assert f"interval {b[0]:.3%} to {b[1]:.3%} ({BA_LABEL})" in md
    assert "| real (true-negative rate) |" in md and "| AI (true-positive rate) |" in md
    assert "approximate range" not in md and "mean of the bounds" not in md
    assert "makes no simultaneous claim across arms or topics" in md
    # no pooled cross-arm figure: every balanced accuracy line names exactly one arm
    for line in md.splitlines():
        if line.startswith("Balanced accuracy,"):
            assert len(set(re.findall(r"`(\w+)`", line))) == 1, line
    assert not re.search(r"(all|every|across|combined|overall) arms?[^.]{0,40}\d+\.\d+%", md, re.I)
    assert "exceeds this specified shortcut baseline by at least 10 points" in md or \
        "does not exceed this specified shortcut baseline by at least 10 points" in md
    assert "People screening of the test images was OpenCV triage plus a Muse Glimmer check" not in md  # synthetic run: no real-data limits


def test_every_balanced_accuracy_in_results_md_has_its_interval_and_label(passed):
    md = (passed["out"] / "RESULTS.md").read_text()
    labels = [ln for ln in md.splitlines() if ln.startswith("**Shortcut label,")]
    assert len(labels) == 4
    for ln in labels:
        n = ln.count("(interval")
        assert n >= 1 and ln.count(BA_LABEL) == n                      # each rate carries its interval and its label
        assert "may be called" not in ln and "is not called" not in ln and "content" not in ln
    assert "exceeds this specified shortcut baseline by at least 10 points" in labels[0] or \
        "does not exceed this specified shortcut baseline by at least 10 points" in labels[0]
    for ln in md.splitlines():                                          # no balanced accuracy outside a labelled interval
        if ln.startswith("Balanced accuracy,"):
            assert "interval" in ln and BA_LABEL in ln
    assert "2 reserve unit(s) used" in md and "1 rejected primary unit(s) had no reserve left" in md


def test_results_md_shows_shortcut_labels_and_attrition(passed):
    md = (passed["out"] / "RESULTS.md").read_text()
    base = json.loads((passed["out"] / "baseline.json").read_text())
    assert set(base) == set(gate_run.ARMS)
    assert base["original"]["balanced_accuracy"] == 1.0           # size, aspect and format give the classes away
    assert "Shortcut label, original: shortcut-exposed" in md
    assert "| test | real | 401 | 3 | 2 | 400 |" in md           # 401 planned primaries - 3 rejected + 2 reserve units = 400
    assert "| test | ai | 200 | 0 | 0 | 200 |" in md
    assert "C3 faces and persons: 3" in md


def test_harness_output_is_the_one_in_the_run_folder(passed):
    out = passed["out"]
    assert (out / "RESULT.md").read_text().strip() and json.loads((out / "result.json").read_text())["run_id"] == "fx1"


def test_a_rerun_needs_a_new_folder_and_reuses_identical_derived_files(tiny, capsys):
    """The tiny dev split is below the harness minimum, so each run stops at the harness, after deriving."""
    assert run(tiny, rungs=fx.fake_classifier()) == 2
    assert "L2 harness refused its inputs" in capsys.readouterr().err
    first = {p: p.read_bytes() for p in (tiny.l4 / "images-derived").rglob("*.jpg")}
    assert len(first) == 24 * 3
    assert run(tiny, rungs=fx.fake_classifier()) == 2                # same run folder
    assert "already holds files" in capsys.readouterr().err
    assert run(tiny, rungs=fx.fake_classifier(), run_id="fx2") == 2  # a new folder: derived files are reused as they are
    assert "L2 harness refused its inputs" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in (tiny.l4 / "images-derived").rglob("*.jpg")} == first


def test_fails_when_false_positives_are_too_many(big, capsys):
    code = run(big, rungs=fx.fake_classifier(flag_ids=ids("test", "real", 12)), run_id="fail1")
    out = big.root / "runs" / "fail1"
    md = (out / "RESULTS.md").read_text()
    assert code == 1
    assert "**FAIL**" in md and "false-positive upper bound" in md
    assert "not met" in md                                          # published as it stands
    assert json.loads((out / "result.json").read_text())["status"] == "FAIL"


def test_a_gated_arm_that_fails_alone_fails_the_run(big):
    """format_matched is gated too: a classifier that only fails on the derived arm must not pass."""
    flag = set(ids("test", "real", 12))
    calls = []
    base = fx.fake_classifier(flag_ids=(), calls=calls)[0][1]

    def check(path):
        p = Path(path)
        if p.parent.name == "format_matched" and p.stem in flag:
            return fx.fake_classifier(flag_ids=flag)[0][1](path)
        return base(path)

    code = run(big, rungs=[("classifier", check)], run_id="fmfail")
    res = json.loads((big.root / "runs" / "fmfail" / "result.json").read_text())
    assert code == 1 and res["arms"]["original"]["rule"]["passes"] and not res["arms"]["format_matched"]["rule"]["passes"]


def run_invalid_files(f):
    out = f.root / "runs" / "fx1"
    return sorted(p.name for p in out.iterdir())


def test_a_test_set_under_the_floor_is_published_invalid_before_anything_is_derived(tmp_path, real_floors, capsys):
    """DAT-852 review finding 1 (c014e175: a shortfall after the frozen reserves publishes as INVALID)."""
    small = fx.build(tmp_path, dev_real=100, dev_ai=100, test_real=30, test_ai=20, rejected=0, reserve=0)
    calls = []
    code = run(small, rungs=fx.fake_classifier(calls=calls))
    out = small.root / "runs" / "fx1"
    md = (out / "RESULTS.md").read_text()
    err = capsys.readouterr().err
    assert code == 3 and "no predictions written" not in err
    assert "Gate verdict: **INVALID** (exit code 3)" in md
    assert "original: 30 real and 20 AI accepted test units, below the locked 400 real and 200 AI" in md
    assert "format_matched: 30 real and 20 AI accepted test units, below the locked 400 real and 200 AI" in md
    assert "dev split" not in md.split("Reasons:")[1].split("##")[0]           # dev met its floor: only the test shortfall
    assert "| Dev split, accepted units | 100 | 100 | 100, 100 | yes |" in md
    assert "| Test split, accepted units (each gated arm: original, format_matched) | 30 | 20 | 400, 200 | no |" in md
    assert "no predictions file was written" in md and "no detector result" in md
    assert "| test | real | 30 | 0 | 0 | 30 |" in md                                  # attrition, with planned and accepted
    assert run_invalid_files(small) == ["RESULTS.md", "gate-run.json"]               # no predictions, no harness files
    assert json.loads((out / "gate-run.json").read_text())["predictions_written"] is False
    assert calls == [] and not (small.l4 / "images-derived").exists()               # nothing derived, nothing scored
    assert not ALLOWED.findall(md) and "On " not in md                               # no detector sentence exists


def test_a_dev_set_under_the_floor_is_published_invalid_with_the_counts(tmp_path, real_floors, capsys):
    """Review probe: dev 10 real + 10 AI. It used to derive and score every arm, write predictions, then exit 2."""
    f = fx.build(tmp_path, dev_real=10, dev_ai=10)
    calls = []
    code = run(f, rungs=fx.fake_classifier(calls=calls))
    out = f.root / "runs" / "fx1"
    md = (out / "RESULTS.md").read_text()
    assert code == 3 and "no predictions written" not in capsys.readouterr().err
    assert "dev split: 10 real and 10 AI accepted units, below the floor of 100 real and 100 AI" in md
    assert "| Dev split, accepted units | 10 | 10 | 100, 100 | no |" in md
    assert run_invalid_files(f) == ["RESULTS.md", "gate-run.json"]
    assert calls == [] and not (f.l4 / "images-derived").exists()
    assert "Replacements came only from the reserve order frozen in the manifests" in md   # verified, so it is printed


@pytest.mark.parametrize("dev_real,dev_ai,test_real,test_ai,reason", [
    (99, 100, 400, 200, "dev split: 99 real and 100 AI"),
    (100, 99, 400, 200, "dev split: 100 real and 99 AI"),
    (100, 100, 399, 200, "original: 399 real and 200 AI accepted test units"),
    (100, 100, 400, 199, "format_matched: 400 real and 199 AI accepted test units"),
    (100, 100, 400, 200, None)])
def test_floor_boundaries_are_the_locked_ones(real_floors, dev_real, dev_ai, test_real, test_ai, reason):
    """Dev 100 + 100 and gated-arm test 400 real + 200 AI, counted on the accepted units (after attrition and reserves)."""
    from types import SimpleNamespace
    mk = lambda split, label, n: [{"unit_id": f"{split}{label}{i}", "label": label} for i in range(n)]
    dev = mk("d", "real", dev_real) + mk("d", "ai", dev_ai)
    test = mk("t", "real", test_real) + mk("t", "ai", test_ai)
    plan = SimpleNamespace(ctx={"dev_rows": dev, "accepted": {"units": {"dev": dev, "test": test}}})
    if reason is None:
        gate_run.Plan.check_floors(plan)
        return
    with pytest.raises(gate_run.Invalid) as e:
        gate_run.Plan.check_floors(plan)
    assert any(reason in r for r in e.value.reasons) and not isinstance(e.value, KeyError)


def test_the_dry_run_says_a_real_run_would_publish_invalid(tmp_path, real_floors, capsys):
    f = fx.build(tmp_path, dev_real=10, dev_ai=10)
    code = gate_run.main(f.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 3
    assert "[INVALID] floors: dev 100 + 100" in out and "would publish INVALID with no predictions (exit 3)" in out
    assert not (f.root / "runs").exists()


def test_a_refusal_still_comes_before_an_invalid(tmp_path, real_floors, capsys):
    """A tampered lock is a refusal (2), not a published INVALID: the counts are only trusted after the lock holds."""
    f = fx.build(tmp_path, dev_real=10, dev_ai=10)
    (f.l4 / "PROTOCOL.md").write_text("edited after the lock\n")
    assert_refused(f, capsys, "no longer matches LOCK.json")
    assert not (f.root / "runs").exists()


# ------------------------------------------------------------------ crashes exit 4, never 1 (review finding 3)

@pytest.mark.parametrize("exc", [ImportError("No module named 'sklearn'"), RuntimeError("boom"),
                                 ZeroDivisionError("x"), AssertionError("a"), TypeError("t"), AttributeError("a")])
def test_any_unexpected_exception_exits_4_never_1(tiny, capsys, monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(gate_baseline, "fit_and_score", boom)
    code = run(tiny, rungs=fx.fake_classifier())
    err = capsys.readouterr().err
    assert code == 4 and "gate_run: crashed" in err and type(exc).__name__ in err


def test_a_crash_in_the_harness_exits_4(tiny, capsys, monkeypatch):
    monkeypatch.setattr(gate_run.harness, "main", lambda argv: gate_run.harness.EXIT_CRASH)
    assert run(tiny, rungs=fx.fake_classifier()) == 4
    assert "harness crashed" in capsys.readouterr().err


def test_a_crash_in_the_dry_run_exits_4(tiny, capsys, monkeypatch):
    def boom(self):
        raise RuntimeError("boom")
    monkeypatch.setattr(gate_run.Plan, "check_lock", boom)
    assert gate_run.main(tiny.argv("--dry-run"), rungs=fx.fake_classifier()) == 4
    assert "crashed" in capsys.readouterr().err


def test_the_gate_failing_is_still_exit_1(big, capsys):
    assert run(big, rungs=fx.fake_classifier(flag_ids=ids("test", "real", 12)), run_id="exit1") == 1


def test_missing_scikit_learn_is_refused_up_front_not_after_the_scoring(tiny, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "sklearn.tree", None)
    calls = []
    code = run(tiny, rungs=fx.fake_classifier(calls=calls))
    err = capsys.readouterr().err
    assert code == 2 and "sklearn.tree (scikit-learn" in err and "Install the gate extra" in err
    assert calls == [] and not (tiny.l4 / "images-derived").exists() and not (tiny.root / "runs").exists()


def test_the_dry_run_lists_a_missing_dependency(tiny, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "sklearn", None)
    monkeypatch.setitem(sys.modules, "sklearn.tree", None)
    code = gate_run.main(tiny.argv("--dry-run"), rungs=fx.fake_classifier())
    assert code == 2 and "[REFUSED] dependencies (Pillow, scikit-learn)" in capsys.readouterr().out


def test_a_harness_refusal_after_predictions_exist_does_not_say_none_were_written(tiny, capsys):
    """The harness's own dev floor stays real here (only gate_run's is lowered), so it refuses after the scoring."""
    code = run(tiny, rungs=fx.fake_classifier())
    err = capsys.readouterr().err
    assert code == 2 and "refused after files were written" in err and "no predictions written" not in err
    assert (tiny.root / "runs" / "fx1" / "predictions.csv").is_file()


# ------------------------------------------------------------------ frozen reserves (review finding 4)

def reserve_plan(f):
    plan = gate_run.Plan(gate_run.build_parser().parse_args(f.argv()), rungs=fx.fake_classifier())
    plan.run_checks()
    return plan


def drop_unit(d, label, uid):
    for key in ("groups", "manifest"):
        d[key][label] = [r for r in d[key][label] if r["unit_id"] != uid]


def set_field(d, uid, **kw):
    for key in ("groups", "manifest"):
        for rows in d[key].values():
            for r in rows:
                if r["unit_id"] == uid:
                    r.update(kw)


def test_the_fixture_reserves_follow_the_frozen_order(tmp_path):
    """rej-0 (landscape) and rej-1 (object) are replaced by the rank-1 reserve of their topic; rej-2 (building) has none."""
    plan = reserve_plan(fx.build(tmp_path))
    assert plan.ctx["reserve_check"] == {"used": 2, "skipped": 0, "unfilled": 1, "rejected_unneeded": 0, "awaiting_fetch": 0}


def test_a_reserve_that_was_itself_rejected_is_skipped_in_order(tmp_path):
    def tweak(d):
        d["rejections"].append({"unit_id": "spare-0", "split": "test", "label": "real", "topic": "landscape",
                                "check": "C3 faces and persons", "reason": "fixture"})
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 0)], tweak=tweak)
    plan = reserve_plan(f)                         # rank 0 rejected, rank 1 (test-real-4) takes the place of rej-0
    assert plan.ctx["reserve_check"] == {"used": 2, "skipped": 1, "unfilled": 1, "rejected_unneeded": 0, "awaiting_fetch": 0}
    md = gate_run.gate_report.attrition(plan, gate_run.planned_counts(f.l4))
    assert any("2 reserve unit(s) used" in x and "1 reserve unit(s) skipped" in x and "1 rejected primary unit(s) had no "
               "reserve left" in x for x in md)


def test_refuses_a_replacement_out_of_order(tmp_path, capsys):
    """A rank-0 reserve for the same split and topic was passed over: test-real-4 (rank 1) was taken instead."""
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 0)])
    err = assert_refused(f, capsys, "replacement out of order: real reserve spare-0 (spare_rank 0, test / landscape)")
    assert "was not used" in err


def test_refuses_a_reserve_used_where_no_replacement_was_owed(tmp_path, capsys):
    def tweak(d):                                   # no rejection in "object": its rank-1 reserve was still accepted
        d["rejections"] = [r for r in d["rejections"] if r["unit_id"] != "rej-1"]
        drop_unit(d, "real", "rej-1")
    f = fx.build(tmp_path, tweak=tweak)
    err = assert_refused(f, capsys, "replacement out of order: real reserve test-real-5")
    assert "no rejected primary unit there is owed a replacement" in err


def test_refuses_a_second_reserve_when_one_replacement_was_owed(tmp_path, capsys):
    """One rejected landscape primary, two landscape reserves accepted: only the rank-1 reserve was owed."""
    def tweak(d):
        set_field(d, "test-real-5", topic="landscape", spare_rank="2")
        for u in d["units"]["test"]:
            if u["unit_id"] == "test-real-5":
                u["topic"] = "landscape"
    f = fx.build(tmp_path, rejected=1, tweak=tweak)
    err = assert_refused(f, capsys, "replacement out of order: real reserve test-real-5 (spare_rank 2, test / landscape)")
    assert "already replaced by lower-ranked reserves" in err


def test_refuses_a_unit_that_is_in_neither_manifest(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=lambda d: drop_unit(d, "ai", "test-ai-0"))
    assert_refused(f, capsys, "unit test-ai-0 (ai) is in neither manifest-ai.csv nor groups-ai.csv")


def test_refuses_a_rejection_of_a_unit_that_is_in_neither_manifest(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=lambda d: d["rejections"].append(
        {"unit_id": "ghost", "split": "test", "label": "real", "topic": "plant", "check": "C3", "reason": "x"}))
    assert_refused(f, capsys, "unit ghost (real) is in neither manifest-real.csv nor groups-real.csv")


def test_refuses_a_reserve_for_the_other_split(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=lambda d: set_field(d, "test-real-4", spare_for="dev"))
    assert_refused(f, capsys, "unit test-real-4 is accepted into test as 'reserve', but the frozen manifests make it "
                              "a reserve for dev")


def test_refuses_a_primary_claimed_as_a_reserve(tmp_path, capsys):
    def tweak(d):
        for u in d["units"]["test"]:
            if u["unit_id"] == "test-real-0":
                u["role"] = "reserve"
    f = fx.build(tmp_path, tweak=tweak)
    assert_refused(f, capsys, "unit test-real-0 is accepted into test as 'reserve', but the frozen manifests make it "
                              "a primary unit of test")


def test_refuses_a_topic_that_is_not_the_frozen_one(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=lambda d: set_field(d, "test-real-0", topic="other"))
    assert_refused(f, capsys, "unit test-real-0: ACCEPTED.json topic")


def test_refuses_groups_and_manifest_that_disagree(tmp_path, capsys):
    def tweak(d):
        for r in d["manifest"]["real"]:
            if r["unit_id"] == "test-real-4":
                r["spare_rank"] = "9"
    f = fx.build(tmp_path, tweak=tweak)
    assert_refused(f, capsys, "groups-real.csv and manifest-real.csv disagree")


def test_refuses_two_reserves_with_the_same_rank(tmp_path, capsys):
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 1)])
    assert_refused(f, capsys, "share a spare_rank")


def test_refuses_a_unit_that_is_both_accepted_and_rejected(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=lambda d: d["rejections"].append(
        {"unit_id": "test-real-0", "split": "test", "label": "real", "topic": "landscape", "check": "C3", "reason": "x"}))
    assert_refused(f, capsys, "both accepted and rejected")


def test_the_reserve_sentence_is_printed_only_when_it_is_true():
    from collections import Counter
    from types import SimpleNamespace
    ctx = {"accepted": {"units": {"dev": [], "test": []}, "rejections": []}}
    off = gate_run.gate_report.attrition(SimpleNamespace(ctx=dict(ctx)), Counter())
    assert not any("Replacements came only" in x for x in off)
    assert any("not checked against the frozen manifests" in x for x in off)
    on = gate_run.gate_report.attrition(
        SimpleNamespace(ctx={**ctx, "reserve_check": {"used": 3, "skipped": 1, "unfilled": 2}}), Counter())
    assert any("Replacements came only" in x and "3 reserve unit(s) used" in x for x in on)


# ------------------------------------------------------------------ c014e175: population and wording

def test_real_run_wording_carries_the_narrowed_people_screen_claim(big):
    argv = [a for a in big.argv(run_id="real1") if a != "--synthetic"] + ["--results-md", str(big.root / "real1.md")]
    code = gate_run.main(argv, rungs=fx.fake_classifier(flag_ids=ids("test", "real", 2)))
    assert code == 0
    md = (big.root / "real1.md").read_text()
    assert "SYNTHETIC FIXTURE RUN" not in md
    assert "OpenCV triage plus a Muse Glimmer check of flagged images only; not certified person-free" in md
    assert "conservative, at least 95%, conditional on per-class binomial unit sampling" in md
    assert "content-based" not in md and "certified person-free." not in md.replace("not certified person-free", "")


def test_a_diagnostic_arm_failure_never_changes_the_gated_population(big, monkeypatch):
    """One size_matched_512 conversion fails: the run goes on, both gated arms keep all 600 units, and the
    diagnostic arm is reported with its coverage (c014e175 B3)."""
    real = gate_run.load_derive()
    victim = Path(big.l4 / "images" / "original" / "real" / "test-real-5.jpg").read_bytes()

    class Wrapped:
        env = staticmethod(real.env)

        @staticmethod
        def derive(data, arm):
            if arm == "size_matched_512" and data == victim:
                raise ValueError("fixture: this image cannot be cropped")
            return real.derive(data, arm)

    monkeypatch.setattr(gate_run, "load_derive", lambda: Wrapped)
    code = run(big, rungs=fx.fake_classifier(flag_ids=ids("test", "real", 2)), run_id="diag1")
    out = big.root / "runs" / "diag1"
    assert code == 0
    res = json.loads((out / "result.json").read_text())
    assert res["status"] == "PASS" and set(res["arms"]) == {"original", "format_matched", "compressed_q70_1080"}
    for arm in ("original", "format_matched"):
        assert res["arms"][arm]["n_real"] == 400 and res["arms"][arm]["n_ai"] == 200
    assert len(read_csv(out / "predictions.csv")) == 600 * 3
    part = read_csv(out / "predictions-partial-arms.csv")
    assert len(part) == 599 and {r["arm"] for r in part} == {"size_matched_512"}
    assert "test-real-5" not in {r["item_id"] for r in part}
    fails = json.loads((out / "derive-failures.json").read_text())
    assert [(f["arm"], f["split"], f["item_id"]) for f in fails] == [("size_matched_512", "test", "test-real-5")]
    md = (out / "RESULTS.md").read_text()
    assert "A conversion failure in a diagnostic arm never changes the gated population" in md
    assert "`size_matched_512` covers 599 of 600 test units" in md
    assert "On 399 synthetic real-class images and 200 synthetic AI-class images" in md   # that arm's own N
    assert "On 400 synthetic real-class images and 200 synthetic AI-class images" in md   # the gated arms' N
    assert md.count("**gated**") == 2


def test_a_failure_in_a_gated_arm_still_stops_the_run(tiny, monkeypatch, capsys):
    real = gate_run.load_derive()

    class Wrapped:
        env = staticmethod(real.env)

        @staticmethod
        def derive(data, arm):
            if arm == "format_matched":
                raise ValueError("fixture: boom")
            return real.derive(data, arm)

    monkeypatch.setattr(gate_run, "load_derive", lambda: Wrapped)
    code = run(tiny, rungs=fx.fake_classifier())
    assert code != 0 and "format_matched" in capsys.readouterr().err


# ------------------------------------------------------------------ v2 study (L4 PROTOCOL.md part K, DAT-852)
# v2 = v1's accepted units plus a top-up of new real units. The v2 L4 folder holds groups-real.csv and manifest-real.csv
# as v1's rows and the top-up rows with a `study` column. A top-up spare replaces only a top-up unit, a v1 reserve only a
# v1 unit, so v1 and v2 reserves can share a spare_rank. The accepted file is second-look-accepted/1 with a `study` on
# every unit and rejection and the v2 PROTOCOL.md hash. The join output of L4's scripts/join_v2.py is a different shape
# (second-look-l4-accepted/1, accepted.<split>.<class>); these tests follow the gate's own schema and part K.

V2_IDS = ("test-real-5", "rej-1")       # a v2 reserve and a v2 rejected primary, both made test / landscape


def to_v2(d, v2=V2_IDS, columns=True):
    """The fixture's real-class rows in the v2 layout: test-real-4 (v1 reserve, landscape, rank 1) and test-real-5 (v2
    reserve, landscape, rank 1) share a rank; rej-0 (v1) and rej-1 (v2) are the two rejected landscape primaries."""
    for uid in v2:
        set_field(d, uid, topic="landscape")
    for u in d["units"]["test"]:
        if u["unit_id"] in v2:
            u["topic"] = "landscape"
    for r in d["rejections"]:
        if r["unit_id"] in v2:
            r["topic"] = "landscape"
    if not columns:
        return
    for key in ("groups", "manifest"):
        for r in d[key]["real"]:
            r["study"] = "v2" if r["unit_id"] in v2 else "v1"
    for split in ("dev", "test"):
        for u in d["units"][split]:
            u["study"] = "v2" if u["unit_id"] in v2 else "v1"
    for r in d["rejections"]:
        r["study"] = "v2" if r["unit_id"] in v2 else "v1"


def v2_build(tmp_path, more=None, **kw):
    def tweak(d):
        to_v2(d)
        if more:
            more(d)
    return fx.build(tmp_path, tweak=tweak, protocol_text="v2 protocol\n", **kw)


def unit(d, uid):
    return next(u for split in d["units"].values() for u in split if u["unit_id"] == uid)


def test_v1_and_v2_reserves_follow_their_own_frozen_order(tmp_path):
    """Both studies have a landscape reserve of spare_rank 1 in test. rej-0 (v1) takes test-real-4, rej-1 (v2) takes
    test-real-5; rej-2 (v1, building) has no reserve. The totals are the v1 fixture's, now split by study."""
    plan = reserve_plan(v2_build(tmp_path))
    assert plan.ctx["reserve_check"] == {"used": 2, "skipped": 0, "unfilled": 1, "rejected_unneeded": 0, "awaiting_fetch": 0}
    assert plan.ctx["reserve_check_by_study"] == {"v1": {"used": 1, "skipped": 0, "unfilled": 1},
                                                  "v2": {"used": 1, "skipped": 0, "unfilled": 0}}


def test_the_same_rows_without_a_study_column_share_a_spare_rank(tmp_path, capsys):
    """Without the column two landscape reserves with rank 1 are one queue with a repeated rank: the old refusal stands."""
    f = fx.build(tmp_path, tweak=lambda d: to_v2(d, columns=False))
    err = assert_refused(f, capsys, "real reserves for test / landscape share a spare_rank")
    assert "study" not in err                                                       # the v1 layout's messages are unchanged


def test_a_v1_layout_gives_no_reserve_check_by_study(tmp_path):
    assert "reserve_check_by_study" not in reserve_plan(fx.build(tmp_path)).ctx


def test_the_v2_layout_runs_every_check_and_the_dry_run(tmp_path, capsys):
    f = v2_build(tmp_path)
    code = gate_run.main(f.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 0, out
    assert "[ok] replacements follow the frozen reserve order" in out and "REFUSED" not in out


def test_the_v2_layout_goes_through_derive_and_scoring(tmp_path, capsys):
    """As test_a_harness_refusal_after_predictions_exist: the fixture's dev split is below the harness floor, so the run
    reaches the harness and is refused there, which is after every gate_run check, the derive and the scoring."""
    f = v2_build(tmp_path)
    code = run(f, rungs=fx.fake_classifier())
    err = capsys.readouterr().err
    assert code == 2 and "refused after files were written" in err
    assert (f.root / "runs" / "fx1" / "predictions.csv").is_file()


def test_a_v2_spare_that_was_passed_over_is_refused_with_its_study(tmp_path, capsys):
    """rej-1 (v2) is owed a replacement and the v2 reserve was not used. The v1 reserve test-real-4 does not stand in."""
    def more(d):
        d["units"]["test"] = [u for u in d["units"]["test"] if u["unit_id"] != "test-real-5"]
    err = assert_refused(v2_build(tmp_path, more), capsys,
                         "replacement out of order: real reserve test-real-5 (spare_rank 1, test / landscape / study v2) "
                         "is next in the frozen order and was not used, but 1 replacement(s) were still owed")


def test_a_v1_reserve_is_refused_when_only_a_top_up_unit_was_rejected(tmp_path, capsys):
    """The v1 landscape primary was not rejected; only the v2 one was. A v1 reserve cannot replace a top-up unit."""
    def more(d):
        d["rejections"] = [r for r in d["rejections"] if r["unit_id"] != "rej-0"]
        drop_unit(d, "real", "rej-0")
    err = assert_refused(v2_build(tmp_path, more), capsys,
                         "replacement out of order: real reserve test-real-4 (spare_rank 1, test / landscape / study v1) "
                         "was accepted")
    assert "no rejected primary unit there is owed a replacement" in err


def test_a_v2_spare_is_refused_when_only_a_v1_unit_was_rejected(tmp_path, capsys):
    """The other way: only v1's rej-0 was rejected, so the top-up spare cannot have been owed."""
    def more(d):
        d["rejections"] = [r for r in d["rejections"] if r["unit_id"] != "rej-1"]
        drop_unit(d, "real", "rej-1")
    err = assert_refused(v2_build(tmp_path, more), capsys,
                         "replacement out of order: real reserve test-real-5 (spare_rank 1, test / landscape / study v2) "
                         "was accepted")
    assert "no rejected primary unit there is owed a replacement" in err


def test_a_v2_reserve_that_was_itself_rejected_is_skipped_in_order_within_its_study(tmp_path):
    """Two v2 landscape spares (rank 1 rejected, rank 2 accepted) fill the one v2 place; the v1 queue is untouched."""
    def more(d):
        set_field(d, "test-real-5", spare_rank="2")
        for key in ("groups", "manifest"):
            d[key]["real"].append({"unit_id": "spare-v2", "split": "spare", "topic": "landscape", "spare_for": "test",
                                   "spare_rank": "1", "study": "v2", **({"role": "reserve"} if key == "manifest" else {})})
        d["rejections"].append({"unit_id": "spare-v2", "split": "test", "label": "real", "topic": "landscape",
                                "check": "C3 faces and persons", "reason": "fixture", "study": "v2"})
    plan = reserve_plan(v2_build(tmp_path, more))
    assert plan.ctx["reserve_check_by_study"]["v2"] == {"used": 1, "skipped": 1, "unfilled": 0}
    assert plan.ctx["reserve_check_by_study"]["v1"] == {"used": 1, "skipped": 0, "unfilled": 1}


def test_a_v2_layout_unit_without_a_study_is_refused(tmp_path, capsys):
    def more(d):
        del unit(d, "test-real-0")["study"]
    assert_refused(v2_build(tmp_path, more), capsys, "unit test-real-0: ACCEPTED.json does not say which study it is from")


def test_a_rejection_without_a_study_is_refused_in_the_v2_layout(tmp_path, capsys):
    def more(d):
        del d["rejections"][0]["study"]
    assert_refused(v2_build(tmp_path, more), capsys, "unit rej-0: ACCEPTED.json does not say which study it is from")


def test_a_unit_marked_with_the_other_study_is_refused(tmp_path, capsys):
    def more(d):
        unit(d, "test-real-0")["study"] = "v2"
    assert_refused(v2_build(tmp_path, more), capsys,
                   "unit test-real-0: ACCEPTED.json marks it study 'v2', but the frozen manifests make it study 'v1'")


def test_a_rejection_marked_with_the_other_study_is_refused(tmp_path, capsys):
    def more(d):
        d["rejections"][1]["study"] = "v1"                    # rej-1 is a v2 unit
    assert_refused(v2_build(tmp_path, more), capsys,
                   "unit rej-1: ACCEPTED.json marks it study 'v1', but the frozen manifests make it study 'v2'")


def test_groups_and_manifest_that_disagree_on_the_study_are_refused(tmp_path, capsys):
    def more(d):
        for r in d["manifest"]["real"]:
            if r["unit_id"] == "test-real-0":
                r["study"] = "v2"
    assert_refused(v2_build(tmp_path, more), capsys, "groups-real.csv and manifest-real.csv disagree on its study")


@pytest.mark.parametrize("bad", ["v3", ""])
def test_a_study_that_is_not_v1_or_v2_is_refused(tmp_path, capsys, bad):
    def more(d):
        for key in ("groups", "manifest"):
            for r in d[key]["real"]:
                if r["unit_id"] == "test-real-0":
                    r["study"] = bad
    assert_refused(v2_build(tmp_path, more), capsys, f"study {bad!r} is not one of ['v1', 'v2']")


def test_a_v2_ai_unit_is_refused(tmp_path, capsys):
    """v2 adds no AI unit (PROTOCOL.md K2)."""
    def more(d):
        for key in ("groups", "manifest"):
            for r in d[key]["ai"]:
                r["study"] = "v2" if r["unit_id"] == "test-ai-0" else "v1"
    assert_refused(v2_build(tmp_path, more), capsys, "unit test-ai-0: groups-ai.csv marks an AI unit study v2")


def test_v1_ai_units_may_carry_study_v1_in_the_accepted_file(tmp_path):
    def more(d):
        for split in ("dev", "test"):
            for u in d["units"][split]:
                u.setdefault("study", "v1")
    reserve_plan(v2_build(tmp_path, more))


def test_a_v1_folder_accepts_study_v1_and_refuses_study_v2(tmp_path, capsys):
    """A folder with no study column is v1 whole: its accepted file may say v1, never v2."""
    def mark(d, value):
        for split in ("dev", "test"):
            for u in d["units"][split]:
                u["study"] = "v1"
        unit(d, "test-real-0")["study"] = value
    reserve_plan(fx.build(tmp_path / "a", tweak=lambda d: mark(d, "v1")))
    assert_refused(fx.build(tmp_path / "b", tweak=lambda d: mark(d, "v2")), capsys,
                   "unit test-real-0: ACCEPTED.json marks it study 'v2', but the frozen manifests make it study 'v1'")


def test_the_v2_accepted_file_carries_the_v2_protocol_hash_that_the_lock_holds(tmp_path):
    f = v2_build(tmp_path)
    plan = reserve_plan(f)
    locked = json.loads((f.l4 / "LOCK.json").read_text())["sha256"]["PROTOCOL.md"]
    assert plan.ctx["accepted"]["protocol_sha256"] == locked == fx.sha(f.l4 / "PROTOCOL.md")
    assert locked == hashlib.sha256(b"v2 protocol\n").hexdigest()


def test_refuses_an_accepted_file_with_the_v1_protocol_hash_under_a_v2_lock(tmp_path, capsys):
    """Everything else about the file is re-pointed and in the ledger; only the PROTOCOL.md hash is v1's."""
    f = v2_build(tmp_path)
    acc = json.loads((f.l4 / "ACCEPTED.json").read_text())
    acc["protocol_sha256"] = hashlib.sha256(b"fixture protocol\n").hexdigest()
    (f.l4 / "ACCEPTED.json").write_text(json.dumps(acc, indent=1))
    rehash_accepted(f.l4)
    err = assert_refused(f, capsys, "ACCEPTED.json protocol_sha256 is '" + acc["protocol_sha256"] + "', but the LOCK.json "
                                    "entry for PROTOCOL.md has sha256 " + fx.sha(f.l4 / "PROTOCOL.md"))
    assert "not made under this protocol" in err


def test_refuses_an_accepted_file_with_no_protocol_hash(tmp_path, capsys):
    f = v2_build(tmp_path)
    acc = json.loads((f.l4 / "ACCEPTED.json").read_text())
    del acc["protocol_sha256"]
    (f.l4 / "ACCEPTED.json").write_text(json.dumps(acc, indent=1))
    rehash_accepted(f.l4)
    assert_refused(f, capsys, "ACCEPTED.json protocol_sha256 is None, but the LOCK.json entry for PROTOCOL.md")


def test_threshold_commit_finds_a_threshold_below_the_repo_top(tmp_path):
    # The real threshold lives in secondlook/rungs/, not at the repo top. git reads a pathspec from the folder it
    # runs in, so the check must run git from the top for a top-relative path (it refused a committed file, 29 Oct).
    repo = tmp_path / "repo"
    sub = repo / "secondlook" / "rungs"
    sub.mkdir(parents=True)
    fx.git(repo, "init", "-q")
    (sub / "classifier_threshold.json").write_text("{}\n")
    fx.git(repo, "add", "secondlook/rungs/classifier_threshold.json")
    fx.git(repo, "commit", "-q", "-m", "freeze")
    got = gate_run.threshold_commit(sub / "classifier_threshold.json")
    assert got["path"] == "secondlook/rungs/classifier_threshold.json"
    assert got["commit"] == fx.git(repo, "rev-parse", "HEAD").stdout.strip()
