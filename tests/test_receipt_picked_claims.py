"""Receipt keeps the claim words Muse Glimmer picked, and the fact-check they found (DAT-850, TF-722 Beat 7).

The deep check looks a claim up with whole clauses Glimmer picked from the caption. /api/investigate keeps a
short, bounded record of each deep check it ran itself whose caption lookup found a fact-check (a run id, the
file's sha256, the caption and the clauses) and returns the id as `receipt_run`. A receipt request sends only
that id (`deep_run`), never the words: the receipt takes the clauses from the server's record after checking
the file's sha256 and the caption, runs the same fixed S6 lookup, and records the clauses, their source and the
lookup's status in `inputs`, apart from typed claims; the PDF shows the same. An unknown, expired or mismatched
id gives a receipt without picks and a plain reason. Words a client sends get no model attribution (Codex on
PR #42). The answer stays the ladder's. A receipt with no caption and no deep check is main's, byte for byte;
a receipt with a caption records it in `inputs.caption`.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from secondlook import glimmer as g  # noqa: E402
from secondlook import ladder  # noqa: E402
from secondlook import receipt as receipts  # noqa: E402
from secondlook.investigator import CLAIM_CARDS, DeskNote, Step  # noqa: E402
from secondlook.rungs import context as context_rung  # noqa: E402
from secondlook.rungs import exif as exif_rung  # noqa: E402
from secondlook.rungs.claim import build_index  # noqa: E402
from secondlook.types import Context, Evidence, Result  # noqa: E402
from secondlook.web import samples as sample_catalogue  # noqa: E402
from secondlook.web.app import STATIC, create_app  # noqa: E402
from tests.fixtures.dat850.plain_receipt import plain_receipt  # noqa: E402

RUNGS = [("exif", exif_rung.check), ("context", context_rung.check)]
# The Beat 7 caption (docs/M1-GLIMMER.md, Captions): Glimmer picked clause 2, which finds PolitiFact's review.
CAPTION = ("BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. "
           "Pentagon is under attack. Stay safe everyone")
PICK = "Photo shows the U.S. Pentagon is under attack"
PANEL_LINE = "The receipt also looks up the words Muse Glimmer picked from your caption."
HTML = (STATIC / "index.html").read_text()
APP = (STATIC / "app.js").read_text()
INV = (STATIC / "investigate.js").read_text()
FIXTURES = Path(__file__).parent / "fixtures" / "dat850"


@pytest.fixture
def claims_db(tmp_path, monkeypatch):
    """A one-review S6 index (the review test_glimmer.py uses), as the default index for this test."""
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
    monkeypatch.setenv("SECONDLOOK_CLAIMS_DB", str(db))
    return db


@pytest.fixture
def fixed_clock(monkeypatch):
    monkeypatch.setattr(receipts, "_now", lambda: "2026-10-30T00:00:00Z")


def stand_in(text=PICK, template="lookup_claim.found", source="caption", calls=None):
    """Stands in for the model only: one lookup_claim step with these words, as the investigator records it."""
    def investigator(path, caption):
        if calls is not None:
            calls.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        r = ladder.run(path, rungs=RUNGS[:1])
        steps = [Step(1, "lookup_claim", {"text": text, "from": source}, "Searched.", True, "model", 0.1, template,
                      "model")]
        return DeskNote(file=Path(path).name, answer=r.answer, summary=r.summary, evidence=r.evidence, steps=steps,
                        explanation=r.summary, version="test")
    return investigator


@pytest.fixture
def client():
    return TestClient(create_app(stub=False, rungs=RUNGS, investigator=stand_in()))


def jpeg(tmp_path, name="pentagon.jpg", colour=(120, 120, 120)):
    from PIL import Image

    p = tmp_path / name
    Image.new("RGB", (8, 8), colour).save(p, "JPEG")
    return p


def upload(path):
    return {"file": (path.name, path.read_bytes(), "image/jpeg")}


def deep_check(client, path, caption=CAPTION):
    res = client.post("/api/investigate", files=upload(path), data={"caption": caption} if caption else None)
    assert res.status_code == 200, res.text
    return res.json()


def post_receipt(client, path, fmt="json", caption=None, deep_run=None, extra=None):
    data = dict(extra or {})
    if caption is not None:
        data["caption"] = caption
    if deep_run is not None:
        data["deep_run"] = deep_run
    return client.post(f"/api/receipt?format={fmt}", files=upload(path), data=data or None)


def picked_receipt(client, path, caption=CAPTION, fmt="json"):
    run = deep_check(client, path, caption)["receipt_run"]
    assert isinstance(run, str) and run
    return post_receipt(client, path, fmt=fmt, caption=caption, deep_run=run)


# ---- 1. the whole-clause check ------------------------------------------------------------------------

@pytest.mark.parametrize("sent", [PICK, PICK + ".", "  " + PICK + ".  "])
def test_a_whole_clause_is_accepted_as_the_caption_writes_it(sent):
    assert receipts.picked_claims(CAPTION, [sent]) == [PICK]


def test_picks_come_back_in_caption_order_without_repeats():
    caption = "Biden signed the bill. Trump did not sign it."
    assert receipts.picked_claims(caption, ["Trump did not sign it", "Biden signed the bill", "Biden signed the bill."]) \
        == ["Biden signed the bill", "Trump did not sign it"]


def test_no_picks_is_no_picks():
    assert receipts.picked_claims(CAPTION, None) == [] == receipts.picked_claims(None, [])


@pytest.mark.parametrize("sent, why", [
    ("Pentagon is under attack", "part of a clause"),
    ("Photo shows the US Pentagon is under attack", "rewritten words"),
    ("Photo shows the U.S. Pentagon is not under attack", "a word the caption does not have"),
    ("Photo shows the U.S. Pentagon is under attack. Stay safe everyone", "two clauses sent as one"),
    ("", "empty"),
])
def test_anything_but_a_whole_clause_is_refused(sent, why):
    with pytest.raises(receipts.PickedRefused, match="whole clause of the caption"):
        receipts.picked_claims(CAPTION, [sent])


def test_more_than_three_picks_or_no_caption_are_refused():
    caption = "One apple. Two bees. Three cats. Four dogs."
    three = ["One apple", "Two bees", "Three cats"]
    assert receipts.picked_claims(caption, three) == three
    with pytest.raises(receipts.PickedRefused, match="At most 3"):
        receipts.picked_claims(caption, three + ["Four dogs"])
    with pytest.raises(receipts.PickedRefused, match="need the caption"):
        receipts.picked_claims(None, [PICK])
    with pytest.raises(receipts.PickedRefused, match="whole clause"):
        receipts.picked_claims(CAPTION, [123])


# ---- 2. the server's own record: the page sends a run id, never the words -----------------------------

def test_the_deep_check_records_its_picks_and_returns_a_run_id(client, tmp_path, claims_db):
    path = jpeg(tmp_path)
    body = deep_check(client, path)
    assert isinstance(body["receipt_run"], str) and len(body["receipt_run"]) >= 20
    assert deep_check(client, path)["receipt_run"] != body["receipt_run"], "one id per deep check"


@pytest.mark.parametrize("investigator, why", [
    (stand_in(template="lookup_claim.none"), "a lookup that found nothing"),
    (stand_in(template="lookup_claim.unavailable"), "a lookup that could not read the index"),
    (stand_in(source="printed text"), "words from the printed text, not the caption"),
    (stand_in(text="Pentagon is under attack"), "words that are not a whole clause of the caption"),
])
def test_a_deep_check_with_no_caption_pick_that_found_a_fact_check_records_nothing(tmp_path, claims_db,
                                                                                    investigator, why):
    c = TestClient(create_app(stub=False, rungs=RUNGS, investigator=investigator))
    assert deep_check(c, jpeg(tmp_path))["receipt_run"] is None, why


def test_invented_words_get_no_model_attribution(client, tmp_path, claims_db, fixed_clock):
    """Codex's FAIL on PR #42: a client sent any caption plus the same text as picks, without any deep check,
    and the receipt said "Picked by Muse Glimmer". Words a client sends are now ignored."""
    path = jpeg(tmp_path)
    plain = post_receipt(client, path, caption=CAPTION)
    forged = post_receipt(client, path, caption=CAPTION, extra={"picked_claims": [PICK]})
    assert forged.status_code == 200
    rc = json.loads(forged.content)
    assert "picked_claims" not in rc["inputs"] and "Muse Glimmer" not in forged.text
    assert all(c["raw"].get("search_source") != "model" for c in rc["evidence"])
    assert "x-secondlook-picks" not in forged.headers
    assert forged.content == plain.content, "the field is ignored, not half-read"


def test_an_invented_run_id_gets_no_picks_and_a_plain_reason(client, tmp_path, claims_db):
    path = jpeg(tmp_path)
    deep_check(client, path)  # a real run exists, but not under this id
    for made_up in ("abc", "x" * 5000, PICK):
        res = post_receipt(client, path, caption=CAPTION, deep_run=made_up)
        assert res.status_code == 200, res.text
        rc = json.loads(res.content)
        assert "picked_claims" not in rc["inputs"] and "claim" not in [c["rung"] for c in rc["evidence"]]
        assert res.headers["x-secondlook-picks"] == "not-added"
        assert res.headers["x-secondlook-picks-reason"] == receipts.NOT_ADDED["unknown"]


def test_a_run_id_for_another_file_or_caption_gets_no_picks(client, tmp_path, claims_db):
    path, other = jpeg(tmp_path), jpeg(tmp_path, "other.jpg", (10, 200, 10))
    run = deep_check(client, path)["receipt_run"]
    wrong_file = post_receipt(client, other, caption=CAPTION, deep_run=run)
    wrong_caption = post_receipt(client, path, caption=PICK + ". Stay safe everyone", deep_run=run)
    no_caption = post_receipt(client, path, deep_run=run)
    for res, why in ((wrong_file, "file"), (wrong_caption, "caption"), (no_caption, "caption")):
        assert res.status_code == 200, res.text
        assert "picked_claims" not in json.loads(res.content)["inputs"], why
        assert res.headers["x-secondlook-picks-reason"] == receipts.NOT_ADDED[why]
    # The same caption with other spacing is the same caption (verbatim.normalise), and the run still holds.
    spaced = post_receipt(client, path, caption="  " + CAPTION.replace(" Photo", "  Photo") + " ", deep_run=run)
    assert json.loads(spaced.content)["inputs"]["picked_claims"]["clauses"] == [PICK]


def test_runs_expire_and_the_record_is_bounded():
    now = [0.0]
    runs = receipts.DeepRuns(kept=2, seconds=60, clock=lambda: now[0])
    a = runs.add("sha-a", CAPTION, [PICK])
    assert runs.picks(a, "sha-a", CAPTION) == ([PICK], None)
    assert runs.add("sha-a", CAPTION, []) is None, "a run that picked nothing is not kept"
    now[0] = 61
    assert runs.picks(a, "sha-a", CAPTION) == ([], receipts.NOT_ADDED["unknown"]), "expired after an hour"
    ids = [runs.add(f"sha-{i}", CAPTION, [PICK]) for i in range(3)]
    assert len(runs) == 2, "at most `kept` runs"
    assert runs.picks(ids[0], "sha-0", CAPTION)[1] == receipts.NOT_ADDED["unknown"], "the oldest goes first"
    assert runs.picks(ids[2], "sha-2", CAPTION) == ([PICK], None)
    assert runs.picks(None, "sha-2", CAPTION) == ([], None) == runs.picks("", "sha-2", CAPTION), "none asked for"
    assert receipts.DEEP_RUNS_KEPT == 64 and receipts.DEEP_RUN_SECONDS == 3600


def test_the_app_keeps_at_most_the_bounded_number_of_runs(tmp_path, claims_db, monkeypatch):
    monkeypatch.setattr(receipts, "DEEP_RUNS_KEPT", 3)
    monkeypatch.setattr(receipts.DeepRuns.__init__, "__defaults__", (3, receipts.DEEP_RUN_SECONDS,
                                                                    receipts.time.monotonic))
    c = TestClient(create_app(stub=False, rungs=RUNGS, investigator=stand_in()))
    path = jpeg(tmp_path)
    ids = [deep_check(c, path)["receipt_run"] for _ in range(4)]
    first = post_receipt(c, path, caption=CAPTION, deep_run=ids[0])
    last = post_receipt(c, path, caption=CAPTION, deep_run=ids[-1])
    assert "picked_claims" not in json.loads(first.content)["inputs"]
    assert json.loads(last.content)["inputs"]["picked_claims"]["clauses"] == [PICK]


def test_a_run_id_on_the_demo_stub_gets_no_picks(tmp_path, claims_db):
    stub = TestClient(create_app(stub=True))
    res = post_receipt(stub, jpeg(tmp_path), caption=CAPTION, deep_run="anything")
    assert res.status_code == 200 and "picked_claims" not in json.loads(res.content)["inputs"]
    assert res.headers["x-secondlook-picks-reason"] == receipts.NOT_ADDED["unknown"]


def test_the_receipt_checks_the_file_again(tmp_path, claims_db):
    """Each download re-runs the ladder on the uploaded bytes: nothing from the deep check is reused."""
    seen = []

    def counting_exif(path):
        seen.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        return exif_rung.check(path)

    calls = []
    c = TestClient(create_app(stub=False, rungs=[("exif", counting_exif), RUNGS[1]],
                              investigator=stand_in(calls=calls)))
    path = jpeg(tmp_path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    run = deep_check(c, path)["receipt_run"]
    assert calls == [sha] and seen == [], "the deep check ran; the app's own ladder has not"
    for n in (1, 2):
        res = post_receipt(c, path, caption=CAPTION, deep_run=run)
        assert res.status_code == 200 and "picked_claims" in json.loads(res.content)["inputs"]
        assert seen == [sha] * n, "one more ladder run on the same bytes per receipt"
    assert calls == [sha], "the receipt does not run the deep check again"


# ---- 3. the same fixed lookup, nothing copied --------------------------------------------------------

def test_the_receipt_runs_the_investigators_lookup_on_the_picks(monkeypatch):
    seen = []
    real = g.lookup_candidate

    def spy(candidate, db=None, limit=5):
        seen.append((candidate.text, candidate.source, candidate.by_model, limit))
        return real(candidate, db, limit)

    monkeypatch.setattr(g, "lookup_candidate", spy)
    base = Result("cant_tell", [], "s", "v")
    receipts.with_picked_claims(base, ["Biden signed the bill", "Trump did not sign it"])
    # The investigator's query: the picked clauses joined with CLAIM_JOIN, from the caption, marked as a model's
    # choice, CLAIM_CARDS matches (investigator._clauses_of and lookup_claim).
    assert seen == [("Biden signed the bill; Trump did not sign it", "caption", True, CLAIM_CARDS)]
    assert receipts.with_picked_claims(base, []) == (base, None), "no picks, no lookup"
    assert len(seen) == 1


def test_receipt_with_picks_keeps_the_fact_check_and_the_answer(client, tmp_path, claims_db, fixed_clock):
    path = jpeg(tmp_path)
    shown = client.post("/api/check", files=upload(path), data={"caption": CAPTION}).json()
    res = picked_receipt(client, path)
    assert res.status_code == 200, res.text
    assert res.headers["x-secondlook-picks"] == "added" and "x-secondlook-picks-reason" not in res.headers
    rc = json.loads(res.content)
    assert rc["answer"] == shown["answer"] == res.headers["x-secondlook-answer"]
    # The receipt prints the summary the page shows for this check: app.js shows `explanation.summary`, which
    # /api/check builds with explain(result) (PR #39), not the ladder's own `summary` line.
    assert shown["explanation"]["summary"].strip()
    assert rc["summary"] == shown["explanation"]["summary"]
    # The cards the plain check gave, in order, then the fact-check the picks found.
    assert [c["rung"] for c in rc["evidence"]] == [e["rung"] for e in shown["evidence"]] + ["claim"]
    card = rc["evidence"][-1]
    assert card["raw"]["rating"] == "Pants on Fire" and card["raw"]["publisher"] == "politifact.com"
    assert card["raw"]["review_url"] == "https://www.politifact.com/factchecks/2023/may/25/x/"
    assert card["raw"]["search_source"] == "model" and card["raw"]["search_from"] == "caption"
    assert card["finding"].endswith(g.model_search_note("caption"))
    # Design SHOULD 2: words a model picked are never "your" key words on the card.
    assert "key words in the picked words" in card["proves"] and "of your" not in card["proves"]
    assert "typed" not in card["proves"] + card["does_not_prove"]
    # inputs: the caption, then the picks as the caption writes them, with their source and the lookup's status.
    assert rc["inputs"] == {"caption": CAPTION, "picked_claims": {
        "clauses": [PICK], "picked_by": "Muse Glimmer", "from": "caption", "lookup": "found",
        "source": "Picked by Muse Glimmer from the caption (clause choice only; the words are the caption's own)."}}
    assert "claims" not in rc["inputs"]


def test_the_raw_caption_alone_puts_no_fact_check_in_the_receipt(client, tmp_path, claims_db):
    """Before DAT-850 the receipt had no claim card at all: the ladder's rungs do not look a claim up."""
    rc = json.loads(post_receipt(client, jpeg(tmp_path), caption=CAPTION).content)
    assert "claim" not in [c["rung"] for c in rc["evidence"]]
    assert rc["inputs"] == {"caption": CAPTION}


