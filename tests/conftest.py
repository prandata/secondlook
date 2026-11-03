import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"  # the 9 demo samples
FIXTURES = ROOT / "data" / "test-fixtures"  # public test inputs; both folders are filled by scripts/fetch_samples.sh


def sample(name: str) -> str:
    for d in (FIXTURES, SAMPLES):
        if (d / name).is_file():
            return str(d / name)
    pytest.skip(f"{name} missing: run scripts/fetch_samples.sh")


def _sign(src: str, dest: Path, source_type: str | None, generator: str, actions=None) -> str:
    """Sign src with the c2pa-python test certificate (not on the C2PA Trust List).

    source_type: an IPTC term, a full URI (kept as is), or None for no source type.
    """
    import c2pa

    if actions is None:
        act = {"action": "c2pa.created"}
        if source_type:
            act["digitalSourceType"] = (
                source_type if "/" in source_type
                else f"http://cv.iptc.org/newscodes/digitalsourcetype/{source_type}"
            )
        actions = [act]
    manifest = {
        "claim_generator_info": [{"name": generator, "version": "0.0.1"}],
        "title": dest.name,
        "assertions": [{"label": "c2pa.actions", "data": {"actions": actions}}],
    }
    info = c2pa.C2paSignerInfo(
        b"es256",
        Path(sample("py-es256_certs.pem")).read_bytes(),
        Path(sample("py-es256_private.key")).read_bytes(),
        None,
    )
    signer = c2pa.Signer.from_info(info)
    with c2pa.Builder(json.dumps(manifest)) as b:
        b.sign_file(src, str(dest), signer)
    return str(dest)


@pytest.fixture(scope="session")
def made(tmp_path_factory):
    """Files made at test time: signed AI, signed camera, stripped copies."""
    from PIL import Image

    d = tmp_path_factory.mktemp("made")
    base = sample("py-A.jpg")  # unsigned Canon photo
    out = {
        "signed_ai": _sign(base, d / "signed-ai.jpg", "trainedAlgorithmicMedia", "secondlook-test"),
        "signed_camera": _sign(base, d / "signed-camera.jpg", "digitalCapture", "secondlook-test"),
    }
    for name in ("iptc-board-signed.jpg", "py-C.jpg", "py-A.jpg"):
        with Image.open(sample(name)) as im:
            p = d / f"stripped-{name}"
            im.convert("RGB").save(p, "JPEG", quality=90)  # Pillow drops EXIF/XMP/JUMBF unless asked
            out[f"stripped_{name}"] = str(p)
    png = d / "sd-params.png"
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("parameters", "a cat, Steps: 20, Sampler: Euler a, Seed: 1")
    Image.new("RGB", (8, 8)).save(png, pnginfo=meta)
    out["sd_png"] = str(png)
    xmp = d / "xmp-ai.jpg"
    Image.new("RGB", (8, 8)).save(
        xmp,
        "JPEG",
        xmp=(
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
            b'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia">'
            b"<Iptc4xmpExt:AISystemUsed>ExampleGen</Iptc4xmpExt:AISystemUsed>"
            b"</rdf:Description></rdf:RDF></x:xmpmeta>"
        ),
    )
    out["xmp_ai"] = str(xmp)
    junk = d / "junk.jpg"
    junk.write_bytes(os.urandom(2048))
    out["junk"] = str(junk)
    (d / "empty.jpg").write_bytes(b"")
    out["empty"] = str(d / "empty.jpg")
    out["missing"] = str(d / "does-not-exist.jpg")
    return out


@pytest.fixture
def test_root_trusted(monkeypatch):
    """Trust the c2pa-python test root, on top of the C2PA Trust List, for one test."""
    from secondlook.rungs import c2pa as rung

    settings = json.loads(Path(sample("py-trust_config_test_settings.json")).read_text())
    monkeypatch.setattr(rung, "EXTRA_ANCHORS", settings["trust"]["trust_anchors"])
