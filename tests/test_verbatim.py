"""Verbatim checks on what a model takes from a caption: whole clauses and ClaimFacts values (DAT-869)."""
import pytest

from secondlook.verbatim import normalise, split_clauses, verbatim_facts, verbatim_value, whole_clause


@pytest.mark.parametrize("caption, clauses", [
    ("BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. Pentagon is "
     "under attack. Stay safe everyone",
     ["BREAKING: Explosion near the Pentagon, it is under attack",
      "Photo shows the U.S. Pentagon is under attack", "Stay safe everyone"]),
    ("Biden signed the bill. Trump did not sign it.", ["Biden signed the bill", "Trump did not sign it"]),
    ("It cost 3.5 million; see example.com for more", ["It cost 3.5 million", "see example.com for more"]),
    ("Dr. Smith took it in Washington D.C. on Monday. Wow?! Really...",
     ["Dr. Smith took it in Washington D.C. on Monday", "Wow", "Really"]),
    ("first line\nsecond line\r\nthird", ["first line", "second line", "third"]),
    ('He said “it is fake.” Nobody believed him', ["He said “it is fake", "Nobody believed him"]),
    ("Photo shows Gatlinburg, Tennessee, flooded on Sept. 30, 2024. Stay safe",       # M2: month short forms
     ["Photo shows Gatlinburg, Tennessee, flooded on Sept. 30, 2024", "Stay safe"]),
    ("Image shows the Hollywood sign was on fire as of Jan. 8.", ["Image shows the Hollywood sign was on fire as of Jan. 8"]),
    ("no boundary at all", ["no boundary at all"]),
    (" ;; . ", []),
])
def test_split_clauses(caption, clauses):
    text = normalise(caption)
    got = split_clauses(text)
    assert [c.text for c in got] == clauses
    for c in got:                                       # every clause is the caption's own text
        assert text[c.start:c.end] == c.text


def test_normalise_keeps_line_breaks_and_drops_control_characters():
    assert normalise("  a\tb \u0007 c \r\n  d  ") == "a b c\nd"


def test_whole_clause():
    clauses = split_clauses("Biden signed the bill. Trump did not sign it.")
    assert whole_clause("Trump did not sign it", clauses).text == "Trump did not sign it"
    assert whole_clause("  Trump did not sign it.  ", clauses).text == "Trump did not sign it"
    for bad in ("Trump did not sign", "trump did not sign it", "Biden did not sign the bill",
                "Biden signed the bill. Trump did not sign it", "", "Trump  did not sign it"):
        assert whole_clause(bad, clauses) is None, bad


CAPTION = "Taken with my iPhone 15 Pro in Paris, France on 28 Oct 2026. Nobody edited it"


@pytest.mark.parametrize("value, kept", [
    ("iPhone 15 Pro", "iPhone 15 Pro"),
    ("Paris, France", "Paris, France"),
    (' "Paris" ', "Paris"),
    ("28 Oct 2026", "28 Oct 2026"),
    ("Paris", "Paris"),
    ("paris", None),                     # case changed
    ("Pari", None),                      # cuts a word
    ("iPhone 15 Pro Max", None),         # adds a word
    ("2026-10-28", None),                # reformatted: code parses the date, the model must not
    ("Lyon", None),                      # invented
    ("2026. Nobody", None),              # crosses a clause boundary
    ("", None), ("   ", None), (None, None), (15, None),
])
def test_verbatim_value(value, kept):
    assert verbatim_value(value, CAPTION) == kept


def test_verbatim_facts_keeps_only_values_written_in_the_caption():
    kept, problems = verbatim_facts(CAPTION, {"date_text": "28 Oct 2026", "place": "Paris, France",
                                              "device": "iPhone 15 Pro"})
    assert kept == {"date_text": "28 Oct 2026", "place": "Paris, France", "device": "iPhone 15 Pro"}
    assert problems == []
    kept, problems = verbatim_facts(CAPTION, {"date_text": "2026-10-28", "place": "Paris, France",
                                              "device": "", "rating": "real"})
    assert kept == {"date_text": None, "place": "Paris, France", "device": None}
    assert problems == ["unknown field: rating", "date_text is not written in the caption"]
    assert verbatim_facts(CAPTION, ["Paris"]) == ({"date_text": None, "place": None, "device": None},
                                                  ["reply is not an object of facts"])
    kept, problems = verbatim_facts(CAPTION, {"place": ["Paris"]})
    assert kept["place"] is None and problems == ["place is not written in the caption"]
