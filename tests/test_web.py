"""Tests for the S5 local page (DAT-850), ported into secondlook/web/."""
import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from secondlook.types import Evidence
from secondlook.web import stub
from secondlook.web.app import CSP, MAX_BYTES, MAX_NAME, MAX_REQUEST, STATIC, create_app, safe_name

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)
# PLAN.md "Claims allowed": never "detects AI", "accurate" or "this is real". No em dashes in public copy.
# The product never calls an image real or authentic in any form, so the bare words are banned too (W3 review, a reviewer).
BANNED = [r"detects? ai", r"\baccura", r"this is real", r"\breal\b", r"\bauthentic", r"\bgenuine", "—"]
# Every file the page loads, W10 desk files (parts 1 and 2) included, so every banned-word and no-external-call check covers all page copy.
PAGE_FILES = ["index.html", "app.css", "app.js", "desk.css", "desk.js", "investigate.css", "investigate.js"]

stub_client = TestClient(create_app(stub=True))
empty_ladder = TestClient(create_app(stub=False, rungs=[]))


def post(client, name, data=PNG):
    return client.post("/api/check", files={"file": (name, data, "image/png")})


# --- stub engine, only behind the flag, always marked as a demo -------------------------------

@pytest.mark.parametrize(
    "name,answer",
    [("demo-signed-ai.png", "signed_ai"), ("demo-camera.png", "signed_camera"),
     ("demo-concerns.png", "concerns"), ("holiday.png", "cant_tell")],
)
def test_stub_answers_are_marked_demo_inside_the_result(name, answer):
    r = post(stub_client, name)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == answer
    assert body["engine"] == "stub" and body["demo"] is True
    assert body["summary"].startswith(stub.DEMO_MARK)
    assert body["version"] and body["version"] != "stub"
    for ev in body["evidence"]:
        assert ev["raw"]["stub"] is True
        for key in ("rung", "finding", "proves", "does_not_prove", "strength"):
            assert ev[key].strip(), f"{key} empty on {ev['rung']}"


def test_default_app_uses_the_real_ladder(monkeypatch):
    monkeypatch.delenv("SECONDLOOK_STUB", raising=False)
    h = TestClient(create_app()).get("/api/health").json()
    assert h["engine"] == "ladder"
    monkeypatch.setenv("SECONDLOOK_STUB", "1")
    assert TestClient(create_app()).get("/api/health").json()["engine"] == "stub"


# --- real ladder.run() ------------------------------------------------------------------------

def test_real_ladder_with_no_findings_is_cant_tell_and_not_demo():
    body = post(empty_ladder, "holiday.png").json()
    assert body["answer"] == "cant_tell"
    assert body["engine"] == "ladder" and body["demo"] is False
    assert body["rungs"] == []


def test_real_ladder_runs_the_given_rungs():
    seen = []

    def c2pa(path):
        seen.append(Path(path).read_bytes() == PNG)
        return [Evidence(rung="c2pa", finding="C2PA manifest signed by Example (test).", proves="p",
                         does_not_prove="d", strength="signed",
                         raw={"valid": True, "trusted": True, "source_class": "ai"})]

    client = TestClient(create_app(stub=False, rungs=[("c2pa", c2pa)]))
    body = post(client, "a.png").json()
    assert seen == [True], "the rung read the uploaded bytes"
    assert body["answer"] == "signed_ai" and body["demo"] is False and body["rungs"] == ["c2pa"]


def test_a_stub_card_from_the_ladder_still_marks_the_result_demo():
    def fake(path):
        return [Evidence(rung="exif", finding="f", proves="p", does_not_prove="d",
                         strength="metadata", raw={"stub": True})]

    body = post(TestClient(create_app(stub=False, rungs=[("exif", fake)])), "a.png").json()
    assert body["demo"] is True


def test_upload_dir_is_deleted_after_the_check():
    paths = []
    client = TestClient(create_app(stub=False, rungs=[("exif", lambda p: paths.append(p) or [])]))
    assert post(client, "a.png").status_code == 200
    assert paths and not Path(paths[0]).exists()


# --- F1: long file names keep their suffix ----------------------------------------------------

@pytest.mark.parametrize("stem_len", [76, 77, 116, 300])
def test_long_names_are_accepted(stem_len):
    name = "a" * stem_len + ".png"
    r = post(empty_ladder, name)
    assert r.status_code == 200, (len(name), r.text)
    assert safe_name(name).endswith(".png") and len(safe_name(name)) <= MAX_NAME


def test_81_character_name_returns_200():
    name = "w" * 77 + ".png"
    assert len(name) == 81
    assert post(empty_ladder, name).status_code == 200


