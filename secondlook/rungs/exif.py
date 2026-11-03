"""EXIF rung: camera and software fields, known AI-generator labels in EXIF and
XMP text fields (_generators.py), plus image-generator settings that some tools
write into PNG text chunks.

Library: Pillow (licence MIT-CMU, a permissive MIT-family licence). No
exiftool or exiv2 (both GPL-family). Location is never copied out: the rung
only records whether GPS fields exist.

Everything here is unsigned "metadata": EXIF can be written, copied between
files or stripped by anyone, so it describes the file and never settles
where the picture came from.
"""

import json
import logging
import re

from secondlook.types import Evidence
from secondlook.rungs._generators import generator_in
from secondlook.rungs.iptc import xmp_texts

log = logging.getLogger(__name__)

# EXIF tag ids (EXIF 2.32 / TIFF 6.0)
MAKE, MODEL, SOFTWARE, DATETIME = 0x010F, 0x0110, 0x0131, 0x0132
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
DATETIME_ORIGINAL, LENS_MODEL = 0x9003, 0xA434
IMAGE_DESCRIPTION, ARTIST, USER_COMMENT, XP_COMMENT = 0x010E, 0x013B, 0x9286, 0x9C9C

# EXIF text fields a generator label can sit in (DAT-848). XMP text fields are
# listed in iptc.XMP_TEXT_FIELDS.
LABEL_FIELDS = (
    ("ImageDescription", IMAGE_DESCRIPTION),
    ("Software", SOFTWARE),
    ("Artist", ARTIST),
    ("UserComment", USER_COMMENT),
    ("Make", MAKE),
    ("Model", MODEL),
    ("XPComment", XP_COMMENT),
)
MAX_LABEL_TEXT = 4096

# PNG text keys written by common local image generators:
# "parameters" (AUTOMATIC1111 / Forge WebUI), "prompt" and "workflow" (ComfyUI),
# "Dream", "sd-metadata" and "invokeai_metadata" (InvokeAI). Words like
# "prompt" and "workflow" are ordinary key names in other tools too, so a key
# counts only when its value has the shape that generator writes.
GENERATOR_PNG_KEYS = ("parameters", "prompt", "workflow", "Dream", "sd-metadata", "invokeai_metadata")
MAX_PNG_TEXT = 2 * 1024 * 1024
_A1111 = re.compile(r"\bSteps: \d+, Sampler: ")
_DREAM = re.compile(r"(?:^|\s)-s ?\d+\b.*(?:^|\s)-S ?\d+\b")
_INVOKE_KEYS = {"model", "model_weights", "generation_mode", "steps", "cfg_scale", "seed", "scheduler", "sampler", "positive_prompt", "image"}


def _json(v: str):
    try:
        return json.loads(v)
    except (ValueError, RecursionError):
        return None


def _generator_like(key: str, value) -> bool:
    """True when a PNG text value has the shape the named generator writes."""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if not isinstance(value, str) or len(value) > MAX_PNG_TEXT:
        return False
    if key == "parameters":
        return bool(_A1111.search(value))
    if key == "Dream":
        return bool(_DREAM.search(value[:4096]))  # a Dream line is short; bounds the regex
    doc = _json(value)
    if not isinstance(doc, dict):
        return False
    if key == "prompt":  # ComfyUI API graph: {"3": {"class_type": "KSampler", "inputs": {...}}}
        nodes = [n for n in doc.values() if isinstance(n, dict)]
        return bool(nodes) and all(isinstance(n.get("class_type"), str) for n in nodes)
    if key == "workflow":  # ComfyUI UI graph: {"nodes": [{"type": "KSampler", ...}], "links": [...]}
        nodes = doc.get("nodes")
        return (
            isinstance(nodes, list)
            and bool(nodes)
            and "links" in doc
            and all(isinstance(n, dict) and isinstance(n.get("type"), str) for n in nodes)
        )
    # sd-metadata, invokeai_metadata: InvokeAI settings JSON
    inner = doc.get("image") if isinstance(doc.get("image"), dict) else {}
    return len(_INVOKE_KEYS & (set(doc) | set(inner))) >= 2


def _text(tag: int, v) -> str:
    """An EXIF text value as str: UserComment's 8-byte charset prefix removed, XP* fields UTF-16LE."""
    if isinstance(v, (tuple, list)) and all(isinstance(x, int) and 0 <= x < 256 for x in v):
        v = bytes(v)
    if isinstance(v, bytes):
        if tag == XP_COMMENT:
            v = v.decode("utf-16-le", "replace")
        elif tag == USER_COMMENT:
            code, body = v[:8], v[8:]
            if code.startswith(b"UNICODE"):
                # byte order is the file's; take the reading with fewer non-Latin characters
                tries = [body.decode(enc, "replace") for enc in ("utf-16-le", "utf-16-be")]
                v = min(tries, key=lambda t: sum(ord(c) > 0x024F for c in t))
            elif code.startswith((b"ASCII", b"JIS")) or code == b"\x00" * 8:
                v = body.decode("utf-8", "replace")
            else:
                v = v.decode("utf-8", "replace")
        else:
            v = v.decode("utf-8", "replace")
    return str(v).replace("\x00", "").strip()[:MAX_LABEL_TEXT]


