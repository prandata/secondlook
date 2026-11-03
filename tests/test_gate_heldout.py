"""The held-out diagnostic arm (L4 PROTOCOL.md part I6 to I8, DAT-852): fixtures and a fake classifier only.

They prove the wiring, the pins, the I7 counts and bounds, the I8 wording and every refusal. They say nothing about any
detector. The acceptance on L4's real files is a dry run, in docs/GATE-RUN.md.
"""
from __future__ import annotations

import csv
import json
import re

import pytest

from gate.stats import lower_bound
from secondlook import gate_heldout as gh
from secondlook import gate_run
from tests import gate_fixture as fx

GENS = {"GenA": 4, "GenB": 4}
MISS = {"HFCF_PublicEval_0_2", "HFCF_PublicEval_1_1", "HFCF_PublicEval_1_3"}   # image stems the fake detector misses


class Held:
    def __init__(self, f, root, hdir, v2_lock, threshold, gate_run_dir, out):
        self.f, self.root, self.hdir, self.v2_lock = f, root, hdir, v2_lock
        self.threshold, self.gate_run_dir, self.out = threshold, gate_run_dir, out

    def argv(self, *extra, dry=False, out=None):
        a = ["--l4-dir", str(self.f.l4), "--heldout", str(self.hdir / "ACCEPTED-heldout.json"),
             "--threshold", str(self.threshold), "--v2-lock", str(self.v2_lock),
             "--gate-run", str(self.gate_run_dir), "--dev-csv", str(self.f.l4 / "splits" / "dev.csv"),
             "--out", str(out or self.out), "--run-id", "ho1", *extra]
        return a + (["--dry-run"] if dry else [])

    def run(self, *extra, rungs=None, out=None):
        return gh.main(self.argv(*extra, out=out), rungs=rungs if rungs is not None else fx.fake_classifier(miss_ids=MISS))


def sha(p):
    return fx.sha(p)


def jdump(path, obj):
    path.write_text(json.dumps(obj, indent=1))


