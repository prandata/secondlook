"""Tests for the W10 desk page, part 1 (DAT-850): claim box, voice notes, capability-aware health."""
import asyncio
import json
import re
import sys
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from secondlook.rungs import claim as claim_rung
from secondlook.rungs import voice as voice_rung
from secondlook.web import desk
from secondlook.web.app import CSP, STATIC, create_app

# PLAN.md "Claims allowed": never "detects AI", "accurate" or "this is real". No em dashes in public copy.
BANNED = [r"detects? ai", r"\baccura", r"this is real", "—"]
DESK_FILES = ["index.html", "desk.js", "desk.css"]
CAPS = {"engine", "claims_index", "voice", "receipt", "glimmer", "batch", "recorded"}

COFFEE = "Drinking coffee causes cancer"


def _item(text, url, rating, checker="Example Checker", published="2024-03-01"):
    return {"claimReviewed": text, "url": url, "datePublished": published,
            "reviewRating": {"alternateName": rating},
            "author": {"name": checker, "url": url.rsplit("/", 1)[0]},
            "itemReviewed": {"author": {"name": "A social media post"}}}


@pytest.fixture(scope="module")
def index(tmp_path_factory):
    d = tmp_path_factory.mktemp("claims")
    feed = d / "feed.json"
    feed.write_text(json.dumps({"dateModified": "2026-10-28T01:15:35Z", "dataFeedElement": [
        {"item": [_item("Drinking coffee causes cancer", "https://www.politifact.com/fact/coffee-cancer",
                        "Mostly False: the studies say otherwise", checker="PolitiFact")]},
        {"item": [_item("The town hall clock was stolen in Brisbane", "https://blog.example-site.net/clock",
                        "Misleading")]},
    ]}))
    out = d / "claims.sqlite"
    claim_rung.build_index(feed, out, downloaded_at="2026-10-28T01:18:35Z", sha256="test")
    return out


@pytest.fixture
def with_index(index, monkeypatch):
    monkeypatch.setenv(claim_rung.DB_ENV, str(index))
    return index


@pytest.fixture
def no_index(tmp_path, monkeypatch):
    monkeypatch.setenv(claim_rung.DB_ENV, str(tmp_path / "not-built.sqlite"))


@pytest.fixture(autouse=True)
def glimmer_down(monkeypatch):
    """No Glimmer server for these tests, whatever runs on this computer (tests/test_w11_stage2.py covers it)."""
    monkeypatch.setattr(desk, "glimmer", lambda: {"ready": False, "reason": "unavailable"})


@pytest.fixture
def client():
    return TestClient(create_app(stub=False, rungs=[]))


def claim(client, text):
    return client.post("/api/claim", json={"text": text})


def voice(client, name, data):
    return client.post("/api/voice", files={"file": (name, data, "audio/wav")})


def _wav(path: Path, seconds: float) -> bytes:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(seconds * 16000))
    return path.read_bytes()


def _every_card_has_its_limit(body):
    assert body["evidence"], "every answer carries at least one card"
    for ev in body["evidence"]:
        assert ev["does_not_prove"].strip() and ev["proves"].strip() and ev["finding"].strip()


# --- POST /api/claim, index present ------------------------------------------------------------