def _label_hits(exif, sub, path: str) -> list[tuple[str, str]]:
    """(field, generator) for each EXIF or XMP text field that carries a known AI-generator label.

    Caption fields match an exact label phrase and tool-identity fields a tool
    name (_generators.py). The text itself is matched and dropped: request ids
    and prompts can sit in these fields, so a card names the field and the
    generator only.
    """
    texts: list[tuple[str, str]] = []
    for name, tag in LABEL_FIELDS:
        v = exif.get(tag)
        if v in (None, "", b"") and tag == USER_COMMENT:
            v = sub.get(tag)  # UserComment normally lives in the Exif sub-IFD
        if v not in (None, "", b""):
            texts.append((name, _text(tag, v)))
    for name, values in xmp_texts(path).items():
        texts.extend((name, v) for v in values)
    hits: list[tuple[str, str]] = []
    for name, text in texts:
        generator = generator_in(name, text)
        if generator and (name, generator) not in hits:
            hits.append((name, generator))
    return hits


def _clean(v) -> str:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).replace("\x00", "").strip()[:200]


def check(path: str) -> list[Evidence]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            sub = exif.get_ifd(EXIF_IFD) if exif else {}
            gps = exif.get_ifd(GPS_IFD) if exif else {}
            info = dict(im.info)
            fmt = im.format
    except Exception as e:
        log.info("exif: could not open %s: %s", path, e)
        return []

    hits = _label_hits(exif, sub, path)
    ai_fields = {f for f, _ in hits}

    evidence = []
    # A field that matched a label is left out of raw: the card names the field and the generator only.
    make = "" if "Make" in ai_fields else _clean(exif.get(MAKE, ""))
    model = "" if "Model" in ai_fields else _clean(exif.get(MODEL, ""))
    software = "" if "Software" in ai_fields else _clean(exif.get(SOFTWARE, ""))
    taken = _clean(sub.get(DATETIME_ORIGINAL, "") or exif.get(DATETIME, ""))
    lens = _clean(sub.get(LENS_MODEL, ""))
    raw = {
        "format": fmt,
        "make": make or None,
        "model": model or None,
        "software": software or None,
        "datetime_original": taken or None,
        "lens": lens or None,
        "has_gps": bool(gps),
    }

    if (make or model) and not ai_fields & {"Make", "Model"}:  # "Ideogram AI" is not a camera
        camera = " ".join(x for x in (make, model if not model.startswith(make) else model[len(make):].strip()) if x)
        finding = f"EXIF names a camera: {camera}" + (f", dated {taken}" if taken else "") + "."
        evidence.append(
            Evidence(
                rung="exif",
                finding=finding,
                proves="The file carries camera fields, as a camera or an editor wrote them.",
                does_not_prove=(
                    "That this camera took this picture: EXIF is unsigned and can be copied "
                    "onto any image, including an AI-made one."
                ),
                strength="metadata",
                raw=dict(raw, source_class="camera"),
            )
        )

    if software:
        evidence.append(
            Evidence(
                rung="exif",
                finding=f"EXIF Software field names {software!r}.",
                proves="The last program to save the file wrote its name here.",
                does_not_prove=(
                    "How the image was made: editors overwrite this field and anyone can set it."
                ),
                strength="metadata",
                raw=dict(raw, ai_tool_name=None, ai_label=False),
            )
        )

    for field, generator in hits:
        evidence.append(
            Evidence(
                rung="exif",
                finding=f"The file's {field} names {generator}, an AI image tool.",
                proves="A tool or person wrote this generator's name into the file's metadata.",
                does_not_prove=(
                    "This label is unsigned: anyone can add or remove it, and it can't tell you "
                    "whether the picture was edited after, and a missing label proves nothing; "
                    "it is not proof that AI made the picture."
                ),
                strength="metadata",
                raw={"field": field, "generator": generator, "ai_tool_name": generator, "ai_label": True},
            )
        )

    keys = [k for k in GENERATOR_PNG_KEYS if k in info and _generator_like(k, info[k])]
    if fmt == "PNG" and keys:
        evidence.append(
            Evidence(
                rung="exif",
                finding=(
                    f"PNG text fields hold image-generator settings ({', '.join(keys)}), "
                    "as local AI image tools write them."
                ),
                proves="A generator-style settings block was saved into this file.",
                does_not_prove=(
                    "That this image is the one those settings made; text chunks are unsigned "
                    "and can be pasted into any PNG."
                ),
                strength="metadata",
                raw=dict(
                    raw,
                    png_keys=keys,
                    ai_label=True,
                ),
            )
        )
    return evidence
