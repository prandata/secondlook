"""The v2 two-lock arrangement (DAT-852): a gate folder with its own LOCK.json, plus L4's v2 LOCK.json.

L4's v2 join file (`ACCEPTED-v2.json`) names the sha256 of L4's v2 LOCK.json in its `lock_sha256`. The gate folder rebuilds
manifest-real.csv and groups-real.csv (a `study` column), so its own LOCK.json hashes those and lists the v2 LOCK's sha256 in
`v2_lock_sha256`. `--v2-lock PATH` hands the gate that v2 LOCK; calibrate.verify_v2_lock proves it against the folder, and
gate_splits, gate_run (checks 2 and 4) and calibrate (score, freeze) all apply the same rule. Fixtures only: the acceptance
on L4's real v2 files is in docs/GATE-RUN.md.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from secondlook import calibrate, gate_run, gate_splits
from tests import gate_fixture as fx
from tests.test_classifier import _l4 as classifier_l4, _scores, _sha, calibrate_main
from tests.test_gate_l4_schema import accepted_args, read_csv, to_l4, write_ledger
from tests.test_gate_run import low_floors, to_v2  # noqa: F401  (low_floors is autouse: the fixtures are small)


class V2:
    def __init__(self, f, accepted, v2_lock):
        self.f, self.accepted, self.v2_lock = f, accepted, v2_lock

    def argv(self, *extra, **kw):
        return self.f.argv(*extra, *accepted_args([self.accepted]), "--v2-lock", str(self.v2_lock), **kw)


def make_v2(tmp_path, mutate=None, gate_mutate=None, accepted_lock=None, name_in_gate=True, threshold_v2="match"):
    """A fixture folder turned into the v2 arrangement. `mutate(doc)` edits the v2 LOCK before its sha256 is taken, and
    the gate folder's LOCK.json and the accepted file follow it (so only that edit is wrong); `gate_mutate(doc)` edits the
    gate folder's LOCK.json after; `accepted_lock` overrides the accepted file's lock_sha256; `threshold_v2`: `match`
    writes the v2 LOCK's sha256 into the committed threshold.json, `none` leaves it out, anything else is written as is."""
    f = fx.build(tmp_path, tweak=to_v2, protocol_text="v2 protocol\n")
    accepted, = to_l4(f, "join", name="ACCEPTED-v2.json")
    table = json.loads((f.l4 / "LOCK.json").read_text())["sha256"]
    v2doc = {"sha256": {**table, "manifest-real-v2.csv": "1" * 64, "scripts/join_v2.py": "2" * 64},
             "context": [{"file": "ACCEPTED-v1-context.json", "sha256": fx.sha(f.l4 / "ACCEPTED-v1-context.json"),
                          "splits": ["dev", "test"]}],
             "gate_view": {n: fx.sha(f.l4 / n) for n in calibrate.V2_GATE_VIEW}}
    if mutate:
        mutate(v2doc)
    v2_lock = tmp_path / "v2" / "LOCK.json"
    v2_lock.parent.mkdir()
    v2_lock.write_text(json.dumps(v2doc, indent=1))
    v2_sha = fx.sha(v2_lock)

    acc = json.loads(accepted.read_text())
    acc["lock_sha256"] = accepted_lock or v2_sha
    accepted.write_text(json.dumps(acc, indent=1))
    write_ledger(f, [accepted])

    gate_doc = {"sha256": table}
    if name_in_gate:
        gate_doc["v2_lock_sha256"] = v2_sha
    if gate_mutate:
        gate_mutate(gate_doc)
    (f.l4 / "LOCK.json").write_text(json.dumps(gate_doc))
    thr = json.loads(f.threshold.read_text())
    thr["l4_lock_sha256"] = fx.sha(f.l4 / "LOCK.json")
    if threshold_v2 == "match":
        thr["v2_lock_sha256"] = v2_sha
    elif threshold_v2 != "none":
        thr["v2_lock_sha256"] = threshold_v2
    f.threshold.write_text(json.dumps(thr, indent=1))
    fx.git(f.repo, "add", "threshold.json")
    fx.git(f.repo, "commit", "-q", "-m", "freeze threshold under the v2 lock")
    return V2(f, accepted, v2_lock)


def plan(v, *extra):
    return gate_run.Plan(gate_run.build_parser().parse_args(v.argv(*extra)), rungs=fx.fake_classifier())


def splits(v, out="out-splits", **kw):
    return gate_splits.build(v.f.l4, [v.accepted], v.f.root / out, v2_lock=v.v2_lock, **kw)


