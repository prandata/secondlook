"""Stub engine for the S5 page (DAT-850), used only behind the --stub flag.

It reads nothing from the image: it picks a canned Result from a keyword in
the file name, so the page can show each of the four answers. Every Evidence
is marked raw["stub"] = True, every summary starts with DEMO_MARK, and the
server adds "demo": true to the result.

File name keyword -> answer:
    "signed-ai" or "signed_ai"         -> signed_ai
    "camera"                           -> signed_camera
    "concern" or "label"               -> concerns
    anything else                      -> cant_tell
"""
from __future__ import annotations

from pathlib import Path

from secondlook.types import Evidence, Result

STUB = {"stub": True}
DEMO_MARK = "Demo answer (stub rungs, chosen by file name, your image was not read): "


def _signed_ai() -> Result:
    return Result(
        answer="signed_ai",
        evidence=[
            Evidence(
                rung="c2pa",
                finding="C2PA manifest signed by Example Generator (stub), 2026-10-01, "
                "with DigitalSourceType trainedAlgorithmicMedia.",
                proves="The signer stated this file was made by a generative AI tool, "
                "and the file has not changed since it was signed.",
                does_not_prove="Whether the scene shown is true, or who first shared it. "
                "Copies with the manifest stripped would carry no such record.",
                strength="signed",
                raw={**STUB, "claim_generator": "Example Generator/1.0 (stub)"},
            ),
        ],
        summary="A valid signed record says an AI tool made this image. "
        "That tells you how it was made, not whether what it shows is true.",
        version="stub",
    )


def _signed_camera() -> Result:
    return Result(
        answer="signed_camera",
        evidence=[
            Evidence(
                rung="c2pa",
                finding="C2PA manifest signed by Example Camera Co. (stub), 2026-09-12, "
                "with a capture action and no AI ingredient.",
                proves="The signer stated a camera captured this file, "
                "and the file has not changed since it was signed.",
                does_not_prove="That the scene was not staged, or that the caption around it "
                "is true. A camera can photograph a screen or a printed fake.",
                strength="signed",
                raw={**STUB, "action": "c2pa.created", "source": "digitalCapture"},
            ),
            Evidence(
                rung="exif",
                finding="EXIF names a camera: Example Model X (stub), dated 2026:09:12 08:14:03.",
                proves="The file's unsigned metadata names a camera and a date.",
                does_not_prove="Anything on its own: EXIF can be edited or copied "
                "onto any image.",
                strength="metadata",
                raw={**STUB},
            ),
        ],
        summary="A valid signed record says a camera captured this image. "
        "That is a record of capture, not a guarantee that what it shows is true.",
        version="stub",
    )


def _concerns() -> Result:
    return Result(
        answer="concerns",
        evidence=[
            Evidence(
                rung="iptc",
                finding="Unsigned IPTC metadata says DigitalSourceType: "
                "trainedAlgorithmicMedia (stub).",
                proves="Someone or some tool labelled this file as AI-made in its metadata.",
                does_not_prove="Who wrote the label, or that it is right. Unsigned "
                "metadata can be added or removed by anyone.",
                strength="metadata",
                raw={**STUB},
            ),
            Evidence(
                rung="classifier",
                finding="The classifier score is 0.91 against a threshold of 0.80 (stub numbers).",
                proves="The image shares patterns with AI-made images in the classifier's "
                "training data.",
                does_not_prove="That this image was made by AI. Classifiers make mistakes, "
                "more often on compressed copies and on kinds of image they have not seen.",
                strength="signal",
                raw={**STUB, "score": 0.91, "threshold": 0.80},
            ),
        ],
        summary="We found reasons for concern, listed below. "
        "None of them is a signed record, so treat them as reasons to look closer, not a verdict.",
        version="stub",
    )


def _cant_tell() -> Result:
    return Result(
        answer="cant_tell",
        evidence=[
            Evidence(
                rung="c2pa",
                finding="No C2PA manifest found (stub).",
                proves="This copy carries no signed provenance record.",
                does_not_prove="That the image was made by a camera or by AI. Most images have no record, "
                "and many sites strip it on upload.",
                strength="none",
                raw={**STUB},
            ),
        ],
        summary="We can't tell how this image was made. "
        "That is an honest answer: nothing here is signed, and no signal crossed our threshold.",
        version="stub",
    )


def _pick(name: str) -> Result:
    if "signed-ai" in name or "signed_ai" in name:
        return _signed_ai()
    if "camera" in name:
        return _signed_camera()
    if "concern" in name or "label" in name:
        return _concerns()
    return _cant_tell()


def check(path: str) -> Result:
    """Return a canned Result chosen by a keyword in the file name, marked as a demo."""
    result = _pick(Path(path).name.lower())
    result.summary = DEMO_MARK + result.summary
    return result
