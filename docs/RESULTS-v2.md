# Results

Gate verdict from the harness: **FAIL** (exit code 1). It is published as it stands, with every arm, the bounds, the per-topic results, the attrition and the shortcut labels.

Reasons the harness gave:

- original: false-positive upper bound 9.718% is above 2%

## The run

| Field | Value |
|---|---|
| Run id | gate-20261029-040012 |
| Date (UTC) | 2026-10-29 |
| Gate verdict, harness exit code | FAIL, 1 |
| Gated arms (both must pass) | original, format_matched |
| Secondlook commit | c59f0644890aed1216257066c07fcc9c45e7b4f3 |
| Classifier | Community Forensics ViT-S/16 384 (OwensLab/commfor-model-384), version 6076002bf0d9dd37537f965ee2f06f826c333b61 |
| Weights sha256 | b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387 |
| Threshold | 0.4319957792758942 (AI if score>=threshold with threshold 0.4319957792758942; never abstain) |
| Threshold file sha256 | 686e0e6379be42271501c7016e4c16b1cdfef759904818f6382c5f37479eb53c |
| Threshold committed in git | e4c8879349ed628d113a83ed45330f300fdb02c8 at 2026-10-29T03:51:39Z |
| Frozen at, frozen by | 2026-10-29T03:51:27Z, the project lead |
| Dev split sha256 | fb2dc36a0974ca29c0f95116ff0737f6385e731f96c6b3d370e433e820ed4fd5 |
| Test split sha256 | f59847e2b5978618003e709eb26a2e3ed49b3b640f6f413f3f4b2336db37a3ca |
| L4 LOCK.json sha256 | 04ebb07c1d7e7704d6577ffa83379778429df61fa701bfccf5075d2900ae4e93 |
| SCREEN-CONFIG.json sha256 | 4e8f7c9af51912614f09f4b4a670894ac1817077151692985cb2d788bec08afa |
| ACCEPTED.json sha256 | 07d2fc22535d7456c489eabb07b7a6d3bda028aeef1ec7150ee651f5a7860482 |
| Originals read from (gate_splits --file-base) | <runs>/L4-test-set |
| Predictions file sha256 | 6715638ebdec5b2e997bd3de75a30f05da796fba65cfe399b8b97ad93351f7da |
| Recipe R1 code (scripts/derive.py) sha256 | 53d6ad997d914b93cb617c45eeb9b2b79786a9af737064482e99872f220bf6b1 |
| Derive environment | libjpeg 6.2, pillow 13.0.0, python 3.12.13, zlib 1.3.1.zlib-ng |

Detector training data, as stated in the threshold file: Detector: Community Forensics ViT-S/16 384 (OwensLab/commfor-model-384, revision 6076002b, Park and Owens, CVPR 2025). Overlap verdict: same generator family on the AI side; no image-level overlap found on either side. AI side: the detector's training generators include the Stable Diffusion 1.x checkpoints themselves (CompVis/stable-diffusion-v1-1, v1-2, v1-3, v1-4 and runwayml/stable-diffusion-v1-5) among 4,763 Hugging Face latent diffusion models in the Systematic subset it trains on (systematic_set_metadata.csv; the dataset card: 'a vast majority of the generators we collect are derivatives of Stable Diffusion'). Our AI test images are Stable Diffusion 1.x output from DiffusionDB, exact version unrecorded, so the AI side is an in-family test, not a held-out-generator test. DiffusionDB is not named as a source in the paper, the dataset card or the code repository's README; the authors sampled the training images themselves from captions of real datasets or BLIP (paper section 3.5), so DiffusionDB's own images are not in it, and the released LAION training link list holds no DiffusionDB link. Real side: Wikimedia Commons is not a named real-image source. The real training images are LAION 40.30%, ImageNet 40.30%, CelebA 7.15%, COCO 4.39%, LandscapesHQ 3.34%, FFHQ 2.34%, IMD2020 1.17%, AFHQv2 0.59%, VISION 0.25%, Forchheim 0.13% and MetFaces 0.05% (dataset card). The released LAION link list (2,000,000 rows) holds 2,034 Commons file URLs and none of the 1,088 Commons files in our manifest (matched by file name, 28 Oct 2026). Not checked: ImageNet and the other real sets image by image, copies of our photos at other URLs, and the backbone's CLIP pretraining on LAION-2B (paper section 4.1), which is web-crawled and may hold Commons photographs; Stable Diffusion 1.x was itself trained on subsets of LAION-2B(en) (its model card). Overlap with the real side's distribution is therefore possible but not measured.

## What was measured

| | Real | AI |
|---|---|---|
| Dev split (threshold frozen here only), units | 125 | 120 |
| Test split, independent units, one image each | 425 | 293 |

