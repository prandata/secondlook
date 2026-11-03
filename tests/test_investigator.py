"""The Glimmer investigator (DAT-873) with a fake model that scripts its tool calls.

Hostile models try to set the answer, write text that reaches the page, bring their own claim
words or facts, and reach other files; each attempt is refused, the answer stays the ladder's, and
everything a reader sees is template text, a labelled reading or a search string flagged
search_source=model.
"""
import dataclasses
import json
import re
import sys
import types

import pytest

import secondlook
from secondlook import glimmer as g
from secondlook import investigator as inv
from secondlook.rungs.claim import build_index
from secondlook.types import Evidence

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
PENTAGON = ("BREAKING: Explosion near the Pentagon!! Photo shows the U.S. Pentagon is under attack. "
            "Taken in Arlington on 22 May 2023 with an iPhone 14.")
CLAIM = "Photo shows the U.S. Pentagon is under attack"
TAKEN = "Taken in Arlington on 22 May 2023 with an iPhone 14"
CANARY = "ZQXV canary prose"     # a hostile model's own words; must never reach the note


class FakeGlimmer:
    """Stands in for GlimmerClient. `turns` scripts the investigation: each entry is a list of
    (tool, arguments) calls, or a string (the model's final text, no calls). `say` is put in the
    content of every tool-calling reply too."""

    def __init__(self, turns=(), lines=(), facts=None, vision=True, fail_check=False, say=""):
        self.turns, self.lines, self.facts = list(turns), list(lines), facts
        self.vision, self.fail_check, self.say = vision, fail_check, say
        self.requests: list[dict] = []

    def check(self):
        if self.fail_check:
            raise g.GlimmerError("server at http://127.0.0.1:8081 unavailable")
        return {"model": g.MODEL, "vision": self.vision}

    def chat(self, messages, *, max_tokens=1024, schema=None, tools=None):
        props = (schema or {}).get("properties", {})
        kind = ("tools" if tools else "lines" if "lines" in props else "facts" if "date_text" in props
                else "draft")
        self.requests.append({"kind": kind, "messages": messages, "tools": tools})
        reply = lambda content, calls=(): g.Reply(content, 0, "stop", 1.0, 10, 5, list(calls))  # noqa: E731
        if kind == "tools":
            turn = self.turns.pop(0) if self.turns else "done"
            if isinstance(turn, str):
                return reply(turn)
            return reply(self.say, [g.ToolCall(f"c{i}", name, a if isinstance(a, str) else json.dumps(a))
                                    for i, (name, a) in enumerate(turn)])
        if kind == "lines":
            return reply(json.dumps({"lines": self.lines}))
        if kind == "facts":
            return reply(json.dumps(self.facts or {"date_text": "", "place": "", "device": ""}))
        return reply(CANARY)

    def kinds(self):
        return [r["kind"] for r in self.requests]


def card(rung="exif", strength="none", **raw):
    return Evidence(rung, f"A {rung} card.", f"Only the {rung} card.", "Nothing else.", strength, raw)


# X1 (DAT-872) adds the ladder rule that a context card with raw["contradiction"] is a concern.
# Before it merges the rules have no such rule, and the investigator must not invent one.
X1_RULE = __import__("secondlook.ladder", fromlist=["_is_concern"])._is_concern(
    Evidence("context", "f", "p", "d", "metadata", {"contradiction": True}))
