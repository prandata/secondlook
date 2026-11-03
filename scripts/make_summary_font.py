"""Make the heading font summary.html embeds (DAT-850 A271): the vendored Playfair Display (static/fonts/
PlayfairDisplay.ttf, SIL OFL 1.1) fixed at Bold (wght 700), cut to Latin, and saved as WOFF2.

A cut-down WOFF2 is a Modified Version under the OFL (OFL FAQ 2.5 and 2.6), and the font carries the Reserved
Font Name "Playfair Display" (OFL.txt line 1), so this copy is renamed "SL Title". Its copyright, licence and
licence URL name records (IDs 0, 13 and 14) are kept unchanged, and summary.html carries the full OFL text
beside it (batch.SUMMARY_FONT_NOTICE).

Needs fontTools and brotli, which the product itself does not: run it from a scratch venv.

    python -m venv /tmp/font-venv && /tmp/font-venv/bin/pip install fonttools brotli
    /tmp/font-venv/bin/python scripts/make_summary_font.py secondlook/fonts/sl-title-bold-latin.woff2
"""
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "secondlook/web/static/fonts/PlayfairDisplay.ttf"
OUT = ROOT / "secondlook/fonts/sl-title-bold-latin.woff2"
FAMILY = "SL Title"
# Basic Latin, Latin-1 Supplement, and the punctuation a folder or file name may carry: dashes, curly quotes,
# bullet, ellipsis, the euro sign and the trade mark sign.
UNICODES = "U+0020-007E,U+00A0-00FF,U+0131,U+0152-0153,U+2013-2014,U+2018-201E,U+2022,U+2026,U+20AC,U+2122"
# Name records kept unchanged: 0 copyright, 13 licence, 14 licence URL. The rest name the font, so they change.
KEEP = {0, 13, 14}


def build(out: Path) -> int:
    # Keep the source's own timestamp, so the same source always gives the same bytes.
    font = instantiateVariableFont(TTFont(str(SOURCE), recalcTimestamp=False), {"wght": 700})
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["kern", "liga", "calt", "ccmp", "locl", "mark", "mkmk"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    sub = subset.Subsetter(options)
    sub.populate(unicodes=subset.parse_unicodes(UNICODES))
    sub.subset(font)
    names = font["name"]
    for rec in list(names.names):
        if rec.nameID in KEEP:
            continue
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID in (2, 17):
            rec.string = "Bold"
        elif rec.nameID == 4:
            rec.string = f"{FAMILY} Bold"
        elif rec.nameID == 6:
            rec.string = "SLTitle-Bold"
        elif rec.nameID == 3:
            rec.string = "SLTitle-Bold;DAT-850 A271"
        else:
            names.removeNames(nameID=rec.nameID)
    out.parent.mkdir(parents=True, exist_ok=True)
    font.flavor = "woff2"
    font.recalcTimestamp = False
    font.save(str(out))
    return out.stat().st_size


def main(out: str) -> None:
    print(out, build(Path(out)), "bytes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(OUT))
