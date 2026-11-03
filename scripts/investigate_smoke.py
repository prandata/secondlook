"""Live smoke run of the Glimmer investigator (DAT-873): the 9 demo samples and 3 screenshots.

The screenshots are rendered here with Pillow. Each shows, as a mock post, the claim text of a real
ClaimReview entry in the S6 index (SHOTS below: the claim as the publisher wrote it, its review URL),
and is investigated with that text as its caption too, as a user would paste a post's words. The 9
samples have no caption. Needs a running server (scripts/glimmer_server.sh start). The server's
sockets are sampled with lsof for the whole run. For each image the ladder is also run alone, to
show the investigator's answer is the ladder's. No model writes the explanation (N3 retired,
DAT-848): it is the investigator's template text.

    .venv/bin/python scripts/investigate_smoke.py --samples <DEMO-SAMPLES/files> --db <claims.sqlite> \
        --shots docs/M2-SHOTS --out docs/M2-INVESTIGATOR-SMOKE.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from secondlook import glimmer as g  # noqa: E402
from secondlook import investigator as inv  # noqa: E402
from secondlook import ladder  # noqa: E402

# Claim texts from the S6 index (Data Commons ClaimReview feed, 28 Oct 2026), as published.
SHOTS = [
    ("shot-1-pentagon.png", "Photo shows the U.S. Pentagon is under attack.",
     "https://www.politifact.com/factchecks/2023/may/25/facebook-posts/fake-photo-used-in-claim-that-the-pentagon-was-att/"),
    ("shot-2-hollywood-sign.png", "Image shows the Hollywood sign was on fire as of Jan. 8.",
     "https://www.politifact.com/factchecks/2025/jan/10/social-media/the-hollywood-sign-wasnt-on-fire-as-of-jan-10-this/"),
    ("shot-3-gatlinburg.png", "Photo shows Gatlinburg, Tennessee, flooded on Sept. 30, 2024.",
     "https://www.politifact.com/factchecks/2024/oct/03/viral-image/fabricated-photo-appears-to-show-gatlinburg-tennes/"),
]


def render_shot(path: Path, text: str) -> None:
    """A plain mock post: a header line and the claim text, black on white, 1080 px wide."""
    from PIL import Image, ImageDraw, ImageFont
    body, small = ImageFont.load_default(size=52), ImageFont.load_default(size=34)
    lines = textwrap.wrap(text, 34)
    img = Image.new("RGB", (1080, 260 + 70 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((60, 50, 140, 130), fill=(190, 200, 215))
    d.text((165, 62), "Example Account  ·  2h", font=small, fill=(90, 90, 90))
    for i, line in enumerate(lines):
        d.text((60, 170 + 70 * i), line, font=body, fill=(15, 15, 15))
    img.save(path, optimize=True)


def table(rows: list[dict], review_urls: dict) -> list[dict]:
    """One line per image: latency, steps, and whether the expected fact-check was found."""
    out = []
    for r in rows:
        model_steps = [s for s in r["steps"] if s["by"] == "model"]
        want = review_urls.get(r["file"])
        out.append({
            "file": r["file"], "answer": r["answer"], "ladder_alone": r["ladder_alone"],
            "seconds": r["seconds"], "model_seconds": r["model_seconds"],
            "steps": len(model_steps), "refused": sum(1 for s in model_steps if not s["ok"]),
            "tools": [s["tool"] for s in r["steps"]],
            "fact_checks": len(r["claims_found"]),
            "expected_fact_check": None if want is None else any(c["review_url"] == want for c in r["claims_found"]),
            "facts": r["facts"], "image_lines": len(r["image_text"]),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    shots = Path(a.shots)
    shots.mkdir(parents=True, exist_ok=True)
    for name, text, _ in SHOTS:
        render_shot(shots / name, text)
    files = [(f, None) for f in sorted(Path(a.samples).iterdir()) if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
    files += [(shots / name, text) for name, text, _ in SHOTS]

    client = g.GlimmerClient(reasoning="low")
    info = client.check()
    stop = Path(tempfile.mkdtemp(prefix="investigate-smoke-")) / "stop"
    sampler = subprocess.Popen(["bash", str(ROOT / "scripts" / "glimmer_server.sh"), "egress", "3600"],
                               env=os.environ | {"GLIMMER_EGRESS_STOP": str(stop)},
                               stdout=subprocess.PIPE, text=True)
    started = time.monotonic()
    rows = []
    try:
        for f, caption in files:
            note = inv.investigate(str(f), caption, client, claims_db=a.db)
            alone = ladder.run(str(f)).answer
            row = {"caption": caption, **dataclasses.asdict(note)}
            row["ladder_alone"] = alone
            row["claims_found"] = [{"review_url": e.raw.get("review_url"), "search_source": e.raw.get("search_source")}
                                   for e in note.evidence if e.rung == "claim" and e.raw.get("review_url")]
            rows.append(row)
            print(f.name, note.answer, alone, note.seconds, [(s.tool, s.ok) for s in note.steps],
                  len(row["claims_found"]), flush=True)
    finally:
        stop.touch()
        egress_out, _ = sampler.communicate(timeout=60)
    egress = json.loads(egress_out.strip().splitlines()[-1]) if egress_out.strip() else {"error": "no output"}
    egress["exit_code"] = sampler.returncode
    report = {
        "run_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": platform.platform(), "server": info, "url": client.url,
        "reasoning_strength": client.reasoning, "explanation": "templates (no model)",
        "e1_installed": inv._e1() is not None, "context_rung": inv._context_rung() is not None,
        "max_steps": inv.MAX_STEPS, "total_seconds": round(time.monotonic() - started, 1),
        "egress": egress, "shots": [{"file": n, "claim": t, "review_url": u} for n, t, u in SHOTS],
        "table": table(rows, {n: u for n, _, u in SHOTS}),
        "images": rows,
    }
    Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(egress))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
