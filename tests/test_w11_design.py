"""W11 design pass, stage 1 (DAT-850): the six MUST findings from the design check of PR #20.

The answer comes first (the inputs fold into one bar), a published fact-check that matched gets a strip
under the headline, the evidence is one list with "Proves" and "Does not prove" side by side, the concern
headline is worded by count, the page counts its checks once, and the image drop is the hero.
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from secondlook import ladder  # noqa: E402
from secondlook import rungs as rung_registry  # noqa: E402
from secondlook.rungs import context as context_rung  # noqa: E402
from secondlook.rungs import exif as exif_rung  # noqa: E402
from secondlook.types import Evidence  # noqa: E402
from secondlook.web.app import STATIC, create_app  # noqa: E402

HTML = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
DESK = (STATIC / "desk.js").read_text()
INV = (STATIC / "investigate.js").read_text()
CSS = (STATIC / "app.css").read_text()
DESK_CSS = (STATIC / "desk.css").read_text()


def canon_jpeg(tmp_path) -> bytes:
    from PIL import Image

    ex = Image.Exif()
    ex[0x010F] = "Canon"
    ex[0x0110] = "Canon EOS R5"
    p = tmp_path / "canon.jpg"
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=ex)
    return p.read_bytes()


# ---- 1. the answer first ------------------------------------------------------------------------

def test_inputs_fold_into_one_bar_once_an_answer_is_on_screen():
    bar = re.search(r'<div class="again-bar" id="again-bar" hidden>.*?</div>', HTML, re.S).group(0)
    assert '<button type="button" id="again-go">Check another</button>' in bar
    intake = HTML[HTML.index('<div class="intake" id="intake">'):HTML.index('<p id="answer-status"')]
    for part in ('id="drop"', 'id="caption"', 'id="claim-panel"', 'id="voice-panel"', 'id="samples"'):
        assert part in intake, part
    assert HTML.index('id="again-bar"') < HTML.index('id="intake"') < HTML.index('id="result"')
    assert "intake.hidden = true;" in APP and "bar.hidden = false;" in APP
    assert 'collapse(preview.bar || preview.alt);' in APP
    assert 'bar: file.name + (text ? " \\u00b7 with its caption" : "")' in APP
    # Claim and voice answers fold the inputs the same way.
    assert 'new CustomEvent("secondlook:answered"' in DESK
    assert 'document.addEventListener("secondlook:answered"' in APP
    assert 'block: "start"' in APP and "prefers-reduced-motion: reduce" in APP


def test_claim_and_voice_sit_in_a_secondary_row_under_the_image():
    assert HTML.index('id="drop"') < HTML.index('id="more-title"') < HTML.index('id="claim-panel"')
    assert "Or check something else" in HTML
    assert '$("more-title").hidden = !(caps.claims_index || caps.voice);' in DESK


# ---- 2. the published fact-check strip ----------------------------------------------------------

def test_fact_check_strip_sits_under_the_headline_and_keeps_its_limit():
    result = HTML[HTML.index('<section id="result"'):]
    assert result.index('id="answer"') < result.index('id="factcheck"') < result.index('id="clues"')
    strip = re.search(r'<section class="factcheck" id="factcheck" hidden.*?</section>', HTML, re.S).group(0)
    assert "Published fact-check found" in strip and "Does not prove" in strip and 'id="fc-limit"' in strip
    # The rating word for word, who, the date and a real link; only http(s) becomes a link.
    assert "Rating in the publisher's own words: " in APP
    assert 'raw.rating ? quoted(raw.rating)' in APP
    assert "/^https?:\\/\\//i.test(u)" in APP and 'a.rel = "noopener noreferrer";' in APP
    assert '"Missing: this card did not say what it does not prove. Treat it as unchecked."' in APP
    # From the answer's own cards and from the deep check.
    assert "showFactCheck(factChecks(r.evidence));" in APP
    assert 'new CustomEvent("secondlook:deep-evidence"' in INV
    # The link sits on its own line with a 44 px target (design review of PR #48, N1).
    assert re.search(r"\.fc-read \{[^}]*min-height: 44px", CSS) and "overflow-wrap: anywhere" in CSS


# ---- 3. one list, one row per clue --------------------------------------------------------------

def test_one_list_with_proves_and_does_not_prove_side_by_side():
    for gone in ('id="g-proves"', 'id="g-limits"', 'id="found"', 'id="proves"', 'id="limits"'):
        assert gone not in HTML, gone
    assert '<ul class="clues" id="clues"></ul>' in HTML
    assert re.search(r'<details class="quiet" id="quiet" hidden>\s*<summary id="quiet-title">', HTML)
    assert '"Checked, nothing found (" + quiet.length + ")"' in APP
    assert '(concern.has(i) ? first : (ev.strength && ev.strength !== "none") ? shown : quiet)' in APP
    assert ".clue .pd { margin: 10px 0 0; display: grid; grid-template-columns: 1fr 1fr;" in CSS


# ---- 4. the concern headline --------------------------------------------------------------------

def test_concern_headline_is_worded_by_count_and_not_rust():
    assert "concernLabel(Array.isArray(r.concern_cards) ? r.concern_cards.length : 0)" in APP
    assert '(n === 1 ? " reason" : " reasons") + " to look closer"' in APP
    assert '"One"' in APP and "Concerns found" not in APP
    assert ".answer.signed_camera, .answer.cant_tell, .answer.concerns { color: var(--ink); }" in CSS


def test_ladder_summary_is_pluralised():
    one = Evidence(rung="context", strength="metadata", finding="f", proves="p", does_not_prove="d",
                   raw={"contradiction": True})
    two = Evidence(rung="context", strength="metadata", finding="g", proves="p", does_not_prove="d",
                   raw={"contradiction": True})
    assert ladder.decide([one])[1].startswith("1 concern found;")
    assert ladder.decide([one, two])[1].startswith("2 concerns found;")
    assert "concern(s)" not in ladder.decide([one])[1]


def test_check_reports_which_cards_are_concerns(tmp_path):
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("context", context_rung.check)]))
    data = canon_jpeg(tmp_path)
    body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")},
                       data={"caption": "Shot on iPhone 15 Pro."}).json()
    assert body["answer"] == "concerns"
    idx = body["concern_cards"]
    assert len(idx) == 1 and body["evidence"][idx[0]]["rung"] == "context"
    assert body["summary"].startswith("1 concern found;")


# ---- 5. one count, one score --------------------------------------------------------------------

def test_the_check_count_is_the_checks_that_ran(tmp_path, monkeypatch):
    real_load = rung_registry.load

    def load(names=rung_registry.DEFAULT_RUNGS):
        return [("exif", exif_rung.check)] if names != rung_registry.CONTEXT_RUNGS else [("context", context_rung.check)]

    monkeypatch.setattr(rung_registry, "load", load)
    try:
        client = TestClient(create_app(stub=False))
    finally:
        monkeypatch.setattr(rung_registry, "load", real_load)
    data = canon_jpeg(tmp_path)
    plain = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}).json()
    assert plain["rungs"] == ["exif"]           # no caption, so the context check did not run
    with_caption = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")},
                               data={"caption": "Shot on iPhone 15 Pro."}).json()
    assert with_caption["rungs"] == ["exif", "context"] and with_caption["rungs_failed"] == plain["rungs_failed"] == []
    assert "context" in client.get("/api/health").json()["rungs"]   # health still lists what can run


def test_the_page_counts_and_scores_once():
    # The answer's per-card explanation lines are not repeated beside the rows that already say them.
    assert 'id="explain-cards"' not in HTML and "per_card" not in APP
    assert '"This answer ran " + rungs.length' in APP
    # The deep check shows its summary line only (its step lines are in "Checks Muse Glimmer called"), and its cards
    # from checks the answer already ran fold away.
    assert '!l.template.startsWith("step.")' in INV
    assert '"The same checks, run again by the deep check (" + rerun.length + ")"' in INV
    assert '<details class="quiet" id="inv-rerun" hidden>' in HTML


# ---- stage 1 fix round (a reviewer @ 148b255): N1 and the item-5 leftovers -------------------

def test_headline_names_its_reason_word_for_word_from_the_first_concern_card():
    verdict = HTML[HTML.index('<div class="verdict-text">'):HTML.index('id="factcheck"')]
    assert verdict.index('id="answer"') < verdict.index('<p class="reason" id="reason" hidden></p>') < verdict.index('id="meaning"')
    fn = APP[APP.index("function concernReason"):APP.index("function showExplanation")]
    # The card's own finding, untouched, then how many more; nothing is re-worded or invented.
    assert "cards[0].finding.trim()" in fn
    assert '" And " + more + " more " + (more === 1 ? "reason" : "reasons") + ", listed first below."' in fn
    assert 'const reason = r.answer === "concerns" ? concernReason(r.evidence, r.concern_cards) : "";' in APP
    assert '$("reason").hidden = !reason;' in APP
    # Screen readers hear the reason with the answer.
    assert '"Answer: " + a.label + ". " + (reason ? reason + " " : "") + a.meaning' in APP
    assert ".reason { margin: 14px 0 0; font-size: 20px;" in CSS


def test_concern_rows_come_first_and_are_marked():
    fn = APP[APP.index("function render("):APP.index("function concernReason")]
    assert "concern.has(i) ? first" in fn
    assert "list.replaceChildren(...first.map(ev => clue(ev, true)), ...shown.map(ev => clue(ev, false)));" in fn
    assert 'el("span", "tag reason-tag", "Reason to look closer")' in APP
    # A concern row keeps its limit like every other row.
    clue_fn = APP[APP.index("function clue("):APP.index("function render(")]
    assert '"Does not prove"' in clue_fn


def test_reason_line_quotes_the_context_card_the_ladder_counted(tmp_path):
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("context", context_rung.check)]))
    body = client.post("/api/check", files={"file": ("a.jpg", canon_jpeg(tmp_path), "image/jpeg")},
                       data={"caption": "Shot on iPhone 15 Pro."}).json()
    first = body["evidence"][body["concern_cards"][0]]
    # The line the page shows is this finding, word for word: it names both sides of the disagreement.
    assert "iPhone 15 Pro" in first["finding"] and "Canon EOS R5" in first["finding"]
    assert first["does_not_prove"].strip()


def test_the_two_check_counts_are_named_apart():
    assert '"This answer ran " + rungs.length + (rungs.length === 1 ? " check" : " checks")' in APP
    assert '+ " on the file: "' in APP
    assert '"Deep check done in " + seconds() + " s. Muse Glimmer called " + n + " of Second Look\'s checks, "' in INV
    assert '<h4 id="inv-steps-title">Checks Muse Glimmer called</h4>' in HTML
    assert '"Checks Muse Glimmer called (" +' in INV
    assert " checks called." not in INV and "Checks it called" not in HTML


def test_the_cant_tell_sentence_shows_once():
    # The ladder's summary hides when the plain-language summary says the same answer; the demo stub keeps its line.
    assert "const explained = showExplanation(r.explanation);" in APP
    assert '$("summary").hidden = explained && !demo;' in APP
    # The deep check's summary line is not shown again when it is the plain-language summary above, word for word.
    assert 'explained: explained ? r.explanation.summary : ""' in APP
    assert "summary.trim() === current.explained.trim()" in INV
    assert "if (!shownAbove) line(lines, summary);" in INV
    assert '$("inv-explain-label").hidden = !lines.children.length;' in INV


# ---- 6. the image drop is the hero --------------------------------------------------------------

def test_image_drop_is_the_hero_and_no_box_sits_in_a_box():
    drop = re.search(r"\n#drop \{[^}]*\}", CSS).group(0)
    assert "min-height: 220px" in drop and "1.5px dashed var(--tint)" in drop
    assert "--tint: #B9AD98;" in CSS and "#drop:hover, #drop.over" in CSS
    panel = re.search(r"\.panel \{[^}]*\}", DESK_CSS).group(0)
    assert "border" not in panel and "background" not in panel
    voice = re.search(r"#voice-drop \{[^}]*\}", DESK_CSS).group(0)
    assert "dashed" not in voice
    assert "2px dashed" not in CSS + DESK_CSS


# ---- stage 1 fix round 2 (a reviewer @ 73b2d61): AA on cream, and only the checks that ran -----

CREAM, PANEL = "#F4F0E6", "#EFE9DB"   # the page ground, and the darkest ground small text sits on (.trust, .inv-setup)
ALL_CSS = {n: (STATIC / n).read_text() for n in ("app.css", "desk.css", "investigate.css")}
# Bold text of 24 px and up, so WCAG AA asks 3:1 (large text is 18.66 px bold or 24 px).
LARGE_TEXT = {".answer.signed_ai", ".desk-answer.found", ".dcard .rating"}


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _root() -> dict[str, str]:
    block = re.search(r":root \{([^}]*)\}", ALL_CSS["app.css"]).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _hex(value: str) -> str:
    v = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return _root()[v.group(1)] if v else value


def _text_rules():
    """(file, selector, declarations, colour) for every rule in the page's CSS that sets a text colour."""
    for name, css in ALL_CSS.items():
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for sel, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            m = re.search(r"(?<![\w-])color:\s*([^;]+)", decls)
            if m:
                yield name, " ".join(sel.split()), decls, m.group(1).strip()


