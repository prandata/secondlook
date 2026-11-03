# S6 claim receipts: the offline feed

Lane S6 (DAT-851), stage 1.

| | |
|---|---|
| Source | https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json (linked from https://datacommons.org/factcheck/download) |
| Downloaded | 2026-10-28T01:18:35Z (12:18 AEDT), one download |
| Feed `dateModified` | 2026-10-28T01:15:35Z |
| Size and hash | 200,805,798 bytes; sha256 `80755795cad78e0d880627bd37ce647e19a895f11c3bb1105fc7ae6c5ee92b81` |
| Licence | The compilation is CC BY (stated on the download page). Each ClaimReview has its own `sdLicense` field; none of the 100,369 items in this download carries one. Publishers may also have their own terms. |
| Attribution | Credit Data Commons and name the fact-checker on every match shown. Each review stays the fact-checker's own work. |
| Contents | 94,025 feed elements, 100,369 ClaimReview items, 100,101 unique (url, claim), 99,368 indexed (items with a claim and a link). All languages; no language filter. |
| Where it lives | Kept outside the repo (feed JSON and `claims.sqlite`, 72 MB). Not in git. |

Rebuild the index (7 s):

    python -m secondlook.rungs.claim --build claimreview-data.json claims.sqlite \
        --downloaded-at 2026-10-28T01:18:35Z --sha256 <hash above>

Point the rung at it with `SECONDLOOK_CLAIMS_DB` or `--db`. The index records the source, licence, download time and hash in its `meta` table, and every lookup returns them.

## How matching works (stage 1)

Local text matching only. SQLite FTS5 (BM25) over accent-folded content words fetches 60 candidates. A candidate is kept only if it shares at least 2 content words with the typed claim, **and** the typed claim's content words are all found in it (queries of 6 content words or fewer) or at least 85% of them (longer queries), **and** at least 70% of the reviewed claim's own content words are in the typed claim, so a long, different claim that merely contains the typed words is not shown, **and** the two claims do not differ on a name or a number (below). The default is "No existing fact-check found." (status `none`).

If the index cannot be read (missing, corrupt, a directory, an unusable `db` argument), the status is `unavailable` and the message is "Claim lookup is not available, so no fact-check was looked up." `check_claim` then returns one card saying so, never "no fact-check found". Bad `db` or `limit` arguments never raise.

### Names and numbers must agree (F3)

Word overlap alone let a long claim with one word swapped pass (a reviewer: "Trump" changed to "Biden" in 198 of 198 PolitiFact claims of 7 to 10 key words still returned the Trump review). A match is now dropped, whatever the overlap, if

- the reviewed claim has a word written with a capital letter (a person, organisation, place, month or acronym, including the first word of the claim) that is not among the typed claim's words, or the typed claim has one that is not among the reviewed claim's words; case is not compared, so typing `trump` matches `Trump`. A typed claim written wholly in capitals gives no names, because capitals then say nothing;
- the numbers in the two claims differ. Digits and spelled numbers (zero to twenty, the tens, hundred, thousand, million, billion, trillion) are compared as numbers, so `5,000` agrees with `5000` and `two` with `2`. A number in only one claim counts as a difference.

This is deliberately blunt: a claim that omits a name the reviewed claim has ("the plan failed" against "Trump said the plan failed") is also not shown. A miss is preferred to a review of another person's claim. The names are found from capitals, so a typed claim in lower case is only checked from the reviewed side (its capitalised names must appear in what was typed).

### Who published the review (N5)

The publisher shown is the host of the review link (`politifact.com`), because the feed's `author.name` is sometimes the claimant or blank. The card calls the publisher a **fact-checker** only if that host, or a parent domain of it, is on the list in `secondlook/rungs/factcheckers.py`. Any other host's card reads "Published by <site>: a review of the claim ... that rates it ..." and never uses the word fact-checker; its `does_not_prove` says Second Look has not looked into who runs the site. Listed publishers sort first, then the closest wording.

The list is the IFCN's own signatory list: https://ifcncodeofprinciples.poynter.org/signatories, read 28 Oct 2026 from the JSON the page loads (226 organisations). Kept: status "Verified Signatory" or "In Renewal" (182 sites after reducing each organisation's `website` to its host); "Expired Signatory" is left out. Regenerate with `scripts/build_factchecker_domains.py` (by hand, needs network). Limits:

- It lists the organisation's whole site, so a listed news site (for example `washingtonpost.com`) labels all its reviews as fact-checks; every ClaimReview item from such a site is a fact-check by that site, but the IFCN lists the desk, not every page.
- A genuine fact-checker on a domain the IFCN does not list (a changed domain, a non-signatory) shows as "Published by", which is the safe side. In the 28 Oct index about 79% of rows come from listed sites; the largest unlisted hosts include `boatos.org`, `lupa.uol.com.br` (the IFCN lists `lupa.news`), `logicallyfacts.com` and `rumorscanner.com`. Adding such a domain needs its own source; none was added from memory.
- The unlisted publishers the review found (`service.essayhave.com`, `*.blogspot.com`) are on no list and now read "Published by".

Bad `limit` values are clamped to between 1 and 50, so a limit of 0 or below shows the closest match instead of a false "No existing fact-check found." (F2).

The feed's own name is shown as "The feed lists the author as ...". A review with no `datePublished` says "publication date not given in the feed". The card's `proves` line states the number of shared key words, not that the claim is "worded closely".

No embedding model yet. Negations ("not", "no", "never") are not match terms, so a claim and its opposite can still match. Such a match carries `negation_differs` (also set when the reviewed claim holds a prefixed form such as in-/un- of a typed word) and its card says so; the card always quotes the claim the publisher reviewed next to the rating.

## Informal measures on the real index (28 Oct, not a gate)

Fix round 3 (DAT-851, names and numbers, publisher label), same index, one script run on e4c918b and on the fix (probe output kept in the review evidence, outside this repo):

- a reviewer's subject swap (PolitiFact claims of 7 to 10 key words naming Trump, "Trump" replaced by "Biden", seed 7, 198 swaps): the original Trump review came back for 198 before and for **0** after, and no card at all came back for 197 before and 0 after.
- Control, 60 unchanged claims of the same set: each finds its own review, 60 of 60, before and after.
- 150 random ASCII claims of 3 or more key words (seed 7, a different sample from the round-2 figures below), typed as in the feed: 150 of 150 find their own review, before and after.
- The same 150 with every fourth word dropped: the original is among the matches in 132 before and **64** after. The drop is the cost of treating a missing name or number as a difference: when the dropped word is a capitalised name or a number, the card is no longer shown. It is the miss-over-wrong-match trade the matcher already makes. If it proves too costly, the rule can be relaxed to block only a name that is swapped (one claim has a name the other lacks and the reverse), which still gives 0 of the 198.

Earlier figures, after the S6 fix round (DAT-851), same index, rerun the same day (before round 3):

- 300 random English claims (seed 7) from the feed, queried as typed: 298 found, 2 not.
- Same claims with every fourth word dropped: the original is among the matches in 272 of 300, and 28 give "No existing fact-check found." (the stricter match trades some recall for fewer wrong matches).
- 20 short statements I picked, including the 6 that a reviewer found returning reviews of other claims (Biden 2020, Earth orbits the Sun, COVID vaccines effective, Great Wall, Amazon oxygen, Climate change is real): 6 returned a card, 14 returned none. Reading each card's reviewed claim, 0 of the 6 review a different claim (for example "Coffee causes cancer" shows a review of "drinking coffee causes cancer"). Before the fix 8 returned a card and 6 of those reviewed a different claim (a reviewer). This is a hand check on 20 statements, not a rate.
- About 36 ms per query.

These show the matcher finds a review whose wording it is given and that the six known wrong matches are gone. They say nothing about how often a user's real claim has an existing fact-check; the feed holds what fact-checkers chose to publish.

## Contract notes for the lead

- `check(path)` returns `[]`: this rung reads typed text, not an image. The entry point is `check_claim(text)` (list of `Evidence`, `[]` when nothing found) or `lookup_claim(text)` (a `ClaimLookup` with the "no existing fact-check found" message and the feed provenance).
- `Evidence.strength` is `"none"` for a match: a fact-checker's rating of a similar claim is not metadata or a signal about the file. If a card should read differently, that is a contract change for S1.
