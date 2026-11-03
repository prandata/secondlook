"""Plain-language explanation (E1, DAT-848): fixed templates filled by rules, no model.

the project lead decided on DAT-848 (after a reviewer's FAIL @ 2c34209 on the N3 model path) that no
model writes user-facing text. So every sentence here comes from TEMPLATES or SUMMARIES below.
A template is chosen by the card's rung, the shape of its finding and the answer, and its slots
are filled only from structured fields, each checked before use:

- numbers (a detector score, a cut-off, matched bits, days, km, word counts): finite, in range,
  formatted here;
- names from a closed list held in this file or in Second Look's own code (check names, IPTC and
  EXIF field names, AI tool names, trust lists, IFCN fact-checker sites);
- an IFCN fact-checker's domain, from the fixed list in rungs/factcheckers.py, and only in the
  fixed form "published by <domain>" (PUBLISHED_BY). A domain is an identity, not a claim, so it
  is the one slot the banned-word list does not read: stopfake.org and fakenews.pl are names.

The signer of a C2PA record is never named here: a sentence says "signed by a certificate on" the
trust list it chains to. The card below still shows the signer's name.

Free text from the file, the feed or a transcript (a finding, a Software field, a claim, a rating,
a caption) is never copied in. A sentence names the field instead of quoting it.

Each card sentence pairs what the card shows with what it does not show. No sentence says or
implies real, authentic, genuine, fake or true (BANNED, checked by tests on every template and on
every rendered sentence outside "published by <domain>"). The same Result always gives the same
Explanation, and explain() never raises: a card it can't read gets the "unknown" line, which sends
the reader to the card's own text.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from .ladder import LADDER_LIMIT_KEY, _is_concern, _valid_c2pa
from .rungs._sources import AI_SOURCE_TYPES, CAPTURE_SOURCE_TYPES
from .types import Evidence, Result

LABEL = "Plain-language summary (written by Second Look's rules, not by a model)"
SOURCE = "rules"

# Words no sentence may contain, matched on whole words after NFKC and case folding. The first
# five stems are the ones the product rule names; the rest imply them.
BANNED = (
    r"real\w*", r"authentic\w*", r"genuin\w*", r"fake\w*", r"true\w*", r"truly", r"truth\w*", r"untrue",
    r"fals\w*", r"legit\w*", r"verif\w*", r"proof\w*", r"prove\w*", r"accura\w*", r"correct\w*",
    r"untouched", r"unedited", r"unaltered", r"trustworth\w*", r"honest\w*", r"doctored", r"forg\w*",
    r"deepfake\w*", r"hoax\w*", r"bogus", r"definite\w*", r"certain\w*", r"guarantee\w*",
)
_BANNED_RE = re.compile(r"\b(?:" + "|".join(BANNED) + r")\b")


def banned_words(text: str) -> list[str]:
    """The banned words in text (empty when it is clean)."""
    folded = unicodedata.normalize("NFKC", str(text)).casefold()
    return _BANNED_RE.findall(folded)


# A fact-checker's domain is printed only in this form, and only when it is on the IFCN list.
PUBLISHED_BY = "published by {site}"
_PUBLISHED_BY_RE = re.compile(r"published by ([a-z0-9][a-z0-9.\-]*[a-z0-9])")


def banned_outside_sites(text: str) -> list[str]:
    """The banned words in a sentence once each "published by <listed IFCN domain>" is taken out.

    Everywhere else the whole-word rule holds, so "fake" in any other position still counts."""
    from .rungs.factcheckers import DOMAINS

    def drop(m: re.Match) -> str:
        return "published by" if m.group(1) in DOMAINS else m.group(0)
    return banned_words(_PUBLISHED_BY_RE.sub(drop, str(text)))


@dataclass(frozen=True)
class CardLine:
    rung: str
    sentence: str
    template: str      # the TEMPLATES key it came from, so a reader can trace every sentence


@dataclass(frozen=True)
class Explanation:
    summary: str
    per_card: list[CardLine] = field(default_factory=list)
    label: str = LABEL
    source: str = SOURCE   # always "rules": no model wrote any of it
    summary_template: str = ""

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "source": self.source,
            "summary": self.summary,
            "summary_template": self.summary_template,
            "per_card": [{"rung": c.rung, "sentence": c.sentence, "template": c.template} for c in self.per_card],
        }


# ------------------------------------------------------------------ the template library
# Each card sentence: what the card shows, then (after "but", "so" or "and that") what it does not.
TEMPLATES: dict[str, str] = {
    # C2PA: a signed provenance record
    "c2pa.remote": "The file links to a record stored online, but Second Look stays offline and did not open it.",
    "c2pa.invalid": "The file has a C2PA record that failed its checks, so nothing it says can be relied on.",
    "c2pa.trusted.ai": "A record signed by a certificate on {trust_list} says AI made this, but not who made it "
                       "or why.",
    "c2pa.trusted.camera": "A record signed by a certificate on {trust_list} says a camera took this, but a scene can "
                           "still be staged.",
    "c2pa.trusted.camera_ai_part": "A record signed by a certificate on {trust_list} shows a camera shot with an "
                                   "AI-made part, but not how big it is.",
    "c2pa.trusted.other": "A record signed by a certificate on {trust_list} lists edits, but not if a camera or AI "
                          "made it.",
    "c2pa.trusted.other_ai_part": "A record signed by a certificate on {trust_list} lists edits with an AI-made part, "
                                  "but not how big it is.",
    "c2pa.trusted.conflict": "A record signed by a certificate on {trust_list} says camera but names an AI tool, so it "
                             "settles nothing.",
    "c2pa.trusted.conflict_ai_part": "A record signed by a certificate on {trust_list} says camera, names an AI tool "
                                     "and has an AI-made part, so it settles nothing.",
    "c2pa.untrusted.ai": "A signed record says AI made this, but its signer is not on a C2PA trust list, so anyone "
                         "could have signed it.",
    "c2pa.untrusted.camera": "A signed record says a camera took this, but its signer is not on a C2PA trust list, so "
                             "anyone could have signed it.",
    "c2pa.untrusted.camera_ai_part": "A signed record shows a camera shot with an AI-made part, but its signer is not "
                                     "on a trust list, so anyone could have signed it.",
    "c2pa.untrusted.other": "A signed record lists edits, but its signer is not on a C2PA trust list, so anyone could "
                            "have signed it.",
    "c2pa.untrusted.other_ai_part": "A signed record lists edits with an AI-made part, but its signer is not on a "
                                    "trust list, so anyone could have signed it.",
    "c2pa.untrusted.conflict": "A signed record says camera but names an AI tool, and its signer is not on a trust "
                               "list, so it settles nothing.",
    "c2pa.untrusted.conflict_ai_part": "A signed record says camera, names an AI tool and has an AI-made part, but "
                                       "its signer is not on a trust list, so it settles nothing.",
    # IPTC: unsigned labels in the file's XMP
    "iptc.source.ai": "The file's IPTC label says AI made this, but anyone can add, change or remove that label.",
    "iptc.source.camera": "The file's IPTC label says a device captured this, but anyone can add or change that label.",
    "iptc.source.other": "The file's IPTC label names a source that is neither AI nor a camera, but anyone can change "
                         "it.",
    "iptc.ai_fields": "The file's IPTC fields ({fields}) say AI was used, but anyone can write or remove them.",
    # EXIF: unsigned camera and software fields
    "exif.camera": "The file's EXIF fields ({fields}) name a camera, but they can be copied, so they don't show it "
                   "took this.",
    "exif.software.ai_tool": "The file's EXIF Software field names {tool}, an AI image tool, but anyone can type into "
                             "that field.",
    "exif.software.plain": "The file's EXIF Software field names the last program that saved it, but not how the "
                           "image was made.",
    "exif.png_settings": "The file's PNG text fields ({fields}) hold AI tool settings, but anyone can paste them into "
                         "any PNG.",
    # The detector (classifier)
    "classifier.no_cutoff": "A detector scored this {score} out of 1, but no cut-off is fixed yet, so the score is "
                            "for information only.",
    "classifier.above": "A detector scored this {score} out of 1, at or above our fixed cut-off of {cutoff}, but "
                        "detectors can be wrong.",
    "classifier.below": "A detector scored this {score} out of 1, below our fixed cut-off of {cutoff}, but many AI "
                        "images score low.",
    "classifier.gate_failed": "A detector scored this {score} out of 1, but our cut-off of {cutoff} failed its test, so "
                              "it is no concern.",
    "classifier.untested": "A detector scored this {score} out of 1, but our cut-off of {cutoff} has not passed its "
                           "test, so it is no concern.",
    "classifier.unclear": "A detector ran, but its score could not be read cleanly, so this card shows nothing either "
                          "way.",
    # Second Look's own watermark
    "watermark.present": "Second Look's own hidden mark is here ({matched} of {bits} bits match), but our demo key is "
                         "public, so anyone can add it.",
    "watermark.absent": "Second Look's own hidden mark is not here ({matched} of {bits} bits match), and that says "
                        "nothing about how this was made.",
    "watermark.unclear": "Second Look looked for its hidden mark, but the result could not be read, so this card "
                         "shows nothing.",
    # The ladder's own limit card on a can't-tell answer
    "limit.nothing": "Second Look ran {ran} and none raised a concern, but finding nothing is not evidence.",
    "limit.nothing_failed": "Second Look ran {ran} with no concern, and {failed} could not run, but finding nothing is "
                            "not evidence.",
    "limit.signed_gap": "Second Look ran {ran}: a signed record is here but says neither AI nor camera.",
    "limit.unreadable": "The file could not be read, so nothing was checked, and that says nothing about the image.",
    "limit.no_checks": "No checks are installed, so nothing was read, and that says nothing about the image.",
    "limit.all_failed": "No check could run on this file: {failed} failed, so nothing was read, and that says "
                        "nothing about the image.",
    # Claim receipts (typed claims and voice notes)
    "claim.unavailable": "The offline fact-check list could not be read, so nothing was looked up, and that says "
                         "nothing about the claim.",
    "claim.match.listed": "A rating published by {site}, on the IFCN list, is on a claim with {shared} of your "
                          "{typed} key words, but it may not be yours.",
    "claim.match.unlisted": "A site not on the IFCN list rated a claim sharing {shared} of your {typed} key words, but "
                            "it may not be yours.",
    "claim.match.listed_negation": "A rating published by {site}, on the IFCN list, is on a close claim, but a "
                                   "\"not\" differs, so it may be the opposite.",
    "claim.match.unlisted_negation": "A site not on the IFCN list rated a close claim, but a \"not\" differs, so it "
                                     "may be the opposite.",
    # The same four when a model chose the search words (raw["search_source"] == "model", M1): never "your key words"
    "claim.match.listed.model": "A rating published by {site}, on the IFCN list, is on a claim with {shared} of "
                                "{typed} {words}, so check them.",
    "claim.match.unlisted.model": "A site not on the IFCN list rated a claim with {shared} of {typed} {words}, so check "
                                  "them.",
    "claim.match.listed_negation.model": "A rating published by {site}, on the IFCN list, is on a claim like the "
                                         "{words}, but a \"not\" may flip its meaning.",
    "claim.match.unlisted_negation.model": "A site not on the IFCN list rated a claim like the {words}, but a \"not\" "
                                           "may flip its meaning.",
    "voice.how_made": "Second Look has no check for how a voice note was made, so it can't tell if the voice was "
                      "recorded, edited or made by AI.",
    "voice.transcript": "A speech model on this computer wrote down what it heard, but it makes mistakes and can't tell "
                        "who spoke.",
    "voice.no_speech": "The speech model heard no words, so nothing was looked up, and that says nothing about the "
                       "note.",
    "voice.no_claims": "No statement was long enough to look up, so no fact-check was searched for, and that says "
                       "nothing about what was said.",
    "voice.no_match": "No published fact-check close to the {claims} heard was found, but that does not mean no one "
                      "has checked them.",
    "voice.too_big": "The file is too big to read as a voice note, so nothing was looked up, and that says nothing "
                     "about the note.",
    "voice.too_long": "The note is longer than Second Look reads in full, so none of it was looked up, and that says "
                      "nothing about it.",
    "voice.unreadable": "The file could not be read as audio, so nothing was looked up, and that says nothing about "
                        "the note.",
    "voice.decoder_unavailable": "This computer can't decode this audio format, so nothing was looked up, and that "
                                 "says nothing about the note.",
    "voice.transcript_unavailable": "The speech model is not installed, so nothing was looked up, and that says "
                                    "nothing about the note.",
    "voice.transcript_failed": "Speech recognition did not finish, so nothing was looked up, and that says nothing "
                               "about the note.",
    # The caption check (X1, DAT-872)
    "context.date": "The caption's date is {days} off from the file's capture time, but file times can be changed, so "
                    "it is a reason to ask.",
    "context.place": "The caption's place is about {km} km from the file's GPS position, but GPS can be edited, so it "
                     "is a reason to ask.",
    "context.device": "The caption's camera maker differs from the file's EXIF maker, but EXIF can be edited, so it is "
                      "a reason to ask.",
    "context.agree.one": "The caption's {fields} matches the file's own facts, but those facts are unsigned, so they "
                         "don't show the caption fits.",
    "context.agree": "The caption's {fields} match the file's own facts, but those facts are unsigned, so they don't "
                     "show the caption fits.",
    "context.nothing": "Nothing in the caption could be compared with the file's own facts, so this check shows "
                       "nothing either way.",
    # The demo engine's canned cards
    "demo": "This is a demo card, not a reading of your image, so it shows nothing about it.",
    # Anything without its own template
    "unknown": "Second Look has no plain-language line for this card yet, so read the card itself.",
}

SUMMARIES: dict[str, str] = {
    "signed_ai": "A signed record from a listed signer says AI made this, but not who made it or why.",
    "signed_camera": "A signed record from a listed signer says a camera took this, but a scene can still be staged.",
    "concerns.one": "Second Look found 1 reason to look closer, but on its own it does not show how this was made.",
    "concerns": "Second Look found {reasons} to look closer, but no single one of them shows how this was made.",
    "cant_tell": "Second Look can't tell how this was made, and finding nothing is not evidence either way.",
    "cant_tell.signed_gap": "Second Look can't tell how this was made: a signed record is here but says neither AI "
                            "nor camera.",
    # A detector score at or above a cut-off that has not passed its test (DAT-850, design SHOULD 4 on PR #33):
    # the score raises no concern, but the summary names it, so a high score is never "finding nothing". Three
    # short sentences, ending on "not evidence either way" (a reviewer's SHOULD 1 and 2 on PR #39). A no-break space
    # joins "either" and "way", so a 390 px screen never leaves "way." alone on a line (PR #39, comment 6504292805).
    "cant_tell.gate_failed": "Second Look can't tell how this was made. A detector scored it {score} out of 1, at or "
                             "above our cut-off of {cutoff}. That cut-off failed its test, so we do not count the "
                             "score as evidence either\u00a0way.",
    "cant_tell.untested": "Second Look can't tell how this was made. A detector scored it {score} out of 1, at or "
                          "above our cut-off of {cutoff}. That cut-off has not passed its test, so we do not count "
                          "the score as evidence either\u00a0way.",
    "voice": "Second Look can't tell how this voice note was made, and any fact-check shown is the publisher's rating, "
             "not ours.",
    "claims": "Second Look gives no rating of its own. Any fact-check shown is the publisher's, on a claim that may "
              "differ from yours.",
    "demo": "This is a demo answer, not a reading of your image, so it shows nothing about how it was made.",
    "unknown": "Second Look's answer could not be read here, so read the cards themselves.",
}

# ------------------------------------------------------------------ closed vocabularies
CHECK_NAMES = {"c2pa": "C2PA", "iptc": "IPTC", "exif": "EXIF", "classifier": "detector", "watermark": "watermark",
               "claim": "fact-check", "context": "caption", "voice": "voice"}
CHECK_ORDER = ("c2pa", "iptc", "exif", "classifier", "watermark", "context", "claim", "voice")
TRUST_LISTS = {"official": "the C2PA Trust List", "interim": "the C2PA interim trust list"}
IPTC_AI_FIELDS = ("AISystemUsed", "AISystemVersionUsed", "AIPromptInformation", "AIPromptWriterName")
EXIF_CAMERA_FIELDS = (("make", "Make"), ("model", "Model"))
PNG_KEYS = ("parameters", "prompt", "workflow", "Dream", "sd-metadata", "invokeai_metadata")
CONTEXT_FIELDS = ("date", "place", "device")
# Where a model read the search words of a claim card from (raw["search_from"], set by glimmer.lookup_candidate).
MODEL_SEARCH_WORDS = {"caption": "words a model took from your caption", "image": "words a model took from your image"}
MODEL_SEARCH_WORDS_ANY = "words a model picked"
# One display name per pattern in rungs/_sources.AI_TOOL_PATTERNS (same order; a test keeps them in step).
AI_TOOL_NAMES = (
    (r"adobe[ _]firefly", "Adobe Firefly"),
    (r"dall[-·]?e(?: ?[23])?", "DALL-E"),
    (r"gpt-image(?:-\d+)?", "GPT Image"),
    (r"midjourney", "Midjourney"),
    (r"stable[ _-]?diffusion", "Stable Diffusion"),
    (r"imagen ?[234]", "Imagen"),
    (r"gemini(?: \d+(?:\.\d+)?)?(?: flash| pro)? image", "Gemini Image"),
    (r"flux\.1", "FLUX.1"),
    (r"bing image creator", "Bing Image Creator"),
    (r"microsoft designer", "Microsoft Designer"),
    (r"leonardo\.ai", "Leonardo.Ai"),
    (r"ideogram\.ai", "Ideogram"),
)
_AI_TOOL_RES = tuple((re.compile(p), name) for p, name in AI_TOOL_NAMES)
VOICE_STATUS = ("too_big", "too_long", "unreadable", "decoder_unavailable", "transcript_unavailable",
                "transcript_failed", "no_speech", "no_claims", "no_match")

# ------------------------------------------------------------------ checked slot values
def _num(v, lo: float, hi: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and lo <= f <= hi else None


def _int(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if lo <= v <= hi else None


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _checks(names) -> str | None:
    """'3 checks (C2PA, EXIF and the detector)' from rung names; None if the list is unusable."""
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return None
    known = [CHECK_NAMES[k] for k in CHECK_ORDER if k in names]
    other = len(set(names)) - len([k for k in CHECK_ORDER if k in names])
    if other:
        known.append(_count(other, "other", "others"))
    if not names:
        return "no checks"
    return f"{_count(len(set(names)), 'check', 'checks')} ({_join(known)})"


def _tool(name) -> str | None:
    if not isinstance(name, str):
        return None
    for rx, display in _AI_TOOL_RES:
        if rx.fullmatch(name.lower().replace("_", " ")) or rx.fullmatch(name.lower()):
            return display
    return None


def _site(raw: dict) -> str | None:
    """The IFCN-listed domain the publisher's host belongs to, from the fixed list; else None.

    No banned-word test here: the domain is printed only as "published by <domain>"."""
    from .rungs.factcheckers import DOMAINS

    host = raw.get("publisher")
    if not isinstance(host, str) or not host.isascii():
        return None
    parts = host.lower().split(".")
    for i in range(len(parts) - 1):
        dom = ".".join(parts[i:])
        if dom in DOMAINS and _PUBLISHED_BY_RE.fullmatch(f"published by {dom}"):
            return dom
    return None


# ------------------------------------------------------------------ choosing a template
def _c2pa(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    if "remote_manifest_url" in raw:
        return "c2pa.remote", {}
    if raw.get("valid") is None:
        return "unknown", {}
    if raw.get("valid") is not True:
        return "c2pa.invalid", {}
    terms = [t for t in raw.get("digital_source_types") or [] if isinstance(t, str)]
    tool = raw.get("ai_tool_name")
    # The same order as the rung's _classify: an AI source type wins, so a record that declares both
    # a capture and AI reads as AI-made, as its card does. Only a capture with an AI tool's name and
    # no AI term is the conflict.
    if raw.get("source_class") == "ai" or set(terms) & AI_SOURCE_TYPES:
        cls = "ai"
    elif tool and set(terms) & CAPTURE_SOURCE_TYPES:
        cls = "conflict"
    elif raw.get("source_class") == "camera":
        cls = "camera"
    else:
        cls = "other"
    if cls != "ai" and raw.get("ai_ingredient") is True:
        cls += "_ai_part"
    if _valid_c2pa(e) and raw.get("trusted_by") in TRUST_LISTS:
        return f"c2pa.trusted.{cls}", {"trust_list": TRUST_LISTS[raw["trusted_by"]]}
    return f"c2pa.untrusted.{cls}", {}


def _iptc(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    if raw.get("field") == "Iptc4xmpExt:DigitalSourceType":
        cls = raw.get("source_class")
        return (f"iptc.source.{cls}", {}) if cls in ("ai", "camera", "other") else ("unknown", {})
    fields = raw.get("fields")
    if isinstance(fields, dict):
        named = [f for f in IPTC_AI_FIELDS if f in fields]
        if named:
            return "iptc.ai_fields", {"fields": _join(named)}
    return "unknown", {}


def _exif(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    keys = raw.get("png_keys")
    if isinstance(keys, list) and keys:
        named = [k for k in PNG_KEYS if k in keys]
        return ("exif.png_settings", {"fields": _join(named)}) if named else ("unknown", {})
    if "ai_tool_name" in raw:
        tool = _tool(raw.get("ai_tool_name"))
        if tool:
            return "exif.software.ai_tool", {"tool": tool}
        return "exif.software.plain", {}
    if raw.get("source_class") == "camera":
        named = [label for key, label in EXIF_CAMERA_FIELDS if raw.get(key)]
        return ("exif.camera", {"fields": _join(named)}) if named else ("unknown", {})
    return "unknown", {}


def _classifier(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    score = _num(raw.get("score"), 0.0, 1.0)
    if score is None:
        return "classifier.unclear", {}
    if raw.get("threshold") is None:
        return ("classifier.no_cutoff", {"score": _score(score)}) if not raw.get("above_threshold") \
            else ("classifier.unclear", {})
    cutoff = _num(raw.get("threshold"), 0.0, 1.0)
    above = raw.get("above_threshold")
    if cutoff is None or not isinstance(above, bool) or above != (score >= cutoff):
        return "classifier.unclear", {}
    concern = raw.get("raises_concern")
    if raw.get("gate_result") != "PASS":
        # A frozen cut-off without a passing gate (DAT-850): the score is information only, so no side is stated.
        if concern is not False:
            return "classifier.unclear", {}
        key = "classifier.gate_failed" if raw.get("gate_result") == "FAIL" else "classifier.untested"
        cut = f"{cutoff:.3f}"
        # At or above the cut-off, the places that keep the shown score at or above the shown cut-off: the card and
        # the can't-tell summary (_score_only_summary) print the same number.
        return key, {"score": _score(score, cut, True) if above else _score(score), "cutoff": cut}
    if concern is not above:
        return "classifier.unclear", {}
    # Two places where that is enough; never let rounding put the shown score on the wrong side.
    cut = _places(cutoff, lambda s: float(s) == round(cutoff, 6))
    shown = _score(score, cut, above)
    if cut is None or shown is None:
        return "classifier.unclear", {}
    return ("classifier.above" if above else "classifier.below"), {"score": shown, "cutoff": cut}


def _score(score: float, cut: str | None = None, above: bool | None = None) -> str | None:
    """A score as a reader sees it: "under 0.01" and "over 0.99" at the ends, else two places, or
    more when two would put the shown score on the wrong side of the shown cut-off."""
    if cut is None and above is None:
        return "under 0.01" if score < 0.01 else "over 0.99" if score > 0.99 else f"{score:.2f}"
    if cut is None:
        return None
    if score < 0.01 and not above and float(cut) >= 0.01:
        return "under 0.01"
    if score > 0.99 and above and float(cut) <= 0.99:
        return "over 0.99"
    return _places(score, lambda s: (float(s) >= float(cut)) == above)


def _places(x: float, ok) -> str | None:
    """x with the fewest decimal places (2 to 6) that pass ok(), or None."""
    for p in range(2, 7):
        s = f"{x:.{p}f}"
        if ok(s):
            return s
    return None


def _watermark(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    bits = _int(raw.get("bits"), 1, 4096)
    got = _int(raw.get("matched_bits"), 0, bits) if bits else None
    need = _int(raw.get("min_match"), 0, bits) if bits else None
    present = raw.get("present")
    if got is None or need is None or not isinstance(present, bool) or present != (got >= need):
        return "watermark.unclear", {}
    return ("watermark.present" if present else "watermark.absent"), {"matched": str(got), "bits": str(bits)}


def _limit(e: Evidence, signed_present: bool) -> tuple[str, dict]:
    raw = e.raw
    ran, failed = raw.get("rungs_run"), raw.get("rungs_failed")
    ran_s, failed_s = _checks(ran), _checks(failed)
    if ran_s is None or failed_s is None:
        return "unknown", {}
    if raw.get("file_readable") is False and not signed_present:
        return "limit.unreadable", {}
    if not ran and not signed_present:
        # Installed checks that all failed are not "no checks".
        return ("limit.all_failed", {"failed": failed_s}) if failed else ("limit.no_checks", {})
    if signed_present:
        return "limit.signed_gap", {"ran": ran_s}
    if failed:
        return "limit.nothing_failed", {"ran": ran_s, "failed": failed_s}
    return "limit.nothing", {"ran": ran_s}


def _claim(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    kind = raw.get("kind")
    if raw.get("source") == "voice" and kind in ("how_made", "transcript") + VOICE_STATUS:
        if kind == "no_match":
            claims = raw.get("claims")
            n = len(claims) if isinstance(claims, list) else None
            if n is None or not 1 <= n <= 1000:
                return "unknown", {}
            return "voice.no_match", {"claims": _count(n, "claim", "claims")}
        return f"voice.{kind}", {}
    if raw.get("status") == "unavailable":
        return "claim.unavailable", {}
    if "is_listed_fact_checker" in raw:
        listed = raw.get("is_listed_fact_checker") is True
        site = _site(raw) if listed else None
        negation = raw.get("negation_differs") is True
        slots = {"site": site or ""} if listed else {}
        if listed and site is None:
            return "unknown", {}
        # The search words are the user's own unless a model chose them; then say so, and from what.
        model = raw.get("search_source") == "model"
        suffix = ".model" if model else ""
        if model:
            read_from = raw.get("search_from")
            slots["words"] = MODEL_SEARCH_WORDS.get(read_from, MODEL_SEARCH_WORDS_ANY) if isinstance(read_from, str) \
                else MODEL_SEARCH_WORDS_ANY
        if negation:
            return ("claim.match.listed_negation" if listed else "claim.match.unlisted_negation") + suffix, slots
        typed = _int(raw.get("typed_terms"), 1, 1000)
        shared = _int(raw.get("shared_terms"), 0, typed) if typed else None
        if typed is None or shared is None:
            return "unknown", {}
        slots |= {"shared": str(shared), "typed": str(typed)}
        return ("claim.match.listed" if listed else "claim.match.unlisted") + suffix, slots
    return "unknown", {}


def _context(e: Evidence) -> tuple[str, dict]:
    raw = e.raw
    if raw.get("contradiction") is True:
        fld, gap = raw.get("field"), raw.get("disagreement")
        if not isinstance(gap, dict) or fld not in CONTEXT_FIELDS:
            return "unknown", {}
        if fld == "date":
            days = _num(gap.get("days_apart"), 0.0, 1e6)
            if days is None:
                return "unknown", {}
            n = max(1, round(days))
            return "context.date", {"days": f"about {_count(n, 'day', 'days')}"}
        if fld == "place":
            km = _num(gap.get("distance_km"), 0.0, 25000.0)
            return ("context.place", {"km": f"{round(km):,}"}) if km is not None else ("unknown", {})
        return "context.device", {}
    if raw.get("contradiction") is False:
        checks = raw.get("checks")
        agreed = [c.get("field") for c in checks if isinstance(c, dict) and c.get("outcome") == "agree"] \
            if isinstance(checks, list) else []
        named = [f for f in CONTEXT_FIELDS if f in agreed]
        if raw.get("compared") is True and named:
            return ("context.agree.one" if len(named) == 1 else "context.agree"), {"fields": _join(named)}
        return "context.nothing", {}
    return "unknown", {}


def card_template(e: Evidence, signed_present: bool = False) -> tuple[str, dict]:
    """(TEMPLATES key, slot values) for one card. Never raises; anything odd is "unknown"."""
    try:
        if not isinstance(e, Evidence) or not isinstance(e.raw, dict):
            return "unknown", {}
        if e.raw.get("stub") is True:
            return "demo", {}
        if e.raw.get(LADDER_LIMIT_KEY) is True or (e.rung == "origin" and e.raw.get("limit") is True):
            return _limit(e, signed_present)
        pick = {"c2pa": _c2pa, "iptc": _iptc, "exif": _exif, "classifier": _classifier,
                "watermark": _watermark, "claim": _claim, "context": _context}.get(e.rung)
        return pick(e) if pick else ("unknown", {})
    except Exception:  # a malformed raw must never take the page down
        return "unknown", {}


def render(key: str, slots: dict, library: dict[str, str] = TEMPLATES) -> tuple[str, str]:
    """(key used, sentence) for one template. An unknown key, a missing slot, or a slot value that
    is not plain ASCII text or holds a banned word gives the library's "unknown" line instead."""
    tpl = library.get(key)
    try:
        if tpl is None:
            raise KeyError(key)
        used = set(re.findall(r"\{(\w+)\}", tpl))
        for name, v in ((n, slots[n]) for n in sorted(used)):
            if not isinstance(v, str) or not v.isascii():
                raise ValueError("bad slot")
            if banned_words(v) and not _published_site(name, v, tpl):
                raise ValueError("bad slot")
        text = tpl.format_map(dict(slots))
        if banned_outside_sites(text):   # defence in depth: the whole sentence, outside "published by"
            raise ValueError("banned word")
    except (KeyError, ValueError, IndexError, AttributeError, TypeError):
        return "unknown", library["unknown"]
    return key, text[0].upper() + text[1:]