def make_held(tmp_path, monkeypatch, lock_files=True):
    f = fx.build(tmp_path)
    root, hdir = tmp_path, f.l4 / "heldout-v2"
    (hdir / "images").mkdir(parents=True)
    # the v2 LOCK: its table holds the locked files the held-out file names
    v2 = tmp_path / "v2" / "LOCK.json"
    v2.parent.mkdir()
    table = {n: sha(f.l4 / n) for n in ("PROTOCOL.md", "SCREEN-CONFIG.json")}
    jdump(v2, {"sha256": table})
    # the held-out images, manifest, candidates, provenance
    entries, manifest, prov = [], [], []
    for s, (g, n) in enumerate(GENS.items()):
        (hdir / "images" / g).mkdir()
        for i in range(n):
            stem = f"HFCF_PublicEval_{s}_{i}"
            p = hdir / "images" / g / f"{stem}.png"
            fx.make_ai(p, 100 + s * 10 + i)
            rel = f"heldout-v2/images/{g}/{stem}.png"
            e = {"id": f"{g}/{stem}", "file": rel, "generator": g, "sha256": sha(p), "format": "PNG"}
            entries.append(e)
            manifest.append({"id": e["id"], "generator": g, "path": rel, "sha256": e["sha256"]})
            hit = (s, i) == (0, 0)
            prov.append({"id": e["id"], "generator": g, "c2pa": "none", "exif_rung": "[{}]" if hit else "none",
                         "iptc_rung": "none", "watermark_present": "False", "all_empty": "False" if hit else "True"})
    fx.write_csv(hdir / "manifest-heldout.csv", ("id", "generator", "path", "sha256"), manifest)
    fx.write_csv(hdir / "heldout-provenance.csv", ("id", "generator", "c2pa", "exif_rung", "iptc_rung",
                                                   "watermark_present", "all_empty"), prov)
    (hdir / "candidates.csv").write_text("id\n" + "\n".join(e["id"] for e in entries) + "\n")
    (hdir / "publiceval-meta.csv").write_text("id\nfixture\n")
    monkeypatch.setattr(gh, "PREREGISTERED", {"candidates.csv": sha(hdir / "candidates.csv"),
                                               "publiceval-meta.csv": sha(hdir / "publiceval-meta.csv")})
    parquet = {f"HFCF_PublicEval_{i}.parquet": f"{i}" * 64 for i in range(6)}
    monkeypatch.setattr(gh, "PARQUET_LFS_SHA256", parquet)
    acc = {"schema": gh.ACCEPTED_SCHEMA, "arm": gh.HELDOUT_ARM, "lock_sha256": sha(v2),
           "locked_files_sha256": table, "protocol_sha256": table["PROTOCOL.md"],
           "screen_config_sha256": table["SCREEN-CONFIG.json"], "derive_script_sha256": gate_run.DERIVE_SHA256,
           "manifest_heldout_csv_sha256": sha(hdir / "manifest-heldout.csv"),
           "candidates_csv_sha256": sha(hdir / "candidates.csv"), "accepted": entries, "n_accepted": len(entries),
           "per_generator": {g: {"candidates": n + 2, "passed": n + 1, "rejected": 2, "accepted": n,
                                 "rejected_by_check": {"C3": 2}} for g, n in GENS.items()},
           "rejected": [{"id": "x", "check": "C3"}] * 4}
    jdump(hdir / "ACCEPTED-heldout.json", acc)
    if lock_files:
        write_lock(hdir, parquet)
    # the threshold, now under the v2 LOCK, committed
    thr = f.threshold
    cfg = json.loads(thr.read_text())
    cfg["v2_lock_sha256"] = sha(v2)
    jdump(thr, cfg)
    fx.git(f.repo, "add", "threshold.json")
    fx.git(f.repo, "commit", "-q", "-m", "freeze threshold under the v2 lock")
    # the main gate run: its record and its result
    grun = tmp_path / "main-run"
    grun.mkdir()
    jdump(grun / "gate-run.json", {"run_id": "main1", "threshold_sha256": sha(thr), "v2_lock_sha256": sha(v2)})
    jdump(grun / "result.json", {"arms": {arm: {"false_positive": {"k": 1, "n": 461, "upper_bound": 0.0103}}
                                            for arm in gh.HELDOUT_ARMS}})
    return Held(f, root, hdir, v2, thr, grun, tmp_path / "out")


def write_lock(hdir, parquet):
    jdump(hdir / "LOCK-heldout.json", {"schema": gh.LOCK_SCHEMA, "sha256": {n: sha(hdir / n) for n in gh.LOCK_FILES},
                                       "parquet_lfs_sha256": parquet})


@pytest.fixture
def held(tmp_path, monkeypatch):
    return make_held(tmp_path, monkeypatch)


def refused(held, capsys, needle, rungs=None, *extra, **kw):
    calls = []
    code = held.run(*extra, rungs=rungs if rungs is not None else fx.fake_classifier(miss_ids=MISS, calls=calls),
                    **kw)
    err = capsys.readouterr().err
    assert code == 2, err
    assert needle in err, err
    assert not calls, "the classifier was called on a refused run"
    assert not (held.out / "predictions-heldout.csv").exists()
    assert not (held.out / "RESULTS-heldout.md").exists()
    return err


# ------------------------------------------------------------------ I7: the bounds and the counts

def test_lower_bound_matches_closed_forms():
    # k = n: L = 0.05 ** (1 / n);  k = 1: L = 1 - 0.95 ** (1 / n);  k = 0: L = 0
    assert gh.lower(300, 300) == pytest.approx(0.05 ** (1 / 300), abs=1e-12)
    assert gh.lower(1, 17) == pytest.approx(1 - 0.95 ** (1 / 17), abs=1e-12)
    assert gh.lower(0, 17) == 0.0
    assert gh.lower(0, 0) is None
    assert gh.lower(16, 17) == lower_bound(16, 17, 0.95)


def test_lower_bound_known_value_and_scipy_when_installed():
    assert gh.lower(8, 10) == pytest.approx(0.4931, abs=5e-5)         # exact one-sided 95%, the textbook figure
    stats = pytest.importorskip("scipy.stats")
    for k, n in ((8, 10), (270, 300), (15, 17), (299, 300)):
        assert gh.lower(k, n) == pytest.approx(stats.beta.ppf(0.05, k, n - k + 1), abs=1e-9)


