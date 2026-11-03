"""Claim receipts: look a typed claim up in an offline copy of the Data Commons
ClaimReview feed and return what fact-checkers have already published.

Second Look gives no true or false verdict of its own here. It returns the
publisher's own rating word for word, with the publisher's name, the publication
date and the link. A publisher is called a fact-checker only if its site is on the
IFCN list in factcheckers.py; any other site's card says "published by <site>".
The default answer is "no existing fact-check found".

Feed: https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json
Licence: the compilation is CC BY (https://datacommons.org/factcheck/download).
Each publisher keeps authorship of its review; credit it wherever a match is shown.

Usage:
    python -m secondlook.rungs.claim --build feed.json claims.sqlite
    python -m secondlook.rungs.claim --db claims.sqlite "a typed claim"
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from secondlook.rungs.factcheckers import DOMAINS as FACT_CHECKER_DOMAINS
from secondlook.types import Evidence

log = logging.getLogger(__name__)

NO_MATCH_MESSAGE = "No existing fact-check found."
UNAVAILABLE_MESSAGE = "Claim lookup is not available, so no fact-check was looked up."
DB_ENV = "SECONDLOOK_CLAIMS_DB"
DEFAULT_DB = Path.home() / ".cache" / "secondlook" / "claims.sqlite"

# Matching thresholds. A wrong match shown as "a fact-check of your claim" does
# more harm than a miss, so these err towards "no existing fact-check found".
MIN_SHARED_TERMS = 2      # content words the query and the reviewed claim must share
ALL_TERMS_UP_TO = 6       # a query of this many content words or fewer needs every one in the claim
MIN_QUERY_COVERAGE = 0.85 # longer queries: share of their content words found in the claim
MIN_CLAIM_COVERAGE = 0.7  # share of the reviewed claim's content words found in the query, so a
                          # long, different claim that merely contains the typed words is not a match
MAX_LIMIT = 50
UNNAMED = "(unnamed fact-checker)"   # stored by build_index when the feed gives no author name
_NEG_PREFIXES = ("un", "in", "im", "ir", "il", "non", "dis")
RATING_SHOWN_CHARS = 120  # longer rating text is cut in the one-line finding (kept whole in raw)
CANDIDATES = 60           # BM25 candidates fetched before re-ranking
DEFAULT_LIMIT = 5

_STOP = frozenset(
    "a an and are as at be been but by for from had has have he her his i in is it its "
    "of on or she that the their there they this to was were will with you your we our "
    "not no so if than then these those who whom what which when where why how do does did "
    "says said say after before about into over under also just very can could would should".split()
)
_NEGATION = frozenset("not no never nor cannot without".split())
_TOKEN = re.compile(r"\w+", re.UNICODE)


def negated(text: str) -> bool:
    """True if the text carries a negation word. Negations are dropped from the match
    terms, so a claim and its opposite can match; this flag lets the card say so."""
    words = set(_TOKEN.findall(text.lower().replace("\u2019", "'")))
    return bool(words & _NEGATION) or any(w.endswith("n't") for w in text.lower().replace("\u2019", "'").split())


def _prefix_negation(q: set[str], c: set[str]) -> bool:
    """True if the reviewed claim holds a word that is a query word with a negating prefix
    (effective / ineffective), a difference the negation word list cannot see."""
    return any(p + t in c for t in q for p in _NEG_PREFIXES) or any(p + t in q for t in c for p in _NEG_PREFIXES)


def terms(text: str) -> list[str]:
    """Lower-cased content words. Accents are folded, so "Zelensky" and "Zelenský" agree."""
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return [t for t in _TOKEN.findall(folded) if t not in _STOP and len(t) > 1]


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split())}
_NUMBER_WORDS |= {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                  "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
                  "million": "1000000", "billion": "1000000000", "trillion": "1000000000000"}


def _fold(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in folded if not unicodedata.combining(c))


def named_things(text: str) -> set[str]:
    """Words written with a capital letter or in capitals (people, organisations, places, months,
    acronyms), folded like terms(). A text written wholly in capitals says nothing about names,
    so it gives none. Stop words are left out. The first word counts: it is often the subject."""
    words = _TOKEN.findall(text)
    if not any(c.islower() for c in text):
        return set()
    out = set()
    for w in words:
        if w[0].isalpha() and w[0].isupper() and len(w) > 1:
            f = _fold(w)
            if f not in _STOP:
                out.add(f)
    return out


def numbers_in(text: str) -> set[str]:
    """Numbers in a text, as digits ("5,000" and "5000" agree; "two" and "2" agree)."""
    out = {m.replace(",", "") for m in _NUMBER.findall(text)}
    out |= {_NUMBER_WORDS[w] for w in _TOKEN.findall(text.lower()) if w in _NUMBER_WORDS}
    return out


def same_subject(typed: str, uq: set[str], reviewed: str, ct: set[str]) -> bool:
    """False if the typed claim and the reviewed claim differ on a name or a number, whatever
    their other overlap: a capitalised name in one that is not among the other's words, or
    a number in one that the other lacks. "Biden said X" is not a review of "Trump said X"."""
    return (not (named_things(reviewed) - uq) and not (named_things(typed) - ct)
            and numbers_in(typed) == numbers_in(reviewed))


@dataclass
class ClaimMatch:
    claim_reviewed: str          # the claim as the publisher wrote it
    rating: str | None           # the publisher's rating word, verbatim (None if none published)
    rating_explanation: str | None
    publisher: str               # the site that published the review: the host of the review link
    publisher_url: str | None
    is_listed_fact_checker: bool # the site is on the IFCN list in factcheckers.py
    listed_as: str | None        # the author name the feed lists; not always the publisher
    review_url: str
    date_published: str | None
    claimed_by: str | None       # who made the claim, as the publisher recorded it
    claim_date: str | None
    negation_differs: bool       # the typed claim and the reviewed claim differ on negation
    similarity: float            # the lower of the two coverages below, 0 to 1
    shared_terms: int            # content words the typed and reviewed claims share
    typed_terms: int             # content words in the typed claim
    reviewed_terms: int          # content words in the reviewed claim
    raw: dict = field(default_factory=dict)


@dataclass
class ClaimLookup:
    status: str                  # "found", "none" (looked, nothing close) or "unavailable" (could not look)
    message: str
    query: str
    matches: list[ClaimMatch]
    feed: dict                   # provenance of the offline copy: source, date, licence
    note: str = (
        "Matches are by wording. Each is a published rating of the claim it names, "
        "which may differ from the claim typed here. Second Look gives no true or "
        "false verdict of its own."
    )


def _text(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _row(it: dict) -> dict | None:
    """One ClaimReview item as a flat row, or None if it lacks a claim or a link."""
    claim = _text(it.get("claimReviewed"))
    url = _text(it.get("url"))
    if not claim or not url:
        return None
    rating = it.get("reviewRating") or {}
    author = it.get("author") or {}
    reviewed = it.get("itemReviewed") or {}
    claimant = reviewed.get("author") or {}
    return {
        "claim": claim,
        "url": url,
        "rating": _text(rating.get("alternateName")),
        "rating_explanation": _text(rating.get("ratingExplanation")),
        "rating_value": None if rating.get("ratingValue") is None else str(rating["ratingValue"]),
        "best_rating": None if rating.get("bestRating") is None else str(rating["bestRating"]),
        "worst_rating": None if rating.get("worstRating") is None else str(rating["worstRating"]),
        "checker": _text(author.get("name")) or "(unnamed fact-checker)",
        "checker_url": _text(author.get("url")),
        "published": _text(it.get("datePublished")),
        "claimant": _text(claimant.get("name")) if isinstance(claimant, dict) else None,
        "claim_date": _text(reviewed.get("datePublished")) if isinstance(reviewed, dict) else None,
    }


def build_index(feed_path: str | Path, db_path: str | Path, *, source: str = "", downloaded_at: str = "",
                sha256: str = "") -> int:
    """Build the local SQLite FTS5 index from the downloaded feed. Returns rows indexed."""
    with open(feed_path, encoding="utf-8") as f:
        feed = json.load(f)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(db_path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(
        """
        CREATE TABLE review(id INTEGER PRIMARY KEY, claim TEXT, url TEXT, rating TEXT,
            rating_explanation TEXT, rating_value TEXT, best_rating TEXT, worst_rating TEXT,
            checker TEXT, checker_url TEXT, published TEXT, claimant TEXT, claim_date TEXT,
            nterms INTEGER);
        CREATE VIRTUAL TABLE fts USING fts5(terms, tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        """
    )
    seen: set[tuple[str, str]] = set()
    n = 0
    for el in feed.get("dataFeedElement", []):
        for it in el.get("item") or []:
            row = _row(it)
            if row is None or (row["url"], row["claim"]) in seen:
                continue
            seen.add((row["url"], row["claim"]))
            t = terms(row["claim"])
            if not t:
                continue
            n += 1
            con.execute(
                "INSERT INTO review VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (n, row["claim"], row["url"], row["rating"], row["rating_explanation"],
                 row["rating_value"], row["best_rating"], row["worst_rating"], row["checker"],
                 row["checker_url"], row["published"], row["claimant"], row["claim_date"], len(set(t))),
            )
            con.execute("INSERT INTO fts(rowid, terms) VALUES(?,?)", (n, " ".join(t)))
    meta = {
        "source": source or "https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json",
        "licence": "CC BY (compilation), https://datacommons.org/factcheck/download; "
                   "each review remains the fact-checker's own work",
        "feed_name": str(feed.get("name") or ""),
        "feed_date_modified": str(feed.get("dateModified") or ""),
        "downloaded_at": downloaded_at,
        "feed_sha256": sha256,
        "rows": str(n),
    }
    con.executemany("INSERT INTO meta VALUES(?,?)", list(meta.items()))
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    return n


def _db_path(db) -> Path:
    return Path(db or os.environ.get(DB_ENV) or DEFAULT_DB)


def _host(url: str | None) -> str | None:
    """Host of a URL without a leading "www.", or None."""
    if not isinstance(url, str):
        return None
    try:
        host = urlparse(url.strip()).hostname
    except ValueError:
        return None
    return host[4:] if host and host.startswith("www.") else host


def _limit(limit) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_LIMIT
    return min(max(n, 1), MAX_LIMIT)   # at least 1: a limit of 0 must not turn matches into "none"


def listed_fact_checker(host: str | None) -> bool:
    """True if the host, or a parent domain of it, is on the IFCN list in factcheckers.py."""
    if not host:
        return False
    parts = host.lower().split(".")
    return any(".".join(parts[i:]) in FACT_CHECKER_DOMAINS for i in range(len(parts) - 1))


def lookup_claim(claim: str, db: str | Path | None = None, limit: int = DEFAULT_LIMIT) -> ClaimLookup:
    """Find published fact-checks whose reviewed claim closely matches the typed claim.

    Never raises, whatever the arguments. Status "none" means the index was read and holds
    nothing close; "unavailable" means it could not be read (missing, corrupt, bad path).
    """
    query = claim if isinstance(claim, str) else ""
    feed: dict = {}
    try:
        n_limit = _limit(limit)
        uq = set(terms(query))
        try:
            path = _db_path(db)
        except (TypeError, ValueError, OSError) as e:
            log.warning("claim index path unusable: %s", e)
            return ClaimLookup("unavailable", UNAVAILABLE_MESSAGE, query, [], feed)
        if not path.is_file():
            log.warning("claim index not found at %s; build it with --build", path)
            return ClaimLookup("unavailable", UNAVAILABLE_MESSAGE, query, [], feed)
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            con.row_factory = sqlite3.Row
            feed = {k: v for k, v in con.execute("SELECT key, value FROM meta")}
            if len(uq) < MIN_SHARED_TERMS:
                return ClaimLookup("none", NO_MATCH_MESSAGE, query, [], feed)
            match = " OR ".join(f'"{t}"' for t in sorted(uq))
            cands = con.execute(
                "SELECT r.*, f.terms AS fterms FROM fts f JOIN review r ON r.id = f.rowid "
                "WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (match, CANDIDATES),
            ).fetchall()
        finally:
            con.close()
        return _rank(query, uq, cands, n_limit, feed)
    except Exception as e:  # a rung must never take the caller down
        log.warning("claim lookup failed: %s", e)
        return ClaimLookup("unavailable", UNAVAILABLE_MESSAGE, query, [], feed)


def _rank(query: str, uq: set[str], cands, limit: int, feed: dict) -> ClaimLookup:
    scored = []
    for r in cands:
        ct = set(r["fterms"].split())
        shared = len(uq & ct)
        qcov, ccov = shared / len(uq), shared / max(len(ct), 1)
        if shared < MIN_SHARED_TERMS or ccov < MIN_CLAIM_COVERAGE:
            continue
        if qcov < (1.0 if len(uq) <= ALL_TERMS_UP_TO else MIN_QUERY_COVERAGE):
            continue
        if not same_subject(query, uq, r["claim"], ct):
            continue
        host = _host(r["url"]) or _host(r["checker_url"])
        scored.append((qcov + ccov, qcov, ccov, shared, ct, r, host, listed_fact_checker(host)))
    # Listed fact-checkers first, then the closest wording, then the oldest review.
    scored.sort(key=lambda s: (not s[7], -s[0], s[5]["published"] or ""))
    matches = []
    for _, qcov, ccov, shared, ct, r, host, is_listed in scored[:limit]:
        checker_url = r["checker_url"]
        listed = None if r["checker"] == UNNAMED else r["checker"]
        matches.append(ClaimMatch(
            claim_reviewed=r["claim"], rating=r["rating"], rating_explanation=r["rating_explanation"],
            publisher=host or "an unidentified site", publisher_url=checker_url,
            is_listed_fact_checker=is_listed,
            listed_as=listed, review_url=r["url"],
            date_published=r["published"], claimed_by=r["claimant"], claim_date=r["claim_date"],
            negation_differs=negated(query) != negated(r["claim"]) or _prefix_negation(uq, ct - uq),
            similarity=round(min(qcov, ccov), 3), shared_terms=shared, typed_terms=len(uq),
            reviewed_terms=len(ct),
            raw={"rating_value": r["rating_value"], "best_rating": r["best_rating"],
                 "worst_rating": r["worst_rating"]},
        ))
    if not matches:
        return ClaimLookup("none", NO_MATCH_MESSAGE, query, [], feed)
    n, n_listed = len(matches), sum(m.is_listed_fact_checker for m in matches)
    if n_listed == n:
        msg = "1 existing fact-check found." if n == 1 else f"{n} existing fact-checks found."
    elif n_listed == 0:
        msg = ("1 published review found, from a site not on the IFCN list." if n == 1
               else f"{n} published reviews found, from sites not on the IFCN list.")
    else:
        msg = f"{n} published reviews found, {n_listed} from sites on the IFCN list."
    return ClaimLookup("found", msg, query, matches, feed)


def _quote(s: str | None, cap: int | None = None) -> str:
    if not s:
        return "no rating word published"
    if cap and len(s) > cap:
        s = s[:cap].rstrip() + " [...]"
    return f"“{s}”"


def to_evidence(lookup: ClaimLookup, *, picked: bool = False) -> list[Evidence]:
    """One Evidence card per match, in the shared contract's shape. An unavailable lookup
    gives one card saying so, so a missing index is never shown as "no fact-check found".
    picked=True: a model picked the search words (glimmer.lookup_candidate), so the cards call them
    "the picked words", never "your" words (DAT-850 design review, SHOULD 2)."""
    words = "the picked words" if picked else "your claim"
    if lookup.status == "unavailable":
        return [Evidence(
            rung="claim",
            finding=UNAVAILABLE_MESSAGE,
            proves="Nothing: the offline fact-check index could not be read.",
            does_not_prove="Whether a fact-check of this claim exists. Second Look gives no true or "
                           "false verdict of its own.",
            strength="none",
            raw={"status": "unavailable", "feed": lookup.feed},
        )]
    out = []
    for m in lookup.matches:
        when = f" on {m.date_published}" if m.date_published else " (publication date not given in the feed)"
        listed = f" The feed lists the author as “{m.listed_as}”." if m.listed_as else ""
        warn = (f" Careful: it differs from {words} on negation, so read the claim they reviewed."
                if m.negation_differs else "")
        quoted, rating = _quote(m.claim_reviewed, RATING_SHOWN_CHARS), _quote(m.rating, RATING_SHOWN_CHARS)
        if m.is_listed_fact_checker:
            finding = f"{m.publisher} reviewed the claim {quoted} and rated it {rating}{when}: {m.review_url}{listed}{warn}"
            who = f"That {m.publisher}, a fact-checker on the IFCN list, published this rating"
            not_who = ""
        else:
            # Not on the list: name the site as the publisher only, never as a fact-checker.
            finding = f"Published by {m.publisher}: a review of the claim {quoted} that rates it {rating}{when}: " \
                      f"{m.review_url}{listed}{warn}"
            who = f"That {m.publisher} published this rating"
            not_who = "Who runs the site or how it checks its claims, which Second Look has not looked into. "
        out.append(Evidence(
            rung="claim",
            finding=finding,
            proves=f"{who} on a claim that shares "
                   + (f"{m.shared_terms} of the {m.typed_terms} key words in the picked words" if picked
                      else f"{m.shared_terms} of your {m.typed_terms} key words")
                   + f" and is {m.reviewed_terms} key words "
                   "long itself. It is not necessarily the claim you are checking.",
            does_not_prove=not_who + "That the words searched for are the claim they reviewed, or that the rating "
                           "still holds. Second Look gives no true or false verdict of its own.",
            strength="none",
            raw=dataclasses.asdict(m) | {"feed": lookup.feed},
        ))
    return out


def check_claim(claim: str, db: str | Path | None = None, limit: int = DEFAULT_LIMIT) -> list[Evidence]:
    """Rung entry point for a typed claim: [] when no existing fact-check is found."""
    return to_evidence(lookup_claim(claim, db, limit))


def check(path: str) -> list[Evidence]:
    """Contract signature. This rung reads a typed claim, not an image file, so it
    returns [] for a path. Use check_claim(text)."""
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="secondlook.rungs.claim")
    ap.add_argument("--build", nargs=2, metavar=("FEED_JSON", "DB"), help="build the index from a downloaded feed")
    ap.add_argument("--downloaded-at", default="", help="UTC time the feed was downloaded (recorded in the index)")
    ap.add_argument("--sha256", default="", help="sha256 of the downloaded feed (recorded in the index)")
    ap.add_argument("--source", default="", help="the URL the feed was downloaded from (recorded in the index); "
                                                 "default the moving `latest` URL")
    ap.add_argument("--db", help=f"index path (default ${DB_ENV} or {DEFAULT_DB})")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("claim", nargs="?", help="the claim to look up")
    a = ap.parse_args(argv)
    if a.build:
        n = build_index(a.build[0], a.build[1], source=a.source, downloaded_at=a.downloaded_at,
                        sha256=a.sha256)
        print(json.dumps({"indexed": n, "db": a.build[1]}))
        return 0
    if not a.claim:
        ap.error("give a claim to look up, or --build")
    print(json.dumps(dataclasses.asdict(lookup_claim(a.claim, a.db, a.limit)), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