Gate floors, set in advance: test at least 400 real and 200 AI per gated arm; dev at least 100 and 100. A run below a floor is INVALID. Pass condition in each gated arm: the one-sided 95% exact Clopper-Pearson upper bound on false positives is at most 2% and the lower bound on AI images caught is at least 40%.

## Per arm

One table per arm, each over the same test units unless its coverage line says otherwise. No figure is added up across arms. Rates are as observed; the bounds in the gate table and per-topic tables are one-sided 95% exact (Clopper-Pearson); the class tables give each recall a two-sided 95% exact interval.

Diagnostic arms: every accepted unit was derived into every arm, so each arm covers the same units.

### original (the fetched bytes, untouched)

Role: **gated**.

On 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 293 unit-level Stable Diffusion 1.x images from one August 2022 sample, observed false-alarm rate 31/425 (7.294%), one-sided 95% upper bound 9.718%; observed catch rate 293/293 (100.000%), lower bound 98.983%; arm: original.

| Arm | n real | Real flagged (observed) | Upper bound on false positives | n AI | AI flagged (observed) | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| original | 425 | 31/425 (7.294%) | 9.718% | 293 | 293/293 (100.000%) | 98.983% |

Gate conditions in this arm: false-positive upper bound at most 2%: not met; AI-caught lower bound at least 40%: met; floors of 400 real and 200 AI: met.

Detector in `original`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 394/425 | 92.706% | 89.806% to 94.990% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, detector, `original`: 96.353%, interval 93.943% to 97.633% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

Shortcut baseline in `original` (depth-3 tree on file facts, fitted on 245 dev units, scored on 425 real and 293 AI test units). Features the tree used: source_jpeg_quality.

Shortcut baseline in `original`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 425/425 | 100.000% | 99.136% to 100.000% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, shortcut baseline, `original`: 100.000%, interval 98.745% to 100.000% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

