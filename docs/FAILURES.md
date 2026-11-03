# Published failures

Status: draft, 2 Nov 2026 (AEDT). This page lists where Second Look failed or fell short, with the numbers as observed. We publish it so a desk can see the misses before it relies on the queue.

Every number here comes from one public set, TrueFake (Dell'Anna et al., IJCNN 2025; the Facebook-shared subset; CC BY 4.0). No other dataset is used on this page. Rates are counts with exact one-sided 95% bounds (`gate/stats.py`). Nothing here is a general rate.

| # | Failure | Where |
|---|---|---|
| 1 | The shipped ladder caught none of 500 AI images at scale, because its detector gate failed | Section 1 |
| 2 | Three AI images went to `likely_fine` | Section 2 |
| 3 | We make no 1% claim for the `likely_fine` side | Section 3 |
| 4 | Muse Image misses | Section 4 (pending) |

## 1. The shipped ladder caught 0 of 500 AI images

The DAT-1321 scale evaluation ran the ladder as shipped on all 983 TrueFake files. That is 483 camera photos and 500 AI images. The code was Second Look at `b5953956ffa2d047c16715334b90d851e2d76454`. It ran twice, on `sq512` crops and on the files as given.

| Form | Camera photos flagged | AI images flagged | Answer on every file |
|---|---|---|---|
| `sq512` | 0 of 483 | 0 of 500 | `cant_tell` (983 of 983) |
| files as given | 0 of 483 | 0 of 500 | `cant_tell` (983 of 983) |

Why it caught nothing:

- The ladder raises a concern from the detector only if the classifier gate passed. The gate failed on 29 Oct 2026 (run `gate-20261029-040012`, [RESULTS-v2.md](RESULTS-v2.md)). The gate status on all 983 rows is `FAIL`, and no row raised a concern from the score.
- No file in the set had a C2PA record, an AI label in IPTC or EXIF, or our watermark. No other rung found anything that changes an answer.
- So the ladder gave its designed answer, `cant_tell`, on every file. For a desk with a folder of such images, that answer sorts nothing. This is why the queue exists.

This is not a measure of the detector. It is a measure of the ladder as shipped, on images that carried no signed record. The per-image rows are in `scores.csv` of the DAT-1321 run. That file is not in this repository; its sha256 is `b313f58e083837be9ff96059bf538d65558e2039dbc41a58e3b3929cec4b531a`, so a copy can be checked against it.

## 2. Three AI images in `likely_fine`

Held-out TrueFake, 326 AI images. The queue put 3 of them in `likely_fine`, the band no one looks at. All 3 are Stable Diffusion 3: 3 of the 70 SD 3 images. The band label says "not proof that the image is real". These three images were AI-made.

The same 3 images fall in `likely_fine` in both forms. The table names them by receipt and hash. It shows no images.

| # | TrueFake file | File sha256 (as given) | Receipt id (`sq512` run) | Receipt sha256 | Detector score, `sq512` / original |
|---|---|---|---|---|---|
| 1 | `Facebook/Fake/StableDiffusion3/faces/00490.jpg` | `b2fe58cc0586d5ee1b77eefce5e3b41eb43c52393f610ead479a5b458c7e4d47` | `0449-b2fe58cc0586d5ee1b77.jpg.json` | `131d19998693bda1fe562f348f43884794412983f2671defd1b8333ad89e929b` | 0.000142 / 0.000059 |
| 2 | `Facebook/Fake/StableDiffusion3/landscapes/00607.jpg` | `e63a889e5526bdf369b3bc093318476e6761942ee4bccc2b5594b825ecdcaf5c` | `0582-e63a889e5526bdf369b3.jpg.json` | `944e5e2a4112a4d999a234a9ea544df5a80b6179e2698842bf4259819bc96e79` | 0.000136 / 0.000247 |
| 3 | `Facebook/Fake/StableDiffusion3/landscapes/01151.jpg` | `c095f206a1d52ea56e80baa8fa999b1d8af5c562f9d5d333f8832336ce2f7bef` | `0495-c095f206a1d52ea56e80.jpg.json` | `1a743cd66ffb01c5f8cb3d6e1bfef75306f047df059437aad3fba58cb31db95b` | 0.00005 / 0.000116 |

The frozen "low" thresholds are 0.000235 (`sq512`) and 0.000367 (original), in `secondlook/queue_bands.json`. Each score above is below its form's threshold.

Notes on the table:

- The receipt records the sha256 of the file the queue read. In the `sq512` run that is the crop: `c05ac5327369e79516dbf5e68c33a818d414e06d90d96e40f4679f532d6591c5` (row 1), `ada1b95fa4dd986432f3a18f0e560d8746669196f549fb2dafc471fdb137b531` (row 2) and `16ce5751d38278eb574f6bb96788da6db1ca58ae32735ddeeee250be195ebbc9` (row 3).
- The receipt ids and receipt sha256 values come from the `sq512` replay build of the held-out queue run. The replay files are not in this repository. Each receipt sha256 is the hash of the receipt file named by its receipt id, and it matches the `.sha256` file beside that receipt. Rows 1, 2 and 3 were created at 02:20:21Z, 02:20:24Z and 02:20:22Z on 2 Nov 2026. A receipt records when it was made, so another run gives other receipt hashes. The file sha256 values do not change.
- The split file, `split.csv`, is not in this repository either (sha256 `16739fc7b4bdf8f9ea4eac84af29d04cfbdf9102d6730cacfbd8a0f00665ca1e`). The count of 3 of 326, with its bound of 2.36%, is in [QUEUE.md](QUEUE.md) and [README.md](../README.md).

## 3. No 1% claim for the `likely_fine` side

The 1% target is a dev-fitted operating point. On the development split, 1 of 174 AI images fell in `likely_fine`, by construction. On held-out the count is 3 of 326, with a 95% upper bound of 2.36%.

| Side | Held-out result | 95% upper bound | Can we say "at most 1%"? |
|---|---|---|---|
| Camera photos in `auto_decided` | 0 of 314 | 0.95% | Yes, as the bound on this set only |
| AI images in `likely_fine` | 3 of 326 | 2.36% | No |

We state the count and the bound for `likely_fine`, and never "under 1%". The 3 misses are all SD 3. We have not tested whether more SD 3 images, or other new generators, would add more.

## 4. Muse Image misses: pending, only if the Arena runs

| Slot | Status |
|---|---|
| Images made with Muse Image that the queue sent to `likely_fine` or missed | Pending. Filled only if the Arena run happens and a reviewer has checked the rows. No numbers until then. |

## What this page does not do

- It does not report the detector's catch rate on other datasets or on the gate's own test set. Those are in [RESULTS-v2.md](RESULTS-v2.md) and [RESULTS-heldout.md](RESULTS-heldout.md).
- It does not say the queue works on a desk's own folder. It says what happened on 640 held-out images from one source.
