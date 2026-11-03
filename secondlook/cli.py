"""secondlook check <file> [--caption TEXT] [--claimed-date D --claimed-place P --claimed-device X] [--receipt DIR]: print the Result as JSON,
with its plain-language explanation; with --receipt, also write the evidence receipt (JSON, its sha256 and a PDF).
secondlook check --plain <file> [same options] : print only the plain-language explanation.
secondlook batch FOLDER --out DIR [--jobs N [--allow-many-workers]] [--no-pdf] : check a folder, write receipts and a summary.
secondlook queue FOLDER --out DIR [--form original|sq512] [--jobs N [--allow-many-workers]] [--pdf] : check a folder and write
a ranked desk triage queue (CSV and JSON) with a receipt per image; bands from the frozen secondlook/queue_bands.json.
secondlook mark <in> <out> : add Second Look's own invisible watermark (demo), then read it back.
secondlook investigate <file> [--caption TEXT] : print the Glimmer investigator's DeskNote as JSON."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from . import extract
from .explain import as_text, explain
from .ladder import run
from .types import Context


def _date(text: str) -> str:
    parsed = extract.parse_date(text)
    if parsed is None:
        raise argparse.ArgumentTypeError("use an ISO date or time, e.g. 2026-10-01 or 2026-10-01T09:30+10:00")
    return parsed


def context_from(caption: str | None, date: str | None, place: str | None, device: str | None) -> Context | None:
    """The ladder's Context from what was given, or None when nothing was (the ladder then runs as before).
    Typed facts win field by field over what is read from the caption."""
    typed = extract.typed(date, place, device) if (date or place or device) else None
    read = extract.rule_based(caption) if caption else None
    if typed is None and read is None:
        return None
    return Context(caption=caption, facts=extract.merge(typed, read))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="secondlook", description="Second Look (in development)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check one image file")
    c.add_argument("file")
    c.add_argument("--caption", help="the caption that came with the picture; its date, place and device are compared with the file's own")
    c.add_argument("--claimed-date", type=_date, help="the date the picture is said to be from (ISO)")
    c.add_argument("--claimed-place", help="where the picture is said to be from, e.g. 'Paris, France'")
    c.add_argument("--claimed-device", help="what the picture is said to be taken on, e.g. 'iPhone 15 Pro'")
    c.add_argument("--plain", action="store_true", help="print only the plain-language explanation")
    c.add_argument("--receipt", metavar="DIR", help="also write the evidence receipt (JSON, its .sha256 and PDF) into DIR")
    b = sub.add_parser("batch", help="check every image in a folder; write a receipt per file and a summary")
    b.add_argument("folder")
    b.add_argument("--out", required=True, metavar="DIR", help="where the receipts and summary files go")
    b.add_argument("--jobs", type=int, default=None, metavar="N",
                   help="worker processes (default: 2; at most min(4, half the CPU cores) unless "
                        "--allow-many-workers; see docs/PERFORMANCE.md)")
    b.add_argument("--allow-many-workers", action="store_true",
                   help="let --jobs go above that limit. Each worker holds its own copy of the classifier, "
                        "about 0.9 GiB, so check the memory first")
    b.add_argument("--no-pdf", action="store_true", help="write the JSON receipts only")
    q = sub.add_parser("queue", help="check every image in a folder; write a ranked triage queue and a receipt per image")
    q.add_argument("folder")
    q.add_argument("--out", required=True, metavar="DIR", help="where queue.csv, queue.json and the receipts go")
    q.add_argument("--form", choices=("original", "sq512"), default="original",
                   help="which frozen thresholds to use: original (files as given, the default) or sq512 "
                        "(512 x 512 centre crops without metadata)")
    q.add_argument("--jobs", type=int, default=None, metavar="N", help="worker processes (as for batch)")
    q.add_argument("--allow-many-workers", action="store_true", help="let --jobs go above the usual limit")
    q.add_argument("--pdf", action="store_true", help="also write a PDF beside each JSON receipt")
    m = sub.add_parser("mark", help="add Second Look's own invisible watermark to a copy (demo)")
    m.add_argument("input")
    m.add_argument("output")
    m.add_argument("--force", action="store_true", help="replace output if it exists (never the input)")
    i = sub.add_parser("investigate", help="Muse Glimmer, local, looks into one image with the checks as tools")
    i.add_argument("file")
    i.add_argument("--caption", help="the caption posted with the image")
    a = p.parse_args(argv)
    if a.cmd == "batch":
        return _batch(a)
    if a.cmd == "queue":
        return _queue(a)
    if a.cmd == "mark":
        return _mark(a)
    if a.cmd == "investigate":
        from .investigator import investigate
        json.dump(dataclasses.asdict(investigate(a.file, a.caption)), sys.stdout, indent=2, ensure_ascii=False,
                  default=str)
        sys.stdout.write("\n")
        return 0
    context = context_from(a.caption, a.claimed_date, a.claimed_place, a.claimed_device)
    result = run(a.file) if context is None else run(a.file, context=context)
    ex = explain(result)
    if a.plain:
        sys.stdout.write(as_text(ex) + "\n")
    else:
        json.dump(dataclasses.asdict(result) | {"explanation": ex.as_dict()}, sys.stdout, indent=2,
                  ensure_ascii=False, default=str)
        sys.stdout.write("\n")
    # The receipt records the caption the check ran with (docs/RECEIPT.md, `inputs`), as the web routes do.
    inputs = {"caption": a.caption} if context is not None and context.caption else None
    return write_receipt(result, a.file, a.receipt, inputs) if a.receipt else 0


def write_receipt(result, file: str, out_dir: str, inputs: dict | None = None) -> int:
    """Write the receipt files; the answer is already printed. Exit 0 ok, 2 no receipt possible, 3 no PDF.
    `inputs` is what else the user gave (the caption), recorded as given."""
    from . import receipt

    try:
        rc = receipt.build(result, file, inputs=inputs)
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        written = [receipt.write_json(rc, folder)]
        written.append(written[0].with_name(written[0].name + ".sha256"))
    except OSError as exc:  # the file or the folder cannot be read or written: there is nothing to hash
        print(f"secondlook: no receipt written: {exc}", file=sys.stderr)
        return 2
    code = 0
    try:
        written.append(receipt.write_pdf(rc, folder))
    except ImportError as exc:  # the JSON receipt is complete without the PDF
        print(f"secondlook: no PDF written: {exc}", file=sys.stderr)
        code = 3
    for path in written:
        print(f"receipt: {path}", file=sys.stderr)
    return code


def _batch(a) -> int:
    from . import batch

    if a.jobs is not None and a.jobs < 1:
        print("secondlook batch: --jobs must be 1 or more", file=sys.stderr)
        return 2
    limit = batch.max_jobs()
    if a.jobs is not None and a.jobs > limit:
        if not a.allow_many_workers:
            print(f"secondlook batch: --jobs {a.jobs} is above the limit of {limit} on this machine "
                  f"(the smaller of 4 and half the CPU cores). {batch.MEMORY_NOTE} "
                  "Add --allow-many-workers to go higher.", file=sys.stderr)
            return 2
        print(f"secondlook batch: {a.jobs} workers, above the usual limit of {limit}. {batch.MEMORY_NOTE}",
              file=sys.stderr)

    def progress(done: int, total: int, name: str) -> None:
        print(f"[{done}/{total}] {name}", file=sys.stderr)

    try:
        s = batch.run_batch(a.folder, a.out, jobs=a.jobs, pdf=not a.no_pdf, progress=progress,
                            allow_many_workers=a.allow_many_workers)
    except NotADirectoryError as exc:
        print(f"secondlook batch: {exc}", file=sys.stderr)
        return 2
    c = s["counts"]
    print(f"Checked {c['checked']} of {c['images']} images in {s['run']['elapsed_seconds']} s "
          f"({s['run']['jobs']} worker(s)); skipped {c['skipped']}, failed {c['failed']}.")
    for key, label in batch.ANSWER_LABELS.items():
        print(f"  {label}: {c[key]}")
    print(f"  Near-duplicate groups (information only): {c['near_duplicate_groups']}")
    r = s["receipts"]
    if not a.no_pdf:
        print(f"PDF receipts: {r['pdf_written']} written, {r['pdf_failed']} failed"
              + (" (see receipt_error in summary.json)" if r["pdf_failed"] else "") + ".")
    print(f"Summary: {a.out}/summary.html, summary.json, summary.csv; receipts in {a.out}/receipts/")
    return 0


def _queue(a) -> int:
    from . import batch, queue

    if a.jobs is not None and a.jobs < 1:
        print("secondlook queue: --jobs must be 1 or more", file=sys.stderr)
        return 2
    if a.jobs is not None and a.jobs > batch.max_jobs() and not a.allow_many_workers:
        print(f"secondlook queue: --jobs {a.jobs} is above the limit of {batch.max_jobs()} on this machine. "
              f"{batch.MEMORY_NOTE} Add --allow-many-workers to go higher.", file=sys.stderr)
        return 2

    def progress(done: int, total: int, name: str) -> None:
        print(f"[{done}/{total}] {name}", file=sys.stderr)

    try:
        q = queue.run_queue(a.folder, a.out, form=a.form, jobs=a.jobs, pdf=a.pdf, progress=progress,
                            allow_many_workers=a.allow_many_workers)
    except (NotADirectoryError, queue.BandsError) as exc:
        print(f"secondlook queue: {exc}", file=sys.stderr)
        return 2
    c, b = q["counts"], q["bands"]
    print(f"Queued {c['checked']} of {c['images']} images in {q['run']['elapsed_seconds']} s "
          f"({q['run']['jobs']} worker(s)); skipped {c['skipped']}, failed {c['failed']}.")
    for band in queue.BAND_ORDER:
        print(f"  {queue.BAND_LABELS[band]}: {c[band]}")
    print(f"Bands: {b['file']} sha256 {b['sha256']} ({b['form']}). {b['note']}")
    print(f"Queue: {a.out}/queue.csv, queue.json; receipts in {a.out}/receipts/")
    return 0


def _mark(a) -> int:
    from .rungs import watermark

    try:
        out = watermark.mark(a.input, a.output, force=a.force)
    except Exception as exc:
        sys.stderr.write(f"secondlook mark: {exc}\n")
        return 1
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if out["present"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
