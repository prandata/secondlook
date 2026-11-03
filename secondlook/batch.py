"""Batch: check a whole folder of images and write a receipt per file plus a summary (B1, DAT-871).

    secondlook batch FOLDER --out DIR [--jobs N] [--no-pdf]

What it writes into DIR:
    receipts/NNNN-<name>.json   one evidence receipt per checked file, written by secondlook.receipt (R1,
                                DAT-870): canonical JSON, with NNNN-<name>.json.sha256 beside it and
                                NNNN-<name>.pdf unless --no-pdf. See docs/RECEIPT.md.
    summary.json, summary.csv   one row per file: answer, one-line summary, sha256, receipt paths
                                receipts.pdf is true only if every checked file got its PDF; pdf_written and pdf_failed count them
    summary.html                the same, as one static page (inline style, no script, nothing fetched)
    run.json                    how this run went (jobs, seconds per image). Timings differ on every run.

The receipts are not part of the byte-for-byte promise below: each one carries `created_at`, the time it
was made, and so do its .json.sha256 and its PDF. With `created_at` removed, two runs give identical
receipt JSON. summary.json, summary.csv and summary.html are the files that stay the same.

The answers come from `secondlook.ladder.run`, one file at a time, exactly as `secondlook check`
gives them. Batch adds three things around that and changes nothing inside it:

1. It chooses which files to check. Only .jpg, .jpeg, .png and .webp are read. A symlink that leaves
   the folder, anything that is not a regular file, a file over 25 MB, and an image over the
   classifier's size or shape limits (S3: 100 million pixels, long side at most 8 times the short
   side) is skipped and listed with its reason. Nothing skipped is ever opened as an image by the
   ladder.
2. It groups near-duplicate copies: a perceptual hash (dHash, 64 bits, from Pillow) with a Hamming
   distance of at most 8, or an identical sha256. The groups are information only. They are computed
   after every answer is final, they are never evidence, and they never change an answer.
3. It runs files in parallel worker processes (`--jobs`). Each worker loads its rungs, and the
   classifier model, once. The count is capped at min(4, half the CPU cores) unless the caller
   opts in to more (`allow_many_workers`, `--allow-many-workers`): every worker holds its own copy
   of the model.

Workers are started with "spawn", which re-imports the calling script in each of them. A script that
calls `run_batch` with more than one worker must therefore guard the call with
`if __name__ == "__main__":`; without it `run_batch` raises a RuntimeError that says so.

No network is used. The summary is deterministic: the same folder and the same code give the same
summary.json, summary.csv and summary.html, byte for byte, for any `--jobs`.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import csv
import hashlib
import html
import functools
import io
import json
import logging
import multiprocessing
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from . import ladder
from .explain import explain
from . import receipt as receipt_writer
from . import rungs as rung_registry
from .rungs import classifier as _clf

log = logging.getLogger("secondlook.batch")

SCHEMA = "second-look-batch/1"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
MAX_BYTES = 25 * 1024 * 1024  # one file; the same limit as the web page
HAMMING_MAX = 8               # near-duplicate copies: dHash distance at most this
FLAT_RANGE = 8                # a picture whose 9 x 8 grey grid varies by less than this has no usable hash
# The names this module writes, and the only ones it clears: the receipt, its PDF, and the .json.sha256
# that secondlook.receipt.write_json leaves beside each receipt.
RECEIPT_NAME = re.compile(r"^\d{4}-.*\.(json|pdf|json\.sha256)$")
MEMORY_NOTE = ("Each worker holds its own copy of the classifier model, about 0.9 GiB, so N workers need "
               "about N x 0.9 GiB of memory.")
GUARD_MESSAGE = ("run_batch started worker processes, and each one re-ran the calling script before it could "
                 "start. Put the call under `if __name__ == \"__main__\":` in that script (or call run_batch "
                 "with jobs=1).")

DUP_LABEL = "near-duplicate copies"
DUP_NOTE = ("Information only. Near-duplicate copies are not evidence and never change an answer: "
            "each file was checked on its own.")
# The page's own labels (web/static/app.js ANSWERS and batch.js), so the CLI, summary.html and the page name
# each answer the same way (DAT-850 A263). The machine values (signed_camera, ...) are unchanged everywhere.
ANSWER_LABELS = {
    "signed_ai": "Signed as AI-made",
    "signed_camera": "Signed as a camera capture",
    "concerns": "Reasons to look closer",
    "cant_tell": "Can't tell",
}
COUNT_WORDS = ("No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine")


def answer_label(answer: str, reasons: int = 0) -> str:
    """One file's answer as the page words it: a concerns answer by its count ("One reason to look closer",
    as app.js concernLabel does), every other answer by ANSWER_LABELS."""
    if answer != "concerns" or reasons < 1:
        return ANSWER_LABELS.get(answer, answer)
    word = COUNT_WORDS[reasons] if reasons < len(COUNT_WORDS) else f"{reasons:,}"
    return f"{word} {'reason' if reasons == 1 else 'reasons'} to look closer"


# --- choosing the files ------------------------------------------------------------------------

def shape_problem(path: str) -> str | None:
    """Why this image is over the classifier's size or shape limits, or None. Reads the header only.

    A file Pillow cannot open at all is not refused here: the ladder reports it as can't tell.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Image.DecompressionBombError:
        return "image is too large to open safely"
    except Exception:
        return None
    try:
        _clf._check_size(w, h)
    except _clf.CantAnalyse as exc:
        return str(exc)
    return None


