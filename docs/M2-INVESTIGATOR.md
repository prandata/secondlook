# M2: the Glimmer investigator (DAT-873)

Meta's Muse Glimmer 30B, on this machine through `llama-server` on loopback (M1, `docs/M1-GLIMMER.md`), calls Second Look's checks as tools and the desk gets a note of what was looked at. Code: `secondlook/investigator.py`. Command line: `secondlook investigate photo.jpg --caption "..."`. Web: `POST /api/investigate` (file plus an optional `caption` form field, up to 2,000 characters; the stub engine answers 409).

## Tools

| Tool | What it does | What the model may pass |
|---|---|---|
| `run_ladder(path)` | the evidence ladder | the image's own name |
| `read_text_in_image(path)` | M1's vision reader; plates, phone numbers and emails removed | the image's own name |
| `extract_claim_facts(text)` | date, place and device as ClaimFacts | whole clauses of the caption |
| `lookup_claim(text)` | S6 fact-check lookup | whole clauses of the caption or of the printed text, or a run of whole printed lines |
| `context_check(path, facts)` | X1's context rung, when installed | values an earlier `extract_claim_facts` kept |

At most 8 calls (`MAX_STEPS`). A call outside these rules is refused, recorded as a step, and nothing from it reaches the evidence.

## Time limit

One request, from the CLI or `POST /api/investigate`, gets at most 300 s in all (`MAX_SECONDS`). Each request to the model server may wait only for the time left, and 15 s (`LADDER_RESERVE_SECONDS`) is kept for the file checks until they have run (the ladder takes about 0.4 s a file plus a 3 s model load, `docs/PERFORMANCE.md`). At the limit the deep check stops, a `timed_out` step says "The deep check timed out after 300 seconds, so it stopped there. Fixed rules give the answer from the file checks and the cards found before then.", the ladder runs if the model had not asked for it, and the answer is the rules' as always. The smoke run below took a median of 108.27 s per image, and its slowest image 126.42 s.

## What the model cannot do