@pytest.mark.parametrize("db_state, status, rungs_added", [
    ("empty", "none", []),
    ("missing", "unavailable", ["claim"]),
])
def test_the_receipt_records_what_its_own_lookup_did(tmp_path, monkeypatch, db_state, status, rungs_added):
    """a reviewer N2: a lookup that finds nothing and one that cannot read the index are told apart."""
    if db_state == "empty":
        f = tmp_path / "feed.json"
        f.write_text(json.dumps({"dataFeedElement": []}))
        build_index(f, tmp_path / "claims.sqlite")
    monkeypatch.setenv("SECONDLOOK_CLAIMS_DB", str(tmp_path / "claims.sqlite"))
    c = TestClient(create_app(stub=False, rungs=RUNGS, investigator=stand_in()))
    res = picked_receipt(c, jpeg(tmp_path))
    rc = json.loads(res.content)
    assert rc["inputs"]["picked_claims"]["clauses"] == [PICK]
    assert rc["inputs"]["picked_claims"]["lookup"] == status
    assert [c["rung"] for c in rc["evidence"] if c["rung"] == "claim"] == rungs_added
    pdf = _pdf_text(picked_receipt(c, jpeg(tmp_path), fmt="pdf").content)
    assert receipts.LOOKUP_LINES[status] in pdf


