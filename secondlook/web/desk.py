"""Second Look desk page, part 1 (W10, DAT-850): the claim box, voice notes and what this computer can check.

POST /api/claim   {"text": "..."}  S6 lookup_claim then to_evidence. The publisher's rating word for word,
                  with the publisher, date and link. "No existing fact-check found." by default. With no
                  claims index installed the answer is a "claims index not installed" state (HTTP 200).
POST /api/voice   one audio file. W9: a local transcript, the claims heard in it, then the S6 lookup for
                  each. W9's limits hold: 50 MB, and a note over 10 minutes gets W9's card, never a cut.
capabilities()    what /api/health reports, so the page shows a panel only when its part can run here.

Every answer carries at least one card, and every card fills does_not_prove. Second Look gives no
true or false verdict of its own on a claim, and always "can't tell" on how a voice note was made.
"""
from __future__ import annotations

import dataclasses
import importlib
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from secondlook.rungs import claim as claim_rung
from secondlook.rungs import voice as voice_rung
from secondlook.types import Evidence

log = logging.getLogger("secondlook.web.desk")

CLAIM_MAX_CHARS = 1000
CLAIM_MAX_REQUEST = 16 * 1024                       # a typed claim, as JSON, with room to spare
VOICE_MAX_BYTES = voice_rung.MAX_BYTES              # W9's limit: 50 MB
VOICE_MAX_REQUEST = VOICE_MAX_BYTES + 64 * 1024     # room for the multipart boundary and part headers
VOICE_MB = VOICE_MAX_BYTES // (1024 * 1024)

NOT_INSTALLED = "Claims index not installed, so no fact-check was looked up."
INSTALL_HINT = ("Build it once from the Data Commons ClaimReview feed (CC BY): see \"Claim box and voice "
                "notes\" in the README.")
FEED_CREDIT = ("Fact-checks from the Data Commons ClaimReview feed (CC BY). Each review is its "
               "publisher's own work.")

# How long /api/health waits for the Glimmer server on each of its two requests. A server that is down
# refuses the connection at once; this only bounds one that accepts and then hangs.
GLIMMER_TIMEOUT = 1.0
# The whole Glimmer check, both requests, ends within this. GLIMMER_TIMEOUT is per socket operation, so a local
# server that sends a byte every 0.4 s held /api/health for 22 s without it (a reviewer on PR #32, DAT-850).
GLIMMER_DEADLINE = 1.5
# One check at a time: while a slow server still holds an earlier one, health says unavailable at once rather
# than start another thread that would wait on the same server.
_glimmer_slot = threading.Lock()

# What the receipt PDF needs to import: the receipt module, and the ReportLab modules its write_pdf and
# font set-up import (R1/DAT-870).
RECEIPT_MODULES = ("secondlook.receipt", "reportlab.lib.colors", "reportlab.lib.enums", "reportlab.lib.pagesizes",
                   "reportlab.lib.styles", "reportlab.lib.units", "reportlab.pdfgen.canvas", "reportlab.platypus",
                   "reportlab.pdfbase.pdfmetrics", "reportlab.pdfbase.ttfonts")


# ---------------------------------------------------------------- what this computer can check

def claims_index() -> dict[str, Any]:
    """Whether the offline fact-check index is installed and readable, with its feed's credit.
    No file paths: the page and its screenshots never show where things live on this computer."""
    try:
        path = claim_rung._db_path(None)
        if not path.is_file():
            return {"installed": False, "reason": "not_installed"}
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            meta = {k: v for k, v in con.execute("SELECT key, value FROM meta")}
            con.execute("SELECT 1 FROM fts LIMIT 1").fetchall()
        finally:
            con.close()
    except Exception as e:  # a corrupt or foreign file: say so, never an error page
        log.warning("claims index unreadable: %s", e)
        return {"installed": False, "reason": "unreadable"}
    return {"installed": True, "rows": meta.get("rows", ""), "feed_date_modified": meta.get("feed_date_modified", ""),
            "downloaded_at": meta.get("downloaded_at", ""), "source": meta.get("source", ""),
            "licence": meta.get("licence", ""), "credit": FEED_CREDIT}


