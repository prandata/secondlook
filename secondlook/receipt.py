"""Evidence receipt (R1, DAT-870): one record of a check that a team can attach to a story or a claim file.

    receipt = build(result, "photo.jpg")      # a plain dict, schema "second-look-receipt/1"
    write_json(receipt, "photo.receipt.json")  # canonical JSON, plus photo.receipt.json.sha256
    write_pdf(receipt, "photo.receipt.pdf")    # one or two pages, fonts embedded

The receipt records what was checked (file name, size, sha256), by what (tool version, Python and
library versions), the answer, and every evidence card with all its fields, including the limit
(`does_not_prove`) of each. It states the product's limits in one fixed line taken from
docs/CLAIMS.md. It says nothing the ladder did not say: it never turns an answer into "real".

Two checks of the same file give receipts that differ only in `created_at`: the few fields in
VOLATILE_FIELDS (how long a rung took) are left out of a card's `raw` and listed in `raw_dropped`.

Claim words Muse Glimmer picked (DAT-850, TF-722 Beat 7): the deep check's fact-check lookup uses whole
clauses Glimmer picked from the caption. The server keeps a short, bounded record of the deep checks it ran
itself (DeepRuns: a run id, the file's sha256, the caption and the picked clauses), and the page sends only
the run id with a receipt request, never the words. The receipt takes the clauses from that record after
checking the file's sha256 and the caption; an unknown, expired or mismatched run id gives a receipt without
picks and a plain reason. picked_claims() accepts clauses only when each is a whole clause of the caption
(secondlook.verbatim), at most MAX_PICKED. with_picked_claims() runs the same fixed S6 lookup on them that the
investigator ran, adds its cards and returns the lookup's status. Nothing is copied from the investigator's
output, and the answer stays the ladder's: claim cards never move it, and a check refuses the receipt if they
would. picked_input() records the clauses in `inputs`, apart from typed claims, with their source and the
lookup's status.

Nothing here uses the network. The JSON half needs only the standard library; the PDF half needs
ReportLab (BSD), installed by the `receipt` and `web` extras. The claim lookup reads the local S6 index.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from xml.sax.saxutils import escape

from .explain import explain
from .types import Result
from .verbatim import normalise, split_clauses, whole_clause

SCHEMA = "second-look-receipt/1"
TOOL_NAME = "secondlook"

# One fixed line, built from docs/CLAIMS.md ("What we cannot claim" and the answer definitions).
# tests/test_receipt.py checks that each sentence below still has its source in that file.
LIMITS = (
    "Second Look never says an image is real, genuine or authentic, or safe to trust. "
    "signed_camera means a signer's record says a camera captured it. "
    "cant_tell means we found nothing either way. "
    "A signed record does not prove that what the picture shows is true. "
    "A concern is a reason to look closer, not a verdict. "
    "Second Look is not a fact-checker and is not affiliated with Meta."
)

ANSWER_LABELS = {
    "signed_ai": "Signed as AI-made",
    "signed_camera": "Signed as a camera capture",
    "concerns": "Concerns found",
    "cant_tell": "Can't tell",
}

# Libraries whose versions are recorded when installed: the ones the rungs and the PDF rely on.
LIBRARIES = ("c2pa-python", "pillow", "torch", "torchvision", "timm", "safetensors", "reportlab")

# Per-run timing is not evidence, and it makes two runs of the same check differ. These are the only
# fields build() drops: each is a (rung, path) pair, where path is the tuple of keys from the top of that
# card's `raw`. A dropped field is listed in the card's `raw_dropped`. Nothing is matched by name, so a
# time read from the file or the audio (the C2PA sign_time, the EXIF datetime_original, the voice
# transcript's seconds and each segment's end_ms) always stays. A new rung that records how long it took
# must add its own entry here deliberately, and docs/RECEIPT.md lists the same entries.
VOLATILE_FIELDS = frozenset({
    ("classifier", ("seconds",)),   # secondlook/rungs/classifier.py: wall-clock time of the model run
    ("watermark", ("seconds",)),    # secondlook/rungs/watermark.py: wall-clock time of the model run
})

# How raw detail is kept: bytes and very long text are replaced by a descriptor (length and
# sha256 of what was dropped), so the receipt stays small and a reader can still tell what was there.
MAX_TEXT = 8192
MAX_ITEMS = 500
MAX_DEPTH = 12
HEAD = 200

_HASH_CHUNK = 1 << 20


# --- build ------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_facts(path) -> dict:
    p = Path(path)
    h = hashlib.sha256()
    size = 0
    with p.open("rb") as f:
        while chunk := f.read(_HASH_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return {"name": p.name, "size_bytes": size, "sha256": h.hexdigest()}


def _versions() -> dict:
    libs = {}
    for name in LIBRARIES:
        try:
            libs[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    try:
        package = metadata.version(TOOL_NAME)
    except metadata.PackageNotFoundError:
        package = "unknown"
    return {
        "package": package,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "libraries": libs,
    }


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _drop_volatile(rung: str, raw, dropped: list[str]):
    """`raw` (already a JSON-safe copy) without the VOLATILE_FIELDS entries for this rung.

    Each dropped field is noted in `dropped`. A path that is not there is skipped without a note.
    """
    for r, path in sorted(VOLATILE_FIELDS):
        if r != rung:
            continue
        node = raw
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict) and path[-1] in node:
            del node[path[-1]]
            dropped.append("raw." + ".".join(path) + " (per-run timing)")
    return raw


def _clean(value, where: str, dropped: list[str], depth: int = 0):
    """A JSON-safe copy of `value`. Appends a note to `dropped` for each blob it replaced."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            dropped.append(f"{where} (text, {len(value)} characters)")
            return {"dropped": "text", "length": len(value), "head": value[:HEAD],
                    "sha256": _digest(value.encode("utf-8", "surrogatepass"))}
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        dropped.append(f"{where} (bytes, {len(data)} bytes)")
        return {"dropped": "bytes", "length": len(data), "sha256": _digest(data)}
    if depth >= MAX_DEPTH:
        dropped.append(f"{where} (nested too deep)")
        return {"dropped": "nested too deep"}
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = k if isinstance(k, str) else str(k)
            out[key] = _clean(v, f"{where}.{key}", dropped, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if isinstance(value, (set, frozenset)):
            items.sort(key=repr)
        kept = [_clean(v, f"{where}[{i}]", dropped, depth + 1) for i, v in enumerate(items[:MAX_ITEMS])]
        if len(items) > MAX_ITEMS:
            dropped.append(f"{where} ({len(items) - MAX_ITEMS} more items)")
            kept.append({"dropped": "items", "count": len(items) - MAX_ITEMS})
        return kept
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def build(result: Result, path, *, inputs=None, created_at: str | None = None) -> dict:
    """The receipt for one check, as a plain dict.

    `path` is the file that was checked: its name, size and sha256 are read from it, so it must
    still exist (FileNotFoundError otherwise). `inputs` is whatever else the user gave, such as a
    caption or typed claims; it is recorded as given. `created_at` is for a reproducible run (the queue's
    `run_time`): the exact "%Y-%m-%dT%H:%M:%SZ" text to record instead of the clock. Left out, it is the
    time now, which is what every normal check records.
    """
    cards = []
    for i, e in enumerate(result.evidence, start=1):
        dropped: list[str] = []
        raw = _drop_volatile(e.rung, _clean(e.raw, "raw", dropped), dropped)
        cards.append({
            "index": i,
            "rung": e.rung,
            "finding": e.finding,
            "proves": e.proves,
            "does_not_prove": e.does_not_prove,
            "strength": e.strength,
            "raw": raw,
            "raw_dropped": dropped,
        })
    input_notes: list[str] = []
    return {
        "schema": SCHEMA,
        "created_at": _now() if created_at is None else created_at,
        "tool": {"name": TOOL_NAME, "version": result.version},
        "versions": _versions(),
        "file": _file_facts(path),
        "answer": result.answer,
        # The plain-language summary the page, the desk note and the CLI show (DAT-850), never the ladder's own.
        "summary": explain(result).summary,
        # True when any card comes from the demo stub. The web layer also sets it for the stub engine.
        "demo": any(isinstance(e.raw, dict) and e.raw.get("stub") is True for e in result.evidence),
        "evidence": cards,
        "inputs": _clean(inputs if inputs is not None else {}, "inputs", input_notes),
        "limits": LIMITS,
    }


# --- claim words Muse Glimmer picked from the caption -------------------------------------------

MAX_PICKED = 3   # glimmer.MAX_PICKS: the investigator never looks up more clauses than this at once
PICKED_KEY = "picked_claims"   # in `inputs`; typed claims would be "claims", so the two never mix
PICKED_SOURCE = ("Picked by Muse Glimmer from the caption (clause choice only; the words are the "
                 "caption's own).")


class PickedRefused(ValueError):
    """Picked claim words that are not whole clauses of the caption, or too many. str() is the plain reason."""


class AnswerWouldChange(RuntimeError):
    """Adding the picked words' fact-check cards would change the ladder's answer. Claim cards never move
    the answer (ladder.decide has no rule for them), so this is a guard, not an expected path."""


def picked_claims(caption: str | None, picked) -> list[str]:
    """The picked clauses as the caption writes them, in caption order, without repeats.

    `picked` is what the page sent: one string per clause. Each must be a whole clause of `caption`
    (verbatim.whole_clause, which allows outer spaces and the clause's own full stop), and at most
    MAX_PICKED may be sent. Anything else raises PickedRefused with a plain reason; nothing is repaired.
    No picks gives []."""
    picks = list(picked or [])
    if not picks:
        return []
    if len(picks) > MAX_PICKED:
        raise PickedRefused(f"At most {MAX_PICKED} picked claim clauses can go in a receipt; {len(picks)} were sent.")
    text = normalise(caption) if isinstance(caption, str) else ""
    if not text:
        raise PickedRefused("Picked claim words need the caption they were picked from, and no caption was sent.")
    clauses = split_clauses(text)
    chosen = []
    for p in picks:
        c = whole_clause(p, clauses) if isinstance(p, str) else None
        if c is None:
            shown = " ".join(str(p).split())
            shown = shown if len(shown) <= 80 else shown[:79] + "…"
            raise PickedRefused("Each picked claim must be a whole clause of the caption, word for word. "
                                f"This one is not: “{shown}”.")
        if c not in chosen:
            chosen.append(c)
    return [c.text for c in sorted(chosen, key=lambda c: c.start)]


def with_picked_claims(result: Result, picked: list[str], db=None) -> tuple[Result, str | None]:
    """`result` with the fact-check cards the picked clauses find, from the same S6 lookup the investigator
    runs: one query, the clauses joined in caption order with glimmer.CLAIM_JOIN, up to
    investigator.CLAIM_CARDS matches, each card marked as model-chosen search words from the caption.

    Returns (result, lookup status): S6's own status, "found", "none" (looked, nothing close) or
    "unavailable" (the index could not be read). The answer and summary are the ladder's. If the new cards
    would change them, AnswerWouldChange is raised and no receipt is made. No picks: (`result`, None)."""
    if not picked:
        return result, None
    from . import glimmer, ladder
    from .investigator import CLAIM_CARDS

    query = glimmer.CLAIM_JOIN.join(picked)
    got = glimmer.lookup_candidate(glimmer.CandidateClaim(query, "caption"), db, CLAIM_CARDS)
    cards, found = got["cards"], got.get("lookup")
    status = getattr(found, "status", None) or ("found" if cards else "none")
    evidence = [*result.evidence, *cards]
    if ladder.decide(evidence) != ladder.decide(result.evidence):
        raise AnswerWouldChange("the picked claim words' cards would change the answer")
    return Result(answer=result.answer, evidence=evidence, summary=result.summary, version=result.version), status


# What the PDF says the receipt's own lookup did, by S6 status (a reviewer N2, design NIT 5).
LOOKUP_LINES = {
    "found": "The receipt looked these words up and found a match in published fact-checks; see the claim cards below.",
    "none": "No published fact-check matched these words.",
    "unavailable": "The offline fact-check list could not be read, so no fact-check could be looked up.",
}


def picked_input(picked: list[str], lookup: str | None = None) -> dict:
    """The `inputs` entry for picked clauses, with the receipt's own lookup status: {} when there are none."""
    if not picked:
        return {}
    return {PICKED_KEY: {"clauses": list(picked), "picked_by": "Muse Glimmer", "from": "caption",
                         "source": PICKED_SOURCE, "lookup": lookup}}


# --- the deep checks this server ran -----------------------------------------------------------

DEEP_RUNS_KEPT = 64          # the most recent deep checks kept; the oldest goes first
DEEP_RUN_SECONDS = 3600      # and none is kept longer than this
NOT_ADDED = {
    "unknown": ("The deep check this answer came from is no longer on record (the server keeps its last "
                f"{DEEP_RUNS_KEPT} deep checks for an hour, and none after a restart), so this receipt has no "
                "picked claim words. Run the deep check again to add them."),
    "file": ("The deep check on record was run on a different file, so this receipt has no picked claim "
             "words."),
    "caption": ("The deep check on record was run with a different caption, so this receipt has no picked "
                "claim words."),
}


def deep_run_picks(note, caption: str | None) -> list[str]:
    """The clauses Muse Glimmer picked from `caption` in a deep check (an investigator DeskNote, or its dict),
    for the receipt: those of its first lookup that ran on caption words and found a published fact-check,
    checked again as whole clauses of the caption. [] when there is none."""
    steps = note.get("steps") if isinstance(note, dict) else getattr(note, "steps", None)
    for st in steps or []:
        get = st.get if isinstance(st, dict) else (lambda k, st=st: getattr(st, k, None))
        args = get("arguments")
        if not (get("ok") and get("tool") == "lookup_claim" and get("template") == "lookup_claim.found"
                and isinstance(args, dict) and args.get("from") == "caption" and isinstance(args.get("text"), str)):
            continue
        from .glimmer import CLAIM_JOIN
        try:
            return picked_claims(caption, [t for t in args["text"].split(CLAIM_JOIN) if t.strip()])
        except PickedRefused:
            return []
    return []


@dataclass(frozen=True)
class DeepRun:
    sha256: str
    caption: str          # normalised, as verbatim.normalise gives it
    clauses: tuple[str, ...]
    at: float


class DeepRuns:
    """A short, bounded record of the deep checks this server ran itself, so a receipt can keep the claim
    words Muse Glimmer picked without trusting words a client sends (Codex on PR #42). Kept in memory only:
    at most `kept` runs, each for at most `seconds`; a restart forgets them all."""

    def __init__(self, kept: int = DEEP_RUNS_KEPT, seconds: float = DEEP_RUN_SECONDS, clock=time.monotonic):
        self.kept, self.seconds, self.clock = kept, seconds, clock
        self._runs: OrderedDict[str, DeepRun] = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        while self._runs:
            oldest = next(iter(self._runs.values()))
            if now - oldest.at <= self.seconds and len(self._runs) <= self.kept:
                break
            self._runs.popitem(last=False)

    def add(self, sha256: str, caption: str | None, clauses: list[str]) -> str | None:
        """Record one deep check's picks; its run id, or None when it picked nothing."""
        if not clauses:
            return None
        run_id = secrets.token_urlsafe(18)
        with self._lock:
            now = self.clock()
            self._runs[run_id] = DeepRun(sha256, normalise(caption or ""), tuple(clauses), now)
            self._expire(now)
        return run_id

    def __len__(self) -> int:
        with self._lock:
            self._expire(self.clock())
            return len(self._runs)

    def picks(self, run_id, sha256: str, caption: str | None) -> tuple[list[str], str | None]:
        """(clauses, None) when `run_id` is a deep check on record for this file (by sha256) and this caption;
        otherwise ([], the plain reason, a NOT_ADDED value). No run id: ([], None)."""
        if run_id is None or (isinstance(run_id, str) and not run_id.strip()):
            return [], None
        with self._lock:
            self._expire(self.clock())
            run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            return [], NOT_ADDED["unknown"]
        if run.sha256 != sha256:
            return [], NOT_ADDED["file"]
        if run.caption != normalise(caption or ""):
            return [], NOT_ADDED["caption"]
        return list(run.clauses), None


# --- JSON -------------------------------------------------------------------------------------

def canonical_json(receipt: dict) -> bytes:
    """The receipt as bytes: sorted keys, two-space indent, UTF-8, one trailing newline.

    The same receipt always gives the same bytes. The `.sha256` file and the PDF both quote the
    sha256 of exactly these bytes.
    """
    text = json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def receipt_name(receipt: dict) -> str:
    """A file name stem for this receipt's files: '<checked file name>.receipt'."""
    return f"{receipt['file']['name']}.receipt"


def _target(out, receipt: dict, suffix: str) -> Path:
    out = Path(out)
    return out / f"{receipt_name(receipt)}{suffix}" if out.is_dir() else out


def write_json(receipt: dict, out) -> Path:
    """Write canonical JSON to `out` and its hash to `<out>.sha256`; return the JSON path.

    `out` is a file path (or an existing directory, which gets `<file name>.receipt.json`). The
    `.sha256` file is in `sha256sum` format, so `sha256sum -c photo.receipt.json.sha256` checks it.
    """
    target = _target(out, receipt, ".json")
    data = canonical_json(receipt)
    target.write_bytes(data)
    sidecar = target.with_name(target.name + ".sha256")
    sidecar.write_text(f"{_digest(data)}  {target.name}\n", encoding="utf-8")
    return target


# --- PDF --------------------------------------------------------------------------------------

_FONTS: dict[str, str] = {}


def _register_fonts() -> dict[str, str]:
    """Embed TrueType fonts: Bitstream Vera (ships inside ReportLab) and Playfair Display for the title."""
    if _FONTS:
        return _FONTS
    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    vera = Path(reportlab.__file__).parent / "fonts"
    for name, file in (("SL-Sans", "Vera.ttf"), ("SL-Sans-Bold", "VeraBd.ttf"), ("SL-Sans-Italic", "VeraIt.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(vera / file)))
    pdfmetrics.registerFontFamily("SL-Sans", normal="SL-Sans", bold="SL-Sans-Bold", italic="SL-Sans-Italic",
                                  boldItalic="SL-Sans-Bold")
    title = "SL-Sans-Bold"
    playfair = Path(__file__).parent / "web" / "static" / "fonts" / "PlayfairDisplay.ttf"
    try:
        pdfmetrics.registerFont(TTFont("SL-Title", str(playfair)))
        title = "SL-Title"
    except Exception:  # a brand nicety: never let it stop a receipt
        pass
    _FONTS.update(sans="SL-Sans", bold="SL-Sans-Bold", italic="SL-Sans-Italic", title=title)
    return _FONTS


class _Text:
    """Makes text safe for the embedded fonts: glyphs the font lacks become '?', and we remember it."""

    def __init__(self):
        from reportlab.pdfbase import pdfmetrics

        self.glyphs = pdfmetrics.getFont("SL-Sans").face.charToGlyph
        self.title_glyphs = pdfmetrics.getFont(_FONTS["title"]).face.charToGlyph
        self.lossy = False

    def __call__(self, value, glyphs=None) -> str:
        glyphs = glyphs or self.glyphs
        text = unicodedata.normalize("NFC", str(value))
        out = []
        for ch in text:
            if ch in "\n\r\t":
                out.append(" ")
            elif unicodedata.category(ch)[0] == "C":
                continue
            elif ord(ch) in glyphs:
                out.append(ch)
            else:
                out.append("?")
                self.lossy = True
        return "".join(out)

    def para(self, value) -> str:
        """Escaped, ready for a ReportLab Paragraph."""
        return escape(self(value))


def _answer_label(receipt: dict) -> str:
    return ANSWER_LABELS.get(receipt["answer"], str(receipt["answer"]))


# The PDF quotes the sha256 of the canonical JSON of the receipt it was made from, in the same run. The CLI and the
# batch write that JSON beside the PDF; the web page's JSON button runs a new check (its own created_at), so its
# file can have another hash (DAT-850 A249, judge re-run 30 Oct, old snag 4).
PDF_JSON_SHA_LINE = ("SHA-256 of this receipt's JSON, built with this PDF. "
                     "A separate JSON download is a new check and can differ.")


def write_pdf(receipt: dict, out) -> Path:
    """Write the receipt as a PDF (A4, one or two pages for a typical check); return the path.

    The PDF is a view of the receipt dict: the same answer, summary, hashes and every card with its
    limit. It also quotes the sha256 of this receipt's canonical JSON, built in the same run, so a JSON written
    from the same receipt (as the CLI and the batch do) can be tied to it. The output is deterministic: the same receipt gives the same bytes.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ImportError("write_pdf needs ReportLab: pip install 'secondlook[receipt]'") from exc

    fonts = _register_fonts()
    text = _Text()
    target = _target(out, receipt, ".pdf")
    json_sha = _digest(canonical_json(receipt))
    file = receipt["file"]

    ink, grey, rule = colors.HexColor("#1a1a1a"), colors.HexColor("#555555"), colors.HexColor("#c9c9c9")
    base = dict(fontName=fonts["sans"], textColor=ink, alignment=TA_LEFT)
    body = ParagraphStyle("body", fontSize=9, leading=12, **base)
    small = ParagraphStyle("small", fontSize=7.5, leading=9.5, **base)
    cell = ParagraphStyle("cell", fontSize=7.5, leading=9.5, splitLongWords=1, **base)
    head = ParagraphStyle("head", parent=cell, fontName=fonts["bold"], textColor=colors.white)
    h2 = ParagraphStyle("h2", fontSize=11, leading=14, spaceBefore=10, spaceAfter=4,
                        **{**base, "fontName": fonts["bold"]})
    title = ParagraphStyle("title", fontSize=21, leading=26, **{**base, "fontName": fonts["title"]})
    answer = ParagraphStyle("answer", fontSize=15, leading=19, **{**base, "fontName": fonts["bold"]})
    mono_hash = ParagraphStyle("hash", parent=body, fontSize=8.5, leading=11)
    banner = ParagraphStyle("banner", parent=body, fontName=fonts["bold"], textColor=colors.HexColor("#8a1c1c"))

    def P(value, style=cell):
        return Paragraph(text.para(value), style)

    def kv_table(rows, widths):
        # A value given as a list of lines puts each line on its own line in the cell.
        t = Table([[P(k, ParagraphStyle("k", parent=cell, fontName=fonts["bold"])),
                    [P(x) for x in v] if isinstance(v, list) else P(v)] for k, v in rows],
                  colWidths=widths)
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), fonts["sans"]),  # the table default is Helvetica, not embedded
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, rule),
                               ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                               ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
        return t

    width = A4[0] - 36 * mm
    story = [
        Paragraph(escape(text("Second Look evidence receipt", text.title_glyphs)), title),
        Paragraph(text.para(f"In development. Schema {receipt['schema']}. Created {receipt['created_at']}."), small),
        Spacer(1, 8),
    ]
    if receipt.get("demo"):
        story += [Paragraph(text.para("DEMO ANSWER. The demo stub chose this answer from the file name; "
                                      "the image was not read. This receipt is not a check of the image."), banner),
                  Spacer(1, 6)]
    story += [
        Paragraph(text.para(f"Answer: {_answer_label(receipt)} ({receipt['answer']})"), answer),
        Spacer(1, 3),
        Paragraph(text.para(receipt["summary"]), body),
        Paragraph(text.para("File checked"), h2),
        kv_table([("Name", file["name"]), ("Size", f"{file['size_bytes']} bytes")], [60, width - 60]),
        Spacer(1, 2),
        Paragraph(text.para("SHA-256 of the file"), small),
        Paragraph(text.para(file["sha256"]), mono_hash),
    ]
    inputs = receipt.get("inputs")
    if inputs:
        story.append(Paragraph(text.para("Inputs given"), h2))
        shown = dict(inputs) if isinstance(inputs, dict) else {"input": inputs}
        rows = []
        caption = shown.pop("caption", None)
        if isinstance(caption, str):
            rows.append(("Caption", caption))
        elif caption is not None:
            shown["caption"] = caption
        picked = shown.pop(PICKED_KEY, None)
        if isinstance(picked, dict) and isinstance(picked.get("clauses"), list):
            # One row, one line each: the clauses as the caption writes them, where they came from, and what
            # the receipt's own lookup did with them.
            lines = ["; ".join(f"“{c}”" for c in picked["clauses"]) + "."]
            lines += [x for x in (picked.get("source"), LOOKUP_LINES.get(picked.get("lookup"))) if x]
            rows.append(("Claim words", lines))
        elif picked is not None:
            shown[PICKED_KEY] = picked
        rows += [(k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True))
                 for k, v in shown.items()]
        story.append(kv_table(rows, [60, width - 60]))

    cards = receipt["evidence"]
    story.append(Paragraph(text.para(f"Evidence cards ({len(cards)})"), h2))
    if cards:
        cols = [48, 140, 108, 150, width - 446]
        rows = [[P(h, head) for h in ("Rung", "Finding", "Proves", "Does not prove", "Strength")]]
        for c in cards:
            # A classifier score that raises no concern (a threshold without a gate PASS) reads "Score only",
            # as on the page, not the bare "signal" beside "raises no concern" (PR #33 design check).
            score_only = (c["rung"] == "classifier" and c["strength"] == "signal"
                          and (c.get("raw") or {}).get("raises_concern") is False)
            rows.append([P(f"{c['index']}. {c['rung']}"), P(c["finding"]), P(c["proves"]),
                         P(c["does_not_prove"]), P("Score only" if score_only else c["strength"])])
        t = Table(rows, colWidths=cols, repeatRows=1, splitInRow=1)
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), fonts["sans"]), ("BACKGROUND", (0, 0), (-1, 0), ink), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.25, rule),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                               ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t)
        story.append(Paragraph(text.para("Machine detail for each card is in the JSON receipt."), small))
    else:
        story.append(Paragraph(text.para("This check produced no evidence cards."), body))

    versions = receipt["versions"]
    libs = ", ".join(f"{k} {v}" for k, v in versions.get("libraries", {}).items()) or "none recorded"
    story += [
        Paragraph(text.para("Versions and hashes"), h2),
        kv_table([
            ("Tool", f"{receipt['tool']['name']} {receipt['tool']['version']}"),
            ("Package", versions.get("package", "unknown")),
            ("Python", f"{versions.get('python_implementation', '')} {versions.get('python', '')}".strip()),
            ("Libraries", libs),
        ], [60, width - 60]),
        Spacer(1, 2),
        Paragraph(text.para(PDF_JSON_SHA_LINE), small),
        Paragraph(text.para(json_sha), mono_hash),
        Paragraph(text.para("What this receipt does not say"), h2),
        Paragraph(text.para(receipt["limits"]), body),
    ]
    if text.lossy:
        story.append(Paragraph(text.para("Some characters could not be shown in this PDF and appear as ?. "
                                         "The JSON receipt has the exact text."), small))

    footer = f"Second Look (in development)  |  {file['name']}  |  sha256 {file['sha256'][:16]}"

    class Numbered(rl_canvas.Canvas):
        """Canvas that writes 'Page x of y' once the page count is known."""

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self.setFont(fonts["sans"], 7)
                self.setFillColor(grey)
                self.drawString(18 * mm, 10 * mm, text(footer)[:110])
                self.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
                super().showPage()
            super().save()

    doc = SimpleDocTemplate(
        str(target), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
        title="Second Look evidence receipt", author="Second Look", subject=text(file["name"]),
        creator="secondlook.receipt", invariant=1,
        initialFontName=fonts["sans"], initialFontSize=9,  # else Helvetica, which is not embedded
    )
    doc.build(story, canvasmaker=Numbered)
    return target


_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def attachment_name(receipt: dict, suffix: str) -> str:
    """An ASCII-only download name, for HTTP headers: '<file name>.receipt<suffix>'."""
    return _SAFE.sub("_", receipt_name(receipt)) + suffix