def test_a_card_that_would_change_the_answer_stops_the_receipt(client, tmp_path, monkeypatch, claims_db):
    """The guard. Claim cards never move the answer; if a lookup ever returned one that did, no receipt."""
    concern = Evidence("context", "The caption and the file disagree.", "A disagreement.", "Which is right.",
                       "metadata", {"contradiction": True})
    path = jpeg(tmp_path)
    run = deep_check(client, path)["receipt_run"]
    monkeypatch.setattr(g, "lookup_candidate", lambda c, db=None, limit=5: {"cards": [concern], "lookup": None})
    with pytest.raises(receipts.AnswerWouldChange):
        receipts.with_picked_claims(Result("cant_tell", [], "s", "v"), [PICK])
    res = post_receipt(client, path, caption=CAPTION, deep_run=run)
    assert res.status_code == 500 and "would change the answer" in res.json()["detail"]


def test_claim_cards_never_move_the_ladders_answer(claims_db):
    cards = g.lookup_candidate(g.CandidateClaim(PICK, "caption"), None, CLAIM_CARDS)["cards"]
    assert cards
    for base in ([], [Evidence("exif", "Camera make: Canon.", "x", "y", "metadata", {})],
                 [Evidence("context", "Disagree.", "x", "y", "metadata", {"contradiction": True})]):
        assert ladder.decide(base + cards) == ladder.decide(base)