def test_catch_counts_per_arm_and_generator_and_an_abstention_is_not_a_catch():
    gens = {"a1": "GenA", "a2": "GenA", "b1": "GenB"}
    preds = []
    for arm in gh.HELDOUT_ARMS:
        preds += [{"item_id": "a1", "arm": arm, "verdict": "ai"}, {"item_id": "a2", "arm": arm, "verdict": "abstain"},
                  {"item_id": "b1", "arm": arm, "verdict": "real"}]
    c = gh.catch_counts(preds, gens)
    assert set(c) == set(gh.HELDOUT_ARMS)
    for arm in gh.HELDOUT_ARMS:
        assert (c[arm]["a"], c[arm]["n"]) == (1, 3)
        assert c[arm]["per_generator"] == {"GenA": {"a": 1, "n": 2}, "GenB": {"a": 0, "n": 1}}


def test_provenance_hits_per_generator(tmp_path):
    p = tmp_path / "prov.csv"
    fx.write_csv(p, ("id", "generator", "c2pa", "exif_rung", "iptc_rung", "watermark_present", "all_empty"), [
        {"id": "a", "generator": "G", "c2pa": "none", "exif_rung": "[{}]", "iptc_rung": "none",
         "watermark_present": "False", "all_empty": "False"},
        {"id": "b", "generator": "G", "c2pa": "none", "exif_rung": "none", "iptc_rung": "none",
         "watermark_present": "False", "all_empty": "True"},
        {"id": "z", "generator": "G", "c2pa": "yes", "exif_rung": "none", "iptc_rung": "none",
         "watermark_present": "False", "all_empty": "False"}])
    out = gh.provenance_by_generator(p, {"a", "b"})                   # z is not in the arm
    assert out == {"G": {"n": 2, "rung_hit": 1, "c2pa": 0, "exif_rung": 1, "iptc_rung": 0, "watermark": 0,
                         "any_metadata": 1}}


# ------------------------------------------------------------------ a full run

def test_full_run_writes_the_report_and_scores_exactly_the_accepted_images(held, capsys):
    calls = []
    assert held.run(rungs=fx.fake_classifier(miss_ids=MISS, calls=calls)) == 0
    out = held.out
    for name in ("RESULTS-heldout.md", "heldout-run.json", "predictions-heldout.csv", "baseline.json", "counts.json",
                 "hashes-derived.csv", "derive-env.json"):
        assert (out / name).is_file(), name
    preds = list(csv.DictReader((out / "predictions-heldout.csv").open()))
    accepted_ids = {e["id"] for e in json.loads((held.hdir / "ACCEPTED-heldout.json").read_text())["accepted"]}
    assert {r["item_id"] for r in preds} == accepted_ids
    assert len(preds) == 8 * len(gh.HELDOUT_ARMS) == len(calls)       # 3 arms, nothing else scored
    assert {r["arm"] for r in preds} == set(gh.HELDOUT_ARMS)
    assert "size_matched_512" not in {p.parent.parent.name for p in (out / "derived").rglob("*.jpg")}
    counts = json.loads((out / "counts.json").read_text())["counts"]
    assert counts["original"]["a"] == 5 and counts["original"]["n"] == 8      # 3 of 8 are forced misses
    assert counts["original"]["per_generator"] == {"GenA": {"a": 3, "n": 4}, "GenB": {"a": 2, "n": 4}}
    record = json.loads((out / "heldout-run.json").read_text())
    pins = record["pins"]
    assert record["gated"] is False and record["n_images"] == 8
    assert pins["heldout_accepted_sha256"] == sha(held.hdir / "ACCEPTED-heldout.json")
    assert pins["threshold_sha256"] == sha(held.threshold)
    assert pins["derive_py_sha256"] == sha(gate_run.DERIVE_PY)
    assert pins["v2_lock_sha256"] == sha(held.v2_lock)
    assert pins["lock_heldout_sha256"] == sha(held.hdir / "LOCK-heldout.json")
    assert pins["classifier_weights_sha256"] == "0" * 64
    assert pins["threshold_commit"]["commit"]
    assert record["predictions_sha256"] == sha(out / "predictions-heldout.csv")