def voice() -> dict[str, Any]:
    """Whether a voice note can be transcribed here: whisper-cli and the pinned model (sha256 checked)."""
    cli = voice_rung._whisper_cli()
    model = voice_rung.model_path(voice_rung.DEFAULT_MODEL, warn=False)  # said once at start, not per poll
    decoder = "ffmpeg" if voice_rung._ffmpeg() else ("afconvert" if shutil.which("afconvert") else None)
    return {"ready": bool(cli and model),
            "transcriber": voice_rung._engine_version(cli) if cli else None,
            "model": voice_rung.DEFAULT_MODEL if model else None,
            "decoder": decoder,
            "max_seconds": voice_rung.MAX_SECONDS, "max_bytes": VOICE_MAX_BYTES}


def glimmer() -> dict[str, Any]:
    """Whether a Muse Glimmer server answers here: GlimmerClient.check() on its configured loopback URL, with
    a short timeout per request and one deadline for the whole check. A URL that is not a literal loopback address is refused before any request is made, and
    the client uses no proxy and follows no redirect, so nothing leaves this computer. Never raises."""
    from secondlook import glimmer as g

    end = time.monotonic() + GLIMMER_DEADLINE
    try:
        # g.deadline_open is read here, not at import, so a test can patch the one place requests go out. Its
        # sockets shut at `end` themselves, so the check thread ends by the deadline too, not only this wait.
        client = g.GlimmerClient(timeout=GLIMMER_TIMEOUT, opener=g.deadline_open(end))
    except ValueError:  # SECONDLOOK_GLIMMER_URL is not a bare http://<loopback>:<port>
        return {"ready": False, "reason": "not_loopback"}
    if not _glimmer_slot.acquire(blocking=False):  # an earlier check has not yet reached its deadline
        return {"ready": False, "reason": "unavailable"}
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["info"] = client.check()
        except BaseException as e:  # handed to the caller below; a thread must not raise
            out["error"] = e
        finally:
            _glimmer_slot.release()

    worker = threading.Thread(target=run, name="secondlook-glimmer-health", daemon=True)
    try:
        worker.start()
    except BaseException:  # no thread, so nothing else will release the slot
        _glimmer_slot.release()
        raise
    worker.join(max(0.0, end - time.monotonic()))
    if worker.is_alive():  # the server accepted and is still answering slowly; its socket shuts at the deadline
        log.warning("glimmer check passed its %.1f s deadline", GLIMMER_DEADLINE)
        return {"ready": False, "reason": "unavailable"}
    if "error" in out:
        if not isinstance(out["error"], g.GlimmerError):  # health must answer whatever the socket does
            log.warning("glimmer check failed: %s", out["error"])
        # Nothing listening on the port: the page may say Glimmer is not running (DAT-850 A228). A timeout, an
        # unusable reply or another model is only "unavailable", and the page says so in neutral words.
        if _refused(out["error"]):
            return {"ready": False, "reason": "not_running"}
        return {"ready": False, "reason": "unavailable"}
    info = out["info"]
    return {"ready": True, "model": info["model"], "vision": bool(info["vision"])}


def _refused(error: BaseException) -> bool:
    """Whether the Glimmer check failed because the connection was refused: no server is listening there."""
    cause = error.__cause__ if error.__cause__ is not None else error
    reason = getattr(cause, "reason", cause)  # urllib wraps the socket error in URLError.reason
    return isinstance(cause, ConnectionRefusedError) or isinstance(reason, ConnectionRefusedError)


def receipt_ready() -> bool:
    """Whether the receipt and its PDF writer import here, so POST /api/receipt can answer."""
    try:
        for name in RECEIPT_MODULES:
            importlib.import_module(name)
    except ImportError as e:  # ReportLab is an extra: a missing one is a plain "no", never an error page
        log.warning("receipt writer unavailable: %s", e)
        return False
    return True


def capabilities(engine_name: str, rung_names: list[str]) -> tuple[dict[str, bool], dict[str, Any]]:
    """Booleans the page reads to show or hide each panel, and the details behind them."""
    idx, v, gl = claims_index(), voice(), glimmer()
    caps = {"engine": engine_name == "stub" or bool(rung_names),
            "claims_index": bool(idx["installed"]),
            "voice": bool(v["ready"]),
            "receipt": receipt_ready(),
            "glimmer": bool(gl["ready"])}
    return caps, {"claims_index": idx, "voice": v, "glimmer": gl}


# ---------------------------------------------------------------- cards the rungs do not make

