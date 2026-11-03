# Diagnostic arm `heldout_cf_publiceval`: results (reported, not gated)

This arm never changes the gate result, the threshold, the floors or the accepted population of the gated run (L4 PROTOCOL.md part I). Every figure below is for one arm on its own; nothing is added up across arms or with the gated arms. The image is the unit: the set records no prompt, user or source to group by.

## The run

| Item | Value |
|---|---|
| Run id | heldout-20261029-041328 |
| Started | 2026-10-29T04:13:28Z |
| Second Look commit | 6992f97f159817bef717ae8537e07eff61ae8ec9 |
| ACCEPTED-heldout.json sha256 | 6b7984b06c1a88b99679307dcf8daab537d5dc6d670607d8f12e004b25d3efbc |
| LOCK-heldout.json sha256 | 749f6f3a647118346f52c3041333771e44e5157b8969a09847d296a7fc914bc4 |
| v2 LOCK sha256 | 432cc97c6752275dd81edeac20fa63dd119cbf58701e88f48e61bbabb54dca2b |
| threshold.json sha256 | 686e0e6379be42271501c7016e4c16b1cdfef759904818f6382c5f37479eb53c |
| threshold.json commit | e4c8879349ed628d113a83ed45330f300fdb02c8 |
| derive.py sha256 | 53d6ad997d914b93cb617c45eeb9b2b79786a9af737064482e99872f220bf6b1 |
| classifier weights sha256 | b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387 |
| predictions-heldout.csv sha256 | 11bb317cf348472e9d0022b2094f06c2f6a52444558dfa2a9266d697b21c2f48 |
| Main gate run quoted for false alarms | gate-20261029-040012 |

## Arm `original`

original (the fetched bytes, untouched)

Held-out diagnostic, reported, not gated: on 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 300 images from 18 generators held out of the detector's training by its authors (their public evaluation set, Community Forensics PublicEval, CC BY 4.0), observed false-alarm rate 31/425, one-sided 95% upper bound 9.718%; observed catch rate 263/300, lower bound 84.096%; arm: original. About 17 images per generator; per-generator figures are descriptive.

The false-alarm figure is the main run's, quoted from its `result.json`, not recomputed here. The catch rate counts a verdict of `ai`; an abstention is never a catch. Bound: exact one-sided 95% Clopper-Pearson lower bound on the catch rate.

| Generator | Caught / n | Catch rate | One-sided 95% lower bound |
|---|---|---|---|
| Dalle2 | 16/17 | 94.118% | 74.988% |
| Dalle3 | 18/18 | 100.000% | 84.668% |
| DeciDiffusionV2 | 18/18 | 100.000% | 84.668% |
| FLUX-schnell | 16/17 | 94.118% | 74.988% |
| Firefly_Image2 | 14/18 | 77.778% | 56.112% |
| Firefly_Image3 | 11/17 | 64.706% | 41.971% |
| GALIP | 18/18 | 100.000% | 84.668% |
| Hourglass | 15/18 | 83.333% | 62.332% |
| IdeogramV1 | 14/16 | 87.500% | 65.617% |
| IdeogramV2 | 11/13 | 84.615% | 58.990% |
| Imagen3 | 10/18 | 55.556% | 34.060% |
| LCM_lora_sdv15 | 18/18 | 100.000% | 84.668% |
| LCM_lora_sdxl | 12/13 | 92.308% | 68.366% |
| LCM_lora_ssd1b | 16/16 | 100.000% | 82.925% |
| MidjourneyV5_2 | 16/17 | 94.118% | 74.988% |
| MidjourneyV6_1 | 6/13 | 46.154% | 22.396% |
| kandinsky_2_2 | 16/17 | 94.118% | 74.988% |
| kvikontent_midjourney_v6 | 18/18 | 100.000% | 84.668% |

Per-generator rows are descriptive: about 17 images each.

These generators and images were chosen and made by the detector's authors for their evaluation set, not collected from the web by us. Sizes run from 224 to 2,048 px and formats differ by generator (Firefly JPEG, the rest PNG), unlike the real side; the format_matched and compressed arms reduce the format difference only.

File-facts shortcut baseline (part G, tree fitted on DiffusionDB dev; it reads no pixels), AI calls on these files in `original`: 265/300 (88.333%), one-sided 95% lower bound 84.831%. Model: sklearn DecisionTreeClassifier(max_depth=3, random_state=0), fitted on 245 dev units, features used by the tree: source_jpeg_quality.

## Arm `format_matched`

format_matched (JPEG q90, at most 1,536 px, shrink only)

