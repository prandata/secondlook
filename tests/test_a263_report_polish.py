"""DAT-850 A263: persona polish on the batch report and the server terminal.

1. summary.html in the product's look: cream, the serif stack, one boxed sheet, sha256 on its own line.
2. One set of answer labels: the CLI and summary.html use the page's (app.js ANSWERS, concernLabel).
3. The whisper "model not found" line is said once at server start, only where whisper-cli is installed,
   and never by the health poll.

Sources: the claims-handler persona run of 31 Oct 2026, snags 1 and 9, and
the newsroom-desk persona run, snag 5 (notes kept outside this repo).
"""
import logging
import re
import wave
from pathlib import Path

import pytest

from secondlook import batch, cli
from secondlook.rungs import voice as voice_rung

STATIC = Path(batch.__file__).parent / "web" / "static"
APP_JS = (STATIC / "app.js").read_text()
APP_CSS = (STATIC / "app.css").read_text()
HEX64 = "ab" * 32


def page_labels() -> dict[str, str]:
    """The page's own answer labels, read from app.js ANSWERS."""
    return dict(re.findall(r"(signed_ai|signed_camera|concerns|cant_tell):\s*\{\s*label:\s*\"([^\"]+)\"", APP_JS))


def summary(files, **counts) -> dict:
    base = {"images": len(files), "checked": len(files), "skipped": 0, "failed": 0,
            "signed_ai": 0, "signed_camera": 0, "concerns": 0, "cant_tell": 0, "near_duplicate_groups": 0}
    return {"folder": "claim-4471", "version": "abc123", "counts": base | counts, "files": files,
            "skipped": [], "failed": [], "near_duplicate_groups": []}


def row(name, answer, reasons=(), sha=HEX64):
    return {"file": name, "answer": answer, "summary": f"Summary of {name}.", "reasons": list(reasons),
            "receipt_json": f"receipts/0000-{name}.json", "receipt_pdf": f"receipts/0000-{name}.pdf",
            "near_duplicate_group": None, "sha256": sha}


# --- fix 2: one set of labels ------------------------------------------------------------------

def test_batch_labels_are_the_pages_labels():
    labels = page_labels()
    assert set(labels) == {"signed_ai", "signed_camera", "concerns", "cant_tell"}, "app.js ANSWERS moved"
    assert batch.ANSWER_LABELS == labels


@pytest.mark.parametrize("n, label", [(0, "Reasons to look closer"), (1, "One reason to look closer"),
                                      (2, "Two reasons to look closer"), (9, "Nine reasons to look closer"),
                                      (12, "12 reasons to look closer")])
def test_a_concerns_row_is_worded_by_its_count_as_the_page_does(n, label):
    # app.js concernLabel: COUNT words up to Nine, then the number; "reason" only for one.
    assert '"One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"' in APP_JS
    assert batch.answer_label("concerns", n) == label


def test_other_answers_ignore_the_count():
    assert batch.answer_label("signed_camera", 3) == "Signed as a camera capture"
    assert batch.answer_label("cant_tell") == "Can't tell"


def test_cli_prints_the_pages_labels(tmp_path, capsys, monkeypatch):
    s = summary([], signed_camera=2, concerns=1) | {"run": {"elapsed_seconds": 0.1, "jobs": 1},
                                                    "receipts": {"pdf_written": 0, "pdf_failed": 0}}
    monkeypatch.setattr(batch, "run_batch", lambda *a, **k: s)
    (tmp_path / "in").mkdir()
    assert cli.main(["batch", str(tmp_path / "in"), "--out", str(tmp_path / "out"), "--no-pdf"]) == 0
    out = capsys.readouterr().out
    assert "  Signed as a camera capture: 2" in out and "  Reasons to look closer: 1" in out
    assert "Concerns found" not in out and "Signed as camera capture" not in out


def test_summary_html_uses_the_pages_labels():
    page = batch._html(summary([row("08.jpg", "concerns", ["Caption and EXIF disagree."]),
                                row("09.jpg", "concerns", ["One.", "Two."]),
                                row("01.jpg", "signed_camera")], concerns=2, signed_camera=1))
    assert "One reason to look closer" in page and "Two reasons to look closer" in page
    assert "Signed as a camera capture" in page and "Reasons to look closer" in page
    assert "Concerns found" not in page and "Signed as camera capture" not in page


# --- fix 1: the product's look -----------------------------------------------------------------

def tokens(css: str) -> dict[str, str]:
    return {k: re.sub(r",\s+", ",", v.strip()) for k, v in re.findall(r"(--[a-z-]+):\s*([^;}]+)[;}]", css)}


def test_summary_html_carries_the_products_tokens_and_serif():
    page = batch._html(summary([row("a.jpg", "cant_tell")], cant_tell=1))
    ours, product = tokens(page), tokens(APP_CSS)
    for name in ("--cream", "--paper", "--ink", "--rust", "--rust-text", "--line", "--soft", "--serif", "--sans"):
        assert ours[name] == product[name], name
    assert "background:var(--cream)" in page and "font:700 36px/1.1 var(--title)" in page
    assert ours["--title"] == '"SL Title",var(--serif)', "the embedded heading font first, then the page's stack"