def test_claim_returns_the_publishers_rating_word_for_word(client, with_index):
    r = claim(client, COFFEE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "claim" and body["status"] == "found" and body["demo"] is False
    _every_card_has_its_limit(body)
    raw = body["evidence"][0]["raw"]
    assert raw["rating"] == "Mostly False: the studies say otherwise"   # verbatim, not a verdict of ours
    assert raw["publisher"] == "politifact.com" and raw["is_listed_fact_checker"] is True
    assert raw["date_published"] == "2024-03-01"
    assert raw["review_url"] == "https://www.politifact.com/fact/coffee-cancer"
    assert "no true or false verdict" in body["note"]
    assert body["feed"]["credit"] == desk.FEED_CREDIT and "CC BY" in body["feed"]["licence"]


def test_unlisted_site_is_never_called_a_fact_checker(client, with_index):
    ev = claim(client, "The town hall clock was stolen in Brisbane").json()["evidence"][0]
    assert ev["raw"]["is_listed_fact_checker"] is False
    assert ev["finding"].startswith("Published by blog.example-site.net")
    assert "fact-checker" not in ev["finding"]


def test_no_match_is_the_default_and_still_carries_its_limit(client, with_index):
    body = claim(client, "The moon is made of green cheese and sold by the kilo").json()
    assert body["status"] == "none" and body["message"] == claim_rung.NO_MATCH_MESSAGE
    assert len(body["evidence"]) == 1
    ev = body["evidence"][0]
    assert ev["finding"] == "No existing fact-check found." and ev["raw"]["kind"] == "no_match"
    assert "the 2 published reviews" in ev["proves"]
    assert "true or false" in ev["does_not_prove"]


# --- POST /api/claim, index absent or unreadable -----------------------------------------------

def test_missing_index_is_a_clear_state_not_an_error_page(client, no_index):
    r = claim(client, COFFEE)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["status"] == "not_installed" and body["message"] == desk.NOT_INSTALLED
    assert "README" in body["hint"]
    _every_card_has_its_limit(body)
    assert body["evidence"][0]["raw"]["kind"] == "not_installed"
    assert body["message"] != claim_rung.NO_MATCH_MESSAGE, "a missing index never reads as 'no fact-check'"


def test_corrupt_index_says_unavailable_not_no_match(client, tmp_path, monkeypatch):
    bad = tmp_path / "claims.sqlite"
    bad.write_bytes(b"not a database")
    monkeypatch.setenv(claim_rung.DB_ENV, str(bad))
    body = claim(client, COFFEE).json()
    assert body["status"] == "unavailable"
    _every_card_has_its_limit(body)
    assert client.get("/api/health").json()["capabilities"]["claims_index"] is False


@pytest.mark.parametrize("text,status", [("", 400), ("   \n ", 400), ("x " * 600, 413)])
def test_claim_input_limits(client, with_index, text, status):
    assert claim(client, text).status_code == status


def test_claim_of_exactly_the_limit_is_read(client, with_index):
    assert claim(client, "a" * desk.CLAIM_MAX_CHARS).status_code == 200


# --- POST /api/voice ---------------------------------------------------------------------------

def _fake_transcript(text):
    return voice_rung.Transcript(text=text, segments=[voice_rung.Segment(0, 4000, text)], model="small.en",
                                 model_sha256=voice_rung.PINNED_MODELS["small.en"].sha256,
                                 engine="whisper.cpp (test)", seconds=4.0)


def test_voice_note_gives_transcript_then_receipts(client, with_index, tmp_path, monkeypatch):
    seen = []

    def fake(path, model=voice_rung.DEFAULT_MODEL):
        seen.append(Path(path))
        return _fake_transcript("I heard that drinking coffee causes cancer. Please forward this.")

    monkeypatch.setattr(voice_rung, "transcribe", fake)
    r = voice(client, "note.wav", _wav(tmp_path / "n.wav", 1))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "voice" and body["answer"] == "cant_tell" and body["demo"] is False
    _every_card_has_its_limit(body)
    kinds = [e["raw"].get("kind") for e in body["evidence"]]
    assert kinds[:2] == ["how_made", "transcript"] and "claim" in kinds
    match = next(e for e in body["evidence"] if e["raw"].get("kind") == "claim")
    assert match["raw"]["rating"] == "Mostly False: the studies say otherwise"
    assert match["raw"]["heard"]["sentence"] == "Drinking coffee causes cancer."
    assert body["feed"]["credit"] == desk.FEED_CREDIT
    assert seen and not seen[0].exists(), "the upload is deleted after the check"


def test_voice_without_index_says_lookup_unavailable(client, no_index, tmp_path, monkeypatch):
    monkeypatch.setattr(voice_rung, "transcribe",
                        lambda p, model=voice_rung.DEFAULT_MODEL: _fake_transcript("Drinking coffee causes cancer."))
    body = voice(client, "note.wav", _wav(tmp_path / "n.wav", 1)).json()
    assert body["evidence"][-1]["finding"] == claim_rung.UNAVAILABLE_MESSAGE
    assert body["feed"] == {}


def test_over_length_note_gets_w9s_card_and_is_never_transcribed(client, with_index, tmp_path, monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("whisper ran on an over-length note")

    monkeypatch.setattr(voice_rung, "_whisper_cli", must_not_run)
    body = voice(client, "long.wav", _wav(tmp_path / "long.wav", voice_rung.MAX_SECONDS + 1)).json()
    assert body["answer"] == "cant_tell"
    card = body["evidence"][-1]
    assert card["raw"]["kind"] == "too_long" and card["finding"] == voice_rung.TOO_LONG
    assert card["raw"]["limit_seconds"] == 600
    _every_card_has_its_limit(body)


@pytest.mark.parametrize("name,data,status", [("notes.txt", b"x", 415), ("a.png", b"x", 415),
                                             ("../../x.exe", b"x", 415), ("empty.m4a", b"", 400)])
def test_voice_upload_checks(client, no_index, name, data, status):
    r = voice(client, name, data)
    assert r.status_code == status and r.headers["content-security-policy"] == CSP


def test_voice_limits_are_w9s():
    assert desk.VOICE_MAX_BYTES == voice_rung.MAX_BYTES == 50 * 1024 * 1024
    assert voice_rung.MAX_SECONDS == 600


def _raw_post(path, headers):
    """Call the ASGI app directly; fail the test if it tries to read the body."""
    app = create_app(stub=False, rungs=[])
    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers, "query_string": b"",
             "http_version": "1.1", "scheme": "http", "root_path": "", "server": ("127.0.0.1", 8765),
             "client": ("127.0.0.1", 1)}
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], json.loads(sent[1]["body"])["detail"]


