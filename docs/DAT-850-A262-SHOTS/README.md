# A262 lane A shots (DAT-850)

Taken 31 Oct 2026 with `headless-run` (chrome-headless-shell, muted) against `python -m secondlook.web --samples samples` on 127.0.0.1 at commit 7f2e6f7, at 1440 and 390 wide. No request left 127.0.0.1. This Mac has the voice transcriber installed, so the shot task answered `/api/health` with voice off, as on both persona Macs; every other answer is the server's own.

| Fix | State | 1440 | 390 |
|---|---|---|---|
| 3, 4 | Home: no stub banner on the real engine; the two "off" lines | `a262-1-home-off-lines-1440.png` | `a262-1-home-off-lines-390.png` |
| 1 | Sample 02, C2PA card with "Source" closed | `a262-2-c2pa-card-closed-1440.png`, `a262-2-sample02-answer-1440.png` | `a262-2-c2pa-card-closed-390.png`, `a262-2-sample02-answer-390.png` |
| 1 | Sample 02, "Source" opened: the rung's full line, the list URL and its sha256 | `a262-3-c2pa-card-source-open-1440.png` | `a262-3-c2pa-card-source-open-390.png` |
| 7 | Receipt row after pressing JSON (pointer left on it) | `a262-4-receipt-after-press-1440.png` | `a262-4-receipt-after-press-390.png` |
| 5 | Sample 05, the AI-made card's limit | `a262-5-ai-made-card-1440.png` | `a262-5-ai-made-card-390.png` |
| 2 | Sample 06, deep check with no model and no recording | `a262-6-deep-check-off-1440.png`, `a262-6-sample06-answer-1440.png` | `a262-6-deep-check-off-390.png`, `a262-6-sample06-answer-390.png` |
| 6 | Batch page, the nine samples | `a262-7-batch-nine-1440.png` | `a262-7-batch-nine-390.png` |
