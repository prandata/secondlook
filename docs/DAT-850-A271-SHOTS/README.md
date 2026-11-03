# DAT-850 A271: batch summary.html shots after the design fix round

| File | What it shows |
|---|---|
| `summary-1440.png` | `summary.html` from `secondlook batch samples --out ... --jobs 2` at `db71d06` (the page's code version line shows it), all ten files under `samples/`, full page at 1440 px |
| `summary-390.png` | The same file at 390 px |
| `summary-800.png` | The same file at 800 px, the narrowest width that keeps four columns |

The run had the C2PA interim trust list folder (`secondlook/trust/interim`, gitignored, fetched by `scripts/fetch_trust.sh`) moved aside, as on a fresh checkout. Rows 03, 04 and 05 then carry the long C2PA reason ("the signer is not on the C2PA Trust List (2026-10-17); the C2PA interim trust list was not loaded", 299 to 321 characters), the case a reviewer saw collapse the File column. The folder was put back after the run.

Taken with `headless-run` (chrome-headless-shell) on a `file://` URL, 31 Oct 2026. Measured on the same render:

| Check | 1440 px | 800 px | 700 px | 390 px |
|---|---|---|---|---|
| File column width (22 zeros at 16 px semibold) | 292 px (278) | 250 px (237) | stacked, 634 px | stacked, 354 px |
| Answer column width | 466 px | 268 px | stacked | stacked |
| Lines per file name | 1 or 2, breaking at hyphens | 2 | 1 | 1 or 2 |
| Extension split across lines | never | never | never | never |
| Heading font | `SL Title 700 loaded` (the embedded Playfair Display Bold subset) | same | same | same |
| Gap from each answer label's text to its count | 94 to 232 px (was about 700) | 94 to 232 px | 94 to 232 px | 88 to 226 px |
| Copy group cell | `g1` under the Copies column head | same | `Copies: g1` | `Copies: g1` |
| Body, notes / hashes / SHA256 label, column heads, badge | 16 / 13 / 12 px | same | same | same |
| Sideways scroll | none | none | none | none |
