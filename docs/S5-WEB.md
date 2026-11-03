# S5 local page (DAT-850)

A local web page for Second Look: drop an image and see the answer as a big label, with one card per piece of evidence showing the finding, what it proves and what it does not prove. "Can't tell" is styled as a complete answer, not an error. The page carries an "In development" badge and a "Not affiliated with Meta" footer.

Ported from the S5 stage 1 repo (a separate worktree, commit `c4af517`) into `secondlook/web/`, with the fixes from the S5 review notes (FAIL @ `c4af517`).

## Run

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[web,test,provenance]'
.venv/bin/python -m secondlook.web            # real ladder, http://127.0.0.1:8765 (--port or SECONDLOOK_PORT)
.venv/bin/python -m secondlook.web --stub     # canned demo answers chosen by file name
.venv/bin/python -m pytest -q
```

## Engine

- **Default: the real ladder.** The app loads the installed rungs once at start-up (`secondlook.rungs.load()`) and calls `secondlook.ladder.run(path, rungs=...)` for each upload. `/api/health` reports `"engine": "ladder"` and the list of rungs that loaded; the result carries the same list, and the page shows "Checks run: ...". If no rung is installed, the page says so in rust: "No checks are installed, so this answer is "can't tell" by default."
- **Stub only behind a flag** (`--stub` or `SECONDLOOK_STUB=1`). `secondlook/web/stub.py` reads nothing from the image. It picks a canned `Result` from a keyword in the file name (`signed-ai`, `camera`, `concern` or `label`; anything else gives `cant_tell`).

## Review fixes

| Review item | Fix | Test |
|---|---|---|
| F1 long names refused as "not an image" | `safe_name()` splits the suffix off the original base name first, then cuts the stem so the whole name fits in 80 characters | `test_81_character_name_returns_200`, `test_long_names_are_accepted` (80, 81, 120, 304 characters) |
| F2 stub banner fails open | The banner starts visible and is hidden only when `/api/health` returns `engine == "ladder"`. Inside the result: every stub summary starts with "Demo answer (stub rungs, chosen by file name, your image was not read):", the JSON carries `"demo": true`, and a rust "Demo answer" tag sits above the label unless the result says `engine == "ladder"` and `demo == false`. A ladder card with `raw.stub` also sets `demo` | `test_stub_answers_are_marked_demo_inside_the_result`, `test_stub_banner_fails_closed`, `test_a_stub_card_from_the_ladder_still_marks_the_result_demo` |
| F3 "can't tell" with no card | When the evidence list is empty the page shows a fixed limit card: what the checks that ran do establish, and that this is not evidence the image was made by a camera or by AI. A card with a blank "does not prove" shows a rust "Missing" line instead of an empty row | `test_empty_evidence_shows_a_limit_card` (reads the page script; no browser run) |
| N1 CSP | Every response, errors included, carries `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The inline CSS and script moved to `static/app.css` and `static/app.js` so no `unsafe-inline` is needed | `test_security_headers_on_every_response`, `test_security_headers_on_errors_too` |
| N1 size limit applied late | An outer ASGI layer refuses `POST /api/check` from its `Content-Length` (over 25 MB plus 64 KB of multipart overhead gives 413; no length gives 411) before any body is read. The 25 MB read check stays as a backstop | `test_oversize_content_length_is_refused_before_the_body_is_read` (the receive call fails the test if reached), `test_missing_content_length_is_refused`, `test_exactly_25_mb_passes_and_one_byte_more_fails` |
| N2 Playfair | `static/fonts/PlayfairDisplay.ttf` (variable, v1.203, sha256 `c40f2293...d8d6`) with `static/fonts/OFL.txt` beside it, both copied unchanged from the WSAI desk brand kit (`design/fonts/`) | `test_font_ships_with_its_ofl_licence` |
| N3 small items | The check handler is a plain `def`, so FastAPI runs it in a worker thread and a slow rung no longer blocks `/api/health`. The page reads the error body safely (no "Unexpected token" on a non-JSON reply, no `[object Object]`). Web deps are pinned in `pyproject.toml` extras | |

## What the server does with an upload

- Binds to 127.0.0.1 only. The page loads no fonts, scripts or images from the network; the CSP enforces it and a test scans the page files for `http(s)://` and `//` URLs.
- Accepts image suffixes only (JPEG, PNG, WebP, HEIC/HEIF, AVIF, GIF, TIFF), at most 25 MB. It does not sniff content; the rungs must not raise on bad bytes (contract).
- Writes the upload to a private temp directory under a sanitised name, runs the ladder, and deletes the directory before replying.

## Checked on the real rungs (28 Oct)

The app was run from a throwaway copy of this branch plus S2's rung files (`a reviewer-second-look` `1a65967`: `c2pa.py`, `iptc.py`, `exif.py`), with c2pa-python 0.38.2. Health reported `engine: ladder, rungs: [c2pa, iptc, exif]`, and each S2 sample came back from the real ladder with `demo: false` and real cards (for example `iptc-board-signed.jpg`: `concerns`, with C2PA, IPTC and EXIF cards). The answers themselves are S1 and S2's logic, not this lane's. A 400 MB upload was refused with 413 in 0.08 s. S2's files were not added to this branch.

## Licences

FastAPI (MIT), python-multipart (Apache-2.0), pytest (MIT). Uvicorn, Starlette and httpx are BSD-3-Clause, accepted by the project lead. Playfair Display is SIL OFL 1.1, accepted on condition that the licence file ships with the font; it does (`static/fonts/OFL.txt`).

## Not done

- No live-browser run of the new page in this stage; the script was checked with `node --check` and by tests that read it.
- The page's limit card for an empty list duplicates nothing today. When S1 hardening (F1 in the fast-track plan) makes the ladder always return a limit card for "can't tell", the page shows the ladder's card instead, because the fixed card appears only for an empty list.
