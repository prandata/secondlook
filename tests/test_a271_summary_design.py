"""DAT-850 A271: the design fix round on summary.html (a reviewer, PR #54 comment 6526032274, FIX @ 3edf83a).

1. The File column never collapses: fixed column widths, and a file name never breaks inside its extension.
2. The headings carry the pages' Playfair Display Bold inside the file, as a renamed Latin WOFF2 data: font.
3. The answer counts sit beside their labels (the counts table is capped at 360 px).
4. At 390 px a copy group reads "Copies: g1", not a bare g1.
5. Body and notes 16 px; hashes 13 px; nothing under 12 px.
"""
import base64
import re
import tomllib
from pathlib import Path

from secondlook import batch

ROOT = Path(batch.__file__).parent.parent
UNLISTED = ("The C2PA record is signed, but by a signer that is not on the trust list, so who made it "
            "cannot be confirmed.")


def page(files=None, **counts) -> str:
    files = files or [row("a.jpg", "cant_tell")]
    base = {"images": len(files), "checked": len(files), "skipped": 0, "failed": 0, "signed_ai": 0,
            "signed_camera": 0, "concerns": 0, "cant_tell": len(files), "near_duplicate_groups": 0}
    return batch._html({"folder": "claim-4471", "version": "abc123", "counts": base | counts, "files": files,
                        "skipped": [], "failed": [], "near_duplicate_groups": []})


def row(name, answer, reasons=(), group=None):
    return {"file": name, "answer": answer, "summary": f"Summary of {name}.", "reasons": list(reasons),
            "receipt_json": f"receipts/0000-{name}.json", "receipt_pdf": f"receipts/0000-{name}.pdf",
            "near_duplicate_group": group, "sha256": "ab" * 32}


def css(text: str) -> str:
    return re.search(r"<style>(.*)</style>", text, re.S).group(1)