# ---- 4. no deep check: main's receipt, byte for byte; the caption recorded ----------------------------

def test_a_receipt_with_no_caption_and_no_deep_check_is_mains_byte_for_byte(monkeypatch):
    """Codex's test gap: tests/fixtures/dat850/plain.receipt.json was written by main at e219f4a with the same
    helper (the clock, git sha and library versions fixed), and rewritten with it when PR #39 merged main: only
    its summary line changed, to explain(result).summary (PR #39)."""
    assert plain_receipt(monkeypatch.setattr) == (FIXTURES / "plain.receipt.json").read_bytes()


def test_a_receipt_with_a_caption_records_it_and_nothing_else(client, tmp_path, claims_db, fixed_clock):
    path = jpeg(tmp_path)
    plain = post_receipt(client, path, caption=CAPTION)
    empty = post_receipt(client, path, caption=CAPTION, deep_run="")
    assert plain.status_code == empty.status_code == 200
    assert plain.content == empty.content and "x-secondlook-picks" not in empty.headers
    rc = json.loads(plain.content)
    assert rc["inputs"] == {"caption": CAPTION}
    built = receipts.build(ladder.run(str(path), rungs=RUNGS, context=Context(caption=CAPTION)), path)
    assert [c["finding"] for c in rc["evidence"]] == [c["finding"] for c in built["evidence"]]
    no_caption = json.loads(post_receipt(client, path).content)
    assert no_caption["inputs"] == {} and "claim" not in [c["rung"] for c in no_caption["evidence"]]
    # /api/check?receipt=1 records it too.
    body = client.post("/api/check?receipt=1", files=upload(path), data={"caption": CAPTION}).json()
    assert body["receipt"]["inputs"] == {"caption": CAPTION}


