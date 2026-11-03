"""Static replay page for the Desk triage queue (J13, DAT-1375): what a judge opens.

    .venv/bin/python scripts/build_replay_site.py \
        --split  <runs>/arena/split.csv \
        --scores <runs>/scale-eval/scores.csv \
        --crops  <runs>/scale-eval/data/normalised/sq512 \
        --work   /tmp/j13-work --out /tmp/j13-site

1. Picks the TrueFake held-out split only (640 images, CC BY 4.0) from the J3 split. Every row must be a
   TrueFake row; any other dataset (OpenFake, Community Forensics, RAISE) stops the run.
2. Copies each image's sq512 crop (metadata stripped, 512 x 512 centre crop, JPEG quality 90, the
   pre-registration's headline form) into WORK/images, checking its sha256 against the crops' index.csv.
3. Runs `secondlook queue WORK/images --out WORK/queue --form sq512` (skipped with --reuse-queue when
   WORK/queue/queue.json is already there).
4. Writes a static site to OUT: index.html (the queue), one page per image (the receipt, its sha256,
   each rung's reading) and results.html (band shares and error counts with exact one-sided 95% bounds,
   recomputed from scores.csv the way scripts/queue_band_shares.py does for J4, for both forms).

The live sq512 run must put every image in the band that scores.csv gives it; any difference stops the
build. Plain HTML and CSS, no JavaScript, no external requests: the title font is copied in, everything
else is the system font. Links are relative, so the site works from file:// and from GitHub Pages.
The Arena rows (Muse Image, Spark, the SAM box) are slots marked "pending": no number is invented.

The site is not published by this script. It refuses to write a site whose files contain this machine's
home folder path (receipts record absolute paths of the files they read, so run the queue from a checkout
outside the home folder, or pass --allow-home-paths for a private look).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gate import stats  # noqa: E402
from secondlook import queue  # noqa: E402

FORM = "sq512"
HELD_OUT_N = 640
TRUEFAKE_LICENCE = "CC BY 4.0"
TRUEFAKE_SOURCE = "TrueFake (Facebook-shared subset)"

ATTRIBUTION = (
    'Images: <a href="https://github.com/MMLab-unitn/TrueFake-IJCNN25">TrueFake</a> (Dell’Anna et al., '
    'IJCNN 2025, <a href="https://arxiv.org/abs/2504.20658">arXiv 2504.20658</a>), Facebook-shared subset, '
    'licensed <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>. Changes: metadata removed, '
    'centre-cropped to 512 x 512 pixels and saved as JPEG quality 90; thumbnails scaled down. '
    'Only the AI images are shown: the camera photos can show identifiable people, so this site does not '
    'publish them; their rows keep the receipt and the image sha256. '
    'The TrueFake authors do not endorse Second Look.')
PHOTO_WITHHELD = ("Camera photo not shown. TrueFake's camera photos can show identifiable people, so this site "
                  "does not publish them. The receipt and the image sha256 are what the queue read.")
FOOTER = ("Second Look is a What Survives in AI project, in development. It reads provenance and flags "
          "concerns with reasons; it does not decide what is true. Not affiliated with Meta.")

BAND_SHORT = {queue.UNCERTAIN: "Needs a person", queue.AUTO: "Auto-decided", queue.FINE: "Likely fine"}
BAND_NOTE = {
    queue.UNCERTAIN: "A person looks, highest detector score first.",
    queue.AUTO: "Routed as likely AI-made without a person; spot checks only.",
    queue.FINE: "Very low detector score and nothing else found. Not proof that the image is real.",
}
RUNG_NAMES = {"c2pa": "Content Credentials (C2PA)", "iptc": "IPTC metadata", "exif": "EXIF metadata",
              "classifier": "Detector score", "watermark": "Second Look watermark", "origin": "What was checked"}
ARENA_SLOTS = (
    ("Muse Image catch rate", "Share of our own Muse Image generations and SAM-masked edits that the queue "
     "routes away from Likely fine. Runs only if the Meta API key arrives (decision 2 Nov)."),
    ("Muse Spark second opinion", "A second reading on the Needs a person band only."),
    ("SAM “look here” box", "A box on the region a person should look at first."),
)


def esc(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- 1 and 2: the held-out TrueFake rows and their crops ----------------------------------------

def held_out_rows(split_rows: list[dict], expected: int | None = HELD_OUT_N) -> dict[str, dict]:
    """sha256 -> split row for the held-out rows. Every row must be from TrueFake's Facebook subset (and
    stage_crops checks the crop's source dataset again); anything else stops the run, so no OpenFake,
    Community Forensics or RAISE image is used."""
    held = {}
    for r in split_rows:
        if r["split"] != "heldout":
            continue
        if not r["source_ref"].startswith(("Facebook/Real/", "Facebook/Fake/")):  # TrueFake's own layout
            raise SystemExit(f"a held-out row is not TrueFake: {r['sha256'][:12]} ({r['generator']})")
        held[r["sha256"]] = r
    if expected is not None and len(held) != expected:
        raise SystemExit(f"expected {expected} held-out rows, found {len(held)}")
    return held


def stage_crops(held: dict[str, dict], crops_dir: Path, dest: Path) -> dict[str, dict]:
    """Copy each held-out image's sq512 crop into dest, checking its sha256. Returns
    crop sha256 -> {"source_sha256", "label", "generator", "name"}."""
    index = {r["source_sha256"]: r for r in read_csv(crops_dir / "index.csv")}
    dest.mkdir(parents=True, exist_ok=True)
    out = {}
    for src_sha, row in sorted(held.items()):
        ix = index.get(src_sha)
        if ix is None:
            raise SystemExit(f"no sq512 crop for held-out image {src_sha[:12]}")
        if ix["source_dataset"] != TRUEFAKE_SOURCE:
            raise SystemExit(f"crop {ix['normalised_path']} is not from {TRUEFAKE_SOURCE}")
        name = Path(ix["normalised_path"]).name
        src = crops_dir / name
        if sha256_file(src) != ix["sha256_normalised"]:
            raise SystemExit(f"{name} does not match its sha256 in index.csv")
        target = dest / name
        if not target.exists() or sha256_file(target) != ix["sha256_normalised"]:
            shutil.copyfile(src, target)
        out[ix["sha256_normalised"]] = {"source_sha256": src_sha, "label": row["label"],
                                        "generator": row["generator"], "name": name}
    return out


def run_queue(images: Path, queue_dir: Path, python: str, repo: Path, jobs: int | None) -> None:
    cmd = [python, "-m", "secondlook.cli", "queue", str(images), "--out", str(queue_dir), "--form", FORM]
    if jobs:
        cmd += ["--jobs", str(jobs)]
    subprocess.run(cmd, cwd=repo, check=True)


# --- 4a: the numbers, from scores.csv (J4's method) --------------------------------------------

def tally_form(score_rows: list[dict], held: dict[str, dict], form: str, bands_path=None) -> dict:
    """Band every held-out row with secondlook.queue.band_for from scores.csv, as
    scripts/queue_band_shares.py does. Returns counts, bounds and the per-generator table."""
    b = queue.load_bands(bands_path, form)
    t = {(lab, band): 0 for lab in ("real", "ai") for band in queue.BAND_ORDER}
    gens: dict[str, dict[str, int]] = {}
    per_image = {}
    for r in score_rows:
        if r["sha256"] not in held:
            continue
        if r["licence"] != TRUEFAKE_LICENCE:
            raise SystemExit("a non-TrueFake row is in the held-out split")
        s = r[f"{form}_classifier_score"]
        band, _ = queue.band_for(r[f"{form}_answer"], float(s) if s != "" else None, b)
        per_image[r["sha256"]] = band
        t[(r["label"], band)] += 1
        if r["label"] == "ai":
            gens.setdefault(r["generator"], {x: 0 for x in queue.BAND_ORDER})[band] += 1
    if len(per_image) != len(held):
        raise SystemExit(f"{len(held) - len(per_image)} held-out rows have no scores")
    n_real = sum(t[("real", x)] for x in queue.BAND_ORDER)
    n_ai = sum(t[("ai", x)] for x in queue.BAND_ORDER)
    fr, fa = t[("real", queue.AUTO)], t[("ai", queue.FINE)]
    ok_ai, ok_real = t[("ai", queue.AUTO)], t[("real", queue.FINE)]
    return {
        "form": form, "bands_sha256": b["sha256"], "t_hi": b["t_hi"], "t_lo": b["t_lo"],
        "n": n_real + n_ai, "n_real": n_real, "n_ai": n_ai, "tally": t, "generators": gens,
        "per_image": per_image,
        "real_auto": fr, "real_auto_upper": stats.upper_bound(fr, n_real),
        "ai_fine": fa, "ai_fine_upper": stats.upper_bound(fa, n_ai),
        "ai_auto": ok_ai, "ai_auto_lower": stats.lower_bound(ok_ai, n_ai),
        "real_fine": ok_real, "real_fine_lower": stats.lower_bound(ok_real, n_real),
    }


def check_live_matches(live_rows: list[dict], crops: dict[str, dict], expected: dict[str, str]) -> None:
    """Every image the live queue checked must be in the band scores.csv gives it."""
    if len(live_rows) != len(expected):
        raise SystemExit(f"the live queue has {len(live_rows)} images, scores.csv has {len(expected)}")
    diffs = [r["file"] for r in live_rows if expected[crops[r["sha256"]]["source_sha256"]] != r["band"]]
    if diffs:
        raise SystemExit(f"{len(diffs)} images are in a different band from scores.csv, e.g. {diffs[:3]}")


# --- 4b: HTML -----------------------------------------------------------------------------------

def page(title: str, body: str, *, depth: int = 0, images: bool = True, here: str = "") -> str:
    up = "../" * depth
    nav = "".join(
        f'<a href="{up}{href}"{" aria-current=\"page\"" if here == key else ""}>{label}</a>'
        for key, href, label in (("queue", "index.html", "The queue"), ("results", "results.html", "Results"),
                                 ("about", "about.html", "How to read this")))
    attribution = f'<p class="attrib">{ATTRIBUTION}</p>' if images else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noai, noimageai">
<title>{esc(title)} · Second Look replay</title>
<link rel="stylesheet" href="{up}assets/site.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
<a class="brand" href="{up}index.html">Second Look <span>replay</span></a>
<nav aria-label="Pages">{nav}</nav>
</div></header>
<main id="main" class="wrap">
{body}
</main>
<footer><div class="wrap">
{attribution}
<p>{FOOTER}</p>
</div></footer>
</body>
</html>
"""


