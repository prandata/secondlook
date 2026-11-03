"""Claim extraction: turn what a person says about a picture into ClaimFacts.

An Extractor is any callable from caption text to ClaimFacts. Two paths exist today:

- typed(): the person types the date, place and device themselves (CLI --claimed-date,
  --claimed-place, --claimed-device). source "typed".
- rule_based(): a small reader for captions (CLI --caption, web caption field). It takes only
  what it can read without guessing: the first full date with a year, a capitalised place after
  "in", "at", "near", "outside" or "from" (the place words in any case, "from" only when no other
  place word is followed by a place), and a device after "shot on", "taken with" and the like.
  source "model".

A local model extractor (Muse Glimmer, DAT-873) plugs in behind the same type.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Callable

from secondlook.types import ClaimFacts

Extractor = Callable[[str], ClaimFacts]

MONTHS = {m: i for i, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
    ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
    ("october", "oct"), ("november", "nov"), ("december", "dec")], start=1) for m in names}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY = re.compile(rf"\b{_DAY}\s+({_MON})\.?,?\s+(\d{{4}})\b", re.I)
_MDY = re.compile(rf"\b({_MON})\.?\s+{_DAY},?\s+(\d{{4}})\b", re.I)
_NUM = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
AMBIGUOUS = "ambiguous"

# A capitalised run after a place word: "in Paris", "in Paris, France", "near New York City".
_WORD = r"[A-Z][\w'’.-]*"
_JOIN = r"(?:de|del|da|do|di|du|la|le|upon|on|of|am)"
_PLACE = re.compile(rf"\b((?i:in|at|near|from|outside))\s+({_WORD}(?:\s+(?:{_JOIN}\s+)?{_WORD})*"
                    rf"(?:,\s*{_WORD}(?:\s+{_WORD})*)?)")
# Capitalised words that follow "in" and are not places.
_NOT_PLACES = {m.capitalize() for m in MONTHS} | {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "The", "A", "An",
    "This", "That", "My", "Our", "Their", "His", "Her", "Its", "January", "Spring", "Summer",
    "Autumn", "Winter", "AI", "Photoshop", "Lightroom", "Instagram", "Facebook", "TikTok", "X"}
# Words that end a device name: a place, a time or a joining word ("... iPhone 15 Pro outside the town hall",
# "... iPhone 15 Pro last night"). A device name never holds one of these.
_DEVICE_END = ("in", "at", "on", "by", "near", "from", "during", "outside", "inside", "around", "across",
               "beside", "behind", "along", "through", "within", "nearby", "last", "yesterday", "today",
               "tonight", "this", "earlier", "early", "late", "just", "after", "before", "while", "when",
               "then", "and", "but", "with", "for")
_DEVICE = re.compile(r"\b(?:shot|taken|captured|photographed|filmed|snapped|recorded)\s+(?:on|with)\s+"
                     r"(?:(?:a|an|my|the|his|her|their|our)\s+)?([\w][\w .+-]{1,40}?)"
                     rf"(?=[,.;:!?)\]]|\s+(?:{'|'.join(_DEVICE_END)})\b|$)", re.I)


def parse_date(text: str) -> str | None:
    """An ISO day or time ("2026-10-01", "2026-10-01T09:30+10:00") checked and returned as given."""
    text = (text or "").strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _day(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def ambiguous_date(caption: str) -> str | None:
    """The first all-number date in the caption that is a calendar day read day first and read
    month first ("03/04/2026"), as written, or None. Neither reading is kept as a fact."""
    for m in _NUM.finditer(caption or ""):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if _day(y, b, a) and _day(y, a, b):
            return m[0]
    return None


def find_date(caption: str) -> str | None:
    """The first full date (with a year) in the caption, as an ISO day. None when the first date is
    all numbers that read both ways, such as 03/04/2026: a later date is not taken in its place."""
    found = []
    for m in _ISO.finditer(caption):
        found.append((m.start(), _day(int(m[1]), int(m[2]), int(m[3]))))
    for m in _DMY.finditer(caption):
        found.append((m.start(), _day(int(m[3]), MONTHS[m[2].lower()], int(m[1]))))
    for m in _MDY.finditer(caption):
        found.append((m.start(), _day(int(m[3]), MONTHS[m[1].lower()], int(m[2]))))
    for m in _NUM.finditer(caption):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 >= b:      # 28/10/2026: day first
            found.append((m.start(), _day(y, b, a)))
        elif b > 12 >= a:    # 10/28/2026: month first
            found.append((m.start(), _day(y, a, b)))
        elif _day(y, b, a) and _day(y, a, b):
            found.append((m.start(), AMBIGUOUS))   # day first and month first both fit: not read
    found = sorted((pos, d) for pos, d in found if d)
    return found[0][1] if found and found[0][1] != AMBIGUOUS else None


def find_place(caption: str) -> str | None:
    """The first place after "in", "at", "near" or "outside"; failing those, after "from". "From"
    often says where someone comes from ("visitors from India in Sydney"), not where the picture was taken."""
    found = []
    for m in _PLACE.finditer(caption):
        words = m[2].rstrip(".").split()
        while words and words[0] in _NOT_PLACES:
            words = words[1:]
        if words and not _ISO.match(words[0]):
            found.append((m[1].lower() == "from", " ".join(words)))
    return min(found, key=lambda f: f[0])[1] if found else None


def find_device(caption: str) -> str | None:
    m = _DEVICE.search(caption)
    return m[1].strip() if m else None


def rule_based(caption: str) -> ClaimFacts:
    caption = caption or ""
    return ClaimFacts(date=find_date(caption), place=find_place(caption),
                      device=find_device(caption), source="model")


def typed(date: str | None = None, place: str | None = None, device: str | None = None) -> ClaimFacts:
    return ClaimFacts(date=parse_date(date) if date else None, place=(place or "").strip() or None,
                      device=(device or "").strip() or None, source="typed")


def merge(typed_facts: ClaimFacts | None, read: ClaimFacts | None) -> ClaimFacts | None:
    """Typed facts win field by field. The source is "typed" only when every fact given was typed."""
    if typed_facts is None or read is None:
        return typed_facts or read
    fields = ("date", "place", "device")
    out = {f: getattr(typed_facts, f) or getattr(read, f) for f in fields}
    from_read = any(getattr(typed_facts, f) is None and getattr(read, f) for f in fields)
    return ClaimFacts(**out, source="model" if from_read else "typed")
