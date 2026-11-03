# DAT-850: the summary names a high detector score (design SHOULD 4) and a low score's short Proves line (FIX 4)

Follow-up to PR #33. Captured at `3e30955` with `capture.mjs` through `headless-run` (chrome-headless-shell, muted),
at 1280 px and 390 px, against `python -m secondlook.web --port 8913` on this branch: the real ladder, with the
committed `classifier_threshold.json` (0.432) and `classifier_gate.json` (the v2 gate, FAIL). No request left
127.0.0.1 (`dat850s4-capture.json`, `offHost: []`).

| Input | Answer | Classifier card | Plain-language summary |
|---|---|---|---|
| `sd-dev-A036055fd.png` (L4 dev split, DiffusionDB, CC0 1.0; score 1.000) | Can't tell | What we found, "Score only" | "Second Look can't tell how this was made: a detector scored this over 0.99 out of 1, at or above our cut-off of 0.432, but that cut-off failed its test, so the score raises no concern." |
| `06-stripped-pixel10pro-atlantic-sign.jpg` (demo sample; score 0.001) | Can't tell | Checked, nothing found | "Second Look can't tell how this was made, and finding nothing is not evidence either way." |

The low-score card's Proves line is now "Only that this detector did not raise a concern, and its threshold has not
passed its test." (before: the threshold, the failed test, 9.7%, the 2% limit and the results path).

Files: `dat850s4-sd-above-{top,card}-{desktop,phone}.png` and the full pages `dat850s4-sd-above-{desktop,phone}.png`;
`dat850s4-camera-sample-{top,card}-{desktop,phone}.png`. The capture record also holds the AI demo sample
(`07-stripped-firefly-mountain-landscape.jpg`, score 0.019), which reads the same as the camera sample.

**Superseded text (fix round on PR #39, after `6b31d49`).** These shots and the capture record show the summary as
it was at `3e30955`. The summary for the high score is now three sentences (a reviewer's SHOULD 1 and 2): "Second
Look can't tell how this was made. A detector scored it over 0.99 out of 1, at or above our cut-off of 0.432. That
cut-off failed its test, so we do not count the score as evidence either way." The shots were not retaken in that
round.