def label_text(info: dict) -> str:
    if info["label"] == "ai":
        return f"AI-made ({info['generator'].replace(' (TrueFake)', '')})"
    return "Camera photo"


def shown(info: dict) -> bool:
    """Only AI images are published. Camera photos can show identifiable people (lead ruling, 2 Nov):
    no thumbnail, no full image, a neutral placeholder instead."""
    return info["label"] == "ai"


def routing_error(band: str, label: str) -> str | None:
    if band == queue.AUTO and label == "real":
        return "Routing error: a camera photo auto-decided"
    if band == queue.FINE and label == "ai":
        return "Routing error: an AI image in Likely fine"
    return None


def pct(k: int, n: int) -> str:
    return f"{k}/{n} = {100 * k / n:.1f}%" if n else "0/0"


def chip(band: str) -> str:
    return f'<span class="chip chip-{band}">{esc(BAND_SHORT[band])}</span>'


def pending_slots(heading: str = "h2") -> str:
    items = "".join(f'<li><strong>{esc(n)}</strong> <span class="pending">Pending</span><br>'
                    f'<span class="soft">{esc(d)}</span></li>' for n, d in ARENA_SLOTS)
    return (f'<section class="arena" aria-labelledby="arena-h"><{heading} id="arena-h">Arena rows</{heading}>'
            f'<p class="soft">Not run yet. These slots stay empty until a reviewed run fills them; '
            f'no number is shown before then.</p><ul>{items}</ul></section>')