def test_report_carries_the_i8_wording_and_nothing_banned(held):
    assert held.run() == 0
    md = (held.out / "RESULTS-heldout.md").read_text()
    lb = gh.pct(gh.lower(5, 8))
    sentence = gh.ALLOWED.format(n_real=461, m=8, g=2, fa_k=1, fa_ub="1.030%", a=5, lb=lb, arm="original", per_gen=4)
    assert sentence in md
    assert md.count("Held-out diagnostic, reported, not gated:") == len(gh.HELDOUT_ARMS)
    assert gh.LIMIT_LINE in md
    assert md.count(gh.LIMIT_LINE) == len(gh.HELDOUT_ARMS)                  # printed under each arm's table
    for arm in gh.HELDOUT_ARMS:
        assert f"arm: {arm}." in md
    low = md.lower()
    for phrase in gh.BANNED:
        assert phrase not in low
    # "held-out" never stands without "held out of the detector's training by its authors" in its paragraph
    for para in re.split(r"\n\s*\n", md):
        if "held-out" in para.lower():
            assert gh.AUTHORS_PHRASE in para, para
    # every catch rate is printed with its bound and its n; per-generator rows are labelled descriptive
    assert "| GenA | 3/4 | 75.000% |" in md and "descriptive" in md
    assert "tree fitted on DiffusionDB dev" in md
    assert "Cleared screening" in md and "passed the gate" not in low
    assert "pooled" not in low


def test_the_allowed_sentence_is_the_protocols_text():
    # I8 word for word, with the slots filled in
    s = gh.ALLOWED.format(n_real="N", m="M", g="18", fa_k="x", fa_ub="y", a="a", lb="b", arm="ARM", per_gen=17)
    assert s == ("Held-out diagnostic, reported, not gated: on N unit-level real photographs (curated Wikimedia "
                 "Commons, pre-2022) and M images from 18 generators held out of the detector's training by its "
                 "authors (their public evaluation set, Community Forensics PublicEval, CC BY 4.0), observed "
                 "false-alarm rate x/N, one-sided 95% upper bound y; observed catch rate a/M, lower bound b; arm: "
                 "ARM. About 17 images per generator; per-generator figures are descriptive.")
    assert gh.LIMIT_LINE.startswith("These generators and images were chosen and made by the detector's authors")
    assert gh.LIMIT_LINE.endswith("reduce the format difference only.")


def test_pre_registered_hashes_are_the_ones_in_the_protocol():
    assert gh.PREREGISTERED["candidates.csv"].startswith("76bd065c")
    assert gh.PREREGISTERED["publiceval-meta.csv"].startswith("38affc79")
    assert len(gh.PARQUET_LFS_SHA256) == 6 and all(len(v) == 64 for v in gh.PARQUET_LFS_SHA256.values())


# ------------------------------------------------------------------ refusals (nothing scored, nothing written)

def test_refuses_when_the_threshold_is_missing(held, capsys):
    held.threshold.unlink()
    refused(held, capsys, "no frozen threshold")


def test_refuses_a_threshold_never_committed(tmp_path, monkeypatch, capsys):
    h = make_held(tmp_path, monkeypatch)
    loose = tmp_path / "loose-threshold.json"
    loose.write_text(h.threshold.read_text())
    h.threshold = loose                                                  # outside any git repo
    refused(h, capsys, "not inside a git repository")


def test_refuses_a_threshold_edited_after_its_commit(held, capsys):
    cfg = json.loads(held.threshold.read_text())
    cfg["threshold"] = 0.7
    jdump(held.threshold, cfg)
    refused(held, capsys, "differs from its last commit")