def test_the_cli_receipt_records_the_caption(tmp_path, claims_db, monkeypatch):
    from secondlook import cli

    path = jpeg(tmp_path)
    monkeypatch.setattr(cli, "run", lambda f, context=None: ladder.run(f, rungs=RUNGS, context=context))
    out = tmp_path / "out"
    assert cli.main(["check", str(path), "--caption", CAPTION, "--receipt", str(out)]) == 0
    rc = json.loads((out / f"{path.name}.receipt.json").read_text())
    assert rc["inputs"] == {"caption": CAPTION}
    out2 = tmp_path / "out2"
    assert cli.main(["check", str(path), "--receipt", str(out2)]) == 0
    assert json.loads((out2 / f"{path.name}.receipt.json").read_text())["inputs"] == {}


def test_deep_run_is_ignored_by_api_check(client, tmp_path, claims_db):
    """Only the receipt routes take a deep check's picks; /api/check is the check the page shows, as before."""
    path = jpeg(tmp_path)
    run = deep_check(client, path)["receipt_run"]
    body = client.post("/api/check", files=upload(path), data={"caption": CAPTION, "deep_run": run,
                                                                "picked_claims": [PICK]}).json()
    assert "claim" not in [e["rung"] for e in body["evidence"]]


# ---- the sample route ---------------------------------------------------------------------------------