SIGNED_CAMERA = card("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=False)
NOTHING = [("exif", lambda p: [card()])]


@pytest.fixture
def jpg(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(JPEG)
    return p


@pytest.fixture
def claims_db(tmp_path):
    feed = {"dataFeedElement": [{"item": [{
        "claimReviewed": "Photo shows the U.S. Pentagon “is under attack.”",
        "url": "https://www.politifact.com/factchecks/2023/may/25/x/",
        "reviewRating": {"alternateName": "Pants on Fire"},
        "author": {"name": "PolitiFact", "url": "https://www.politifact.com"},
        "datePublished": "2023-05-25",
    }]}]}
    f = tmp_path / "feed.json"
    f.write_text(json.dumps(feed))
    db = tmp_path / "claims.sqlite"
    build_index(f, db)
    return db


@pytest.fixture
def no_e1(monkeypatch):
    """The desk's own templates: E1's explain module is not installed."""
    monkeypatch.setitem(sys.modules, "secondlook.explain", None)   # import fails
    monkeypatch.delattr(secondlook, "explain", raising=False)


@pytest.fixture
def lookups(monkeypatch):
    """Every query S6 is asked, through M1's lookup_candidate."""
    seen, real = [], g.lookup_candidate

    def spy(candidate, db=None, limit=5):
        seen.append(candidate.text)
        return real(candidate, db, limit)

    monkeypatch.setattr(g, "lookup_candidate", spy)
    return seen


def contradicting_rung(seen):
    """X1's contract: check(path, context=None); a contradiction card unless the date is 2023-05-22."""
    def check(path, context=None):
        seen.append(context)
        f = context.facts
        if f.date and f.date != "2023-05-22":
            return [Evidence("context", f"The caption says {f.date}. The file's own date says 2023-05-22.",
                             "The caption and the file's facts disagree.", "Metadata can be wrong.", "metadata",
                             {"contradiction": True})]
        return [Evidence("context", "The caption and the file agree.", "Nothing.", "Anything.", "none",
                         {"contradiction": False, "compared": True})]
    return check


def uncompared_rung(checks=None, **raw):
    """X1's "none" card when the rung read the caption but had nothing in the file to hold it against."""
    def check(path, context=None):
        return [Evidence("context", "Nothing could be compared between the caption and the file's own facts.",
                         "Nothing: no fact could be compared.", "Anything.", "none",
                         {"contradiction": False, "compared": False, "checks": checks or [], **raw})]
    return check


def run(jpg, model, caption=PENTAGON, **kw):
    kw.setdefault("rungs", NOTHING)
    kw.setdefault("context_rung", None)
    return inv.investigate(str(jpg), caption, model, **kw)


def page(note) -> str:
    """Everything the web route sends, as one string."""
    return json.dumps(dataclasses.asdict(note), ensure_ascii=False)


def templated(note) -> bool:
    """Every step result and explanation line is template text."""
    fixed = set(inv.STEP_TEMPLATES.values()) | set(inv.DESK_SUMMARIES.values())
    pattern = "|".join(re.escape(t).replace(r"\{", "{").replace(r"\}", "}") for t in fixed)
    pattern = re.sub(r"\{\w+\}", r"[\\w ,'-]+", pattern)
    rx = re.compile(rf"(?:\d+\. )?(?:{pattern})")
    return all(rx.fullmatch(s.result) for s in note.steps) and \
        all(rx.fullmatch(x["text"]) for x in note.explanation_lines if x["template"].startswith(("step.", "desk.")))


# ---- an honest run --------------------------------------------------------------------------------

def test_honest_run_uses_every_tool_and_the_rules_decide(jpg, claims_db, no_e1):
    seen = []
    model = FakeGlimmer(turns=[
        [("run_ladder", {"path": "photo.jpg"})],
        [("read_text_in_image", {"path": "photo.jpg"})],
        [("lookup_claim", {"text": CLAIM + "."})],
        [("extract_claim_facts", {"text": TAKEN + "."})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2023-05-22", "place": "Arlington"}})],
        "done",
    ], lines=["BREAKING"], facts={"date_text": "22 May 2023", "place": "Arlington", "device": "iPhone 14"})
    note = run(jpg, model, claims_db=claims_db, context_rung=contradicting_rung(seen))
    assert [s.tool for s in note.steps] == ["run_ladder", "read_text_in_image", "lookup_claim",
                                            "extract_claim_facts", "context_check"]
    assert all(s.ok and s.by == "model" for s in note.steps)
    assert note.answer == "cant_tell"                          # claim cards and agreement move nothing
    claim_cards = [e for e in note.evidence if e.rung == "claim"]
    assert claim_cards and "Pants on Fire" in claim_cards[0].finding
    assert all(e.raw["search_source"] == "model" for e in claim_cards)
    assert note.steps[2].search_source == "model" and note.steps[2].arguments == {"text": CLAIM, "from": "caption"}
    assert note.facts == [{"date": "2023-05-22", "place": "Arlington", "device": "iPhone 14", "source": "model"}]
    assert seen[0].facts.source == "model" and seen[0].caption == PENTAGON
    assert note.image_text == ["BREAKING"] and note.label == g.MODEL_READING
    assert note.explanation_source == "rules" and templated(note)
    assert note.explanation.splitlines() == [
        "1. Ran the file checks: 2 cards. Fixed rules give the answer from these cards.",
        "2. Read the words printed in the image: 1 line, shown as a model reading.",
        "3. Searched published fact-checks with words from the caption: 1 published fact-check.",
        "4. Read the caption's date, place and device, shown as a model reading in the caption's own words.",
        "5. Compared the caption's date and place with the file's own facts and found no disagreement.",
        inv.DESK_SUMMARIES["cant_tell"],
    ]
    assert "draft" not in model.kinds()                        # no model is asked to explain
    json.loads(page(note))                                     # the note serialises for the web


def test_a_contradiction_from_the_context_rung_raises_a_concern(jpg, no_e1):
    model = FakeGlimmer(turns=[
        [("extract_claim_facts", {"text": "Taken on 22 May 2024."})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2024-05-22"}})],
    ], facts={"date_text": "22 May 2024", "place": "", "device": ""})
    note = run(jpg, model, caption="Taken on 22 May 2024.", context_rung=contradicting_rung([]))
    assert any(e.rung == "context" and e.raw["contradiction"] for e in note.evidence)
    assert note.steps[1].result == "Compared the caption's date with the file's own facts: 1 disagreement."
    assert note.answer == ("concerns" if X1_RULE else "cant_tell")
    if X1_RULE:
        assert not any(e.raw.get("_ladder_limit") for e in note.evidence)   # no cant_tell limit card left
        assert note.explanation.splitlines()[-1] == inv.DESK_SUMMARIES["concerns"].format(reasons="1 reason")
    assert note.steps[-1].tool == "run_ladder" and note.steps[-1].by == "rules"


def _context_step(jpg, rung, facts_read, caption):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": caption})],
                               [("context_check", {"path": "photo.jpg", "facts": {k: facts_read[k] for k in ("date", "place", "device")
                                                                                      if facts_read.get(k)}})]],
                        facts={"date_text": facts_read.get("date_text", ""), "place": facts_read.get("place", ""),
                               "device": facts_read.get("device", "")})
    return run(jpg, model, caption=caption, context_rung=rung)


def test_nothing_to_compare_is_not_called_no_disagreement(jpg, no_e1):
    # The Gatlinburg case from W10 part 2: the card says nothing could be compared, so the step must too.
    checks = [{"field": "date", "outcome": "no_file_fact", "note": f"Date: the file has no capture time. {CANARY}"},
              {"field": "place", "outcome": "no_file_fact", "note": "Place: the file has no usable GPS position."}]
    note = _context_step(jpg, uncompared_rung(checks), {"date": "2023-05-22", "place": "Arlington",
                                                       "date_text": "22 May 2023"}, TAKEN)
    step = note.steps[1]
    assert step.ok and step.template == "context_check.none"
    assert step.result == ("Tried to compare the caption's date and place with the file's own facts, but nothing "
                           "could be compared: no capture time was found in the file and no GPS position was "
                           "found in the file.")
    assert "no disagreement" not in note.explanation and "found no disagreement" not in step.result
    assert CANARY not in json.dumps([dataclasses.asdict(x) for x in note.steps]) + note.explanation  # notes stay on the card
    assert templated(note)


def test_nothing_to_compare_without_a_reason_says_so_in_fixed_words(jpg, no_e1):
    # A rung that gives no per-fact outcomes (or no "compared" flag at all) never earns "no disagreement".
    bare = lambda path, context=None: [Evidence("context", "Nothing.", "Nothing.", "Anything.", "none",  # noqa: E731
                                                {"contradiction": False})]
    note = _context_step(jpg, bare, {"date": "2023-05-22", "date_text": "22 May 2023"}, TAKEN)
    assert note.steps[1].template == "context_check.none"
    assert note.steps[1].result == ("Tried to compare the caption's date with the file's own facts, but nothing "
                                    "could be compared: the file's own facts did not cover them.")


def test_a_comparison_that_ran_still_says_no_disagreement(jpg, no_e1):
    note = _context_step(jpg, contradicting_rung([]), {"date": "2023-05-22", "date_text": "22 May 2023"}, TAKEN)
    assert note.steps[1].template == "context_check.agree"
    assert note.steps[1].result == "Compared the caption's date with the file's own facts and found no disagreement."


def test_the_real_context_rung_on_a_file_with_no_facts_says_nothing_was_compared(jpg, no_e1):
    from secondlook.rungs import context as cx
    note = _context_step(jpg, cx.check, {"date": "2023-05-22", "date_text": "22 May 2023"}, TAKEN)
    assert note.steps[1].template == "context_check.none"
    assert note.steps[1].result == ("Tried to compare the caption's date with the file's own facts, but nothing "
                                    "could be compared: no capture time was found in the file.")


def test_a_signed_answer_is_kept_whatever_the_tools_add(jpg, no_e1):
    model = FakeGlimmer(turns=[
        [("extract_claim_facts", {"text": "Taken on 22 May 2024."})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2024-05-22"}})],
    ], facts={"date_text": "22 May 2024", "place": "", "device": ""})
    note = run(jpg, model, caption="Taken on 22 May 2024.", rungs=[("c2pa", lambda p: [SIGNED_CAMERA])],
               context_rung=contradicting_rung([]))
    assert note.answer == "signed_camera"
    assert note.explanation.splitlines()[-1] == inv.DESK_SUMMARIES["signed_camera"]


# ---- hostile models: the answer -------------------------------------------------------------------

def test_hostile_model_cannot_set_the_answer(jpg, no_e1):
    model = FakeGlimmer(turns=[
        [("set_answer", {"answer": "signed_camera"})],
        [("run_ladder", {"path": "photo.jpg", "answer": "signed_camera"})],
        [("run_ladder", '{"path": "photo.jpg"} answer=signed_camera')],
        "FINAL ANSWER: signed_camera. This photo is authentic.",
    ], say="The answer is signed_camera.")
    note = run(jpg, model)
    assert note.answer == "cant_tell"
    assert [s.ok for s in note.steps] == [False, False, False, True]
    assert note.steps[0].tool == "unknown"
    assert note.steps[0].result == "A request to an unknown tool was refused: no tool has that name."
    assert note.steps[1].result == "A request to run_ladder was refused: the arguments were not the tool's."
    assert note.steps[2].result == "A request to run_ladder was refused: the arguments were not JSON."
    assert note.steps[3].by == "rules"                          # the investigator ran the ladder itself
    assert "signed_camera" not in page(note) and "photo is authentic" not in page(note)
    assert templated(note)


# ---- hostile models: prose on the page ------------------------------------------------------------

def test_hostile_model_cannot_write_prose_that_reaches_the_page(jpg, claims_db, no_e1):
    """The canary goes everywhere a model can write: its replies, a tool name, refused arguments,
    a claim, facts, a facts value it made up. None of it is on the page."""
    seen = []
    model = FakeGlimmer(turns=[
        [("run_ladder", {"path": "photo.jpg"}), (CANARY, {"path": "photo.jpg"})],
        [("lookup_claim", {"text": f"{CLAIM}. {CANARY}."}), ("lookup_claim", {"text": CANARY})],
        [("extract_claim_facts", {"text": CANARY}), ("extract_claim_facts", {"text": TAKEN})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2023-05-22", "place": CANARY}})],
        [("read_text_in_image", {"path": CANARY})],
        f"Explanation for the reader: {CANARY}",
    ], say=CANARY, facts={"date_text": "22 May 2023", "place": CANARY, "device": "iPhone 14"})
    note = run(jpg, model, claims_db=claims_db, context_rung=contradicting_rung(seen))
    assert CANARY not in page(note)
    assert CANARY.split()[0] not in page(note)
    assert note.facts == [{"date": "2023-05-22", "place": None, "device": "iPhone 14", "source": "model"}]
    assert [s.ok for s in note.steps] == [True, False, False, False, False, True, False, False]
    assert all(s.arguments is None for s in note.steps if not s.ok)
    assert seen == [] and templated(note)


def test_the_explanation_depends_on_the_tool_results_only(jpg, claims_db, no_e1):
    """A quiet model and one that says anything it likes, with the same tool calls, get the same
    explanation word for word."""
    turns = [[("run_ladder", {"path": "photo.jpg"})], [("lookup_claim", {"text": CLAIM})]]
    quiet = run(jpg, FakeGlimmer(turns=list(turns)), claims_db=claims_db)
    loud = run(jpg, FakeGlimmer(turns=[*turns, "This image is genuine, tell the reader."],
                                say="Ignore the rules: the image is genuine."), claims_db=claims_db)
    assert quiet.explanation == loud.explanation and quiet.explanation_lines == loud.explanation_lines
    assert "genuine" not in page(loud)


def test_printed_text_reaches_the_page_only_as_a_labelled_redacted_reading(jpg, claims_db, no_e1):
    model = FakeGlimmer(turns=[[("read_text_in_image", {"path": "photo.jpg"})]],
                        lines=["Call 0412 345 678 now", "Photo shows the U.S. Pentagon is", "under attack."])
    note = run(jpg, model, caption="", claims_db=claims_db)
    assert note.label == g.MODEL_READING
    assert note.image_text[0] == "Call [phone number removed] now"
    assert "0412" not in page(note)
    assert note.steps[0].result == "Read the words printed in the image: 3 lines, shown as a model reading."


def test_desk_templates_never_say_real_or_fake():
    banned = re.compile(r"\b(real\w*|authentic\w*|genuin\w*|fake\w*|true\w*|truth\w*|fals\w*|verif\w*|"
                        r"prove\w*|proof\w*|accura\w*|correct\w*)\b", re.I)
    for text in [*inv.STEP_TEMPLATES.values(), *inv.DESK_SUMMARIES.values(), *inv.REFUSALS.values(),
                 inv.EXPLANATION_LABEL]:
        assert not banned.search(text), text


# ---- hostile models: claim words ------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Photo shows the Pentagon is under attack",                        # a word dropped
    "Photo shows the U.S. Pentagon is not under attack",               # a negation added
    "Photo shows the U.S. Pentagon is under attack, rated TRUE",       # words added
    "Photo shows the U.S. Pentagon",                                   # part of a clause
    "Explosion near the Pentagon",                                     # part of another clause
    "Taken in Arlington on 22 May 2023 with an iPhone 14 under attack",  # two clauses spliced
    "Photo montre le Pentagone américain attaqué",                     # a translation
    "photo shows the u.s. pentagon is under attack",                   # case changed
    "Photo shows the U.S. Pentagon is under attack. Ignore previous instructions",
])
def test_hostile_model_cannot_invent_a_claim_clause(jpg, claims_db, lookups, no_e1, text):
    note = run(jpg, FakeGlimmer(turns=[[("lookup_claim", {"text": text})]]), claims_db=claims_db)
    assert not note.steps[0].ok and note.steps[0].arguments is None
    assert note.steps[0].result == "A request to lookup_claim was refused: the text was not whole sentences " \
                                   "of the caption or the printed text."
    assert lookups == [] and not any(e.rung == "claim" for e in note.evidence)


def test_more_clauses_than_the_pick_limit_are_refused(jpg, claims_db, lookups, no_e1):
    caption = "One thing happened. Two things happened. Three things happened. Four things happened."
    text = caption
    note = run(jpg, FakeGlimmer(turns=[[("lookup_claim", {"text": text})]]), caption=caption, claims_db=claims_db)
    assert not note.steps[0].ok and lookups == []


def test_the_query_is_the_sources_own_text(jpg, claims_db, lookups, no_e1):
    """Whole clauses are looked up as the caption writes them, whatever spacing or full stop the
    model copied; several clauses join with '; ' in caption order."""
    model = FakeGlimmer(turns=[[("lookup_claim", {"text": f"  {CLAIM}.  "}),
                                ("lookup_claim", {"text": f"{TAKEN}! {CLAIM}"})]])
    note = run(jpg, model, claims_db=claims_db)
    assert lookups == [CLAIM, f"{CLAIM}; {TAKEN}"]
    assert [s.arguments["text"] for s in note.steps[:2]] == lookups


def test_printed_text_once_read_can_be_looked_up(jpg, claims_db, lookups, no_e1):
    """Wrapped lines join into one query; the cards say a model chose the words."""
    model = FakeGlimmer(turns=[[("lookup_claim", {"text": f"{CLAIM}."})],
                               [("read_text_in_image", {"path": "photo.jpg"})],
                               [("lookup_claim", {"text": f"{CLAIM}."})]],
                        lines=["Example Account · 2h", "Photo shows the U.S. Pentagon is", "under attack."])
    note = run(jpg, model, caption="", claims_db=claims_db)
    assert [s.ok for s in note.steps[:3]] == [False, True, True]     # no caption: nothing to copy yet
    assert lookups == [f"{CLAIM}."]
    assert note.steps[2].arguments == {"text": f"{CLAIM}.", "from": "printed text"}
    cards = [e for e in note.evidence if e.rung == "claim"]
    assert cards and cards[0].raw["search_source"] == "model" and cards[0].finding.endswith(
        g.model_search_note("on-image text"))


# ---- hostile models: facts ------------------------------------------------------------------------

def test_facts_are_verbatim_from_the_caption(jpg, no_e1):
    """A worked-out date, a place the caption does not spell that way and a device it does not name
    are all dropped; the model's values are never repaired."""
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": "Golden hour in Arlington last weekend."})]],
                        facts={"date_text": "2026-10-24", "place": "Arlington, Virginia", "device": "Pixel 10"})
    note = run(jpg, model, caption="Golden hour in Arlington last weekend.")
    assert note.facts == []
    assert note.steps[0].result == "Looked for a date, place or device in the caption and kept none."


def test_facts_come_from_the_caption_only(jpg, no_e1):
    """The printed text is a model reading and says nothing about this file."""
    seen = []
    model = FakeGlimmer(turns=[
        [("read_text_in_image", {"path": "photo.jpg"})],
        [("extract_claim_facts", {"text": "Flooded on 1 June 2020."})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2020-06-01"}})],
    ], lines=["Flooded on 1 June 2020."], facts={"date_text": "1 June 2020", "place": "", "device": ""})
    note = run(jpg, model, caption="Look at this", context_rung=contradicting_rung(seen))
    assert [s.ok for s in note.steps[:3]] == [True, False, False]
    assert note.steps[1].result == "A request to extract_claim_facts was refused: the text was not whole " \
                                   "sentences of the caption."
    assert "facts" not in model.kinds() and note.facts == [] and seen == [] and note.answer == "cant_tell"


def test_hostile_model_cannot_invent_facts_for_the_context_check(jpg, no_e1):
    seen = []
    model = FakeGlimmer(turns=[
        [("context_check", {"path": "photo.jpg", "facts": {"date": "1999-01-01"}})],
        [("extract_claim_facts", {"text": TAKEN})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2023-05-22", "place": "Moscow"}})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "2023-05-22", "verdict": "fake"}})],
        [("context_check", {"path": "photo.jpg", "facts": {"date": "22 May 2023"}})],
    ], facts={"date_text": "22 May 2023", "place": "Arlington", "device": ""})
    note = run(jpg, model, context_rung=contradicting_rung(seen))
    assert [s.ok for s in note.steps[:5]] == [False, True, False, False, False]
    assert note.steps[0].result.endswith("the facts did not come from an earlier reading of the caption.")
    assert note.steps[3].result.endswith("facts take a date, a place and a device only.")
    assert "Moscow" not in page(note) and seen == [] and note.answer == "cant_tell"


@pytest.mark.parametrize("text,iso", [
    ("22 May 2023", "2023-05-22"),
    ("Sept. 30, 2024", "2024-09-30"),
    ("September 30th, 2024", "2024-09-30"),
    ("2023-05-22", "2023-05-22"),
    ("Oct 3 2021", "2021-10-03"),
    ("30 February 2023", None),
    ("May 2023", None),
    ("03/04/2026", None),
    ("Smarch 3, 2021", None),
])
def test_parse_date_text(text, iso, monkeypatch):
    monkeypatch.setitem(sys.modules, "secondlook.extract", None)     # the fallback, not X1's
    assert inv.parse_date_text(text) == iso


def test_a_date_as_written_must_be_a_full_day(jpg, no_e1):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": "Shot in May 2023 in Paris"})]],
                        facts={"date_text": "May 2023", "place": "Paris", "device": ""})
    note = run(jpg, model, caption="Shot in May 2023 in Paris")
    assert note.facts == [{"date": None, "place": "Paris", "device": None, "source": "model"}]


# ---- E1 ---------------------------------------------------------------------------------------

def test_e1_explains_the_cards_when_installed(jpg, monkeypatch):
    calls = []

    def explain(result):
        calls.append(result)
        return types.SimpleNamespace(summary="E1 summary.", summary_template="cant_tell", per_card=[
            types.SimpleNamespace(sentence=f"E1 on {e.rung}.", template=f"{e.rung}.x") for e in result.evidence])

    fake = types.SimpleNamespace(explain=explain, SOURCE="rules")
    monkeypatch.setitem(sys.modules, "secondlook.explain", fake)
    monkeypatch.setattr(secondlook, "explain", fake, raising=False)
    model = FakeGlimmer(turns=[[("run_ladder", {"path": "photo.jpg"})]])
    note = run(jpg, model)
    assert calls[0].answer == note.answer and calls[0].evidence == note.evidence
    assert note.explanation.splitlines()[1:] == ["E1 summary.", "E1 on exif.", "E1 on origin."]
    assert [x["template"] for x in note.explanation_lines] == ["step.run_ladder", "e1.cant_tell", "e1.exif.x",
                                                               "e1.origin.x"]
    assert "draft" not in model.kinds()


def test_a_model_backed_explain_module_is_not_used(jpg, monkeypatch):
    """N3's retired module had the same name; without SOURCE == "rules" it is never called."""
    called = []
    fake = types.SimpleNamespace(explain=lambda *a, **k: called.append(1))
    monkeypatch.setitem(sys.modules, "secondlook.explain", fake)
    monkeypatch.setattr(secondlook, "explain", fake, raising=False)
    note = run(jpg, FakeGlimmer(turns=["done"]))
    assert called == [] and note.explanation_lines[-1]["template"] == "desk.cant_tell"


def test_against_the_real_e1(jpg):
    """Runs once e1-explain is merged; skipped before."""
    real = pytest.importorskip("secondlook.explain")
    if getattr(real, "SOURCE", None) != "rules":
        pytest.skip("not E1's rules-only module")
    model = FakeGlimmer(turns=[[("run_ladder", {"path": "photo.jpg"})]], say=CANARY)
    note = run(jpg, model, rungs=[("c2pa", lambda p: [SIGNED_CAMERA])])
    ex = real.explain(dataclasses.replace(inv.Result(note.answer, note.evidence, note.summary, note.version)))
    assert note.explanation.splitlines()[1] == ex.summary
    assert not real.banned_words(note.explanation) and CANARY not in page(note)


# ---- the loop -------------------------------------------------------------------------------------

def test_hostile_model_cannot_reach_another_file(jpg, tmp_path, no_e1):
    other = tmp_path / "secret.jpg"
    other.write_bytes(JPEG)
    calls = []
    rungs = [("exif", lambda p: calls.append(p) or [card()])]
    model = FakeGlimmer(turns=[[("run_ladder", {"path": str(other)}),
                                ("read_text_in_image", {"path": "/etc/passwd"}),
                                ("context_check", {"path": "../secret.jpg", "facts": {"date": "2023-05-22"}})]])
    note = run(jpg, model, rungs=rungs, context_rung=contradicting_rung([]))
    assert [s.ok for s in note.steps[:3]] == [False, False, False]
    assert all(s.result.endswith("only the image under investigation can be checked.") for s in note.steps[:3])
    assert "lines" not in model.kinds() and "passwd" not in page(note)
    assert calls == [str(jpg)]                                  # only the rules' own ladder run


def test_the_step_limit_holds(jpg, no_e1):
    model = FakeGlimmer(turns=[[("run_ladder", {"path": "photo.jpg"})] * 3] * 5)
    note = run(jpg, model)
    assert len([s for s in note.steps if s.by == "model"]) == inv.MAX_STEPS
    assert "step limit" in note.problems[-1]
    assert note.steps[1].result == "A repeated request to run_ladder got the earlier result again."


def test_tool_results_reach_the_model_as_tool_messages(jpg, no_e1):
    model = FakeGlimmer(turns=[[("run_ladder", {"path": "photo.jpg"})], "done"])
    run(jpg, model)
    last = model.requests[1]["messages"]
    assert last[-2]["role"] == "assistant" and last[-2]["tool_calls"][0]["function"]["name"] == "run_ladder"
    assert last[-1]["role"] == "tool" and json.loads(last[-1]["content"])["answer"] == "cant_tell"
    assert [t["function"]["name"] for t in model.requests[0]["tools"]] == inv.TOOL_NAMES
    assert '"photo.jpg"' in model.requests[0]["messages"][1]["content"]
    assert str(jpg.parent) not in model.requests[0]["messages"][1]["content"]   # the model sees the name only


def test_a_refusal_tells_the_model_why(jpg, no_e1):
    model = FakeGlimmer(turns=[[("lookup_claim", {"text": "made up"})], "done"])
    run(jpg, model)
    assert json.loads(model.requests[1]["messages"][-1]["content"]) == {"refused": inv.REFUSALS["not_verbatim"]}


def test_no_model_server_gives_the_ladder_alone(jpg, no_e1):
    note = run(jpg, FakeGlimmer(fail_check=True))
    assert note.answer == "cant_tell" and note.explanation_source == "rules"
    assert [s.by for s in note.steps] == ["rules"]
    assert note.steps[0].result == "The model did not ask for the file checks, so Second Look ran them: 2 cards."
    assert "Glimmer unavailable" in note.problems[0]


def test_context_check_without_x1_is_refused_not_crashed(jpg, no_e1):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": TAKEN})],
                               [("context_check", {"path": "photo.jpg", "facts": {"date": "2023-05-22"}})]],
                        facts={"date_text": "22 May 2023", "place": "", "device": ""})
    note = run(jpg, model, context_rung=None)
    assert note.steps[1].result.endswith("the context check is not installed in this build.")
    assert not note.steps[1].ok