@pytest.mark.parametrize("path,limit", [("/api/voice", desk.VOICE_MAX_REQUEST), ("/api/claim", desk.CLAIM_MAX_REQUEST)])
def test_oversize_bodies_are_refused_before_they_are_read(path, limit):
    status, detail = _raw_post(path, [(b"content-length", str(limit + 1).encode())])
    assert status == 413 and detail
    assert _raw_post(path, [])[0] == 411


# --- GET /api/health capabilities ----------------------------------------------------------------

def test_health_lists_capabilities(client, with_index, monkeypatch):
    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/opt/homebrew/Cellar/whisper-cpp/1.8.4/bin/whisper-cli")
    monkeypatch.setattr(voice_rung, "model_path", lambda name=voice_rung.DEFAULT_MODEL, warn=True: Path("/models/m.bin"))
    h = client.get("/api/health").json()
    assert set(h["capabilities"]) == CAPS
    assert h["capabilities"] == {"engine": False, "claims_index": True, "voice": True,
                                 "receipt": True, "glimmer": False, "batch": True,
                                 "recorded": True}  # the committed samples/recorded/ (DAT-850)
    assert h["details"]["claims_index"]["rows"] == "2"
    assert h["details"]["voice"]["transcriber"] == "whisper.cpp 1.8.4"
    assert str(Path.home()) not in json.dumps(h), "no file paths in health"


def test_health_reports_what_is_missing(client, no_index, monkeypatch):
    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: None)
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["claims_index"] is False and caps["voice"] is False
    assert caps["glimmer"] is False


def test_health_reports_receipt_where_the_receipt_route_exists(client, no_index):
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["receipt"] is True
    assert {"/api/receipt"} <= {r.path for r in client.app.routes}, "health says receipt: the route must be there"


