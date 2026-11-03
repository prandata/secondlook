# Results

Gate verdict: **INVALID** (exit code 3). It is published as it stands. The floors were checked first, on the accepted units after attrition and the frozen reserves, so no image was derived, no image was scored and no predictions file was written. This run has no detector result, no balanced accuracy and no shortcut baseline.

Reasons:

- dev split: 96 real and 131 AI accepted units, below the floor of 100 real and 100 AI the threshold must be frozen on

## Counts against the floors

| Set | Real | AI | Floor (real, AI) | Met |
|---|---|---|---|---|
| Dev split, accepted units | 96 | 131 | 100, 100 | no |
| Test split, accepted units (each gated arm: original, format_matched) | 416 | 315 | 400, 200 | yes |

The floors were set in advance and are not lowered. Both gated arms are made from the same accepted originals, so each has the test counts above.

## Attrition

| Split | Label | Planned (primary units in the locked manifests) | Rejected | Reserve units used | Accepted and scored |
|---|---|---|---|---|---|
| dev | real | 111 | 23 | 8 | 96 |
| dev | ai | 131 | 2 | 2 | 131 |
| test | real | 437 | 73 | 52 | 416 |
| test | ai | 315 | 7 | 7 | 315 |

Rejections by check: C1: 35, C3: 105.
Replacements came only from the reserve order frozen in the manifests (`spare_for`, `spare_rank`), checked against `groups-real.csv` and `groups-ai.csv` before anything was derived: 69 reserve unit(s) used, each the lowest unused `spare_rank` for its split and topic; 33 reserve unit(s) skipped because they were rejected themselves; 36 rejected primary unit(s) had no reserve left in their split and topic, so that count fell. Nothing was redrawn. 2 reserve unit(s) beyond the replacements owed were screened and rejected, and were not used.

## The run

| Field | Value |
|---|---|
| Run id | gate-20261028-180131 |
| Date (UTC) | 2026-10-28T18:01:32Z |
| Gate verdict, exit code | INVALID, 3 |
| Secondlook commit | 26eaae33d1f333c175ff45e36a56abed760b62ed |
| L4 LOCK.json sha256 | 64af27d17f6780bc56a914292a1b3f3420ac986f9160fee16104f0405c200fd1 |
| SCREEN-CONFIG.json sha256 | 4c4807c05cb396607b542b5983b0ddb50b3b0ec6fadad381745e9bb5423b6965 |
| ACCEPTED.json sha256 | e3da8658293fc5fc8dbfe5badf8ebc345b74549b5b759e2732cc4ea3c63a480e |
| Dev split sha256 | b212c7a17bd6e06ea3eef378fc4f70960a229ed8ea728b82f8d6d1ba82e5e0c1 |
| Test split sha256 | aa9ccd579f25d601102bee7c92631f20caa80d6d1aa7cc112ec545f2507738f5 |