def test_refuses_a_threshold_frozen_under_another_v2_lock_or_none(tmp_path, monkeypatch, capsys):
    h = make_held(tmp_path, monkeypatch)
    cfg = json.loads(h.threshold.read_text())
    cfg["v2_lock_sha256"] = "a" * 64
    jdump(h.threshold, cfg)
    fx.git(h.f.repo, "add", "threshold.json")
    fx.git(h.f.repo, "commit", "-q", "-m", "another lock")
    refused(h, capsys, "was not frozen under this v2 LOCK")
    del cfg["v2_lock_sha256"]
    jdump(h.threshold, cfg)
    fx.git(h.f.repo, "add", "threshold.json")
    fx.git(h.f.repo, "commit", "-q", "-m", "no lock")
    refused(h, capsys, "was not frozen under this v2 LOCK")


def test_refuses_a_threshold_committed_in_the_future(tmp_path, monkeypatch, capsys):
    h = make_held(tmp_path, monkeypatch)
    cfg = json.loads(h.threshold.read_text())
    cfg["threshold"] = 0.55
    jdump(h.threshold, cfg)
    fx.git(h.f.repo, "add", "threshold.json")
    fx.git(h.f.repo, "commit", "-q", "-m", "future", date="2099-01-01T00:00:00Z")
    refused(h, capsys, "not before now")


def test_refuses_a_threshold_without_a_training_data_statement(held, capsys):
    cfg = json.loads(held.threshold.read_text())
    cfg["training_data_statement"] = ""
    jdump(held.threshold, cfg)
    fx.git(held.f.repo, "add", "threshold.json")
    fx.git(held.f.repo, "commit", "-q", "-m", "no statement")
    refused(held, capsys, "no training_data_statement")


def test_refuses_another_v2_lock(held, capsys):
    doc = json.loads(held.v2_lock.read_text())
    doc["sha256"]["extra"] = "b" * 64
    jdump(held.v2_lock, doc)
    refused(held, capsys, "was made under the lock with sha256")


def test_refuses_a_locked_file_that_changed_after_the_lock(held, capsys):
    (held.f.l4 / "PROTOCOL.md").write_text("edited after the lock\n")
    refused(held, capsys, "no longer has the sha256")


def test_refuses_a_held_out_file_naming_a_hash_the_v2_lock_does_not_hold(held, capsys):
    p = held.hdir / "ACCEPTED-heldout.json"
    acc = json.loads(p.read_text())
    acc["locked_files_sha256"]["PROTOCOL.md"] = "c" * 64
    jdump(p, acc)
    refused(held, capsys, "but the v2 LOCK holds")


def test_refuses_another_derive_recipe(held, capsys):
    p = held.hdir / "ACCEPTED-heldout.json"
    acc = json.loads(p.read_text())
    acc["derive_script_sha256"] = "d" * 64
    jdump(p, acc)
    refused(held, capsys, "not the frozen recipe's")


def test_refuses_a_derive_py_that_is_not_the_frozen_recipe(held, capsys, monkeypatch):
    other = held.root / "derive.py"
    other.write_text("# not the recipe\n")
    monkeypatch.setattr(gate_run, "DERIVE_PY", other)
    refused(held, capsys, "not the frozen L4 recipe's")


def test_refuses_candidates_that_are_not_the_pre_registered_list(held, capsys, monkeypatch):
    monkeypatch.setattr(gh, "PREREGISTERED", {**gh.PREREGISTERED, "candidates.csv": "f" * 64})
    refused(held, capsys, "not the list pre-registered")


def test_refuses_a_tampered_image(held, capsys):
    p = next((held.hdir / "images" / "GenA").glob("*.png"))
    p.write_bytes(p.read_bytes() + b"\0")
    refused(held, capsys, "sha256 differs")


def test_refuses_a_missing_image_and_never_drops_it(held, capsys):
    next((held.hdir / "images" / "GenB").glob("*.png")).unlink()
    refused(held, capsys, "not found at")


def test_refuses_an_image_the_manifest_does_not_hold(held, capsys):
    p = held.hdir / "manifest-heldout.csv"
    rows = list(csv.DictReader(p.open()))[1:]
    fx.write_csv(p, ("id", "generator", "path", "sha256"), rows)
    cfg = json.loads((held.hdir / "ACCEPTED-heldout.json").read_text())
    cfg["manifest_heldout_csv_sha256"] = sha(p)
    jdump(held.hdir / "ACCEPTED-heldout.json", cfg)
    write_lock(held.hdir, gh.PARQUET_LFS_SHA256)
    refused(held, capsys, "is not in manifest-heldout.csv")


