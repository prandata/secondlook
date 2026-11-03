"""The L4-to-gate bridge (DAT-852): the gate reads L4's real accepted schema, and a step writes the split files from it.

The fixture is the one the gate-run tests use. Its native `second-look-accepted/1` file is turned into the shape L4's
screen.py, rederive_d1prime.py and join_v2.py write (`accepted.<split>.<class>`, `rejected`, a role of `replacement`),
so the same units and the same refusals are read through both schemas. Offline; nothing is measured.
"""
import csv
import json
from collections import Counter
from types import SimpleNamespace

import pytest

from gate import harness
from secondlook import gate_run, gate_splits, l4_accepted
from tests import gate_fixture as fx
from tests.test_gate_run import low_floors, real_floors, to_v2  # noqa: F401  (fixtures: low_floors is autouse)

L4 = l4_accepted.L4_SCHEMA


def phash(i: int) -> str:
    return f"{(i * 0x9E3779B97F4A7C15) % 2**64:016x}"


def entry(f, u, i):
    label = u["label"]
    stem = u["unit_id"]
    ext = "jpg" if label == "real" else "png"
    e = {"unit_id": stem, "sha256": u["sha256"], "topic": u["topic"], "image_id": stem, "phash": phash(i),
         "file": f"images/original/{label}/{stem}.{ext}", "role": "replacement" if u["role"] == "reserve" else "primary",
         "replaces": None, "reasons": {"C1": "ok"}}
    if "study" in u:
        e["study"] = u["study"]
    return e


def rejected_entry(r):
    e = {"unit_id": r["unit_id"], "class": r["label"], "split": r["split"], "topic": r.get("topic"), "check": r["check"],
         "reason": r["reason"], "role": r.get("role", "primary"), "sha256": "0" * 64}
    if "study" in r:
        e["study"] = r["study"]
    return e


def write_ledger(f, paths):
    f.l4.joinpath("LEDGER.md").write_text("# ledger\n\n" + "".join(f"{p.name} sha256 {fx.sha(p)}\n" for p in paths))


def to_l4(f, mode="one", complete=True, unfetched=(), name="ACCEPTED-L4.json"):
    """Writes L4-schema accepted files from the fixture's native ACCEPTED.json and returns their paths.
    mode `one`: one file for dev and test. `split`: one file per split, as v1's dev and test screenings are.
    `join`: a v2 join file (units marked with their study) plus the v1 context file that holds v1's rejections."""
    acc = json.loads((f.l4 / "ACCEPTED.json").read_text())
    n = {"i": 0}
    lists = {s: {"real": [], "ai": []} for s in ("dev", "test")}
    for s in ("dev", "test"):
        for u in acc["units"][s]:
            lists[s][u["label"]].append(entry(f, u, n["i"]))
            n["i"] += 1
    hashes = {k: acc[k] for k in ("protocol_sha256", "lock_sha256", "screen_config_sha256")}
    base = {"schema": L4, "complete": complete, "unfetched_reserves": list(unfetched), **hashes}

    def one(splits, rej, fname, extra=None):
        doc = {**base, "accepted": {s: lists[s] for s in splits}, "splits_screened": list(splits),
               "rejected": [rejected_entry(r) for r in rej if r["split"] in splits or r["split"] == "spare"], **(extra or {})}
        p = f.l4 / fname
        p.write_text(json.dumps(doc, indent=1))
        return p

    rej = acc["rejections"]
    if mode == "one":
        paths = [one(("dev", "test"), rej, name)]
    elif mode == "split":
        paths = [one(("dev",), [r for r in rej if r["split"] == "dev"], "ACCEPTED-dev.json"),
                 one(("test",), [r for r in rej if r["split"] != "dev"], "ACCEPTED-test.json")]
    else:
        v1 = [r for r in rej if r.get("study") == "v1"]
        v2 = [r for r in rej if r.get("study") == "v2"]
        ctx = f.l4 / "ACCEPTED-v1-context.json"
        ctx.write_text(json.dumps({"schema": L4, "accepted": {"dev": {}, "test": {}}, "splits_screened": ["dev", "test"],
                                   "rejected": [rejected_entry(r) for r in v1]}))
        doc = {**base, "study": "v2", "accepted": lists, "splits_screened": ["dev", "test"],
               "join": {"context": [{"file": ctx.name, "sha256": fx.sha(ctx), "splits": ["dev", "test"]}]},
               "topup_screening": {"rejected": [rejected_entry(r) for r in v2], "unfetched_reserves": []}}
        p = f.l4 / name
        p.write_text(json.dumps(doc, indent=1))
        paths = [p]
    write_ledger(f, paths)
    return paths


