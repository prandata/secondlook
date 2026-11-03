"""S2 fix round (DAT-847): one test per finding in a reviewer's review of 1a65967."""

import json
import time

import pytest

from secondlook import ladder
from secondlook.rungs import _sources, c2pa, exif, iptc
from tests.conftest import _sign, sample


# The SDK requires a source type on "created"; a URI outside the IPTC and
# c2pa.org vocabularies is one the rung reads as "no source type".
UNKNOWN_TYPE = "https://example.invalid/unknown"


def _answer(path):
    return ladder.run(path, rungs=[("c2pa", c2pa.check), ("iptc", iptc.check), ("exif", exif.check)]).answer


# --- finding 1: trust list loaded; an untrusted signer can't reach signed_* -----

def test_trust_list_is_pinned_and_recorded():
    import hashlib

    pem = (c2pa.TRUST_DIR / "C2PA-TRUST-LIST.pem").read_bytes()
    assert hashlib.sha256(pem).hexdigest() == c2pa.TRUST_LIST["sha256"]
    assert pem.count(b"BEGIN CERTIFICATE") >= 30
    assert "c2pa-org/conformance-public/" in c2pa.TRUST_LIST["source"] and c2pa.TRUST_LIST["date"]
    (e,) = c2pa.check(sample("py-C.jpg"))
    assert e.raw["trusted_by"] is None and e.raw["trust_list"] is None
    assert [t["id"] for t in e.raw["trust_lists"]] == ["official", "interim"]
    assert e.raw["trust_lists"][0]["sha256"] == c2pa.TRUST_LIST["sha256"]


def test_forged_camera_over_ai_is_not_signed_camera(tmp_path):
    """rev8_forge.py: re-sign an AI file as a 'Leica M11-P' capture with the public test cert."""
    ai = _sign(sample("py-A.jpg"), tmp_path / "ai.jpg", "trainedAlgorithmicMedia", "Leica M11-P")
    cam = _sign(ai, tmp_path / "cam-over-ai.jpg", "digitalCapture", "Leica M11-P")
    (e,) = c2pa.check(cam)
    assert e.raw["valid"] is True and e.raw["trusted"] is False
    assert e.strength == "metadata"
    assert _answer(cam) != "signed_camera"


@pytest.mark.parametrize(
    "name,cls,answer",
    [
        ("g22-pixel-capture.jpg", "camera", "signed_camera"),
        ("g22-photos-ai-edit.jpg", "ai", "signed_ai"),
        ("g22-ai-transcoded.jpg", "other", "concerns"),  # AI only in an ingredient's history
    ],
)
def test_trusted_public_samples(name, cls, answer):
    (e,) = c2pa.check(sample(name))
    assert e.raw["valid"] and e.raw["trusted"] and e.strength == "signed"
    assert e.raw["source_class"] == cls
    assert _answer(sample(name)) == answer


# --- finding 2: linear XMP scan ------------------------------------------------

def test_xmp_openers_scan_is_linear(tmp_path):
    p = tmp_path / "openers.jpg"
    p.write_bytes(b"\xff\xd8" + b"<x:xmpmeta " * 20_000)  # 220 KB, no closing tag
    t = time.perf_counter()
    assert iptc.check(str(p)) == []
    assert time.perf_counter() - t < 1.0


def test_xmp_scan_finds_packets_between_junk():
    pkt = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>'
    data = b"<x:xmpmetaZ" + pkt + b"junk" + pkt + b"<x:xmpmeta >"
    assert iptc._scan(data) == [pkt, pkt]


# --- finding 3: word-bounded, unambiguous AI tool names ------------------------

@pytest.mark.parametrize(
    "text,hit",
    [
        ("Editor de imagen", None),
        ("Gemini Photos", None),
        ("Flux Capacitor 2", None),
        ("OpenAI-compatible exporter", None),
        ("Meta AI glasses", None),
        ("Adobe Firefly", "adobe firefly"),
        ("DALL-E 3", "dall-e 3"),
        ("Midjourney v6", "midjourney"),
        ("Stable Diffusion XL", "stable diffusion"),
        ("Imagen 3", "imagen 3"),
        ("Gemini 2.5 Flash Image", "gemini 2.5 flash image"),
        ("FLUX.1 [dev]", "flux.1"),
        ("notmidjourneyx", None),
    ],
)
def test_ai_tool_names(text, hit):
    assert _sources.ai_tool_in(text) == hit


def test_gemini_photos_capture_is_not_ai(tmp_path, test_root_trusted):
    """rev8_cls.py: a declared digitalCapture is never AI evidence."""
    f = _sign(sample("py-A.jpg"), tmp_path / "g.jpg", "digitalCapture", "Gemini Photos")
    (e,) = c2pa.check(f)
    assert e.strength == "signed" and e.raw["source_class"] == "camera"
    assert "generative AI" not in e.finding
    assert _answer(f) == "signed_camera"


