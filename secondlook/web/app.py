"""Second Look local web page (S5, DAT-850): FastAPI plus one static HTML page.

Run:  python -m secondlook.web            real ladder, http://127.0.0.1:8765
      python -m secondlook.web --stub     canned stub answers (demo only)
      python -m secondlook.web --samples DEMO-SAMPLES/files
                                          adds the "Try a sample" gallery (N1's nine files)

The claim box (/api/claim) and voice notes (/api/voice) live in desk.py (W10); /api/health
lists which of them can run on this computer, and the page shows only those panels.

The engine is the real `secondlook.ladder.run()` on whichever rungs are
installed. The stub is used only when asked for (`--stub` or SECONDLOOK_STUB=1),
and every stub answer is marked as a demo inside the result itself.

No external calls. The page loads nothing from the network, a strict
Content-Security-Policy enforces that, and the server binds to 127.0.0.1 only.
/api/check takes the image and an optional `caption` form field; with a caption,
the context rung compares its date, place and device with the file's own facts.
Uploads over the size limit are refused from the Content-Length header, before
the body is read. Uploads go to a private temp dir that is deleted as soon as
the check returns.

Receipts (R1, DAT-870): `POST /api/receipt` returns the PDF, `POST /api/check?receipt=1` adds the receipt
JSON to the answer. The receipt records the file's base name only, never the temp path.
W11 stage 2: `POST /api/receipt?format=json` returns the same receipt as the canonical JSON file that
`secondlook check --receipt` writes, the optional `caption` field checks the file with its caption as
/api/check does, and `POST /api/samples/{id}/receipt` does the same for a sample. Each re-runs the check:
nothing from an earlier check is kept, so the receipt is a fresh check of the same file. The
X-Secondlook-Answer header carries the receipt's answer, so the page can tell if it differs from the screen.
A receipt records the caption it was checked with in `inputs.caption`.
Claim words Muse Glimmer picked (DAT-850): /api/investigate keeps a short, bounded record of each deep check it
ran whose caption lookup found a published fact-check (secondlook.receipt.DeepRuns: a run id, the file's
sha256, the caption and the picked clauses) and returns the id as `receipt_run`. Both receipt routes take an
optional `deep_run` form field, that id; never the words. The receipt takes the clauses from the record after
checking the file's sha256 and the caption, checks with Context(caption, claims=picks), runs the same S6 lookup
and records the picks, their source and the lookup's status in `inputs`. An unknown, expired or mismatched id
gives a receipt without picks and a plain reason in X-Secondlook-Picks-Reason. The answer stays the ladder's
(secondlook.receipt.with_picked_claims).

Recorded deep checks (DAT-850): `POST /api/recorded` (the file, and the caption if any) and
`GET /api/samples/{id}/recorded` return a saved Muse Glimmer run of the same bytes (sha256) from
samples/recorded/, under its "Recorded run, not live" label, or 204 with no body when there is none (not a 404,
which the browser logs as a red console error on every such drop; judge re-run 30 Oct, A249). An unknown sample
id is still a 404. The page asks only when no Glimmer server answers; a recording never changes the answer. See
secondlook/web/recorded.py.

POST /api/batch takes up to 20 images in one request (100 MB in all, 25 MB each),
checks each on its own, and lists near-duplicate copies for information only. Each checked file
carries `seconds`, the time its check took; `?receipt=1` adds its receipt (the dict, and
`receipt_json`, the canonical JSON text `secondlook check --receipt` writes, with its sha256).
GET /batch is the page for it (DAT-850): a folder or many files in, one row and one receipt per file.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from secondlook import batch, ladder
from secondlook import receipt as receipts
from secondlook import rungs as rung_registry
from secondlook.explain import explain
from secondlook.types import Context, Result
from secondlook.web import desk
from secondlook.web import recorded as recordings
from secondlook.web import samples as sample_catalogue

log = logging.getLogger("secondlook.web")

STATIC = Path(__file__).parent / "static"
MAX_BYTES = 25 * 1024 * 1024
# Room for the multipart boundary and part headers around a file of MAX_BYTES.
MAX_REQUEST = MAX_BYTES + 64 * 1024
MAX_NAME = 80
MAX_CAPTION = 2000
# POST /api/batch: several files in one request, each at most MAX_BYTES, and these limits on the whole.
MAX_BATCH_FILES = 20
MAX_BATCH_BYTES = 100 * 1024 * 1024  # all the files together
MAX_BATCH_REQUEST = MAX_BATCH_BYTES + MAX_BATCH_FILES * 64 * 1024
# The receipt downloads (W11 stage 2): the PDF, and the canonical JSON that `secondlook check --receipt` writes.
RECEIPT_FORMATS = {"pdf": "application/pdf", "json": "application/json"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".avif", ".gif", ".tif", ".tiff"}

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# POST paths whose body size is checked from Content-Length before any of it is read: (limit, message).
REQUEST_LIMITS = {
    "/api/check": (MAX_REQUEST, "That file is over 25 MB."),
    "/api/voice": (desk.VOICE_MAX_REQUEST, f"That file is over {desk.VOICE_MB} MB."),
    "/api/claim": (desk.CLAIM_MAX_REQUEST, "That claim is too long. Paste one claim at a time."),
    "/api/batch": (MAX_BATCH_REQUEST, "That batch is over 100 MB in all."),
    "/api/investigate": (MAX_REQUEST, "That file is over 25 MB."),
    "/api/receipt": (MAX_REQUEST, "That file is over 25 MB."),
    "/api/recorded": (MAX_REQUEST, "That file is over 25 MB."),
}
SECURITY_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def safe_name(filename: str | None) -> str:
    """A base name safe to write in a temp dir, keeping the image suffix.

    The suffix is taken from the original name before the length cut, so a
    long name never loses its extension (review F1: an 81-character name was
    refused as "not an image").
    """
    base = Path((filename or "").replace("\\", "/")).name
    stem, suffix = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip(".") or "upload"
    suffix = re.sub(r"[^A-Za-z0-9.]", "", suffix)[:8]
    return stem[: MAX_NAME - len(suffix)] + suffix


def given_name(filename: str | None) -> str:
    """The base name the user gave the file, unchanged, as `secondlook check` reads it from its path.

    Only for what is recorded (the receipt's file name); the file is written to disk under safe_name().
    """
    return Path((filename or "").replace("\\", "/")).name or safe_name(filename)


def _stub_requested() -> bool:
    return os.environ.get("SECONDLOOK_STUB", "").strip().lower() in ("1", "true", "yes")


class _Guard:
    """Outer ASGI layer: security headers on every response, and the size limit
    applied from Content-Length before any of the body is read."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message = {**message, "headers": list(message.get("headers", []))
                           + [(k, v) for k, v in SECURITY_HEADERS if k not in names]}
            await send(message)

        limit = REQUEST_LIMITS.get(scope["path"]) if scope["method"] == "POST" else None
        if limit:
            length = dict(scope.get("headers", [])).get(b"content-length")
            if length is None:
                return await _plain(send_with_headers, 411, "Please send the file with a Content-Length.")
            try:
                n = int(length)
            except ValueError:
                return await _plain(send_with_headers, 400, "Bad Content-Length.")
            if n > limit[0]:
                return await _plain(send_with_headers, 413, limit[1])
        return await self.inner(scope, receive, send_with_headers)

    def __getattr__(self, name):  # tests and tools reach the FastAPI app through this
        return getattr(self.inner, name)