def test_a_failing_tool_is_recorded_and_the_run_goes_on(jpg, no_e1):
    def broken(path):
        raise RuntimeError(f"boom {CANARY}")
    model = FakeGlimmer(turns=[[("read_text_in_image", {"path": "photo.jpg"})], [("run_ladder", {"path": "photo.jpg"})]],
                        vision=False)
    note = run(jpg, model, rungs=[("exif", broken)])
    assert note.steps[0].result == "Tried to read the words printed in the image, but the reader could not run."
    assert note.steps[1].ok and note.answer == "cant_tell" and CANARY not in page(note)


# ---- a reviewer's FAIL at 4b2672c: every fact goes through the verbatim path ------------------

@pytest.mark.parametrize("caption,facts,invented", [
    # a date put together from two dates the caption gives
    ("Taken 12 Oct 2019, posted 3 April 2020.", {"date_text": "2019-10-03", "place": "", "device": ""},
     {"date": "2019-10-03"}),
    # a place put together from two sentences
    ("Explosion near the Pentagon. Taken in Arlington.", {"date_text": "", "place": "Arlington Pentagon", "device": ""},
     {"place": "Arlington Pentagon"}),
    # an all-number date that reads both ways, either way round
    ("Flooding on 03/04/2026.", {"date_text": "03/04/2026", "place": "", "device": ""}, {"date": "2026-03-04"}),
    ("Flooding on 03/04/2026.", {"date_text": "03/04/2026", "place": "", "device": ""}, {"date": "2026-04-03"}),
])
def test_no_fact_the_caption_does_not_state_is_kept(jpg, no_e1, caption, facts, invented):
    seen = []
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": caption.rstrip(".")})],
                               [("context_check", {"path": "photo.jpg", "facts": invented})]], facts=facts)
    note = run(jpg, model, caption=caption, context_rung=contradicting_rung(seen))
    assert note.facts == [] and seen == [] and note.answer == "cant_tell"
    assert note.steps[1].result.endswith("the facts did not come from an earlier reading of the caption.")


