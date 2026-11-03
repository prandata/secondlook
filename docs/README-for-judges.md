# Second Look: a guide for judges

Status: in development. First written 28 Oct 2026 and reframed 2 Nov 2026 so a judge starts at the queue. Second Look is not affiliated with Meta.

## What to look at

Start at the queue on a folder, then open one receipt. The single-image page is shown only as the receipt behind a queue row.

1. Set up: `./scripts/demo.sh --no-serve` from a clone (steps under "How to run it"). It installs everything and does not start the page.
2. Run the queue on the samples folder: `.venv/bin/secondlook queue samples --out queue-out`. It checks the 9 demo samples and the recorded Pentagon image in `samples/recorded/`. Open `queue-out/queue.csv`: each row names its band (`uncertain`, `auto_decided` or `likely_fine`), its reason and the receipt behind it. The receipts are in `queue-out/receipts/`. The queue never changes a receipt's answer, and it never says an image is real.
3. Open a receipt. Run `./scripts/demo.sh`, wait for `Ready:`, open that address and drop a row's file from `samples/` on the page. Under the evidence, beside "Download receipt", press "PDF" or "JSON". Each card on it has a `proves` and a `does_not_prove` field. Two rows to try:
   - `samples/02-camera-pixel10pro-utah-sign.jpg`: the receipt says "Signed as a camera capture".
   - `samples/06-stripped-pixel10pro-atlantic-sign.jpg`, a copy of sample 01 with its signed record removed: the receipt says "Can't tell". `samples/01-camera-pixel10pro-atlantic-sign.jpg` is the signed original.