def _published_site(name: str, value: str, tpl: str) -> bool:
    """A listed IFCN domain in the site slot of a template that prints it as "published by {site}"."""
    from .rungs.factcheckers import DOMAINS

    return name == "site" and value in DOMAINS and PUBLISHED_BY in tpl and tpl.count("{site}") == 1


def _summary(result: Result, cards: list[Evidence]) -> tuple[str, dict]:
    answer = getattr(result, "answer", None)
    if any(c.raw.get("stub") is True for c in cards):
        return "demo", {}
    if cards and all(c.rung == "claim" and c.raw.get("source") == "voice" for c in cards):
        return "voice", {}
    if cards and all(c.rung == "claim" for c in cards):
        return "claims", {}
    if answer in ("signed_ai", "signed_camera"):
        return answer, {}
    if answer == "concerns":
        n = sum(1 for c in cards if _is_concern(c))
        if n == 1:
            return "concerns.one", {}
        return ("concerns", {"reasons": f"{n} reasons"}) if n else ("unknown", {})
    if answer == "cant_tell":
        if any(_valid_c2pa(c) for c in cards):
            return "cant_tell.signed_gap", {}
        return _score_only_summary([c for c in cards if c.rung == "classifier"])
    return "unknown", {}


def _score_only_summary(detector: list[Evidence]) -> tuple[str, dict]:
    """The can't-tell summary, read from the detector cards' own rules (_classifier), not their stored flags.

    A can't-tell answer counted no concern, so each detector card must read cleanly as raising none. A card
    that doesn't (a flag that isn't the boolean its score and cut-off give, a concern raised, a score that
    can't be read) gets the "unknown" summary, never "finding nothing". A score at or above a cut-off without
    a passing test is named, with the card's own numbers."""
    read = [(_classifier(e), e.raw.get("above_threshold")) for e in detector]
    if any(key in ("classifier.unclear", "classifier.above") for (key, _), _ in read):
        return "unknown", {}
    for (key, slots), above in read:
        if key in ("classifier.gate_failed", "classifier.untested") and above is True:
            return "cant_tell." + key.split(".", 1)[1], slots
    return "cant_tell", {}


