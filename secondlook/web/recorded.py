"""Recorded Muse Glimmer runs (DAT-850, mock judging item 3a): a deep check saved once, shown when no model runs here.

scripts/record_investigations.py ran the investigator on a few demo samples against Meta's Muse Glimmer 30B and
wrote one JSON per sample under samples/recorded/. The page offers a recording only when:

- the checked file's sha256 is the recorded sample's sha256 (the bytes, not the name);
- the check's caption is the recording's caption: none for a recording without one, and for the one captioned
  recording (the Pentagon mock post, DAT-850 mock judging v2 item 1) the same text, trimmed, matched by the
  sha256 of its UTF-8 bytes, which the file stores beside the caption and validate() checks;
- no Glimmer server answers (/api/health). When one does, the button runs live, as before.

A recording is shown under its own label, built here and nowhere else, before any of its content: what it is,
the model, the machine, the date and the seconds, and that it is not live. It never changes the answer on the
page, and it is never mixed with a live run. A file that fails validate() is not offered.

A recorded run is never a server run: its `receipt_run` is null, so a receipt never takes picked clauses from it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("secondlook.web.recorded")

SCHEMA = "second-look-recorded-run/1"
MODEL_NAME = "Muse Glimmer 30B"
DEFAULT_DIR = Path(__file__).resolve().parents[2] / "samples" / "recorded"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
# The note's keys the page reads (investigate.js showNote), all present in a POST /api/investigate body.
MAX_CAPTION = 2000   # the page's and the server's own limit (secondlook/web/app.py MAX_CAPTION)
NOTE_KEYS = ("file", "answer", "summary", "evidence", "steps", "explanation", "explanation_lines",
             "explanation_label", "image_text", "facts", "label", "problems", "model", "model_seconds",
             "seconds", "version", "glimmer")


def caption_sha256(caption: str | None) -> str | None:
    """The sha256 of a caption as the page sends it (trimmed), or None for no caption or a blank one."""
    text = (caption or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def matches(rec: dict, caption: str | None) -> bool:
    """True when a check with this caption is the check the recording ran: both had no caption, or the
    trimmed caption's sha256 is the one the recording is bound to."""
    return caption_sha256(caption) == rec.get("caption_sha256")


def validate(rec: dict) -> None:
    """Raises ValueError unless rec is a complete recording of a run in which Glimmer ran."""
    def need(ok: bool, what: str) -> None:
        if not ok:
            raise ValueError(f"recorded run: {what}")

    need(isinstance(rec, dict) and rec.get("schema") == SCHEMA, f"schema is not {SCHEMA}")
    sample, model, note = rec.get("sample"), rec.get("model"), rec.get("note")
    need(isinstance(sample, dict) and bool(_SHA.match(str(sample.get("sha256", "")))), "sample.sha256 missing")
    need(isinstance(sample.get("file"), str) and bool(sample["file"]), "sample.file missing")
    cap = rec.get("caption")
    if cap is None:
        need(rec.get("caption_sha256") is None, "caption_sha256 without a caption")
    else:
        need(isinstance(cap, str) and bool(cap.strip()) and cap == cap.strip(), "caption is not trimmed text")
        need(len(cap) <= MAX_CAPTION, f"caption is over {MAX_CAPTION} characters")
        need(rec.get("caption_sha256") == caption_sha256(cap), "caption_sha256 does not bind the caption")
    need(isinstance(model, dict) and model.get("name") == MODEL_NAME, f"model.name is not {MODEL_NAME}")
    need(bool(_SHA.match(str(model.get("sha256", "")))), "model.sha256 missing")
    need(isinstance(rec.get("llama_cpp_build"), str) and bool(rec["llama_cpp_build"]), "llama_cpp_build missing")
    need(bool(_COMMIT.match(str(rec.get("git_commit", "")))), "git_commit missing")
    need(isinstance(rec.get("machine"), str) and bool(rec["machine"].strip()), "machine missing")
    try:
        when = datetime.strptime(str(rec.get("date_utc")), "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("recorded run: date_utc is not YYYY-MM-DDTHH:MM:SSZ") from None
    need(when.year >= 2026, "date_utc out of range")
    s = rec.get("seconds")
    need(isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0, "seconds missing")
    need(isinstance(note, dict) and all(k in note for k in NOTE_KEYS), "note is not an investigator body")
    need(note.get("glimmer") == "ran", "Glimmer did not run in this recording")
    need(note.get("demo") is False, "a recording of a demo stub is not offered")
    need(note.get("receipt_run") is None, "a recording never carries a receipt run id")


def label(rec: dict) -> str:
    """The plain words shown before a recording's content (CRITERIA 3.3: what it is, when, where, not live)."""
    day = datetime.strptime(rec["date_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (f"Recorded run, not live. {MODEL_NAME} on {rec['machine'].strip()}, "
            f"{day.day} {day:%b %Y} UTC, {round(rec['seconds'])} s.")


def load(folder: str | Path | None = None) -> dict[str, dict]:
    """The valid recordings in folder (default samples/recorded/), keyed by the recorded file's sha256."""
    root = Path(folder) if folder else DEFAULT_DIR
    found: dict[str, dict] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            validate(rec)
        except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
            log.warning("recorded run %s skipped: %s", path.name, exc)
            continue
        found[rec["sample"]["sha256"]] = rec
    return found


def public(rec: dict) -> dict:
    """What the page gets: the label first, the provenance of the run, then the note it renders."""
    return {
        "recorded": True,
        "live": False,
        "label": label(rec),
        "file": rec["sample"]["file"],
        "sha256": rec["sample"]["sha256"],
        "model": rec["model"],
        "llama_cpp_build": rec["llama_cpp_build"],
        "git_commit": rec["git_commit"],
        "machine": rec["machine"],
        "date_utc": rec["date_utc"],
        "seconds": rec["seconds"],
        # The caption the recording checked, in full, or None. A recording is offered only for that caption.
        "caption": rec.get("caption"),
        "caption_sha256": rec.get("caption_sha256"),
        # Where a captioned recording's fact-check lookup searched: the feed file and when it was fetched.
        "claims_index": rec.get("claims_index"),
        # A recorded run is never a server run, so no receipt takes picks from it: its run id is null, as
        # scripts/record_investigations.py writes it (recordings made before that field have no key).
        "note": {**rec["note"], "receipt_run": None},
    }