@pytest.mark.parametrize(
    "name,status",
    [("../../../../etc/passwd.png", 200), ("..\\..\\win camera.png", 200), ("evil\x00.png", 200),
     ("PHOTO.JPG", 200), ("..", 415), (".png", 415), ("x.png.exe", 415), ("notes.txt", 415), ("a.svg", 415)],
)
def test_path_tricks(name, status):
    assert post(empty_ladder, name).status_code == status
    if status == 200:
        assert "/" not in safe_name(name) and "\\" not in safe_name(name)


def test_rejects_empty():
    assert post(empty_ladder, "empty.png", b"").status_code == 400


# --- size limit, applied before the body is read ----------------------------------------------

def _raw_post(headers):
    """Call the ASGI app directly; fail the test if it tries to read the body."""
    app = create_app(stub=False, rungs=[])
    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/check", "headers": headers,
             "query_string": b"", "http_version": "1.1", "scheme": "http", "root_path": "",
             "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1)}
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    return start["status"], dict(start["headers"])


def test_oversize_content_length_is_refused_before_the_body_is_read():
    status, headers = _raw_post([(b"content-length", str(MAX_REQUEST + 1).encode())])
    assert status == 413
    assert headers[b"content-security-policy"] == CSP.encode()


def test_missing_content_length_is_refused():
    assert _raw_post([])[0] == 411


def test_exactly_25_mb_passes_and_one_byte_more_fails():
    assert post(empty_ladder, "big.png", b"\0" * MAX_BYTES).status_code == 200
    assert post(empty_ladder, "big.png", b"\0" * (MAX_BYTES + 1)).status_code == 413


# --- CSP and headers --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/api/health", "/static/app.js", "/static/app.css"])
def test_security_headers_on_every_response(path):
    r = stub_client.get(path)
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in CSP and "https:" not in CSP and "unsafe-inline" not in CSP


def test_security_headers_on_errors_too():
    r = post(stub_client, "notes.txt", b"x")
    assert r.status_code == 415 and r.headers["content-security-policy"] == CSP


# --- page -------------------------------------------------------------------------------------

def test_page_has_badge_footer_and_cant_tell():
    html = stub_client.get("/").text
    js = (STATIC / "app.js").read_text()
    assert "In development" in html and "Not affiliated with Meta" in html
    assert "Can't tell" in js
    # One list, one row per clue, with what it proves and what it does not prove side by side (W11).
    assert "What we found" in html and '"Proves"' in js and '"Does not prove"' in js


def test_stub_banner_starts_hidden_and_shows_on_a_stub_answer():
    # A262 (claims-handler snag 7): the banner starts hidden, so it never flashes before /api/health answers.
    # A270 and A273 (Codex FAILs on PR #55): it shows exactly when the answer on screen is a stub one, whatever
    # health said or failed to say, with no latch; with no answer on screen it follows health. Every stub answer
    # also carries its own "Demo answer" tag. The page tests in test_a262_page_polish.py run each sequence.
    html = (STATIC / "index.html").read_text()
    banner = re.search(r'<div[^>]*id="stub"[^>]*>', html).group(0)
    assert "hidden" in banner, "banner must start hidden"
    js = (STATIC / "app.js").read_text()
    assert '$("stub").hidden = !(answerStub === null ? healthStub : answerStub);' in js
    assert 'answerStub = r.engine !== "ladder";' in js
    assert "stubAnswered" not in js, "no latch"
    assert '!(r.engine === "ladder" && r.demo === false)' in js
    assert 'id="demo-tag"' in html


def test_empty_evidence_shows_a_limit_card():
    js = (STATIC / "app.js").read_text()
    assert "render(r.evidence.length ? r.evidence : [limitCard(rungs)], r.concern_cards)" in js
    assert '"missing"' in js, "a blank does_not_prove is shown as an error"


def test_page_makes_no_external_calls():
    for f in PAGE_FILES:
        text = (STATIC / f).read_text()
        assert not re.search(r"https?://", text), f
        assert not re.search(r"""["'(]\s*//""", text), f
        assert "@import" not in text, f


def test_font_ships_with_its_ofl_licence():
    assert (STATIC / "fonts" / "PlayfairDisplay.ttf").stat().st_size > 100_000
    ofl = (STATIC / "fonts" / "OFL.txt").read_text()
    assert "SIL Open Font License, Version 1.1" in ofl and "Playfair Display" in ofl
    assert stub_client.get("/static/fonts/PlayfairDisplay.ttf").status_code == 200
    assert 'url("fonts/PlayfairDisplay.ttf")' in (STATIC / "app.css").read_text()


def test_copy_never_makes_banned_claims():
    texts = [(STATIC / f).read_text() for f in PAGE_FILES] + [Path(stub.__file__).read_text()]
    for r in (stub.check(n) for n in ("a-signed-ai.png", "a-camera.png", "a-concern.png", "a.png")):
        texts.append(r.summary)
        texts += [e.finding + e.proves + e.does_not_prove for e in r.evidence]
    for t in texts:
        for pat in BANNED:
            assert not re.search(pat, t, re.I), f"banned pattern {pat!r} found"