def test_refuses_when_accepted_and_manifest_disagree_on_a_sha256(held, capsys):
    p = held.hdir / "ACCEPTED-heldout.json"
    acc = json.loads(p.read_text())
    acc["accepted"][0]["sha256"] = "9" * 64
    jdump(p, acc)
    write_lock(held.hdir, gh.PARQUET_LFS_SHA256)
    refused(held, capsys, "disagree on its sha256")


def test_refuses_a_file_that_leaves_the_l4_folder(held, capsys):
    p = held.hdir / "ACCEPTED-heldout.json"
    acc = json.loads(p.read_text())
    acc["accepted"][0]["file"] = "../outside.png"
    jdump(p, acc)
    write_lock(held.hdir, gh.PARQUET_LFS_SHA256)
    m = held.hdir / "manifest-heldout.csv"
    rows = list(csv.DictReader(m.open()))
    rows[0]["path"] = "../outside.png"
    fx.write_csv(m, ("id", "generator", "path", "sha256"), rows)
    acc["manifest_heldout_csv_sha256"] = sha(m)
    jdump(p, acc)
    write_lock(held.hdir, gh.PARQUET_LFS_SHA256)
    refused(held, capsys, "is not inside the L4 folder")


def test_refuses_without_lock_heldout(tmp_path, monkeypatch, capsys):
    h = make_held(tmp_path, monkeypatch, lock_files=False)
    refused(h, capsys, "requires LOCK-heldout.json to be written before any held-out image is scored")


def test_refuses_a_file_changed_after_lock_heldout(held, capsys):
    p = held.hdir / "heldout-provenance.csv"
    p.write_text(p.read_text() + "\n")
    refused(held, capsys, "does not record the current sha256 of heldout-provenance.csv")


def test_refuses_lock_heldout_with_other_parquet_hashes(held, capsys):
    p = held.hdir / "LOCK-heldout.json"
    doc = json.loads(p.read_text())
    doc["parquet_lfs_sha256"]["HFCF_PublicEval_0.parquet"] = "ab" * 32
    jdump(p, doc)
    refused(held, capsys, "differs from the six pre-registered")


def test_refuses_the_main_run_of_another_threshold_or_lock_or_none(held, capsys):
    rec = held.gate_run_dir / "gate-run.json"
    doc = json.loads(rec.read_text())
    jdump(rec, {**doc, "threshold_sha256": "1" * 64})
    refused(held, capsys, "another threshold.json")
    jdump(rec, {**doc, "v2_lock_sha256": "2" * 64})
    refused(held, capsys, "not made under this v2 LOCK")
    (held.gate_run_dir / "result.json").unlink()
    jdump(rec, doc)
    refused(held, capsys, "has not been made")


def test_refuses_a_main_run_without_a_false_alarm_figure(held, capsys):
    res = held.gate_run_dir / "result.json"
    doc = json.loads(res.read_text())
    del doc["arms"]["compressed_q70_1080"]
    jdump(res, doc)
    refused(held, capsys, "no false-alarm figure for arm compressed_q70_1080")


def test_refuses_a_dev_split_other_than_the_one_the_threshold_was_frozen_for(held, capsys):
    p = held.f.l4 / "splits" / "dev.csv"
    p.write_text(p.read_text() + "\n")
    refused(held, capsys, "is not the dev split threshold.json was frozen against")


def test_refuses_a_tampered_dev_original(held, capsys):
    row = next(csv.DictReader((held.f.l4 / "splits" / "dev.csv").open()))
    p = held.f.l4 / "images" / "original" / row["label"] / (row["item_id"] + (".jpg" if row["label"] == "real" else ".png"))
    p.write_bytes(p.read_bytes() + b"\0")
    refused(held, capsys, "is not the file dev.csv names")


def test_refuses_before_deriving_when_the_real_classifier_weights_are_missing(held, capsys, monkeypatch):
    monkeypatch.setenv("SECONDLOOK_COMMFOR_WEIGHTS", str(held.root / "no-such-weights.safetensors"))
    code = gh.main(held.argv())                                  # no injected rungs: the installed ones
    err = capsys.readouterr().err
    assert code == 2 and "classifier weights not found" in err
    assert not (held.out / "derived").exists()