def index_html(doc: dict, rows: list[dict], crops: dict, live: dict) -> str:
    counts = doc["counts"]
    n = counts["checked"]
    bar = "".join(
        f'<a class="seg seg-{b}" href="#band-{b}" style="flex:{counts[b]}">'
        f'<span>{esc(BAND_SHORT[b])}</span> <b>{counts[b]}</b></a>' for b in queue.BAND_ORDER if counts[b])
    sections = []
    for b in queue.BAND_ORDER:
        items = []
        for r in (r for r in rows if r["band"] == b):
            info = crops[r["sha256"]]
            err = routing_error(b, info["label"])
            score = "no score" if r["detector_score"] is None else f"{r['detector_score']:.6f}"
            stem = Path(info["name"]).stem
            thumb = (f'<a class="thumb" href="i/{stem}.html" tabindex="-1" aria-hidden="true">'
                     f'<img src="t/{stem}.jpg" width="72" height="72" alt="" loading="lazy" decoding="async"></a>'
                     if shown(info) else
                     '<span class="thumb thumb-none" aria-hidden="true">Photo not shown</span>')
            items.append(
                f'<li class="row{" row-err" if err else ""}">{thumb}'
                f'<div class="row-body"><p class="row-top"><span class="rank">#{r["rank"]}</span> {chip(b)} '
                f'<span class="score">score {score}</span></p>'
                f'<p class="reason">{esc(r["reason"])}</p>'
                f'<p class="row-meta"><span>TrueFake label: {esc(label_text(info))}</span>'
                f'{f" <strong class=err>{esc(err)}</strong>" if err else ""} '
                f'<a href="i/{stem}.html">Receipt for #{r["rank"]}</a></p></div></li>')
        sections.append(
            f'<section id="band-{b}" aria-labelledby="h-{b}"><h2 id="h-{b}">{chip(b)} '
            f'<span class="count">{counts[b]} of {n}</span></h2><p class="soft">{esc(BAND_NOTE[b])}</p>'
            f'<ol class="queue">{"".join(items)}</ol></section>')
    t = live
    body = f"""
<h1>The desk triage queue, replayed</h1>
<p class="lede">A desk gets a folder of images and one question: which ones should a person look at first?
<code>secondlook queue</code> checks every file in the folder, writes an evidence receipt for each and sorts the
folder into three bands. This is one run on a folder of the {n} held-out TrueFake images ({t['n_real']} camera
photos, {t['n_ai']} AI images from five generators), frozen here. Nothing on this page runs live; nothing is
uploaded.</p>
<div class="bar" role="img" aria-label="{counts[queue.UNCERTAIN]} need a person, {counts[queue.AUTO]} auto-decided,
{counts[queue.FINE]} likely fine">{bar}</div>
<p class="legend">{' '.join(f'{chip(b)} {counts[b]}' for b in queue.BAND_ORDER)}</p>
<div class="facts">
<p><b>{pct(counts[queue.AUTO] + counts[queue.FINE], n)}</b> decided without a person (sq512 form).</p>
<p><b>{t['real_auto']} of {t['n_real']}</b> camera photos auto-decided (95% upper bound
{100 * t['real_auto_upper']:.2f}%).</p>
<p><b>{t['ai_fine']} of {t['n_ai']}</b> AI images in Likely fine (95% upper bound {100 * t['ai_fine_upper']:.2f}%).</p>
</div>
<p class="soft">Bands use thresholds fitted on the separate dev split only and frozen before this run (the
file's sha256 is on the <a href="results.html#prov-h">results page</a>).
<a href="results.html">All results and bounds</a> · <a href="about.html">How to read this</a></p>
<p class="arena-strip">Arena rows: Muse Image catch rate, Muse Spark second opinion, SAM box
<span class="pending">Pending</span> <a href="results.html#arena-h">What they are</a></p>
{''.join(sections)}
"""
    return page("The queue", body, here="queue")