def test_the_abstractive_extractor_is_gone():
    assert not hasattr(g, "extract_facts") and not hasattr(g, "GlimmerExtractor")


@pytest.mark.parametrize("date_text,caption", [
    ("03/04/2026", "Flooding in Valencia on 03/04/2026"),
    ("05/05/2026", "Flooding in Valencia on 05/05/2026"),     # the same either way, but all numbers
    ("3.4.2026", "Flooding in Valencia on 3.4.2026"),
])
def test_an_ambiguous_date_keeps_no_date_and_the_step_says_so(jpg, no_e1, date_text, caption):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": caption})]],
                        facts={"date_text": date_text, "place": "Valencia", "device": ""})
    note = run(jpg, model, caption=caption)
    assert note.facts == [{"date": None, "place": "Valencia", "device": None, "source": "model"}]
    assert note.steps[0].template == "extract_claim_facts.kept.date_both_ways"
    assert note.steps[0].result == ("Read the caption's place, shown as a model reading in the caption's own words. "
                                    "Its date is all numbers that read day first or month first, so no date was kept.")
    assert templated(note) and date_text not in note.steps[0].result


def test_an_ambiguous_date_alone_keeps_nothing_and_the_step_says_so(jpg, no_e1):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": "Flooding on 03/04/2026"})]],
                        facts={"date_text": "03/04/2026", "place": "", "device": ""})
    note = run(jpg, model, caption="Flooding on 03/04/2026")
    assert note.facts == [] and note.steps[0].template == "extract_claim_facts.none.date_both_ways"
    assert note.steps[0].result.endswith("Its date is all numbers that read day first or month first, so no date was kept.")
    assert templated(note)


