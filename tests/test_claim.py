"""Tests for the claim rung, on a small synthetic ClaimReview feed (not real reviews)."""
import json
import os
from pathlib import Path

import pytest

from secondlook.rungs import claim
from secondlook.types import Evidence


def _item(text, url, rating, checker="Example Checker", date="2026-01-02", checker_url="https://checker.example/"):
    it = {
        "@type": "ClaimReview",
        "author": {"@type": "Organization", "name": checker, "url": checker_url},
        "claimReviewed": text,
        "datePublished": date,
        "url": url,
        "reviewRating": {"@type": "Rating", "alternateName": rating},
        "itemReviewed": {"@type": "Claim", "author": {"@type": "Person", "name": "A viral post"},
                         "datePublished": "2026-01-01"},
    }
    if checker is None:
        del it["author"]["name"]
    if date is None:
        del it["datePublished"]
    return it


FEED = {
    "@type": "DataFeed", "name": "test", "dateModified": "2026-10-28T00:00:00Z",
    "dataFeedElement": [{"@type": "DataFeedItem", "item": [i]} for i in [
        _item("The mayor of Springfield was hospitalised after a sudden illness.", "https://c.example/1", "False"),
        _item("Vaccines cause autism.", "https://c.example/2", "False", "Health Desk"),
        _item("Vaccines do not cause autism.", "https://c.example/3", "True", "Other Desk"),
        _item("Photo shows a purple elephant crossing the Harbour Bridge.", "https://c.example/4",
              "Fabricated: " + "long explanation " * 40),
        _item("Dup claim about river levels and flood warnings this week.", "https://c.example/5", "Mostly true"),
        _item("Dup claim about river levels and flood warnings this week.", "https://c.example/5", "Mostly true"),
        _item("No rating word here but a claim about bridge toll prices rising.", "https://c.example/6", None),
        # near misses: other claims that share some of the typed words
        _item("Sunscreen causes cancer.", "https://c.example/8", "False"),
        _item("The Great Wall of China was built by the Tartarian Empire.", "https://c.example/9", "False"),
        _item("I won that second election in 2020.", "https://c.example/10", "Pants on Fire"),
        _item("Covid-19 vaccines are ineffective.", "https://c.example/11", "False"),
        # a different name or number in a claim that otherwise overlaps strongly
        _item("A banner with a swastika, Trump and Pence is from a Michigan coronavirus protest.",
              "https://c.example/12", "False"),
        _item("The county reported 4000 new jobs created during the 2020 harvest season.",
              "https://c.example/13", "Mostly True"),
        _item("Unemployment fell to 3.5 percent in March across the whole country.",
              "https://c.example/14", "True"),
        # a site that is on the IFCN list and one that is not
        _item("Vaccines cause autism.", "https://www.politifact.com/factchecks/2026/jul/02/vaccines/", "Pants on Fire",
              checker="PolitiFact", checker_url="https://www.politifact.com/"),
        _item("Moon landing footage was filmed in a studio in Nevada.", "https://service.essayhave.com/moon-1",
              "Fake", checker="Essay Have", checker_url="https://service.essayhave.com/"),
        # the feed's author name is the claimant, not the fact-checker
        _item("Senator Smith voted against the harbour dredging budget last June.",
              "https://www.politifact.com/factchecks/2026/jun/01/smith/", "Mostly True",
              checker="Senator Smith", checker_url="https://www.politifact.com/"),
        # no author name, no date
        _item("Council confirms the ferry timetable will change from next month.",
              "https://www.pesacheck.org/ferry-123", "Misleading", checker=None, date=None,
              checker_url="https://pesacheck.org/"),
        {"@type": "ClaimReview", "claimReviewed": "", "url": "https://c.example/7"},
        {"@type": "ClaimReview", "claimReviewed": "No url so no receipt"},
    ]] + [{"@type": "DataFeedItem", "item": None}],
}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("claims")
    feed = d / "feed.json"
    feed.write_text(json.dumps(FEED), encoding="utf-8")
    out = d / "claims.sqlite"
    n = claim.build_index(feed, out, downloaded_at="2026-10-28T01:18:16Z", sha256="abc")
    assert n == 17   # duplicate, empty-claim, no-url and null-item rows dropped
    return out


