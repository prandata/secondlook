# Results

Second Look's classifier rung may raise a concern only above a threshold frozen in advance. The gate measures that threshold on a pre-registered test set before the product relies on it. Every gate result is published as it stands: PASS, FAIL or INVALID.

All dates and times below are AEDT (UTC+11) unless marked UTC. The reports and run records carry UTC; 04:00 UTC on 29 Oct is 15:00 AEDT on 29 Oct.

Training-overlap label (TRAINING-DATA.md, adopted in DAT-848), carried next to each gated verdict below: **"training-overlap: same generator family"**. The detector was trained on images from Stable Diffusion 1.x checkpoints (v1-1 to v1-5); our AI test images are Stable Diffusion 1.x. This result says how it does on a generator family it was trained on, not on unseen generators.

## v2: FAIL (29 Oct 2026, 15:00 to 15:07 AEDT)

- **Verdict:** FAIL, harness exit code 1. The gate needs both gated arms to pass; the `original` arm did not.
- **Why:** in `original`, the one-sided 95% upper bound on false positives is 9.718% (31 of 425 real photographs flagged, 7.294% observed), above the 2% limit. In `format_matched` it is 1.814% (3 of 425). The lower bound on AI images caught is 98.983% in both gated arms (293 of 293), above the 40% floor. The test and dev floors were met: test 425 real and 293 AI, dev 125 real and 120 AI.
- On 983 TrueFake images (Facebook-shared copies, CC BY 4.0), the shipped ladder flagged 0 of 500 AI images, because the detector raises no concern on its own.
- **Shortcut-exposed:** every arm is labelled shortcut-exposed. A baseline that reads only file facts reaches a balanced accuracy of 100.0%; the detector's is 96.4% in `original` and 99.6% in `format_matched`. The detector does not exceed that baseline by the 10 points the label asks for.
- **Training overlap:** training-overlap: same generator family (see the label above and [TRAINING-DATA.md](TRAINING-DATA.md)).
- **Report:** [RESULTS-v2.md](RESULTS-v2.md), written by `secondlook.gate_run` and published as it stands, with every arm, the bounds, the per-topic results, the attrition and the shortcut labels.
- **Run record:** [gate-runs/v2-gate-run.json](gate-runs/v2-gate-run.json), run id `gate-20261029-040012`, Second Look commit `c59f0644890aed1216257066c07fcc9c45e7b4f3`.
- **Frozen threshold:** 0.4319957792758942, frozen by the project lead at 14:51 AEDT on 29 Oct 2026 (03:51 UTC) on the v2 dev split and committed in git at `e4c8879`, before any test score. The threshold is the `original` arm's; the `format_matched` arm's was lower, and the dev real false-positive rate is at most 0.02 in each arm.
- **Lock:** the v2 lock in force at the run is sha256 `432cc97c6752275dd81edeac20fa63dd119cbf58701e88f48e61bbabb54dca2b` (the `v2_lock_sha256` in the run record), written under D3 before any score. It replaced the v2 lock `5dfc2d53` that this index named while the run was pending.
- **Pre-registration:** v2 was pre-registered on 29 Oct 2026, before any classifier score existed on any split. One change from v1: a fixed top-up of new real units from the same source, under the same eligibility and independence rules. Every floor is unchanged.
- **Screening rule (D3):** v2 uses the frozen, pre-registered screening answer rule on every split (L4 ERRATA-v2.md, D3, 29 Oct 2026 08:04 AEDT, before any classifier score). See the D1′ note under v1.

## v2 held-out-generator arm: reported, not gated (29 Oct 2026, 15:13 AEDT)

- **Report:** [RESULTS-heldout.md](RESULTS-heldout.md). It never changes the gate result, the threshold, the floors or the accepted population of the gated run.
- **What it is:** 300 images from 18 generators held out of the detector's training by its authors (their public evaluation set, Community Forensics PublicEval, CC BY 4.0), with about 17 images per generator. The false-alarm figures are the main run's (31/425, 3/425 and 3/425), quoted, not recomputed.
- **Catch rate, as observed:** 263/300 in `original` (one-sided 95% lower bound 84.096%), 206/300 in `format_matched` (63.970%) and 166/300 in `compressed_q70_1080` (50.422%). Per-generator rows are descriptive. The file-facts baseline, which reads no pixels, calls 265 of the same 300 files AI.
- **Run record:** [gate-runs/v2-heldout-run.json](gate-runs/v2-heldout-run.json), run id `heldout-20261029-041328`, Second Look commit `6992f97f159817bef717ae8537e07eff61ae8ec9`.

## v1: INVALID (29 Oct 2026, 05:01 AEDT; 28 Oct 18:01 UTC)

- **Report:** [RESULTS-v1.md](RESULTS-v1.md), written by `scripts/gate_run.sh` (exit 3). No image was derived or scored.
- **Why:** after the people screen, the dev split held 96 real units against a floor of 100. The test split cleared its floors (416 real, 315 AI).
- **Disclosed amendment (D1′):** under the frozen answer rule, real dev was 88. After the dev shortfall was seen, and before any classifier score existed, a parser fix (deviation D1′) re-read the stored screening replies and raised it to 96. Both counts are below 100. No floor was lowered.
- **D1′ was later not accepted.** A Codex review of the D1′ evidence packet returned FAIL on 29 Oct 2026 at 08:04 AEDT, still before any classifier score (L4 ERRATA-v2.md, D2). D3 then applied the frozen rule to every split. Under it real dev is 88, below the floor of 100, so v1 stays INVALID. RESULTS-v1 used the D1′ set (96); its reasons stand as written.
- **Training overlap:** training-overlap: same generator family. v1 has no detector result, so the label carries no rate.
- **Run record:** [gate-runs/v1-gate-run.json](gate-runs/v1-gate-run.json).