def accepted_args(paths):
    return [x for p in paths for x in ("--accepted", str(p))]


def plan_for(f, paths):
    plan = gate_run.Plan(gate_run.build_parser().parse_args(f.argv(*accepted_args(paths))), rungs=fx.fake_classifier())
    return plan


def native_units(f):
    acc = json.loads((f.l4 / "ACCEPTED.json").read_text())
    return {s: sorted((u["unit_id"], u["label"], u["topic"], u["sha256"], u["role"]) for u in acc["units"][s])
            for s in ("dev", "test")}


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ the gate reads L4's schema

def test_l4_schema_in_one_file_gives_the_same_units_and_passes_every_check(tmp_path, capsys):
    f = fx.build(tmp_path)
    paths = to_l4(f)
    plan = plan_for(f, paths)
    plan.run_checks()
    got = {s: sorted((u["unit_id"], u["label"], u["topic"], u["sha256"], u["role"]) for u in plan.ctx["accepted"]["units"][s])
           for s in ("dev", "test")}
    assert got == native_units(f)                                           # `replacement` reads as `reserve`
    assert plan.ctx["reserve_check"] == {"used": 2, "skipped": 0, "unfilled": 1, "rejected_unneeded": 0, "awaiting_fetch": 0}
    code = gate_run.main(f.argv("--dry-run", *accepted_args(paths)), rungs=fx.fake_classifier())
    assert code == 0 and "REFUSED" not in capsys.readouterr().out