4. Read the held-out result and the failures: [What the held-out result says](../README.md#what-the-held-out-result-says) in the README, then [FAILURES.md](FAILURES.md), where three AI images went to `likely_fine`. Then [RESULTS-v2.md](RESULTS-v2.md): the classifier gate, a FAIL, published as it stands.

### More receipts: a recorded Muse Glimmer run and a claim lookup

5. See a recorded Muse Glimmer run with no model installed. Pick sample 02, 04 or 08 under "Try a sample". Under Deep check, press "Show the recorded run (not live)". Its label gives the machine, the date and the seconds.
   - In recording 04 the model reads `ADBE` / `FIRLE`. Those letters are read as printed: the AI-made logo itself is stylised and spelt that way.
   - The Pentagon case, recorded: type `BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. Pentagon is under attack. Stay safe everyone` in the caption box, then drop `samples/recorded/pentagon-mock-post.png`, a mock post we made with that wording (our own, from the M1 test). Press "Show the recorded run (not live)". It shows the caption, the clause Muse Glimmer picked to search, PolitiFact's published fact-check ("Pants on Fire") with its CC BY credit, and "Can't tell". It is offered only for that file with that caption, word for word.
6. Type a claim. The "Check a claim" box needs the fact-check index, a 200 MB download. This line builds it from the feed file we used on 29 Oct 2026, checked by its sha256:
   ```sh
   curl -L -o claimreview.json https://storage.googleapis.com/datacommons-feeds/claimreview/20261029-141029/data.json && echo "3c1470845f2f4adc4254bb3cc2b73349aec6b0cc6de7bc5ab15ecc71712b6024  claimreview.json" | shasum -a 256 -c && .venv/bin/python -m secondlook.rungs.claim --build claimreview.json ~/.cache/secondlook/claims.sqlite --downloaded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --sha256 3c1470845f2f4adc4254bb3cc2b73349aec6b0cc6de7bc5ab15ecc71712b6024 --source https://storage.googleapis.com/datacommons-feeds/claimreview/20261029-141029/data.json
   ```
   Restart `./scripts/demo.sh`, then type `Photo shows the U.S. Pentagon is under attack` (the clause the model picked). PolitiFact's review comes up. The whole caption, typed as is, finds none. A later feed than this one may give other matches.

Hardware: the install runs only on a Mac with Apple silicon and macOS 14 or later. The first run needs about 2.1 GB of free disk (about 1.9 GB with `--no-watermark`). Optional Muse Glimmer features (text read from an image, caption claims, the investigator) need Meta's Muse Glimmer 30B files. The download is about 18 GB and needs 40 GB free (`scripts/fetch_glimmer.sh`). Each extra worker of a batch run holds its own copy of the classifier, about 0.9 GiB.

## What it is

Second Look is a triage queue for the images a desk receives. A folder goes in. A ranked queue and an evidence receipt for every image come out. Each receipt is a single file run through an evidence ladder. Each piece of evidence on it is a card with two fixed fields: **what it proves** and **what it does not prove**. Every rung fills the "does not prove" field; that is the contract each rung follows.

Everything in this repository runs on your own machine. It uses only open-source parts and open data (MIT, Apache, CC0, CC BY and a few other permissive licences, all listed in [LICENSES.md](LICENSES.md), which also names the exceptions still waiting for a decision). The code in this repository calls no paid or metered service.

## The 4 answers

| Answer | What it means | What it rests on |
|---|---|---|
| `signed_ai` | A valid Content Credentials (C2PA) record says AI made this image. | A signed manifest naming an AI generator or the "trained algorithmic media" source type. |
| `signed_camera` | A valid Content Credentials record says a camera captured this image. | A signed manifest with a camera capture action and no AI ingredient. |
| `concerns` | We found reasons to look closer. These are reasons, not a verdict. | An AI label in unsigned metadata (IPTC or XMP, EXIF, PNG text), or a classifier score at or above a threshold we froze in advance on separate images, once that threshold has passed its pre-registered gate. The v2 gate failed, so today the classifier score raises no concern on its own. |
| `cant_tell` | We found no signed record and no concern. | Nothing. This is a complete answer, and it is not evidence either way. |

Two rules sit above the four answers:

1. Unsigned metadata is never a verdict on its own. It can only raise `concerns`.
2. Second Look never tells you an image is authentic. `signed_camera` says how a signer says the file was made. It does not say what the picture shows is true.

The full list of what we do and do not claim is in [CLAIMS.md](CLAIMS.md).

## Why it is built this way

Tools that put one number or one label on an image invite people to treat it as a verdict. Second Look is built around three choices instead:

- **Signed evidence first.** A signed provenance record is the strongest thing a file can carry, so it is checked first and it alone can produce a `signed_*` answer.
- **A classifier that can only raise a concern.** The local classifier reports a score. The score counts only at a threshold frozen on a development split before the test images were scored, and only after that exact threshold file passes the gate (`secondlook/rungs/classifier_gate.json`). The v2 gate failed, so the card shows the score, the frozen threshold and the failed test, and the score raises no concern on its own. It cannot produce a `signed_*` answer.
- **"Can't tell" as a first-class answer.** Most images carry no signed record. The tool says so plainly instead of guessing.

## How to run it

You need a Mac with Apple silicon and macOS 14 or later, [uv](https://docs.astral.sh/uv/) and Python 3.12. This is the one quickstart; the README has the same steps:

```
git clone https://github.com/prandata/secondlook.git
cd secondlook
./scripts/demo.sh
```

Wait for the line `Ready: open http://127.0.0.1:8765` (the first start loads the models, about 30 seconds, longer on a busy Mac), then open that address. The page shows the receipt for one file: drop a row's file from `samples/` on it, or pick one under "Try a sample". Keep that terminal open while you use the page; Ctrl-C in it stops the server. The first run installs pinned, hash-checked dependencies, fetches the classifier weights (87 MB) and the Video Seal watermark weights (228 MB, `--no-watermark` skips them), each with a progress bar, and the C2PA trust lists (each checked by sha256), downloads the 9 demo samples from their public sources, and starts the page. It binds to `127.0.0.1` only and loads nothing from the network. After that, `./scripts/demo.sh --offline` and `make test` need no network. If a download stops with HTTP 429 (the image host's rate limit), run the same command again: files already fetched and checked are kept.

The 9 samples cover all four answers: two Pixel 10 Pro captures (`signed_camera`), three Adobe Firefly files (`signed_ai`), two copies with the metadata stripped (`cant_tell`) and two with an unsigned AI label (`concerns`). `make clean-clone-test` proves this from a fresh clone with the network denied.

For one file on the command line, `.venv/bin/secondlook check photo.jpg` prints the full result as JSON: the answer, the summary, and every evidence card with its `proves` and `does_not_prove` fields. Run the tests with `make test`.

Status: pivot release candidate; see the repository's commit history.

Notes on what is where:

- The demo page (`secondlook.web`) is on `main`. `./scripts/demo.sh` serves it.
- On `main` today: the ladder and CLI, the provenance rungs (C2PA, IPTC, EXIF), the claim rung and the classifier rung. Merged is not measured: the classifier's gate has been run, and the v2 run on 29 Oct 2026 (AEDT) is a FAIL (see "Where the numbers are"), so the classifier card shows a score only. The gate runner is on `main` (`scripts/gate_run.sh`; see [GATE-RUN.md](GATE-RUN.md)). It is one offline command that checks the L4 lock, the splits and the frozen threshold, derives the test arms from the originals, scores them with the classifier rung, passes the predictions to the vendored harness and writes `RESULTS.md`. It has been run on real data twice: v1 was INVALID and v2 is a FAIL. [RESULTS.md](RESULTS.md) indexes both and the held-out-generator arm.
- The classifier rung is set up by `./scripts/demo.sh`: it installs the pinned dependencies in `requirements/classifier.lock` and fetches the 87 MB weights, checked by sha256, once. `--no-classifier` skips the classifier dependencies.
- After the first `./scripts/demo.sh` run, `make test` runs the test suite with no network. `.venv/bin/secondlook check photo.jpg` checks one file.
- `samples/recorded/` holds four recorded Muse Glimmer deep checks: samples 02, 04 and 08, and the Pentagon mock post. `scripts/record_investigations.py` wrote them. The page offers one only when no Muse Glimmer server answers. The file's sha256 must match the recording's. Samples 02, 04 and 08 are offered only with no caption. The Pentagon recording is offered only for `samples/recorded/pentagon-mock-post.png` with its caption, word for word, as in step 7. Spaces at either end of the caption are ignored. The model sha256 in each is of the file the running server reported loading. The projector sha256 is of the file `scripts/glimmer_server.sh` names, as llama-server does not report its projector. Both match the pins in `scripts/fetch_glimmer.sh`.
- The recorder runs from a snapshot of the checked commit.

## Where the numbers are

We measured the classifier on a held-out test set under a gate fixed in advance, and publish the observed rate and its upper bound for every arm, whatever they turn out to be. The v2 run (29 Oct 2026, AEDT) is a **FAIL**. In the `original` arm, 31 of 425 real photographs were flagged and the one-sided 95% upper bound on false positives is 9.718%, above the 2% limit. In the `format_matched` arm it is 1.814% (3 of 425). The lower bound on AI images caught is 98.983% in both gated arms (293 of 293). Every arm is **shortcut-exposed**: a baseline that reads only file facts reaches a balanced accuracy of 100.0%, and the detector's is 96.4% in `original` and 99.6% in `format_matched`. The AI side is Stable Diffusion 1.x, a generator family the detector was trained on. A separate held-out-generator arm, reported and not gated, caught 263 of 300 images from 18 generators in `original`. v1 was INVALID on the dev floor. The floors were not lowered. The index is [RESULTS.md](RESULTS.md); the full reports are [RESULTS-v2.md](RESULTS-v2.md) and [RESULTS-heldout.md](RESULTS-heldout.md).

On 983 TrueFake images (Facebook-shared copies, CC BY 4.0), the shipped ladder flagged 0 of 500 AI images, because the detector raises no concern on its own.

## Files in this folder

| File | What it is |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | The ladder and its rungs, as diagrams, with the data contract and branch status. |
| [CLAIMS.md](CLAIMS.md) | What we can claim, what we cannot, and the known limits. |
| [LICENSES.md](LICENSES.md) | Every dependency, model and data source, with its licence. |
| [RESULTS.md](RESULTS.md) | Index of the gate results: v1 INVALID, v2 FAIL, held-out arm reported. |
| [RESULTS-v2.md](RESULTS-v2.md) | The v2 gate report (FAIL): every arm, bounds, per-topic results, attrition and shortcut labels. |
| [RESULTS-heldout.md](RESULTS-heldout.md) | The held-out-generator arm: reported, not gated. |
