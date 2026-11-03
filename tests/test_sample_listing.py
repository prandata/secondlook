"""samples/recorded/ (the recorded Muse Glimmer runs) is not a demo sample.

The judge persona test of 30 Oct (DAT-850) found two places that listed samples/ and counted that
folder: scripts/fetch_samples.sh printed "10 demo samples", and scripts/clean_clone_test.sh failed
its samples check on main, so `make clean-clone-test` stopped before pass 2. Both now list through
scripts/list_samples.sh. Each test here runs with the recorded folder present.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NINE = [
    "01-camera-pixel10pro-atlantic-sign.jpg",
    "02-camera-pixel10pro-utah-sign.jpg",
    "03-ai-firefly-mountain-landscape.jpg",
    "04-ai-firefly-typography-logo.jpg",
    "05-ai-edit-photoshop-firefly-bentley.jpg",
    "06-stripped-pixel10pro-atlantic-sign.jpg",
    "07-stripped-firefly-mountain-landscape.jpg",
    "08-iptc-ai-label-firefly-mountain-landscape.jpg",
    "09-iptc-ai-label-firefly-typography-logo.jpg",
]


def _samples_with_recorded(base: Path) -> Path:
    d = base / "samples"
    (d / "recorded").mkdir(parents=True)
    (d / "recorded" / "02.json").write_text("{}")
    for n in NINE:
        (d / n).write_bytes(b"")
    return d


def test_the_listing_leaves_out_the_recorded_folder(tmp_path):
    d = _samples_with_recorded(tmp_path)
    out = subprocess.run([str(SCRIPTS / "list_samples.sh"), str(d)], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == sorted(NINE)


def test_the_listing_keeps_any_other_extra_file(tmp_path):
    """Only the recorded folder is left out: a stray file still shows, so the clean-clone check still catches it."""
    d = _samples_with_recorded(tmp_path)
    (d / "10-stray.jpg").write_bytes(b"")
    out = subprocess.run([str(SCRIPTS / "list_samples.sh"), str(d)], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == sorted(NINE + ["10-stray.jpg"])


def test_the_listing_fails_on_a_missing_folder(tmp_path):
    r = subprocess.run([str(SCRIPTS / "list_samples.sh"), str(tmp_path / "none")], capture_output=True, text=True)
    assert r.returncode != 0


def _clean_clone_samples_check() -> str:
    """The clean-clone test's own lines: the EXPECTED block, then want, have and the comparison."""
    s = (SCRIPTS / "clean_clone_test.sh").read_text()
    expected = re.search(r'^EXPECTED="[^"]*"$', s, re.M).group(0)
    want = re.search(r"^want=.*$", s, re.M).group(0)
    have = re.search(r"^have=.*$", s, re.M).group(0)
    assert "list_samples.sh" in have, have
    return "\n".join(["set -euo pipefail", expected, want, have,
                      '[ "$have" = "$want" ] && echo SAME || { echo DIFFERENT; echo "$have"; }'])


def test_the_clean_clone_samples_check_passes_with_the_recorded_folder(tmp_path):
    _samples_with_recorded(tmp_path)
    (tmp_path / "scripts").mkdir()
    shutil.copy2(SCRIPTS / "list_samples.sh", tmp_path / "scripts" / "list_samples.sh")
    r = subprocess.run(["bash", "-c", _clean_clone_samples_check()], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "SAME", r.stdout


def test_the_clean_clone_samples_check_still_fails_on_a_stray_file(tmp_path):
    d = _samples_with_recorded(tmp_path)
    (d / "notes.txt").write_text("x")
    (tmp_path / "scripts").mkdir()
    shutil.copy2(SCRIPTS / "list_samples.sh", tmp_path / "scripts" / "list_samples.sh")
    r = subprocess.run(["bash", "-c", _clean_clone_samples_check()], cwd=tmp_path, capture_output=True, text=True)
    assert r.stdout.splitlines()[0] == "DIFFERENT", r.stdout


def test_fetch_samples_offline_counts_nine_with_the_recorded_folder(tmp_path):
    """The real script, offline, on a copy of this checkout's fetched samples and fixtures plus samples/recorded/."""
    src_samples, src_fix = ROOT / "samples", ROOT / "data" / "test-fixtures"
    if not all((src_samples / n).is_file() for n in NINE) or not src_fix.is_dir():
        pytest.skip("samples or fixtures missing: run scripts/fetch_samples.sh")
    (tmp_path / "scripts").mkdir()
    for f in ("fetch_samples.sh", "list_samples.sh"):
        shutil.copy2(SCRIPTS / f, tmp_path / "scripts" / f)
    (tmp_path / "samples").mkdir()
    for n in NINE:
        shutil.copy2(src_samples / n, tmp_path / "samples" / n)
    shutil.copytree(ROOT / "samples" / "recorded", tmp_path / "samples" / "recorded")
    shutil.copytree(src_fix, tmp_path / "data" / "test-fixtures")
    r = subprocess.run([str(tmp_path / "scripts" / "fetch_samples.sh"), "--offline"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "samples" / "recorded").is_dir()
    last = r.stdout.strip().splitlines()[-1]
    assert re.fullmatch(r"ok   9 demo samples, \d+ test fixtures", last), last


def test_the_listing_keeps_a_file_named_recorded(tmp_path):
    """Only a folder named recorded is left out (A228): a file of that name is a stray, so it is listed and the
    clean-clone check catches it."""
    d = tmp_path / "samples"
    d.mkdir()
    for n in NINE:
        (d / n).write_bytes(b"")
    (d / "recorded").write_text("not a folder")
    out = subprocess.run([str(SCRIPTS / "list_samples.sh"), str(d)], capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == sorted(NINE + ["recorded"])
