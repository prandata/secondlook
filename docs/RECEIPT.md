# Evidence receipt (R1, DAT-870)

Status: in development, 28 Oct 2026. Code: `secondlook/receipt.py`. Tests: `tests/test_receipt.py`.

A team attaches the receipt to a story or a claim file. It records what was checked, by what, and what the ladder found, including the limit of every card. It is the same result the ladder gave, written down. It adds no verdict and never says an image is real.

## Make one

```
secondlook check photo.jpg --receipt receipts/
```

The answer prints to stdout as before. The folder gets three files, named after the image:

| File | What it is |
|---|---|
| `photo.jpg.receipt.json` | The receipt. Canonical JSON: sorted keys, two-space indent, UTF-8, one trailing newline. |
| `photo.jpg.receipt.json.sha256` | The hash of that JSON file, in `sha256sum` format. Check it with `shasum -a 256 -c photo.jpg.receipt.json.sha256` (or `sha256sum -c` on Linux). |
| `photo.jpg.receipt.pdf` | One or two pages for a typical check: answer, summary, file hash, a card table (finding, proves, does not prove, strength), versions, the hash of this receipt's JSON (built with the PDF, so it matches the `.json` beside it here; the page's separate JSON download is a new check and can differ, as the PDF says) and the limits line. Fonts are embedded. |

Exit codes: 0 done, 2 no receipt (the file or folder could not be read or written; the answer was still printed), 3 JSON written but no PDF (ReportLab missing; install the `receipt` extra).

Batch (`secondlook batch FOLDER --out DIR`) writes the same three files for every image it checks, into `DIR/receipts/`, named by position and path: `0000-photo.jpg.json`, `0000-photo.jpg.json.sha256` and `0000-photo.jpg.pdf`. `--no-pdf` leaves out the PDF. The receipts come from `secondlook/receipt.py` itself, not a separate writer, so they are the same as `check --receipt` gives. They differ between runs only in `created_at`; `summary.json`, `summary.csv` and `summary.html` are byte-for-byte the same on every run. A file whose receipt could not be written still has its answer in the summary, with the reason in `receipt_error`. `summary.json` carries `receipts.pdf_written` and `receipts.pdf_failed`, and `receipts.pdf` is true only when PDFs were asked for and none failed. See the header of `secondlook/batch.py`.

Local page (`python -m secondlook.web`, binds to 127.0.0.1 only):

- `POST /api/receipt` with the image as multipart field `file` returns the PDF as a download. `?format=json` returns the canonical JSON file instead, the bytes `check --receipt` writes (W11 stage 2). An optional `caption` field checks the file with its caption, as `/api/check` does, and the receipt records it in `inputs.caption`, as `check --caption ... --receipt` and `/api/check?receipt=1` do (DAT-850).
- `POST /api/samples/{id}/receipt` (`?format=pdf` or `json`) does the same for a sample, checked in place. It takes the same optional `caption` and `deep_run` fields; with neither it is the sample check as before.
- **Claim words Muse Glimmer picked (DAT-850).** Both receipt routes take an optional `deep_run` form field: the run id `/api/investigate` returned as `receipt_run`. The receipt takes the clauses from the server's own record of that deep check, never from the client. See "Picked claim words" below.
- Each download checks the file again; nothing from an earlier check is kept. The `X-Secondlook-Answer` header carries the receipt's answer, and the page says so if it differs from the answer on screen.
- The page's "Download receipt" row under the evidence has a PDF button (shown when `/api/health` says `receipt: true`) and a JSON button. When the deep check on this file and this caption found a published fact-check with clauses Glimmer picked from the caption, the page sends that deep check's run id with the receipt request, and one fixed line before the buttons says so: "The receipt also looks up the words Muse Glimmer picked from your caption." When the server could not add them, the saved line says why. The buttons are at least 44 px tall.
- `POST /api/check?receipt=1` returns the usual answer with the receipt under `"receipt"`.
- The same upload checks apply: image types only, 25 MB, refused from `Content-Length` before the body is read. The receipt names the file by its base name, never the temp path.