Held-out diagnostic, reported, not gated: on 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 300 images from 18 generators held out of the detector's training by its authors (their public evaluation set, Community Forensics PublicEval, CC BY 4.0), observed false-alarm rate 3/425, one-sided 95% upper bound 1.814%; observed catch rate 206/300, lower bound 63.970%; arm: format_matched. About 17 images per generator; per-generator figures are descriptive.

The false-alarm figure is the main run's, quoted from its `result.json`, not recomputed here. The catch rate counts a verdict of `ai`; an abstention is never a catch. Bound: exact one-sided 95% Clopper-Pearson lower bound on the catch rate.

| Generator | Caught / n | Catch rate | One-sided 95% lower bound |
|---|---|---|---|
| Dalle2 | 14/17 | 82.353% | 60.436% |
| Dalle3 | 18/18 | 100.000% | 84.668% |
| DeciDiffusionV2 | 18/18 | 100.000% | 84.668% |
| FLUX-schnell | 12/17 | 70.588% | 47.808% |
| Firefly_Image2 | 15/18 | 83.333% | 62.332% |
| Firefly_Image3 | 11/17 | 64.706% | 41.971% |
| GALIP | 17/18 | 94.444% | 76.234% |
| Hourglass | 5/18 | 27.778% | 11.643% |
| IdeogramV1 | 6/16 | 37.500% | 17.777% |
| IdeogramV2 | 2/13 | 15.385% | 2.805% |
| Imagen3 | 1/18 | 5.556% | 0.285% |
| LCM_lora_sdv15 | 18/18 | 100.000% | 84.668% |
| LCM_lora_sdxl | 10/13 | 76.923% | 50.535% |
| LCM_lora_ssd1b | 16/16 | 100.000% | 82.925% |
| MidjourneyV5_2 | 9/17 | 52.941% | 31.083% |
| MidjourneyV6_1 | 3/13 | 23.077% | 6.605% |
| kandinsky_2_2 | 13/17 | 76.471% | 53.945% |
| kvikontent_midjourney_v6 | 18/18 | 100.000% | 84.668% |

Per-generator rows are descriptive: about 17 images each.

These generators and images were chosen and made by the detector's authors for their evaluation set, not collected from the web by us. Sizes run from 224 to 2,048 px and formats differ by generator (Firefly JPEG, the rest PNG), unlike the real side; the format_matched and compressed arms reduce the format difference only.

File-facts shortcut baseline (part G, tree fitted on DiffusionDB dev; it reads no pixels), AI calls on these files in `format_matched`: 265/300 (88.333%), one-sided 95% lower bound 84.831%. Model: sklearn DecisionTreeClassifier(max_depth=3, random_state=0), fitted on 245 dev units, features used by the tree: source_jpeg_quality.

## Arm `compressed_q70_1080`

compressed_q70_1080 (JPEG q70, at most 1,080 px; AI images are mostly 512 px and stay that size)

Held-out diagnostic, reported, not gated: on 425 unit-level real photographs (curated Wikimedia Commons, pre-2022) and 300 images from 18 generators held out of the detector's training by its authors (their public evaluation set, Community Forensics PublicEval, CC BY 4.0), observed false-alarm rate 3/425, one-sided 95% upper bound 1.814%; observed catch rate 166/300, lower bound 50.422%; arm: compressed_q70_1080. About 17 images per generator; per-generator figures are descriptive.

The false-alarm figure is the main run's, quoted from its `result.json`, not recomputed here. The catch rate counts a verdict of `ai`; an abstention is never a catch. Bound: exact one-sided 95% Clopper-Pearson lower bound on the catch rate.

| Generator | Caught / n | Catch rate | One-sided 95% lower bound |
|---|---|---|---|
| Dalle2 | 5/17 | 29.412% | 12.377% |
| Dalle3 | 14/18 | 77.778% | 56.112% |
| DeciDiffusionV2 | 18/18 | 100.000% | 84.668% |
| FLUX-schnell | 8/17 | 47.059% | 26.011% |
| Firefly_Image2 | 14/18 | 77.778% | 56.112% |
| Firefly_Image3 | 9/17 | 52.941% | 31.083% |
| GALIP | 10/18 | 55.556% | 34.060% |
| Hourglass | 3/18 | 16.667% | 4.702% |
| IdeogramV1 | 3/16 | 18.750% | 5.315% |
| IdeogramV2 | 0/13 | 0.000% | 0.000% |
| Imagen3 | 0/18 | 0.000% | 0.000% |
| LCM_lora_sdv15 | 18/18 | 100.000% | 84.668% |
| LCM_lora_sdxl | 11/13 | 84.615% | 58.990% |
| LCM_lora_ssd1b | 15/16 | 93.750% | 73.604% |
| MidjourneyV5_2 | 7/17 | 41.176% | 21.191% |
| MidjourneyV6_1 | 1/13 | 7.692% | 0.394% |
| kandinsky_2_2 | 12/17 | 70.588% | 47.808% |
| kvikontent_midjourney_v6 | 18/18 | 100.000% | 84.668% |