def reading_html(e: dict) -> str:
    name = RUNG_NAMES.get(e.get("rung"), e.get("rung", ""))
    score = ""
    raw = e.get("raw") or {}
    if e.get("rung") == "classifier" and isinstance(raw.get("score"), (int, float)):
        score = (f'<p class="kv"><span>Model</span> {esc(raw.get("model"))}, weights sha256 '
                 f'<code class="hash">{esc(raw.get("weights_sha256"))}</code></p>')
    return (f'<li class="reading"><h3>{esc(name)} <span class="strength">Evidence weight: {esc(e.get("strength"))}</span></h3>'
            f'<p>{esc(e.get("finding"))}</p>'
            f'<p class="kv"><span>What it shows</span> {esc(e.get("proves"))}</p>'
            f'<p class="kv"><span>What it does not show</span> {esc(e.get("does_not_prove"))}</p>{score}</li>')


def receipt_cutoff(receipt: dict) -> float | None:
    for e in receipt.get("evidence", []):
        if e.get("rung") == "classifier" and isinstance((e.get("raw") or {}).get("threshold"), (int, float)):
            return e["raw"]["threshold"]
    return None


def image_html(r: dict, receipt: dict, info: dict, receipt_name: str, prev_next: tuple, live: dict) -> str:
    stem = Path(info["name"]).stem
    cut = receipt_cutoff(receipt)
    cut_text = f"its cut-off of {cut:.3f}" if cut is not None else "its detector cut-off"
    if shown(info):
        figure = (f'<img src="../f/{esc(info["name"])}" width="512" height="512" alt="TrueFake held-out image '
                  f'{esc(stem)} ({esc(label_text(info))}), 512 x 512 crop">')
        file_link = f' · <a href="../f/{esc(info["name"])}">the image file</a>'
    else:
        figure = f'<div class="photo-none"><p>{esc(PHOTO_WITHHELD)}</p></div>'
        file_link = ""
    note = (f"The band comes from the queue's dev-fitted thresholds (t_lo {live['t_lo']:.6f}, t_hi "
            f"{live['t_hi']:.6f}); the receipt below is written separately, and {cut_text} comes from the 29 Oct "
            "classifier gate, which failed its test, so the receipt does not count the score as evidence and that "
            "cut-off does not decide the band.")
    err = routing_error(r["band"], info["label"])
    readings = "".join(reading_html(e) for e in receipt.get("evidence", []))
    prev, nxt = prev_next
    pn = "".join(x for x in (
        f'<a href="{prev}.html">← Previous</a>' if prev else "",
        '<a href="../index.html#band-' + r["band"] + '">Back to the queue</a>',
        f'<a href="{nxt}.html">Next →</a>' if nxt else "") if x)
    score = "no score" if r["detector_score"] is None else f"{r['detector_score']:.6f}"
    body = f"""
<p class="crumbs">{pn}</p>
<h1>Image #{r['rank']} <span class="sub">of the queue</span></h1>
<div class="split">
<figure>{figure}
<figcaption>TrueFake label: {esc(label_text(info))}. The label comes from the dataset; Second Look did not see it.</figcaption>
</figure>
<div>
<p>{chip(r['band'])}</p>
<p class="reason big">{esc(r['reason'])}</p>
<p class="soft cutoffs">{esc(note)}</p>
{f'<p class="err">{esc(err)}. It is counted in the results page.</p>' if err else ''}
<dl class="facts-dl">
<dt>Queue rank</dt><dd>{r['rank']} of the {esc(r.get('_n'))}</dd>
<dt>Detector score</dt><dd>{score}</dd>
<dt>Ladder answer</dt><dd><code>{esc(r['answer'])}</code></dd>
<dt>Image sha256</dt><dd><code class="hash">{esc(r['sha256'])}</code></dd>
<dt>Receipt sha256</dt><dd><code class="hash">{esc(r['receipt_sha256'])}</code></dd>
</dl>
<p><a href="../r/{esc(receipt_name)}">The receipt (JSON)</a> · <a href="../r/{esc(receipt_name)}.sha256">its sha256 file</a>{file_link}</p>
</div>
</div>
<h2>The receipt</h2>
<p class="summary">{esc(receipt.get('summary'))}</p>
<h2>Each check’s reading</h2>
<ol class="readings">{readings}</ol>
<p class="soft limits">{esc(receipt.get('limits'))}</p>
<p class="soft">Receipt schema <code>{esc(receipt.get('schema'))}</code>, Second Look
<code class="hash">{esc((receipt.get('tool') or {}).get('version'))}</code>, written {esc(receipt.get('created_at'))}.</p>
{pending_slots('h2')}
"""
    return page(f"Image #{r['rank']}", body, depth=1)