# --- W3 polish (DAT-850): answer meaning, grouped evidence, trust list, samples, a11y ----------

from secondlook.web import samples as sample_catalogue  # noqa: E402


def test_every_answer_has_a_big_label_and_a_one_line_meaning():
    js = (STATIC / "app.js").read_text()
    block = js[js.index("const ANSWERS"):js.index("const RUNGS")]
    for answer in ("signed_ai", "signed_camera", "concerns", "cant_tell"):
        entry = re.search(answer + r":\s*\{(.*?)\}", block, re.S).group(1)
        assert "label:" in entry and "meaning:" in entry, answer
    assert re.search(r"\.answer\s*\{[^}]*font:\s*800 clamp\(38px", (STATIC / "app.css").read_text())


def test_signed_card_shows_its_trust_list_source():
    js = (STATIC / "app.js").read_text()
    assert "raw.trust_list" in js or "raw = ev.raw || {}, t = raw.trust_list" in js
    for key in ("t.name", "t.date", "t.source", "t.sha256"):
        assert key in js
    assert "the signer is on this list" in js and "the signer is not on this list" in js


def test_page_keyboard_and_screen_reader_basics():
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()
    # The file input stays in the tab order (visually hidden, not display:none) and shows focus on the drop zone.
    assert re.search(r'<input id="file" class="visually-hidden"', html)
    assert "#file { display: none" not in css and "#file:focus-visible + #drop" in css
    assert ":focus-visible" in css and "prefers-reduced-motion" in css
    assert 'role="status" aria-live="polite"' in html
    assert 'id="answer" tabindex="-1"' in html and "ans.focus(" in (STATIC / "app.js").read_text()
    assert 'class="skip" href="#check"' in html and 'id="check"' in html
    assert '<html lang="en">' in html and 'name="viewport"' in html


def test_gallery_is_hidden_without_samples():
    assert empty_ladder.get("/api/samples").json() == {"samples": []}
    assert empty_ladder.get("/api/health").json()["samples"] == 0
    assert 'id="samples" aria-labelledby="samples-title" hidden' in (STATIC / "index.html").read_text()
    assert empty_ladder.post("/api/samples/01/check").status_code == 404


def _fake_samples(tmp_path, n=3):
    cat = []
    for i in range(1, n + 1):
        data = PNG + bytes([i])
        (tmp_path / f"s{i}.jpg").write_bytes(data)
        cat.append(sample_catalogue.Sample(f"0{i}", f"s{i}.jpg", f"Title {i}", "Kind", "Credit", "CC0",
                                           "source", __import__("hashlib").sha256(data).hexdigest()))
    return tuple(cat)


def test_samples_are_listed_only_when_the_sha256_matches(tmp_path):
    cat = _fake_samples(tmp_path)
    (tmp_path / "s2.jpg").write_bytes(b"tampered")
    found = sample_catalogue.available(tmp_path, cat)
    assert sorted(found) == ["01", "03"]


def test_catalogue_matches_the_n1_manifest():
    assert [s.id for s in sample_catalogue.CATALOGUE] == [f"0{i}" for i in range(1, 10)]
    for s in sample_catalogue.CATALOGUE:
        assert s.file.startswith(s.id + "-") and re.fullmatch(r"[0-9a-f]{64}", s.sha256)
        assert s.credit and s.licence and s.kind and "—" not in (s.title + s.kind + s.credit)


def test_sample_check_runs_the_ladder_on_the_file_and_names_its_credit(tmp_path, monkeypatch):
    cat = _fake_samples(tmp_path)
    monkeypatch.setattr(sample_catalogue, "CATALOGUE", cat)
    seen = []

    def exif(path):
        seen.append(path)
        return []

    client = TestClient(create_app(stub=False, rungs=[("exif", exif)], samples_dir=str(tmp_path)))
    listed = client.get("/api/samples").json()["samples"]
    assert [s["id"] for s in listed] == ["01", "02", "03"]
    assert all("path" not in s and s["image"] == f"/samples/{s['id']}/image" for s in listed)
    body = client.post("/api/samples/02/check").json()
    assert seen == [str(tmp_path / "s2.jpg")], "the original file is read in place"
    assert body["answer"] == "cant_tell" and body["demo"] is False
    assert body["sample"]["id"] == "02" and body["sample"]["credit"] == "Credit"
    img = client.get("/samples/02/image")
    assert img.status_code == 200 and img.headers["content-security-policy"] == CSP
    for bad in ("../02", "99", "02%2F..%2F01", "s2.jpg"):
        assert client.post(f"/api/samples/{bad}/check").status_code == 404, bad
        assert client.get(f"/samples/{bad}/image").status_code == 404, bad