From code:

```python
from secondlook.receipt import build, write_json, write_pdf
receipt = build(result, "photo.jpg", inputs={"caption": "..."})   # a dict
write_json(receipt, "photo.jpg.receipt.json")   # also writes photo.jpg.receipt.json.sha256
write_pdf(receipt, "photo.jpg.receipt.pdf")
```

`out` may also be an existing folder; the files are then named after the image as above. Both writers return the path they wrote.

## Schema `second-look-receipt/1`

| Field | Meaning |
|---|---|
| `schema` | `second-look-receipt/1` |
| `created_at` | UTC, `YYYY-MM-DDTHH:MM:SSZ`. The only field that changes between two receipts for the same file and result. |
| `tool` | `{name: "secondlook", version: <result.version, a git sha>}` |
| `versions` | `package`, `python`, `python_implementation`, and `libraries` (c2pa-python, pillow, torch, torchvision, timm, safetensors, reportlab, whichever are installed). |
| `file` | `name` (base name), `size_bytes`, `sha256` of the bytes checked. |
| `answer` | Exactly what the ladder returned. |
| `summary` | The plain-language summary, `explain(result).summary` (`secondlook/explain.py`): the same sentence the page, the desk note, the CLI's `--plain` and the batch summary files show (DAT-850). It is not the ladder's own `Result.summary`. |
| `demo` | `true` when any card comes from the demo stub. The PDF then carries a banner saying the image was not read. |
| `evidence` | Every card, in ladder order: `index` (from 1), `rung`, `finding`, `proves`, `does_not_prove`, `strength`, `raw`, `raw_dropped`. |
| `inputs` | What else the user gave, as given: the caption the check ran with under `caption` (web routes and `check --caption`; a batch has no caption), typed claims under `claims`, and claim words Muse Glimmer picked under `picked_claims` (below). `{}` if nothing. |
| `limits` | One fixed line from `docs/CLAIMS.md`: it never says an image is real, what `signed_camera` and `cant_tell` mean, that a signed record does not prove the picture's claim, that a concern is a reason to look closer, that it is not a fact-checker and not affiliated with Meta. A test checks each sentence still has its source in `CLAIMS.md`. |

**Per-run timing is dropped from `raw` by an explicit list, and listed.** How long a rung took is not evidence, and it made two checks of the same file give different JSON, sha256 and PDF. `build()` drops only the fields on this list, each one a rung and a path from the top of that card's `raw`, and names each one in that card's `raw_dropped` (here `raw.seconds (per-run timing)`):

| Rung | Field | What it is |
|---|---|---|
| `classifier` | `raw.seconds` | Wall-clock time of the model run. |
| `watermark` | `raw.seconds` | Wall-clock time of the model run. |

Nothing else is dropped, and no key is matched by its name. A time or length that comes from the file or the audio stays, because it is what the file says: the C2PA `sign_time`, the EXIF `datetime_original`, the voice rung's `start_ms`, `seconds_read` and `limit_seconds`, and the transcript card's `transcript.seconds` and every `segments[].end_ms`. Only `created_at` carries the time of the check. The `inputs` block is recorded as given and is not stripped. A new rung that records how long it took must add its own entry to `VOLATILE_FIELDS` in `secondlook/receipt.py` and to this table; a test checks that the list is exactly these entries and that this page names each one.

**Large blobs are dropped from `raw`, not hidden.** Bytes of any length, and text over 8,192 characters, are replaced by `{"dropped": ..., "length": ..., "sha256": ...}` (text also keeps its first 200 characters). Lists are cut at 500 items and nesting at 12 levels. Each card's `raw_dropped` lists what was replaced, so a reader can see that something was left out and check it against the original if they have it. Values JSON cannot hold (NaN, objects, dates, paths) are written as text.

## Picked claim words (DAT-850)

The deep check (M2, `secondlook/investigator.py`) looks a claim up with whole clauses Muse Glimmer picked from the caption (`docs/M1-GLIMMER.md`, Captions). A raw caption often finds no fact-check where the picked clause does. Before this change the receipt had no claim card at all: no ladder rung looks a claim up. So the page could show a fact-check that its receipt did not keep.