def _own_ground(decls: str) -> bool:
    m = re.search(r"(?<![\w-])background(?:-color)?:\s*([^;]+)", decls)
    return bool(m) and m.group(1).strip() not in ("transparent", "none")


def test_brand_rust_is_under_aa_on_cream_so_small_text_uses_rust_text():
    root = _root()
    assert root["--rust"] == "#B4532F" and root["--rust-text"] == "#9E4526"
    assert round(_contrast(root["--rust"], CREAM), 2) == 4.37            # under 4.5: fills, rules, large text only
    assert round(_contrast(root["--rust-text"], CREAM), 2) == 5.54
    assert round(_contrast(root["--rust-text"], PANEL), 2) == 5.21


def test_reason_tag_and_missing_text_use_the_darker_rust():
    colour = {sel: _hex(value) for _, sel, _, value in _text_rules()}
    for sel in (".tag.reason-tag", ".clue .pd dd.missing", ".missing"):
        assert colour[sel] == "#9E4526", sel
    assert "border: 1px solid var(--rust)" in re.search(r"\.tag\.reason-tag \{[^}]*\}", CSS).group(0)


def test_every_text_colour_on_the_cream_ground_meets_aa():
    """Every rule that colours text and sets no fill of its own sits on the cream page (or a lighter card,
    or the #EFE9DB panel): its colour must reach 4.5:1 on cream and on that panel, or 3:1 for large text."""
    checked = set()
    for name, sel, decls, value in _text_rules():
        if _own_ground(decls):
            continue   # text on its own fill (cream on ink buttons, the tags) is not on the cream ground
        fg = _hex(value)
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", fg), (name, sel, value)
        grounds, need = ((CREAM,), 3.0) if sel in LARGE_TEXT else ((CREAM, PANEL), 4.5)
        for ground in grounds:
            assert _contrast(fg, ground) >= need, (name, sel, fg, ground, round(_contrast(fg, ground), 2))
        checked.add(sel)
    # The parser reached the rows the review named and the other small rust text on cream.
    assert {".tag.reason-tag", ".clue .pd dd.missing", ".rungs .warn", "#status.error", ".badge",
            "#desk-status.error", "#caption-count.over", ".caption-error", "#receipt-status .warn"} <= checked
    assert len(checked) >= 40


