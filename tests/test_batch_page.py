"""Tests for the batch view (DAT-850): GET /batch, per-file seconds and receipts on /api/batch, the receipt zip,
the single-file page's words, and the page's copy and loopback rules."""
import base64
import hashlib
import io
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from secondlook import receipt as receipts  # noqa: E402
from secondlook.web.app import CSP, STATIC, create_app  # noqa: E402
from tests.test_web import BANNED  # noqa: E402

BATCH_FILES = ["batch.html", "batch.css", "batch.js", "batch-zip.js"]
client = TestClient(create_app(stub=False, rungs=[]))
stub_client = TestClient(create_app(stub=True))


def png(seed: int, size=(96, 64)) -> bytes:
    im = Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256))
    for x in range(0, size[0], 7):
        im.putpixel((x, (x * seed) % size[1]), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def post(files, c=client, query="?receipt=1"):
    return c.post("/api/batch" + query, files=[("files", (n, d, "application/octet-stream")) for n, d in files])


# --- the route --------------------------------------------------------------------------------------

def test_batch_page_is_served_with_the_same_security_headers():
    r = client.get("/batch")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == CSP
    for f in BATCH_FILES:
        assert f in r.text or f == "batch.html"
        assert client.get(f"/static/{f}").status_code == 200
    # Its own files only, plus the shared tokens in app.css.
    assert re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text) == [
        "/static/app.css", "/static/batch.css", "/static/batch-zip.js", "/static/batch.js"]


def test_the_single_file_page_links_to_the_batch_view_and_back():
    index = (STATIC / "index.html").read_text()
    assert index.count('href="/batch"') == 1 and "Check a folder" in index
    assert 'href="/"' in (STATIC / "batch.html").read_text()


def test_the_batch_page_makes_no_request_off_this_computer():
    for f in BATCH_FILES:
        text = (STATIC / f).read_text()
        assert not re.search(r"https?://", text), f
        assert not re.search(r"//[a-z0-9.-]+\.[a-z]{2,}", text, re.I), f
    js = (STATIC / "batch.js").read_text()
    assert set(re.findall(r'fetch\("([^"]+)"', js)) == {"/api/health", "/api/batch?receipt=1"}


# --- seconds and receipts on /api/batch -------------------------------------------------------------

def test_each_checked_file_has_its_seconds_and_a_receipt_byte_for_byte_as_the_cli_writes_it():
    r = post([("one.png", png(1)), ("two.png", png(2)), ("notes.txt", b"hi")])
    assert r.status_code == 200, r.text
    one, two, notes = r.json()["results"]
    for entry, name in ((one, "one.png"), (two, "two.png")):
        assert entry["status"] == 200 and isinstance(entry["seconds"], (int, float)) and entry["seconds"] >= 0
        rc = entry["receipt"]
        assert rc["schema"] == receipts.SCHEMA and rc["answer"] == entry["answer"]
        # The name the user gave the file, not the server's temp folder or its numbered copy.
        assert rc["file"]["name"] == name and "/" not in json.dumps(rc["file"]["name"])
        assert "secondlook-" not in entry["receipt_json"]
        assert entry["receipt_json"].encode() == receipts.canonical_json(rc)
        assert entry["receipt_sha256"] == hashlib.sha256(entry["receipt_json"].encode()).hexdigest()
        assert entry["receipt_name"] == name + ".receipt.json"
        assert rc["demo"] is False
    assert notes["status"] == 415 and "receipt" not in notes and "seconds" not in notes


def test_a_batch_receipt_is_byte_for_byte_the_cli_receipt_for_a_non_ascii_name_with_a_space(tmp_path, monkeypatch):
    from secondlook import cli, ladder
    name = "ünïcode name.png"
    monkeypatch.setattr(receipts, "_now", lambda: "2026-10-29T00:00:00Z")
    # The same rungs as the test app (none), through the CLI's own `check --receipt` path.
    monkeypatch.setattr(cli, "run", lambda path, context=None: ladder.run(path, rungs=[], context=context))
    (tmp_path / "in").mkdir()
    src = tmp_path / "in" / name
    src.write_bytes(png(4))
    assert cli.main(["check", str(src), "--receipt", str(tmp_path / "out")]) in (0, 3)  # 3: no ReportLab, no PDF
    cli_json = (tmp_path / "out" / f"{name}.receipt.json").read_bytes()
    cli_sha = (tmp_path / "out" / f"{name}.receipt.json.sha256").read_text(encoding="utf-8")

    (entry,) = post([(name, src.read_bytes())]).json()["results"]
    assert entry["receipt_json"].encode("utf-8") == cli_json
    assert entry["receipt_name"] == f"{name}.receipt.json" and json.loads(cli_json)["file"]["name"] == name
    assert f"{entry['receipt_sha256']}  {entry['receipt_name']}\n" == cli_sha
    assert entry["file"] == "_n_code_name.png"  # the cleaned name is only the temp copy's (and the API's key)


