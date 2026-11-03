# E1: plain-language explanations from fixed templates (DAT-848)

the project lead decided on DAT-848, after a reviewer's FAIL @ 2c34209 on the N3 model path, that no
model writes user-facing text. N3 (`n3-explain`, local Llama plus a guard) stays as history and is
not merged. E1 replaces it: `secondlook/explain.py` writes every sentence from a fixed library.

## What it does

`explain(result) -> Explanation(summary, per_card=[CardLine(rung, sentence, template)], label, source="rules")`

- **One sentence per card, one summary.** The template is chosen by the card's rung, the shape of
  its finding (fields in `raw`) and the answer. There are 67 card templates and 12 summaries. A
  summary is one to three short sentences, each 20 words or fewer for the detector-score summaries.
- **Slots take structured fields only**, each checked before use:
  - numbers: a detector score and cut-off, matched watermark bits, days, km and key-word counts,
    which must be finite and in range, and are formatted in `explain.py`;
  - names from closed lists: check names, IPTC and EXIF field names, AI tool display names (one per
    pattern in `rungs/_sources.py`) and the trust list;
  - an IFCN fact-checker's domain from `factcheckers.py`, printed only in the fixed form
    "published by <domain>". A domain is an identity, not a claim, so every listed domain prints
    (stopfake.org, fakenews.pl, correctiv.org and the rest), and this is the one place the banned list
    does not read. Everywhere else the whole-word rule holds, on the slots and on the whole sentence.
- **No signer is named** (the project lead, DAT-848). A trusted record reads "A record signed by a
  certificate on the C2PA Trust List" (or the interim list). The card below still shows the name.
- **Free text never goes in.** That covers findings, Software values, claim text, ratings, transcripts,
  captions and `Result.summary`. A sentence names the field ("the file's EXIF Software field") and
  never quotes it.
- **Every sentence pairs what the card shows with what it does not show**, and none says or implies
  real, authentic, genuine, fake or true. The banned list (`BANNED`) covers those five stems plus the
  words that imply them (false, verified, proof, accurate, correct, untouched, forged and more). It is
  matched on whole words after NFKC and case folding.
- **Fail closed, never raise.** An unknown card shape, a bad number, an odd rung name or a slot
  holding a banned word gives "Second Look has no plain-language line for this card yet, so read
  the card itself." `explain()` never raises. Demo (stub) cards get a demo line.
- **C2PA lines follow the rung's own order** (`rungs/c2pa.py` `_classify`): an AI source type wins, so
  a record that declares a capture and AI reads as AI-made, as its card does. Only a capture with an
  AI tool's name and no AI term is the conflict line. An AI-made ingredient is named on every line
  whose card names it, trusted or not.
- **The caption check (X1, DAT-872).** Its templates key off `context.py`'s raw fields (`contradiction`,
  `field`, `disagreement.days_apart` / `distance_km`, `checks[].outcome`), on main since `77e6e63`.
  Agreement on one field reads "The caption's date matches ...", on several "The caption's date and
  place match ...".
- **Search words a model chose are never called "your key words"** (a reviewer's R5 @ d793b03). A
  fact-check card marked `raw["search_source"] = "model"` (M1, `glimmer.lookup_candidate`) gets its own
  four templates, for example "A site not on the IFCN list rated a claim with 3 of 4 words a model took
  from your caption, so check them." Caption or image comes from `raw["search_from"]`, a closed list
  of two that `lookup_candidate` now sets; any other value reads "words a model picked" and is never
  copied in.

## Where it shows

- `POST /api/check` returns `explanation` (`label`, `source`, `summary`, `summary_template`, `per_card`).
- The page shows it under the answer in a box labelled **"Plain-language summary (written by Second
  Look's rules, not by a model)"**. Every line is set with `textContent`.
- `secondlook check <file>` adds `explanation` to its JSON. `secondlook check --plain <file>` prints
  only the explanation.
- The ladder's limit card now carries `file_readable`, so "the file could not be read" and "no checks
  are installed" get different lines. When every installed check failed, the line is "No check could
  run on this file: <checks> failed", never "not installed".

## Tests (`tests/test_explain.py`)

1. Every template, rendered with the slot values of every sample card (`tests/explain_samples.py`,
   at least one card per template, built through the real claim, voice and ladder builders where
   they exist), passes the banned-word list and is plain ASCII with no dashes.
2. Every sentence `explain()` returns parses back to exactly one template, and each slot value is
   from its closed list or number format. Nothing else can appear.
3. A reviewer's batteries (`adv.py`, `adv2.py`, `adv3.py`, `mine.py`: 96 drafts, copied verbatim by
   AST into `tests/explain_batteries.py`) are recast as "no model text can reach the Explanation".
   Each draft is put into every free-text field and every raw string of every sample card, one at a
   time, under all four answers, and into `Result.summary`. No draft appears in the output, and no
   signer name either.
