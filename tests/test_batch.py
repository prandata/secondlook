"""Tests for batch (B1, DAT-871): folder walk, limits, symlinks, near-duplicate groups, determinism, no network.

Every image here is generated at test time with Pillow; no sample download is needed.
"""
import csv
import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from secondlook import batch, cli, ladder  # noqa: E402
from secondlook.explain import explain  # noqa: E402
from secondlook.rungs import classifier  # noqa: E402

_real = batch.run_batch
FAST = ("c2pa", "iptc", "exif")  # the metadata rungs: no model to load, so the tests stay quick


def picture(seed: int, size=(96, 64)) -> Image.Image:
    """A picture with structure (coarse random blocks), so its difference hash means something.
    Different seeds give pictures about 32 bits apart; a re-saved or shrunk copy is within a few."""
    rnd = random.Random(seed)
    small = Image.new("L", (9, 8))
    small.putdata([rnd.randrange(256) for _ in range(72)])
    return small.resize(size, Image.Resampling.NEAREST).convert("RGB")


XMP_AI = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
    b'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia">'
    b"<Iptc4xmpExt:AISystemUsed>ExampleGen</Iptc4xmpExt:AISystemUsed>"
    b"</rdf:Description></rdf:RDF></x:xmpmeta>"
)


@pytest.fixture
def folder(tmp_path):
    """photos/: a.png, b.jpg, sub/c.webp, sub/deeper/d.JPEG, plus things that are not checked."""
    d = tmp_path / "photos"
    (d / "sub" / "deeper").mkdir(parents=True)
    picture(1).save(d / "a.png")
    picture(2).save(d / "b.jpg", quality=92)
    picture(3).save(d / "sub" / "c.webp")
    picture(4).save(d / "sub" / "deeper" / "d.JPEG")
    (d / "notes.txt").write_text("not an image")
    (d / ".hidden.jpg").write_bytes(b"x")
    return d


def run(folder, out, **kw):
    kw.setdefault("jobs", 1)
    kw.setdefault("rung_names", FAST)
    return batch.run_batch(folder, out, **kw)


# --- the walk ----------------------------------------------------------------------------------

def test_walks_nested_folders_and_only_image_suffixes(folder, tmp_path):
    s = run(folder, tmp_path / "out")
    assert [f["file"] for f in s["files"]] == ["a.png", "b.jpg", "sub/c.webp", "sub/deeper/d.JPEG"]
    assert s["ignored"] == {"hidden": 1, "not_an_image": 1}
    assert s["counts"]["images"] == 4 and s["counts"]["checked"] == 4
    assert (tmp_path / "out" / "summary.json").is_file()


def test_every_answer_is_the_ladders_own_answer(folder, tmp_path):
    s = run(folder, tmp_path / "out")
    rungs = batch.rung_registry.load(FAST)
    for f in s["files"]:
        direct = ladder.run(str(folder / f["file"]), rungs=rungs)
        # The answer is the ladder's; the summary is the plain-language one the page shows (DAT-850).
        assert (f["answer"], f["summary"]) == (direct.answer, explain(direct).summary)


def test_out_dir_inside_the_folder_is_not_scanned(folder, tmp_path):
    out = folder / "results"
    run(folder, out)
    s = run(folder, out)  # a second run must not pick up its own output
    assert s["counts"]["images"] == 4


def test_empty_and_missing_folders(tmp_path):
    (tmp_path / "empty").mkdir()
    s = run(tmp_path / "empty", tmp_path / "out")
    assert s["files"] == [] and s["counts"]["checked"] == 0
    assert "None found" in (tmp_path / "out" / "summary.html").read_text()
    with pytest.raises(NotADirectoryError):
        run(tmp_path / "nope", tmp_path / "out2")


# --- symlinks, special files, limits -------------------------------------------------------------

