"""Desk triage queue (J4, DAT-1367): a folder of images in, a ranked queue and a receipt per image out.

    secondlook queue FOLDER --out DIR [--form original|sq512] [--jobs N] [--pdf]

Each image runs through the ladder exactly as `secondlook check` runs it (C2PA, IPTC, EXIF, the detector
score, the watermark). The ladder's answer is never changed here. The queue adds one routing step around
it, rule BAND from the J3 pre-registration (arena/PREREG.md, section 5), with thresholds fitted on the
TrueFake dev split only and frozen in `queue_bands.json` (sha256 in `queue_bands.json.sha256`, and pinned
in this module as BANDS_SHA256, so the file and its sidecar cannot be edited together):

    auto_decided  the ladder found a signed AI record or a concern (f = 1), or the detector score is at or
                  above t_hi (the dev operating point with at most 1% of dev real photos flagged)
    likely_fine   no signed AI record and no concern (f = 0), and the detector score is below t_lo (the
                  largest threshold with at most 1% of dev AI images below it). Not proof the image is real.
    uncertain     everything else: a person looks, highest detector score first

A band is used only when its frozen dev error rate is 1% or less (MAX_ERROR); otherwise its images go to
a person. One deviation from PREREG's wording, which no TrueFake row can reach (the ladder answered
cant_tell on all 983): a signed camera record together with a score at or above t_hi is a conflict, and a
conflict goes to a person, not to auto_decided. An image with no detector score is never auto-cleared.

The 29 Oct classifier gate stays FAIL. These thresholds route images on a desk; they are not the gate's
threshold, they never raise a concern in the ladder, and they never change an answer or a receipt.

What it writes into DIR:
    receipts/NNNN-<name>.json   the existing evidence receipt (secondlook.receipt), with its .json.sha256;
                                and NNNN-<name>.pdf with --pdf
    queue.json, queue.csv       every checked image in queue order: rank, band, reason, detector score,
                                ladder answer, the file's sha256 and its receipt's path and sha256
    run.json                    how this run went (workers, seconds per image). Timings differ on every run.

queue.json and queue.csv are deterministic for the same folder, code, bands file and run time. Each
receipt records the time it was made (`created_at`), and queue.json holds each receipt's sha256, so two runs
in different seconds differ in those hashes. A normal run records the real time. `run_time` pins it for a
reproducible run (the tests, a recorded demo): every receipt then says that time, queue.json says so under
`run_time`, and run.json repeats it.
"""
from __future__ import annotations

import concurrent.futures as cf
import csv
import hashlib
import io
import json
import math
import multiprocessing
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from . import batch, ladder
from . import receipt as receipt_writer
from . import rungs as rung_registry

SCHEMA = "second-look-queue/1"
BANDS_SCHEMA = "second-look-queue-bands/1"
BANDS_PATH = Path(__file__).with_name("queue_bands.json")
# The sha256 of the committed frozen file, pinned in code (DAT-1404). The sidecar beside the file can be edited
# together with it, so the queue also checks the frozen file against this constant. A refit of the bands
# (scripts/fit_queue_bands.py) is a code change: it must update this value in the same commit.
BANDS_SHA256 = "5ec803a5197165164232d80aa1d430983e7c984e2aeacc1ffc615d7199eb0327"
MAX_ERROR = 0.01   # per class, on the dev split: reals auto-flagged, AI images auto-cleared
FORMS = ("original", "sq512")

AUTO = "auto_decided"
UNCERTAIN = "uncertain"
FINE = "likely_fine"
# Queue order: the images a person must look at first, then the two automatic piles for spot checks.
BAND_ORDER = (UNCERTAIN, AUTO, FINE)
BAND_LABELS = {
    UNCERTAIN: "Needs a person",
    AUTO: "Auto-decided: routed as likely AI-made",
    FINE: "Likely fine (not proof that it is real)",
}
FLAG_ANSWERS = ("signed_ai", "concerns")   # f = 1 in PREREG section 4


class BandsError(ValueError):
    """The frozen bands file is missing, altered (sha256 mismatch) or not usable. str() is the reason."""