4. A hostile "model" is patched in everywhere one could hide: the explain module (`model`, `llm`,
   `generate` and others), fake `ollama`, `llama_cpp`, `openai`, `anthropic` and `transformers`
   modules, `urllib`, `http.client` and sockets. The ladder also gets a rung and a summary full of
   hostile text. `/api/check` still returns template text only, and the hostile callable is never
   called. An AST test pins `explain.py`'s imports to the standard library and Second Look's own
   modules.
5. The same input gives the same output across deep copies, reordered `raw` keys, and three
   processes with different `PYTHONHASHSEED` values.
6. Reading level: the 82 distinct sentences the samples produce average Flesch-Kincaid grade 8.3
   (median 7.97). The syllables are counted by vowel groups. The test holds the mean at 8.5 or below
   and the median at 8.0 or below.
7. A reviewer's FAIL @ 5608354: all 72 C2PA combinations (trusted, untrusted or invalid; 6 sets of
   source terms; an AI tool or not; an AI ingredient or not) are built by the real rung from a store,
   decided by the real ladder, and each line must say what its card's own text says (R1). Rungs that
   all raise give the all-failed line (R2). Every IFCN domain renders as "published by <domain>",
   and the banned list still catches a banned word anywhere else in that sentence (R3).

Two mutants were each caught by the suite at the first round: `Result.summary` passed through, and
the Software text used as the tool name.

## Three rendered examples (real sample files, real ladder)

`samples/commons-firefly-mountain-landscape.jpg`, `signed_ai`:

> A signed record from a listed signer says AI made this, but not who made it or why.
> - A record signed by a certificate on the C2PA interim trust list says AI made this, but not who made it or why.
> - A detector scored this 0.01 out of 1, but no cut-off is fixed yet, so the score is for information only.
> - Second Look's own hidden mark is not here (134 of 256 bits match), and that says nothing about how this was made.

`samples/iptc-board-signed.jpg`, `concerns`:

> Second Look found 1 reason to look closer, but on its own it does not show how this was made.
> - The file has a C2PA record that failed its checks, so nothing it says can be relied on.
> - The file's IPTC label says a device captured this, but anyone can add or change that label.
> - The file's EXIF fields (Make and Model) name a camera, but they can be copied, so they don't show it took this.
> - The file's EXIF Software field names the last program that saved it, but not how the image was made.
> - A detector scored this under 0.01 out of 1, but no cut-off is fixed yet, so the score is for information only.
> - Second Look's own hidden mark is not here (137 of 256 bits match), and that says nothing about how this was made.

`samples/py-A.jpg`, `cant_tell`:

> Second Look can't tell how this was made, and finding nothing is not evidence either way.
> - The file's EXIF fields (Make and Model) name a camera, but they can be copied, so they don't show it took this.
> - The file's EXIF Software field names the last program that saved it, but not how the image was made.
> - A detector scored this under 0.01 out of 1, but no cut-off is fixed yet, so the score is for information only.
> - Second Look's own hidden mark is not here (128 of 256 bits match), and that says nothing about how this was made.
> - Second Look ran 5 checks (C2PA, IPTC, EXIF, detector and watermark) and none raised a concern, but finding nothing is not evidence.

## Known limits

- The "no model text" guarantee covers the explanation only. The ladder's own `Result.summary` is
  older text and not part of E1 (its `signed_ai` and `signed_camera` lines end "...is true",
  `secondlook/ladder.py` `decide()`). Since DAT-850 the page, the desk note, the CLI's `--plain`, the
  JSON and PDF receipts and the batch summary files all print the explanation's summary instead
  (`tests/test_summary_surfaces.py`). The ladder's line stays in the `summary` field of the
  `/api/check` body and the CLI's JSON output; the image page shows it only for the demo stub. The
  voice desk still shows the voice check's own line (`secondlook/web/desk.py`), outside this change.
  The cards quote file text (EXIF Software, IPTC values, generator names) and the desk shows a
  speech model's transcript, labelled as the model's. the project lead left those outside E1 (DAT-848).
- Template sentences are short, so they drop detail the cards keep, such as the signing date and the
  exact source-type terms. The cards stay on the page below the box.