def reading_it(forms: list[dict]) -> str:
    """The plain reading of the numbers, built from the same tallies (nothing typed in by hand)."""
    by = {t["form"]: t for t in forms}
    h = by[FORM]
    fine_gens = sorted({g.replace(" (TrueFake)", "") for g, c in h["generators"].items() if c[queue.FINE]})

    def decided(t: dict) -> str:
        k = sum(t["tally"][(lab, b)] for lab in ("real", "ai") for b in (queue.AUTO, queue.FINE))
        return f"{100 * k / t['n']:.1f}%"
    items = [
        f"The 1% target for camera photos auto-decided was set on the dev split. On held-out (sq512): "
        f"{h['real_auto']} of {h['n_real']}, upper bound {100 * h['real_auto_upper']:.2f}%.",
        f"The Likely fine side was also fitted at 1% on dev. On held-out (sq512): {h['ai_fine']} of {h['n_ai']} AI "
        f"images, upper bound {100 * h['ai_fine_upper']:.2f}%"
        + (f"; all from {', '.join(fine_gens)}." if fine_gens else "."),
        "\u201cDecided without a person\u201d depends on the form: "
        + ", ".join(f"{decided(t)} on {'the sq512 crops' if t['form'] == 'sq512' else 'the files as given'}"
                    for t in forms) + ".",
        "The detector was trained on a set that includes SD 1.x images, so the SD 1.5 and SD 2 rows are partly "
        "in-family. FLUX.1 and SD 3 are the nearer out-of-family tests.",
        "These thresholds route images on a desk. They are not the 29 Oct classifier gate, which stays FAIL, and "
        "they never change the ladder\u2019s answer or a receipt.",
    ]
    return ('<section aria-labelledby="read-h"><h2 id="read-h">Reading it</h2><ul>'
            + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul></section>")



def results_html(forms: list[dict], doc: dict, run: dict) -> str:
    blocks = []
    for t in forms:
        tl = t["tally"]
        rows = "".join(
            f"<tr><th scope=row>{chip(b)}</th><td>{pct(tl[('real', b)] + tl[('ai', b)], t['n'])}</td>"
            f"<td>{pct(tl[('real', b)], t['n_real'])}</td><td>{pct(tl[('ai', b)], t['n_ai'])}</td></tr>"
            for b in queue.BAND_ORDER)
        gens = "".join(
            f"<tr><th scope=row>{esc(g.replace(' (TrueFake)', ''))}</th>"
            + "".join(f"<td>{pct(c[b], sum(c.values()))}</td>" for b in queue.BAND_ORDER) + "</tr>"
            for g, c in sorted(t["generators"].items()))
        auto = sum(tl[(lab, b)] for lab in ("real", "ai") for b in (queue.AUTO, queue.FINE))
        title = ("sq512: 512 x 512 crops without metadata (the headline form, and the form replayed here)"
                 if t["form"] == "sq512" else "original: the files as given (the CLI default; second row)")
        blocks.append(f"""
<section aria-labelledby="f-{t['form']}"><h2 id="f-{t['form']}">{esc(title)}</h2>
<p class="soft">Thresholds t_hi {t['t_hi']:.6f} and t_lo {t['t_lo']:.6f}, fitted on the dev split only.</p>
<div class="tbl"><table><caption>Band shares, {t['n']} held-out images (labels from TrueFake)</caption>
<thead><tr><th scope=col>Band</th><th scope=col>All images</th><th scope=col>Camera photos</th>
<th scope=col>AI images</th></tr></thead><tbody>{rows}</tbody></table></div>
<ul class="errs">
<li>Decided without a person: <b>{pct(auto, t['n'])}</b>. Sent to a person: {pct(t['n'] - auto, t['n'])}.</li>
<li>Camera photos auto-decided as likely AI-made: <b>{pct(t['real_auto'], t['n_real'])}</b>
(exact one-sided 95% upper bound {100 * t['real_auto_upper']:.2f}%).</li>
<li>AI images put in Likely fine: <b>{pct(t['ai_fine'], t['n_ai'])}</b>
(exact one-sided 95% upper bound {100 * t['ai_fine_upper']:.2f}%).</li>
<li>AI images auto-decided: {pct(t['ai_auto'], t['n_ai'])} (95% lower bound {100 * t['ai_auto_lower']:.1f}%).
Camera photos in Likely fine: {pct(t['real_fine'], t['n_real'])} (95% lower bound {100 * t['real_fine_lower']:.1f}%).</li>
</ul>
<div class="tbl"><table><caption>AI images by generator</caption>
<thead><tr><th scope=col>Generator</th>{''.join(f'<th scope=col>{esc(BAND_SHORT[b])}</th>' for b in queue.BAND_ORDER)}</tr></thead>
<tbody>{gens}</tbody></table></div>
</section>""")
    body = f"""
<h1>Results on the held-out split</h1>
<p class="lede">640 TrueFake images that took no part in fitting the thresholds. Counts are exact; bounds are exact
one-sided 95% binomial bounds (<code>gate/stats.py</code>). The numbers are recomputed from the saved scores
by the same banding code as the queue, and the replayed sq512 run put every image in the same band.</p>
{''.join(blocks)}
{reading_it(forms)}
{pending_slots()}
<section aria-labelledby="prov-h"><h2 id="prov-h">Provenance of this page</h2>
<dl class="facts-dl">
<dt>Second Look version</dt><dd><code class="hash">{esc(doc['version'])}</code></dd>
<dt>Bands file sha256</dt><dd><code class="hash">{esc(doc['bands']['sha256'])}</code></dd>
<dt>Run</dt><dd>{run.get('jobs')} worker(s), {run.get('elapsed_seconds')} s for {doc['counts']['checked']} images</dd>
<dt>Checked, skipped, failed</dt><dd>{doc['counts']['checked']}, {doc['counts']['skipped']}, {doc['counts']['failed']}</dd>
</dl>
<p><a href="queue.json">queue.json</a> · <a href="queue.csv">queue.csv</a> ·
<a href="SHA256SUMS.txt">SHA256SUMS.txt</a> (every published image and every receipt)</p>
</section>
"""
    return page("Results", body, images=False, here="results")


def about_html() -> str:
    body = """
<h1>How to read this</h1>
<p class="lede">Second Look checks an image for provenance: Content Credentials, metadata, a detector score and its own
watermark. It writes an evidence receipt that says what each check found, what that shows and what it does not show.
The desk queue sorts a folder of images so a person spends time where it matters.</p>
<h2>The three bands</h2>
<dl class="facts-dl">
<dt>Needs a person</dt><dd>Everything that is not clearly one of the other two. Highest detector score first.</dd>
<dt>Auto-decided</dt><dd>A signed record says AI, the checks found a concern, or the detector score is at or above
t_hi. Routed as likely AI-made without a person; spot checks only.</dd>
<dt>Likely fine</dt><dd>No signed AI record, no concern, and a detector score below t_lo. Not proof that the image is
real: it means the detector score was very low and nothing else was found.</dd>
</dl>
<h2>What this replay is</h2>
<ul>
<li>One run of <code>secondlook queue --form sq512</code> on the 640 held-out TrueFake images, saved as plain pages.
No server, no account, no tracking, no script.</li>
<li>Each image page shows the receipt as written. Its sha256 is in <code>SHA256SUMS.txt</code>; check a download with
<code>shasum -a 256 -c SHA256SUMS.txt</code>.</li>
<li>Only the AI images are shown. TrueFake's camera photos can show identifiable people, so this site does not
publish them, not even as thumbnails: their rows show a placeholder, the receipt and the image sha256.</li>
<li>The TrueFake label beside each image comes from the dataset. Second Look never saw it; it is shown so you can see
where the routing was right and where it was wrong.</li>
<li>The receipt’s own verdict field (<code>cant_tell</code> on every TrueFake image) is separate from the band: the
29 Oct classifier gate failed its test, so the receipt does not count the detector score as evidence. The queue
uses the score only to decide who looks first.</li>
</ul>
"""
    return page("How to read this", body, images=False, here="about")


CSS_SOURCE = ROOT / "secondlook" / "web" / "static" / "fonts"


def write_assets(out: Path) -> None:
    a = out / "assets"
    a.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CSS_SOURCE / "PlayfairDisplay.ttf", a / "PlayfairDisplay.ttf")
    shutil.copyfile(CSS_SOURCE / "OFL.txt", a / "OFL-PlayfairDisplay.txt")
    shutil.copyfile(Path(__file__).with_name("replay_site.css"), a / "site.css")