def test_receipts_come_only_when_asked_and_the_seconds_always():
    (entry,) = post([("one.png", png(1))], query="").json()["results"]
    assert "receipt" not in entry and "receipt_json" not in entry and entry["seconds"] >= 0


def test_a_demo_answer_says_so_in_its_receipt():
    (entry,) = post([("demo-concerns.png", png(3))], c=stub_client).json()["results"]
    assert entry["demo"] is True and entry["receipt"]["demo"] is True and '"demo": true' in entry["receipt_json"]


def test_a_failed_receipt_leaves_the_answer(monkeypatch):
    monkeypatch.setattr(receipts, "build", lambda result, path, *, inputs=None: 1 / 0)
    (entry,) = post([("one.png", png(1))]).json()["results"]
    assert entry["status"] == 200 and entry["answer"] == "cant_tell"
    assert entry["receipt_error"] == "The check ran but the receipt failed." and "receipt_json" not in entry


def test_each_batch_entry_carries_its_own_failed_check_reasons_with_receipts_on():
    from secondlook.types import NotInstalled

    def missing(path):
        raise NotInstalled("no weights")

    def slow_on_two(path):
        if Path(path).name == "two.png":
            raise TimeoutError("slow")
        return []

    c = TestClient(create_app(stub=False, rungs=[("watermark", missing), ("classifier", slow_on_two)]))
    one, two = post([("one.png", png(1)), ("two.png", png(2))], c=c).json()["results"]
    # One reason per failed check, in the same order, from the ladder's fixed list, for that file only.
    assert one["rungs"] == ["classifier"] and two["rungs"] == []
    assert one["rungs_failed"] == ["watermark"] and one["rungs_failed_why"] == ["not installed"]
    assert two["rungs_failed"] == ["watermark", "classifier"]
    assert two["rungs_failed_why"] == ["not installed", "timed out"]
    for entry in (one, two):
        assert entry["status"] == 200 and entry["receipt_name"] == entry["file"] + ".receipt.json"
        assert "These checks could not run: " + ", ".join(entry["rungs_failed"]) in entry["receipt_json"]


def test_near_duplicate_copies_are_still_grouped_with_receipts_on():
    body = post([("a.png", png(5, (192, 128))), ("b.png", png(5, (192, 128))), ("c.png", png(9))]).json()
    (g,) = body["near_duplicate_groups"]
    assert g["indexes"] == [0, 1] and g["files"] == ["a.png", "b.png"]
    assert [x.get("near_duplicate_group") for x in body["results"]] == ["g1", "g1", None]


# --- the receipt zip, built in the page -------------------------------------------------------------

NODE = shutil.which("node") or shutil.which("bun")


def in_page_scripts(expr: str, data) -> str:
    """Evaluate `expr` with the page's own batch-zip.js and batch.js loaded (no document, so only their plain
    functions run); `data` is the JSON value bound to `input`. Returns what the expression printed."""
    script = (
        "const fs = require('fs'), vm = require('vm');"
        "const box = { TextEncoder, Uint8Array, Uint32Array, DataView, ArrayBuffer, Buffer };"
        "box.globalThis = box;"
        "for (const f of process.argv.slice(1)) vm.runInNewContext(fs.readFileSync(f, 'utf8'), box);"
        "box.input = JSON.parse(fs.readFileSync(0, 'utf8'));"
        f"process.stdout.write(String(vm.runInNewContext({json.dumps(expr)}, box)));"
    )
    out = subprocess.run([NODE, "-e", script, str(STATIC / "batch-zip.js"), str(STATIC / "batch.js")],
                         input=json.dumps(data), capture_output=True, text=True, timeout=60, check=True)
    return out.stdout


def page_zip(entries: list[dict], groups: list[dict]) -> bytes:
    """The zip exactly as the page's "Download all receipts" builds it: batch.js's bundleFiles, batch-zip.js's zip."""
    expr = ("Buffer.from(SecondLookZip.zip(SecondLookBatch.bundleFiles(input.entries, input.groups)))"
            ".toString('base64')")
    return base64.b64decode(in_page_scripts(expr, {"entries": entries, "groups": groups}))


