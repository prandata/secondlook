"""Shared vocabulary for the provenance rungs (S2).

IPTC Digital Source Type terms, from the IPTC NewsCodes vocabulary
https://cv.iptc.org/newscodes/digitalsourcetype/ . A value counts only when
it is a URI in that vocabulary (http or https), or in the c2pa.org
digitalsourcetype namespace; a look-alike URI elsewhere is ignored.
"""

import re

# Made by a model trained on sampled content ("generative AI").
AI_SOURCE_TYPES = {
    "trainedAlgorithmicMedia",
    "compositeWithTrainedAlgorithmicMedia",
}

# Captured from the physical world by a device.
CAPTURE_SOURCE_TYPES = {
    "digitalCapture",
    "computationalCapture",
    "negativeFilm",
    "positiveFilm",
    "print",
}

_SOURCE_NAMESPACES = (
    "http://cv.iptc.org/newscodes/digitalsourcetype/",
    "https://cv.iptc.org/newscodes/digitalsourcetype/",
    "http://c2pa.org/digitalsourcetype/",
    "https://c2pa.org/digitalsourcetype/",
)

# Product names whose appearance in a claim generator or Software field we
# report. A name match is a lead, never proof: anyone can write these strings.
# Each pattern names one image-generator product unambiguously; bare common
# words ("imagen" is Spanish for "image", "gemini", "flux", "openai",
# "meta ai") are left out, so "Editor de imagen" or "Gemini Photos" don't match.
AI_TOOL_PATTERNS = (
    r"adobe[ _]firefly",
    r"dall[-·]?e(?: ?[23])?",
    r"gpt-image(?:-\d+)?",
    r"midjourney",
    r"stable[ _-]?diffusion",
    r"imagen ?[234]",
    r"gemini(?: \d+(?:\.\d+)?)?(?: flash| pro)? image",
    r"flux\.1",
    r"bing image creator",
    r"microsoft designer",
    r"leonardo\.ai",
    r"ideogram\.ai",
)


def source_term(uri: str | None) -> str:
    """'http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture' -> 'digitalCapture'.

    '' for anything outside the IPTC and c2pa.org digitalsourcetype namespaces.
    """
    if not uri:
        return ""
    s = str(uri).strip()
    for ns in _SOURCE_NAMESPACES:
        if s.startswith(ns):
            term = s[len(ns):].strip("/")
            return term if term and "/" not in term else ""
    return ""


_TOOL_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(AI_TOOL_PATTERNS) + r")(?![a-z0-9])")


def ai_tool_in(text: str | None) -> str | None:
    """The first AI image-tool name found in text as whole words (case-insensitive), or None."""
    if not text:
        return None
    m = _TOOL_RE.search(str(text).lower())
    return m.group(1) if m else None