def thumbnail(src: Path, dest: Path, size: int = 144) -> None:
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((size, size))
        im.save(dest, "JPEG", quality=78, optimize=True)


def private_paths(out: Path, needles: list[str]) -> list[str]:
    hits = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.suffix in (".html", ".json", ".csv", ".txt", ".css", ".sha256"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if any(n in text for n in needles):
                hits.append(str(p.relative_to(out)))
    return hits


def build_site(queue_dir: Path, images_dir: Path, crops: dict, forms: list[dict], out: Path,
               *, allow_home_paths: bool = False) -> dict:
    doc = json.loads((queue_dir / "queue.json").read_text(encoding="utf-8"))
    run = json.loads((queue_dir / "run.json").read_text(encoding="utf-8")) if (queue_dir / "run.json").exists() else {}
    if doc["bands"]["form"] != FORM:
        raise SystemExit(f"the queue was run with --form {doc['bands']['form']}, not {FORM}")
    if doc["counts"]["failed"] or doc["counts"]["skipped"]:
        raise SystemExit(f"the queue skipped or failed images: {doc['counts']}")
    rows = doc["queue"]
    unknown = [r["file"] for r in rows if r["sha256"] not in crops]
    if unknown:
        raise SystemExit(f"{len(unknown)} queued images are not held-out TrueFake crops, e.g. {unknown[:3]}")
    live = next(t for t in forms if t["form"] == FORM)
    check_live_matches(rows, crops, live["per_image"])

    if out.exists():
        shutil.rmtree(out)
    for d in ("i", "t", "f", "r"):
        (out / d).mkdir(parents=True)
    write_assets(out)
    sums = []
    stems = [Path(crops[r["sha256"]]["name"]).stem for r in rows]
    for k, r in enumerate(rows):
        info = crops[r["sha256"]]
        src = images_dir / info["name"]
        if sha256_file(src) != r["sha256"]:
            raise SystemExit(f"{info['name']} changed after the queue run")
        if shown(info):  # camera photos are never copied, not even as a thumbnail
            shutil.copyfile(src, out / "f" / info["name"])
            thumbnail(src, out / "t" / f"{stems[k]}.jpg")
            sums.append(f"{r['sha256']}  f/{info['name']}")
        rec_src = queue_dir / r["receipt_json"]
        if sha256_file(rec_src) != r["receipt_sha256"]:
            raise SystemExit(f"{r['receipt_json']} does not match the sha256 in queue.json")
        rec_name = rec_src.name
        shutil.copyfile(rec_src, out / "r" / rec_name)
        shutil.copyfile(rec_src.with_name(rec_name + ".sha256"), out / "r" / (rec_name + ".sha256"))
        sums.append(f"{r['receipt_sha256']}  r/{rec_name}")
        receipt = json.loads(rec_src.read_text(encoding="utf-8"))
        prev_next = (stems[k - 1] if k else None, stems[k + 1] if k + 1 < len(rows) else None)
        (out / "i" / f"{stems[k]}.html").write_text(
            image_html({**r, "_n": len(rows)}, receipt, info, rec_name, prev_next, live), encoding="utf-8")
    (out / "index.html").write_text(index_html(doc, rows, crops, live), encoding="utf-8")
    (out / "results.html").write_text(results_html(forms, doc, run), encoding="utf-8")
    (out / "about.html").write_text(about_html(), encoding="utf-8")
    shutil.copyfile(queue_dir / "queue.json", out / "queue.json")
    shutil.copyfile(queue_dir / "queue.csv", out / "queue.csv")
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "LICENSE-IMAGES.txt").write_text(
        "The images in f/ and t/ are the AI images of TrueFake (Dell'Anna et al., IJCNN 2025, arXiv 2504.20658,\n"
        "https://github.com/MMLab-unitn/TrueFake-IJCNN25), Facebook-shared subset, licensed CC BY 4.0\n"
        "(https://creativecommons.org/licenses/by/4.0/). Changes: metadata removed, centre-cropped to\n"
        "512 x 512 pixels and saved as JPEG quality 90; the thumbnails in t/ are scaled down.\n"
        "The camera photos are not included: they can show identifiable people. Their receipts are in r/.\n"
        "The TrueFake authors do not endorse Second Look.\n", encoding="utf-8")
    if not allow_home_paths:
        home = str(Path.home())
        hits = private_paths(out, [home, home.replace("/Users/", "/private/var/")])
        if hits:
            shutil.rmtree(out)
            raise SystemExit(f"{len(hits)} files hold this machine's home folder path (e.g. {hits[:2]}); "
                             "run the queue from a checkout outside the home folder, or pass --allow-home-paths")
    return {"pages": len(rows) + 3, "images": len(rows), "counts": doc["counts"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--split", type=Path, required=True, help="J3 split.csv")
    p.add_argument("--scores", type=Path, required=True, help="scale-eval scores.csv")
    p.add_argument("--crops", type=Path, required=True, help="the sq512 crops folder with its index.csv")
    p.add_argument("--work", type=Path, required=True, help="where the staged images and the queue run go")
    p.add_argument("--out", type=Path, required=True, help="the static site folder (replaced)")
    p.add_argument("--python", default=sys.executable, help="Python with secondlook's classifier extras")
    p.add_argument("--repo", type=Path, default=ROOT, help="checkout to run `secondlook queue` from")
    p.add_argument("--jobs", type=int, default=None)
    p.add_argument("--reuse-queue", action="store_true", help="use WORK/queue/queue.json if it is there")
    p.add_argument("--allow-home-paths", action="store_true")
    a = p.parse_args(argv)

    held = held_out_rows(read_csv(a.split))
    crops = stage_crops(held, a.crops, a.work / "images")
    score_rows = read_csv(a.scores)
    forms = [tally_form(score_rows, held, f) for f in ("sq512", "original")]
    qdir = a.work / "queue"
    if not (a.reuse_queue and (qdir / "queue.json").exists()):
        run_queue(a.work / "images", qdir, a.python, a.repo, a.jobs)
    info = build_site(qdir, a.work / "images", crops, forms, a.out, allow_home_paths=a.allow_home_paths)
    print(f"Site: {a.out}/index.html ({info['pages']} pages, {info['images']} images; counts {info['counts']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