def _boom(path):
    raise RuntimeError("weights not fetched")


def _ai_label(path):
    return [Evidence(rung="iptc", finding="The IPTC source type says AI.", proves="A label is present.",
                     does_not_prove="Labels are easily added or removed.", strength="metadata",
                     raw={"ai_label": True})]


def test_a_check_that_raises_is_not_counted_as_run(tmp_path):
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("watermark", _boom)]))
    data = canon_jpeg(tmp_path)
    for extra in ({}, {"data": {"caption": "Shot on a Canon."}}):
        body = client.post("/api/check", files={"file": ("a.jpg", data, "image/jpeg")}, **extra).json()
        assert body["rungs"] == ["exif"] and body["rungs_failed"] == ["watermark"]
        assert body["answer"] == "cant_tell" and "could not run: watermark" in body["evidence"][-1]["finding"]
    batch = client.post("/api/batch", files=[("files", ("a.jpg", data, "image/jpeg"))]).json()
    entry = batch["results"][0]
    assert entry["rungs"] == ["exif"] and entry["rungs_failed"] == ["watermark"]


def test_a_failed_check_is_named_even_when_there_is_a_concern(tmp_path):
    # A concern answer has no limit card, so the response itself must carry the split.
    client = TestClient(create_app(stub=False, rungs=[("iptc", _ai_label), ("watermark", _boom)]))
    body = client.post("/api/check", files={"file": ("a.jpg", canon_jpeg(tmp_path), "image/jpeg")}).json()
    assert body["answer"] == "concerns" and not any(e["raw"].get("limit") for e in body["evidence"])
    assert body["rungs"] == ["iptc"] and body["rungs_failed"] == ["watermark"]