# ------------------------------------------------------------------ what is accepted

def test_gate_splits_takes_the_v2_accepted_file_under_a_verified_v2_lock(tmp_path):
    v = make_v2(tmp_path)
    report = splits(v)
    v2_sha = fx.sha(v.v2_lock)
    assert report["l4"]["lock_sha256"] == fx.sha(v.f.l4 / "LOCK.json") != v2_sha          # the folder's own lock, as ever
    assert report["l4"]["v2_lock_sha256"] == v2_sha
    assert set(report["l4"]["v2_lock_gate_view"]) == set(calibrate.V2_GATE_VIEW)
    assert report["l4"]["v2_lock_context"] == [{"file": "ACCEPTED-v1-context.json",
                                                 "sha256": fx.sha(v.f.l4 / "ACCEPTED-v1-context.json")}]
    out = v.f.root / "out-splits"
    assert json.loads((out / "SPLIT.json").read_text()) == report
    assert len(read_csv(out / "dev.csv")) and len(read_csv(out / "test.csv"))


def test_the_same_v2_inputs_give_the_same_bytes(tmp_path):
    v = make_v2(tmp_path)
    splits(v, "a")
    splits(v, "b")
    for name in ("dev.csv", "test.csv", "SPLIT.json"):
        assert (v.f.root / "a" / name).read_bytes() == (v.f.root / "b" / name).read_bytes()


def test_without_the_v2_lock_the_same_file_is_refused_and_the_message_says_how_to_pass_it(tmp_path):
    v = make_v2(tmp_path)
    with pytest.raises(calibrate.Refused, match=r"lock_sha256 .* the accepted set was not made under this protocol.*"
                                                 r"pass --v2-lock PATH"):
        gate_splits.build(v.f.l4, [v.accepted], v.f.root / "out")
    assert not (v.f.root / "out").exists()