def test_capture_with_ai_tool_generator_conflicts(tmp_path, test_root_trusted):
    f = _sign(sample("py-A.jpg"), tmp_path / "ff.jpg", "digitalCapture", "Adobe Firefly")
    (e,) = c2pa.check(f)
    assert e.raw["source_class"] == "other" and e.raw["ai_label"] is True
    assert "conflict" in e.finding and "digitalCapture" in e.finding
    assert _answer(f) == "concerns"


def test_ai_tool_generator_without_source_type_is_ai(tmp_path, test_root_trusted):
    f = _sign(sample("py-A.jpg"), tmp_path / "fx.jpg", UNKNOWN_TYPE, "Adobe Firefly")
    (e,) = c2pa.check(f)
    assert e.raw["source_class"] == "ai" and "signing tool is called" in e.finding


def test_imagen_spanish_editor_is_not_an_ai_label(tmp_path):
    f = _sign(sample("py-A.jpg"), tmp_path / "es.jpg", UNKNOWN_TYPE, "Editor de imagen")
    (e,) = c2pa.check(f)
    assert e.raw["ai_tool_name"] is None and e.raw["source_class"] == "other"


def test_foreign_uri_is_not_an_iptc_term(tmp_path, test_root_trusted):
    f = _sign(sample("py-A.jpg"), tmp_path / "u.jpg", "https://example.invalid/digitalCapture", "X")
    (e,) = c2pa.check(f)
    assert e.raw["digital_source_types"] == [] and e.raw["source_class"] == "other"
    assert _sources.source_term("https://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture") == "digitalCapture"
    assert _sources.source_term("https://example.invalid/trainedAlgorithmicMedia") == ""


# --- finding 4: PNG keys need generator-shaped values --------------------------

def _png(tmp_path, **text):
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    for k, v in text.items():
        meta.add_text(k, v)
    p = tmp_path / "t.png"
    Image.new("RGB", (4, 4)).save(p, pnginfo=meta)
    return str(p)


@pytest.mark.parametrize(
    "text",
    [
        {"prompt": "Enter your name"},
        {"workflow": "manual photo retouch"},
        {"parameters": "GPSLatitude=51.5; GPSLongitude=-0.12"},
        {"prompt": json.dumps({"a": 1})},
        {"Dream": "a dream I had"},
    ],
)
def test_generic_png_text_is_not_an_ai_label(tmp_path, text):
    assert exif.check(_png(tmp_path, **text)) == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("parameters", "a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"),
        ("prompt", json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 1}}})),
        ("workflow", json.dumps({"nodes": [{"id": 3, "type": "KSampler"}], "links": []})),
        ("Dream", '"a cat" -s 50 -S 123 -W 512 -H 512 -C 7.5 -A k_lms'),
        ("sd-metadata", json.dumps({"model": "stable diffusion", "image": {"steps": 50, "seed": 1}})),
    ],
)
def test_generator_png_text_is_an_ai_label(tmp_path, key, value):
    (e,) = exif.check(_png(tmp_path, **{key: value}))
    assert e.raw["ai_label"] is True and e.raw["png_keys"] == [key]
    assert "png_excerpt" not in e.raw  # no prompt text copied out (it can carry coordinates)


# --- also: remote-only manifest reported; malformed store shapes contained ----

def test_remote_only_manifest_is_reported(tmp_path):
    import c2pa as sdk

    S = sample("py-es256_certs.pem")
    signer = sdk.Signer.from_info(
        sdk.C2paSignerInfo(b"es256", open(S, "rb").read(), open(sample("py-es256_private.key"), "rb").read(), None)
    )
    act = {"action": "c2pa.created", "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"}
    m = {"claim_generator_info": [{"name": "t", "version": "1"}], "assertions": [{"label": "c2pa.actions", "data": {"actions": [act]}}]}
    out = tmp_path / "remote.jpg"
    with sdk.Builder(json.dumps(m)) as b:
        b.set_no_embed()
        b.set_remote_url("http://127.0.0.1:9/m.c2pa")
        b.sign_file(sample("py-A.jpg"), str(out), signer)
    (e,) = c2pa.check(str(out))
    assert e.strength == "metadata" and e.raw["remote_manifest_url"] == "http://127.0.0.1:9/m.c2pa"
    assert e.raw["valid"] is None and "did not fetch" in e.finding
    assert _answer(str(out)) == "cant_tell"


@pytest.mark.parametrize("store", [[], {"active_manifest": "m", "manifests": {"m": {"assertions": [None]}}}, "x"])
def test_malformed_store_never_raises(monkeypatch, store):
    class R:
        def json(self):
            return json.dumps(store)

        def is_embedded(self):
            return True

        def close(self):
            pass

    monkeypatch.setattr(c2pa, "_reader", lambda path: (R(), "test"))
    assert isinstance(c2pa.check("whatever.jpg"), list)


