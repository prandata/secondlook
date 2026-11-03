"""Known AI-generator labels (DAT-848): the curated lists the exif rung matches.

Two classes of field, two rules.

Caption-type fields (EXIF ImageDescription, UserComment, XPComment, Artist; XMP
dc:description, photoshop:Credit) hold free text that people write: "Foto dalle
Alpi", "Photo from a Midjourney workshop", "Not made with Midjourney". A product
name inside a sentence means nothing there, so these fields match only an exact
label phrase, the whole field and nothing else, that a real file carries
(OBSERVED) or a vendor or standards page documents (PRIMARY).

Tool-identity fields (EXIF Software, Make, Model; XMP xmp:CreatorTool) hold the
name of the program or device. There a bare product name counts, in the
product's own spelling, but only when the field is that name and nothing else
but a version or edition word ("Midjourney V6", "Stable Diffusion XL", "DALL-E
3"). A sentence that mentions a product ("Camera: Stable Diffusion is not
used", "ideogram of Chinese characters", "Imagen 2 de 10") is not a tool name.

Each entry's evidence says where the string comes from:
  OBSERVED  read in a file of the Community Forensics public evaluation set
            (heldout-v2, 28 Oct 2026), with the file count;
  PRIMARY   documented by the vendor or a standards body (URL and date);
  NAME      only the product's own name, matched in tool-identity fields only.

A label is plain text that anyone can add, copy or strip. A match raises
concerns and never gives a signed answer, and a missing label proves nothing.
Add an entry only for a string that is observed or documented (a NAME entry
only for a name that is unambiguous as a tool), and extend
tests/test_generator_labels.py with it. Left out on purpose: "Imagined with AI"
(a visible platform label, not file metadata), bare "Gemini", "Imagen",
"Leonardo", "Flux", "OpenAI" and "Meta AI" (ordinary words or names of other
things).
"""

import re
from typing import NamedTuple

CAPTION_FIELDS = (
    "ImageDescription", "UserComment", "XPComment", "Artist", "dc:description", "photoshop:Credit",
)
TOOL_FIELDS = ("Software", "Make", "Model", "xmp:CreatorTool")
MAX_FIELD_TEXT = 128  # a label or a tool name is short; bounds the regex


class Label(NamedTuple):
    pattern: str  # lowercase regex; the whole field must match
    generator: str  # the product name a card shows
    evidence: str  # OBSERVED / PRIMARY / NAME, then the source
    fields: tuple  # field names the pattern may match in


CAPTION_LABELS = (
    Label(
        r"made with openai dall-e",
        "OpenAI DALL-E",
        "OBSERVED: EXIF UserComment 'Made with OpenAI DALL-E' in 34 of 34 DALL-E 2 files of the "
        "Community Forensics public eval set (heldout-v2, 28 Oct 2026).",
        CAPTION_FIELDS,
    ),
    Label(
        r"made with google ai",
        "Google AI (Gemini)",
        "PRIMARY: IPTC, 'Google extends AI transparency based on IPTC standards', 28 Oct 2024, "
        "https://iptc.org/news/google-ai-transparency-based-on-iptc-standards/ : images made with "
        "Gemini carry the IPTC Credit property \"Made with Google AI\" (XMP photoshop:Credit).",
        ("photoshop:Credit",),
    ),
)

# Version and edition words a tool name may carry after the product name.
_QUALIFIERS = (
    r"(?:[ _.-]?(?:v?\d+(?:\.\d+)*[a-z]?|ai|xl|pro|flash|image|bot|turbo|ultra|fast|lightning|"
    r"preview|beta|large|medium|dev|schnell))*"
)

TOOL_LABELS = (
    Label(
        r"dall[-.·]e",
        "OpenAI DALL-E",
        "NAME: OpenAI's product name, with its hyphen or dot.",
        TOOL_FIELDS,
    ),
    Label(r"gpt-image", "OpenAI GPT Image", "NAME: OpenAI's image model name (gpt-image-1).", TOOL_FIELDS),
    Label(
        r"ideogram",
        "Ideogram",
        "OBSERVED: EXIF Make 'Ideogram AI' in 68 of 68 Ideogram files of the Community Forensics "
        "public eval set (heldout-v2, 28 Oct 2026). NAME for 'Ideogram' and 'ideogram.ai'.",
        TOOL_FIELDS,
    ),
    Label(r"midjourney", "Midjourney", "NAME: the product name.", TOOL_FIELDS),
    Label(r"stable[ _-]diffusion", "Stable Diffusion", "NAME: the model name.", TOOL_FIELDS),
    Label(r"adobe firefly", "Adobe Firefly", "NAME: Adobe's product name.", TOOL_FIELDS),
    # Point Grey and FLIR sell a "Firefly" camera line, so a bare "Firefly" is not read from Make or Model.
    Label(r"firefly", "Adobe Firefly", "NAME: Adobe's product name (Software and CreatorTool only).",
          ("Software", "xmp:CreatorTool")),
    Label(r"imagen \d+(?:\.\d+)*", "Google Imagen",
          "NAME: Google's Imagen model names, always numbered ('Imagen 3').", TOOL_FIELDS),
    Label(
        r"gemini(?: v?\d+(?:\.\d+)*)?(?: flash| pro)? image",
        "Google Gemini image model",
        "NAME: e.g. 'Gemini 2.5 Flash Image'. Bare 'Gemini' is not matched.",
        TOOL_FIELDS,
    ),
    Label(r"bing image creator", "Bing Image Creator", "NAME: Microsoft's product name.", TOOL_FIELDS),
    Label(r"microsoft designer", "Microsoft Designer", "NAME: Microsoft's product name.", TOOL_FIELDS),
    Label(r"leonardo(?:\.| )ai", "Leonardo.Ai", "NAME: the product name, with its '.Ai' or ' AI'.", TOOL_FIELDS),
    Label(r"flux\.1", "FLUX.1", "NAME: Black Forest Labs' model name.", TOOL_FIELDS),
)

GENERATOR_LABELS = CAPTION_LABELS + TOOL_LABELS

_CAPTION = tuple((re.compile(l.pattern), l) for l in CAPTION_LABELS)
_TOOL = tuple((re.compile(l.pattern + _QUALIFIERS), l) for l in TOOL_LABELS)


def generator_in(field: str, text) -> str | None:
    """The generator a field's text names under that field's rule, or None.

    Case-insensitive; the whole field must be the label (caption fields) or the
    tool name plus version words (tool-identity fields).
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not text or len(str(text)) > MAX_FIELD_TEXT:
        return None
    low = " ".join(str(text).lower().split())
    for rx, label in _CAPTION + _TOOL:
        if field in label.fields and rx.fullmatch(low):
            return label.generator
    return None
