"""The "Try a sample" gallery: the nine N1 demo samples (DAT-849).

The files are not in this repo. scripts/fetch_samples.sh downloads them into
samples/ (01 to 05) and makes the rest from those (06 to 09), each checked by sha256,
and the page lists them only when the server is started with --samples DIR or
SECONDLOOK_SAMPLES=DIR.

Every entry below is copied from the DEMO-SAMPLES MANIFEST.md that a worker built for
lane N1 (28 Oct 2026). A file is listed only when its sha256 matches the manifest, so
the credit and licence shown always belong to the exact bytes being checked. "kind" says what the file
is according to its source; the answer on the page comes from the ladder.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("secondlook.web.samples")


@dataclass(frozen=True)
class Sample:
    id: str
    file: str
    title: str
    kind: str
    credit: str
    licence: str
    source: str
    sha256: str


_COMMONS = "https://commons.wikimedia.org/wiki/File:"
_ADOBE_CREDIT = "Adobe Firefly Image 3, prompted by Jiří Vedral, via Wikimedia Commons"

CATALOGUE: tuple[Sample, ...] = (
    Sample("01", "01-camera-pixel10pro-atlantic-sign.jpg", "Atlantic town sign",
           "Camera capture, signed by a Pixel 10 Pro",
           "Mark Clifton (WashuOtaku), via Flickr and Wikimedia Commons", "CC BY-SA 4.0",
           _COMMONS + "260906-0903_Atlantic.jpg",
           "12583a7485499524f93c0f06b9a74cd3d5f7fdb38841e3cfc930695a8b08977a"),
    Sample("02", "02-camera-pixel10pro-utah-sign.jpg", "Utah welcome sign",
           "Camera capture, signed by a Pixel 10 Pro",
           "Bobjgalindo, own work, via Wikimedia Commons", "CC BY 4.0",
           _COMMONS + "A_welcome_sign_Idaho_Utah_border.jpg",
           "fbb1bb7183dc018e092ec3dda7a565543e605f25fe78d21818dbf61a888800d5"),
    Sample("03", "03-ai-firefly-mountain-landscape.jpg", "Mountain landscape",
           "AI-made, signed by Adobe Firefly", _ADOBE_CREDIT, "Public domain",
           _COMMONS + "Adobe_Firefly_generated_landscape_photography.jpg",
           "4413739275382578d694dd02817c79c3714dafc6e5aa77ae8e80f59161618338"),
    Sample("04", "04-ai-firefly-typography-logo.jpg", "Typography logo",
           "AI-made, signed by Adobe Firefly", _ADOBE_CREDIT, "Public domain",
           _COMMONS + "Adobe_Firefly_generated_typography_logo.jpg",
           "5550fa2572d435234d6a750ce93b21a711cccd9c650dd1cdd6d400fb1351b3b6"),
    Sample("05", "05-ai-edit-photoshop-firefly-bentley.jpg", "1927 Bentley",
           "Camera photo retouched with Firefly in Photoshop, signed",
           "Thilo Parg, own work and Adobe Firefly, via Wikimedia Commons", "CC BY-SA 4.0",
           _COMMONS + "Bentley_4.5_Litre_1927_YU_5837_2a.jpg",
           "cb5a122e070e7601b222b3ffb39d5914e4db27d1dcb94d4c4a623e0f6adca8af"),
    Sample("06", "06-stripped-pixel10pro-atlantic-sign.jpg", "Atlantic sign, stripped",
           "Copy of 01 with its record removed, as many sites do on upload",
           "Mark Clifton (WashuOtaku); re-encoded and stripped by Second Look", "CC BY-SA 4.0",
           _COMMONS + "260906-0903_Atlantic.jpg",
           "135f4578afd3a0fcd3c1d0f254d42a0d66e5a733954b913f39e2a8974522257b"),
    Sample("07", "07-stripped-firefly-mountain-landscape.jpg", "Mountain landscape, stripped",
           "Copy of 03 with its record removed, as many sites do on upload",
           _ADOBE_CREDIT + "; stripped by Second Look", "Public domain",
           _COMMONS + "Adobe_Firefly_generated_landscape_photography.jpg",
           "e7828da31f47369aeaa12e62227f1f37ff9779ba54e747cce02fef87e82502b6"),
    Sample("08", "08-iptc-ai-label-firefly-mountain-landscape.jpg", "Mountain landscape, AI label",
           "Copy of 07 with an unsigned IPTC AI label we added",
           _ADOBE_CREDIT + "; label added by Second Look", "Public domain",
           _COMMONS + "Adobe_Firefly_generated_landscape_photography.jpg",
           "70f5070988eeca4669ca22f512e531938e5ad54b0cc6fc4d2001e53a289d5936"),
    Sample("09", "09-iptc-ai-label-firefly-typography-logo.jpg", "Typography logo, AI label",
           "Stripped copy of 04 with an unsigned IPTC AI label we added",
           _ADOBE_CREDIT + "; label added by Second Look", "Public domain",
           _COMMONS + "Adobe_Firefly_generated_typography_logo.jpg",
           "52d331a1b22c9444241a4b28714614ca7b48578e7edad83215661d68ead44f50"),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def samples_dir_from_env() -> str | None:
    return os.environ.get("SECONDLOOK_SAMPLES", "").strip() or None


def available(folder: str | os.PathLike | None, catalogue=None) -> dict[str, tuple[Sample, Path]]:
    """The catalogue entries whose file is in `folder` with the manifest's sha256, keyed by id."""
    if not folder:
        return {}
    if catalogue is None:
        catalogue = CATALOGUE
    root = Path(folder).expanduser()
    found: dict[str, tuple[Sample, Path]] = {}
    for s in catalogue:
        path = root / s.file
        try:
            if path.is_file() and _sha256(path) == s.sha256:
                found[s.id] = (s, path)
            else:
                log.warning("sample %s skipped: missing, or not the file in the manifest", s.file)
        except OSError as exc:
            log.warning("sample %s skipped: %s", s.file, exc)
    return found


def public(s: Sample) -> dict:
    """What the page sees: everything but the local path."""
    d = asdict(s)
    d["image"] = f"/samples/{s.id}/image"
    return d
