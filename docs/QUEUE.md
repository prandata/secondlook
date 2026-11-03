# Desk triage queue (J4, DAT-1367)

```
.venv/bin/secondlook queue photos/ --out desk/ [--form original|sq512] [--jobs N [--allow-many-workers]] [--pdf]
```

A folder in, a ranked queue out. Every image runs through the same ladder as `secondlook check` (C2PA, IPTC, EXIF, the detector score, the watermark), and gets the existing evidence receipt (`secondlook.receipt`, schema `second-look-receipt/1`) with its `.json.sha256`. The queue then puts each image in one of three bands and ranks them. It never changes the ladder's answer or a receipt.

| Band | When | Who acts |
|---|---|---|
| `uncertain` | Everything not in the two bands below, including any image with no detector score, and a signed camera record with a score at or above `t_hi` | A person, highest detector score first |
| `auto_decided` | The ladder answered `signed_ai` or `concerns`, or the detector score is at or above `t_hi` | No one (spot checks) |
| `likely_fine` | No signed AI record and no concern, and the detector score is below `t_lo`. Not proof that the image is real | No one (spot checks) |

Queue order: `uncertain`, then `auto_decided`, then `likely_fine`; inside a band, the highest detector score first, images without a score last, then the file path.

## The thresholds

`secondlook/queue_bands.json`, sha256 in `secondlook/queue_bands.json.sha256` and pinned in code as `BANDS_SHA256` in `secondlook/queue.py`. The queue checks both before it reads a single image. It refuses to run if the file does not match both. A refit changes the file, its sidecar and `BANDS_SHA256` in one commit. Fitted by `scripts/fit_queue_bands.py` with rule BAND of the J3 pre-registration (`arena/PREREG.md`, section 5, DAT-1366), on the TrueFake dev split only (CC BY 4.0; 169 real photos and 174 AI images; held-out rows are never read):

- `t_hi` (rule OP): the threshold with the largest dev catch such that at most floor(0.01 x 169) = 1 dev real photo is at or above it; ties to the highest.
- `t_lo`: the largest dev score such that at most floor(0.01 x 174) = 1 dev AI image is below it.
- A band is used only while its dev error rate in the file is 1% or less; otherwise its images go to a person.

Each form has its own pair, fitted on its own dev scores: `original` (files as given, the CLI default) and `sq512` (metadata stripped, centre-cropped 512 x 512 JPEG, quality 90, the pre-registration's headline form; use it only on folders of such crops).

One deviation from PREREG's wording, which no TrueFake row can reach (the ladder answered `cant_tell` on all of them): a signed camera record with a score at or above `t_hi` is a conflict and goes to a person.

## What this is not

- Not the classifier gate. The 29 Oct gate (`secondlook/rungs/classifier_gate.json`) stays FAIL and its frozen threshold 0.4320 is unchanged, so the ladder still never raises a concern from the detector. `t_hi` and `t_lo` are a separate, dev-fitted operating point for routing images on a desk.
- Not a certified error rate. The 1% target is met on the dev split by construction; held-out results are reported separately, with exact bounds, by `scripts/queue_band_shares.py`.
- Not a statement that anything is real. `likely_fine` means the detector score was very low and nothing else was found.

## Files written

| File | What |
|---|---|
| `queue.json`, `queue.csv` | Every checked image in queue order: rank, band, reason, detector score, ladder answer, the file's sha256, and its receipt's path and sha256. Deterministic for the same folder, code, bands file and run time: each receipt records its `created_at`, so `run_time` (an argument of `queue.run_queue`, a `%Y-%m-%dT%H:%M:%SZ` text) pins it for a reproducible run, and queue.json records it under `run_time` (null for a normal run, which records the real time). Reasons show a score and its threshold with 6 significant figures, more when two different numbers would print alike |
| `receipts/NNNN-<name>.json` | The ladder's receipt, unchanged, with `.json.sha256` beside it (and a PDF with `--pdf`) |
| `run.json` | Workers and seconds per image; differs on every run |

Tests: `tests/test_queue.py` (fitting, banding, ranking, receipts, the frozen file and the CLI). No test starts a browser.
