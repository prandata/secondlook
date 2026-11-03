# DAT-850 A263: batch summary.html shots

| File | What it shows |
|---|---|
| `summary-1440.png` | `report/summary.html` from `secondlook batch` on the nine demo samples (`samples/01` to `09`), full page at 1440 px wide |
| `summary-390.png` | The same file at 390 px wide |

Taken with `headless-run` (chrome-headless-shell) on a `file://` URL, 31 Oct 2026. Measured on the same render: the page background is `rgb(244, 240, 230)` (`--cream`), the heading font stack is the product's `--serif`, nothing scrolls sideways (scroll width 1440 and 390), and every sha256 takes one line at 1440 px and two at 390 px. The heading falls back to Georgia where Playfair Display is not installed: the file embeds no font, so it stays self-contained.
