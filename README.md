# Second Look (in development)

A triage queue for the images a desk receives. A folder goes in. A ranked queue and an evidence receipt for every image come out. It never says "this is real". It is not affiliated with Meta. Everything runs locally with open-source parts (MIT, Apache, CC0, CC BY).

## A desk triage queue

A desk gets a folder of images and one question: which ones should a person look at first? `secondlook queue` checks every file in the folder and writes an evidence receipt for each. It then sorts the folder into three bands. Setup is the one command in the Quickstart below.

```
.venv/bin/secondlook queue <folder> --out <dir> [--form original|sq512] [--jobs N] [--pdf]
```

| Band | What puts an image there | Who acts |
|---|---|---|
| `uncertain` | Everything not in the other two bands, including any image with no detector score | A person, highest detector score first |
| `auto_decided` | The ladder answered `signed_ai` or `concerns`, or the detector score is at or above a frozen high threshold | No one, spot checks only |
| `likely_fine` | No signed AI record, no concern, and a detector score below a frozen low threshold. This is not proof that the image is real | No one, spot checks only |

The queue lists `uncertain` first, then `auto_decided`, then `likely_fine`. Each row names its band, its reason and the receipt behind it. The queue never changes the ladder's answer or a receipt.

Four thresholds were fitted on the development split of one public set, TrueFake (CC BY 4.0). Each of the two forms has a high and a low threshold. They are frozen in `secondlook/queue_bands.json`. Its sha256 is recorded beside it and pinned in `secondlook/queue.py`. The queue refuses to run if the file does not match both. Held-out images were never read when the thresholds were fitted. The detector's scores for all 983 images existed before the split. Only the four thresholds were fitted, on dev only. The detector's TrueFake numbers are not blind.

### What the held-out result says

One run on the 640 held-out TrueFake images: 314 camera photos and 326 AI images from five generators. These are the `sq512` figures: metadata stripped, centre-cropped to 512 by 512 pixels. Bounds are exact one-sided 95% upper bounds.

| What we counted | Result | 95% upper bound |
|---|---|---|
| Camera photos placed in `auto_decided` | 0 of 314 | 0.95% |
| AI images placed in `likely_fine` | 3 of 326 | 2.36% |
| Images decided without a person | 344 of 640 (53.8%): 227 `auto_decided` (all AI images) plus 117 `likely_fine` (114 camera photos, 3 AI images) | not applicable |

On files as given (`--form original`, the default), 312 of 640 (48.8%) were decided without a person. The other two rows are the same in that form.

Read these limits with the numbers:

- The 0.95% bound covers the `auto_decided` side only. The `likely_fine` side has 3 misses in 326 and an upper bound of 2.36%. We make no 1% claim for it. The 1% target was a dev-fitted operating point.
- All 3 AI images in `likely_fine` are Stable Diffusion 3. Their receipt ids and sha256 values are in [docs/FAILURES.md](docs/FAILURES.md).
- The detector trained on Stable Diffusion 1.x checkpoints, so the SD 1.5 and SD 2 rows are partly in-family. FLUX.1 and SD 3 are the nearer out-of-family tests. Most of their images go to a person.
- This is one public set. Every image in it is a copy shared through Facebook. A desk's own folder may behave differently.
- Dev and held-out share the same five generators and one camera source. New generators may behave differently.
- The 344 is the sum of two bands that mean different things. The 227 `auto_decided` images are routed as likely AI-made. The 117 `likely_fine` images are routed as likely fine, which proves nothing about any single image: 3 of the 117 are AI-made.
- The gate failed, and the queue still auto-decides on the detector score. Both are true. The gate tested one fixed threshold on a different image set and failed on 29 Oct 2026, so the ladder still raises no concern from the detector alone. The queue uses the same score with two dev-fitted cut-offs per form, fitted on TrueFake dev, with a person band between them. They decide which band a row goes in, never the receipt's answer.

Details, files written and the fitting rules: [docs/QUEUE.md](docs/QUEUE.md). What we do not claim, and where it went wrong: [docs/FAILURES.md](docs/FAILURES.md) and [docs/CLAIMS.md](docs/CLAIMS.md).

## The receipt behind each row

Every row in the queue points to a receipt. That receipt is a single file run through the evidence ladder. A desk opens it to read every evidence card and its limit. The single-image check is the same ladder on one file: `.venv/bin/secondlook check photo.jpg --receipt DIR`, or the local page described below. The ladder answers one of four things. Evidence cards back each answer and state what they prove and what they don't:

- `signed_ai`: a valid Content Credentials (C2PA) manifest says AI made it.
- `signed_camera`: a valid C2PA manifest says a camera captured it.
- `concerns`: an AI label in unsigned metadata, or a local detector score at or above a threshold frozen in advance, only after that threshold passed its pre-registered gate. The v2 gate failed, so today the detector shows a score only. These are reasons, not a verdict.
- `cant_tell`: no signed record and no concern. This is a real answer.

## Quickstart

You need a Mac with **Apple silicon and macOS 14 or later** (the pinned torch has wheels for nothing else) and [uv](https://docs.astral.sh/uv/) (`brew install uv`) with Python 3.12 on the machine. Clone the repository, then one command sets everything up. It does not start the page: the queue comes first.

```
git clone https://github.com/prandata/secondlook.git
cd secondlook
./scripts/demo.sh --no-serve
```

### 1. Run the queue on the samples folder

```
.venv/bin/secondlook queue samples --out queue-out
```

This checks the 9 demo samples and the recorded Pentagon image in `samples/recorded/`. It writes a receipt for each image in `queue-out/receipts/` and the ranked list in `queue-out/queue.csv` and `queue-out/queue.json`. `secondlook queue` is described under "A desk triage queue" above.

### 2. Open a receipt

```
./scripts/demo.sh
```

This starts the local receipt page. It shows the full receipt for one row of the queue you just ran: drop that row's file from `samples/` on the page, or pick it under "Try a sample". When the terminal prints `Ready: open http://127.0.0.1:8765`, open that address. With no model installed, you can still see a recorded Muse Glimmer run. Pick sample 02, 04 or 08 under "Try a sample". Then press "Show the recorded run (not live)" under Deep check. It shows a run we recorded on that exact file, matched by its sha256 fingerprint (`samples/recorded/`). Its label gives the machine, the date and the seconds the run took. One recording has a caption: the video's Pentagon case (`samples/recorded/pentagon-mock-post.png`, with the caption in step 7 of the judges' guide). It is offered only for that file and that caption. Keep that terminal open while you use the page; Ctrl-C in it stops the server. The first start loads the models (about 30 seconds, longer on a busy Mac). It also runs one check on a bundled sample, so your first drop is quick. The two large downloads (87 MB and 228 MB) show a progress bar. Short on time? [docs/README-for-judges.md](docs/README-for-judges.md) starts at the queue on the samples folder and opens one receipt second: four steps, one hardware line.

What it does, skipping any step already done:

1. makes `.venv` with Python 3.12;
2. installs the pinned dependencies, every one checked by hash: `requirements/build.lock` (the build backend), `requirements/demo.lock` and, because the classifier rung is in the tree, `requirements/classifier.lock`. The package itself is then installed with `--no-build-isolation`, so no build step fetches anything unpinned;
3. fetches the model weights and checks each sha256: Community Forensics (MIT, 87 MB, a pinned Hugging Face revision) and Meta Video Seal 1.0 (MIT, 228 MB, for the watermark rung; `--no-watermark` skips it, and on a `cant_tell` answer the page then lists the watermark check under "could not run"; other answers do not carry that line);
4. fetches the C2PA trust lists (the official list and the interim list) and checks each sha256;
5. runs `scripts/fetch_samples.sh`: it downloads the 9 demo samples into a real `samples/` folder from their public sources (Wikimedia Commons; licences in the script header and `docs/LICENSES.md`) and makes four of them from those downloads. It also downloads the public test files the test suite reads into `data/test-fixtures/`. Every file is checked against a sha256 pinned in the script. Nothing is bundled in this repo;
6. serves the page on 127.0.0.1 only, with the "Try a sample" gallery, and runs one warm-up check on a bundled sample before it prints `Ready`. It stops with an error if the port is already in use, ignores proxy settings for its own requests to 127.0.0.1, and stops its server when you press Ctrl-C. `--no-serve` stops before this step.

The first run needs about 2.1 GB of free disk by default, or about 1.9 GB with `--no-watermark` (measured 29 Oct 2026 after a cold-cache install of the locks: uv cache 882 MB, `.venv` 868 MB, Community Forensics weights 87 MB, Video Seal weights 228.5 MB, samples 25 MB). After it, these work with no network:

```
./scripts/demo.sh --offline
make test
```

`make test` runs the test suite. `make clean-clone-test` clones HEAD to a temp folder with a fresh, empty HOME, sets it up, then runs the tests and the page with all outbound network denied, and posts all 9 demo samples to it. It fails unless every sample gives its expected answer with a non-empty card list. `./scripts/demo.sh --help` lists the other options. The command line checker is `.venv/bin/secondlook check photo.jpg`.

Add `--receipt DIR` to write an evidence receipt (JSON, its sha256 and a PDF): see `docs/RECEIPT.md`.

Demo watermark (our own mark only; it does not detect Meta AI images or anyone else's mark):

```
.venv/bin/python -m secondlook.rungs.watermark --fetch      # Meta Video Seal 1.0, MIT, sha256-pinned (demo.sh already did this)
.venv/bin/secondlook mark photo.jpg photo-marked.jpg
.venv/bin/secondlook check photo-marked.jpg                 # shows a "watermark" card; the answer is unchanged
```

The absence of a mark says nothing about how an image was made. Robustness and limits: `docs/watermark-robustness.md`.

## A whole folder (B1, DAT-871)

```
.venv/bin/secondlook batch photos/ --out report/ [--jobs N [--allow-many-workers]] [--no-pdf]
```

Checks every `.jpg`, `.jpeg`, `.png` and `.webp` under the folder, each on its own through the same ladder as `secondlook check`, and writes `report/summary.html` (one static page, nothing fetched), `summary.json`, `summary.csv`, one receipt per file in `report/receipts/`, and `run.json` (timings). `summary.json`, `summary.csv` and `summary.html` are byte-for-byte the same for the same folder and code, whatever `--jobs`. The receipts are not part of that promise: each one records when it was made, and each `.json` receipt has a `.json.sha256` beside it, so those files differ from run to run.

- **Skipped, with the reason listed:** a symlink that leaves the folder (never opened), a symlinked folder, anything that is not a regular file, an empty file, a file over 25 MB, and an image over the classifier's limits (100 million pixels, or longer than 8:1).
- **Near-duplicate copies** (a 64-bit difference hash within 8 bits, or an identical sha256) are grouped for information only. They are not evidence and never change an answer. A group is a chain of links, and the summary gives its widest pair. Flat pictures have no usable hash and are grouped only when the files are identical.
- **Workers:** `--jobs` processes, each loading the classifier once. The default is 2: each holds its own copy of the model and they share one GPU, and more did not help on the M5 (see the performance table). `--jobs` is capped at the smaller of 4 and half the CPU cores; going higher needs `--allow-many-workers`, and each worker holds about 0.9 GiB with the classifier. From Python, `run_batch(..., jobs=2)` starts spawned workers, so call it under `if __name__ == "__main__":`; without that it raises an error that says so (`jobs=1` needs no guard).
- **Web:** `POST /api/batch` on the local page takes up to 20 images in the form field `files` (25 MB each, 100 MB in all) and returns one result per file plus the near-duplicate groups. The server still binds to 127.0.0.1 only.
- **Speed and memory** on the M5, with and without the classifier: [docs/PERFORMANCE.md](docs/PERFORMANCE.md) (`scripts/bench.py`).

Rungs live in `secondlook/rungs/<name>.py` and each exposes `check(path) -> list[Evidence]`.

In this snapshot the absolute run path in `docs/RESULTS-v2.md` and `docs/gate-runs/v2-gate-run.json` is replaced by `<runs>/L4-test-set`, and `docs/gate-runs/v2-heldout-run.json` records the sha256 of that edited file.

In this snapshot the fact-checker's own explanation text (`rating_explanation`) is set to `null` in `docs/M2-INVESTIGATOR-SMOKE.json`; the claim lines, ratings and links are kept.

## Classifier rung (S3, DAT-848)

`secondlook/rungs/classifier.py` runs Community Forensics (Park and Owens, CVPR 2025; code MIT at github.com/JeongsooP/Community-Forensics, weights MIT at huggingface.co/OwensLab/commfor-model-384, pinned by revision and sha256) locally, on torch MPS when available. It reports a score and never a verdict. The score is a concern only at or above the threshold in `secondlook/rungs/classifier_threshold.json`, frozen on the L4 dev split only with `python -m secondlook.calibrate`. The gate arms are `original` and `format_matched` (the project lead ruling, DAT-849 ec3ad415): the dev split is scored in each arm, the rule (lowest threshold with dev real FPR at most 2%) is applied to each arm's dev scores, and the higher of the two thresholds is frozen, so dev real FPR is at most 2% in both arms. `original` scores the fetched files dev.csv names; `format_matched` scores `<images-derived>/format_matched/<item_id>.jpg`, made from the original by L4 `scripts/derive.py` (JPEG q90, long side at most 1,536 px, shrink only). Each score row records the arm and the sha256 of the file scored. `freeze` refuses a missing or repeated arm, a file that mixes arms, and any arm whose ids are not exactly the dev ids. `threshold.json` records `gate_arms`, each arm's own threshold, dev scores sha256 and dev metrics, and which arm set the frozen number. Commit it before any test image is scored: git order is the proof. Until that file exists, or if it is not truly frozen (dev and test split hashes, frozen_at and frozen_by present, a finite threshold in [0, 1]), the card shows the score for information and the ladder cannot act on it. A frozen threshold also has to pass the gate: the score raises a concern only when `secondlook/rungs/classifier_gate.json` records `PASS` for that threshold file's exact sha256 (the project lead ruling, DAT-850). With no status, `FAIL`, `INVALID`, a status for another threshold or an unreadable file, the card shows the score, the threshold and the test result, and raises no concern. The v2 gate failed (`gate-20261029-040012`, [docs/RESULTS-v2.md](docs/RESULTS-v2.md)), so the committed status is `FAIL` and the classifier is score only. Both calibrate commands refuse anything but the frozen L4 dev split: the L4 `LOCK.json` must still match the files it locks, and the CSVs must carry the sha256 values in SPLIT.json. Images over 100 million pixels or longer than 8:1 are not scored (checked from the header, before resize).

```
uv pip install -p .venv/bin/python -e '.[test,classifier]'
.venv/bin/python -m secondlook.rungs.classifier --fetch      # 87 MB, once, sha256 checked
.venv/bin/python -m secondlook.calibrate score --arm original --split dev.csv --split-json SPLIT.json \
    --l4-dir <L4 folder> --root <images> --out dev_scores_original.csv
.venv/bin/python -m secondlook.calibrate score --arm format_matched --split dev.csv --split-json SPLIT.json \
    --l4-dir <L4 folder> --derived-root <L4 folder>/images-derived --out dev_scores_format_matched.csv
.venv/bin/python -m secondlook.calibrate freeze --scores dev_scores_original.csv dev_scores_format_matched.csv \
    --split-json SPLIT.json --dev-csv dev.csv --test-csv test.csv --l4-dir <L4 folder> \
    --frozen-by <agent> --out secondlook/rungs/classifier_threshold.json
```

## Meta Muse Glimmer features (optional, M1, DAT-869; M2, DAT-873)

Optional features run Meta's open-weight Muse Glimmer 30B (Apache 2.0) on this machine, on loopback only: reading the words printed in an image; picking which clauses of a caption to search for in the fact-check lookup (the code splits the caption into clauses and refuses any pick that is not a whole clause word for word); and the investigator (M2), which calls Second Look's checks as tools (`secondlook investigate photo.jpg --caption "..."`, `POST /api/investigate`). Model output is used only as a search string or as a choice of which check to run next. None of it is evidence, and the model never sets the answer; fixed rules do. No model writes text a reader sees: explanations are fixed templates. Details: `docs/M1-GLIMMER.md`, `docs/M2-INVESTIGATOR.md`.

**For adult and professional use.** Meta's usage policy for the model says "Muse Glimmer is not intended for individuals under the age of 18" (`USAGE_POLICY.md` in huggingface.co/meta-models/Muse-Glimmer-30B, revision and sha256 in `docs/LICENSES.md`). The Glimmer features are offered for adult and professional use on that basis. Second Look has no age gate.

Number plates, phone numbers and email addresses are removed from every on-image reading before it is shown, stored or searched.

```
scripts/fetch_glimmer.sh             # Meta's own GGUF files, pinned revision, sha256 checked; needs 40 GB free
scripts/glimmer_server.sh start      # llama-server on 127.0.0.1:8081, loading Meta's pinned files
.venv/bin/python -m secondlook.glimmer image photo.jpg
scripts/glimmer_server.sh stop
```

## Claim box and voice notes (W10, DAT-850)

The local page (`python -m secondlook.web`) has a "Check a claim" box and a "Voice note" box beside the image drop. In the voice box you choose or drop a file. Each panel shows only when `/api/health` says its part can run on this computer. Claims are looked up in an offline index of the Data Commons ClaimReview feed (compilation CC BY, https://datacommons.org/factcheck/download; each review stays its publisher's own work, and every match shown names the publisher, date and link). Build the index once (about 200 MB to download, then a few seconds):

```
curl -L -o claimreview.json https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json && .venv/bin/python -m secondlook.rungs.claim --build claimreview.json ~/.cache/secondlook/claims.sqlite --downloaded-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --sha256 "$(shasum -a 256 claimreview.json | cut -d' ' -f1)"
```

`~/.cache/secondlook/claims.sqlite` is the default; set `SECONDLOOK_CLAIMS_DB` to use another path. Without an index the claim box answers "Claims index not installed", never an error page. Voice notes need whisper.cpp and its pinned model: see `docs/W9-voice.md`. Details: `docs/W10-DESK.md`.