def test_gate_run_passes_checks_2_to_4_and_the_dry_run_under_the_v2_lock(tmp_path, capsys):
    v = make_v2(tmp_path)
    p = plan(v)
    p.run_checks()
    assert p.ctx["l4"]["v2_lock_sha"] == fx.sha(v.v2_lock) and p.ctx["l4"]["lock_sha"] != p.ctx["l4"]["v2_lock_sha"]
    code = gate_run.main(v.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 0 and "REFUSED" not in out and "All checks passed" in out, out


def test_gate_run_without_the_v2_lock_refuses_at_check_4_with_the_hint(tmp_path, capsys):
    v = make_v2(tmp_path)
    code = gate_run.main(v.f.argv("--dry-run", *accepted_args([v.accepted])), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2
    assert "[ok] L4 LOCK.json and the split files" in out
    assert "[REFUSED] ACCEPTED.json: ACCEPTED-v2.json lock_sha256" in out and "pass --v2-lock PATH" in out


def test_the_run_record_helper_names_the_v2_lock(tmp_path):
    """Both run records (an INVALID one and a scored one) are built from v2_provenance(ctx)."""
    v = make_v2(tmp_path)
    p = plan(v)
    p.run_checks()
    prov = gate_run.v2_provenance(p.ctx)
    assert prov["v2_lock_sha256"] == fx.sha(v.v2_lock)
    assert set(prov["v2_lock_gate_view"]) == set(calibrate.V2_GATE_VIEW) and prov["v2_lock_context"]
    assert gate_run.v2_provenance({"l4": {}}) == {}                  # a v1 run's record is unchanged


def test_a_v2_run_publishes_the_lock_in_gate_run_json(tmp_path, capsys, monkeypatch):
    for name in ("MIN_DEV_REAL", "MIN_DEV_AI", "MIN_REAL", "MIN_AI"):
        monkeypatch.setattr(gate_run, name, 1000)                    # not met: the run publishes INVALID and its record
    v = make_v2(tmp_path)
    code = gate_run.main(v.argv(), rungs=fx.fake_classifier())
    assert code == 3, capsys.readouterr().err
    rec = json.loads((v.f.root / "runs" / "fx1" / "gate-run.json").read_text())
    assert rec["status"] == "INVALID" and rec["v2_lock_sha256"] == fx.sha(v.v2_lock)
    assert rec["l4_lock_sha256"] == fx.sha(v.f.l4 / "LOCK.json")


def test_the_written_splits_pass_the_calibrate_lock_check_under_the_v2_lock(tmp_path):
    v = make_v2(tmp_path)
    splits(v)
    out = v.f.root / "out-splits"
    got = calibrate.verify_l4(str(v.f.l4), str(out / "SPLIT.json"), str(v.v2_lock))
    assert got["accepted_lock_sha"] == got["v2_lock_sha"] == fx.sha(v.v2_lock) != got["lock_sha"]
    plain = calibrate.verify_l4(str(v.f.l4), str(out / "SPLIT.json"))      # no --v2-lock: as before
    assert plain["accepted_lock_sha"] == plain["lock_sha"] and "v2_lock_sha" not in plain


def test_a_split_json_written_under_another_v2_lock_is_refused(tmp_path):
    v = make_v2(tmp_path)
    splits(v)
    sj = v.f.root / "out-splits" / "SPLIT.json"
    doc = json.loads(sj.read_text())
    doc["l4"]["v2_lock_sha256"] = "a" * 64
    sj.write_text(json.dumps(doc))
    with pytest.raises(calibrate.Refused, match="SPLIT.json was written under the v2 LOCK with sha256 a{64}"):
        calibrate.verify_l4(str(v.f.l4), str(sj), str(v.v2_lock))


# ------------------------------------------------------------------ the threshold

def test_a_threshold_that_names_the_v2_lock_is_accepted_and_one_naming_another_is_refused(tmp_path):
    v = make_v2(tmp_path)
    p = plan(v)
    p.run_checks()                                                   # check 8 holds the matching v2_lock_sha256
    bad = make_v2(tmp_path / "bad", threshold_v2="b" * 64)
    with pytest.raises(gate_run.Refused, match="threshold.json v2_lock_sha256 is not the sha256 of the v2 LOCK this run verified"):
        plan(bad).run_checks()


def test_a_v2_threshold_is_refused_in_a_run_without_a_v2_lock(tmp_path):
    """The accepted file here carries the folder's own lock sha256, so check 4 passes and check 8 is the one that stops."""
    v = make_v2(tmp_path)
    own = fx.sha(v.f.l4 / "LOCK.json")
    acc = json.loads(v.accepted.read_text())
    acc["lock_sha256"] = own
    v.accepted.write_text(json.dumps(acc))
    write_ledger(v.f, [v.accepted])
    p = gate_run.Plan(gate_run.build_parser().parse_args(v.f.argv(*accepted_args([v.accepted]))), rungs=fx.fake_classifier())
    with pytest.raises(gate_run.Refused, match=r"threshold.json v2_lock_sha256 .* \(no --v2-lock was given\)"):
        p.run_checks()


# ------------------------------------------------------------------ refusals: the v2 LOCK is not proved

def refused_everywhere(v, needle, capsys):
    """gate_splits, gate_run (as the dry run's check 2) and calibrate.verify_l4 all refuse with the same reason."""
    with pytest.raises(calibrate.Refused, match=needle):
        splits(v)
    assert not (v.f.root / "out-splits").exists()                    # nothing written
    code = gate_run.main(v.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "[REFUSED] L4 LOCK.json and the split files" in out and "not checked (an earlier check failed)" in out
    with pytest.raises(calibrate.Refused, match=needle):
        calibrate.verify_l4(str(v.f.l4), str(v.f.l4 / "splits" / "SPLIT.json"), str(v.v2_lock))


def test_a_v2_lock_with_a_wrong_gate_view_hash_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path, mutate=lambda d: d["gate_view"].update({"manifest-real.csv": "c" * 64}))
    refused_everywhere(v, r"gate folder's manifest-real.csv does not match the v2 LOCK's gate_view", capsys)


def test_a_v2_lock_whose_gate_view_leaves_out_a_real_class_file_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path, mutate=lambda d: d["gate_view"].pop("groups-real.csv"))
    refused_everywhere(v, r"gate_view does not hash \['groups-real.csv'\]", capsys)


def test_a_v2_lock_with_a_wrong_context_hash_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path, mutate=lambda d: d["context"][0].update({"sha256": "d" * 64}))
    refused_everywhere(v, r"ACCEPTED-v1-context.json is missing or is not the context file with sha256 d{64}", capsys)


def test_a_v2_lock_whose_context_file_is_not_in_the_gate_folder_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path)
    (v.f.l4 / "ACCEPTED-v1-context.json").unlink()
    refused_everywhere(v, "ACCEPTED-v1-context.json is missing or is not the context file", capsys)


