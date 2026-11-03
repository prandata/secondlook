"""W10 desk, part 2 (DAT-850): the caption box and the Muse Glimmer investigator on the page.

The APIs are X1's (`caption` on /api/check, DAT-872) and M2's (/api/investigate, DAT-873); these
tests pin the page that reaches them and the one field the page reads to say "not set up".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from secondlook import glimmer as g  # noqa: E402
from secondlook import investigator as inv  # noqa: E402
from secondlook.rungs import context as context_rung  # noqa: E402
from secondlook.rungs import exif as exif_rung  # noqa: E402
from secondlook.web.app import MAX_CAPTION, STATIC, create_app  # noqa: E402

HTML = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
INV = (STATIC / "investigate.js").read_text()
CSS = (STATIC / "investigate.css").read_text()
README = (Path(__file__).resolve().parents[1] / "README.md").read_text()


def canon_jpeg(tmp_path) -> bytes:
    """An 8 x 8 JPEG whose EXIF says a Canon made it."""
    from PIL import Image

    ex = Image.Exif()
    ex[0x010F] = "Canon"            # Make
    ex[0x0110] = "Canon EOS R5"     # Model
    p = tmp_path / "canon.jpg"
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=ex)
    return p.read_bytes()


# ---- the caption box ----------------------------------------------------------------------------

def test_caption_box_is_labelled_and_described_and_in_the_check_form():
    check = HTML[HTML.index('<section id="check"'):HTML.index("</section>", HTML.index('<section id="check"'))]
    assert '<label for="caption">' in check and '<textarea id="caption"' in check
    assert 'aria-describedby="caption-hint caption-count caption-error"' in check
    # Keyboard order follows the page: the image drop first, then the caption, then "check again".
    assert check.index('id="file"') < check.index('id="caption"') < check.index('id="caption-again"')
    # No maxlength: a pasted caption over the limit is not cut silently; the error beside the box says so.
    box = re.search(r'<textarea id="caption"[^>]*>', check).group(0)
    assert "maxlength" not in box
    assert 'id="caption-error"' in check


def test_page_limit_matches_the_api_limit():
    assert MAX_CAPTION == 2000
    assert "const MAX_CAPTION = 2000;" in APP
    assert "2,000 characters" in HTML


def test_app_sends_the_caption_field_and_blocks_an_over_length_one():
    assert 'fd.append("caption", text)' in APP
    assert "if (!captionCheck())" in APP          # nothing is sent while the caption is over the limit
    assert 'caption.setAttribute("aria-invalid", "true")' in APP
    assert "if (/caption/i.test(d)) { captionProblem(d);" in APP   # a server refusal shows beside the box too
    assert 'context: "Caption context"' in APP


def test_a_caption_concern_comes_back_as_a_card(tmp_path):
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("context", context_rung.check)]))
    data = canon_jpeg(tmp_path)
    plain = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert not [e for e in plain["evidence"] if e["rung"] == "context"]
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")},
                       data={"caption": "Shot on iPhone 15 Pro."}).json()
    context = [e for e in body["evidence"] if e["rung"] == "context"]
    assert context and context[0]["raw"].get("contradiction") is True
    assert body["answer"] == "concerns"
    assert all(e["does_not_prove"].strip() for e in body["evidence"])


def test_an_over_length_caption_is_refused_with_a_message_naming_the_caption(tmp_path):
    client = TestClient(create_app(stub=False, rungs=[]))
    data = canon_jpeg(tmp_path)
    r = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}, data={"caption": "x" * 2001})
    assert r.status_code == 400 and re.search("caption", r.json()["detail"], re.I)
    ok = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}, data={"caption": "é" * 2000})
    assert ok.status_code == 200


# ---- the investigator panel ---------------------------------------------------------------------

def test_investigator_panel_sits_under_the_answer():
    result = HTML[HTML.index('<section id="result"'):]
    assert result.index('id="clues"') < result.index('id="investigate"')
    assert ">Investigate with Muse Glimmer (runs locally)</button>" in HTML
    assert 'id="inv-status" role="status" aria-live="polite"' in HTML
    assert 'id="inv-time" role="timer"' in HTML
    assert 'id="inv-answer" tabindex="-1"' in HTML
    # M2's explanation lines are numbered already; an <ol> would number them twice.
    assert '<ul class="inv-lines" id="inv-lines"></ul>' in HTML and ".inv-lines { list-style: none;" in CSS
    assert '<script src="/static/investigate.js"></script>' in HTML and 'href="/static/investigate.css"' in HTML


def test_the_local_line_stays_true():
    # The page starts by saying it is looking, with no live button; investigate.js says Glimmer runs here only
    # when /api/health's capabilities.glimmer is true (judge persona test, 30 Oct).
    line = re.sub(r"\s+", " ", re.search(r'<p class="inv-local" id="inv-local">(.*?)</p>', HTML, re.S).group(1))
    assert line == "Looking for Muse Glimmer on this computer."
    assert '<button type="button" id="inv-go" aria-describedby="inv-local" hidden>' in HTML
    live = re.search(r'const LOCAL_LIVE = ((?:"[^"]*"\s*\+?\s*)+);', INV).group(1)
    assert "".join(re.findall(r'"([^"]*)"', live)) == (
        "Muse Glimmer runs on this computer. Second Look reaches it only at a loopback address "
        "(127.0.0.1), so the image and caption you send it stay on this computer.")
    # What makes it true: the client refuses any URL that is not plain http to a literal loopback address.
    assert g.loopback_url(g.DEFAULT_URL) == g.DEFAULT_URL
    for url in ("http://example.com:8081", "https://127.0.0.1:8081", "http://10.0.0.1:8081", "http://localhost:8081"):
        with pytest.raises(ValueError):
            g.loopback_url(url)


def test_setup_commands_on_the_page_are_the_readme_ones():
    for cmd in ("scripts/fetch_glimmer.sh", "scripts/glimmer_server.sh start"):
        assert cmd in HTML and cmd in README
    # Meta's two pinned files are 16,756,683,904 and 1,400,328,928 bytes (docs/M1-GLIMMER.md): about 18 GB.
    assert "about 18 GB" in HTML and "40 GB free" in HTML


def test_investigate_js_posts_the_same_file_and_caption_and_writes_text_only():
    assert 'fetch("/api/investigate", { method: "POST", body: fd })' in INV
    assert 'fd.append("file", current.file, current.file.name)' in INV
    assert 'fd.append("caption", current.caption)' in INV
    assert "innerHTML" not in INV and "insertAdjacentHTML" not in INV
    # The lines shown are M2's: template lines, step results, readings under M2's label.
    for field in ("explanation_lines", "explanation_label", "st.result", "image_text", "r.facts", "r.label"):
        assert field in INV
    # Elapsed seconds while it runs, from the smoke run's measured range and M2's own limit.
    assert "Running for " in INV and "53 to 126 seconds" in INV and "300 seconds" in INV
    assert inv.MAX_SECONDS == 300.0
    # The button keeps focus while it runs; stale replies are dropped.
    assert 'go.setAttribute("aria-disabled", "true")' in INV and "if (id !== runId) return;" in INV


def test_investigate_js_says_plainly_when_glimmer_is_not_set_up_or_the_stub_runs():
    assert 'res.status === 409' in INV and 'body.glimmer === "unavailable"' in INV
    assert "Muse Glimmer is not set up on this computer" in INV
    assert "This page is running the demo stub." in INV
    # On the stub the box says the deep check is off, with no live button and no health request of its own.
    check = INV.split("async function deepCheck(of, id) {", 1)[1].split("async function offerRecording", 1)[0]
    assert 'if (of && of.engine === "stub") { local.textContent = LOCAL_STUB; return; }' in check
    assert check.index("LOCAL_STUB") < check.index('fetch("/api/health"')


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_investigate_route_says_unavailable_when_no_server_answers(monkeypatch):
    monkeypatch.setenv(g.URL_ENV, "http://127.0.0.1:9")   # discard port: nothing listens
    body = TestClient(create_app(stub=False, rungs=[])).post(
        "/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")}).json()
    assert body["glimmer"] == "unavailable" and body["problems"][0].startswith(inv.UNAVAILABLE)


def test_investigate_route_says_ran_when_the_model_answered():
    def fake(path, caption):
        return inv.DeskNote(file="a.jpg", answer="cant_tell", summary="", evidence=[], steps=[], explanation="x")

    body = TestClient(create_app(stub=False, rungs=[], investigator=fake)).post(
        "/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")}).json()
    assert body["glimmer"] == "ran"


def test_investigate_on_the_stub_is_409_with_its_reason():
    r = TestClient(create_app(stub=True)).post("/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 409 and "stub" in r.json()["detail"]


def test_focus_and_contrast_hooks():
    assert "--rust-text: #9E4526;" in (STATIC / "app.css").read_text() and "color: var(--rust-text)" in CSS
    for sel in ("#caption:focus-visible", "#caption-again:focus-visible", "#inv-go:focus-visible", ".inv-answer:focus-visible"):
        assert sel in CSS
    assert 'prefers-reduced-motion: reduce' in INV
    assert "font-family" not in CSS.replace("font-family: ui-monospace", "")   # no new web fonts
    assert "@font-face" not in CSS


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("fg,bg", [
    ("#2B2F33", "#FBF9F3"), ("#5F6367", "#FBF9F3"), ("#B4532F", "#FBF9F3"),   # ink, soft, rust on paper
    ("#2B2F33", "#EFE9DB"), ("#5F6367", "#F4F0E6"), ("#9E4526", "#F4F0E6"),   # setup box; caption hint and error on cream
    ("#F4F0E6", "#2B2F33"), ("#2B2F33", "#FFFFFF"), ("#B4532F", "#FFFFFF"),   # button and code; textarea; rust on a card
])
def test_text_colours_meet_wcag_aa(fg, bg):
    assert _contrast(fg, bg) >= 4.5, (fg, bg, round(_contrast(fg, bg), 2))