# --- W3 fix round (DAT-850, a reviewer FAIL @ 5fb50b2): answer meanings match the ladder ---------

def _meaning(answer):
    js = (STATIC / "app.js").read_text()
    block = js[js.index("const ANSWERS"):js.index("const RUNGS")]
    entry = re.search(answer + r":\s*\{(.*?)\}", block, re.S).group(1)
    return re.search(r'meaning:\s*"([^"]*)"', entry).group(1)


def test_concerns_meaning_covers_a_signed_record_that_failed_its_checks():
    # Sample 02 today: a signed C2PA record whose credential expired. The ladder answers "concerns",
    # so the meaning under that label must not call it unsigned.
    def c2pa(path):
        return [Evidence(rung="c2pa", finding="C2PA manifest signed by Example (test); validation failed.",
                         proves="p", does_not_prove="d", strength="signed",
                         raw={"valid": False, "source_class": "camera"})]

    body = post(TestClient(create_app(stub=False, rungs=[("c2pa", c2pa)])), "a.png").json()
    assert body["answer"] == "concerns"
    meaning = _meaning("concerns")
    assert meaning == ("Clues worth a closer look: unsigned labels or signals, or a signed record that failed "
                       "its checks. A reason to check further, not a verdict.")
    assert not meaning.lower().startswith("unsigned")


def test_cant_tell_meaning_allows_a_valid_record_that_names_neither_ai_nor_camera():
    # A valid, trusted record that states neither AI nor a camera capture still gives "cant_tell".
    def c2pa(path):
        return [Evidence(rung="c2pa", finding="C2PA manifest signed by Example (test).", proves="p",
                         does_not_prove="d", strength="signed", raw={"valid": True, "trusted": True, "source_class": "other"})]

    body = post(TestClient(create_app(stub=False, rungs=[("c2pa", c2pa)])), "a.png").json()
    assert body["answer"] == "cant_tell"
    meaning = _meaning("cant_tell")
    assert "nothing signed" not in meaning.lower()
    assert meaning == "No signed record settles it, and no clue is strong enough. That is an honest answer, not a failure."


def test_signed_ai_meaning_covers_an_ai_edit_not_only_an_ai_made_image():
    # Sample 05 is a camera photo retouched with Firefly (compositeWithTrainedAlgorithmicMedia).
    meaning = _meaning("signed_ai")
    assert "an AI tool made it" not in meaning
    assert meaning == "A valid signed record in this file says AI was used to make or edit it."


def test_empty_result_limit_says_camera_or_ai_not_real_or_fake():
    line = "That the image was made by a camera or by AI."
    js = (STATIC / "app.js").read_text()
    limit_card = js[js.index("function limitCard"):js.index("function showExplanation")]
    assert 'does_not_prove: "' + line in limit_card
    assert stub.check("a.png").evidence[0].does_not_prove.startswith(line)
    for text in (limit_card, stub.check("a.png").evidence[0].does_not_prove):
        assert not re.search(r"\b(real|fake)\b", text, re.I)


def test_no_page_or_sample_text_calls_an_image_real_or_authentic():
    texts = [(STATIC / f).read_text() for f in PAGE_FILES]
    texts += [s.title + " " + s.kind + " " + s.credit for s in sample_catalogue.CATALOGUE]
    texts.append(post(empty_ladder, "a.png").text)
    for t in texts:
        assert not re.search(r"\b(real|authentic|genuine)\b", t, re.I), t[:200]


def test_favicon_is_served_and_linked_from_both_pages():
    """A249 (DAT-850, judge re-run 30 Oct): /favicon.ico was a 404. It is the brand mark from scripts/make_favicon.py:
    a cream "S" on the brand burnt orange, in the four sizes the pages' link names."""
    res = stub_client.get("/favicon.ico")
    assert res.status_code == 200 and res.headers["content-type"] == "image/x-icon"
    assert res.content == (STATIC / "favicon.ico").read_bytes() and res.content[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(res.content[4:6], "little") == 4, "16, 32, 48 and 64 px"
    for page in ("index.html", "batch.html"):
        assert '<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48 64x64">' in (STATIC / page).read_text()
    Image = pytest.importorskip("PIL.Image")
    import io
    icon = Image.open(io.BytesIO(res.content))
    assert icon.info["sizes"] == {(16, 16), (32, 32), (48, 48), (64, 64)}
    icon.size = (64, 64)
    px = icon.convert("RGB")
    assert px.getpixel((0, 0)) == (0xB4, 0x53, 0x2F), "the brand burnt orange, as app.css --rust"
    assert (0xF4, 0xF0, 0xE6) in {c for _, c in px.getcolors(64 * 64)}, "the cream glyph"