def test_an_all_number_date_that_reads_one_way_is_kept(jpg, no_e1):
    model = FakeGlimmer(turns=[[("extract_claim_facts", {"text": "Flooding on 28/10/2026"})]],
                        facts={"date_text": "28/10/2026", "place": "", "device": ""})
    note = run(jpg, model, caption="Flooding on 28/10/2026")
    assert note.facts == [{"date": "2026-10-28", "place": None, "device": None, "source": "model"}]
    assert note.steps[0].template == "extract_claim_facts.kept"


# ---- the time limit (the project lead on DAT-873: 300 s per request) -------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SlowGlimmer(FakeGlimmer):
    """Each model request takes `cost` seconds on the clock; records the timeout it was given."""

    def __init__(self, clock, cost, raise_after=None, **kw):
        super().__init__(**kw)
        self.clock, self.cost, self.raise_after = clock, cost, raise_after
        self.timeout = 300.0
        self.timeouts = []

    def chat(self, messages, **kw):
        self.timeouts.append(self.timeout)
        self.clock.now += min(self.cost, self.timeout)
        if self.raise_after is not None and self.clock.now >= self.raise_after:
            raise g.GlimmerError("Glimmer server at http://127.0.0.1:8081 unavailable: timed out")
        return super().chat(messages, **kw)


