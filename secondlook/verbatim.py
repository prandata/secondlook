"""Verbatim checks for anything a model takes from a caption (DAT-869, the project lead decision after
a reviewer's FAIL at ea8bd19).

The model may only choose text that is already in the caption. Code checks the choice and
refuses anything else; nothing a model rewrote is ever kept. Two shapes:

- whole clauses, for the S6 claim (split_clauses, whole_clause): the caption is cut at . ! ? ;
  and line breaks, and a chosen clause must be one of those pieces exactly. Negation, subject and
  numbers are then the caption's own, by construction.
- short values, for ClaimFacts (verbatim_value, verbatim_facts): a date as written, a place
  name, a device. Each must be an exact substring of the caption, on word boundaries, inside one
  clause. Code parses the value afterwards (for a date: secondlook.extract.find_date on X1).

What counts as a boundary:
- ";" and a line break always end a clause.
- A run of ".", "!" or "?" (with any closing quotes or brackets after it) ends a clause when
  whitespace or the end of the text follows. So "3.5" and "example.com" are not cut.
- A "." after an initialism, a common short title or a month's short form is not a boundary:
  "U.S. Pentagon", "Dr. Smith", "e.g. this", "Sept. 30, 2024". Not cutting keeps more of the caption together, the safe side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A run of sentence punctuation, its closing quotes or brackets, then whitespace or the end.
_STOP_RUN = re.compile(r"[.!?]+[\"'”’)\]]*(?=\s|$)")
_END_RUN = re.compile(r"[.!?]+[\"'”’)\]]*$")
_HARD = re.compile(r"[;\n]")
_INITIALISM = re.compile(r"(?:\b[A-Za-z]\.)+$")
_TITLES = frozenset("mr mrs ms dr st jr sr prof vs etc gen sen rep gov lt col sgt capt inc ltd co corp".split()
                    # month short forms (M2, DAT-873): "Sept. 30, 2024" is one date, not two clauses
                    + "jan feb mar apr jun jul aug sep sept oct nov dec".split())
_LAST_WORD = re.compile(r"(\w+)$")
_TRIM = " \t\"'“”‘’([{"


@dataclass(frozen=True)
class Clause:
    text: str      # exactly as in the caption (whitespace collapsed to single spaces)
    start: int     # offsets of text in the caption the splitter was given
    end: int


def normalise(text: str) -> str:
    """Control characters become spaces and runs of spaces collapse; line breaks are kept (they
    are clause boundaries). Nothing else changes, so every clause is still the caption's text."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c if c.isprintable() or c == "\n" else " " for c in text)
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r" *\n *", "\n", text).strip()


def _boundaries(text: str) -> list[tuple[int, int]]:
    """(start, end) of every boundary mark in text, in order."""
    marks = [(m.start(), m.end()) for m in _HARD.finditer(text)]
    for m in _STOP_RUN.finditer(text):
        before = text[:m.start() + 1]
        if text[m.start()] == "." and m.group().count(".") == 1:
            word = _LAST_WORD.search(before[:-1])
            if _INITIALISM.search(before) or (word and word.group(1).lower() in _TITLES):
                continue
        marks.append((m.start(), m.end()))
    return sorted(marks)


def split_clauses(text: str) -> list[Clause]:
    """The text's clauses, in order, without their boundary marks. Empty pieces are dropped."""
    out, pos = [], 0
    for b_start, b_end in _boundaries(text) + [(len(text), len(text))]:
        if b_start < pos:
            continue
        piece = text[pos:b_start]
        lead = len(piece) - len(piece.lstrip())
        stripped = piece.strip()
        if stripped:
            out.append(Clause(stripped, pos + lead, pos + lead + len(stripped)))
        pos = b_end
    return out


def whole_clause(choice: str, clauses: list[Clause]) -> Clause | None:
    """The clause that `choice` is, exactly, or None. Surrounding whitespace and a trailing
    boundary mark are allowed (the model may copy the full stop too); nothing else is."""
    choice = choice.strip()
    m = _END_RUN.search(choice)
    if m:
        choice = choice[:m.start()].rstrip()
    choice = choice.rstrip(";").rstrip()
    for c in clauses:
        if c.text == choice:
            return c
    return None


def verbatim_value(value: str, caption: str) -> str | None:
    """`value` if it appears in the caption exactly (same letters, same case), starting and
    ending on word boundaries and inside one clause; otherwise None. Outer spaces and quotes
    around the value are ignored. This is the gate for a model's ClaimFacts values: a date, a
    place or a device the caption does not spell out is never kept."""
    if not isinstance(value, str) or not isinstance(caption, str):
        return None
    value = value.strip(_TRIM).strip()
    if not value or "\n" in value:
        return None
    clauses = split_clauses(normalise(caption))
    for c in clauses:
        start = c.text.find(value)
        while start != -1:
            end = start + len(value)
            left_ok = start == 0 or not (c.text[start - 1].isalnum() or c.text[start - 1] == "_")
            right_ok = end == len(c.text) or not (c.text[end].isalnum() or c.text[end] == "_")
            if left_ok and right_ok:
                return value
            start = c.text.find(value, start + 1)
    return None


FACT_FIELDS = ("date_text", "place", "device")


def verbatim_facts(caption: str, values: dict) -> tuple[dict[str, str | None], list[str]]:
    """Check a model's ClaimFacts reading field by field: {"date_text", "place", "device"}.

    Returns (kept, problems). A field the model left empty stays None with no problem. A field
    whose value is not verbatim in the caption is dropped (None) and named in problems; the model's
    value is never repaired. Unknown keys are refused too. M2 (DAT-873) parses what is kept:
    date_text through secondlook.extract.find_date, place and device as written."""
    kept: dict[str, str | None] = {f: None for f in FACT_FIELDS}
    problems: list[str] = []
    if not isinstance(values, dict):
        return kept, ["reply is not an object of facts"]
    for key in sorted(set(values) - set(FACT_FIELDS)):
        problems.append(f"unknown field: {key}")
    for f in FACT_FIELDS:
        v = values.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        found = verbatim_value(v, caption) if isinstance(v, str) else None
        if found is None:
            problems.append(f"{f} is not written in the caption")
        kept[f] = found
    return kept, problems