# --- stage 3: official and interim lists, and which one trusted the signer ----

INTERIM_PHRASE = "on the C2PA interim trust list, which C2PA describes as temporary"


@pytest.fixture
def interim_dir(monkeypatch, tmp_path):
    """Point the rung at another interim folder for one test; caches cleared both ways."""

    def use(path):
        monkeypatch.setattr(c2pa, "INTERIM_DIR", path)
        c2pa._interim.cache_clear()
        c2pa._settings.cache_clear()

    yield use
    monkeypatch.undo()
    c2pa._interim.cache_clear()
    c2pa._settings.cache_clear()


def _need_interim():
    if not c2pa.trust_lists()[1]["loaded"]:
        pytest.skip("interim trust list missing: run scripts/fetch_trust.sh")


def test_both_lists_recorded_with_source_date_sha():
    _need_interim()
    import hashlib

    off, itl = c2pa.trust_lists()
    assert off["loaded"] and off["source"].startswith("https://") and off["date"] and len(off["sha256"]) == 64
    assert itl["loaded"] and itl["source"] == "https://contentcredentials.org/trust/" and itl["date"]
    for name, pin in itl["files"].items():
        assert hashlib.sha256((c2pa.INTERIM_DIR / name).read_bytes()).hexdigest() == pin


def test_expired_short_lived_cert_trusted_by_official_time_stamp():
    """Demo 02: with no list the signer's short-lived cert (2025-11-09 to 2026-02-06) reads as expired; the official list trusts
    the Google time stamp, so the cert is checked at signing time and the capture is trusted."""
    import c2pa as sdk

    path = sample("commons-pixel10pro-utah-sign.jpg")
    bare = sdk.Settings.from_dict({"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False, "verify_trust": False}})
    r = sdk.Reader.try_create(path, context=sdk.Context(settings=bare))
    st = json.loads(r.json())
    r.close()
    res = st["validation_results"]["activeManifest"]
    assert [x["code"] for x in res["failure"]] == ["signingCredential.expired"]
    assert "timeStamp.untrusted" in [x["code"] for x in res["informational"]]

    (e,) = c2pa.check(path)
    assert e.raw["trusted_by"] == "official" and e.raw["failures"] == [] and e.strength == "signed"
    assert "The signer is on the C2PA Trust List (2026-10-17)." in e.finding
    assert e.raw["trust_list"]["id"] == "official"
    assert _answer(path) == "signed_camera"


def test_interim_only_signer_is_named_as_interim():
    _need_interim()
    path = sample("commons-firefly-mountain-landscape.jpg")
    (e,) = c2pa.check(path)
    assert e.raw["trusted"] is True and e.raw["trusted_by"] == "interim" and e.strength == "signed"
    assert INTERIM_PHRASE in e.finding and e.raw["trust_list"]["id"] == "interim"
    assert e.raw["source_class"] == "ai"
    assert _answer(path) == "signed_ai"


def test_official_signer_never_reported_as_interim():
    (e,) = c2pa.check(sample("g22-pixel-capture.jpg"))
    assert e.raw["trusted_by"] == "official" and INTERIM_PHRASE not in e.finding


def test_without_interim_the_interim_signer_is_metadata(interim_dir, tmp_path):
    interim_dir(tmp_path / "absent")
    path = sample("commons-firefly-mountain-landscape.jpg")
    (e,) = c2pa.check(path)
    assert e.raw["valid"] is True and e.raw["trusted"] is False and e.raw["trusted_by"] is None
    assert e.strength == "metadata" and "interim trust list was not loaded" in e.finding
    assert e.raw["trust_lists"][1]["loaded"] is False and "not fetched" in e.raw["trust_lists"][1]["not_loaded"]
    assert _answer(path) == "concerns"  # an untrusted AI declaration is a concern, not signed_ai


def test_tampered_interim_file_is_not_loaded(interim_dir, tmp_path):
    _need_interim()
    d = tmp_path / "interim"
    d.mkdir()
    for name in c2pa.INTERIM_LIST["files"]:
        (d / name).write_bytes((c2pa.INTERIM_DIR / name).read_bytes())
    with open(d / "anchors.pem", "ab") as f:
        f.write(b"\n")
    interim_dir(d)
    assert "does not match its pinned sha256" in c2pa.trust_lists()[1]["not_loaded"]
    (e,) = c2pa.check(sample("commons-firefly-mountain-landscape.jpg"))
    assert e.raw["trusted_by"] is None and e.strength == "metadata"


def test_test_cert_is_on_neither_list(made):
    _need_interim()
    (e,) = c2pa.check(made["signed_ai"])
    assert e.raw["valid"] is True and e.raw["trusted_by"] is None and e.strength == "metadata"
    assert "or on the C2PA interim trust list." in e.finding