def test_l4_schema_in_one_file_per_split_and_the_run_record_pins_each_file(tmp_path, capsys):
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    plan = plan_for(f, paths)
    plan.run_checks()
    assert [x["name"] for x in plan.ctx["accepted_files"]] == ["ACCEPTED-dev.json", "ACCEPTED-test.json"]
    assert [x["sha256"] for x in plan.ctx["accepted_files"]] == [fx.sha(p) for p in paths]
    assert [x["splits"] for x in plan.ctx["accepted_files"]] == [["dev"], ["test"]]
    assert all(fx.sha(p) in plan.ctx["accepted_sha"] for p in paths)
    code = gate_run.main(f.argv("--dry-run", *accepted_args(paths)), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 0 and "Accepted files read" in out and fx.sha(paths[0]) in out and fx.sha(paths[1]) in out


def test_the_gate_run_json_of_an_invalid_run_pins_the_files(tmp_path, real_floors, capsys):
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    code = gate_run.main(f.argv(*accepted_args(paths)), rungs=fx.fake_classifier())
    capsys.readouterr()
    rec = json.loads((f.root / "runs" / "fx1" / "gate-run.json").read_text())
    assert code == 3 and rec["status"] == "INVALID"
    assert [x["sha256"] for x in rec["accepted_files"]] == [fx.sha(p) for p in paths]
    assert not (f.root / "runs" / "fx1" / "predictions.csv").exists()


def test_a_native_file_still_reads_as_before_and_is_one_file(tmp_path, capsys):
    f = fx.build(tmp_path)
    plan = plan_for(f, [f.l4 / "ACCEPTED.json"])
    plan.run_checks()
    assert plan.ctx["accepted"]["files"][0]["schema"] == "second-look-accepted/1"
    assert plan.ctx["accepted_sha"] == fx.sha(f.l4 / "ACCEPTED.json")
    copy = f.l4 / "ACCEPTED-copy.json"
    copy.write_text((f.l4 / "ACCEPTED.json").read_text())
    with pytest.raises(gate_run.Refused, match="holds dev and test itself: give one file, not 2"):
        l4_accepted.load([f.l4 / "ACCEPTED.json", copy], f.l4)


def test_refuses_mixed_schemas_and_one_split_in_two_files(tmp_path):
    f = fx.build(tmp_path)
    one, = to_l4(f)
    with pytest.raises(gate_run.Refused, match="different schemas"):
        l4_accepted.load([f.l4 / "ACCEPTED.json", one], f.l4)
    dup = f.l4 / "ACCEPTED-dup.json"
    dup.write_text(one.read_text())
    with pytest.raises(gate_run.Refused, match="two accepted files have the same name|both list accepted units for"):
        l4_accepted.load([one, dup], f.l4)


def test_refuses_an_l4_file_made_under_another_lock(tmp_path, capsys):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    doc = json.loads(p.read_text())
    doc["lock_sha256"] = "a" * 64
    p.write_text(json.dumps(doc))
    write_ledger(f, [p])
    code = gate_run.main(f.argv("--dry-run", *accepted_args([p])), rungs=fx.fake_classifier())
    assert code == 2 and "ACCEPTED-L4.json lock_sha256 is 'aaaa" in capsys.readouterr().out


def test_refuses_when_the_second_file_is_not_in_the_ledger(tmp_path, capsys):
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    write_ledger(f, paths[:1])
    code = gate_run.main(f.argv("--dry-run", *accepted_args(paths)), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and f"the sha256 of ACCEPTED-test.json ({fx.sha(paths[1])}) is not recorded in LEDGER.md" in out


def test_refuses_two_files_made_under_different_protocols(tmp_path):
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    doc = json.loads(paths[1].read_text())
    doc["protocol_sha256"] = "b" * 64
    paths[1].write_text(json.dumps(doc))
    with pytest.raises(gate_run.Refused, match="disagree on protocol_sha256"):
        l4_accepted.load(paths, f.l4)


def test_refuses_a_missing_test_split(tmp_path, capsys):
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    write_ledger(f, paths[:1])
    code = gate_run.main(f.argv("--dry-run", *accepted_args(paths[:1])), rungs=fx.fake_classifier())
    assert code == 2 and "units.test is missing" in capsys.readouterr().out


def test_the_screen_config_of_l4_names_its_phash_distance_under_perceptual_hash(tmp_path, capsys):
    """L4's real SCREEN-CONFIG.json has no top-level phash_distance: it is perceptual_hash.same_unit_if_distance_at_most."""
    f = fx.build(tmp_path, lock_screen=True)
    cfg = {"schema": "l4", "perceptual_hash": {"same_unit_if_distance_at_most": 8}}
    (f.l4 / "SCREEN-CONFIG.json").write_text(json.dumps(cfg))
    lock = json.loads((f.l4 / "LOCK.json").read_text())
    lock["sha256"]["SCREEN-CONFIG.json"] = fx.sha(f.l4 / "SCREEN-CONFIG.json")
    (f.l4 / "LOCK.json").write_text(json.dumps(lock))
    acc = json.loads((f.l4 / "ACCEPTED.json").read_text())
    for k, p in (("screen_config_sha256", "SCREEN-CONFIG.json"), ("lock_sha256", "LOCK.json")):
        acc[k] = fx.sha(f.l4 / p)
    (f.l4 / "ACCEPTED.json").write_text(json.dumps(acc))
    (f.l4 / "LEDGER.md").write_text(f"ACCEPTED.json sha256 {fx.sha(f.l4 / 'ACCEPTED.json')}\n")
    # the threshold was frozen under the old lock, so the first refusal is check 8: checks 1 to 7 passed on the new layout
    code = gate_run.main(f.argv("--dry-run"), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert "[ok] SCREEN-CONFIG.json" in out and "[ok] ACCEPTED.json" in out, out
    assert code == 2 and "[REFUSED] frozen threshold.json: threshold.json l4_lock_sha256 is not this LOCK.json's sha256" in out, out


# ------------------------------------------------------------------ reserves as L4 writes them

def spare_landscape(rank=2, rejected=True):
    """A second landscape reserve (rank 2) behind test-real-4 (rank 1), rejected by L4 though nobody was owed it."""
    def tweak(d):
        d["rejections"].append({"unit_id": "spare-0", "split": "spare", "label": "real", "topic": "landscape", "role": "reserve",
                                "check": "C1", "reason": "format MPO is not the expected JPEG"})
    return tweak


def test_a_rejected_reserve_beyond_the_replacements_owed_is_counted_not_refused(tmp_path, capsys):
    """L4's screen.py screens every fetched reserve; rank 1 fills the one landscape place owed, rank 2 is rejected unused."""
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 2)], tweak=spare_landscape())
    for schema in ("native", "l4"):
        plan = plan_for(f, [f.l4 / "ACCEPTED.json"] if schema == "native" else to_l4(f))
        plan.run_checks()
        assert plan.ctx["reserve_check"]["rejected_unneeded"] == 1
        assert plan.ctx["reserve_check"]["used"] == 2