@pytest.mark.parametrize("name", ["../ACCEPTED-v1-context.json", "/etc/hosts"])
def test_a_context_file_outside_the_gate_folder_is_refused(tmp_path, capsys, name):
    v = make_v2(tmp_path / "x", mutate=lambda d: d["context"][0].update({"file": name}))
    with pytest.raises(calibrate.Refused, match="is not inside the gate folder"):
        splits(v)


def test_a_v2_lock_that_is_not_the_one_the_gate_folder_names_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path)
    other = tmp_path / "other-LOCK.json"
    doc = json.loads(v.v2_lock.read_text())
    doc["floor_gib"] = 1                                             # same content otherwise: a different file, other sha256
    other.write_text(json.dumps(doc))
    v.v2_lock = other
    refused_everywhere(v, r"has sha256 [0-9a-f]{64}, but the gate folder's LOCK.json v2_lock_sha256 is", capsys)


def test_a_gate_folder_that_names_no_v2_lock_refuses_a_v2_lock(tmp_path, capsys):
    v = make_v2(tmp_path, name_in_gate=False)
    refused_everywhere(v, "names no v2_lock_sha256", capsys)


def test_a_malformed_v2_lock_sha_in_the_gate_folder_is_refused(tmp_path, capsys):
    v = make_v2(tmp_path, gate_mutate=lambda d: d.update({"v2_lock_sha256": "not-a-sha"}))
    refused_everywhere(v, "names no v2_lock_sha256", capsys)


def test_a_missing_v2_lock_file_is_refused(tmp_path):
    v = make_v2(tmp_path)
    with pytest.raises(calibrate.Refused, match="is not a file"):
        gate_splits.build(v.f.l4, [v.accepted], v.f.root / "out", v2_lock=tmp_path / "nope.json")


def test_a_v2_lock_without_a_gate_view_is_refused(tmp_path):
    v = make_v2(tmp_path, mutate=lambda d: d.pop("gate_view"))
    with pytest.raises(calibrate.Refused, match=r"unusable .*need"):
        splits(v)


def test_two_locks_that_disagree_on_a_file_the_gate_folder_holds_unchanged_are_refused(tmp_path, capsys):
    v = make_v2(tmp_path, mutate=lambda d: d["sha256"].update({"PROTOCOL.md": "e" * 64}))
    refused_everywhere(v, r"PROTOCOL.md has sha256 [0-9a-f]{64} in the gate folder's LOCK.json but e{64} in the v2 LOCK", capsys)


def test_the_v2_lock_entries_for_the_rebuilt_files_may_differ_from_the_gate_folders(tmp_path):
    """The v2 LOCK's own manifest-real.csv entry is L4's file, not the gate folder's rebuilt copy: gate_view is what ties them."""
    v = make_v2(tmp_path, mutate=lambda d: d["sha256"].update({"manifest-real.csv": "f" * 64, "groups-real.csv": "f" * 64}))
    splits(v)


# ------------------------------------------------------------------ refusals: the accepted file

