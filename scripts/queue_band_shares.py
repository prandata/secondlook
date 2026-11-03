"""Band shares of the frozen desk triage queue on the TrueFake held-out split (J4, DAT-1367).

    python scripts/queue_band_shares.py --scores scale-eval/scores.csv --split arena/split.csv

Reads the frozen bands file (sha256 checked by secondlook.queue.load_bands), then the held-out rows only,
and bands each with secondlook.queue.band_for from the ladder answer and the detector score already in
scores.csv. Prints a Markdown table per form: share of images in each band, and the per-class errors with
exact one-sided 95% bounds (gate/stats.py). TrueFake only (CC BY 4.0); no other dataset is read.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate import stats  # noqa: E402
from secondlook import queue  # noqa: E402


def pct(k: int, n: int) -> str:
    return f"{k}/{n} = {100 * k / n:.1f}%" if n else "0/0"


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--scores", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--bands", type=Path, default=queue.BANDS_PATH)
    a = p.parse_args(argv)
    with a.split.open(newline="") as f:
        held = {r["sha256"]: r for r in csv.DictReader(f) if r["split"] == "heldout"}
    rows = []
    with a.scores.open(newline="") as f:
        for r in csv.DictReader(f):
            if r["sha256"] in held:
                if r["licence"] != "CC BY 4.0":
                    raise SystemExit("a non-TrueFake row is in the held-out split")
                rows.append(r)
    if len(rows) != len(held):
        raise SystemExit(f"{len(held) - len(rows)} held-out rows have no scores")
    for form in ("sq512", "original"):
        b = queue.load_bands(a.bands, form)
        print(f"\n### {form} (bands file sha256 {b['sha256']}; t_hi {b['t_hi']}, t_lo {b['t_lo']})\n")
        tally = {(lab, band): 0 for lab in ("real", "ai") for band in queue.BAND_ORDER}
        by_gen: dict[str, dict[str, int]] = {}
        for r in rows:
            s = r[f"{form}_classifier_score"]
            band, _ = queue.band_for(r[f"{form}_answer"], float(s) if s != "" else None, b)
            tally[(r["label"], band)] += 1
            if r["label"] == "ai":
                g = by_gen.setdefault(r["generator"], {x: 0 for x in queue.BAND_ORDER})
                g[band] += 1
        n_real = sum(tally[("real", x)] for x in queue.BAND_ORDER)
        n_ai = sum(tally[("ai", x)] for x in queue.BAND_ORDER)
        n = n_real + n_ai
        print("| Band | All held-out images | Real photos | AI images |")
        print("|---|---|---|---|")
        for band in queue.BAND_ORDER:
            k = tally[("real", band)] + tally[("ai", band)]
            print(f"| {band} | {pct(k, n)} | {pct(tally[('real', band)], n_real)} | {pct(tally[('ai', band)], n_ai)} |")
        fr, fa = tally[("real", queue.AUTO)], tally[("ai", queue.FINE)]
        auto = tally[("real", queue.AUTO)] + tally[("ai", queue.AUTO)] + tally[("real", queue.FINE)] + tally[("ai", queue.FINE)]
        print(f"\nDecided without a person: {pct(auto, n)}. Sent to a person: {pct(n - auto, n)}.")
        print(f"Real photos auto-decided as likely AI: {pct(fr, n_real)} (95% upper bound {100 * stats.upper_bound(fr, n_real):.2f}%).")
        print(f"AI images put in likely_fine: {pct(fa, n_ai)} (95% upper bound {100 * stats.upper_bound(fa, n_ai):.2f}%).")
        ok_r, ok_a = tally[("ai", queue.AUTO)], tally[("real", queue.FINE)]
        print(f"AI images auto-decided: {pct(ok_r, n_ai)} (95% lower bound {100 * stats.lower_bound(ok_r, n_ai):.1f}%). "
              f"Real photos in likely_fine: {pct(ok_a, n_real)} (95% lower bound {100 * stats.lower_bound(ok_a, n_real):.1f}%).")
        print("\n| Generator (held-out AI) | " + " | ".join(queue.BAND_ORDER) + " |")
        print("|---|---|---|---|")
        for gen in sorted(by_gen):
            g = by_gen[gen]
            t = sum(g.values())
            print(f"| {gen} | " + " | ".join(pct(g[x], t) for x in queue.BAND_ORDER) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