def discover(folder, exclude=None) -> tuple[list[tuple[str, str]], list[dict], dict]:
    """Walk `folder`; return ([(relative path, real path)], skipped, ignored counts), all sorted.

    The real path is where the file really is after any symlink inside the folder is resolved, so the
    ladder reads exactly what was checked here. `exclude` is a directory (the output folder) to leave out.
    """
    root = Path(folder).resolve(strict=True)
    excluded = Path(exclude).resolve() if exclude is not None else None
    found: list[tuple[str, str]] = []
    skipped: list[dict] = []
    ignored = {"hidden": 0, "not_an_image": 0}
    seen: set[str] = set()

    def skip(rel: str, reason: str) -> None:
        skipped.append({"file": rel, "reason": reason})

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            full = here / d
            if d.startswith("."):
                ignored["hidden"] += 1
            elif excluded is not None and full.resolve() == excluded:
                continue
            elif full.is_symlink():  # os.walk would not follow it; say so rather than skip it silently
                skip(full.relative_to(root).as_posix(), "symlinked folder, not followed")
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if name.startswith("."):
                ignored["hidden"] += 1
                continue
            if not name.lower().endswith(IMAGE_SUFFIXES):
                ignored["not_an_image"] += 1
                continue
            full = here / name
            rel = full.relative_to(root).as_posix()
            real = Path(os.path.realpath(full)) if full.is_symlink() else full
            if full.is_symlink() and not real.is_relative_to(root):
                skip(rel, "symlink leaves the folder")
                continue
            try:
                st = os.stat(real)
            except OSError:
                skip(rel, "can't be read")
                continue
            if not stat.S_ISREG(st.st_mode):
                skip(rel, "not a regular file")
            elif st.st_size == 0:
                skip(rel, "empty file")
            elif st.st_size > MAX_BYTES:
                skip(rel, f"over {MAX_BYTES // (1024 * 1024)} MB")
            elif str(real) in seen:
                skip(rel, "symlink to a file already in the batch")
            else:
                problem = shape_problem(str(real))
                if problem:
                    skip(rel, problem)
                else:
                    seen.add(str(real))
                    found.append((rel, str(real)))
    found.sort()
    skipped.sort(key=lambda s: (s["file"], s["reason"]))
    return found, skipped, ignored


# --- near-duplicate copies ---------------------------------------------------------------------