1. **The server keeps its own record; the page sends a run id, never the words** (Codex on PR #42: words a client sent were credited to Muse Glimmer without any check that Glimmer ran). When a deep check's first caption lookup found a published fact-check, `/api/investigate` records the run in memory (`secondlook.receipt.DeepRuns`): a random run id, the file's sha256, the caption and the picked clauses, checked again as whole clauses of the caption. It returns the id as `receipt_run` (`null` when nothing was picked). The record keeps the last 64 runs (`DEEP_RUNS_KEPT`), each for at most an hour (`DEEP_RUN_SECONDS`); a restart forgets them all. A `picked_claims` field sent by a client is ignored.
2. **Checked before use.** A receipt with `deep_run` takes the clauses from the record only when the uploaded file's sha256 and the caption sent (after `verbatim.normalise`) are the run's. Otherwise, or when the id is unknown or expired (and always on the demo stub, which has no deep check), the receipt is made without picks, as a 200, with `X-Secondlook-Picks: not-added` and a plain reason in `X-Secondlook-Picks-Reason`, which the page shows after "Saved ...". It is never an error page. A receipt with picks carries `X-Secondlook-Picks: added`.
3. **The same fixed lookup, run again.** The receipt checks the file again with `Context(caption=..., claims=[the picked clauses])`, then `with_picked_claims` runs the investigator's S6 lookup on them: one query, the clauses joined in caption order with `"; "`, up to 3 matches (`investigator.CLAIM_CARDS`), each card marked `search_source: "model"`, `search_from: "caption"`, its finding ending "Search words came from a model reading of your caption; check them." and its proves line counting "key words in the picked words", never "your" key words. Nothing is copied from the investigator's output.
4. **The answer stays the ladder's.** Claim cards never move it (`ladder.decide` has no rule for them). If the new cards would change the answer, no receipt is made (500, "Adding the picked claim words would change the answer, so no receipt was made.").
5. **Recorded apart from typed claims, with what the lookup did.** `inputs.picked_claims` is `{"clauses": [...], "picked_by": "Muse Glimmer", "from": "caption", "source": "Picked by Muse Glimmer from the caption (clause choice only; the words are the caption's own).", "lookup": "found" | "none" | "unavailable"}`. `lookup` is S6's status for the receipt's own lookup: it found a match, it found nothing close, or the offline index could not be read (S6 then adds its one "could not be read" card). The clauses are written as the caption writes them. Typed claims would go under `claims`, so the two never mix. The PDF's "Inputs given" table shows the caption row, then one "Claim words" row with three lines: each clause in quotes, the source sentence, and what the lookup did ("No published fact-check matched these words." when it found nothing).

With no caption and no deep check, the receipt is main's, byte for byte (`tests/fixtures/dat850/plain.receipt.json`, written by main at e219f4a; its summary line rewritten when PR #39 merged main). `/api/check` ignores `deep_run`. Tests: `tests/test_receipt_picked_claims.py`, and the page in a real headless browser in `tests/test_receipt_picked_page.py`.

## What the receipt does not give you

- It is not signed. The `.sha256` file shows the JSON was not changed by accident, not who made it. Anyone can write a receipt for any file.
- It does not prove the image is real, authentic or safe to trust. See `docs/CLAIMS.md`.
- It records the file's hash. It does not store the image.
- The PDF cannot draw every character: the fonts cover Latin text. A character they lack is shown as `?` and the PDF says so on the page. The JSON always has the exact text.

## Determinism

The same receipt dict always gives the same JSON bytes and the same PDF bytes (the PDF has fixed dates and ids). Two real checks of the same file (the real ladder, the real classifier) give receipts that differ only in `created_at`: with that one field removed, the JSON, its sha256 and, with `created_at` fixed, the PDF bytes are identical. `tests/test_receipt.py::test_two_real_ladder_runs_give_identical_receipts_apart_from_created_at` runs the ladder twice to prove it. Nothing in the receipt code uses the network, and a test checks that.