# --- fitting (dev split only) -------------------------------------------------------------------

def allowed_errors(n: int, max_error: float = MAX_ERROR) -> int:
    """floor(max_error x n): the most errors a class of n dev images may have (169 reals -> 1)."""
    return math.floor(max_error * n + 1e-9)


def fit_t_hi(real: Iterable[float], ai: Iterable[float], max_false: int) -> float:
    """Rule OP: over every distinct dev score and +infinity, the threshold with the largest dev catch
    (AI scores >= t) such that at most `max_false` dev real scores are >= t. Ties: the highest threshold."""
    real, ai = list(real), list(ai)
    best: tuple[int, float] | None = None
    for t in sorted(set(real) | set(ai)) + [math.inf]:
        if sum(s >= t for s in real) > max_false:
            continue
        key = (sum(s >= t for s in ai), t)
        if best is None or key > best:
            best = key
    assert best is not None  # +infinity always qualifies
    return best[1]


def fit_t_lo(real: Iterable[float], ai: Iterable[float], max_missed: int) -> float:
    """Rule BAND's clear threshold: the largest distinct dev score t such that at most `max_missed` dev
    AI scores are below t. -infinity (nothing cleared) when there are no dev scores."""
    real, ai = list(real), list(ai)
    ok = [t for t in set(real) | set(ai) if sum(s < t for s in ai) <= max_missed]
    return max(ok) if ok else -math.inf


def band_counts(real: list[float], ai: list[float], t_hi: float, t_lo: float) -> dict:
    """What the two thresholds do to one labelled set (f = 0 for every row, as on TrueFake)."""
    def band(s: float) -> str:
        return _band_of_score(s, t_hi, t_lo)
    rb = [band(s) for s in real]
    ab = [band(s) for s in ai]
    return {
        "real": len(real), "ai": len(ai),
        "real_auto_decided": rb.count(AUTO), "real_uncertain": rb.count(UNCERTAIN), "real_likely_fine": rb.count(FINE),
        "ai_auto_decided": ab.count(AUTO), "ai_uncertain": ab.count(UNCERTAIN), "ai_likely_fine": ab.count(FINE),
    }


def fit_bands(real: list[float], ai: list[float], max_error: float = MAX_ERROR) -> dict:
    """t_hi, t_lo and the dev counts for one form. Inputs are dev scores only."""
    t_hi = fit_t_hi(real, ai, allowed_errors(len(real), max_error))
    t_lo = fit_t_lo(real, ai, allowed_errors(len(ai), max_error))
    counts = band_counts(real, ai, t_hi, t_lo)
    return {
        "t_hi": None if math.isinf(t_hi) else t_hi,
        "t_lo": None if math.isinf(t_lo) else t_lo,
        "dev": counts,
        "dev_real_flag_rate": counts["real_auto_decided"] / counts["real"] if counts["real"] else None,
        "dev_ai_clear_rate": counts["ai_likely_fine"] / counts["ai"] if counts["ai"] else None,
    }


# --- the frozen file ----------------------------------------------------------------------------