**Shortcut label, original: shortcut-exposed: the file-facts baseline reaches a balanced accuracy of 100.0% (interval 98.7% to 100.0%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.75 or more; the detector's is 96.4% (interval 93.9% to 97.6%; conservative, at least 95%, conditional on per-class binomial unit sampling), 3.6 points below it (point estimates), so the detector does not exceed this specified shortcut baseline by at least 10 points.**

Per topic, `original` (one-sided 95% bounds on small counts; not gate results):

| Topic | n real | Real flagged | Upper bound on false positives | n AI | AI flagged | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| animal | 97 | 3/97 (3.093%) | 7.799% | 100 | 100/100 (100.000%) | 97.049% |
| building | 94 | 12/94 (12.766%) | 19.862% | 13 | 13/13 (100.000%) | 79.418% |
| food | 19 | 3/19 (15.789%) | 35.943% | 74 | 74/74 (100.000%) | 96.033% |
| landscape | 70 | 5/70 (7.143%) | 14.433% | 55 | 55/55 (100.000%) | 94.699% |
| object | 70 | 5/70 (7.143%) | 14.433% | 42 | 42/42 (100.000%) | 93.116% |
| plant | 75 | 3/75 (4.000%) | 10.015% | 9 | 9/9 (100.000%) | 71.687% |

### format_matched (JPEG q90, at most 1,536 px, shrink only)

Role: **gated**.

On 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 293 unit-level Stable Diffusion 1.x images from one August 2022 sample, observed false-alarm rate 3/425 (0.706%), one-sided 95% upper bound 1.814%; observed catch rate 293/293 (100.000%), lower bound 98.983%; arm: format_matched.

| Arm | n real | Real flagged (observed) | Upper bound on false positives | n AI | AI flagged (observed) | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| format_matched | 425 | 3/425 (0.706%) | 1.814% | 293 | 293/293 (100.000%) | 98.983% |

Gate conditions in this arm: false-positive upper bound at most 2%: met; AI-caught lower bound at least 40%: met; floors of 400 real and 200 AI: met.

Detector in `format_matched`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 422/425 | 99.294% | 97.951% to 99.854% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, detector, `format_matched`: 99.647%, interval 98.121% to 99.944% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

Shortcut baseline in `format_matched` (depth-3 tree on file facts, fitted on 245 dev units, scored on 425 real and 293 AI test units). Features the tree used: source_jpeg_quality.

Shortcut baseline in `format_matched`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 425/425 | 100.000% | 99.136% to 100.000% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, shortcut baseline, `format_matched`: 100.000%, interval 98.745% to 100.000% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

**Shortcut label, format_matched: shortcut-exposed: the file-facts baseline reaches a balanced accuracy of 100.0% (interval 98.7% to 100.0%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.75 or more; the detector's is 99.6% (interval 98.1% to 99.9%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.4 points below it (point estimates), so the detector does not exceed this specified shortcut baseline by at least 10 points.**

Per topic, `format_matched` (one-sided 95% bounds on small counts; not gate results):

| Topic | n real | Real flagged | Upper bound on false positives | n AI | AI flagged | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| animal | 97 | 0/97 (0.000%) | 3.041% | 100 | 100/100 (100.000%) | 97.049% |
| building | 94 | 0/94 (0.000%) | 3.137% | 13 | 13/13 (100.000%) | 79.418% |
| food | 19 | 2/19 (10.526%) | 29.580% | 74 | 74/74 (100.000%) | 96.033% |
| landscape | 70 | 0/70 (0.000%) | 4.189% | 55 | 55/55 (100.000%) | 94.699% |
| object | 70 | 1/70 (1.429%) | 6.598% | 42 | 42/42 (100.000%) | 93.116% |
| plant | 75 | 0/75 (0.000%) | 3.916% | 9 | 9/9 (100.000%) | 71.687% |

### compressed_q70_1080 (JPEG q70, at most 1,080 px; AI images are mostly 512 px and stay that size)

Role: reported, not gated.

On 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 293 unit-level Stable Diffusion 1.x images from one August 2022 sample, observed false-alarm rate 3/425 (0.706%), one-sided 95% upper bound 1.814%; observed catch rate 291/293 (99.317%), lower bound 97.867%; arm: compressed_q70_1080.

| Arm | n real | Real flagged (observed) | Upper bound on false positives | n AI | AI flagged (observed) | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| compressed_q70_1080 | 425 | 3/425 (0.706%) | 1.814% | 293 | 291/293 (99.317%) | 97.867% |

Detector in `compressed_q70_1080`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 422/425 | 99.294% | 97.951% to 99.854% |
| AI (true-positive rate) | 291/293 | 99.317% | 97.556% to 99.917% |

Balanced accuracy, detector, `compressed_q70_1080`: 99.306%, interval 97.492% to 99.916% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

Shortcut baseline in `compressed_q70_1080` (depth-3 tree on file facts, fitted on 245 dev units, scored on 425 real and 293 AI test units). Features the tree used: source_jpeg_quality.

Shortcut baseline in `compressed_q70_1080`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 425/425 | 100.000% | 99.136% to 100.000% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, shortcut baseline, `compressed_q70_1080`: 100.000%, interval 98.745% to 100.000% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

**Shortcut label, compressed_q70_1080: shortcut-exposed: the file-facts baseline reaches a balanced accuracy of 100.0% (interval 98.7% to 100.0%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.75 or more; the detector's is 99.3% (interval 97.5% to 99.9%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.7 points below it (point estimates), so the detector does not exceed this specified shortcut baseline by at least 10 points.**

Per topic, `compressed_q70_1080` (one-sided 95% bounds on small counts; not gate results):

| Topic | n real | Real flagged | Upper bound on false positives | n AI | AI flagged | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| animal | 97 | 0/97 (0.000%) | 3.041% | 100 | 99/100 (99.000%) | 95.344% |
| building | 94 | 1/94 (1.064%) | 4.947% | 13 | 13/13 (100.000%) | 79.418% |
| food | 19 | 1/19 (5.263%) | 22.637% | 74 | 74/74 (100.000%) | 96.033% |
| landscape | 70 | 1/70 (1.429%) | 6.598% | 55 | 54/55 (98.182%) | 91.663% |
| object | 70 | 0/70 (0.000%) | 4.189% | 42 | 42/42 (100.000%) | 93.116% |
| plant | 75 | 0/75 (0.000%) | 3.916% | 9 | 9/9 (100.000%) | 71.687% |

### size_matched_512 (diagnostic: centre square crop, 512 x 512, JPEG q90)

Role: reported, not gated.

On 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 293 unit-level Stable Diffusion 1.x images from one August 2022 sample, observed false-alarm rate 8/425 (1.882%), one-sided 95% upper bound 3.371%; observed catch rate 293/293 (100.000%), lower bound 98.983%; arm: size_matched_512.

| Arm | n real | Real flagged (observed) | Upper bound on false positives | n AI | AI flagged (observed) | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| size_matched_512 | 425 | 8/425 (1.882%) | 3.371% | 293 | 293/293 (100.000%) | 98.983% |

Detector in `size_matched_512`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 417/425 | 98.118% | 96.325% to 99.184% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, detector, `size_matched_512`: 99.059%, interval 97.277% to 99.642% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

Shortcut baseline in `size_matched_512` (depth-3 tree on file facts, fitted on 245 dev units, scored on 425 real and 293 AI test units). Features the tree used: source_jpeg_quality.

Shortcut baseline in `size_matched_512`, by class (each recall with its exact two-sided 95% Clopper-Pearson interval):

| Class | Correct / n | Recall | 95% two-sided interval |
|---|---|---|---|
| real (true-negative rate) | 425/425 | 100.000% | 99.136% to 100.000% |
| AI (true-positive rate) | 293/293 | 100.000% | 98.749% to 100.000% |

Balanced accuracy, shortcut baseline, `size_matched_512`: 100.000%, interval 98.745% to 100.000% (conservative, at least 95%, conditional on per-class binomial unit sampling). The interval is the mean of the ends of two 97.5% two-sided Clopper-Pearson intervals, one per class (0.05 split over two classes). It makes no simultaneous claim across arms or topics.

**Shortcut label, size_matched_512: shortcut-exposed: the file-facts baseline reaches a balanced accuracy of 100.0% (interval 98.7% to 100.0%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.75 or more; the detector's is 99.1% (interval 97.3% to 99.6%; conservative, at least 95%, conditional on per-class binomial unit sampling), 0.9 points below it (point estimates), so the detector does not exceed this specified shortcut baseline by at least 10 points.**

Per topic, `size_matched_512` (one-sided 95% bounds on small counts; not gate results):

| Topic | n real | Real flagged | Upper bound on false positives | n AI | AI flagged | Lower bound on AI flagged |
|---|---|---|---|---|---|---|
| animal | 97 | 0/97 (0.000%) | 3.041% | 100 | 100/100 (100.000%) | 97.049% |
| building | 94 | 1/94 (1.064%) | 4.947% | 13 | 13/13 (100.000%) | 79.418% |
| food | 19 | 2/19 (10.526%) | 29.580% | 74 | 74/74 (100.000%) | 96.033% |
| landscape | 70 | 1/70 (1.429%) | 6.598% | 55 | 55/55 (100.000%) | 94.699% |
| object | 70 | 2/70 (2.857%) | 8.722% | 42 | 42/42 (100.000%) | 93.116% |
| plant | 75 | 2/75 (2.667%) | 8.157% | 9 | 9/9 (100.000%) | 71.687% |

## Attrition

| Split | Label | Planned (primary units in the locked manifests) | Rejected | Reserve units used | Accepted and scored |
|---|---|---|---|---|---|
| dev | real | 161 | 48 | 12 | 125 |
| dev | ai | 131 | 17 | 6 | 120 |
| test | real | 498 | 146 | 73 | 425 |
| test | ai | 315 | 44 | 22 | 293 |

Rejections by check: C1: 43, C3: 261.
Replacements came only from the reserve order frozen in the manifests (`spare_for`, `spare_rank`), checked against `groups-real.csv` and `groups-ai.csv` before anything was derived: 113 reserve unit(s) used, each the lowest unused `spare_rank` for its split and topic; 48 reserve unit(s) skipped because they were rejected themselves; 142 rejected primary unit(s) had no reserve left in their split and topic, so that count fell. Nothing was redrawn. 1 reserve unit(s) beyond the replacements owed were screened and rejected, and were not used.

## Ladder answers on the test split (`original` arm)

The full ladder ran on each original. These counts are reported; they are not a gate input.

| Answer | Images |
|---|---|
| cant_tell | 394 |
| concerns | 324 |

## Reading the result

Only what the tables above support. The limits that apply:

- The AI side is one Stable Diffusion 1.x family from one August 2022 sample, mostly 512 px square; the real side is curated, pre-2022 Wikimedia Commons photographs. The test set's size sets how tight any bound can be.
- The splits are by source unit, not by generator: a result here says nothing about other generators, newer versions of Stable Diffusion, or any Meta model.
- Commons is widely crawled, so these photographs may be in the detector's or a generator's training data; the threshold file's training-data statement is quoted above.
- In an arm labelled shortcut-exposed, file facts alone separate the classes; read the detector result there with that label. "Exceeds this specified shortcut baseline" compares the detector with one specified file-facts model; it is not a finding about what the detector reads.
- The balanced-accuracy intervals are conservative, at least 95%, conditional on per-class binomial unit sampling (each test image one independent trial). They make no simultaneous claim across arms or topics.
- People screening of the test images was OpenCV triage plus a Muse Glimmer check of flagged images only; not certified person-free.

## Provenance of this page

Written by `python -m secondlook.gate_run` from `predictions.csv` (sha256 6715638ebdec5b2e997bd3de75a30f05da796fba65cfe399b8b97ad93351f7da), the L2 harness's `RESULT.md` and `result.json`, `baseline.json`, `ladder-answers.csv`, `hashes-derived.csv` and `gate-run.json` in the run folder.