- **Set the answer.** `DeskNote.answer` is `ladder.decide()` over the cards the tools produced. If the model never runs the ladder, the investigator runs it (`by: "rules"`). The model's own text, including its final reply, is never read.
- **Invent a claim clause** (extractive, the project lead on DAT-869). `lookup_claim` splits the model's text with `secondlook.verbatim.split_clauses` and needs every piece to be a whole clause of the source (`whole_clause`), at most 3. The query is then rebuilt from the source's own clauses, joined with "; " in caption order, so the model's string is never searched. A run of up to 6 whole printed lines is accepted too, because the reader splits wrapped text into lines. S6's cards are marked `search_source: "model"` through M1's `lookup_candidate`.
- **Invent facts.** `extract_claim_facts` takes whole clauses of the caption only, never the printed text, which is itself a model reading. The model copies `date_text`, `place` and `device`; `verbatim_facts` keeps a value only if it is an exact substring of those clauses on word boundaries, and never repairs one. Code parses the kept date (`parse_date_text`: X1's `find_date` when installed, else the same three written forms). An all-number date that reads day first or month first, such as 03/04/2026, keeps no date, and the step line says so ("Its date is all numbers that read day first or month first, so no date was kept."). `context_check` takes only values a facts step kept. There is no other route for facts: M1's abstractive `glimmer.extract_facts` and `GlimmerExtractor`, which let the model write a date or place the caption never states, are deleted (a reviewer's FAIL at 4b2672c; the project lead on DAT-873).
- **Write anything a reader sees** (N3 retired, the project lead on DAT-848). Each step's `result` is a `STEP_TEMPLATES` sentence filled with counts and fixed names. A refused step shows a fixed `REFUSALS` reason and keeps no arguments. The explanation is those step lines, then E1's `explain()` over the final Result when E1 (`secondlook/explain.py` with `SOURCE == "rules"`) is installed, else one `DESK_SUMMARIES` sentence. N3's model-backed module had the same file name; it is never called. Model output reaches the note only as `image_text` and `facts` (both labelled `model reading, not evidence`) and as the lookup query (`search_source: "model"`).
- **Reach another file.** A `path` must be the image's own name or path.

`explanation_lines` gives each line with its template key (`step.<key>`, `e1.<key>` or `desk.<key>`), so every sentence traces to a template.

## Tests

`tests/test_investigator.py` scripts a fake model. The hostile cases:

- it calls a made-up `set_answer` tool, adds an `answer` argument, sends arguments that are not JSON, and states a verdict in its final text: the answer stays the ladder's;
- it writes a canary string into its replies, a tool name, refused arguments, a claim, a facts value and a path: the canary is nowhere in the serialised note;
- a quiet model and a model that says whatever it likes, with the same tool calls, give the same explanation word for word;
- nine invented claim texts (a word dropped, a negation added, words added, part of a clause, part of another clause, two clauses spliced, a translation, the case changed, an injected sentence) are refused, and S6 is never queried;
- facts that are worked out, spelled differently or not in the caption are dropped, and facts read from printed text are refused;
- invented, reshaped or unparsed facts never reach the context check, including the three in a reviewer's FAIL (a date assembled from two dates, a place joined from two sentences, 03/04/2026 read either way);
- an all-number date that reads both ways keeps no date, and the step says so;
- a slow model is stopped at the time limit, the step says so, and the answer is the ladder's.

With E1 merged, `test_against_the_real_e1` runs against E1's real `explain()`.

`secondlook/verbatim.py`: a month's short form ("Sept.", "Jan.") is no longer a clause boundary, so "Sept. 30, 2024" stays inside one clause.

## Live smoke run

Script: `scripts/investigate_smoke.py`. Raw output: `docs/M2-INVESTIGATOR-SMOKE.json`. Server: `scripts/glimmer_server.sh start` on Meta's pinned GGUF (`Muse-Glimmer-30B-GGUF@b44f1072`, Q4_K_M, with its vision projector), temperature 0. Samples: the 9 demo files (`DEMO-SAMPLES/files`, no caption) and the 3 screenshots in `docs/M2-SHOTS/`, each investigated with its own post text as the caption. S6 index: the 28 Oct ClaimReview index (`S6-data/claims.sqlite`). At the time of the run this branch had neither E1 nor X1, so the explanation used `DESK_SUMMARIES` and `context_check` was refused as not installed.

Run 2026-10-28T09:14:34Z, macOS-26.6.2-arm64-arm-64bit, server muse-glimmer, reasoning low, E1 installed: False, X1 context rung: False, whole run 1189.6 s.
Egress (lsof on the server pid 22951): 669 samples, 1304 loopback lines, 0 non-loopback lines, exit 0.

| Image | Caption | Answer (ladder alone) | Seconds (model) | Model steps (refused) | Tools | Fact-checks (expected found) | Facts kept |
|---|---|---|---|---|---|---|---|
| 01-camera-pixel10pro-atlantic-sign.jpg | no | signed_camera (signed_camera) | 126.42 (30.59) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 02-camera-pixel10pro-utah-sign.jpg | no | signed_camera (signed_camera) | 53.59 (19.45) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 03-ai-firefly-mountain-landscape.jpg | no | signed_ai (signed_ai) | 52.72 (22.11) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 04-ai-firefly-typography-logo.jpg | no | signed_ai (signed_ai) | 58.4 (27.08) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 05-ai-edit-photoshop-firefly-bentley.jpg | no | signed_ai (signed_ai) | 117.09 (44.05) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 06-stripped-pixel10pro-atlantic-sign.jpg | no | cant_tell (cant_tell) | 120.05 (66.09) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 07-stripped-firefly-mountain-landscape.jpg | no | cant_tell (cant_tell) | 116.99 (54.71) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 08-iptc-ai-label-firefly-mountain-landscape.jpg | no | concerns (concerns) | 108.93 (54.53) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| 09-iptc-ai-label-firefly-typography-logo.jpg | no | concerns (concerns) | 107.61 (59.78) | 2 (0) | run_ladder, read_text_in_image | 0 | none |
| shot-1-pentagon.png | yes | cant_tell (cant_tell) | 104.05 (75.51) | 3 (0) | run_ladder, read_text_in_image, lookup_claim | 1 (yes) | none |
| shot-2-hollywood-sign.png | yes | cant_tell (cant_tell) | 118.58 (65.23) | 4 (0) | run_ladder, read_text_in_image, extract_claim_facts, lookup_claim | 1 (yes) | place Hollywood sign |
| shot-3-gatlinburg.png | yes | cant_tell (cant_tell) | 98.15 (48.25) | 5 (1) | run_ladder, read_text_in_image, extract_claim_facts, lookup_claim, context_check | 1 (yes) | date 2024-09-30, place Gatlinburg, Tennessee |

Median 108.27 s per image over the 12 rows (min 52.72, max 126.42); 2.5 model steps per image; answers equal to the ladder alone: 12 of 12.

"Expected found" means S6 returned the PolitiFact review the screenshot was made from (its URL is in `SHOTS`). The egress check supports one claim only: the model server process opened no non-loopback connection while it was sampled.

Notes from the run:

- Gatlinburg: the facts step kept `date_text` "Sept. 30, 2024" word for word, and code parsed it to 2024-09-30. Before the month fix in `verbatim.py`, "Sept." ended a clause, so this date could not be kept.
- Hollywood sign: "Jan. 8" has no year, so no date was kept (only the place).
- Each lookup the model made used a whole caption clause, and none was refused. The one refusal in the run is `context_check` without X1.
- Explanation lines read, for example: "3. Searched published fact-checks with words from the caption: 1 published fact-check." With E1 merged, E1's summary and card sentences replace the closing `DESK_SUMMARIES` line.

The first run (`af0c075`, before the extractive and template changes, Ollama-store GGUF, N3 guard loaded) is in this branch's history only.