def page_entries(results: list[dict], groups: list[dict]) -> list[dict]:
    """The page's row records for one set: row numbers from 1, the path it was given, its group number."""
    group_of = {k: g["n"] for g in groups for k in g["indexes"]}
    return [{"n": i + 1, "path": r["file"] if i else f"holiday/{r['file']}", "group": group_of.get(i),
             "result": r} for i, r in enumerate(results)]


@pytest.mark.skipif(NODE is None, reason="needs node or bun to run the page's zip writer")
def test_the_receipt_zip_from_the_pages_own_code_holds_every_receipt_its_sha256_and_the_summary():
    body = post([("one.png", png(5, (192, 128))), ("ünïcode name.png", png(5, (192, 128))), ("notes.txt", b"hi"),
                 ("three.png", png(9))]).json()
    results = body["results"]
    groups = [{"n": i + 1, "indexes": g["indexes"]} for i, g in enumerate(body["near_duplicate_groups"])]
    assert groups == [{"n": 1, "indexes": [0, 1]}]
    entries = page_entries(results, groups)
    summary_groups = [{"group": g["n"], "files": [k + 1 for k in g["indexes"]]} for g in groups]
    data = page_zip(entries, summary_groups)
    assert page_zip(entries, summary_groups) == data  # fixed dates, stored entries: same receipts, same zip

    checked = [(e["n"], e["result"]) for e in entries if e["result"]["status"] == 200]
    assert len(checked) == 3
    expected = []
    for n, r in checked:
        expected += [f"second-look-receipts/receipts/{n:04d}-{r['receipt_name']}",
                     f"second-look-receipts/receipts/{n:04d}-{r['receipt_name']}.sha256"]
    expected.append("second-look-receipts/summary.json")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
        assert z.namelist() == expected  # nothing dropped, nothing extra, in row order
        for n, r in checked:
            name = f"{n:04d}-{r['receipt_name']}"
            receipt_bytes = z.read(f"second-look-receipts/receipts/{name}")
            assert receipt_bytes == r["receipt_json"].encode("utf-8") == receipts.canonical_json(r["receipt"])
            assert z.read(f"second-look-receipts/receipts/{name}.sha256").decode("utf-8") == (
                f"{hashlib.sha256(receipt_bytes).hexdigest()}  {name}\n")
        summary = json.loads(z.read("second-look-receipts/summary.json"))
    assert summary["schema"] == "second-look-batch-page/1"
    assert summary["near_duplicate_groups"] == [{"group": 1, "files": [1, 2]}]
    rows = summary["files"]
    assert [x["n"] for x in rows] == [1, 2, 3, 4] and rows[0]["path"] == "holiday/one.png"
    for x, e in zip(rows, entries):
        r = e["result"]
        assert x["status"] == r["status"]
        if r["status"] == 200:
            assert x["receipt"] == f"receipts/{e['n']:04d}-{r['receipt_name']}"
            assert x["receipt_sha256"] == r["receipt_sha256"] and x["answer"] == r["answer"] == "cant_tell"
            assert x["headline"] == "Can't tell" and x["seconds"] == r["seconds"] and x["error"] is None
        else:  # the refused file has its row, its reason, and no receipt
            assert x["receipt"] is None and x["receipt_sha256"] is None and x["answer"] is None
            assert x["error"] == r["error"] and r["status"] == 415
    assert [x["near_duplicate_group"] for x in rows] == [1, 1, None, None]


@pytest.mark.skipif(NODE is None, reason="needs node or bun to check the page's scripts")
def test_the_page_scripts_parse():
    for f in ("batch.js", "batch-zip.js"):
        subprocess.run([shutil.which("node") or NODE, "--check", str(STATIC / f)], check=True, timeout=60)


# --- the words: the single-file page's, and no new claims ---------------------------------------------

def labels(js: str) -> dict:
    return dict(re.findall(r'(\w+):\s*\{\s*label:\s*"([^"]+)"', js))


def test_the_rows_use_the_single_file_page_answer_words():
    app, batch = (STATIC / "app.js").read_text(), (STATIC / "batch.js").read_text()
    assert labels(batch) == labels(app) and len(labels(app)) == 4
    for pattern in (r"const COUNT = \[[^\]]+\];", r"const concernLabel = [^;]+;", r"const RUNGS = \{[^}]+\};"):
        assert re.search(pattern, batch).group(0) == re.search(pattern, app).group(0), pattern