def _not_installed_card() -> Evidence:
    return Evidence(
        rung="claim",
        finding=NOT_INSTALLED,
        proves="Nothing about the claim: the offline fact-check index is not on this computer.",
        does_not_prove="Whether a fact-check of this claim exists. Second Look gives no true or false "
                       "verdict of its own.",
        strength="none",
        raw={"kind": "not_installed", "status": "not_installed"},
    )


def _no_match_card(feed: dict) -> Evidence:
    rows = str(feed.get("rows") or "")
    held = f"the {int(rows):,} published reviews in the offline index" if rows.isdigit() else "the reviews in the offline index"
    return Evidence(
        rung="claim",
        finding=claim_rung.NO_MATCH_MESSAGE,
        proves=f"That none of {held} reviews a claim worded closely enough to this one.",
        does_not_prove="That the claim is true or false, or that no one has checked it: the index holds only "
                       "what publishers put in the ClaimReview feed, and matching is by wording. Second Look "
                       "gives no true or false verdict of its own.",
        strength="none",
        raw={"kind": "no_match", "status": "none", "feed": feed},
    )


def _cards(cards: list[Evidence]) -> list[dict]:
    return [dataclasses.asdict(c) for c in cards]


def check_claim(text: str) -> dict[str, Any]:
    """The claim box's answer. Never raises; a missing index is a state, not an error."""
    idx = claims_index()
    if not idx["installed"] and idx["reason"] == "not_installed":
        return {"kind": "claim", "status": "not_installed", "message": NOT_INSTALLED, "hint": INSTALL_HINT,
                "query": text, "evidence": _cards([_not_installed_card()]), "feed": {}, "note": ""}
    lookup = claim_rung.lookup_claim(text)
    cards = claim_rung.to_evidence(lookup) if lookup.status != "none" else [_no_match_card(lookup.feed)]
    feed = lookup.feed | ({"credit": FEED_CREDIT} if lookup.feed else {})
    return {"kind": "claim", "status": lookup.status, "message": lookup.message, "query": lookup.query,
            "evidence": _cards(cards), "feed": feed, "note": lookup.note}


# ---------------------------------------------------------------- routes

class ClaimIn(BaseModel):
    text: str


def register(api: FastAPI, version: str, safe_name: Callable[[str | None], str]) -> None:
    """Add the claim and voice routes. Plain defs: FastAPI runs them in a worker thread."""
    voice_rung.log_setup_once()  # why voice notes are off, once at server start; the health poll stays quiet

    @api.post("/api/claim")
    def claim(body: ClaimIn) -> JSONResponse:
        text = " ".join(body.text.split())
        if not text:
            raise HTTPException(400, "Please type a claim to look up.")
        if len(text) > CLAIM_MAX_CHARS:
            raise HTTPException(413, f"That claim is over {CLAIM_MAX_CHARS:,} characters. Paste one claim at a time.")
        out = check_claim(text)
        return JSONResponse(out | {"version": version, "demo": False})

    @api.post("/api/voice")
    def voice_note(file: UploadFile = File(...)) -> JSONResponse:
        name = safe_name(file.filename)
        if Path(name).suffix.lower() not in voice_rung.AUDIO_EXTS:
            raise HTTPException(415, "Please choose an audio file (M4A, MP3, WAV, OGG, Opus, AAC, FLAC, AMR, "
                                     "CAF, AIFF or WebM).")
        data = file.file.read(VOICE_MAX_BYTES + 1)
        if len(data) > VOICE_MAX_BYTES:
            raise HTTPException(413, f"That file is over {VOICE_MB} MB.")
        if not data:
            raise HTTPException(400, "That file is empty.")
        with tempfile.TemporaryDirectory(prefix="secondlook-voice-up-") as tmp:
            path = Path(tmp) / name
            path.write_bytes(data)
            try:
                result = voice_rung.analyse(str(path))
            except Exception:  # analyse should never raise; the page still needs an answer
                log.exception("voice check failed on %s", name)
                raise HTTPException(500, "The voice check failed on this file. Nothing was kept.")
        body = dataclasses.asdict(result)
        idx = claims_index()
        feed = ({k: idx[k] for k in ("rows", "feed_date_modified", "source", "licence", "credit")}
                if idx["installed"] else {})
        return JSONResponse(body | {"kind": "voice", "feed": feed, "version": result.version or version,
                                    "demo": False})
