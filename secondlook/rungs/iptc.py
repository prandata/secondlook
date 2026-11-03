"""IPTC rung: unsigned AI labels in a file's XMP metadata.

Reads the IPTC Photo Metadata properties that say how an image was made:
Iptc4xmpExt:DigitalSourceType and the 2025.1 AI properties AISystemUsed,
AISystemVersionUsed, AIPromptInformation, AIPromptWriterName
(https://iptc.org/std/photometadata/specification/IPTC-PhotoMetadata).

Python standard library only (xml.etree), plus Pillow (MIT-CMU) to reach XMP
stored in compressed PNG chunks. XMP is plain, unsigned text: these findings
are "metadata" strength and are never a verdict on their own.
"""

import logging
import xml.etree.ElementTree as ET

from secondlook.types import Evidence
from secondlook.rungs._sources import (
    AI_SOURCE_TYPES,
    CAPTURE_SOURCE_TYPES,
    source_term,
)

log = logging.getLogger(__name__)

NS_EXT = "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
AI_PROPS = ("AISystemUsed", "AISystemVersionUsed", "AIPromptInformation", "AIPromptWriterName")
MAX_READ = 64 * 1024 * 1024  # don't scan more than 64 MB of a file
MAX_PACKET = 4 * 1024 * 1024
MAX_PACKETS = 16
_OPEN, _CLOSE = b"<x:xmpmeta", b"</x:xmpmeta>"


def _scan(data: bytes) -> list[bytes]:
    """XMP packets in data, in one linear pass (bytes.find, no regex).

    Each opener is paired with the first closer after it, and the scan stops
    at the first opener with no closer, so a file of repeated openers costs
    one pass, not one pass per opener.
    """
    found: list[bytes] = []
    pos = 0
    while len(found) < MAX_PACKETS:
        start = data.find(_OPEN, pos)
        if start < 0:
            break
        after = data[start + len(_OPEN) : start + len(_OPEN) + 1]
        if after not in (b" ", b"\t", b"\r", b"\n", b">"):
            pos = start + len(_OPEN)
            continue
        end = data.find(_CLOSE, start)
        if end < 0:
            break
        end += len(_CLOSE)
        if end - start <= MAX_PACKET:
            found.append(data[start:end])
        pos = end
    return found


def _packets(path: str) -> list[bytes]:
    with open(path, "rb") as f:
        data = f.read(MAX_READ)
    found = _scan(data)
    if not found:  # PNG iTXt may be compressed; Pillow inflates it
        try:
            from PIL import Image

            with Image.open(path) as im:
                xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
            if isinstance(xmp, str):
                xmp = xmp.encode("utf-8")
            if xmp and len(xmp) <= MAX_PACKET:
                found.append(xmp)
        except Exception as e:
            log.debug("iptc: Pillow could not open %s: %s", path, e)
    return found


def _texts(el: ET.Element) -> list[str]:
    """Text of a simple, Alt, Bag or Seq XMP value."""
    items = [li.text.strip() for li in el.iter(f"{{{NS_RDF}}}li") if li.text and li.text.strip()]
    if items:
        return items
    res = el.get(f"{{{NS_RDF}}}resource")
    if res:
        return [res]
    return [el.text.strip()] if el.text and el.text.strip() else []


def _read(packet: bytes) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        root = ET.fromstring(packet)
    except ET.ParseError as e:
        log.info("iptc: unparsable XMP packet: %s", e)
        return out
    names = ("DigitalSourceType",) + AI_PROPS
    for desc in root.iter(f"{{{NS_RDF}}}Description"):
        for name in names:
            attr = desc.get(f"{{{NS_EXT}}}{name}")
            if attr:
                out.setdefault(name, []).append(attr.strip())
        for child in desc:
            for name in names:
                if child.tag == f"{{{NS_EXT}}}{name}":
                    out.setdefault(name, []).extend(_texts(child))
    return out


XMP_TEXT_FIELDS = (
    ("dc:description", "http://purl.org/dc/elements/1.1/", "description"),
    ("xmp:CreatorTool", "http://ns.adobe.com/xap/1.0/", "CreatorTool"),
    ("photoshop:Credit", "http://ns.adobe.com/photoshop/1.0/", "Credit"),
)


def xmp_texts(path: str) -> dict[str, list[str]]:
    """Free-text XMP fields a generator label can sit in, by prefixed name. {} on any error.

    Used by the exif rung's generator-label check (DAT-848); the text is matched
    there and never copied into a card.
    """
    out: dict[str, list[str]] = {}
    try:
        for packet in _packets(path):
            root = ET.fromstring(packet)
            for desc in root.iter(f"{{{NS_RDF}}}Description"):
                for label, ns, local in XMP_TEXT_FIELDS:
                    values = out.setdefault(label, [])
                    attr = desc.get(f"{{{ns}}}{local}")
                    if attr and attr.strip():
                        values.append(attr.strip())
                    for child in desc:
                        if child.tag == f"{{{ns}}}{local}":
                            values.extend(_texts(child))
    except Exception as e:
        log.info("iptc: could not read XMP text from %s: %s", path, e)
    return {k: v for k, v in out.items() if v}


def check(path: str) -> list[Evidence]:
    try:
        fields: dict[str, list[str]] = {}
        for packet in _packets(path):
            for k, values in _read(packet).items():
                seen = fields.setdefault(k, [])
                for v in values:
                    if v not in seen:
                        seen.append(v)
    except Exception as e:
        log.info("iptc: could not read %s: %s", path, e)
        return []
    if not fields:
        return []

    evidence = []
    terms = [source_term(t) for t in fields.get("DigitalSourceType", [])]
    if terms:
        ai = [t for t in terms if t in AI_SOURCE_TYPES]
        capture = [t for t in terms if t in CAPTURE_SOURCE_TYPES]
        label = "ai" if ai else "camera" if capture else "other"
        what = {
            "ai": "says it was made with generative AI",
            "camera": "says it was captured by a device",
            "other": "names a source type that is neither generative AI nor a camera capture",
        }[label]
        evidence.append(
            Evidence(
                rung="iptc",
                finding=f"Unsigned IPTC metadata {what} (DigitalSourceType: {', '.join(terms)}).",
                proves="A tool or person wrote this source label into the file's metadata.",
                does_not_prove=(
                    "That the label is true: XMP is unsigned and anyone can add, change or "
                    "remove it, and most platforms strip it on upload."
                ),
                strength="metadata",
                raw={
                    "field": "Iptc4xmpExt:DigitalSourceType",
                    "values": fields["DigitalSourceType"],
                    "source_class": label,
                    "ai_label": bool(ai),
                },
            )
        )

    ai_fields = {k: fields[k] for k in AI_PROPS if fields.get(k)}
    if ai_fields:
        system = ", ".join(ai_fields.get("AISystemUsed", [])) or "an AI system"
        evidence.append(
            Evidence(
                rung="iptc",
                finding=f"Unsigned IPTC metadata names the AI system used: {system}.",
                proves="A tool or person recorded AI use in the file's metadata.",
                does_not_prove=(
                    "That the record is true or complete; unsigned metadata can be written, "
                    "copied or removed by anyone."
                ),
                strength="metadata",
                raw={"fields": ai_fields, "ai_label": True},
            )
        )
    return evidence