def test_symlink_that_leaves_the_folder_is_refused_and_never_read(folder, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    picture(9).save(outside / "secret.png")
    os.symlink(outside / "secret.png", folder / "link.png")
    os.symlink(outside, folder / "linked-dir")
    os.symlink("../../outside/secret.png", folder / "sub" / "relative.png")  # a relative link out, too
    seen = []
    orig = ladder.run
    monkeypatch.setattr(ladder, "run", lambda path, rungs=None: (seen.append(path), orig(path, rungs=rungs))[1])
    s = run(folder, tmp_path / "out")
    reasons = {x["file"]: x["reason"] for x in s["skipped"]}
    assert reasons["link.png"] == "symlink leaves the folder"
    assert reasons["sub/relative.png"] == "symlink leaves the folder"
    assert reasons["linked-dir"] == "symlinked folder, not followed"
    assert not any("secret.png" in p or "outside" in p for p in seen)
    assert [f["file"] for f in s["files"]] == ["a.png", "b.jpg", "sub/c.webp", "sub/deeper/d.JPEG"]


def test_symlink_inside_the_folder_is_read_once_through_its_real_path(folder, tmp_path):
    os.symlink(folder / "a.png", folder / "alias.png")
    s = run(folder, tmp_path / "out")
    names = [f["file"] for f in s["files"]]
    assert "a.png" in names and "alias.png" not in names
    assert {"file": "alias.png", "reason": "symlink to a file already in the batch"} in s["skipped"]


def test_a_symlink_loop_does_not_hang(folder, tmp_path):
    os.symlink(folder, folder / "sub" / "loop")
    s = run(folder, tmp_path / "out")
    assert s["counts"]["checked"] == 4


def test_a_fifo_named_like_an_image_is_skipped_without_opening_it(folder, tmp_path):
    os.mkfifo(folder / "pipe.jpg")  # opening it for reading would block forever
    s = run(folder, tmp_path / "out")
    assert {"file": "pipe.jpg", "reason": "not a regular file"} in s["skipped"]


def test_empty_and_unreadable_files(folder, tmp_path):
    (folder / "zero.jpg").write_bytes(b"")
    os.symlink(folder / "gone.png", folder / "dangling.png")
    s = run(folder, tmp_path / "out")
    reasons = {x["file"]: x["reason"] for x in s["skipped"]}
    assert reasons["zero.jpg"] == "empty file"
    assert reasons["dangling.png"] == "can't be read"


def test_file_over_the_size_limit_is_skipped(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "MAX_BYTES", (folder / "b.jpg").stat().st_size - 1)
    s = run(folder, tmp_path / "out")
    assert any(x["file"] == "b.jpg" and x["reason"].startswith("over ") for x in s["skipped"])
    assert "b.jpg" not in [f["file"] for f in s["files"]]


def test_the_size_limit_is_25_mb_like_the_web_page():
    from secondlook.web.app import MAX_BYTES
    assert batch.MAX_BYTES == MAX_BYTES == 25 * 1024 * 1024


def test_image_over_the_classifier_shape_limits_is_skipped(folder, tmp_path, monkeypatch):
    Image.new("RGB", (400, 40)).save(folder / "thin.png")  # 10:1, over the 8:1 limit
    s = run(folder, tmp_path / "out")
    skipped = {x["file"]: x["reason"] for x in s["skipped"]}
    assert "longer than 8:1" in skipped["thin.png"]
    monkeypatch.setattr(classifier, "MAX_PIXELS", 96 * 64 - 1)  # now every 96 x 64 picture is too big
    s = run(folder, tmp_path / "out2")
    assert s["counts"]["checked"] == 0
    assert all("pixels, over the" in x["reason"] for x in s["skipped"] if x["file"] != "thin.png")


def test_the_limits_are_the_classifiers():
    assert (classifier.MAX_PIXELS, classifier.MAX_ASPECT) == (100_000_000, 8)


def test_a_file_that_is_not_really_an_image_still_gets_the_ladders_answer(folder, tmp_path):
    (folder / "junk.jpg").write_bytes(os.urandom(2048))
    s = run(folder, tmp_path / "out")
    row = next(f for f in s["files"] if f["file"] == "junk.jpg")
    assert row["answer"] == "cant_tell" and row["dhash"] is None and row["near_duplicate_group"] is None


# --- receipts ------------------------------------------------------------------------------------

def _strip_created_at(path: Path) -> dict:
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec.pop("created_at")
    return rec


def test_a_two_image_batch_writes_the_real_receipts_and_a_pdf(tmp_path):
    pytest.importorskip("reportlab")
    d = tmp_path / "two"
    d.mkdir()
    picture(1).save(d / "a.png")
    picture(2).save(d / "b.jpg", quality=92)
    s = run(d, tmp_path / "out")
    assert s["receipts"] == {"writer": "secondlook.receipt", "pdf": True, "pdf_written": 2, "pdf_failed": 0}
    receipts = tmp_path / "out" / "receipts"
    assert sorted(p.name for p in receipts.iterdir()) == [
        "0000-a.png.json", "0000-a.png.json.sha256", "0000-a.png.pdf",
        "0001-b.jpg.json", "0001-b.jpg.json.sha256", "0001-b.jpg.pdf"]
    for f in s["files"]:
        assert f["receipt_error"] is None
        j = tmp_path / "out" / f["receipt_json"]
        rec = json.loads(j.read_text(encoding="utf-8"))
        assert rec["schema"] == "second-look-receipt/1"  # the real writer, not the old stand-in
        assert rec["file"]["sha256"] == f["sha256"] and rec["answer"] == f["answer"]
        assert [e["rung"] for e in rec["evidence"]] and all(e["does_not_prove"] for e in rec["evidence"])
        # the .sha256 file is the hash of exactly the JSON bytes, in sha256sum format
        assert (j.with_name(j.name + ".sha256")).read_text() == f"{hashlib.sha256(j.read_bytes()).hexdigest()}  {j.name}\n"
        assert (tmp_path / "out" / f["receipt_pdf"]).read_bytes().startswith(b"%PDF")


def test_two_batch_runs_give_identical_receipts_apart_from_created_at(tmp_path):
    pytest.importorskip("reportlab")
    d = tmp_path / "two"
    d.mkdir()
    picture(1).save(d / "a.png")
    picture(2).save(d / "b.jpg", quality=92)
    run(d, tmp_path / "o1")
    run(d, tmp_path / "o2")
    names = sorted(p.name for p in (tmp_path / "o1" / "receipts").glob("*.json"))
    assert len(names) == 2
    for n in names:
        assert _strip_created_at(tmp_path / "o1" / "receipts" / n) == _strip_created_at(tmp_path / "o2" / "receipts" / n)


def test_no_pdf_writes_the_json_receipts_only(folder, tmp_path):
    s = run(folder, tmp_path / "out", pdf=False)
    assert s["receipts"] == {"writer": "secondlook.receipt", "pdf": False, "pdf_written": 0, "pdf_failed": 0}
    names = [p.name for p in (tmp_path / "out" / "receipts").iterdir()]
    assert not any(n.endswith(".pdf") for n in names)
    assert len([n for n in names if n.endswith(".json")]) == len([n for n in names if n.endswith(".json.sha256")]) == 4
    assert all(f["receipt_pdf"] is None and f["receipt_json"] and f["receipt_error"] is None for f in s["files"])


def test_it_calls_the_receipt_module_by_the_agreed_names(folder, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(batch.receipt_writer, "build",
                        lambda result, path, *, inputs=None: calls.append(("build", Path(path).name)) or {"a": result.answer})
    monkeypatch.setattr(batch.receipt_writer, "write_json",
                        lambda receipt, out: (calls.append(("json", Path(out).name)), Path(out).write_text("{}")))
    monkeypatch.setattr(batch.receipt_writer, "write_pdf",
                        lambda receipt, out: (calls.append(("pdf", Path(out).name)), Path(out).write_bytes(b"%PDF")))
    s = run(folder, tmp_path / "out")
    assert s["receipts"] == {"writer": "secondlook.receipt", "pdf": True, "pdf_written": 4, "pdf_failed": 0}
    assert all(f["receipt_pdf"] and (tmp_path / "out" / f["receipt_pdf"]).read_bytes() == b"%PDF" for f in s["files"])
    assert [c[0] for c in calls].count("pdf") == 4
    assert ("json", "0000-a.png.json") in calls and ("pdf", "0000-a.png.pdf") in calls


def test_a_receipt_that_fails_is_stated_and_the_answer_stands(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(batch.receipt_writer, "build", lambda result, path, *, inputs=None: 1 / 0)
    s = run(folder, tmp_path / "out")
    assert all(f["receipt_error"] == "ZeroDivisionError" and f["receipt_json"] is None and f["answer"] for f in s["files"])


def test_summary_does_not_say_pdf_true_when_a_pdf_failed(folder, tmp_path, monkeypatch):
    """The JSON receipt is written, the PDF writer fails on the second file: pdf is false and the counts say so."""
    def write_pdf(receipt, out):
        if Path(out).name.startswith("0001-"):
            raise ImportError("no PDF writer")
        Path(out).write_bytes(b"%PDF")
    monkeypatch.setattr(batch.receipt_writer, "write_pdf", write_pdf)
    s = run(folder, tmp_path / "out")
    assert s["receipts"] == {"writer": "secondlook.receipt", "pdf": False, "pdf_written": 3, "pdf_failed": 1}
    bad = [f for f in s["files"] if f["receipt_error"]]
    assert [f["receipt_pdf"] for f in bad] == [None] and bad[0]["receipt_json"] and bad[0]["receipt_error"] == "ImportError"
    on_disk = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["receipts"] == s["receipts"]


def test_summary_counts_every_pdf_failed_when_no_pdf_writer_works(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(batch.receipt_writer, "write_pdf", lambda receipt, out: 1 / 0)
    s = run(folder, tmp_path / "out")
    assert s["receipts"] == {"writer": "secondlook.receipt", "pdf": False, "pdf_written": 0, "pdf_failed": 4}


def test_a_rerun_leaves_no_stale_receipts_and_keeps_other_files(folder, tmp_path):
    out = tmp_path / "out"
    run(folder, out)
    (out / "receipts" / "keep-me.txt").write_text("mine")
    (folder / "b.jpg").unlink()
    run(folder, out)
    names = sorted(p.name for p in (out / "receipts").iterdir())
    assert len([n for n in names if n.endswith(".json")]) == 3 and "keep-me.txt" in names


def test_a_rerun_clears_the_sidecars_the_real_receipt_writer_leaves(folder, tmp_path):
    out = tmp_path / "out"
    s = run(folder, out)
    assert s["receipts"]["writer"] == "secondlook.receipt"
    receipts = out / "receipts"
    assert len(list(receipts.glob("*.json.sha256"))) == 4
    (receipts / "keep-me.json.sha256").write_text("mine")  # not one of ours: no NNNN- prefix
    (folder / "b.jpg").unlink()
    run(folder, out)
    names = sorted(p.name for p in receipts.iterdir())
    sidecars = [n for n in names if n.endswith(".json.sha256") and n != "keep-me.json.sha256"]
    jsons = [n for n in names if n.endswith(".json")]
    assert len(jsons) == 3 and sorted(j + ".sha256" for j in jsons) == sidecars  # none left for the removed file
    assert not any("b.jpg" in n for n in names) and "keep-me.json.sha256" in names


# --- determinism ---------------------------------------------------------------------------------

def _files(out):
    return {n: (out / n).read_bytes() for n in ("summary.json", "summary.csv", "summary.html")}


def test_the_summary_is_byte_for_byte_the_same_on_every_run(folder, tmp_path):
    run(folder, tmp_path / "o1")
    run(folder, tmp_path / "o2")
    assert _files(tmp_path / "o1") == _files(tmp_path / "o2")


def test_the_summary_does_not_depend_on_the_number_of_workers(folder, tmp_path):
    (folder / "copy-of-a.png").write_bytes((folder / "a.png").read_bytes())
    run(folder, tmp_path / "o1", jobs=1)
    s2 = run(folder, tmp_path / "o2", jobs=2, allow_many_workers=True)
    assert s2["run"]["jobs"] == 2
    assert _files(tmp_path / "o1") == _files(tmp_path / "o2")


def test_the_summary_carries_no_clock_and_no_path_outside_the_folder(folder, tmp_path):
    run(folder, tmp_path / "out")
    text = (tmp_path / "out" / "summary.json").read_text()
    assert str(tmp_path) not in text and "elapsed" not in text and "seconds" not in text
    assert json.loads(text)["folder"] == "photos"
    assert "seconds_per_image" in (tmp_path / "out" / "run.json").read_text()


# --- near-duplicate copies -----------------------------------------------------------------------

def test_a_resaved_and_a_shrunk_copy_are_grouped_with_the_original(folder, tmp_path):
    base = picture(7, (192, 128))
    base.save(folder / "orig.png")
    base.save(folder / "resaved.jpg", quality=60)
    base.resize((96, 64), Image.Resampling.LANCZOS).save(folder / "shrunk.webp")
    s = run(folder, tmp_path / "out")
    assert len(s["near_duplicate_groups"]) == 1
    g = s["near_duplicate_groups"][0]
    assert g["files"] == ["orig.png", "resaved.jpg", "shrunk.webp"]
    assert g["label"] == "near-duplicate copies" and g["limit"] == 8 and g["max_distance"] <= 8
    assert "never change an answer" in g["note"]
    grouped = {f["file"]: f["near_duplicate_group"] for f in s["files"]}
    assert grouped["orig.png"] == grouped["resaved.jpg"] == grouped["shrunk.webp"] == "g1"
    assert grouped["a.png"] is None and grouped["b.jpg"] is None


def test_different_pictures_are_not_grouped(folder, tmp_path):
    s = run(folder, tmp_path / "out")
    assert s["near_duplicate_groups"] == [] and s["counts"]["near_duplicate_groups"] == 0


def test_a_rotated_copy_is_grouped_through_its_exif_orientation(folder, tmp_path):
    base = picture(11, (160, 96))
    base.save(folder / "upright.jpg", quality=95)
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways, to be shown rotated 90 degrees clockwise
    base.transpose(Image.Transpose.ROTATE_90).save(folder / "sideways.jpg", quality=95, exif=exif)
    h1, _ = batch.dhash(str(folder / "upright.jpg"))
    h2, _ = batch.dhash(str(folder / "sideways.jpg"))
    assert h1 is not None and (h1 ^ h2).bit_count() <= batch.HAMMING_MAX


def test_flat_pictures_are_not_grouped_by_hash_but_identical_files_are(folder, tmp_path):
    Image.new("RGB", (96, 64), (200, 10, 10)).save(folder / "red.png")
    Image.new("RGB", (96, 64), (10, 10, 200)).save(folder / "blue.png")  # both flat: same hash, different pictures
    s = run(folder, tmp_path / "out")
    assert s["near_duplicate_groups"] == []
    (folder / "red-again.png").write_bytes((folder / "red.png").read_bytes())  # the same file twice
    s = run(folder, tmp_path / "out")
    assert [g["files"] for g in s["near_duplicate_groups"]] == [["red-again.png", "red.png"]]
    assert s["near_duplicate_groups"][0]["max_distance"] == 0


def test_the_distance_limit_is_8_bits():
    def groups(*pairs):
        return batch.group_near_duplicates([{"file": n, "sha256": n, "dhash": h, "usable": True} for n, h in pairs])

    g = groups(("a", 0), ("b", 0b11111111), ("far", (1 << 64) - 1))
    assert [x["files"] for x in g] == [["a", "b"]] and g[0]["max_distance"] == 8  # 8 bits apart: grouped
    assert groups(("a", 0), ("c", 0b111111111)) == []                              # 9 bits apart: not
    # A group is a chain of links, so its widest pair can be past the limit; the summary says how wide.
    chain = groups(("a", 0), ("b", 0b11111111), ("c", 0b111111111))
    assert [x["files"] for x in chain] == [["a", "b", "c"]] and chain[0]["max_distance"] == 9


def test_groups_never_change_an_answer(folder, tmp_path, monkeypatch):
    # An AI label in the metadata of one copy, none on a stripped copy: they are grouped, and the answers differ.
    base = picture(5, (192, 128))
    base.save(folder / "labelled.jpg", xmp=XMP_AI)
    base.save(folder / "stripped.jpg")
    s = run(folder, tmp_path / "out")
    by = {f["file"]: f for f in s["files"]}
    assert by["labelled.jpg"]["near_duplicate_group"] == by["stripped.jpg"]["near_duplicate_group"] is not None
    assert by["labelled.jpg"]["answer"] == "concerns" and by["stripped.jpg"]["answer"] == "cant_tell"
    # And with the grouping switched off entirely, every answer, summary and reason is the same.
    monkeypatch.setattr(batch, "group_near_duplicates", lambda items: [])
    s2 = run(folder, tmp_path / "out2")
    strip = lambda s: [{k: v for k, v in f.items() if k != "near_duplicate_group"} for f in s["files"]]
    assert strip(s) == strip(s2)
    assert s2["near_duplicate_groups"] == []


# --- outputs: csv, html --------------------------------------------------------------------------

def test_csv_has_one_row_per_file_and_neutralises_formulas(folder, tmp_path):
    picture(21).save(folder / "=cmd.png")
    s = run(folder, tmp_path / "out")
    rows = list(csv.DictReader((tmp_path / "out" / "summary.csv").open(newline="")))
    assert len(rows) == len(s["files"]) == 5
    assert rows[0].keys() >= {"file", "answer", "summary", "sha256", "near_duplicate_group", "receipt_json"}
    assert "'=cmd.png" in [r["file"] for r in rows]
    assert not any(r["file"].startswith("=") for r in rows)


def test_html_is_static_escaped_and_self_contained(folder, tmp_path):
    picture(22).save(folder / "a<b>&c.png")
    run(folder, tmp_path / "out")
    page = (tmp_path / "out" / "summary.html").read_text()
    assert 'a&lt;b&gt;&amp;c<span class="ext">.png</span>' in page and "a<b>&c" not in page
    assert "<script" not in page.lower() and "<link" not in page.lower() and "<img" not in page.lower()
    assert not re.search(r"(src|href|url\()\s*=?\s*[\"']?(https?:)?//", page)
    assert "default-src 'none'" in page


def test_copy_never_makes_banned_claims(folder, tmp_path):
    run(folder, tmp_path / "out")
    page = (tmp_path / "out" / "summary.html").read_text()
    for banned in (r"detects? ai", r"\baccura", r"this is real", "—"):
        assert not re.search(banned, page, re.I), banned


# --- no network ----------------------------------------------------------------------------------

def test_a_batch_makes_no_network_call(folder, tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network used")

    for name in ("connect", "connect_ex", "sendto", "bind"):
        monkeypatch.setattr(socket.socket, name, refuse)
    for name in ("getaddrinfo", "gethostbyname", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    picture(5, (192, 128)).save(folder / "labelled.jpg", xmp=XMP_AI)
    s = run(folder, tmp_path / "out")
    assert s["counts"]["checked"] == 5


# --- the worker bound and the spawn guard --------------------------------------------------------

@pytest.mark.parametrize("cores, limit", [(18, 4), (8, 4), (6, 3), (4, 2), (2, 1), (1, 1), (None, 1)])
def test_the_worker_limit_is_the_smaller_of_4_and_half_the_cores(monkeypatch, cores, limit):
    monkeypatch.setattr(os, "cpu_count", lambda: cores)
    assert batch.max_jobs() == limit
    assert batch.default_jobs() <= limit


def test_more_workers_than_the_limit_needs_the_flag_and_names_the_memory(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "max_jobs", lambda: 1)
    with pytest.raises(ValueError, match=r"above the limit of 1.*0\.9 GiB.*allow_many_workers"):
        run(folder, tmp_path / "o1", jobs=2)
    assert not (tmp_path / "o1" / "summary.json").exists()
    s = run(folder, tmp_path / "o2", jobs=2, allow_many_workers=True)
    assert s["run"]["jobs"] == 2 and s["counts"]["checked"] == 4


def test_the_default_and_the_limit_itself_never_need_the_flag(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 2)
    assert run(folder, tmp_path / "o1", jobs=None)["run"]["jobs"] == 1
    assert run(folder, tmp_path / "o2", jobs=1)["run"]["jobs"] == 1


def test_cli_batch_refuses_jobs_over_the_limit_without_the_flag(folder, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(batch, "max_jobs", lambda: 1)
    monkeypatch.setattr(batch, "run_batch", lambda *a, **k: _real(*a, **{**k, "rung_names": FAST}))
    out = tmp_path / "out"
    assert cli.main(["batch", str(folder), "--out", str(out), "--jobs", "2", "--no-pdf"]) == 2
    err = capsys.readouterr().err
    assert "--allow-many-workers" in err and "0.9 GiB" in err and not (out / "summary.json").exists()
    assert cli.main(["batch", str(folder), "--out", str(out), "--jobs", "2", "--no-pdf", "--allow-many-workers"]) == 0
    cap = capsys.readouterr()
    assert "0.9 GiB" in cap.err and "2 worker(s)" in cap.out


GUARDED = textwrap.dedent("""
    import sys
    from secondlook import batch
    {call_open}batch.run_batch(sys.argv[1], sys.argv[2], jobs=2, rung_names=("exif",), allow_many_workers=True){call_close}
""")


def _script(tmp_path, guarded: bool) -> Path:
    body = GUARDED.format(call_open="" if not guarded else "if __name__ == '__main__':\n    ", call_close="")
    path = tmp_path / ("guarded.py" if guarded else "unguarded.py")
    path.write_text(body)
    return path


def _run_script(script, folder, out):
    return subprocess.run([sys.executable, str(script), str(folder), str(out)], capture_output=True, text=True,
                          timeout=180, cwd=script.parent)


def test_an_unguarded_script_gets_a_clear_error_not_a_summary_of_failed_files(folder, tmp_path):
    out = tmp_path / "out"
    p = _run_script(_script(tmp_path, guarded=False), folder, out)
    assert p.returncode != 0
    assert 'if __name__ == "__main__"' in p.stderr and "RuntimeError" in p.stderr
    assert not (out / "summary.json").exists()  # nothing was reported as failed, because nothing was written


def test_a_guarded_script_runs_with_workers(folder, tmp_path):
    out = tmp_path / "out"
    p = _run_script(_script(tmp_path, guarded=True), folder, out)
    assert p.returncode == 0, p.stderr
    s = json.loads((out / "summary.json").read_text())
    assert s["counts"]["checked"] == 4 and s["counts"]["failed"] == 0


# --- the CLI -------------------------------------------------------------------------------------

def test_cli_batch_writes_the_summary_and_prints_the_counts(folder, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(batch, "run_batch", lambda *a, **k: _real(*a, **{**k, "rung_names": FAST}))
    assert cli.main(["batch", str(folder), "--out", str(tmp_path / "out"), "--jobs", "1", "--no-pdf"]) == 0
    cap = capsys.readouterr()
    assert "Checked 4 of 4 images" in cap.out and "Near-duplicate groups (information only): 0" in cap.out
    assert "[4/4]" in cap.err
    assert (tmp_path / "out" / "summary.html").is_file()


def test_cli_batch_refuses_a_missing_folder_and_bad_jobs(tmp_path, capsys):
    assert cli.main(["batch", str(tmp_path / "nope"), "--out", str(tmp_path / "o")]) == 2
    assert "is not a folder" in capsys.readouterr().err
    assert cli.main(["batch", str(tmp_path), "--out", str(tmp_path / "o"), "--jobs", "0"]) == 2


def test_cli_check_is_unchanged(tmp_path, capsys):
    # The batch command leaves check alone: the Result's fields plus E1's plain-language explanation.
    f = tmp_path / "a.jpg"
    picture(1).save(f)
    assert cli.main(["check", str(f)]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"answer", "evidence", "summary", "version", "explanation"}