def test_the_sample_receipt_takes_a_run_id_for_its_own_file(tmp_path, monkeypatch, claims_db, fixed_clock):
    src = jpeg(tmp_path, "s1.jpg")
    monkeypatch.setattr(sample_catalogue, "CATALOGUE", (sample_catalogue.Sample(
        "01", "s1.jpg", "Title 1", "Kind", "Credit", "CC0", "source", hashlib.sha256(src.read_bytes()).hexdigest()),))
    client = TestClient(create_app(stub=False, rungs=RUNGS, samples_dir=str(tmp_path), investigator=stand_in()))
    plain = client.post("/api/samples/01/receipt?format=json")
    assert plain.status_code == 200 and json.loads(plain.content)["inputs"] == {}, "no fields: as before"
    run = deep_check(client, src)["receipt_run"]
    res = client.post("/api/samples/01/receipt?format=json", data={"caption": CAPTION, "deep_run": run})
    assert res.status_code == 200, res.text
    rc = json.loads(res.content)
    assert rc["inputs"]["picked_claims"]["clauses"] == [PICK]
    assert rc["evidence"][-1]["rung"] == "claim" and rc["evidence"][-1]["raw"]["rating"] == "Pants on Fire"
    forged = client.post("/api/samples/01/receipt?format=json", data={"caption": CAPTION, "picked_claims": [PICK]})
    assert forged.status_code == 200 and "picked_claims" not in json.loads(forged.content)["inputs"]