def test_summary_html_is_one_boxed_sheet():
    page = batch._html(summary([row("a.jpg", "cant_tell")], cant_tell=1))
    assert page.count("<main>") == 1 and "</main></body>" in page
    assert re.search(r"main\{[^}]*background:var\(--paper\)[^}]*border:1px solid var\(--line\)[^}]*border-radius", page)
    assert "border:1px solid #ccc" not in page and "#f2f2f2" not in page, "the old grey grid is gone"


def test_sha256_has_its_own_full_width_line_not_a_narrow_column():
    page = batch._html(summary([row("a.jpg", "cant_tell"), row("b.jpg", "cant_tell", sha="cd" * 32)], cant_tell=2))
    assert "<th>sha256</th>" not in page
    cells = re.findall(r'<td class="hash" colspan="(\d)"><span>sha256</span>([0-9a-f]{64})</td>', page)
    assert cells == [("4", HEX64), ("4", "cd" * 32)]
    header = re.search(r'<tr class="head">(.*?)</tr>', page).group(1)
    assert header.count("<th>") == 4, "the hash row spans every column"
    assert "word-break:break-all" not in page


def test_summary_html_stays_self_contained():
    page = batch._html(summary([row("a.jpg", "cant_tell")], cant_tell=1))
    # A271: the one url( is the heading font, carried as data:; nothing is fetched.
    assert re.findall(r"url\(([a-z]+:)", page) == ["data:"] and "@import" not in page
    assert "default-src 'none'; style-src 'unsafe-inline'; font-src data:" in page
    assert "—" not in page


# --- fix 3: the whisper line, once -------------------------------------------------------------

@pytest.fixture
def no_model(tmp_path, monkeypatch):
    monkeypatch.setenv(voice_rung.MODEL_DIR_ENV, str(tmp_path / "empty"))
    monkeypatch.setattr(voice_rung, "_setup_logged", False)


def whisper_lines(caplog):
    return [r for r in caplog.records if r.name == voice_rung.log.name and "whisper model" in r.getMessage()]


def test_health_polls_never_log_the_missing_model(no_model, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from secondlook.web.app import create_app

    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/opt/homebrew/bin/whisper-cli")
    client = TestClient(create_app(stub=True))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=voice_rung.log.name):
        for _ in range(5):
            assert client.get("/api/health").json()["capabilities"]["voice"] is False
    assert whisper_lines(caplog) == []


def test_server_start_says_it_once_when_whisper_cli_is_installed(no_model, monkeypatch, caplog):
    from secondlook.web.app import create_app

    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/opt/homebrew/bin/whisper-cli")
    with caplog.at_level(logging.WARNING, logger=voice_rung.log.name):
        create_app(stub=True)
        create_app(stub=True)
    lines = whisper_lines(caplog)
    assert len(lines) == 1
    assert lines[0].getMessage().startswith("voice notes: off. The whisper model small.en is not at ")


def test_server_start_is_quiet_without_whisper_cli(no_model, monkeypatch, caplog):
    """Start and the page's health polls: no whisper-model line at all without whisper-cli. Before A263 every
    poll said it; with the start-up line unguarded, start says it."""
    from fastapi.testclient import TestClient
    from secondlook.web.app import create_app

    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: None)
    with caplog.at_level(logging.WARNING, logger=voice_rung.log.name):
        client = TestClient(create_app(stub=True))
        for _ in range(3):
            assert client.get("/api/health").json()["capabilities"]["voice"] is False
    assert whisper_lines(caplog) == []


def _wav16k(path: Path, seconds: float = 1) -> bytes:
    """16 kHz mono PCM: transcribe() reads its length directly, so the note reaches the model check."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(seconds * 16000))
    return path.read_bytes()


def test_voice_notes_after_start_add_no_whisper_line(no_model, monkeypatch, caplog, tmp_path):
    """whisper-cli installed, model missing: start plus two voice notes say it exactly once (Codex @ 3edf83a)."""
    from fastapi.testclient import TestClient
    from secondlook.web.app import create_app

    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/opt/homebrew/bin/whisper-cli")
    note = _wav16k(tmp_path / "n.wav")
    with caplog.at_level(logging.WARNING, logger=voice_rung.log.name):
        client = TestClient(create_app(stub=True))
        for _ in range(2):
            r = client.post("/api/voice", files={"file": ("note.wav", note, "audio/wav")})
            assert r.status_code == 200, r.text
            assert [e["raw"]["kind"] for e in r.json()["evidence"]] == ["how_made", "transcript_unavailable"]
    assert len(whisper_lines(caplog)) == 1


def test_voice_cli_says_it_once_and_still_answers(no_model, monkeypatch, caplog, tmp_path, capsys):
    """The standalone secondlook-voice command has no server start, so it says the line itself, once."""
    import json

    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/opt/homebrew/bin/whisper-cli")
    _wav16k(tmp_path / "n.wav")
    with caplog.at_level(logging.WARNING, logger=voice_rung.log.name):
        assert voice_rung.main([str(tmp_path / "n.wav"), "--db", str(tmp_path / "none.sqlite")]) == 0
    assert len(whisper_lines(caplog)) == 1
    assert json.loads(capsys.readouterr().out)["answer"] == "cant_tell"
