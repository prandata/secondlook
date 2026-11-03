import pytest

from secondlook.rungs import c2pa, exif, iptc
from secondlook.types import Evidence
from tests.conftest import sample

RUNGS = (c2pa, iptc, exif)
BANNED = ("detects ai", "accurate", "this is real")


def _all(path):
    return [e for m in RUNGS for e in m.check(path)]


def _well_formed(evs):
    for e in evs:
        assert isinstance(e, Evidence)
        assert e.finding and e.proves and e.does_not_prove
        text = f"{e.finding} {e.proves} {e.does_not_prove}".lower()
        assert not any(b in text for b in BANNED), text


# --- public samples ---------------------------------------------------------

def test_iptc_signed_sample():
    evs = _all(sample("iptc-board-signed.jpg"))
    _well_formed(evs)
    (c,) = [e for e in evs if e.rung == "c2pa"]
    # c2pa-python 0.38.2 reports claimSignature.mismatch on this file (recorded, not hidden)
    assert c.raw["valid"] is False and c.strength == "metadata"
    assert "claimSignature.mismatch" in c.raw["failures"]
    (i,) = [e for e in evs if e.rung == "iptc"]
    assert i.raw["source_class"] == "camera" and i.raw["ai_label"] is False
    assert any(e.rung == "exif" and e.raw.get("make") == "Apple" for e in evs)


def test_c2pa_valid_algorithmic_media_is_not_ai():
    (e,) = c2pa.check(sample("py-C.jpg"))
    # valid signature, but the c2pa test cert is not on the C2PA Trust List
    assert e.strength == "metadata" and e.raw["valid"] and not e.raw["trusted"]
    assert e.raw["digital_source_types"] == ["algorithmicMedia"]
    assert e.raw["source_class"] == "other"


def test_c2pa_valid_edit_chain():
    (e,) = c2pa.check(sample("org-adobe-CAI.jpg"))
    assert e.strength == "metadata" and e.raw["valid"] and e.raw["source_class"] == "other"
    assert e.raw["ingredient_count"] == 2


@pytest.mark.parametrize(
    "name,code",
    [("py-E-sig-CA.jpg", "claimSignature.mismatch"), ("py-XCA.jpg", "assertion.dataHash.mismatch")],
)
def test_c2pa_tampered_is_not_signed(name, code):
    (e,) = c2pa.check(sample(name))
    assert e.strength == "metadata" and not e.raw["valid"]
    assert code in e.raw["failures"]
    _well_formed([e])


def test_no_manifest():
    assert c2pa.check(sample("py-A.jpg")) == []


# --- files made at test time ------------------------------------------------

def test_signed_ai(made, test_root_trusted):
    (e,) = c2pa.check(made["signed_ai"])
    assert e.strength == "signed" and e.raw["trusted"] and e.raw["source_class"] == "ai"
    _well_formed([e])


def test_signed_camera(made, test_root_trusted):
    (e,) = c2pa.check(made["signed_camera"])
    assert e.strength == "signed" and e.raw["trusted"] and e.raw["source_class"] == "camera"
    assert e.raw["ai_ingredient"] is False


def test_untrusted_signer_is_never_signed(made):
    for key, cls in (("signed_ai", "ai"), ("signed_camera", "camera")):
        (e,) = c2pa.check(made[key])
        assert e.raw["valid"] and not e.raw["trusted"] and e.strength == "metadata"
        assert e.raw["source_class"] == cls
        assert "not on the C2PA Trust List" in e.finding
        _well_formed([e])


@pytest.mark.parametrize("name", ["iptc-board-signed.jpg", "py-C.jpg", "py-A.jpg"])
def test_stripped_copies_have_nothing(made, name):
    assert _all(made[f"stripped_{name}"]) == []


def test_xmp_ai_label(made):
    evs = iptc.check(made["xmp_ai"])
    _well_formed(evs)
    assert {e.raw.get("ai_label") for e in evs} == {True}
    assert any(e.raw.get("fields", {}).get("AISystemUsed") == ["ExampleGen"] for e in evs)
    assert all(e.strength == "metadata" for e in evs)


def test_png_generator_params(made):
    (e,) = exif.check(made["sd_png"])
    assert e.raw["ai_label"] and e.raw["png_keys"] == ["parameters"]


@pytest.mark.parametrize("key", ["junk", "empty", "missing"])
def test_bad_input_never_raises(made, key):
    assert _all(made[key]) == []


def test_no_gps_coordinates_leak():
    for e in exif.check(sample("iptc-board-signed.jpg")):
        assert set(e.raw) >= {"has_gps"}
        assert not any("GPS" in str(k) for k in e.raw if k != "has_gps")
