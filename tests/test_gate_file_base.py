"""`gate_splits --file-base` (DAT-852): where an accepted file's originals are read from.

A gate folder that holds no images (the v2 layout) reads each unit's `file` from the L4 folder that does. The base must
contain every original: a `file` that resolves outside it (`..`, an absolute path, a symlink) is refused before anything is
written. The base is recorded in SPLIT.json, and a gate run carries it into gate-run.json and the RESULTS run table.
Fixtures only; the acceptance on L4's real files is in docs/GATE-RUN.md.
"""
from __future__ import annotations

import json
import shutil

import pytest

from secondlook import calibrate, gate_run, gate_splits
from tests import gate_fixture as fx
from tests.test_gate_l4_schema import accepted_args, read_csv, to_l4
from tests.test_gate_run import low_floors  # noqa: F401  (autouse: the fixtures are small)


class Layout:
    """A gate folder without images, and a separate base that holds them (the real v2 layout)."""

    def __init__(self, tmp_path, **build):
        self.f = fx.build(tmp_path, **build)
        self.accepted, = to_l4(self.f)
        self.base = tmp_path / "images-home"
        self.base.mkdir()
        shutil.move(str(self.f.l4 / "images"), str(self.base / "images"))        # the gate folder now holds none
        self.outside = tmp_path / "outside"
        self.outside.mkdir()
        self.tmp = tmp_path

    def point_first_unit_at(self, file):
        """Rewrites the first accepted unit's `file` (the accepted file is not locked here: only the split step reads it)."""
        doc = json.loads(self.accepted.read_text())
        first = doc["accepted"]["dev"]["real"][0]
        first["file"] = file
        self.accepted.write_text(json.dumps(doc, indent=1))
        return first

    def build(self, out="out-splits", **kw):
        return gate_splits.build(self.f.l4, [self.accepted], self.tmp / out, file_base=self.base, **kw)


@pytest.fixture
def lay(tmp_path):
    return Layout(tmp_path)


def original_of(lay, unit):
    return lay.base / "images" / "original" / "real" / f"{unit['unit_id']}.jpg"


def test_a_normal_real_layout_reads_the_originals_from_the_base_and_records_it(lay):
    report = lay.build(hash_originals=True, original_root=lay.base / "images" / "original")
    out = lay.tmp / "out-splits"
    assert report["file_base"] == str(lay.base.resolve())
    assert json.loads((out / "SPLIT.json").read_text())["file_base"] == str(lay.base.resolve())
    rows = read_csv(out / "dev.csv")
    assert rows and all(r["path"].startswith(("real/", "ai/")) for r in rows)      # relative to --original-root
    assert report["dev"]["real"] and report["test"]["ai"]


def test_without_file_base_the_base_is_the_l4_folder_and_is_recorded_as_that(tmp_path):
    f = fx.build(tmp_path)
    accepted, = to_l4(f)
    report = gate_splits.build(f.l4, [accepted], tmp_path / "out")
    assert report["file_base"] == str(f.l4.resolve())


def test_without_the_base_the_images_are_not_found(lay):
    with pytest.raises(calibrate.Refused, match="is not at"):
        gate_splits.build(lay.f.l4, [lay.accepted], lay.tmp / "out-x")


def test_a_dotdot_escape_is_refused_and_nothing_is_written(lay):
    unit = lay.point_first_unit_at("../outside/secret.jpg")
    shutil.copy(original_of(lay, unit), lay.outside / "secret.jpg")                  # a real, readable file outside the base
    with pytest.raises(calibrate.Refused, match=r"outside --file-base"):
        lay.build()
    assert not (lay.tmp / "out-splits").exists()


def test_a_dotdot_that_comes_back_inside_the_base_is_accepted(lay):
    unit = lay.point_first_unit_at("x")
    lay.point_first_unit_at(f"images/original/ai/../real/{unit['unit_id']}.jpg")
    assert lay.build(hash_originals=True)["dev"]["real"]


def test_an_absolute_path_outside_the_base_is_refused_and_nothing_is_written(lay):
    unit = lay.point_first_unit_at("x")
    target = lay.outside / "abs.jpg"
    shutil.copy(original_of(lay, unit), target)
    lay.point_first_unit_at(str(target))
    with pytest.raises(calibrate.Refused, match=r"outside --file-base"):
        lay.build()
    assert not (lay.tmp / "out-splits").exists()


def test_an_absolute_path_inside_the_base_is_accepted(lay):
    unit = lay.point_first_unit_at("x")
    lay.point_first_unit_at(str(original_of(lay, unit)))
    assert lay.build(hash_originals=True)["file_base"] == str(lay.base.resolve())