def test_exact_claim_returns_rating_verbatim_with_credit_date_and_link(db):
    r = claim.lookup_claim("The mayor of Springfield was hospitalised after a sudden illness", db)
    assert r.status == "found"
    m = r.matches[0]
    assert m.rating == "False"
    assert (m.publisher, m.listed_as, m.date_published, m.review_url) == (
        "c.example", "Example Checker", "2026-01-02", "https://c.example/1")
    assert r.feed["downloaded_at"] == "2026-10-28T01:18:16Z" and "CC BY" in r.feed["licence"]


def test_short_paraphrase_still_matches(db):
    assert claim.lookup_claim("mayor Springfield hospitalised sudden illness", db).status == "found"


def test_unrelated_claim_gives_default_none(db):
    q = "The moon is made of green cheese and everyone knows it"
    r = claim.lookup_claim(q, db)
    assert r.status == "none" and r.message == claim.NO_MATCH_MESSAGE and r.matches == []
    assert claim.check_claim(q, db) == []


@pytest.mark.parametrize("q", ["", "   ", "hello", "the and of", None, 42])
def test_bad_or_thin_input_never_raises(db, q):
    assert claim.lookup_claim(q, db).status == "none"


def test_missing_index_is_unavailable_not_none(tmp_path):
    r = claim.lookup_claim("anything at all about a topic", tmp_path / "nope.sqlite")
    assert r.status == "unavailable" and r.message == claim.UNAVAILABLE_MESSAGE != claim.NO_MATCH_MESSAGE
    ev = claim.check_claim("anything at all about a topic", tmp_path / "nope.sqlite")
    assert len(ev) == 1 and ev[0].finding == claim.UNAVAILABLE_MESSAGE and ev[0].raw["status"] == "unavailable"