def test_a_passed_over_reserve_is_still_refused(tmp_path, capsys):
    """Rank 1 (test-real-4) is dropped from the accepted units though rej-0 is owed it, and the rank-2 spare is accepted
    in its place: the walk was not followed."""
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 2)], tweak=unfetch)
    p, = to_l4(f)
    with pytest.raises(gate_run.Refused, match="replacement out of order: real reserve test-real-4"):
        plan_for(f, [p]).run_checks()


def test_the_report_names_the_rejected_unused_reserves(tmp_path):
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 2)], tweak=spare_landscape())
    plan = plan_for(f, to_l4(f))
    plan.run_checks()
    text = "\n".join(gate_run.gate_report.attrition(plan, Counter()))
    assert "1 reserve unit(s) beyond the replacements owed were screened and rejected, and were not used." in text


def unfetch(d):
    d["units"]["test"] = [u for u in d["units"]["test"] if u["unit_id"] != "test-real-4"]     # the landscape reserve, rank 1


def test_a_reserve_l4_had_not_fetched_stops_the_walk_and_the_set_stays_provisional(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=unfetch)
    p, = to_l4(f, complete=False, unfetched=["real:test-real-4"])
    plan = plan_for(f, [p])
    plan.run_checks(stop_on_fail=False)
    assert plan.ctx["reserve_check"]["awaiting_fetch"] == 1 and plan.ctx["reserve_check"]["unfilled"] == 1
    code = gate_run.main(f.argv("--dry-run", *accepted_args([p])), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "the accepted set is PROVISIONAL (`complete` is not true in ACCEPTED-L4.json; " \
                         "1 replacement(s) are still owed from reserves L4 had not fetched)" in out


def test_without_the_unfetched_list_the_same_file_is_an_out_of_order_refusal(tmp_path):
    f = fx.build(tmp_path, tweak=unfetch)
    p, = to_l4(f, complete=False)
    with pytest.raises(gate_run.Refused, match="reserve test-real-4 .* is next in the frozen order and was not used"):
        plan_for(f, [p]).run_checks()


def test_a_reserve_used_behind_an_unfetched_one_is_refused(tmp_path):
    def tweak(d):
        unfetch(d)
        d["rejections"].append({"unit_id": "spare-0", "split": "spare", "label": "real", "topic": "landscape", "role": "reserve",
                                "check": "C1", "reason": "x"})
    f = fx.build(tmp_path, spare_rows=[("test", "landscape", 2)], tweak=tweak)
    p, = to_l4(f, complete=False, unfetched=["real:test-real-4"])
    with pytest.raises(gate_run.Refused, match="spare-0 .* was rejected, but the lower-ranked reserve test-real-4 had not been fetched"):
        plan_for(f, [p]).run_checks()


def test_a_complete_flag_that_is_false_refuses_once_the_floors_are_met(tmp_path, capsys):
    f = fx.build(tmp_path)
    p, = to_l4(f, complete=False)
    code = gate_run.main(f.argv("--dry-run", *accepted_args([p])), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 2 and "[REFUSED] floors" in out and "PROVISIONAL (`complete` is not true in ACCEPTED-L4.json)" in out


def test_a_missed_floor_on_a_provisional_set_is_published_as_invalid_with_the_note(tmp_path, real_floors, capsys):
    f = fx.build(tmp_path)
    p, = to_l4(f, complete=False)
    code = gate_run.main(f.argv("--dry-run", *accepted_args([p])), rungs=fx.fake_classifier())
    out = capsys.readouterr().out
    assert code == 3 and "[INVALID] floors" in out and "note: the accepted set is PROVISIONAL" in out


# ------------------------------------------------------------------ the v2 join file

def test_a_v2_join_file_reads_v1_rejections_from_its_pinned_context(tmp_path, capsys):
    f = fx.build(tmp_path, tweak=to_v2, protocol_text="v2 protocol\n")
    p, = to_l4(f, "join", name="ACCEPTED-v2.json")
    plan = plan_for(f, [p])
    plan.run_checks()
    assert plan.ctx["reserve_check_by_study"] == {"v1": {"used": 1, "skipped": 0, "unfilled": 1},
                                                  "v2": {"used": 1, "skipped": 0, "unfilled": 0}}
    assert {r["study"] for r in plan.ctx["accepted"]["rejections"]} == {"v1", "v2"}
    code = gate_run.main(f.argv("--dry-run", *accepted_args([p])), rungs=fx.fake_classifier())
    assert code == 0 and "REFUSED" not in capsys.readouterr().out


def test_a_v2_join_file_is_refused_when_its_context_file_changed(tmp_path):
    f = fx.build(tmp_path, tweak=to_v2, protocol_text="v2 protocol\n")
    p, = to_l4(f, "join", name="ACCEPTED-v2.json")
    (f.l4 / "ACCEPTED-v1-context.json").write_text("{}")
    with pytest.raises(gate_run.Refused, match="the v1 file it was joined from, ACCEPTED-v1-context.json, is missing or is not"):
        l4_accepted.load([p], f.l4)


def test_a_v2_join_file_without_the_v1_rejections_fails_the_reserve_order(tmp_path):
    """Reading only the join file would drop v1's rejected primaries, so v1's used reserve would look unowed."""
    f = fx.build(tmp_path, tweak=to_v2, protocol_text="v2 protocol\n")
    p, = to_l4(f, "join", name="ACCEPTED-v2.json")
    doc = json.loads(p.read_text())
    ctx = f.l4 / "ACCEPTED-v1-context.json"
    c = json.loads(ctx.read_text())
    c["rejected"] = []
    ctx.write_text(json.dumps(c))
    doc["join"]["context"][0]["sha256"] = fx.sha(ctx)
    p.write_text(json.dumps(doc))
    write_ledger(f, [p])
    with pytest.raises(gate_run.Refused, match="replacement out of order"):
        plan_for(f, [p]).run_checks()


# ------------------------------------------------------------------ the splits writer

def write_splits(f, paths, out=None, **kw):
    out = out or f.root / "out-splits"
    return gate_splits.build(f.l4, paths, out, **kw), out


def test_the_splits_are_the_accepted_units_as_given_with_no_resplit(tmp_path):
    """Two dev real units carry the same pHash. gate.split would cluster them and keep one; this step keeps both."""
    f = fx.build(tmp_path)
    p, = to_l4(f)
    doc = json.loads(p.read_text())
    reals = doc["accepted"]["dev"]["real"]
    reals[1]["phash"] = reals[0]["phash"]
    p.write_text(json.dumps(doc))
    report, out = write_splits(f, [p])
    want = native_units(f)
    for s in ("dev", "test"):
        rows = read_csv(out / f"{s}.csv")
        assert sorted(r["item_id"] for r in rows) == sorted(x[0] for x in want[s])
        assert all(r["group"] == r["item_id"] for r in rows)
        assert {r["item_id"]: r["label"] for r in rows} == {x[0]: x[1] for x in want[s]}
        assert {r["item_id"]: r["sha256"] for r in rows} == {x[0]: x[3] for x in want[s]}
        assert [r["item_id"] for r in rows] == sorted(r["item_id"] for r in rows)
    dev = read_csv(out / "dev.csv")
    assert {reals[0]["unit_id"], reals[1]["unit_id"]} <= {r["item_id"] for r in dev}
    assert report["resplit"] is False and "seed" not in report


def test_split_json_hashes_match_the_files_and_the_counts_match_the_rows(tmp_path):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    report, out = write_splits(f, [p])
    disk = json.loads((out / "SPLIT.json").read_text())
    assert disk == report
    for s in ("dev", "test"):
        assert disk[s]["sha256"] == fx.sha(out / f"{s}.csv")
        n = Counter(r["label"] for r in read_csv(out / f"{s}.csv"))
        assert (disk[s]["real"], disk[s]["ai"]) == (n["real"], n["ai"])
    assert disk["params"]["phash_distance"] == 8 and disk["schema"] == "second-look-split/1"
    assert disk["accepted_files"][0]["sha256"] == fx.sha(p) and disk["l4"]["lock_sha256"] == fx.sha(f.l4 / "LOCK.json")
    assert disk["dev"]["sha256"] != disk["test"]["sha256"]


def test_the_same_inputs_give_the_same_bytes(tmp_path):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    _, a = write_splits(f, [p], f.root / "a")
    _, b = write_splits(f, [p], f.root / "b")
    for name in ("dev.csv", "test.csv", "SPLIT.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_the_files_pass_the_calibrate_lock_check_and_the_harness_input_checks(tmp_path):
    from secondlook import calibrate
    f = fx.build(tmp_path)
    paths = to_l4(f, "split")
    report, out = write_splits(f, paths)
    got = calibrate.verify_l4(str(f.l4), str(out / "SPLIT.json"))
    assert got["dev_sha"] == fx.sha(out / "dev.csv") and got["test_sha"] == fx.sha(out / "test.csv")
    dev, test = harness.load_split(str(out / "dev.csv"), "dev"), harness.load_split(str(out / "test.csv"), "test")
    harness.check_hash_coverage(harness.hash_coverage(dev, test))
    harness.check_source_groups(dev, test)
    harness.check_independence(dev, test, 8)
    assert report["phash_checked"] == len(dev) + len(test)


def test_the_gate_dry_run_reads_the_written_splits(tmp_path, capsys):
    """The gate's checks 1 to 7 and 9 pass on the files this step writes (check 8: the threshold was frozen on others)."""
    f = fx.build(tmp_path)
    paths = to_l4(f)
    _, out = write_splits(f, paths)
    argv = f.argv("--dry-run", "--splits-dir", str(out), *accepted_args(paths))
    code = gate_run.main(argv, rungs=fx.fake_classifier())
    text = capsys.readouterr().out
    assert code == 2
    for step in ("L4 LOCK.json and the split files", "SCREEN-CONFIG.json", "ACCEPTED.json", "accepted units equal",
                 "replacements follow", "floors"):
        assert f"[ok] {step}" in text, text
    assert "[REFUSED] frozen threshold.json: threshold.json was frozen against other dev or test split files" in text


def test_paths_are_relative_to_the_original_root_or_absolute(tmp_path):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    _, out = write_splits(f, [p])
    assert read_csv(out / "dev.csv")[0]["path"].split("/")[0] in ("real", "ai")
    _, out2 = write_splits(f, [p], f.root / "o2", original_root=f.root / "elsewhere")
    assert read_csv(out2 / "dev.csv")[0]["path"].startswith(str(f.l4.resolve()))


def test_phash_is_left_out_when_any_unit_lacks_one(tmp_path):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    doc = json.loads(p.read_text())
    doc["accepted"]["test"]["real"][0].pop("phash")
    p.write_text(json.dumps(doc))
    report, out = write_splits(f, [p])
    assert report["phash_checked"] == 0
    assert all(r["phash"] == "" for s in ("dev", "test") for r in read_csv(out / f"{s}.csv"))


@pytest.mark.parametrize("what", ["lock", "protocol", "native", "missing-split", "both-splits", "missing-original", "nonempty-out",
                                  "inside-l4", "bad-hash"])
def test_the_writer_refuses(tmp_path, what, capsys):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    out = f.root / "out-x"
    kw, paths = {}, [p]
    doc = json.loads(p.read_text())
    if what == "lock":
        (f.l4 / "PROTOCOL.md").write_text("edited after the lock\n")
        needle = "no longer matches LOCK.json"
    elif what == "protocol":
        doc["protocol_sha256"] = "c" * 64
        needle = "protocol_sha256 is"
    elif what == "native":
        paths, needle = [f.l4 / "ACCEPTED.json"], "carry no original's path"
    elif what == "missing-split":
        del doc["accepted"]["test"]
        doc["splits_screened"] = ["dev"]
        needle = "no accepted units for test"
    elif what == "both-splits":
        doc["accepted"]["test"]["real"].append(doc["accepted"]["dev"]["real"][0])
        needle = "is accepted in dev and in test"
    elif what == "missing-original":
        (f.l4 / doc["accepted"]["dev"]["real"][0]["file"]).unlink()
        needle = "is not at"
    elif what == "nonempty-out":
        out.mkdir()
        (out / "x").write_text("x")
        needle = "already holds files"
    elif what == "inside-l4":
        out = f.l4 / "splits-new"
        needle = "is inside the L4 folder"
    else:
        kw["hash_originals"] = True
        doc["accepted"]["dev"]["real"][0]["sha256"] = "d" * 64
        needle = "is not the file whose sha256"
    p.write_text(json.dumps(doc))
    code = gate_splits.main(["--l4-dir", str(f.l4), *accepted_args(paths), "--out", str(out)] +
                            (["--hash-originals"] if kw else []))
    err = capsys.readouterr().err
    assert code == 2 and needle in err, err
    assert not (out / "dev.csv").exists() and not (out / "SPLIT.json").exists()


def test_the_writer_may_write_inside_l4_only_when_told(tmp_path):
    f = fx.build(tmp_path)
    p, = to_l4(f)
    gate_splits.build(f.l4, [p], f.l4 / "splits-new", allow_inside_l4=True)
    assert (f.l4 / "splits-new" / "SPLIT.json").is_file()