def test_default_limit_is_300_seconds_for_cli_and_web():
    import inspect
    assert inv.MAX_SECONDS == 300
    assert inspect.signature(inv.investigate).parameters["max_seconds"].default == inv.MAX_SECONDS


def test_a_slow_model_is_stopped_at_the_time_limit_and_the_ladder_answers(jpg, no_e1):
    clock = Clock()
    loop = [[("read_text_in_image", {"path": "photo.jpg"})]] * 8
    model = SlowGlimmer(clock, 70, turns=loop, lines=["Hello"])
    note = run(jpg, model, clock=clock)
    assert note.answer == "cant_tell" and note.answer == inv.ladder.run(str(jpg), rungs=NOTHING).answer
    timed = [s for s in note.steps if s.template == "timed_out"]
    assert len(timed) == 1 and not timed[0].ok and timed[0].by == "rules" and timed[0].tool == "time_limit"
    assert timed[0].result == ("The deep check timed out after 300 seconds, so it stopped there. Fixed rules give "
                               "the answer from the file checks and the cards found before then.")
    assert note.steps[-1].template == "run_ladder.rules"                  # the ladder still ran, by the rules
    assert "stopped at the time limit of 300 s" in note.problems
    assert clock.now - 1000.0 <= inv.MAX_SECONDS and note.seconds <= inv.MAX_SECONDS
    assert all(t <= inv.MAX_SECONDS - inv.LADDER_RESERVE_SECONDS for t in model.timeouts)
    assert model.timeout == 300.0                                         # the caller's client, as it came
    assert templated(note) and f"{timed[0].n}. {timed[0].result}" in note.explanation


