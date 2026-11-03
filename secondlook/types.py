# secondlook/types.py
from dataclasses import dataclass, field
from typing import Literal

Answer = Literal["signed_ai", "signed_camera", "concerns", "cant_tell"]

@dataclass
class Evidence:
    rung: Literal["c2pa", "iptc", "exif", "watermark", "classifier", "origin", "claim", "context"]
    finding: str            # one plain sentence, e.g. "C2PA manifest signed by Adobe Firefly, 2026-10-01"
    proves: str             # what this does establish
    does_not_prove: str     # its limit, always filled
    strength: Literal["signed", "metadata", "signal", "none"]
    raw: dict = field(default_factory=dict)   # machine detail (scores, claim generator, cert issuer)

@dataclass
class Result:
    answer: Answer
    evidence: list[Evidence]
    summary: str            # 1 to 2 sentences; never "this is real"
    version: str            # git sha of secondlook

@dataclass
class ClaimFacts:
    """What a caption says about the picture: when, where, and on what device.

    date: ISO 8601, a day ("2026-10-01") or a time ("2026-10-01T09:30:00+10:00").
    place: a place name as written ("Paris, France"). device: as written ("iPhone 15 Pro").
    source: "typed" when the person typed the facts, "model" when software read them from
    the caption (the rule-based reader in secondlook.extract now, a local model later).
    """
    date: str | None
    place: str | None
    device: str | None
    source: Literal["typed", "model"]

@dataclass
class Context:
    """Optional input to the ladder about what is claimed of the picture. Rungs that don't take
    it are called exactly as before."""
    caption: str | None = None
    claims: list[str] = field(default_factory=list)
    facts: ClaimFacts | None = None


class NotInstalled(RuntimeError):
    """A rung can't run because a file or library it needs is not on this computer (its weights, say).
    The ladder names it "not installed" on the page (W11 stage 2, S-b)."""


class FailedToLoad(RuntimeError):
    """A rung's model or data is on this computer but would not load (a wrong sha256, a broken file).
    The ladder names it "failed to load" on the page (W11 stage 2, S-b)."""
