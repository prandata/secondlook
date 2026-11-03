# W10 desk page, part 1 (DAT-850): claim box, voice notes, capability-aware health

Branch `w10-desk` from `main` at `83b2671`. Only what is on main: S6 claim receipts and W9 voice notes. Part 2 (receipt download, R1/DAT-870; Glimmer readings, M1/DAT-869) follows once those merge.

## Endpoints

| Route | What it does |
|---|---|
| `POST /api/claim` `{"text": "..."}` | S6 `lookup_claim` then `to_evidence`. Each match card carries the publisher's rating word for word (`raw.rating`), the publisher (host of the review link; called a fact-checker only when on the IFCN list), the date and the link. Nothing close gives one card, "No existing fact-check found.", with its limit. No index gives HTTP 200 with `status: "not_installed"`, the message "Claims index not installed, so no fact-check was looked up." and a card; an unreadable index gives S6's `unavailable` card. Text is whitespace-folded; empty is 400, over 1,000 characters is 413. |
| `POST /api/voice` (one audio file) | W9 `voice.analyse`: local transcript, candidate claims, the S6 lookup for each. W9's limits: 50 MB (413), and a note over 10 minutes gets W9's `too_long` card, never a cut. Audio suffixes only (415); empty is 400. The upload goes to a private temp dir deleted before the reply. The answer is always `cant_tell`. |
| `GET /api/health` | Adds `capabilities` (`engine`, `claims_index`, `voice`, `receipt`, `glimmer`; `glimmer` is now the real state, from a loopback-only check with a 1 s timeout per request and a 1.5 s deadline for the whole check, W11 stage 2) and `details` (index rows, feed date, source, licence and credit; transcriber, model and decoder). No file paths. |

`engine` is true when the stub runs or at least one image rung loaded. `claims_index` is true when the index file opens read-only and has its tables. `voice` is true when `whisper-cli` is found and the pinned model passes its sha256 check.

The size guard that refuses `/api/check` from its `Content-Length` before any body is read now covers `/api/voice` (50 MB plus 64 KB) and `/api/claim` (16 KB) as well. The server still binds to 127.0.0.1 only, and the CSP is unchanged.

## Page

`static/desk.js` and `static/desk.css`, kept apart from `app.js` and `app.css` so W3's page rewrite (PR #1) merges with a small conflict in `index.html` and `app.py` only. Panels start hidden and are shown only when health says their part can run (fail closed); a line says what is ready and what is not installed. Every card shows "Proves" and "Does not prove" (a blank limit shows a rust "Missing" line). Text written by a model or a publisher is labelled: "Machine transcript, made on this computer by whisper.cpp ... It can mishear words.", "Heard in the machine transcript at 0:00", "Rating, in the publisher's own words", "Claims picked out of the machine transcript by a fixed rule". Review links become links only for `http(s)` URLs, with `rel="noopener noreferrer"`. Accessibility follows W3: a polite status region, `aria-busy` while checking, focus moves to the answer heading, scrolling respects reduced motion, stale replies are dropped, and the voice file input is visually hidden with a focus ring on its drop zone. One answer shows at a time: a claim or voice check hides the image answer and an image check hides the desk answer.

## Tests

`tests/test_desk.py`, 28 tests: the claim endpoint with the index present (verbatim rating, publisher, date, link, credit; unlisted site never called a fact-checker; no-match card), absent (200, `not_installed`) and corrupt (`unavailable`); claim input limits; voice end to end with a stubbed transcript against a synthetic index, with the index absent, and an over-length 16 kHz WAV (601 s) refused with W9's card before whisper runs; voice upload checks; oversize `Content-Length` refused before the body is read on both routes; health capabilities with each part present and missing; the page's hidden panels, labels, link rule and accessibility hooks; no external calls; the banned-word check across the new copy and the cards it produces.

## Captures (headless-run, muted)

