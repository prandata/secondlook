# W11 design pass, stage 1 (DAT-850): the six MUST findings

Branch `w11-design-1` from `w10-desk-2` at `2c72c7c` (PR #20). The designer check is a review comment in the private review queue (a reviewer, `FIX @ 2c72c7c`). This stage does the six MUST items only. The SHOULD items and the receipt download (once R1 lands) are stage 2.

## What changed, by finding

1. **The answer first.** Once an answer is on screen (image, sample, claim or voice note), the inputs fold into one bar, "`<file>` · Check another". The bar is scrolled to the top, so the answer sits right under it (`scrollIntoView({block: "start"})`; reduced motion is respected). "Check another" opens the inputs again and moves focus to the file input. The claim box and voice note sit in a secondary row, "Or check something else", under the image drop and its caption. `desk.js` sends `secondlook:answered` and `app.js` folds the inputs, so all four kinds of answer fold the same way.
2. **A "Published fact-check found" strip** sits under the headline when a matched fact-check is among the answer's cards or the deep check's. It gives the rating word for word in quotes, then "Rating in the publisher's own words:", the publisher, whether the publisher is on the IFCN list, the claim they reviewed, the date (2024-10-03 shows as "3 Oct 2024") and a link, "Read it at politifact.com". Only `http(s)` URLs become links, with `rel="noopener noreferrer"` and `overflow-wrap: anywhere`. The card's own "Does not prove" line is under it. The headline stays the provenance answer ("Can't tell"). When the deep check finds one, focus moves to the strip and the status line says where it is.
3. **One list, one row per clue.** Each row has the check and its strength, the clue in bold, any trust-list line, then "Proves" and "Does not prove" side by side (stacked under 560 px). A clue shows when its strength is not "none" or the ladder counts it as a concern. The rest fold into a `<details>`, "Checked, nothing found (N)", and every row there keeps its limit. If no clue has a signal, the list says "None of the checks found a signal in this file." Three groups of 12 rows became one list.
4. **The concern headline** is worded by the ladder's own count: "One reason to look closer", "Two reasons to look closer" and so on. It uses the same size and ink colour as "Can't tell", with rust kept to a 72 px underline. `/api/check` now returns `concern_cards`, the indexes of the cards `ladder._is_concern` counts, so the page never re-derives the rule. The ladder summary now says "1 concern found" or "N concerns found", not "1 concern(s) found".
5. **The page counts its checks once and shows the score once.**
   - `/api/check` (and the sample and batch routes) now report as `rungs` the checks that answer actually ran. The caption context check counts only when a caption was sent; before, it was listed even when no caption was sent. `/api/health` still lists every check that can run.
   - The page says the count in one place ("6 checks ran: C2PA, IPTC, EXIF, Classifier, Watermark, Caption context.").
   - The plain-language box keeps its summary sentence. Its per-card lines are no longer repeated beside the rows, which carried a second wording of the same score ("under 0.01 out of 1" beside "0.000") and a second count.
   - The deep check shows M2's summary line once. Its step lines appear only under "Checks it called", where they were already listed.
   - The deep check's cards from a check the answer already ran (its rerun of the file checks, which reports 5 checks because the context check runs as its own step) fold into "The same checks, run again by the deep check (N)".
   - M2's "found no disagreement" step wording is left alone; claims-fixes-1 (A17) fixes it.
6. **The image drop is the hero.** It is at least 220 px tall, with a 1.5 px dashed edge in a tinted `#B9AD98` (no longer near-black 2 px) and an accent fill on hover and drag-over. The claim and voice tools have no card box. The voice note is a text-style button ("Choose or drop a voice note") with no dashed box inside a card. The claim and caption fields have a 1 px line border, and "Look it up" is an outline button, so the image drop is the one loud thing. The deep check's cards are rows under a rule, not boxes inside its box.

Kept: "Can't tell" as a full answer and the word "real" nowhere (banned-word tests over every page file). A "Does not prove" line on every row, folded or not, and in the strip; a blank limit still shows the rust "Missing" line. The loopback line and its test are unchanged.

## Tests

`tests/test_w11_design.py`, 12 tests, one or more per finding:

- the bar, the intake wrapper and what folds into it, and the claim and voice answers folding too;
- the strip's place, rating, link rule and limit, from both sources;
- the single list, the folded "nothing found" rows and the side-by-side layout;
- the headline worded by count and in ink;
- the pluralised ladder summary;
- `concern_cards` from `/api/check`;
- `rungs` without and with a caption, through the rung registry, and health unchanged;
- no per-card repeat and no deep-check repeat;
- the hero drop zone and no box in a box.

Three older tests changed with the layout. `test_web.py`: the three group names became "What we found" with "Proves" and "Does not prove", and the `render(...)` call takes `r.concern_cards`; one source slice now ends at `showExplanation`. `test_desk.py`: the scroll moved to `app.js`, which keeps the reduced-motion rule. `test_desk_part2.py`: the investigator still sits under the evidence list (`clues`).

## Captures (headless-run, muted)

The shots are in `docs/W11-SHOTS/`. They were taken with `docs/W11-SHOTS/capture.mjs` through `~/bin/headless-run`, from this worktree's page files at `509b33b`. The server ran the real ladder with the 28 Oct S6 index (99,368 reviews), and states 3 and 4 used Glimmer from `scripts/glimmer_server.sh start`. No request went off 127.0.0.1 (`w11-capture.json`; `blob:` preview URLs are local and are not counted). Each state has a full-page shot at 1280 px and at 390 px. States 1, 2 and 4 also have a `-first-screen` shot, the first 1280 x 800 screen.

| State | Before (`docs/W10-SHOTS/`, `2c72c7c`, 1280 px) | After (`docs/W11-SHOTS/`, 1280 px) |
|---|---|---|
| 1 empty | `w10-part2-1-empty.png`, 1035 px tall | `w11-1-empty-desktop.png`, 1125 px |
| 1b caption over the limit | `w10-part2-1b-caption-over-limit.png`, 1134 px | `w11-1b-caption-over-limit-desktop.png`, 1224 px |
| 2 caption concern | `w10-part2-2-caption-concern.png`, 3354 px, answer about 930 px down | `w11-2-caption-concern-desktop.png`, 1806 px, answer at the top under the bar |
| 3 deep check running | `w10-part2-3-investigator-running.png`, 3431 px | `w11-3-investigator-running-desktop.png`, 1394 px |
| 4 deep check done | `w10-part2-4-investigator-done.png`, 5052 px, PolitiFact at about y=4700 | `w11-4-investigator-done-desktop.png`, 2571 px, PolitiFact's "False" under the headline |
| 5 Glimmer not set up | `w10-part2-5-investigator-not-set-up.png`, 3549 px | `w11-5-investigator-not-set-up-desktop.png`, 2003 px |
| 5b demo stub (409) | `w10-part2-5b-investigator-stub-409.png`, 2446 px | `w11-5b-investigator-stub-409-desktop.png`, 1491 px |

The empty and over-limit states are about 90 px taller because the image drop is now the hero. In state 4 the deep check took 58 s this run (the first run today took 71 s), well inside M2's measured 53 to 126 s range. The 1b caption is now realistic pasted text rather than a run of "x".

Two fixes came from looking at the first take:

- **The headline lost its focus ring.** The answer heading takes focus (tabindex -1) so screen-reader and keyboard users land on it. It is not a control, so the rust ring round it is gone.
- **The double rule is gone.** The bar no longer draws a rule above the answer's own rule.

## Fix round (a reviewer's re-check at `148b255`, review comment in the private review queue)

The re-check gave FIX: MUSTs 1 to 4 and 6 were fixed, and two things were still open. The first was a new MUST, N1: "One reason to look closer" never said what the reason was. The second was what was left of item 5. This round does those only. The SHOULD list (N2 to N5, items 7 to 16) waits for stage 2, after R1 merges.

- **N1, the headline names its reason.** A line under "One reason to look closer" quotes the first concern card's finding word for word: "The caption says iPhone 15 Pro. The file's own EXIF camera make says Canon, model Canon EOS R5." With several concerns, it shows the first and then "And N more reasons, listed first below." The line comes from the cards in `concern_cards`, so the page uses the ladder's count and does not work it out again. The general meaning sentence under it steps down to grey, normal weight, so the reason reads first. Screen readers hear the reason with the answer.
- **N1, concern rows first.** The rows the ladder counts as concerns now come first in "What we found", marked with a rust outline tag, "Reason to look closer". The other rows with a signal follow in check order. Before this round, the context row was third, at about y=1180 of 1806. Now it is the first row, and its tag is on the first 1280 x 800 screen.
- **Item 5, two counts with names.** The answer's line reads "This answer ran 6 checks on the file: …". The deep check reads "Muse Glimmer called 5 of Second Look's checks, listed under "Checks Muse Glimmer called"", and that heading now carries the count, "Checks Muse Glimmer called (5)". The old wording was "6 checks ran" against "5 checks called".
- **Item 5, the "can't tell" sentence once.** Before this round, screen 4 said it three times: the ladder's summary ("We can't tell how this image was made…"), the plain-language box ("Second Look can't tell how this was made…") and the deep check's desk note (the same box sentence).
  - The ladder's summary line now hides when the plain-language box is shown, because the two are the same answer worded twice. The demo stub keeps its line, because that line says it is a demo.
  - The desk note leaves out its summary line when it matches the box above word for word, and its "written by" label goes with it.
  - The capture counts "can't tell how" in the page text: 1 on screen 4. The ladder's summary is still in the API response and in every receipt.

Claims rules hold. The word "real" appears nowhere: `test_web.py`'s banned-word check covers every page file. Every row, folded or not, still has its "Does not prove" line, and so does the fact-check strip. The loopback line and its test are unchanged.

Tests: `tests/test_w11_design.py` has 5 new tests: the reason line and its place, concern rows first and marked, the reason quoting the context card the ladder counted (through `/api/check`), the two counts named apart, and the sentence shown once. Two older tests in the same file follow the new wording.

Captures: `docs/W11-SHOTS/`, re-taken from `468a69c` (the page files in this round) with `capture.mjs` through `~/bin/headless-run`, muted. The setup was the same as before: the real ladder with the 28 Oct S6 index, and Glimmer from `scripts/glimmer_server.sh start` for states 3 and 4. 0 requests went off 127.0.0.1 (`w11-capture.json`). The first take's `street-canon.jpg` was not saved, so `docs/W11-SHOTS/make_inputs.py` now makes the inputs: a drawn skyline JPEG whose EXIF names a Canon EOS R5, and the M2 Gatlinburg screenshot. The empty and over-limit shots came out byte-identical.

| State | `148b255` (1280 px) | This round (1280 px) |
|---|---|---|
| 2 caption concern | 1806 px; the reason is the third row, at about y=1180 | 1820 px; the reason is under the headline (y=449) and in the first row |
| 3 deep check running | 1394 px | 1326 px |
| 4 deep check done | 2571 px; "can't tell how" 3 times | 2470 px; once |
| 5 Glimmer not set up | 2003 px | 2018 px |
| 5b demo stub (409) | 1491 px | 1491 px |

In state 4 the deep check took 187 s this run (model time 121 s), which is slower than M2's measured 53 to 126 s. Glimmer had just been started cold, and the full test suite was running on the same Mac at the time. The page's own expectation line still quotes M2's measured range.

## Fix round 2 (a reviewer's FAIL at `73b2d61`, review comment in the private review queue)

Two findings, both fixed. Nothing else changed.

- **AA contrast on cream.** The brand rust `#B4532F` is 4.37:1 on the cream page `#F4F0E6`, under AA's 4.5:1 for small text. The "Reason to look closer" tag and the "Missing: …" text used it. `app.css` now has one token, `--rust-text: #9E4526` (5.54:1 on cream, 5.21:1 on the `#EFE9DB` panels), and every small rust text uses it. That covers the two the review named, plus the header badge, the error lines (`#status`, `#desk-status`, `#inv-status`), the warn line under the answer, the sample numbers, and the limit labels and "Missing" text on the desk and deep-check cards. The caption box's local copy of the token went to the root. The brand rust stays on fills, rules, borders and the three large headlines (`.answer.signed_ai`, `.desk-answer.found`, `.dcard .rating`, bold 24 px and up, where AA asks 3:1).
  - The test reads every rule in `app.css`, `desk.css` and `investigate.css` that sets a text colour and no fill of its own: 65 selectors. Each must reach 4.5:1 on cream and on `#EFE9DB`, or 3:1 on cream for the three large headlines. Run against the old CSS, it fails on `.badge` (4.37:1).
  - Not changed, as the review said: cream text on a rust fill (`.tag.strength.signed`, `.demo-tag`, the `#again-go` and `#inv-go` hover) is also 4.37:1. These sit on their own fill, not on cream, and are not new in this PR. They are listed for stage 2.
- **`rungs` lists only the checks that ran.** `ladder.run_traced` returns the result with the ladder's own split, the rungs that ran and those that raised; `ladder.run` returns the same result and keeps its contract. `/api/check`, the samples and `/api/batch` now set `rungs` to the checks that ran and `rungs_failed` to those that raised. Before, `rungs` was the list loaded for the request, so a raising rung was counted. The response carries the split on every answer, not only on "can't tell", where the limit card already had it. The page counts only the checks that ran and then names the failed ones plainly, in the warn colour: "This answer ran 5 checks on the file: C2PA, IPTC, EXIF, Classifier, Caption context. 1 check could not run: Watermark." If none ran, it says "No check could run on this file." and names them.

Tests: `tests/test_w11_design.py` has 8 new tests. Three cover contrast: the token values and ratios, the two named selectors resolving to `#9E4526`, and every text colour on the cream ground. Five cover the split: a raising rung on `/api/check` with and without a caption and on `/api/batch`; a raising rung beside a concern, which has no limit card; the real watermark rung with its weights path pointing at no file; `run_traced` against `run`; and the page's wording. Against the old `app.py`, the raising-rung and watermark tests fail. `test_desk_part2.py` checks the token at the root.

Captures: `docs/W11-SHOTS/`, every state re-taken from `1de70f3` with the same setup (the real ladder with the 28 Oct S6 index, Glimmer from `scripts/glimmer_server.sh start` for states 3 and 4), through `~/bin/headless-run`, muted. 0 requests went off 127.0.0.1. Two states are new:

- **2b, a check could not run.** The real ladder, with `SECONDLOOK_VIDEOSEAL_WEIGHTS` pointing at no file, on the caption concern. The computed colour of the warn line, the reason tag and the badge is `rgb(158, 69, 38)`, which is `#9E4526`.
- **2c, the "Missing" text.** The same answer, with the EXIF card's `does_not_prove` blanked in the browser by a route handler, because no rung leaves it empty. The server is not changed. The computed colour is `rgb(158, 69, 38)`.

| State (1280 px) | Height |
|---|---|
| 2 caption concern | 1820 px |
| 2b a check could not run (new) | 1804 px |
| 2c "Missing" text (new) | 1782 px |
| 4 deep check done | 2493 px |

In state 4 the deep check took 126 s this run, inside M2's measured 53 to 126 s.