@pytest.mark.skipif(NODE is None, reason="needs node or bun to run the pages' scripts")
def test_the_concern_reason_is_the_single_file_pages_word_for_word():
    """Both pages' concernReason() on the same cards: the same words, except that the single-file page adds
    ", listed first below" to the count of the rest (it lists them below; the batch row has no list)."""
    app_fn = re.search(r"\n  function concernReason\(.*?\n  \}\n", (STATIC / "app.js").read_text(), re.S).group(0)
    ev = [{"finding": "Unsigned IPTC metadata says it was made with generative AI."}, {"finding": "  Second.  "},
          {"finding": ""}, {"finding": "Third."}, {"nope": 1}]
    cases = [[], [0], [2], [0, 1], [1, 0, 3], [4, 0], [9]]
    expr = ("JSON.stringify(input.cases.map(c => [SecondLookBatch.concernReason(input.ev, c),"
            f" ({app_fn.strip()})(input.ev, c)]))")
    pairs = json.loads(in_page_scripts(expr, {"ev": ev, "cases": cases}))
    assert pairs[0] == ["", ""] and pairs[3][0] == "Unsigned IPTC metadata says it was made with generative AI. And 1 more reason."
    for batch_text, app_text in pairs:
        assert batch_text == app_text.replace(", listed first below.", "."), (batch_text, app_text)


def test_the_batch_copy_makes_no_banned_claim():
    for f in BATCH_FILES:
        text = (STATIC / f).read_text()
        for pat in BANNED:
            assert not re.search(pat, text, re.I), f"{pat!r} in {f}"


def test_small_rust_text_uses_the_aa_token():
    css = (STATIC / "batch.css").read_text()
    for rule in re.findall(r"[^{}]+\{[^}]*\}", css):
        assert not re.search(r"(?<![-\w])color:\s*var\(--rust\)", rule), rule


# --- sets and near-duplicate tags (design fix round 2) ------------------------------------------------

SETS_NOTE = ("Files are sent in sets of up to 20 files and 100 MB, so near-duplicate copies are grouped only "
             "within each set.")
MB = 1024 * 1024


@pytest.mark.skipif(NODE is None, reason="needs node or bun to run the page's set split")
def test_the_sets_note_says_the_split_the_page_makes():
    """The note is true of sets(): a set closes at 20 files or at 100 MB, whichever comes first, so eight files of
    14 MB go as 7 and 1, not "20 at a time"."""
    html = re.sub(r"\s+", " ", (STATIC / "batch.html").read_text())
    note = re.search(r'<p class="sets-note" id="sets-note" hidden>([^<]+)</p>', html).group(1).strip()
    assert note == SETS_NOTE
    doc = (Path(__file__).resolve().parents[1] / "docs" / "DAT-850-BATCH.md").read_text()
    for text in (html, doc, (STATIC / "batch.js").read_text()):
        assert "20 at a time" not in text and "each set of 20" not in text
    assert SETS_NOTE in doc
    cases = {"8x14MB": [14 * MB] * 8, "23 small": [1024] * 23, "20x5MB": [5 * MB] * 20, "21x5MB": [5 * MB] * 21,
             "big then small": [25 * MB] * 4 + [1] * 30}
    expr = ("JSON.stringify(Object.fromEntries(Object.entries(input).map(([k, sizes]) =>"
            " [k, SecondLookBatch.sets(sizes.map(size => ({ file: { size } }))).map(s => s.length)])))")
    split = json.loads(in_page_scripts(expr, cases))
    assert split == {"8x14MB": [7, 1], "23 small": [20, 3], "20x5MB": [20], "21x5MB": [20, 1],
                     "big then small": [4, 20, 10]}
    for k, sizes in cases.items():  # every set keeps both limits
        i = 0
        for n in split[k]:
            assert n <= 20 and sum(sizes[i:i + n]) <= 100 * MB
            i += n


@pytest.mark.skipif(NODE is None, reason="needs node or bun to run the page's tag words")
def test_a_near_duplicate_tag_names_at_most_six_rows_then_how_many_more():
    cases = [[6], [3, 7], [1, 3, 4, 8, 9, 10], [1, 3, 4, 8, 9, 10, 13], list(range(2, 21))]
    tags = json.loads(in_page_scripts("JSON.stringify(input.map(SecondLookBatch.dupTag))", cases))
    assert tags == ["Near-duplicate of row 6", "Near-duplicate of rows 3, 7", "Near-duplicate of rows 1, 3, 4, 8, 9, 10",
                    "Near-duplicate of rows 1, 3, 4, 8, 9, 10 and 1 more",
                    "Near-duplicate of rows 2, 3, 4, 5, 6, 7 and 13 more"]  # a group of 20: 19 others
    # The row tag goes through dupTag; the list under the rows still names every row (rowList, uncapped).
    js = (STATIC / "batch.js").read_text()
    assert 'el("span", "btag dup", dupTag(e.dupOf))' in js and "rowList(g.files.map(e => e.n))" in js