def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bands(bands: dict, path) -> str:
    """Write the bands file as canonical JSON and `<path>.sha256` beside it (sha256sum format)."""
    path = Path(path)
    data = (json.dumps(bands, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def load_bands(path=None, form: str = "original") -> dict:
    """The frozen thresholds for one form. Raises BandsError.

    The frozen file (path None, or BANDS_PATH itself) must match both its sidecar sha256 and BANDS_SHA256, the
    value pinned in this module, so editing the file and its sidecar together is refused. A file at any other
    path (a test copy, a refit in progress) is checked against its own sidecar only.

    Returns {"t_hi", "t_lo", "auto_ok", "fine_ok", "sha256", "form", "path"}. A band whose dev error is over
    MAX_ERROR (or unknown) is switched off: its images go to a person.
    """
    path = Path(path) if path is not None else BANDS_PATH
    if form not in FORMS:
        raise BandsError(f"form must be one of {', '.join(FORMS)}")
    sidecar = path.with_name(path.name + ".sha256")
    try:
        pinned = sidecar.read_text(encoding="utf-8").split()[0]
        actual = file_sha256(path)
    except (OSError, IndexError) as exc:
        raise BandsError(f"bands file or its sha256 can't be read: {exc}") from None
    if actual != pinned:
        raise BandsError(f"{path.name} does not match its sha256 ({actual} against {pinned}); "
                         "the frozen thresholds were changed")
    if path.resolve() == BANDS_PATH.resolve() and actual != BANDS_SHA256:
        raise BandsError(f"{path.name} does not match the sha256 pinned in secondlook/queue.py "
                         f"({actual} against {BANDS_SHA256}); the frozen thresholds were changed")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        entry = doc["forms"][form]
    except (ValueError, KeyError, TypeError) as exc:
        raise BandsError(f"bands file is not usable: {exc!r}") from None
    if doc.get("schema") != BANDS_SCHEMA:
        raise BandsError(f"bands file schema is {doc.get('schema')!r}, not {BANDS_SCHEMA}")

    def ok(rate) -> bool:
        return isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate <= doc.get("max_error", MAX_ERROR)

    t_hi, t_lo = entry.get("t_hi"), entry.get("t_lo")
    return {
        "t_hi": math.inf if t_hi is None else float(t_hi),
        "t_lo": -math.inf if t_lo is None else float(t_lo),
        "auto_ok": t_hi is not None and ok(entry.get("dev_real_flag_rate")),
        "fine_ok": t_lo is not None and ok(entry.get("dev_ai_clear_rate")),
        "sha256": actual, "form": form, "path": str(path),
    }


# --- banding and ranking ------------------------------------------------------------------------

def _band_of_score(score: float, t_hi: float, t_lo: float) -> str:
    hi, lo = score >= t_hi, score < t_lo
    if hi and not lo:
        return AUTO
    if lo and not hi:
        return FINE
    return UNCERTAIN


SCORE_SIG = 6   # significant figures a score or threshold is shown with in a reason


def _fixed(x: float, sig: int) -> str:
    """x in plain (not exponent) notation with `sig` significant figures, trailing zeros dropped."""
    if x == 0:
        return "0"
    decimals = max(sig - 1 - math.floor(math.log10(abs(x))), 0)
    return f"{x:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{x:.0f}"


def show_pair(score: float, threshold: float) -> tuple[str, str]:
    """A score and the threshold it is compared with, as text that keeps the comparison true: each with
    SCORE_SIG significant figures, and more when two different numbers would otherwise print the same
    (never "0.000 is below 0.000235", never "0.123457 is below 0.123457")."""
    for sig in range(SCORE_SIG, 18):
        a, b = _fixed(score, sig), _fixed(threshold, sig)
        if score == threshold or a != b:
            return a, b
    return repr(score), repr(threshold)


def band_for(answer: str, score: float | None, bands: dict) -> tuple[str, str]:
    """(band, reason) for one image from the ladder's answer and the detector score (None: no score)."""
    if answer in FLAG_ANSWERS:
        why = "a signed record says AI" if answer == "signed_ai" else "the ladder found a reason to look closer"
        return AUTO, f"Auto-decided: {why}."
    if score is None:
        return UNCERTAIN, "No detector score, so a person looks."
    t_hi = bands["t_hi"] if bands.get("auto_ok") else math.inf
    t_lo = bands["t_lo"] if bands.get("fine_ok") else -math.inf
    band = _band_of_score(score, t_hi, t_lo)
    if band == AUTO and answer == "signed_camera":
        return UNCERTAIN, ("A signed camera record and a detector score at or above the auto threshold "
                           "disagree, so a person looks.")
    if band == AUTO:
        shown, limit = show_pair(score, t_hi)
        return AUTO, f"Auto-decided: detector score {shown} is at or above {limit} (dev-fitted)."
    if band == FINE:
        shown, limit = show_pair(score, t_lo)
        return FINE, (f"Detector score {shown} is below {limit} (dev-fitted) and no concern was found. "
                      "Not proof that the image is real.")
    return UNCERTAIN, f"Detector score {_fixed(score, SCORE_SIG)} is between the dev-fitted thresholds, so a person looks."


def rank(rows: list[dict]) -> list[dict]:
    """Queue order: band (uncertain, auto_decided, likely_fine), then the highest detector score first,
    images without a score last in their band, then the file path. Adds `rank` from 1."""
    order = {b: i for i, b in enumerate(BAND_ORDER)}
    ranked = sorted(rows, key=lambda r: (order[r["band"]], r["detector_score"] is None,
                                         -(r["detector_score"] or 0.0), r["file"]))
    return [{**r, "rank": i} for i, r in enumerate(ranked, start=1)]


def detector_score(result) -> float | None:
    """The classifier rung's score from a ladder Result, or None when the rung gave none."""
    for e in result.evidence:
        if e.rung == "classifier" and isinstance(e.raw.get("score"), (int, float)) \
                and not isinstance(e.raw.get("score"), bool):
            return float(e.raw["score"])
    return None


# --- one file -----------------------------------------------------------------------------------

def _process(task) -> dict:
    """Check one file, write its receipt, band it. Returns a plain row; never raises."""
    idx, rel, real, out_dir, pdf, bands, run_time = task
    try:
        t0 = time.perf_counter()
        result = ladder.run(real, rungs=batch._W["rungs"])
        seconds = round(time.perf_counter() - t0, 4)
        score = detector_score(result)
        band, reason = band_for(result.answer, score, bands)
        row = {"file": rel, "sha256": batch._sha256(real), "answer": result.answer, "detector_score": score,
               "band": band, "reason": reason, "version": result.version, "seconds": seconds}
    except Exception as exc:  # the ladder does not raise; this is the backstop
        return {"idx": idx, "file": rel, "error": f"the check failed on this file ({type(exc).__name__})"}
    row.update(idx=idx, receipt_json=None, receipt_sha256=None, receipt_pdf=None, receipt_error=None)
    base = f"{idx:04d}-{batch._safe_stem(rel)}"
    try:
        rc = receipt_writer.build(result, real, created_at=run_time)
        target = receipt_writer.write_json(rc, Path(out_dir) / "receipts" / f"{base}.json")
        row["receipt_json"] = f"receipts/{base}.json"
        row["receipt_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        if pdf:
            receipt_writer.write_pdf(rc, Path(out_dir) / "receipts" / f"{base}.pdf")
            row["receipt_pdf"] = f"receipts/{base}.pdf"
    except Exception as exc:  # the band stands; the missing receipt is stated in the row
        row["receipt_error"] = type(exc).__name__
    return row


RUN_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"   # the receipts' created_at format


def check_run_time(run_time) -> str | None:
    """`run_time` as given if it is None or exactly a receipt timestamp (UTC, whole seconds), else ValueError."""
    if run_time is None:
        return None
    try:
        if isinstance(run_time, str) and datetime.strptime(run_time, RUN_TIME_FORMAT).strftime(RUN_TIME_FORMAT) == run_time:
            return run_time
    except ValueError:
        pass
    raise ValueError("run_time must look like 2026-11-02T00:00:00Z (UTC, whole seconds)")


# --- the run ------------------------------------------------------------------------------------

COLUMNS = ("rank", "band", "file", "detector_score", "answer", "reason", "sha256", "receipt_json",
           "receipt_sha256", "receipt_pdf", "receipt_error")


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([batch._cell("" if r.get(c) is None else r.get(c)) for c in COLUMNS])
    return buf.getvalue()


def run_queue(folder, out_dir, *, form: str = "original", jobs: int | None = None, pdf: bool = False,
              rung_names=rung_registry.DEFAULT_RUNGS, bands_path=None,
              progress: Callable[[int, int, str], None] | None = None,
              allow_many_workers: bool = False, run_time: str | None = None) -> dict:
    """Check every image in `folder`, write receipts and the ranked queue into `out_dir`; return queue.json's
    content plus a "run" key. Raises BandsError before anything is checked if the frozen file is not usable.
    With more than one worker the caller must guard this call with `if __name__ == "__main__":`.
    `run_time` (see the module note) pins the time every receipt records; None records the real time."""
    batch._refuse_if_reimported_by_a_worker()
    run_time = check_run_time(run_time)
    bands = load_bands(bands_path, form)
    if jobs is not None and int(jobs) < 1:
        raise ValueError("jobs must be 1 or more")
    if jobs is not None and int(jobs) > batch.max_jobs() and not allow_many_workers:
        raise ValueError(f"jobs={jobs} is above the limit of {batch.max_jobs()} on this machine. {batch.MEMORY_NOTE}")
    t0 = time.perf_counter()
    root = Path(folder)
    if not root.is_dir():
        raise NotADirectoryError(f"{folder} is not a folder")
    out = Path(out_dir).resolve()
    (out / "receipts").mkdir(parents=True, exist_ok=True)
    for old in (out / "receipts").iterdir():  # our own earlier output only
        if old.is_file() and batch.RECEIPT_NAME.match(old.name):
            old.unlink()

    found, skipped, ignored = batch.discover(root, exclude=out)
    n = len(found)
    workers = max(1, min(batch.default_jobs() if jobs is None else int(jobs), n or 1))
    tasks = [(i, rel, real, str(out), pdf, bands, run_time) for i, (rel, real) in enumerate(found)]
    rows: list[dict | None] = [None] * n
    done = 0

    def finish(i: int, row: dict) -> None:
        nonlocal done
        rows[i] = row
        done += 1
        if progress:
            progress(done, n, found[i][0])

    if workers == 1:
        batch._init_worker(rung_names)
        for t in tasks:
            finish(t[0], _process(t))
    else:
        marker = batch._guard_marker(os.getpid())
        marker.unlink(missing_ok=True)
        with cf.ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                                    initializer=batch._init_worker, initargs=(rung_names,)) as pool:
            futures = {pool.submit(_process, t): t[0] for t in tasks}
            for fut in cf.as_completed(futures):
                i = futures[fut]
                try:
                    finish(i, fut.result())
                except Exception as exc:  # a worker died: that file is failed, not guessed
                    finish(i, {"idx": i, "file": found[i][0],
                               "error": f"the check stopped before it finished ({type(exc).__name__})"})
        if marker.exists():
            marker.unlink(missing_ok=True)
            raise RuntimeError(batch.GUARD_MESSAGE)

    ok = [r for r in rows if "error" not in r]
    failed = [{"file": r["file"], "reason": r["error"]} for r in rows if "error" in r]
    queue = rank([{k: r[k] for k in COLUMNS if k != "rank"} for r in ok])
    counts = {"images": n, "checked": len(ok), "skipped": len(skipped), "failed": len(failed)}
    for b in BAND_ORDER:
        counts[b] = sum(1 for r in queue if r["band"] == b)
    doc = {
        "schema": SCHEMA,
        "folder": root.resolve().name,
        "version": ok[0]["version"] if ok else ladder._version(),
        "rungs": [name for name, _ in rung_registry.load(rung_names)],
        "bands": {"file": Path(bands["path"]).name, "sha256": bands["sha256"], "form": form,
                  "t_hi": None if math.isinf(bands["t_hi"]) else bands["t_hi"],
                  "t_lo": None if math.isinf(bands["t_lo"]) else bands["t_lo"],
                  "auto_decided_on": bands["auto_ok"], "likely_fine_on": bands["fine_ok"],
                  "labels": BAND_LABELS,
                  "note": ("Routing only. The ladder's answers and the receipts are unchanged; the 29 Oct "
                           "classifier gate stays FAIL.")},
        "run_time": run_time,
        "counts": counts,
        "queue": queue,
        "skipped": skipped,
        "failed": failed,
        "ignored": ignored,
    }
    batch._write(out / "queue.json", json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    batch._write(out / "queue.csv", _csv(queue))
    run = {"jobs": workers, "run_time": run_time, "elapsed_seconds": round(time.perf_counter() - t0, 3),
           "seconds_per_image": {r["file"]: r["seconds"] for r in ok}}
    batch._write(out / "run.json", json.dumps(run, indent=2, ensure_ascii=False) + "\n")
    return {**doc, "run": run}