def dhash(path: str) -> tuple[int | None, bool]:
    """(64-bit difference hash, usable). The hash is None when Pillow cannot read the picture.

    Grey, shrunk to at most 256 px with EXIF orientation applied, resized to 9 x 8, then one bit per
    pair of neighbours: left brighter than right. `usable` is False for a flat picture, whose hash
    says nothing about what it shows.
    """
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im.draft("L", (512, 512))  # JPEG only: decode at a fraction of the size
            g = im.convert("L")
            g.thumbnail((256, 256))
            try:
                orient = im.getexif().get(0x0112)
            except Exception:
                orient = None
            transpose = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                         4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                         6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                         8: Image.Transpose.ROTATE_90}.get(orient)
            if transpose is not None:
                g = g.transpose(transpose)
            px = g.resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    except Exception as exc:
        log.info("no hash for %s: %s", path, exc)
        return None, False
    h = 0
    for row in range(8):
        for col in range(8):
            h = (h << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return h, (max(px) - min(px)) >= FLAT_RANGE


def group_near_duplicates(items: list[dict]) -> list[dict]:
    """Group rows that are near-duplicate copies of each other. Information only.

    Each item has "file", "sha256", "dhash" (int or None) and "usable". Two files are linked when
    their sha256 is the same, or both hashes are usable and differ in at most HAMMING_MAX bits. A group
    is a connected set of links, so `max_distance` (the widest pair inside it) can be larger than the
    links that joined it. Hashes are never compared with an unusable one.
    """
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i], items[j]
            same = a["sha256"] == b["sha256"]
            close = (a["usable"] and b["usable"] and a["dhash"] is not None and b["dhash"] is not None
                     and (a["dhash"] ^ b["dhash"]).bit_count() <= HAMMING_MAX)
            if same or close:
                parent[find(i)] = find(j)
    members: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        members.setdefault(find(i), []).append(item)
    groups = []
    for group in members.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda it: it["file"])
        widest = 0
        for x in range(len(group)):
            for y in range(x + 1, len(group)):
                a, b = group[x], group[y]
                if a["sha256"] == b["sha256"]:
                    d = 0
                elif a["dhash"] is not None and b["dhash"] is not None:
                    d = (a["dhash"] ^ b["dhash"]).bit_count()
                else:
                    d = 64
                widest = max(widest, d)
        groups.append({"files": [it["file"] for it in group], "max_distance": widest})
    groups.sort(key=lambda g: g["files"][0])
    for k, g in enumerate(groups, 1):
        g["id"] = f"g{k}"
    return [{"id": g["id"], "label": DUP_LABEL, "files": g["files"], "max_distance": g["max_distance"],
             "limit": HAMMING_MAX, "note": DUP_NOTE} for g in groups]


# --- one file, in a worker -----------------------------------------------------------------------

_W: dict = {}  # per worker process: the rungs, loaded once