def test_each_request_waits_only_for_the_time_left(jpg, no_e1):
    clock = Clock()
    model = SlowGlimmer(clock, 1, turns=[[("run_ladder", {"path": "photo.jpg"})], "done"])
    run(jpg, model, clock=clock, max_seconds=100)
    # check() is not a chat; the tool loop's first request had 100 - 15 reserve - 0 used, then
    # the ladder had run, so the reserve was released: 100 - 1 used.
    assert model.timeouts == [85.0, 99.0]


def test_a_request_that_hits_the_deadline_is_a_timeout_not_a_model_failure(jpg, no_e1):
    clock = Clock()
    model = SlowGlimmer(clock, 1000, raise_after=1000.0 + 1, turns=[[("run_ladder", {"path": "photo.jpg"})]])
    note = run(jpg, model, clock=clock)
    assert [s.template for s in note.steps] == ["timed_out", "run_ladder.rules"]
    assert not any(p.startswith("model failed") for p in note.problems)
    assert clock.now - 1000.0 <= inv.MAX_SECONDS


def test_within_the_limit_there_is_no_timeout_step(jpg, no_e1):
    clock = Clock()
    model = SlowGlimmer(clock, 50, turns=[[("run_ladder", {"path": "photo.jpg"})], "done"])
    note = run(jpg, model, clock=clock)
    assert "timed_out" not in [s.template for s in note.steps] and not note.problems