def test_the_watermark_rung_without_its_weights_could_not_run(tmp_path, monkeypatch):
    from secondlook.rungs import watermark as wm

    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(tmp_path / "missing.jit"))
    client = TestClient(create_app(stub=False, rungs=[("exif", exif_rung.check), ("watermark", wm.check)]))
    body = client.post("/api/check", files={"file": ("a.jpg", canon_jpeg(tmp_path), "image/jpeg")}).json()
    assert body["rungs"] == ["exif"] and body["rungs_failed"] == ["watermark"]


def test_run_traced_reports_the_split_and_run_keeps_its_contract(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(canon_jpeg(tmp_path))
    result, ran, failed, why = ladder.run_traced(str(p), rungs=[("exif", exif_rung.check), ("watermark", _boom)])
    assert ran == ["exif"] and failed == ["watermark"] and why == [None]   # a RuntimeError has no fixed reason
    assert ladder.run(str(p), rungs=[("exif", exif_rung.check), ("watermark", _boom)]) == result


def test_the_page_names_a_check_that_could_not_run():
    fn = APP[APP.index("const rungs = Array.isArray(r.rungs)"):APP.index("render(r.evidence.length")]
    assert "const failed = Array.isArray(r.rungs_failed) ? r.rungs_failed : [];" in fn
    assert '" could not run: "' in fn and "failed.map((n, i) => (RUNGS[n] || n)" in fn
    assert '"No check could run on this file."' in fn
    # Stage 2 (S-a): the paragraph stays grey; the ran line is a plain span unless nothing ran, and the
    # could-not-run line is its own warn span on its own line.
    assert 'rl.className = "rungs";' in fn
    assert 'ranLine && el("span", rungs.length ? "" : "warn", ranLine)' in fn
    assert 'failedLine && el("span", "warn failed", failedLine)' in fn


# ---- stage 2 (DAT-850): small text on a fill of its own --------------------------------------------

def _rules():
    """(file, selector, declarations) for every rule in the page's CSS, one entry per selector in a list."""
    for name, css in ALL_CSS.items():
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for sels, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            for sel in sels.split(","):
                yield name, " ".join(sel.split()), decls


def _colour_of(decls: str) -> str | None:
    m = re.search(r"(?<![\w-])color:\s*([^;]+)", decls)
    return m.group(1).strip() if m else None


def _fill_of(decls: str) -> str | None:
    m = re.search(r"(?<![\w-])background(?:-color)?:\s*([^;]+)", decls)
    return m.group(1).strip() if m and m.group(1).strip() not in ("transparent", "none") else None


def test_text_on_every_fill_a_rule_sets_meets_aa():
    """A rule that sets its own fill: the text on it, from its own colour or (for a :hover or state rule) the
    colour of the rule it modifies, must reach 4.5:1 on that fill. Cream on --rust is 4.37:1, so small cream text
    sits on --rust-text (5.54:1) instead: .demo-tag, .tag.strength.signed and the #again-go and #inv-go hovers."""
    rules = list(_rules())
    colour = {}
    for _, sel, decls in rules:
        c = _colour_of(decls)
        if c:
            colour[sel] = c
    body = _hex(colour["body"])

    def text_on(sel: str, decls: str) -> str:
        own = _colour_of(decls)
        if own:
            return _hex(own)
        base = sel
        while True:   # strip a trailing state (:hover, [aria-disabled="true"], ...) and look again
            if base in colour:
                return _hex(colour[base])
            stripped = re.sub(r"(:[\w-]+(\([^)]*\))?|\[[^\]]*\])$", "", base)
            if stripped == base:
                return body   # nothing sets it: the text inherits the page's ink
            base = stripped

    checked = {}
    for name, sel, decls in rules:
        fill = _fill_of(decls)
        if not fill or sel == "body" or re.search(r"\bimg$|::(after|before)$", sel):
            continue   # the page ground itself, images and drawn rules carry no text
        bg = _hex(fill)
        if re.fullmatch(r"#[0-9A-Fa-f]{3}", bg):
            bg = "#" + "".join(c * 2 for c in bg[1:])
        assert re.fullmatch(r"#[0-9A-Fa-f]{6}", bg), (name, sel, fill)
        fg = text_on(sel, decls)
        ratio = _contrast(fg, bg)
        assert ratio >= 4.5, (name, sel, fg, bg, round(ratio, 2))
        checked[sel] = round(ratio, 2)
    # The four pairs the showcase recording measured at 4.37:1, now on --rust-text.
    for sel in (".demo-tag", ".tag.strength.signed", "#again-go:hover", "#inv-go:hover"):
        assert checked[sel] == 5.54, (sel, checked.get(sel))
    # The parser reached the other filled text: the ink buttons and tags, the stub bar, the setup panel.
    # A262: the receipt buttons' hover tints the outline and fills nothing, so it is not a filled rule any more.
    assert {".tag", ".stub", "#again-go", "#inv-go", ".inv-setup pre", ".trust"} <= set(checked)