def test_refuses_weights_other_than_the_ones_the_threshold_was_frozen_for(held, capsys, monkeypatch):
    w = held.root / "weights.safetensors"
    w.write_bytes(b"not the pinned weights")
    monkeypatch.setenv("SECONDLOOK_COMMFOR_WEIGHTS", str(w))
    code = gh.main(held.argv())
    err = capsys.readouterr().err
    assert code == 2 and "not the ones threshold.json was frozen for" in err
    assert not (held.out / "derived").exists()


def test_refuses_when_there_is_no_classifier_rung(held, capsys):
    refused(held, capsys, "classifier rung is not available", rungs=[("exif", lambda p: [])])


def test_refuses_a_run_folder_that_already_holds_files(held, capsys):
    held.out.mkdir()
    (held.out / "x").write_text("x")
    refused(held, capsys, "already holds files")


def test_refuses_when_an_image_gets_no_classifier_score(held, capsys):
    from secondlook.types import Evidence

    def no_score(path):
        return [Evidence(rung="classifier", finding="f", proves="p", does_not_prove="d", strength="none", raw={})]
    code = held.run(rungs=[("classifier", no_score)])
    err = capsys.readouterr().err
    assert code == 2 and "expected exactly one classifier score" in err
    assert not (held.out / "RESULTS-heldout.md").exists()


# ------------------------------------------------------------------ the dry run

def test_dry_run_checks_everything_and_scores_and_writes_nothing(held, capsys):
    calls = []
    code = gh.main(held.argv(dry=True), rungs=fx.fake_classifier(calls=calls))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "DRY RUN" in out and "[REFUSED]" not in out and "8 from 2 generators" in out
    assert not calls and not held.out.exists()


def test_dry_run_with_no_threshold_stops_there_and_does_not_check_what_needs_it(held, capsys):
    held.threshold.unlink()
    calls = []
    code = gh.main(held.argv(dry=True), rungs=fx.fake_classifier(calls=calls))
    out = capsys.readouterr().out
    assert code == 2
    assert "[REFUSED] frozen threshold.json: no frozen threshold" in out
    assert "[not checked] dev split for the shortcut baseline: not checked (needs: frozen threshold.json)" in out
    assert "[ok] manifest-heldout.csv and the held-out images" in out
    assert "[ok] LOCK-heldout.json" in out
    assert not calls and not held.out.exists()


def _argv_for(held, accepted, dry=True):
    old = str(held.hdir / "ACCEPTED-heldout.json")
    return [str(accepted) if a == old else a for a in held.argv(dry=dry)]


def test_lock_heldout_pins_the_accepted_file_that_heldout_names(held, capsys):
    # Under D3 (ERRATA-v2.md) the held-out accepted file is ACCEPTED-heldout-D3.json. The lock must pin that file,
    # not the D1' ACCEPTED-heldout.json beside it (29 Oct).
    d3 = held.hdir / "ACCEPTED-heldout-D3.json"
    d3.write_bytes((held.hdir / "ACCEPTED-heldout.json").read_bytes())
    lock = json.loads((held.hdir / "LOCK-heldout.json").read_text())
    names = gh.LOCK_FILES[:-1] + (d3.name,)
    jdump(held.hdir / "LOCK-heldout.json", {**lock, "sha256": {n: sha(held.hdir / n) for n in names}})
    gh.main(_argv_for(held, d3), rungs=fx.fake_classifier())
    assert "[ok] LOCK-heldout.json" in capsys.readouterr().out


def test_refuses_a_lock_that_pins_another_accepted_file(held, capsys):
    d3 = held.hdir / "ACCEPTED-heldout-D3.json"
    d3.write_bytes((held.hdir / "ACCEPTED-heldout.json").read_bytes() + b"\n")    # the lock pins only the D1' file
    gh.main(_argv_for(held, d3), rungs=fx.fake_classifier())
    assert "does not record the current sha256 of ACCEPTED-heldout-D3.json" in capsys.readouterr().out