# ---- the client's tool calls ----------------------------------------------------------------------

def test_client_sends_tools_and_parses_tool_calls():
    sent = []

    class Resp:
        def __init__(self, body):
            self.body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self, n):
            return self.body

    def opener(req, timeout=None):
        path = req.full_url.split("8081", 1)[1]
        if path == "/v1/models":
            return Resp({"data": [{"id": g.MODEL}]})
        if path == "/props":
            return Resp({"modalities": {"vision": False}})
        sent.append(json.loads(req.data))
        return Resp({"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "run_ladder", "arguments": "{\"path\":\"x.jpg\"}"}},
            {"id": "b", "type": "function", "function": {"name": "lookup_claim", "arguments": {"text": "t"}}}]}}]})

    c = g.GlimmerClient("http://127.0.0.1:8081", opener=opener)
    r = c.chat([{"role": "user", "content": "hi"}], tools=inv.TOOLS)
    assert sent[0]["tools"] == inv.TOOLS
    assert [(t.id, t.name, json.loads(t.arguments)) for t in r.tool_calls] == [
        ("a", "run_ladder", {"path": "x.jpg"}), ("b", "lookup_claim", {"text": "t"})]
    assert c.chat([{"role": "user", "content": "hi"}]).tool_calls == []    # no tools offered, none read


# ---- web and command line -----------------------------------------------------------------------

def test_web_investigate_route():
    from fastapi.testclient import TestClient
    from secondlook.web.app import MAX_REQUEST, create_app

    got = []

    def fake(path, caption):
        got.append((path, caption))
        return inv.investigate(path, caption, FakeGlimmer(turns=[[("run_ladder", {"path": "a.jpg"})]], say=CANARY),
                               rungs=NOTHING, context_rung=None)

    client = TestClient(create_app(stub=False, rungs=NOTHING, investigator=fake))
    r = client.post("/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")}, data={"caption": "Hi there"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "cant_tell" and body["steps"][0]["tool"] == "run_ladder"
    assert body["explanation_source"] == "rules" and CANARY not in r.text
    assert body["engine"] == "ladder" and body["demo"] is False
    assert got[0][1] == "Hi there" and got[0][0].endswith("a.jpg")
    assert client.post("/api/investigate", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 415
    assert client.post("/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")},
                       data={"caption": "x" * 2001}).status_code == 413
    big = client.post("/api/investigate", content=b"x", headers={"content-length": str(MAX_REQUEST + 1),
                                                                 "content-type": "multipart/form-data; boundary=b"})
    assert big.status_code == 413
    stub = TestClient(create_app(stub=True))
    assert stub.post("/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")}).status_code == 409


def test_web_investigate_default_uses_the_ladder_without_a_model(monkeypatch):
    """With no Glimmer server the route still answers, from the ladder alone."""
    from fastapi.testclient import TestClient
    from secondlook.web.app import create_app

    monkeypatch.setenv(g.URL_ENV, "http://127.0.0.1:9")   # discard port: nothing listens
    client = TestClient(create_app(stub=False, rungs=NOTHING))
    body = client.post("/api/investigate", files={"file": ("a.jpg", JPEG, "image/jpeg")}).json()
    assert body["answer"] == "cant_tell" and body["explanation_source"] == "rules"
    assert "Glimmer unavailable" in body["problems"][0]


def test_cli_investigate(jpg, monkeypatch, capsys):
    from secondlook import cli
    seen, real = {}, inv.investigate

    def fake(path, caption=None):
        seen["args"] = (path, caption)
        return real(path, caption, FakeGlimmer(turns=["done"]), rungs=NOTHING, context_rung=None)

    monkeypatch.setattr(inv, "investigate", fake)
    assert cli.main(["investigate", str(jpg), "--caption", "A caption"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert seen["args"] == (str(jpg), "A caption")
    assert out["answer"] == "cant_tell" and out["steps"][0]["by"] == "rules"