def test_an_accepted_file_made_under_another_lock_is_refused_even_with_a_verified_v2_lock(tmp_path, capsys):
    v = make_v2(tmp_path, accepted_lock="9" * 64)
    with pytest.raises(calibrate.Refused, match=r"lock_sha256 is '9{64}', but the L4 folder's lock gives"):
        splits(v)
    assert not (v.f.root / "out-splits").exists()
    code = gate_run.main(v.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "[ok] L4 LOCK.json and the split files" in out
    assert "[REFUSED] ACCEPTED.json: ACCEPTED-v2.json lock_sha256 is '" + "9" * 64 in out
    assert "the v2 LOCK (--v2-lock, verified against the gate folder)" in out


def test_an_accepted_file_naming_the_gate_folders_own_lock_is_refused_in_a_v2_run(tmp_path, capsys):
    """A v2 run takes the v2 LOCK as the accepted files' lock: the folder's own LOCK.json is not what they were made under."""
    v = make_v2(tmp_path)
    own = fx.sha(v.f.l4 / "LOCK.json")
    acc = json.loads(v.accepted.read_text())
    acc["lock_sha256"] = own
    v.accepted.write_text(json.dumps(acc))
    write_ledger(v.f, [v.accepted])
    with pytest.raises(calibrate.Refused, match="was not made under this protocol, lock and screening"):
        splits(v)
    code = gate_run.main(v.argv("--dry-run"), rungs=fx.fake_classifier())
    assert code == 2 and "[REFUSED] ACCEPTED.json" in capsys.readouterr().out


def test_a_v2_accepted_file_with_v1s_protocol_hash_is_still_refused(tmp_path):
    v = make_v2(tmp_path)
    acc = json.loads(v.accepted.read_text())
    acc["protocol_sha256"] = "7" * 64
    v.accepted.write_text(json.dumps(acc))
    with pytest.raises(calibrate.Refused, match="protocol_sha256 is '7{64}'"):
        splits(v)


# ------------------------------------------------------------------ calibrate

def v2_for_classifier_l4(tmp_path):
    """The classifier tests' tiny L4 folder with a v2 LOCK over it: the gate folder LOCK.json names it."""
    fxl = classifier_l4(tmp_path)
    l4 = fxl["l4"]
    for n in calibrate.V2_GATE_VIEW:
        (l4 / n).write_text(f"locked {n}\n")
    v2doc = {"sha256": json.loads((l4 / "LOCK.json").read_text())["sha256"], "context": [],
             "gate_view": {n: _sha(l4 / n) for n in calibrate.V2_GATE_VIEW}}
    v2_lock = tmp_path / "v2-LOCK.json"
    v2_lock.write_text(json.dumps(v2doc))
    gate = json.loads((l4 / "LOCK.json").read_text())
    gate["v2_lock_sha256"] = _sha(v2_lock)
    (l4 / "LOCK.json").write_text(json.dumps(gate))
    return fxl, v2_lock


def test_calibrate_freeze_records_the_v2_lock_and_gate_run_reads_it_back(tmp_path):
    fxl, v2_lock = v2_for_classifier_l4(tmp_path)
    scores = [_scores(tmp_path, fxl["dev_rows"]), _scores(tmp_path, fxl["dev_rows"], arm="format_matched")]
    out = tmp_path / "threshold.json"
    argv = ["freeze", "--scores", *map(str, scores), "--split-json", str(fxl["split"]), "--dev-csv", str(fxl["dev"]),
            "--test-csv", str(fxl["test"]), "--l4-dir", str(fxl["l4"]), "--frozen-by", "test", "--out", str(out)]
    assert calibrate_main(argv + ["--v2-lock", str(v2_lock)]) == 0
    cfg = json.loads(out.read_text())
    assert cfg["v2_lock_sha256"] == _sha(v2_lock) and cfg["l4_lock_sha256"] == _sha(fxl["l4"] / "LOCK.json")
    out2 = tmp_path / "threshold-v1.json"
    assert calibrate_main(argv[:-1] + [str(out2)]) == 0
    assert "v2_lock_sha256" not in json.loads(out2.read_text())      # no --v2-lock: the file is as before


def test_calibrate_freeze_and_score_refuse_a_v2_lock_that_does_not_verify(tmp_path, capsys):
    fxl, v2_lock = v2_for_classifier_l4(tmp_path)
    (fxl["l4"] / "manifest-real.csv").write_text("changed after the v2 lock\n")        # breaks the gate folder's own lock too
    bad = tmp_path / "bad-LOCK.json"
    shutil.copy(v2_lock, bad)
    scores = [_scores(tmp_path, fxl["dev_rows"]), _scores(tmp_path, fxl["dev_rows"], arm="format_matched")]
    common = ["--split-json", str(fxl["split"]), "--l4-dir", str(fxl["l4"]), "--v2-lock", str(bad)]
    assert calibrate_main(["freeze", "--scores", *map(str, scores), "--dev-csv", str(fxl["dev"]),
                           "--test-csv", str(fxl["test"]), "--frozen-by", "t", "--out", str(tmp_path / "t.json"), *common]) == 2
    assert not (tmp_path / "t.json").exists()
    assert calibrate_main(["score", "--arm", "original", "--split", str(fxl["dev"]), "--out", str(tmp_path / "s.csv"), *common]) == 2
    assert "refused:" in capsys.readouterr().err


def test_calibrate_score_refuses_a_v2_lock_the_gate_folder_does_not_name(tmp_path, capsys):
    fxl, v2_lock = v2_for_classifier_l4(tmp_path)
    doc = json.loads(v2_lock.read_text())
    doc["extra"] = 1
    other = tmp_path / "other.json"
    other.write_text(json.dumps(doc))
    code = calibrate_main(["score", "--arm", "original", "--split", str(fxl["dev"]), "--split-json", str(fxl["split"]),
                           "--l4-dir", str(fxl["l4"]), "--v2-lock", str(other), "--out", str(tmp_path / "s.csv")])
    assert code == 2 and "v2_lock_sha256 is" in capsys.readouterr().err