@pytest.mark.parametrize("missing", desk.RECEIPT_MODULES)
def test_health_reports_receipt_false_when_an_import_fails(client, no_index, monkeypatch, missing):
    # None in sys.modules makes that import raise ImportError: the failure is injected, nothing is uninstalled.
    monkeypatch.setitem(sys.modules, missing, None)
    caps = client.get("/api/health").json()["capabilities"]
    assert caps["receipt"] is False
    assert caps["engine"] is False and caps["batch"] is True, "only the receipt flag moves"


def test_voice_needs_the_pinned_model_too(client, no_index, monkeypatch):
    monkeypatch.setattr(voice_rung, "_whisper_cli", lambda: "/usr/local/bin/whisper-cli")
    monkeypatch.setattr(voice_rung, "model_path", lambda name=voice_rung.DEFAULT_MODEL, warn=True: None)
    assert client.get("/api/health").json()["capabilities"]["voice"] is False


def test_engine_capability_follows_the_rungs(no_index):
    one = TestClient(create_app(stub=False, rungs=[("exif", lambda p: [])]))
    assert one.get("/api/health").json()["capabilities"]["engine"] is True
    assert TestClient(create_app(stub=True)).get("/api/health").json()["capabilities"]["engine"] is True


# --- the page ----------------------------------------------------------------------------------

def test_panels_start_hidden_and_are_shown_from_health():
    html = (STATIC / "index.html").read_text()
    for pid in ("claim-panel", "voice-panel"):
        tag = re.search(rf'<section[^>]*id="{pid}"[^>]*>', html).group(0)
        assert "hidden" in tag, f"{pid} must start hidden (fail closed)"
    js = (STATIC / "desk.js").read_text()
    assert '$("claim-panel").hidden = !caps.claims_index' in js
    assert '$("voice-panel").hidden = !caps.voice' in js
    assert '<script src="/static/desk.js"></script>' in html and 'href="/static/desk.css"' in html


def test_page_labels_model_and_publisher_text_and_shows_every_limit():
    js = (STATIC / "desk.js").read_text()
    assert "Machine transcript, made on this computer" in js
    assert "Heard in the machine transcript" in js
    assert "Rating, in the publisher's own words" in js
    assert '"Does not prove"' in js and '"missing"' in js
    assert "/^https?:\\/\\//i.test(u)" in js, "only http(s) review links become links"
    assert 'rel = "noopener noreferrer"' in js


def test_desk_controls_are_labelled_for_keyboard_and_screen_readers():
    html = (STATIC / "index.html").read_text()
    assert '<label for="claim-text">' in html and 'id="voice-drop" for="voice-file"' in html
    assert re.search(r'id="desk-status" role="status" aria-live="polite"', html)
    assert 'id="desk-answer" tabindex="-1"' in html
    js = (STATIC / "desk.js").read_text()
    assert "aria-busy" in js and "id !== runId" in js
    # The answer's scroll moved to app.js (the answer bar, W11), which keeps the reduced-motion rule.
    assert 'new CustomEvent("secondlook:answered"' in js and "prefers-reduced-motion" in (STATIC / "app.js").read_text()


def test_desk_page_makes_no_external_calls():
    for f in DESK_FILES:
        text = (STATIC / f).read_text()
        assert not re.search(r"https?://", text), f
        assert "@import" not in text, f


def test_new_copy_never_makes_banned_claims(index, tmp_path, monkeypatch):
    texts = [(STATIC / f).read_text() for f in DESK_FILES] + [Path(desk.__file__).read_text()]
    monkeypatch.setenv(claim_rung.DB_ENV, str(index))
    for q in (COFFEE, "The moon is made of green cheese and sold by the kilo"):
        texts.append(json.dumps(desk.check_claim(q), ensure_ascii=False))
    monkeypatch.setenv(claim_rung.DB_ENV, str(tmp_path / "none.sqlite"))
    texts.append(json.dumps(desk.check_claim(COFFEE), ensure_ascii=False))
    texts += [desk.NOT_INSTALLED, desk.INSTALL_HINT, desk.FEED_CREDIT]
    for t in texts:
        for pat in BANNED:
            assert not re.search(pat, t, re.I), f"banned pattern {pat!r} found"