async def _plain(send, status: int, detail: str) -> None:
    resp = JSONResponse({"detail": detail}, status_code=status, headers={"connection": "close"})
    await send({"type": "http.response.start", "status": status, "headers": resp.raw_headers})
    await send({"type": "http.response.body", "body": resp.body})


def _thumbnail(path: Path) -> bytes | None:
    """A small JPEG for the gallery, with no metadata. None when Pillow is not installed."""
    try:
        from PIL import Image
    except ImportError:
        return None
    import io

    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((480, 480))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82)
            return buf.getvalue()
    except Exception:  # a thumbnail is a nicety; the full file is served instead
        log.exception("thumbnail failed for %s", path.name)
        return None


def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    """The safe name and bytes of an uploaded image, or an HTTPException."""
    name = safe_name(file.filename)
    if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise HTTPException(415, "Please drop an image file (JPEG, PNG, WebP, HEIC, AVIF, GIF or TIFF).")
    data = file.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "That file is over 25 MB.")
    if not data:
        raise HTTPException(400, "That file is empty.")
    return name, data


def create_app(stub: bool | None = None, rungs: list | None = None, samples_dir: str | None = None,
               investigator=None, recorded_dir: str | None = None) -> _Guard:
    """Build the app. stub=None reads SECONDLOOK_STUB; rungs=None loads the installed rungs once;
    samples_dir=None reads SECONDLOOK_SAMPLES (no gallery when neither is set); investigator:
    (path, caption) -> DeskNote, None uses the Glimmer investigator on these rungs; recorded_dir=None
    reads SECONDLOOK_RECORDED, else samples/recorded/ (the stub offers no recordings)."""
    use_stub = _stub_requested() if stub is None else stub
    version = _git_sha()
    if use_stub:
        from secondlook.web import stub as stub_engine

        engine_name, rung_names = "stub", []

        def engine(path: str, context: Context | None = None) -> ladder.Traced:
            return stub_engine.check(path), [], [], []
    else:
        loaded = rung_registry.load() if rungs is None else rungs
        # The context rungs join only a check that has a caption, as in ladder.run.
        with_context = rung_registry.load(rung_registry.CONTEXT_RUNGS) if rungs is None else []
        engine_name, rung_names = "ladder", [name for name, _ in loaded + with_context]

        def engine(path: str, context: Context | None = None) -> ladder.Traced:
            """The result, the rungs that ran, the rungs that raised and why (the ladder's own split)."""
            return ladder.run_traced(path, rungs=loaded + with_context if context else loaded, context=context)

    # The deep checks this server ran itself, so a receipt can keep their picked claim words (DAT-850).
    deep_runs = receipts.DeepRuns()

    gallery = sample_catalogue.available(samples_dir or sample_catalogue.samples_dir_from_env())
    # Recorded deep checks, keyed by the recorded file's sha256. None on the stub: its answers are canned,
    # and a real recording beside them would read as a check of the same kind.
    recorded = {} if use_stub else recordings.load(recorded_dir or os.environ.get("SECONDLOOK_RECORDED") or None)
    thumbs: dict[str, bytes | None] = {}

    def to_body(result: Result, ran: list[str], failed: list[str], why: list[str | None]) -> dict:
        body = dataclasses.asdict(result)
        if body.get("version") in (None, "", "stub", "unknown"):
            body["version"] = version
        body["engine"] = engine_name
        # The checks this answer ran, from the ladder's own split: a rung that raised (the watermark rung
        # without its weights) is not counted, and is named apart so the page says it could not run. The
        # context rungs join only a check that had a caption (W11 design pass, finding 5).
        body["rungs"] = list(ran)
        body["rungs_failed"] = list(failed)
        # One reason per failed rung, in the same order, from the ladder's fixed list; null when the error's
        # type is not on it, and the page then says the cause is in the server log (W11 stage 2, S-b).
        body["rungs_failed_why"] = list(why)
        # Which cards the ladder's own rule counts as concerns, so the page words its headline by count.
        body["concern_cards"] = [i for i, e in enumerate(result.evidence) if ladder._is_concern(e)]
        # Fail closed: the result says "demo" unless the real ladder ran and no card is a stub.
        body["demo"] = engine_name != "ladder" or any(e.get("raw", {}).get("stub") for e in body["evidence"])
        return body

    def respond(checked: ladder.Traced, **extra) -> JSONResponse:
        result = checked[0]
        body = to_body(*checked)
        body.update(extra)
        # Written by Second Look's fixed templates from the result's structured fields; no model.
        body["explanation"] = explain(result).as_dict()
        return JSONResponse(body)

    def run_engine(path: str, name: str, context: Context | None = None) -> ladder.Traced:
        try:
            return engine(path, context)
        except Exception:  # the ladder should never raise; the page still needs an answer
            log.exception("check failed on %s", name)
            raise HTTPException(500, "The check failed on this file. Nothing was kept.")

    api = FastAPI(title="Second Look (in development)", docs_url=None, redoc_url=None, openapi_url=None)
    api.mount("/static", StaticFiles(directory=STATIC), name="static")

    @api.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @api.get("/batch")
    def batch_page() -> FileResponse:
        return FileResponse(STATIC / "batch.html", media_type="text/html")

    # Browsers ask for /favicon.ico on their own; the pages also link it (A249: it was a 404 in the console).
    @api.get("/favicon.ico")
    def favicon() -> FileResponse:
        return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon")

    @api.get("/api/health")
    def health() -> dict:
        caps, details = desk.capabilities(engine_name, rung_names)
        caps["batch"] = True  # POST /api/batch is always present in this build (B1, DAT-871)
        caps["recorded"] = bool(recorded)
        details["recorded"] = {"count": len(recorded)}
        return {"ok": True, "engine": engine_name, "rungs": rung_names, "version": version,
                "capabilities": caps, "details": details, "samples": len(gallery)}

    desk.register(api, version, safe_name)

    @api.get("/api/samples")
    def list_samples() -> dict:
        return {"samples": [sample_catalogue.public(s) for s, _ in gallery.values()]}

    def sample(sample_id: str):
        if sample_id not in gallery:  # ids come only from the fixed catalogue, never from a path
            raise HTTPException(404, "No such sample.")
        return gallery[sample_id]

    @api.get("/samples/{sample_id}/image")
    def sample_image(sample_id: str) -> Response:
        _, path = sample(sample_id)
        if sample_id not in thumbs:
            thumbs[sample_id] = _thumbnail(path)
        if thumbs[sample_id] is None:
            return FileResponse(path, media_type="image/jpeg")
        return Response(thumbs[sample_id], media_type="image/jpeg")

    @api.post("/api/samples/{sample_id}/check")
    def check_sample(sample_id: str) -> JSONResponse:
        s, path = sample(sample_id)
        # The original file is read in place: the rungs only read, and nothing is uploaded.
        return respond(run_engine(str(path), s.file), sample=sample_catalogue.public(s))

    def recording(data_sha256: str, caption: str | None = None) -> Response:
        """The recorded run of exactly these bytes and this caption (none, or the recording's own, matched by the
        sha256 of the trimmed text), or 204 No Content: no recording is an ordinary answer, not an error."""
        rec = recorded.get(data_sha256)
        if rec is None or not recordings.matches(rec, caption):
            return Response(status_code=204)
        return JSONResponse(recordings.public(rec))

    @api.get("/api/samples/{sample_id}/recorded")
    def sample_recorded(sample_id: str) -> Response:
        _, path = sample(sample_id)
        return recording(sample_catalogue._sha256(path))

    @api.post("/api/recorded")
    def recorded_for_upload(file: UploadFile = File(...), caption: str | None = Form(None)) -> Response:
        """The recorded run whose file has this upload's sha256. Nothing is written to disk."""
        _, data = _read_upload(file)
        return recording(hashlib.sha256(data).hexdigest(), caption)

    def receipt_format(fmt: str) -> str:
        if fmt not in RECEIPT_FORMATS:
            raise HTTPException(400, "A receipt comes as format=pdf or format=json.")
        return fmt

    def make_receipt(result: Result, demo: bool, path: Path, tmp: str, name: str, fmt: str,
                     inputs: dict | None = None, not_added: str | None = None) -> Response:
        """The receipt of this check as a download: the PDF, or the canonical JSON the CLI writes."""
        try:
            rc = receipts.build(result, path, inputs=inputs or {})
            rc["demo"] = demo
            if fmt == "pdf":
                data = receipts.write_pdf(rc, Path(tmp) / "receipt.pdf").read_bytes()
            else:
                data = receipts.canonical_json(rc)
        except Exception:
            log.exception("receipt failed on %s", name)
            raise HTTPException(500, "The check ran but the receipt failed. Nothing was kept.")
        headers = {"content-disposition": f'attachment; filename="{receipts.attachment_name(rc, "." + fmt)}"',
                   "x-secondlook-answer": str(rc["answer"])}
        if not_added:
            # The deep check's picked claim words were asked for and are not in this receipt: why, in plain words.
            headers["x-secondlook-picks"] = "not-added"
            headers["x-secondlook-picks-reason"] = not_added
        elif receipts.PICKED_KEY in rc["inputs"]:
            headers["x-secondlook-picks"] = "added"
        return Response(data, media_type=RECEIPT_FORMATS[fmt], headers=headers)

    def receipt_caption(caption: str | None) -> str | None:
        caption = (caption or "").strip() or None
        if caption and len(caption) > MAX_CAPTION:
            raise HTTPException(400, f"That caption is over {MAX_CAPTION:,} characters.")
        return caption

    def check_for_receipt(path: Path, name: str, caption: str | None, deep_run: str | None
                          ) -> tuple[ladder.Traced, dict, str | None]:
        """The check a receipt records: with the caption's context and the claim clauses of the deep check
        `deep_run` names, taken from this server's own record (never from the client) after checking the
        file's sha256 and the caption; then the same S6 lookup the investigator ran on them. Returns the
        traced check, the receipt's `inputs`, and the plain reason when picks were asked for but not added.
        With no caption and no deep_run it is the plain check, as before."""
        sha = hashlib.sha256(path.read_bytes()).hexdigest() if deep_run else ""
        picked, not_added = deep_runs.picks(deep_run, sha, caption)
        if picked:
            try:  # the record holds only whole clauses of its caption, which is this caption; checked again
                picked = receipts.picked_claims(caption, picked)
            except receipts.PickedRefused:
                picked, not_added = [], receipts.NOT_ADDED["caption"]
        context = Context(caption=caption, claims=list(picked)) if caption else None
        result, ran, failed, why = run_engine(str(path), name, context)
        if result.version in (None, "", "stub", "unknown"):
            result = dataclasses.replace(result, version=version)
        try:
            result, lookup = receipts.with_picked_claims(result, picked)
        except receipts.AnswerWouldChange:
            log.error("picked claim cards would change the answer on %s; no receipt made", name)
            raise HTTPException(500, "Adding the picked claim words would change the answer, so no receipt was made.")
        inputs = {"caption": caption} if caption else {}
        inputs.update(receipts.picked_input(picked, lookup))
        return (result, ran, failed, why), inputs, not_added

    @api.post("/api/samples/{sample_id}/receipt")
    def sample_receipt(sample_id: str, fmt: str = Query("pdf", alias="format"), caption: str | None = Form(None),
                       deep_run: str | None = Form(None)) -> Response:
        """The receipt for a sample, checked in place as /api/samples/{id}/check checks it (with a caption and a
        deep check's picked claim clauses, if sent)."""
        fmt = receipt_format(fmt)
        caption = receipt_caption(caption)
        s, path = sample(sample_id)
        checked, inputs, not_added = check_for_receipt(path, s.file, caption, deep_run)
        with tempfile.TemporaryDirectory(prefix="secondlook-") as tmp:
            return make_receipt(checked[0], to_body(*checked)["demo"], path, tmp, s.file, fmt, inputs, not_added)

    def run_upload(file: UploadFile, caption: str | None = None, receipt_as: str | None = None,
                   with_receipt: bool = False, deep_run: str | None = None):
        """Check one upload. Returns (body, receipt dict or None, receipt download or None); the file is
        deleted on return. receipt_as "pdf" or "json" makes the download, with the deep check's picked claim
        clauses when deep_run names one on record for this file and caption."""
        caption = receipt_caption(caption)
        name, data = _read_upload(file)

        with tempfile.TemporaryDirectory(prefix="secondlook-") as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            checked, inputs, not_added = check_for_receipt(path, name, caption, deep_run if receipt_as else None)
            result = checked[0]
            body = to_body(*checked)
            # Written by Second Look's fixed templates from the result's structured fields; no model.
            body["explanation"] = explain(result).as_dict()
            rc = download = None
            if receipt_as:
                download = make_receipt(result, body["demo"], path, tmp, name, receipt_as, inputs, not_added)
            elif with_receipt:
                try:
                    rc = receipts.build(result, path, inputs=inputs)
                    rc["demo"] = body["demo"]
                except Exception:
                    log.exception("receipt failed on %s", name)
                    raise HTTPException(500, "The check ran but the receipt failed. Nothing was kept.")
        return body, rc, download

    # Plain defs: FastAPI runs them in a worker thread, so a slow rung does not block /api/health.
    @api.post("/api/check")
    def check(file: UploadFile = File(...), caption: str | None = Form(None), receipt: bool = False) -> JSONResponse:
        """The result as JSON; `?receipt=1` adds the receipt (schema second-look-receipt/1) under "receipt"."""
        body, rc, _ = run_upload(file, caption, with_receipt=receipt)
        if rc is not None:
            body["receipt"] = rc
        return JSONResponse(body)

    @api.post("/api/receipt")
    def receipt_download(file: UploadFile = File(...), caption: str | None = Form(None),
                         deep_run: str | None = Form(None), fmt: str = Query("pdf", alias="format")) -> Response:
        """Check the upload (with its caption, if given) and return the evidence receipt as a PDF, or as the
        canonical JSON file with format=json. `deep_run`, the id /api/investigate returned, adds the claim clauses
        Muse Glimmer picked in that deep check, from this server's own record."""
        _, _, download = run_upload(file, caption, receipt_as=receipt_format(fmt), deep_run=deep_run)
        return download

    def check_batch(uploads: list, with_receipt: bool = False) -> dict:
        """Check each upload on its own, then group near-duplicate copies for information. Never raises
        for one bad file: that file gets an entry with its status and reason, and the others carry on."""
        results: list[dict] = []
        items: list[dict] = []
        total = 0
        with tempfile.TemporaryDirectory(prefix="secondlook-") as tmp:
            for i, up in enumerate(uploads):
                name = safe_name(up.filename)
                entry: dict = {"index": i, "file": name}
                results.append(entry)

                def refuse(status: int, why: str, entry=entry) -> None:
                    entry.update(status=status, error=why)

                if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
                    refuse(415, "Not an image file (JPEG, PNG, WebP, HEIC, AVIF, GIF or TIFF).")
                    continue
                data = up.file.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    refuse(413, "That file is over 25 MB.")
                    continue
                total += len(data)
                if total > MAX_BATCH_BYTES:
                    raise HTTPException(413, "That batch is over 100 MB in all.")
                if not data:
                    refuse(400, "That file is empty.")
                    continue
                # Each file in its own folder under its cleaned name; the receipt records the name the user gave it.
                (Path(tmp) / f"{i:04d}").mkdir()
                path = Path(tmp) / f"{i:04d}" / name
                path.write_bytes(data)
                problem = batch.shape_problem(str(path))
                if problem:
                    refuse(422, f"Not checked: {problem}.")
                    continue
                started = time.monotonic()
                try:
                    result, ran, failed, why = engine(str(path))
                except Exception:  # the ladder should never raise; say so for this file and go on
                    log.exception("check failed on %s", name)
                    refuse(500, "The check failed on this file. Nothing was kept.")
                    continue
                entry.update(status=200, **to_body(result, ran, failed, why))
                entry["seconds"] = round(time.monotonic() - started, 2)
                if with_receipt:
                    batch_receipt(entry, result, path, given_name(up.filename))
                h, usable = batch.dhash(str(path))
                items.append({"file": f"{i:04d}", "sha256": hashlib.sha256(data).hexdigest(), "dhash": h,
                              "usable": usable})
        # Information only: computed after every answer is final, and never read back into one.
        groups = batch.group_near_duplicates(items)
        for g in groups:
            g["indexes"] = [int(k) for k in g["files"]]
            g["files"] = [results[k]["file"] for k in g["indexes"]]
            for k in g["indexes"]:
                results[k]["near_duplicate_group"] = g["id"]
        return {"count": len(results), "checked": len(items), "results": results,
                "near_duplicate_groups": groups,
                "limits": {"files": MAX_BATCH_FILES, "bytes_per_file": MAX_BYTES, "bytes_in_all": MAX_BATCH_BYTES}}

    def batch_receipt(entry: dict, result: Result, path: Path, name: str) -> None:
        """The file's receipt, byte for byte as `secondlook check --receipt` writes it for a file called `name`
        (the user's own name: `path` is the temp copy, written under the cleaned name). A failed receipt leaves
        the answer."""
        try:
            if result.version in (None, "", "stub", "unknown"):
                result = dataclasses.replace(result, version=version)
            rc = receipts.build(result, path)
            rc["file"]["name"] = name
            rc["demo"] = entry["demo"]
            text = receipts.canonical_json(rc)
        except Exception:
            log.exception("receipt failed on %s", path.name)
            entry["receipt_error"] = "The check ran but the receipt failed."
            return
        entry["receipt"] = rc
        entry["receipt_json"] = text.decode("utf-8")
        entry["receipt_sha256"] = hashlib.sha256(text).hexdigest()
        entry["receipt_name"] = receipts.receipt_name(rc) + ".json"

    @api.post("/api/batch")
    async def batch_endpoint(request: Request, receipt: bool = False) -> JSONResponse:
        """Several images in one request (form field `files`, repeated). Each is checked on its own."""
        try:
            form = await request.form(max_files=MAX_BATCH_FILES, max_fields=MAX_BATCH_FILES)
        except StarletteHTTPException as exc:  # Starlette raises its own base class, not FastAPI's
            if "Too many" in str(exc.detail):
                raise HTTPException(413, f"Please send at most {MAX_BATCH_FILES} files at a time.")
            raise
        except MultiPartException:
            raise HTTPException(400, "That request could not be read as files.")
        try:
            uploads = [f for f in form.getlist("files") if hasattr(f, "filename") and hasattr(f, "file")]
            if not uploads:
                raise HTTPException(400, "Please send one or more image files in the form field \"files\".")
            body = await run_in_threadpool(check_batch, uploads, receipt)
        finally:
            await form.close()
        return JSONResponse(body)

    # The Glimmer investigator (M2, DAT-873): the local model calls the checks as tools. The answer
    # is still the ladder's; the stub engine has no investigator.
    @api.post("/api/investigate")
    def investigate(file: UploadFile = File(...), caption: str | None = Form(None)) -> JSONResponse:
        if use_stub:
            raise HTTPException(409, "The investigator runs on the real ladder only, not the demo stub.")
        if caption is not None and len(caption) > MAX_CAPTION:
            raise HTTPException(413, f"That caption is over {MAX_CAPTION} characters.")
        name, data = _read_upload(file)
        run = investigator
        if run is None:
            from secondlook.investigator import investigate as _investigate

            def run(path: str, text: str | None):
                return _investigate(path, text, rungs=loaded)
        with tempfile.TemporaryDirectory(prefix="secondlook-") as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            try:
                note = run(str(path), caption)
            except Exception:  # investigate() should never raise
                log.exception("investigate failed on %s", name)
                raise HTTPException(500, "The investigation failed on this file. Nothing was kept.")
        body = dataclasses.asdict(note)
        if body.get("version") in (None, "", "unknown"):
            body["version"] = version
        body["engine"] = engine_name
        body["rungs"] = rung_names
        body["demo"] = any(e.get("raw", {}).get("stub") for e in body["evidence"])
        # "unavailable": no Glimmer server answered, so the note is the ladder's alone and the page says
        # the investigator is not set up. "ran": the model was reached (it may still have stopped early).
        from secondlook.investigator import UNAVAILABLE
        body["glimmer"] = "unavailable" if any(str(p).startswith(UNAVAILABLE) for p in body["problems"]) else "ran"
        # DAT-850: the clauses Glimmer picked from this caption for a lookup that found a published fact-check,
        # kept on this server with the file's sha256 and the caption. The page sends back only this id.
        body["receipt_run"] = deep_runs.add(hashlib.sha256(data).hexdigest(), caption,
                                            receipts.deep_run_picks(note, (caption or "").strip() or None))
        return JSONResponse(body)

    return _Guard(api)


app = create_app()