def test_a_symlink_that_leads_outside_the_base_is_refused_and_nothing_is_written(lay):
    unit = lay.point_first_unit_at("x")
    target = lay.outside / "real.jpg"
    shutil.copy(original_of(lay, unit), target)
    link = lay.base / "images" / "original" / "real" / "link.jpg"
    link.symlink_to(target)                                  # inside the base by name, outside it by where it leads
    lay.point_first_unit_at("images/original/real/link.jpg")
    with pytest.raises(calibrate.Refused, match=r"outside --file-base"):
        lay.build()
    assert not (lay.tmp / "out-splits").exists()


def test_a_symlinked_directory_that_leads_outside_the_base_is_refused(lay):
    unit = lay.point_first_unit_at("x")
    shutil.copy(original_of(lay, unit), lay.outside / "r.jpg")
    (lay.base / "linkdir").symlink_to(lay.outside, target_is_directory=True)
    lay.point_first_unit_at("linkdir/r.jpg")
    with pytest.raises(calibrate.Refused, match=r"outside --file-base"):
        lay.build()


def test_the_command_line_refuses_an_escape_with_exit_2(lay, capsys):
    lay.point_first_unit_at("../outside/none.jpg")
    code = gate_splits.main(["--l4-dir", str(lay.f.l4), *accepted_args([lay.accepted]), "--out", str(lay.tmp / "o"),
                             "--file-base", str(lay.base)])
    assert code == 2 and "outside --file-base" in capsys.readouterr().err
    assert not (lay.tmp / "o").exists()


# ------------------------------------------------------------------ gate_run carries the base

def freeze_to(lay, out_splits):
    """The fixture's threshold.json names its own dev and test files; point it at the ones gate_splits wrote."""
    split = json.loads((out_splits / "SPLIT.json").read_text())
    thr = json.loads(lay.f.threshold.read_text())
    thr["dev_split_sha256"], thr["test_split_sha256"] = split["dev"]["sha256"], split["test"]["sha256"]
    lay.f.threshold.write_text(json.dumps(thr, indent=1))
    fx.git(lay.f.repo, "add", "threshold.json")
    fx.git(lay.f.repo, "commit", "-q", "-m", "freeze threshold against the written splits")


def run_args(lay, out_splits):
    root = lay.base / "images" / "original"
    return lay.f.argv("--splits-dir", str(out_splits), "--original-root", str(root), *accepted_args([lay.accepted]))


def test_gate_run_carries_the_base_into_its_record_helper(lay):
    lay.build(original_root=lay.base / "images" / "original")
    freeze_to(lay, lay.tmp / "out-splits")
    p = gate_run.Plan(gate_run.build_parser().parse_args(run_args(lay, lay.tmp / "out-splits")), rungs=fx.fake_classifier())
    p.run_checks()
    assert gate_run.file_base_provenance(p.ctx) == {"file_base": str(lay.base.resolve())}
    assert gate_run.file_base_provenance({"l4": {"split": {}}}) == {"file_base": None}     # a SPLIT.json written before


def test_an_invalid_run_records_the_base_in_gate_run_json_and_the_results_table(lay, capsys, monkeypatch):
    for name in ("MIN_DEV_REAL", "MIN_DEV_AI", "MIN_REAL", "MIN_AI"):
        monkeypatch.setattr(gate_run, name, 1000)           # not met: the run publishes INVALID and its record
    lay.build(original_root=lay.base / "images" / "original")
    freeze_to(lay, lay.tmp / "out-splits")
    code = gate_run.main(run_args(lay, lay.tmp / "out-splits") + ["--results-md", str(lay.tmp / "RESULTS.md")],
                         rungs=fx.fake_classifier())
    assert code == 3, capsys.readouterr().err
    run = lay.f.root / "runs" / "fx1"
    assert json.loads((run / "gate-run.json").read_text())["file_base"] == str(lay.base.resolve())
    md = (run / "RESULTS.md").read_text()
    assert f"| Originals read from (gate_splits --file-base) | {lay.base.resolve()} |" in md
    assert md == (lay.tmp / "RESULTS.md").read_text()


def test_a_scored_run_records_the_base_in_gate_run_json_and_the_results_table(tmp_path, capsys):
    # the harness refuses a dev split under 100 real and 100 AI, so this one fixture is sized to it
    lay = Layout(tmp_path, dev_real=100, dev_ai=100, test_real=30, test_ai=20, rejected=0, reserve=0)
    lay.build(original_root=lay.base / "images" / "original")
    freeze_to(lay, lay.tmp / "out-splits")
    code = gate_run.main(run_args(lay, lay.tmp / "out-splits") + ["--results-md", str(lay.tmp / "RESULTS.md")],
                         rungs=fx.fake_classifier())
    assert code in (0, 1, 3), capsys.readouterr().err         # the verdict on a fixture's counts does not matter here
    run = lay.f.root / "runs" / "fx1"
    rec = json.loads((run / "gate-run.json").read_text())
    assert "predictions_sha256" in rec, "this is the scored run's record, not the INVALID one"
    assert rec["file_base"] == str(lay.base.resolve())
    assert f"| Originals read from (gate_splits --file-base) | {lay.base.resolve()} |" in (run / "RESULTS.md").read_text()