def test_corrupt_index_or_directory_is_unavailable(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database" * 100)
    assert claim.lookup_claim("anything at all about a topic", bad).status == "unavailable"
    assert claim.lookup_claim("anything at all about a topic", tmp_path).status == "unavailable"


def test_index_path_with_uri_characters_still_opens(db, tmp_path):
    odd = tmp_path / "a b?c#d%20e"
    odd.mkdir()
    link = odd / "claims.sqlite"
    link.symlink_to(db)
    assert claim.lookup_claim("The mayor of Springfield was hospitalised after a sudden illness", link).status == "found"


@pytest.mark.parametrize("bad_db", [42, b"/x", "a" * 5000, "bad\x00path", 3.5, ["x"]])
def test_bad_db_argument_never_raises(bad_db):
    assert claim.lookup_claim("anything at all about a topic", bad_db).status == "unavailable"


@pytest.mark.parametrize("bad_limit", [None, "3", "x", -1, 0, 1e400, 10**30, [1], True])
def test_bad_limit_never_raises(db, bad_limit):
    r = claim.lookup_claim("vaccines cause autism", db, bad_limit)
    assert r.status == "found"   # exact matches exist, so no limit may turn them into "none" (F2)
    assert r.message != claim.NO_MATCH_MESSAGE and r.matches
    assert claim.lookup_claim("vaccines cause autism", db, "1").status == "found"
    assert len(claim.lookup_claim("vaccines cause autism", db, "1").matches) == 1


@pytest.mark.parametrize("small", [0, -1, -10**30, "0"])
def test_limit_of_zero_or_below_still_shows_one_match(db, small):
    r = claim.lookup_claim("vaccines cause autism", db, small)
    assert r.status == "found" and len(r.matches) == 1 and claim._limit(small) == 1
    assert len(claim.check_claim("vaccines cause autism", db, small)) == 1


def test_limit_none_uses_default(db):
    assert claim.lookup_claim("vaccines cause autism", db, None).status == "found"


@pytest.mark.parametrize("typed", [
    "Coffee causes cancer",                         # reviewed: Sunscreen causes cancer
    "The Great Wall of China is in China",          # reviewed: ...built by the Tartarian Empire
    "Biden won the 2020 election",                  # reviewed: "I won that second election in 2020"
    "COVID vaccines are effective",                 # reviewed: Covid-19 vaccines are ineffective
    "Sunscreen causes skin damage",
])
def test_a_different_claim_is_never_shown_as_close(db, typed):
    r = claim.lookup_claim(typed, db)
    assert r.status == "none" and r.message == claim.NO_MATCH_MESSAGE and r.matches == []


BANNER = "A banner with a swastika, {} and Pence is from a Michigan coronavirus protest"


def test_same_long_claim_matches_and_a_swapped_name_does_not(db):
    assert claim.lookup_claim(BANNER.format("Trump"), db).status == "found"
    # 6 of 7 key words are shared, which used to pass, but the name differs (F3)
    for typed in (BANNER.format("Biden"), BANNER.format("biden").lower()):
        r = claim.lookup_claim(typed, db)
        assert r.status == "none" and r.matches == [] and r.message == claim.NO_MATCH_MESSAGE
    assert claim.lookup_claim(BANNER.format("trump").lower(), db).status == "found"   # case is not a difference


def test_a_different_number_is_not_a_match(db):
    base = "The county reported 4000 new jobs created during the 2020 harvest season"
    assert claim.lookup_claim(base, db).status == "found"
    assert claim.lookup_claim(base.replace("2020", "2021"), db).status == "none"
    assert claim.lookup_claim(base.replace("4000", "400"), db).status == "none"
    assert claim.lookup_claim(base.replace("4000", "4,000"), db).status == "found"
    # numbers that never make a key word (3.5 is not a word of two letters) still count
    unemployment = "Unemployment fell to 3.5 percent in March across the whole country"
    assert claim.lookup_claim(unemployment, db).status == "found"
    assert claim.lookup_claim(unemployment.replace("3.5", "4.5"), db).status == "none"


@pytest.mark.parametrize("typed, reviewed, same", [
    ("Biden said the plan failed", "Trump said the plan failed", False),
    ("the plan failed, Obama said", "the plan failed", False),                 # a name only the typed claim has
    ("the plan failed", "Trump said the plan failed", False),                  # a name only the reviewed claim has
    ("trump said the plan failed", "Trump said the plan failed", True),
    ("TRUMP SAID THE PLAN FAILED", "Trump said the plan failed", True),        # capitals carry no name information
    ("Zelenský said the plan failed", "Zelensky said the plan failed", True),
    ("two hundred jobs", "200 jobs", False),                                   # "hundred" is 100, so 2 and 100 are not 200
    ("two jobs lost", "2 jobs lost", True),
    ("5,000 jobs lost", "5000 jobs lost", True),
    ("5,000 jobs lost", "5,001 jobs lost", False),
])
def test_same_subject_rules(typed, reviewed, same):
    assert claim.same_subject(typed, set(claim.terms(typed)), reviewed, set(claim.terms(reviewed))) is same


def test_long_review_that_only_contains_the_typed_words_is_not_a_match(db):
    assert claim.lookup_claim("Great Wall China", db).status == "none"


def test_proves_states_only_the_shared_words(db):
    ev = claim.check_claim("mayor Springfield hospitalised sudden illness", db)
    assert ev and "shares 5 of your 5 key words" in ev[0].proves
    assert "worded closely" not in ev[0].proves and "not necessarily the claim you are checking" in ev[0].proves


def test_card_text_is_true_whoever_chose_the_words(db):
    """The words searched for may be typed, read from a caption or picked by a model, so the card
    must not say the user typed them (the hero case's words come from a caption)."""
    ev = claim.check_claim("mayor Springfield hospitalised sudden illness", db)
    assert ev
    for e in ev:
        for text in (e.proves, e.does_not_prove):
            assert "typed" not in text and "you typed" not in text
        assert e.does_not_prove.endswith("That the words searched for are the claim they reviewed, or that the "
                                         "rating still holds. Second Look gives no true or false verdict of its own.")


def test_unlisted_publisher_is_named_as_publisher_never_as_fact_checker(db):
    ev = claim.check_claim("Moon landing footage was filmed in a studio in Nevada", db)
    assert len(ev) == 1
    e = ev[0]
    assert e.finding.startswith("Published by service.essayhave.com: a review of the claim")
    assert e.raw["publisher"] == "service.essayhave.com" and e.raw["is_listed_fact_checker"] is False
    for text in (e.finding, e.proves, e.does_not_prove):
        assert "fact-check" not in text.lower()
    assert "“Fake”" in e.finding and "https://service.essayhave.com/moon-1" in e.finding
    r = claim.lookup_claim("Moon landing footage was filmed in a studio in Nevada", db)
    assert "fact-check" not in r.message and "IFCN list" in r.message


def test_listed_site_is_a_fact_checker_and_comes_first(db):
    r = claim.lookup_claim("vaccines cause autism", db)
    assert r.matches[0].publisher == "politifact.com" and r.matches[0].is_listed_fact_checker
    assert [m.is_listed_fact_checker for m in r.matches] == [True, False, False]
    assert "2 from sites on the IFCN list" not in r.message and "1 from sites on the IFCN list" in r.message
    ev = claim.to_evidence(r)
    assert ev[0].finding.startswith("politifact.com reviewed the claim") and "on the IFCN list" in ev[0].proves
    assert ev[1].finding.startswith("Published by c.example")


def _count_db(tmp_path, n, host):
    """A small index holding n reviews of one claim, all from one publisher (listed on the IFCN list or not)."""
    items = [_item("Harbour ferries will be free for every commuter next winter.", f"https://{host}/ferry-{i}",
                   "False", checker=f"Checker {i}", checker_url=f"https://{host}/") for i in range(n)]
    feed = {"@type": "DataFeed", "name": "count", "dateModified": "2026-10-28T00:00:00Z",
            "dataFeedElement": [{"@type": "DataFeedItem", "item": [i]} for i in items]}
    (tmp_path / "feed.json").write_text(json.dumps(feed), encoding="utf-8")
    out = tmp_path / "claims.sqlite"
    assert claim.build_index(tmp_path / "feed.json", out, downloaded_at="2026-10-28T01:18:16Z", sha256="abc") == n
    return out


@pytest.mark.parametrize("n, host, expected", [
    (1, "www.politifact.com", "1 existing fact-check found."),
    (2, "www.politifact.com", "2 existing fact-checks found."),
    (1, "reviews.example", "1 published review found, from a site not on the IFCN list."),
    (2, "reviews.example", "2 published reviews found, from sites not on the IFCN list."),
])
def test_count_headline_is_worded_by_count_never_with_a_bracketed_s(tmp_path, n, host, expected):
    r = claim.lookup_claim("Harbour ferries will be free for every commuter next winter", _count_db(tmp_path, n, host))
    assert r.status == "found" and len(r.matches) == n
    assert r.message == expected
    assert "(s)" not in r.message


@pytest.mark.parametrize("host, listed", [
    ("politifact.com", True), ("www.politifact.com", True),
    ("en.youturn.in", True), ("factcheck.afp.com", True),
    ("notpolitifact.com", False), ("politifact.com.evil.example", False), ("com", False),
    ("service.essayhave.com", False), ("somechecker.blogspot.com", False), ("facebook.com", False),
    ("", False), (None, False),
])
def test_listed_fact_checker_matches_whole_domains_only(host, listed):
    assert claim.listed_fact_checker(host) is listed


def test_factchecker_list_is_sourced_dated_and_holds_no_platforms():
    from secondlook.rungs import factcheckers as f
    assert f.SOURCE.startswith("https://ifcncodeofprinciples.poynter.org/") and f.READ_ON == "2026-10-28"
    assert len(f.DOMAINS) > 100 and all(d == d.lower() and "." in d and "/" not in d for d in f.DOMAINS)
    assert not [d for d in f.DOMAINS if d.split(".")[-2:] in (["facebook", "com"], ["blogspot", "com"],
                                                               ["youtube", "com"], ["twitter", "com"])]
    assert {"politifact.com", "factcheck.org", "africacheck.org"} <= set(f.DOMAINS)


def test_fact_checker_named_from_the_review_url_not_the_claimant(db):
    ev = claim.check_claim("Senator Smith voted against the harbour dredging budget last June", db)
    assert len(ev) == 1
    assert ev[0].finding.startswith("politifact.com reviewed the claim")
    assert ev[0].raw["publisher"] == "politifact.com" and ev[0].raw["listed_as"] == "Senator Smith"
    assert "The feed lists the author as “Senator Smith”" in ev[0].finding


def test_unnamed_fact_checker_is_named_from_its_url(db):
    ev = claim.check_claim("Council confirms the ferry timetable will change from next month", db)
    assert len(ev) == 1 and ev[0].finding.startswith("pesacheck.org reviewed the claim")
    assert "unnamed" not in ev[0].finding and ev[0].raw["listed_as"] is None


def test_missing_date_is_said_plainly(db):
    ev = claim.check_claim("Council confirms the ferry timetable will change from next month", db)
    assert "publication date not given in the feed" in ev[0].finding and ev[0].raw["date_published"] is None
    ev = claim.check_claim("The mayor of Springfield was hospitalised after a sudden illness", db)
    assert "on 2026-01-02" in ev[0].finding and "date not given" not in ev[0].finding


def test_negation_flagged_not_hidden(db):
    r = claim.lookup_claim("vaccines cause autism", db)
    by_url = {m.review_url: m for m in r.matches}
    assert by_url["https://c.example/2"].negation_differs is False
    assert by_url["https://c.example/3"].negation_differs is True
    ev = {e.raw["review_url"]: e for e in claim.to_evidence(r)}
    assert "differs from your claim on negation" in ev["https://c.example/3"].finding
    assert "Vaccines do not cause autism." in ev["https://c.example/3"].finding   # the reviewed claim is shown


def test_evidence_follows_contract_and_never_gives_a_verdict(db):
    ev = claim.check_claim("Photo purple elephant crossing the Harbour Bridge", db)
    assert ev and all(isinstance(e, Evidence) and e.rung == "claim" for e in ev)
    for e in ev:
        assert e.proves and e.does_not_prove and e.strength == "none"
        assert "no true or false verdict" in e.does_not_prove
        assert "[...]" in e.finding and len(e.finding) < 600   # long rating cut in the line...
        assert e.raw["rating"].startswith("Fabricated: ") and len(e.raw["rating"]) > 500  # ...whole in raw


def test_missing_rating_word_is_said_plainly(db):
    ev = claim.check_claim("No rating word here claim about bridge toll prices rising", db)
    assert ev and "no rating word published" in ev[0].finding and ev[0].raw["rating"] is None


def test_check_by_path_returns_empty():
    assert claim.check("/no/such/image.jpg") == []


def test_unicode_accents_fold():
    assert claim.terms("Zelenský Résigned") == claim.terms("Zelensky Resigned")


@pytest.mark.skipif(not os.environ.get(claim.DB_ENV) or not Path(os.environ[claim.DB_ENV]).exists(),
                    reason="needs the real feed index (SECONDLOOK_CLAIMS_DB)")
def test_real_feed_smoke():
    r = claim.lookup_claim("Vaccines cause autism")
    assert r.status == "found" and all(m.rating and m.review_url and m.publisher for m in r.matches)
    assert all(set(claim.terms("Vaccines cause autism")) <= set(claim.terms(m.claim_reviewed)) for m in r.matches)
    assert claim.lookup_claim("Quartz marmalade orbits sideways tomorrow afternoon").status == "none"
    # reviews of other claims that share some of these words (luciferase, magnets) are not shown
    for m in claim.lookup_claim("COVID-19 vaccines contain microchips").matches:
        assert "microchip" in m.claim_reviewed.lower()
    assert claim.lookup_claim("Biden won the 2020 election").status == "none"


@pytest.mark.skipif(not os.environ.get(claim.DB_ENV) or not Path(os.environ[claim.DB_ENV]).exists(),
                    reason="needs the real feed index (SECONDLOOK_CLAIMS_DB)")
def test_real_feed_subject_swap_returns_no_card_for_the_original():
    """a reviewer's probe: PolitiFact claims of 7 to 10 key words naming Trump, with Trump swapped for Biden.
    Before the F3 fix the original review came back for all 198 of the 198 swaps a reviewer ran (the full probe is in the S6 notes)."""
    import re
    import sqlite3
    con = sqlite3.connect(Path(os.environ[claim.DB_ENV]).resolve().as_uri() + "?mode=ro", uri=True)
    rows = con.execute("SELECT claim, url FROM review WHERE nterms BETWEEN 7 AND 10 AND claim LIKE '%Trump%' "
                       "AND url LIKE '%politifact%' ORDER BY id LIMIT 80").fetchall()
    con.close()
    swapped = [(re.sub(r"\bTrump\b", "Biden", c), u) for c, u in rows if re.search(r"\bTrump\b", c)]
    assert len(swapped) > 60
    for typed, url in swapped:
        assert url not in [m.review_url for m in claim.lookup_claim(typed).matches], typed
    # the unchanged claim still finds its own review
    assert sum(url in [m.review_url for m in claim.lookup_claim(c).matches] for c, url in rows[:30]) >= 27


@pytest.mark.skipif(not os.environ.get(claim.DB_ENV) or not Path(os.environ[claim.DB_ENV]).exists(),
                    reason="needs the real feed index (SECONDLOOK_CLAIMS_DB)")
def test_real_feed_limit_zero_still_reports_matches():
    assert claim.lookup_claim("Vaccines cause autism", limit=0).status == "found"
