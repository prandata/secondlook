# W11 design pass, stage 2 (DAT-850): the gaps from the 29 Oct showcase recording

Branch `w11-design-2`, from `w11-design-1` at `dfe273f` (PR #22, stage 1). The showcase recording at `dfe273f` (kept outside this repo) found five gaps. Each is fixed below. The receipt's own wording (`secondlook/receipt.py`) is not changed.

## What changed

1. **Download receipt.** The answer view has a "Download receipt" row under the evidence, with a PDF button and a JSON button. It sits above the version line. Its one line of copy: "A record of this check to keep with a story: the file's sha256, the answer, and every card with what it does not prove. Making it checks the file again. It carries no signature." The line matches docs/CLAIMS.md, which says the receipt "is not signed and proves nothing about the picture".
   - **For a file you drop or choose**, the page posts the same file and caption to `POST /api/receipt`. `?format=json` is new and returns the canonical JSON receipt, the same bytes `secondlook check --receipt` writes. The optional `caption` field is new too: without it, a receipt for an answer that used a caption would have come from a different check. The receipt does not record the caption, and neither does the CLI's.
   - **For a sample**, the page posts to the new `POST /api/samples/{id}/receipt`, which checks the sample file in place, as `/api/samples/{id}/check` does.
   - **Each download checks the file again.** Nothing from the first check is kept on the server. The receipt's answer comes back in the `X-Secondlook-Answer` header. If it differs from the answer on screen, the page says so: "Its answer, “…”, differs from the one on screen: the receipt records the second check it ran." The ladder gives the same answer on the same file (`test_two_real_ladder_runs_give_identical_receipts_apart_from_created_at`), so this line should not show in practice.
   - The PDF button shows only when `/api/health` says `receipt: true`. The JSON button needs no extra. While a receipt is being made, both buttons are `aria-disabled` and keep focus. A newer check drops a receipt that has not arrived yet. It also clears the receipt's busy state, so the buttons work for the new answer at once (fix round 2: before, a dropped request left `aria-busy` set and every later click did nothing; `tests/test_w11_receipt_page.py` runs this in the headless browser).
   - **Batch is not covered.** The page has no batch view (`/api/batch` is API only), so a batch receipt button would need a batch view first. That is not a small change.
2. **The commit hash never breaks badly.** The version under the answer and in the deep-check footer shows the first 12 characters of the hash, in a `nowrap` span, with the full hash in its `title` ("Commit 62a1913…"). Any other version string shows whole. `word-break: break-all` is gone from `.meta` and `.inv-meta`; they use `overflow-wrap: anywhere`, which breaks a word only when it cannot fit.
3. **`/api/health` reports Glimmer's real state.** `desk.glimmer()` calls `GlimmerClient.check()` on the configured URL with a 1 s timeout on each socket operation (`GLIMMER_TIMEOUT`) and one 1.5 s deadline for the whole check (`GLIMMER_DEADLINE`, fix round 2: a local server sending a byte every 0.4 s had held it for 22 s). While a slow server still holds one check, the next says `unavailable` at once. `check()` asks `/v1/models` for `muse-glimmer` and `/props` for vision. `loopback_url` refuses any URL that is not a literal loopback address before a request is made. The client uses no proxy and follows no redirect. `details.glimmer` is `{"ready": true, "model": "muse-glimmer", "vision": true}`, or `{"ready": false, "reason": "unavailable" | "not_loopback"}`. The page's panels do not read it yet.
4. **Cream text on rust fills reaches AA.** Cream on `--rust` `#B4532F` is 4.37:1. `.demo-tag`, `.tag.strength.signed`, `#again-go:hover` and `#inv-go:hover` now fill with `--rust-text` `#9E4526`, where cream is 5.54:1. The brand rust stays on rules, borders, focus rings and large headlines.
5. **Deep-check timing copy.** The timing line now reads: "In our 12-image test run a deep check took 53 to 126 seconds an image; yours may be quicker or slower. It stops itself at 300 seconds." The range is the M2 smoke run's own (docs/M2-INVESTIGATOR.md: "over the 12 rows (min 52.72, max 126.42)"). It says "test run", singular, because the range comes from one run. The 29 Oct showcase run took 42 s and these captures took 70 s, so "our test runs" plural would be false.

## Tests

- `tests/test_w11_stage2.py` has 21 tests. They cover:
  - the JSON receipt matches the CLI's receipt for the same file and rungs (all but the version the web layer fills in), is canonical, has no temp path, and carries the answer header;
  - the PDF is still the default, and an unknown format gets a 400;
  - the caption reaches the receipt (`concerns` with it, `cant_tell` without), and an over-length caption is refused;
  - a stub receipt is marked demo;
  - the sample receipt reads the file in place, and bad sample ids get a 404;
  - the page's control, copy, posts and stale-reply guard;
  - the short hash;
  - Glimmer up, down (refused, timeout, reset), serving another model, and four off-loopback URLs, where no request is made at all. No real server starts: a patched `g.direct_open` answers every request;
  - the timing copy against M2's numbers.
- `tests/test_w11_design.py::test_text_on_every_fill_a_rule_sets_meets_aa` extends the contrast test to rules that set their own fill. For each one, it takes the rule's own text colour, or the colour of the rule a `:hover` or state rule modifies, or the inherited ink, and asks for 4.5:1 on the fill. The four pairs above must be 5.54:1. Run against the stage 1 CSS at `dfe273f`, it fails on the first of the four, `#again-go:hover` (4.37:1).
- `tests/test_desk.py` pins Glimmer down with an autouse fixture, so its health tests do not depend on a server running on this computer.

## Captures (headless-run, muted)

The shots are in `docs/W11-SHOTS-2/`, taken with `docs/W11-SHOTS-2/capture.mjs` through `~/bin/headless-run` from `62a1913`. The capture opens its own browser context so the downloads can be saved. Server A ran the real ladder with the 28 Oct S6 index and the N1 samples. Server C ran the demo stub. Glimmer ran from `scripts/glimmer_server.sh start` and was stopped with `scripts/glimmer_server.sh stop`. 0 requests went off 127.0.0.1 (`w11s2-capture.json`).

| State | 1280 px | 390 px | What the log records |
|---|---|---|---|
| 1 empty | `w11s2-1-empty-desktop.png` (and `-first-screen`) | `-phone` | health: `glimmer: true`, `details.glimmer` ready, vision |
| 2 caption concern, receipts saved | `w11s2-2-caption-concern-receipt-desktop.png`, `-receipt-desktop` | `-phone`, `-receipt-phone` | JSON 5,700 bytes starting `{`, PDF 55,209 bytes starting `%PDF-1.4`; `#again-go:hover` is `rgb(158, 69, 38)` |
| 3 sample 01, signed camera, PDF saved | `w11s2-3-sample-signed-receipt-desktop.png`, `-receipt-desktop` | `-phone`, `-receipt-phone` | PDF 55,621 bytes; `.tag.strength.signed` is `rgb(158, 69, 38)` |
| 4 deep check done | `w11s2-4-investigator-done-desktop.png`, `w11s2-4-footer-hash-desktop.png` | `-phone`, `w11s2-4-footer-hash-phone.png` | footer "secondlook 62a1913769fc", title "Commit 62a1913769fc6ccb81a4763efa22ed75397fafb4"; `#inv-go:hover` is `rgb(158, 69, 38)`; 70 s |
| 5 demo stub | `w11s2-5-demo-stub-desktop.png`, `-receipt-desktop` | `-phone`, `-receipt-phone` | `.demo-tag` is `rgb(158, 69, 38)`; the JSON receipt says `"demo": true` |

At 390 px the deep-check footer wraps once, between words, and the 12-character hash stays whole on the second line (`invMetaPhone` in the log).

Glimmer's health checked live on server A, with and without the server: `glimmer: true` with `details.glimmer` `{"ready": true, "model": "muse-glimmer", "vision": true}` while it ran. After `scripts/glimmer_server.sh stop`, it was `false` with `{"ready": false, "reason": "unavailable"}`, and the health request took 19 ms.

## Fix round on the designer check of `4e755a2` (F3, S-a, S-b)

The designer check of PR #32 at `4e755a2` passed the receipt buttons, the 12-character hash, the darker rust fills and the timing copy, and left three items.

1. **F3, the receipt's answer differs from the screen.** The message now has the warning look. "Saved name." stays grey; the clause "Its answer, “…”, differs from the one on screen: the receipt records the second check it ran." is a `.warn` span in `--rust-text` at weight 600 (`#receipt-status .warn`). It stays in the `role="status"` `aria-live="polite"` region, so a screen reader hears it once. The text is built with `replaceChildren`, not `innerHTML`.
2. **S-a, the weight of the checks line.** "This answer ran N checks on the file: …" stays grey at weight 400. Only "N check(s) could not run: X." takes `--rust-text` at weight 600, as its own span on its own line (`.rungs .failed { display: block }`). When no check ran at all, "No check could run on this file." and "No checks are installed…" keep the warning look, because there is no good news on that line. `.rungs.warn` (the whole line in rust) is gone.
3. **S-b, the reason a check could not run.** `ladder.run_traced` now returns a fourth list: for each rung that raised, a reason from a fixed list in `ladder.FAILURE_REASONS`, picked by the type of the error:
   - `NotInstalled`, `ImportError` (and `ModuleNotFoundError`), `FileNotFoundError`: "not installed";
   - `FailedToLoad`: "failed to load";
   - `MemoryError`: "ran out of memory";
   - `TimeoutError`: "timed out";
   - anything else: no reason (`None`).

   `NotInstalled` and `FailedToLoad` are new in `secondlook/types.py`. Both subclass `RuntimeError`, so every existing `except RuntimeError` and test still holds. The watermark rung raises `NotInstalled` when its weights file is missing, and `FailedToLoad` when the weights fail the pinned sha256 or `torch.jit.load` refuses them. The API sends the list as `rungs_failed_why`, in the same order as `rungs_failed`, on `/api/check`, the sample check and every `/api/batch` entry. The page writes "Watermark (not installed)". When any failed check has no reason, the line ends "The cause is in the server log.", where `run_traced` already logs the error.

Tests, in `tests/test_w11_stage2.py` (fix round section) and the updated `tests/test_w11_design.py`:
- each error type maps to its reason, including `PermissionError` (an `OSError` that is not a missing file), `RuntimeError`, `ValueError` and `SystemExit`, which have none (11 cases), and the list is the four reasons above;
- `run_traced` keeps one reason per failed rung, in order;
- the API sends `rungs_failed_why` on a check and a batch entry (the real watermark rung without its weights gives "not installed"; a `RuntimeError` gives `null`), an empty list when nothing failed, and an empty list from the stub;
- the watermark rung raises `NotInstalled` for a missing file, `FailedToLoad` for a wrong sha256 and `FailedToLoad` for a file with the right sha256 that is not a model;
- the page's reason, the server-log sentence, the two spans, the CSS and the receipt warning;
- the AA contrast test now reaches `.rungs .warn` and `#receipt-status .warn`.

Captures: `docs/W11-SHOTS-2/capture-fix.mjs` through `~/bin/headless-run`, muted, from the working tree on `4e755a2` with these changes. Server D ran the real ladder with `SECONDLOOK_VIDEOSEAL_WEIGHTS` pointing at no file, without the S6 claims index (no claims rung, which the caption concern does not use) and without Glimmer. 0 requests went off 127.0.0.1 (`w11s2-capture-fix.json`). Each state has a full-page shot and a shot of the line at 1280 px and 390 px.

| State | 1280 px | 390 px | What the log records |
|---|---|---|---|
| 2b a check could not run, with its reason | `w11s2-2b-check-could-not-run-desktop.png`, `-line-desktop` | `-phone`, `-line-phone` | "1 check could not run: Watermark (not installed)." in `rgb(158, 69, 38)` at 600 on its own line; the ran line `rgb(95, 99, 103)` at 400 |
| 2d the same with no reason (blanked in the browser) | `w11s2-2d-check-could-not-run-no-reason-desktop.png`, `-line-desktop` | `-phone`, `-line-phone` | the server sent `["not installed"]`; the page, given `[null]`, ends "The cause is in the server log." |
| 2e the receipt's answer differs (header forced in the browser) | `w11s2-2e-receipt-differs-desktop.png`, `-line-desktop` | `-phone`, `-line-phone` | the server's header was `concerns`; forced to `cant_tell`, "Saved street-canon.jpg.receipt.json." stays grey and the differs clause is `rgb(158, 69, 38)` at 600; role `status`, `aria-live` polite |

2d and 2e change only what the browser receives, as 2c did for the Missing text: the server cannot give a matching receipt a different answer, and the watermark rung without its weights now has a reason.