Per-generator rows are descriptive: about 17 images each.

These generators and images were chosen and made by the detector's authors for their evaluation set, not collected from the web by us. Sizes run from 224 to 2,048 px and formats differ by generator (Firefly JPEG, the rest PNG), unlike the real side; the format_matched and compressed arms reduce the format difference only.

File-facts shortcut baseline (part G, tree fitted on DiffusionDB dev; it reads no pixels), AI calls on these files in `compressed_q70_1080`: 265/300 (88.333%), one-sided 95% lower bound 84.831%. Model: sklearn DecisionTreeClassifier(max_depth=3, random_state=0), fitted on 245 dev units, features used by the tree: source_jpeg_quality.

## Attrition per generator

Screening is the one in PROTOCOL.md I4 (C1, C3, C4), run before any score. Candidates are the first 34 rows per generator in I2 order.

| Generator | Candidates | Cleared screening | Rejected | Rejected by | Accepted into the arm |
|---|---|---|---|---|---|
| Dalle2 | 34 | 21 | 13 | C3 13 | 17 |
| Dalle3 | 34 | 20 | 14 | C3 14 | 18 |
| DeciDiffusionV2 | 34 | 28 | 6 | C3 6 | 18 |
| FLUX-schnell | 34 | 22 | 12 | C3 12 | 17 |
| Firefly_Image2 | 34 | 23 | 11 | C3 11 | 18 |
| Firefly_Image3 | 34 | 21 | 13 | C3 13 | 17 |
| GALIP | 34 | 24 | 10 | C3 10 | 18 |
| Hourglass | 34 | 28 | 6 | C3 6 | 18 |
| IdeogramV1 | 34 | 16 | 18 | C3 18 | 16 |
| IdeogramV2 | 34 | 13 | 21 | C3 21 | 13 |
| Imagen3 | 34 | 21 | 13 | C3 13 | 18 |
| LCM_lora_sdv15 | 34 | 18 | 16 | C3 16 | 18 |
| LCM_lora_sdxl | 34 | 13 | 21 | C3 21 | 13 |
| LCM_lora_ssd1b | 34 | 16 | 18 | C3 18 | 16 |
| MidjourneyV5_2 | 34 | 17 | 17 | C3 17 | 17 |
| MidjourneyV6_1 | 34 | 13 | 21 | C3 21 | 13 |
| kandinsky_2_2 | 34 | 22 | 12 | C3 12 | 17 |
| kvikontent_midjourney_v6 | 34 | 19 | 15 | C3 15 | 18 |

Accepted: 300 of 612 candidates; rejected in screening: 257.

## Provenance hits per generator (accepted images)

PROTOCOL.md I5: recorded, not a filter. A file with a hit stays in the arm; the classifier reads pixels only and the derived arms strip metadata again. The `watermark` rung reads only Second Look's own Video Seal mark, not any vendor's watermark.

| Generator | Images | Any rung hit | C2PA | EXIF rung | IPTC rung | Video Seal mark | Any file metadata |
|---|---|---|---|---|---|---|---|
| Dalle2 | 17 | 0 | 0 | 0 | 0 | 0 | 17 |
| Dalle3 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| DeciDiffusionV2 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| FLUX-schnell | 17 | 0 | 0 | 0 | 0 | 0 | 0 |
| Firefly_Image2 | 18 | 0 | 0 | 0 | 0 | 0 | 18 |
| Firefly_Image3 | 17 | 0 | 0 | 0 | 0 | 0 | 17 |
| GALIP | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| Hourglass | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| IdeogramV1 | 16 | 16 | 0 | 16 | 0 | 0 | 16 |
| IdeogramV2 | 13 | 13 | 0 | 13 | 0 | 0 | 13 |
| Imagen3 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| LCM_lora_sdv15 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| LCM_lora_sdxl | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| LCM_lora_ssd1b | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| MidjourneyV5_2 | 17 | 0 | 0 | 0 | 0 | 0 | 0 |
| MidjourneyV6_1 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| kandinsky_2_2 | 17 | 0 | 0 | 0 | 0 | 0 | 0 |
| kvikontent_midjourney_v6 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |

## What this does not say

It does not say how the detector does on any generator outside these 18, on any named generator, or on any service the product might meet. It has no same-prompt comparison and no per-topic table: the files record no prompt and no topic. See `docs/TRAINING-DATA.md` for the detector's training data.