def rules(style: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """Every (selector, declarations) in a style sheet, in order, with @media blocks opened and selector lists split."""
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    out, pos = [], 0
    while m := re.compile(r"\s*([^{}]+?)\s*\{").match(style, pos):
        depth, end = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(style[end], 0)
            end += 1
        body = style[m.end():end - 1]
        if m.group(1).startswith("@media"):
            out += rules(body)
        else:
            decls = [tuple(x.strip() for x in d.split(":", 1)) for d in body.split(";") if ":" in d]
            out += [(sel.strip(), decls) for sel in m.group(1).split(",")]
        pos = end
    return out


def last_value(style: str, selectors: set[str], prop: str) -> str | None:
    """The value the last rule for any of these selectors gives this property (later rules win)."""
    values = [v for sel, decls in rules(style) if sel in selectors for p, v in decls if p == prop]
    return values[-1] if values else None


# A file name or hash is cut off by any of these. summary.html sets none of them anywhere: every ancestor of the
# file and hash cells (body, main, the table, its rows) could clip them, so no rule may carry one.
CLIPPING = re.compile(r"overflow(-x|-y)?|text-overflow|(max-)?height|clip(-path)?|(-webkit-)?line-clamp|contain")


def clipping_rules(style: str) -> list[str]:
    return [f"{sel}{{{p}:{v}}}" for sel, decls in rules(style) for p, v in decls
            if CLIPPING.fullmatch(p) and not (p.startswith("overflow") and v == "visible")]


# Only the extension and the receipt links may refuse to wrap; nowrap on the file or hash cell would stop the wrap.
NOWRAP_ALLOWED = {".ext", "td.rc"}


# --- 1: the File column ------------------------------------------------------------------------

def test_files_table_has_fixed_column_widths_with_a_floor_for_the_file_name():
    text = page([row("03-ai-landscape-with-a-long-name.jpg", "concerns", [UNLISTED, UNLISTED])], concerns=1)
    assert "table.files{table-layout:fixed}" in text
    # 30% of the 976 px sheet is 292 px; from 761 to 900 px, 34% keeps it at 22 characters or more, and below
    # 760 px the rows stack (measured in headless Chromium, PR #54).
    assert "col.c-file{width:30%}" in text and "@media (max-width:900px){col.c-file{width:34%}}" in text
    cols = re.search(r'<table class="files"><colgroup>(.*?)</colgroup><tr class="head">', text).group(1)
    assert re.findall(r'<col class="([a-z-]+)">', cols) == ["c-file", "c-say", "c-rc", "c-dup"]


def test_a_long_file_name_wraps_and_nothing_clips_it():
    text = page([row("03-ai-landscape-with-a-long-name-and-no-spaces-at-all.jpg", "concerns", [UNLISTED])],
                concerns=1)
    style = css(text)
    assert '<td class="file">03-ai-landscape' in text
    # The fixed 30% column only holds a long name if the name may break anywhere inside its stem.
    assert last_value(style, {".file", "td.file"}, "overflow-wrap") == "anywhere", "file names must wrap"
    assert clipping_rules(style) == [], "nothing may clip or cap the height of a file name"
    nowrap = {sel for sel, decls in rules(style) for p, v in decls if p == "white-space" and v == "nowrap"}
    assert nowrap <= NOWRAP_ALLOWED, f"nowrap on {nowrap - NOWRAP_ALLOWED} would stop a file name or hash wrapping"


def test_a_file_name_never_breaks_inside_its_extension():
    text = page([row("03-ai-landscape.jpg", "cant_tell"), row("README", "cant_tell"), row(".hidden", "cant_tell")])
    assert '<td class="file">03-ai-landscape<span class="ext">.jpg</span></td>' in text
    assert '<td class="file">README</td>' in text and '<td class="file">.hidden</td>' in text
    assert ".ext{white-space:nowrap}" in text


def test_the_extension_split_still_escapes_both_parts():
    assert batch._file_name("a<b>.p&g") == 'a&lt;b&gt;<span class="ext">.p&amp;g</span>'


# --- 2: the heading font -----------------------------------------------------------------------

def test_headings_font_is_carried_inside_the_file_as_a_renamed_woff2():
    text = page()
    face = re.search(r"@font-face\{([^}]*)\}", text).group(1)
    assert 'font-family:"SL Title"' in face and "Playfair" not in face, "a cut-down copy may not use the RFN"
    data = base64.b64decode(re.search(r"url\(data:font/woff2;base64,([A-Za-z0-9+/=]+)\)", face).group(1))
    assert data == batch.SUMMARY_FONT.read_bytes() and data[:4] == b"wOF2"
    assert 15_000 <= len(data) <= 25_000, len(data)
    assert "font-src data:" in text and "h1{font:700 36px/1.1 var(--title)" in text


def test_the_page_carries_the_fonts_licence():
    text = page()
    notice = batch.SUMMARY_FONT_LICENCE.read_text(encoding="utf-8")
    # OFL.txt is the oracle, so first check it is the whole licence: the notice, every section, all five conditions.
    for part in ("Reserved Font Name \"Playfair Display\"", "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007",
                 "PREAMBLE", "DEFINITIONS", "PERMISSION & CONDITIONS", "Permission is hereby granted, free of charge",
                 "1) Neither", "2) Original", "3) No Modified", "4) The name(s)", "5) The Font Software",
                 "TERMINATION", "DISCLAIMER", "OTHER DEALINGS IN THE FONT SOFTWARE."):
        assert part in notice, part
    embedded = re.search(r"<style>/\* SL Title:[^\n]*\n(.*?)\*/", text, re.S).group(1)
    # The page writes "*/" as "* /" so the notice cannot end its own comment; OFL.txt has none, but match the page.
    assert " ".join(embedded.split()) == " ".join(notice.replace("*/", "* /").split()), \
        "the page must carry the complete OFL notice, word for word"
    assert text.index("SIL OPEN FONT LICENSE") < text.index("@font-face"), "the licence sits beside the font"


def test_the_font_ships_with_the_package():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    assert "fonts/*.woff2" in data["secondlook"] and batch.SUMMARY_FONT.is_file()


# --- 3: counts beside their labels -------------------------------------------------------------

def test_counts_table_is_capped_so_each_count_sits_by_its_label():
    assert "table.count{max-width:360px}" in page()


# --- 4: Copies at 390 px -----------------------------------------------------------------------

def test_a_copy_group_is_labelled_when_the_column_heads_are_hidden():
    text = page([row("a.jpg", "cant_tell", group="g1"), row("b.jpg", "cant_tell")])
    assert '<td class="dup">g1</td>' in text and '<td class="dup"></td>' in text
    narrow = re.search(r"@media \(max-width:760px\)\{(.*)\}\s*$", css(text), re.S).group(1)
    assert 'table.files td.dup:not(:empty)::before{content:"Copies: "' in narrow
    assert "table.files tr.head,table.files colgroup{display:none}" in narrow


# --- 5: type sizes -----------------------------------------------------------------------------

def test_body_and_notes_are_16px_hashes_13px_and_nothing_under_12px():
    style = batch.SUMMARY_CSS
    assert "body{margin:0;background:var(--cream);color:var(--ink);font:16px/1.55 var(--sans)}" in style
    assert not re.search(r"\.note\{[^}]*font-size", style), "notes take the 16 px body size"
    assert "td.hash{padding-top:0;font:13px/1.5 var(--mono)" in style
    sizes = [int(n) for n in re.findall(r"(?:font-size:|font:(?:\d00 )?)(\d+)px", style)]
    assert sizes and min(sizes) >= 12, sizes


def test_a_hash_wraps_on_its_full_width_line_and_nothing_clips_it():
    text = page()
    style = css(text)
    assert f'<td class="hash" colspan="4"><span>sha256</span>{"ab" * 32}</td>' in text
    # 64 hex digits at 13 px mono do not fit a 390 px phone on one line, and have no space to break at.
    assert last_value(style, {"td.hash", ".hash"}, "overflow-wrap") == "anywhere", "hashes must wrap"
    assert clipping_rules(style) == [], "nothing may clip or cap the height of a hash"
    hash_nowrap = [sel for sel, decls in rules(style) for p, v in decls
                   if p == "white-space" and v == "nowrap" and sel in {"td.hash", ".hash", "td", "table.files td"}]
    assert hash_nowrap == [], hash_nowrap