def _init_worker(rung_names) -> None:
    _W["rungs"] = rung_registry.load(rung_names)
    if any(name == "classifier" for name, _ in _W["rungs"]):
        try:
            if _clf.weights_path().exists():
                _clf.load_model()  # once per worker; check() reuses it for every file
        except Exception as exc:  # check() reports "can't analyse" per file; the batch carries on
            log.warning("classifier did not load in this worker: %s", exc)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_stem(rel: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", rel.replace("/", "__")).strip(".")[-80:] or "file"


def _process(task) -> dict:
    """Check one file and write its receipt. Returns a plain row; never raises."""
    idx, rel, real, out_dir, pdf = task
    row: dict = {"idx": idx, "file": rel}
    try:
        t0 = time.perf_counter()
        result = ladder.run(real, rungs=_W["rungs"])
        row["seconds"] = round(time.perf_counter() - t0, 4)
        row["bytes"] = os.path.getsize(real)
        row["sha256"] = _sha256(real)
        row["answer"] = result.answer
        row["summary"] = explain(result).summary   # the sentence the page and the receipt show (DAT-850)
        row["reasons"] = ([e.finding for e in result.evidence if ladder._is_concern(e)]
                          if result.answer == "concerns" else [])
        row["version"] = result.version
        row["dhash"], row["usable"] = dhash(real)
    except Exception as exc:  # the ladder does not raise; this is the backstop, and the file is reported as failed
        log.exception("check failed on %s", rel)
        return {"idx": idx, "file": rel, "error": f"the check failed on this file ({type(exc).__name__})"}
    row["receipt_json"] = row["receipt_pdf"] = row["receipt_error"] = None
    base = f"{idx:04d}-{_safe_stem(rel)}"
    try:
        receipt = receipt_writer.build(result, real)
        receipt_writer.write_json(receipt, Path(out_dir) / "receipts" / f"{base}.json")  # also leaves .json.sha256
        row["receipt_json"] = f"receipts/{base}.json"
        if pdf:
            receipt_writer.write_pdf(receipt, Path(out_dir) / "receipts" / f"{base}.pdf")
            row["receipt_pdf"] = f"receipts/{base}.pdf"
    except Exception as exc:  # the answer stands; the missing receipt is stated in the summary
        log.warning("receipt failed on %s: %r", rel, exc)
        row["receipt_error"] = f"{type(exc).__name__}"
    return row


# --- the run -------------------------------------------------------------------------------------

def max_jobs() -> int:
    """The most workers allowed without opting in: the smaller of 4 and half the CPU cores, at least 1."""
    return max(1, min(4, (os.cpu_count() or 1) // 2))


def _guard_marker(parent_pid: int) -> Path:
    return Path(tempfile.gettempdir()) / f"secondlook-batch-unguarded-{parent_pid}"


def _refuse_if_reimported_by_a_worker() -> None:
    """Called first in run_batch. A spawned worker re-imports the calling script, and a script with no
    `if __name__ == "__main__":` guard calls run_batch again inside that import (the same flag the standard
    library checks before it refuses to start a process). Say so, and leave a marker for the parent."""
    if getattr(multiprocessing.current_process(), "_inheriting", False):
        try:
            _guard_marker(os.getppid()).write_text("1")
        except OSError:
            pass
        raise RuntimeError(GUARD_MESSAGE)


def default_jobs() -> int:
    """Two workers. Each holds its own copy of the model (about 0.9 GiB) and they share one GPU, so more
    workers did not help with the classifier on the M5 (docs/PERFORMANCE.md); one is used on a 1 to 3 core machine."""
    return 1 if (os.cpu_count() or 1) < 4 else 2


def _cell(text) -> str:
    """A CSV cell safe to open in a spreadsheet: a leading = + - @ tab or CR would run as a formula."""
    s = "" if text is None else str(text)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["file", "answer", "summary", "sha256", "bytes", "near_duplicate_group", "receipt_json", "receipt_pdf"])
    for r in rows:
        w.writerow([_cell(r["file"]), r["answer"], _cell(r["summary"]), r["sha256"], r["bytes"],
                    r["near_duplicate_group"] or "", _cell(r["receipt_json"]), _cell(r["receipt_pdf"])])
    return buf.getvalue()


# summary.html in the product's look (DAT-850 A263): web/static/app.css's tokens (cream, paper, ink, rust, line,
# soft) and its serif stack, in one boxed sheet. The file stays self-contained: no link, no script, no fetch.
# Each sha256 sits on its own full-width line under its file, so it takes one line at 1440 px and two at 390 px.
# DAT-850 A271: the headings set in the pages' own Playfair Display Bold, carried inside the file as a data: font
# (see SUMMARY_FONT); the files table has fixed column widths, so long reasons never squeeze the file names; the
# answer counts sit beside their labels; body and notes are 16 px, hashes 13 px.
SUMMARY_CSS = (
    ":root{--cream:#F4F0E6;--paper:#FBF9F3;--ink:#2B2F33;--rust:#B4532F;--rust-text:#9E4526;--line:#D9D2C2;"
    "--soft:#5F6367;--serif:\"Playfair Display\",\"Playfair\",Georgia,\"Times New Roman\",serif;"
    "--sans:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Helvetica,Arial,sans-serif;"
    "--mono:ui-monospace,\"SF Mono\",Menlo,Consolas,monospace;"
    "--title:\"SL Title\",var(--serif)}"
    "*{box-sizing:border-box}"
    "body{margin:0;background:var(--cream);color:var(--ink);font:16px/1.55 var(--sans)}"
    "main{max-width:1040px;margin:32px auto;padding:28px 32px 32px;background:var(--paper);"
    "border:1px solid var(--line);border-radius:14px}"
    ".kicker{display:flex;align-items:center;gap:12px;margin:0 0 8px;font-weight:600}"
    ".badge{font:600 12px/1 var(--sans);text-transform:uppercase;letter-spacing:.08em;color:var(--rust-text);"
    "border:1.5px solid var(--rust);border-radius:999px;padding:5px 9px}"
    "h1{font:700 36px/1.1 var(--title);margin:0;letter-spacing:-.01em}"
    "h2{font:700 22px/1.2 var(--title);margin:32px 0 8px;padding-bottom:8px;border-bottom:2px solid var(--ink)}"
    ".lede{color:var(--soft);margin:10px 0 0;max-width:70ch}"
    ".note{color:var(--soft);margin:12px 0 0;padding-left:12px;border-left:3px solid var(--rust);"
    "max-width:70ch}"
    "table{border-collapse:collapse;width:100%;margin:4px 0 0}"
    "table.count{max-width:360px}"
    "table.files{table-layout:fixed}col.c-file{width:30%}col.c-rc{width:7.5em}col.c-dup{width:6em}"
    "@media (max-width:900px){col.c-file{width:34%}}"
    "th{font:600 12px/1.3 var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--soft);"
    "text-align:left;padding:8px 12px 8px 0;border-bottom:1px solid var(--line)}"
    "td{padding:12px 12px 4px 0;text-align:left;vertical-align:top}"
    "tr.end td,table.plain td{padding-bottom:12px;border-bottom:1px solid var(--line)}"
    ".count td{padding:8px 12px 8px 0}.count td.n{font-variant-numeric:tabular-nums;font-weight:600;width:6ch}"
    ".file{font-weight:600;overflow-wrap:anywhere}.ext{white-space:nowrap}"
    ".answer{font-weight:700}.say{color:var(--ink)}"
    ".say ul{margin:6px 0 0;padding-left:18px}"
    "td.rc{white-space:nowrap}td.hash{padding-top:0;font:13px/1.5 var(--mono);color:var(--soft);overflow-wrap:anywhere}"
    "td.hash span{font-family:var(--sans);text-transform:uppercase;letter-spacing:.06em;font-size:12px;"
    "margin-right:8px}"
    "a{color:var(--ink);text-underline-offset:3px}a:hover{color:var(--rust-text)}"
    "code{font:14px/1.5 var(--mono);overflow-wrap:anywhere}"
    "@media (max-width:640px){main{margin:0;border:0;border-radius:0;padding:24px 18px}h1{font-size:30px}}"
    # Below 760 px the four columns would leave the answer under 250 px, so each file's row stacks.
    "@media (max-width:760px){table.files,table.files tbody,table.files tr{display:block}"
    "table.files tr.head,table.files colgroup{display:none}table.files td{display:block;padding:2px 0}"
    "table.files tr:not(.end) td:first-child{padding-top:14px}"
    "table.files tr.end td{padding:4px 0 14px}"
    "table.files td.dup:not(:empty)::before{content:\"Copies: \";font-weight:600}}"
)

# The headings font, carried inside summary.html so it needs no network and no installed font (DAT-850 A271).
# fonts/sl-title-bold-latin.woff2 is the pages' own static/fonts/PlayfairDisplay.ttf fixed at Bold and cut to
# Latin (18.4 KB), made by scripts/make_summary_font.py. A cut-down copy is a Modified Version under the OFL
# (OFL FAQ 2.5, 2.6), and "Playfair Display" is a Reserved Font Name, so the copy is named "SL Title". Its
# copyright and licence name records are unchanged, and the page carries the full OFL text beside it.
SUMMARY_FONT = Path(__file__).parent / "fonts" / "sl-title-bold-latin.woff2"
SUMMARY_FONT_LICENCE = Path(__file__).parent / "web" / "static" / "fonts" / "OFL.txt"


@functools.cache
def summary_font_css() -> str:
    notice = SUMMARY_FONT_LICENCE.read_text(encoding="utf-8").replace("*/", "* /").strip()
    data = base64.b64encode(SUMMARY_FONT.read_bytes()).decode("ascii")
    return ("/* SL Title: a Latin Bold subset of Playfair Display, renamed as the licence requires.\n"
            f"{notice}\n*/\n"
            "@font-face{font-family:\"SL Title\";font-weight:700;font-style:normal;font-display:block;"
            f"src:url(data:font/woff2;base64,{data}) format(\"woff2\")}}\n")


def _file_name(name: str) -> str:
    """A file name whose extension never breaks across lines: the stem may wrap, the extension stays whole."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 8:
        return html.escape(name)
    return f'{html.escape(stem)}<span class="ext">.{html.escape(ext)}</span>'


def _html(summary: dict) -> str:
    esc = html.escape

    def link(path, kind):
        return f'<a href="{esc(quote(path), quote=True)}">{kind}</a>' if path else ""

    c = summary["counts"]
    out = [
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "font-src data:\">",
        "<title>Second Look: batch summary</title>",
        f"<style>{summary_font_css()}{SUMMARY_CSS}</style></head><body><main>",
        '<p class="kicker">Second Look <span class="badge">In development</span></p>',
        "<h1>Batch summary</h1>",
        f"<p class=\"lede\">Folder <strong>{esc(summary['folder'])}</strong>. Checked {c['checked']} of {c['images']} "
        f"image files accepted for checking. Skipped, not checked: {c['skipped']}. "
        f"Code version <code>{esc(summary['version'])}</code>.</p>",
        "<p class=\"note\">Second Look never says an image is genuine. A signed record says how a signer says the "
        "file was made. Reasons to look closer are not verdicts. Can't tell means nothing was found "
        "either way.</p>",
        '<h2>Answers</h2><table class="count">',
    ]
    for key, label in ANSWER_LABELS.items():
        out.append(f'<tr><td>{esc(label)}</td><td class="n">{c[key]}</td></tr>')
    out.append("</table>")
    out.append('<h2>Files</h2><table class="files"><colgroup><col class="c-file"><col class="c-say">'
               '<col class="c-rc"><col class="c-dup"></colgroup>'
               '<tr class="head"><th>File</th><th>Answer</th><th>Receipt</th><th>Copies</th></tr>')
    for r in summary["files"]:
        receipts = " · ".join(x for x in (link(r["receipt_json"], "JSON"), link(r["receipt_pdf"], "PDF")) if x)
        out.append(
            f"<tr><td class=\"file\">{_file_name(r['file'])}</td>"
            f"<td class=\"say\"><span class=\"answer\">{esc(answer_label(r['answer'], len(r['reasons'])))}</span>"
            f"<br>{esc(r['summary'])}"
            + ("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in r["reasons"]) + "</ul>" if r["reasons"] else "")
            + f"</td><td class=\"rc\">{receipts}</td><td class=\"dup\">{esc(r['near_duplicate_group'] or '')}</td></tr>")
        out.append(f"<tr class=\"end\"><td class=\"hash\" colspan=\"4\"><span>sha256</span>{esc(r['sha256'])}</td></tr>")
    out.append("</table>")
    if summary["failed"]:
        out.append('<h2>Failed</h2><table class="plain"><tr><th>File</th><th>What happened</th></tr>')
        out += [f"<tr><td>{esc(f['file'])}</td><td>{esc(f['reason'])}</td></tr>" for f in summary["failed"]]
        out.append("</table>")
    if summary["skipped"]:
        out.append('<h2>Skipped, not checked</h2><table class="plain"><tr><th>File</th><th>Why</th></tr>')
        out += [f"<tr><td>{esc(s['file'])}</td><td>{esc(s['reason'])}</td></tr>" for s in summary["skipped"]]
        out.append("</table>")
    out.append(f"<h2>{esc(DUP_LABEL.capitalize())}</h2><p class=\"note\">{esc(DUP_NOTE)}</p>")
    if summary["near_duplicate_groups"]:
        out.append('<table class="plain"><tr><th>Group</th><th>Files</th><th>Widest hash distance</th></tr>')
        for g in summary["near_duplicate_groups"]:
            out.append(f"<tr><td>{esc(g['id'])}</td><td>{'<br>'.join(esc(f) for f in g['files'])}</td>"
                       f"<td>{g['max_distance']} (limit {g['limit']})</td></tr>")
        out.append("</table>")
    else:
        out.append("<p>None found.</p>")
    out.append("</main></body></html>\n")
    return "\n".join(out)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def run_batch(folder, out_dir, *, jobs: int | None = None, pdf: bool = True,
              rung_names=rung_registry.DEFAULT_RUNGS,
              progress: Callable[[int, int, str], None] | None = None,
              allow_many_workers: bool = False) -> dict:
    """Check every image in `folder`, write receipts and the summary into `out_dir`.

    `jobs` is the number of worker processes (default: default_jobs()). Asking for more than max_jobs()
    raises ValueError unless `allow_many_workers` is True; see MEMORY_NOTE. With more than one worker the
    calling script must guard this call with `if __name__ == "__main__":` (RuntimeError otherwise).

    Returns the summary (the same content as summary.json) plus a "run" key with this run's timing,
    which is not part of the deterministic files. `rung_names` picks the rungs, for the benchmark
    and for tests; the default is the installed ladder.
    """
    _refuse_if_reimported_by_a_worker()
    if jobs is not None and int(jobs) < 1:
        raise ValueError("jobs must be 1 or more")
    if jobs is not None and int(jobs) > max_jobs() and not allow_many_workers:
        raise ValueError(f"jobs={jobs} is above the limit of {max_jobs()} on this machine (the smaller of 4 and "
                         f"half the CPU cores). {MEMORY_NOTE} Pass allow_many_workers=True to go higher.")
    t0 = time.perf_counter()
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"{folder} is not a folder")
    out = Path(out_dir).resolve()
    (out / "receipts").mkdir(parents=True, exist_ok=True)
    for old in (out / "receipts").iterdir():  # our own earlier output only, so a rerun leaves no stale receipts
        if old.is_file() and RECEIPT_NAME.match(old.name):
            old.unlink()

    found, skipped, ignored = discover(root, exclude=out)
    n = len(found)
    workers = max(1, min(default_jobs() if jobs is None else int(jobs), n or 1))
    tasks = [(i, rel, real, str(out), pdf) for i, (rel, real) in enumerate(found)]
    rows: list[dict | None] = [None] * n
    done = 0

    def finish(i: int, row: dict) -> None:
        nonlocal done
        rows[i] = row
        done += 1
        if progress:
            progress(done, n, found[i][0])

    if workers == 1:
        _init_worker(rung_names)
        for t in tasks:
            finish(t[0], _process(t))
    else:
        marker = _guard_marker(os.getpid())
        marker.unlink(missing_ok=True)
        with cf.ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=_init_worker, initargs=(rung_names,)) as pool:
            futures = {pool.submit(_process, t): t[0] for t in tasks}
            for fut in cf.as_completed(futures):
                i = futures[fut]
                try:
                    finish(i, fut.result())
                except Exception as exc:  # a worker died (for example out of memory): that file is failed, not guessed
                    finish(i, {"idx": i, "file": found[i][0],
                               "error": f"the check stopped before it finished ({type(exc).__name__})"})
        if marker.exists():  # the workers died re-running an unguarded script: that is the caller's bug, not the files'
            marker.unlink(missing_ok=True)
            raise RuntimeError(GUARD_MESSAGE)

    ok = [r for r in rows if "error" not in r]
    failed = [{"file": r["file"], "reason": r["error"]} for r in rows if "error" in r]
    groups = group_near_duplicates(
        [{"file": r["file"], "sha256": r["sha256"], "dhash": r["dhash"], "usable": r["usable"]} for r in ok])
    group_of = {f: g["id"] for g in groups for f in g["files"]}

    files = [{"file": r["file"], "bytes": r["bytes"], "sha256": r["sha256"],
              "dhash": None if r["dhash"] is None else f"{r['dhash']:016x}",
              "answer": r["answer"], "summary": r["summary"], "reasons": r["reasons"],
              "receipt_json": r["receipt_json"], "receipt_pdf": r["receipt_pdf"],
              "receipt_error": r["receipt_error"], "near_duplicate_group": group_of.get(r["file"])} for r in ok]
    # pdf is true only when PDFs were asked for and every checked file got one. A file whose PDF could not be
    # written counts as failed whether the JSON before it worked or not; files that failed the check have no
    # receipt and are in "failed", not here.
    pdf_written = sum(1 for f in files if f["receipt_pdf"]) if pdf else 0
    pdf_failed = (len(files) - pdf_written) if pdf else 0
    pdf_ok = bool(pdf) and pdf_failed == 0
    counts = {"images": n, "checked": len(ok), "skipped": len(skipped), "failed": len(failed)}
    for key in ANSWER_LABELS:
        counts[key] = sum(1 for f in files if f["answer"] == key)
    counts["near_duplicate_groups"] = len(groups)
    summary = {
        "schema": SCHEMA,
        "folder": root.resolve().name,
        "version": ok[0]["version"] if ok else ladder._version(),
        "rungs": [name for name, _ in rung_registry.load(rung_names)],
        "receipts": {"writer": "secondlook.receipt", "pdf": pdf_ok, "pdf_written": pdf_written,
                     "pdf_failed": pdf_failed},
        "counts": counts,
        "files": files,
        "skipped": skipped,
        "failed": failed,
        "ignored": ignored,
        "near_duplicate_groups": groups,
    }
    _write(out / "summary.json", json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    _write(out / "summary.csv", _csv(files))
    _write(out / "summary.html", _html(summary))

    run = {"jobs": workers, "elapsed_seconds": round(time.perf_counter() - t0, 3),
           "seconds_per_image": {r["file"]: r["seconds"] for r in ok}}
    _write(out / "run.json", json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    return {**summary, "run": run}