Captures made on 28 Oct 2026 and kept outside this repo: `w10-desk-1-panels.png` (health shows both panels), `w10-desk-2-claim.png` ("Drinking coffee causes cancer" against the 28 Oct index: thip.media's "Mostly False", 2023-02-07), `w10-desk-3-voice.png` (a 5 s macOS `say` note, transcribed by whisper.cpp 1.8.4 small.en, same receipt). Server run from this branch with `SECONDLOOK_CLAIMS_DB` pointing at the S6 index.

## Merged with main at `1ec563a` (W3 polish, W8 watermark)

A merge commit, no rebase. `app.py`: both imports (`desk`, `samples`); `/api/health` returns W10's `capabilities` and `details` and W3's `samples` count; `desk.register` sits beside W3's sample routes. `index.html`: W3's skip link, `#check` section, gallery and grouped answer, with W10's capability line and two panels between the image drop and the gallery, and the desk answer above the image answer. One join fix: a sample check is an image check, so a click in the gallery now clears the desk answer as a file pick or drop does. `tests/test_web.py` `PAGE_FILES` now lists `desk.css` and `desk.js`, so W3's stricter banned-word list (no "real", "authentic" or "genuine") and the no-external-call check cover all page copy. Suite on Python 3.12 with every extra: 444 passed, 3 skipped (the S6 real-index tests).

# W10 desk page, part 2 (DAT-850): the caption box and the Muse Glimmer investigator

Branch `w10-desk-2` from `main` at `e24ad30`. Both APIs were already on main (X1's `caption` field on `/api/check`, DAT-872; M2's `/api/investigate`, DAT-873); this part puts them on the page. The receipt download waits for R1 (PR #4) and comes in part 3.

## Caption box

Under the image drop, in the check form: a labelled textarea, sent as `caption` with the next image you drop or choose. The page counts characters as the server does (trimmed, by code point) and shows "N of 2,000 characters." Over 2,000, the error shows beside the box ("That caption is over 2,000 characters. Shorten it to check the image with it."), the box gets `aria-invalid`, and nothing is sent. A server refusal that names the caption is shown beside the box too. There is no `maxlength`, so a long paste is never cut silently. "Check the same image again with this caption" reruns the last file you chose. Sample checks take no caption (the hint says so). The context rung's cards show under the tag "Caption context", with the rest of the evidence.

## Investigator panel ("Deep check")

Under every image answer. The button "Investigate with Muse Glimmer (runs locally)" posts the same file and caption the answer came from to `/api/investigate`. The page shows only what M2 returns, as text:

- the answer, labelled as Second Look's fixed rules over every card found;
- `explanation_label` and the `explanation_lines` (M2's template lines, E1's when installed);
- the checks it called: each step's tool, whether the model or the rules asked for it, refused or not, its seconds, and its template `result`;
- the model's readings under M2's own label ("model reading, not evidence"): the printed words, and the place and device in the caption's words. The date is shown as the calendar day code parsed from the caption, unquoted, because it is not the caption's wording;
- any card the deep check found that the answer above does not already list, each with "Proves" and "Does not prove".

While it runs: "Running for N s", updated each second in a `role="timer"` (not announced every second), with the measured range from M2's smoke run (53 to 126 s an image) and its 300 s limit. The button keeps focus (`aria-disabled`, not `disabled`), the panel has `aria-busy`, a polite status line announces start and end, focus moves to the note's answer when it is done, and a reply for an older check is dropped.

Not set up: `/api/investigate` now adds `glimmer: "unavailable"` when no Glimmer server answered (the note's problem line starts with `investigator.UNAVAILABLE`), else `"ran"`. The page then says "Muse Glimmer is not set up on this computer, so there was no deep check." and gives README's two commands (`scripts/fetch_glimmer.sh`, `scripts/glimmer_server.sh start`) with the size of Meta's two pinned files (16,756,683,904 and 1,400,328,928 bytes, about 18 GB; the fetch needs 40 GB free). On the demo stub (409) it says the page runs the demo stub and adds `python -m secondlook.web`.

The line that stays true: "Muse Glimmer runs on this computer. Second Look reaches it only at a loopback address (127.0.0.1), so the image and caption you send it stay on this computer." `glimmer.loopback_url` refuses any other URL, and a test pins both the sentence and that refusal. The page shows that line, and the live button, only when `/api/health` says `capabilities.glimmer` is true. Until health answers it says "Looking for Muse Glimmer on this computer." with no button; when health says nothing listens there (`details.glimmer.reason` is `not_running`) it says "Muse Glimmer is not running on this computer, so the deep check cannot run here." and shows no live button (judge persona test, 30 Oct, DAT-850). For any other no (another model answers, a slow server, an earlier check still running, or health failing or passing 6 s) it says "Second Look could not confirm Muse Glimmer on this computer just now, so the deep check cannot run here.", again with no live button (A228).

## Access and style

Labels on every control; keyboard order drop, caption, "check again", then the claim and voice panels; focus rings on every new control; `aria-live` results; reduced motion respected. Colours and type are app.css's. Every new text colour meets WCAG AA (4.5:1), checked by a test. The page's rust on cream is 4.37:1, so the caption error uses a darker rust, #9E4526 (5.54:1). The existing `#status.error` (rust on cream, from W3) is the same 4.37:1 and is left as it is for the project lead. New files: `static/investigate.js` and `static/investigate.css`, added to `PAGE_FILES` so the banned-word and no-external-call checks cover them.

## Tests

`tests/test_desk_part2.py`: the caption box's label, description, keyboard order and missing `maxlength`; page and API limits agree; a caption that disagrees with the file's EXIF make comes back as a `context` card with `contradiction: true` and the answer `concerns`; an over-length caption is refused with a message naming the caption, and 2,000 two-byte characters pass; the panel's place, button text, live region, timer and focus target; the loopback line and `loopback_url`'s refusals; setup commands match README; the script posts the same file and caption and writes with `textContent` only; `glimmer` is `unavailable` with nothing listening and `ran` with a model; the stub's 409; focus and contrast.

## Captures (headless-run, muted)

`docs/W10-SHOTS/` in this repo (captured on 29 Oct 2026), desktop 1280 px, full page, real ladder with the 28 Oct S6 index, 0 requests off 127.0.0.1 in every run (`w10-part2-capture-A.json`, `-B.json`):

- `w10-part2-1-empty.png`: the page before a check.
- `w10-part2-1b-caption-over-limit.png`: 2,001 characters, the error beside the box, nothing sent.
- `w10-part2-2-caption-concern.png`: a test JPEG whose EXIF says Canon EOS R5, with the caption "Taken on an iPhone 15 Pro in Paris, 2026-10-01": "Concerns found", card 4 "Caption context".
- `w10-part2-3-investigator-running.png` and `w10-part2-4-investigator-done.png`: `docs/M2-SHOTS/shot-3-gatlinburg.png` with its post text as the caption, Glimmer from `scripts/glimmer_server.sh start` on Meta's pinned GGUF. 5 checks called. PolitiFact's "False" review was found, and the answer stayed "Can't tell".
- `w10-part2-5-investigator-not-set-up.png`: no Glimmer server; `w10-part2-5b-investigator-stub-409.png`: the demo stub.

States 3 and 4 were rendered at `810e246`, the others at `4e7a649`; the only page change between them is the unnumbered list for M2's lines, which those states do not show.

## Found on the way, for their lanes

- X1 (`extract.find_device`): "Shot on an iPhone 15 Pro outside the town hall last night." keeps no device, and "Shot on an iPhone 15 Pro last night." keeps "iPhone 15 Pro last night". Both are misses, not false concerns.
- M2 (`STEP_TEMPLATES["context_check.agree"]`): on the Gatlinburg screenshot the step says "Compared the caption's date and place with the file's own facts and found no disagreement." while the card says nothing could be compared (no capture time, no GPS). The step line would be clearer as "nothing to compare" in that case.
