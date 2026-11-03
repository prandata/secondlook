"""DAT-850 (Codex and a reviewer FAIL on PR #39 @ 6b31d49): every surface carries the same summary.

The page, the desk note, the CLI's --plain, the JSON receipt, the PDF receipt and the batch summary files
all print explain(result).summary, never the ladder's own line. One check runs through the real ladder
with a detector card at or above the committed cut-off whose gate FAILed: the case where the ladder's
line ("No signed record and no concern was found") and the plain-language summary differ.
"""
from __future__ import annotations

import csv
import html
import json
import re

from fastapi.testclient import TestClient
from pypdf import PdfReader

from secondlook import batch, cli, investigator, ladder
from secondlook.web.app import create_app

from . import explain_samples as S

CUT = 0.4319957792758942   # the committed v2 threshold, whose gate FAILed (docs/RESULTS-v2.md)
SCORE = 0.4325             # two places would print 0.43, below the cut-off of 0.432
SENTENCE = ("Second Look can't tell how this was made. A detector scored it 0.432 out of 1, at or above our "
            "cut-off of 0.432. That cut-off failed its test, so we do not count the score as evidence either\u00a0way.")
CARD = "A detector scored this 0.432 out of 1, but our cut-off of 0.432 failed its test, so it is no concern."
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)
# Not named "classifier", so the batch worker does not try to load the real model.
RUNGS = [("fake_detector", lambda path: [S.classifier(SCORE, CUT, gate="FAIL")])]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def pdf_text(data: bytes, tmp_path) -> str:
    p = tmp_path / "r.pdf"
    p.write_bytes(data)
    return squash("\n".join(page.extract_text() for page in PdfReader(str(p)).pages))


def test_every_surface_carries_the_same_summary(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: list(RUNGS))
    folder = tmp_path / "photos"
    folder.mkdir()
    image = folder / "a.png"
    image.write_bytes(PNG)
    result = ladder.run(str(image))
    assert result.answer == "cant_tell"
    assert result.summary != SENTENCE   # the ladder's own line differs, so each surface below must not use it

    # The page: the plain-language box, the receipt JSON that /api/check?receipt=1 adds, and both downloads.
    client = TestClient(create_app(stub=False, rungs=list(RUNGS)))
    files = {"file": ("a.png", PNG, "image/png")}
    body = client.post("/api/check?receipt=1", files=files).json()
    assert body["explanation"]["summary"] == SENTENCE
    assert body["receipt"]["summary"] == SENTENCE
    got = client.post("/api/receipt?format=json", files=files)
    assert got.status_code == 200 and json.loads(got.content)["summary"] == SENTENCE
    got = client.post("/api/receipt?format=pdf", files=files)
    assert got.status_code == 200 and squash(SENTENCE) in pdf_text(got.content, tmp_path)

    # The desk note.
    note = investigator.explain_note(result, [])
    assert [n["text"] for n in note if n["template"].startswith("e1.cant_tell")] == [SENTENCE]
    assert CARD in [n["text"] for n in note]   # the card prints the same 0.432 as the summary

    # The CLI's --plain, and the receipt files it writes.
    out = tmp_path / "receipt"
    assert cli.main(["check", "--plain", str(image), "--receipt", str(out)]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[1] == SENTENCE and f"- {CARD}" in printed
    assert not any("0.43 out of 1" in line for line in printed)
    (receipt_json,) = out.glob("*.json")
    assert json.loads(receipt_json.read_text())["summary"] == SENTENCE
    (receipt_pdf,) = out.glob("*.pdf")
    assert squash(SENTENCE) in pdf_text(receipt_pdf.read_bytes(), tmp_path)

    # The batch: summary.json, summary.csv, summary.html and the receipt beside each file.
    s = batch.run_batch(folder, tmp_path / "batch", jobs=1, rung_names=["fake_detector"])
    (row,) = s["files"]
    assert row["summary"] == SENTENCE
    assert json.loads((tmp_path / "batch" / "summary.json").read_text())["files"][0]["summary"] == SENTENCE
    with open(tmp_path / "batch" / "summary.csv", newline="", encoding="utf-8") as f:
        assert [r["summary"] for r in csv.DictReader(f)] == [SENTENCE]
    assert SENTENCE in html.unescape((tmp_path / "batch" / "summary.html").read_text())
    (batch_json,) = (tmp_path / "batch" / "receipts").glob("*.json")
    assert json.loads(batch_json.read_text())["summary"] == SENTENCE
    (batch_pdf,) = (tmp_path / "batch" / "receipts").glob("*.pdf")
    assert squash(SENTENCE) in pdf_text(batch_pdf.read_bytes(), tmp_path)