# ---- the PDF row --------------------------------------------------------------------------------------

def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    return re.sub(r"\s+", " ", " ".join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages))


def _pdf_lines(data: bytes) -> list[str]:
    from pypdf import PdfReader

    return [ln.strip() for p in PdfReader(io.BytesIO(data)).pages for ln in p.extract_text().splitlines()]


def test_the_pdf_shows_the_caption_the_picked_words_their_source_and_the_fact_check(client, tmp_path, claims_db):
    res = picked_receipt(client, jpeg(tmp_path), fmt="pdf")
    assert res.status_code == 200 and res.content.startswith(b"%PDF")
    text = _pdf_text(res.content)
    assert "Inputs given" in text and "Caption" in text and "Claim words" in text
    assert f"“{PICK}”" in text
    assert "Pants on Fire" in text and "picked_claims" not in text, "one plain row, not a JSON dump"
    # Design NIT 1: the source sentence and the lookup line each start their own line.
    lines = _pdf_lines(res.content)
    assert receipts.PICKED_SOURCE in lines
    assert receipts.LOOKUP_LINES["found"] in " ".join(lines)
    assert any(ln.startswith(receipts.LOOKUP_LINES["found"][:40]) for ln in lines)


def test_the_pdf_without_a_caption_or_picks_has_no_inputs_section(client, tmp_path, claims_db):
    text = _pdf_text(post_receipt(client, jpeg(tmp_path), fmt="pdf").content)
    assert "Inputs given" not in text and "Claim words" not in text


# ---- the page -----------------------------------------------------------------------------------------

def test_the_receipt_panel_says_so_in_one_fixed_line_before_the_buttons():
    block = HTML[HTML.index('id="receipt"'):HTML.index('id="meta"')]
    assert block.index('id="receipt-picked"') < block.index('id="receipt-pdf"') < block.index('id="receipt-json"')
    assert '<p class="receipt-picked" id="receipt-picked" hidden></p>' in block
    assert 'aria-describedby="receipt-picked receipt-about"' in block
    # Design MUST 1: one fixed line of 13 words, whatever the clause count.
    assert f'const PICKED_LINE = "{PANEL_LINE}";' in APP
    assert len(PANEL_LINE.split()) == 13
    assert "receiptPicked.textContent = PICKED_LINE;" in APP


def test_the_page_sends_only_the_run_id_of_the_deep_check_on_screen():
    assert "receipt_run: body.receipt_run || null, file: current.file," in INV
    assert "steps: body.steps" not in INV
    fn = APP[APP.index("function receiptPicks"):APP.index("async function downloadReceipt")]
    assert "d.file !== shown.file || d.caption !== shown.caption" in fn
    # a reviewer N1: each deep check on this answer clears the last one's id and line first.
    assert fn.index("shown.deepRun = null;") < fn.index("receiptPicked.hidden = true;") < fn.index("d.receipt_run")
    assert "answer: r.answer, deepRun: null }" in APP
    reset = APP[APP.index("function receiptReset"):APP.index("function receiptPicks")]
    assert "receiptPicked.hidden = true;" in reset
    send = APP[APP.index("async function downloadReceipt"):APP.index("b.addEventListener(\"click\", () => downloadReceipt")]
    assert 'if (of.deepRun) fd.append("deep_run", of.deepRun);' in send
    assert "picked_claims" not in APP
    assert "x-secondlook-picks-reason" in send
    assert "—" not in fn, "no em dashes in page copy"


def test_the_receipt_buttons_are_44_px_tall():
    css = (STATIC / "app.css").read_text()
    rule = css[css.index(".receipt button {"):]
    assert "min-height: 44px" in rule[:rule.index("}")]