def explain(result: Result) -> Explanation:
    """The plain-language explanation of a Result, from the templates alone. Never raises: a card
    or a Result it can't read gets the "unknown" line, which sends the reader to the card's own text."""
    evidence = getattr(result, "evidence", None)
    evidence = evidence if isinstance(evidence, list) else []
    try:
        cards = [e for e in evidence if isinstance(e, Evidence) and isinstance(e.raw, dict)]
        signed_present = any(_valid_c2pa(c) for c in cards)
    except Exception:
        cards, signed_present = [], False
    per_card = [_card_line(e, signed_present) for e in evidence]
    try:
        skey, summary = render(*_summary(result, cards), library=SUMMARIES)
    except Exception:
        skey, summary = "unknown", SUMMARIES["unknown"]
    return Explanation(summary=summary, per_card=per_card, summary_template=skey)


def _card_line(e, signed_present: bool) -> CardLine:
    try:
        key, text = render(*card_template(e, signed_present))
    except Exception:
        key, text = "unknown", TEMPLATES["unknown"]
    rung = getattr(e, "rung", None) if isinstance(e, Evidence) else None
    return CardLine(rung=rung if isinstance(rung, str) and rung in CHECK_NAMES.keys() | {"origin"} else "unknown",
                    sentence=text, template=key)


def as_text(ex: Explanation) -> str:
    """The explanation as plain lines, for the command line."""
    lines = [ex.label + ":", ex.summary]
    lines += [f"- {c.sentence}" for c in ex.per_card]
    return "\n".join(lines)
