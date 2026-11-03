"""DAT-848: known AI-generator labels in EXIF and XMP text fields.

Synthetic files only. A label raises concerns and never gives a signed answer.
Caption fields match only an exact observed or documented label phrase; tool-identity
fields match a tool name (see rungs/_generators.py).
"""

import re

import pytest
from PIL import Image

from secondlook.ladder import decide
from secondlook.rungs import _generators, exif, iptc

BANNED = ("detects ai", "accurate", "this is real", "authentic", "genuine")
REQ_ID = "OpenAI--req_0123456789abcdef0123456789abcdef"
IDS = {"ImageDescription": 0x010E, "Software": 0x0131, "Artist": 0x013B, "Make": 0x010F, "Model": 0x0110}
EXIF_IFD = 0x8769
USER_COMMENT = 0x9286
XP_COMMENT = 0x9C9C


def _jpeg(tmp_path, name, fields=None, user_comment=None, xp=None):
    ex = Image.Exif()
    for field, value in (fields or {}).items():
        ex[IDS[field]] = value
    if user_comment is not None:
        ex.get_ifd(EXIF_IFD)[USER_COMMENT] = user_comment
    if xp is not None:
        ex[XP_COMMENT] = xp.encode("utf-16-le") + b"\x00\x00"
    p = tmp_path / f"{name}.jpg"
    Image.new("RGB", (8, 8)).save(p, "JPEG", exif=ex)
    return str(p)


def _png_exif(tmp_path, name, tags):
    ex = Image.Exif()
    for tag, value in tags.items():
        ex[tag] = value
    p = tmp_path / f"{name}.png"
    Image.new("RGB", (8, 8)).save(p, exif=ex)
    return str(p)


def _xmp(tmp_path, name, body):
    p = tmp_path / f"{name}.jpg"
    Image.new("RGB", (8, 8)).save(
        p,
        "JPEG",
        xmp=(
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
            'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/">' + body + "</rdf:Description></rdf:RDF></x:xmpmeta>"
        ).encode(),
    )
    return str(p)


def _cards(path):
    return [e for e in exif.check(path) if e.raw.get("ai_label")]


DOES_NOT_PROVE = (
    "This label is unsigned: anyone can add or remove it, and it can't tell you "
    "whether the picture was edited after, and a missing label proves nothing; "
    "it is not proof that AI made the picture."
)


def _check_card(e, field, generator):
    assert e.rung == "exif" and e.strength == "metadata"
    assert e.finding == f"The file's {field} names {generator}, an AI image tool."
    assert e.does_not_prove == DOES_NOT_PROVE
    # raw holds the field name and the generator, never the field's text
    assert e.raw == {"field": field, "generator": generator, "ai_tool_name": generator, "ai_label": True}
    text = f"{e.finding} {e.proves} {e.does_not_prove}".lower()
    assert not any(b in text for b in BANNED), text
    assert "req_" not in repr(e) and "req_" not in text


EXIF_FIELDS = ("ImageDescription", "Software", "Artist", "Make", "Model")
XMP_FIELDS = (
    ("dc:description", '<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{}</rdf:li></rdf:Alt></dc:description>'),
    ("xmp:CreatorTool", "<xmp:CreatorTool>{}</xmp:CreatorTool>"),
    ("photoshop:Credit", "<photoshop:Credit>{}</photoshop:Credit>"),
)


def _everywhere(tmp_path, name, text):
    """Every file that carries text in each EXIF text field and each XMP text field."""
    paths = [_jpeg(tmp_path, f"{name}-{f}", {f: text}) for f in EXIF_FIELDS]
    paths.append(_jpeg(tmp_path, f"{name}-uc", user_comment=b"ASCII\x00\x00\x00" + text.encode()))
    paths.append(_jpeg(tmp_path, f"{name}-xp", xp=text))
    paths += [_xmp(tmp_path, f"{name}-{label}", tpl.format(text)) for label, tpl in XMP_FIELDS]
    return paths


# --- the observed Community Forensics DALL-E 2 shape ---------------------------

def test_dalle2_png_usercomment_and_request_id(tmp_path):
    p = _png_exif(tmp_path, "d2", {USER_COMMENT: "Made with OpenAI DALL-E", 37500: REQ_ID})
    (e,) = _cards(p)
    _check_card(e, "UserComment", "OpenAI DALL-E")
    assert REQ_ID not in repr(e)


def test_dalle_usercomment_with_charset_prefix_in_exif_ifd(tmp_path):
    p = _jpeg(tmp_path, "d3", user_comment=b"ASCII\x00\x00\x00Made with OpenAI DALL-E")
    (e,) = _cards(p)
    _check_card(e, "UserComment", "OpenAI DALL-E")


def test_usercomment_unicode_both_byte_orders(tmp_path):
    for enc in ("utf-16-le", "utf-16-be"):
        p = _jpeg(tmp_path, enc, user_comment=b"UNICODE\x00" + "Made with OpenAI DALL-E".encode(enc))
        (e,) = _cards(p)
        _check_card(e, "UserComment", "OpenAI DALL-E")


@pytest.mark.parametrize("field", ["ImageDescription", "Artist"])
def test_dalle_phrase_in_other_caption_fields(tmp_path, field):
    (e,) = _cards(_jpeg(tmp_path, "cap", {field: "MADE WITH OPENAI DALL-E"}))
    _check_card(e, field, "OpenAI DALL-E")


def test_xp_comment_label(tmp_path):
    (e,) = _cards(_jpeg(tmp_path, "xp", xp="Made with OpenAI DALL-E"))
    _check_card(e, "XPComment", "OpenAI DALL-E")


def test_dalle_phrase_in_xmp_description(tmp_path):
    (e,) = _cards(_xmp(tmp_path, "dcd", XMP_FIELDS[0][1].format("Made with OpenAI DALL-E")))
    _check_card(e, "dc:description", "OpenAI DALL-E")


# --- the observed Ideogram shape and the documented Google label ----------------

def test_ideogram_make_is_not_called_a_camera(tmp_path):
    evs = exif.check(_jpeg(tmp_path, "ide", {"Make": "Ideogram AI"}))
    assert [e.finding for e in evs] == ["The file's Make names Ideogram, an AI image tool."]
    _check_card(evs[0], "Make", "Ideogram")


def test_google_ai_credit_in_xmp_photoshop_credit(tmp_path):
    (e,) = _cards(_xmp(tmp_path, "g", XMP_FIELDS[2][1].format("Made with Google AI")))
    _check_card(e, "photoshop:Credit", "Google AI (Gemini)")


# --- tool-identity fields: a bare product name, in the product's own spelling -----

@pytest.mark.parametrize(
    "field,text,generator",
    [
        ("Make", "Ideogram AI", "Ideogram"),
        ("Make", "ideogram.ai", "Ideogram"),
        ("Model", "Ideogram", "Ideogram"),
        ("Software", "Midjourney", "Midjourney"),
        ("Software", "Midjourney V6.1", "Midjourney"),
        ("Software", "Stable Diffusion XL", "Stable Diffusion"),
        ("Software", "Stable Diffusion 1.5", "Stable Diffusion"),
        ("Software", "Adobe Firefly", "Adobe Firefly"),
        ("Software", "Firefly", "Adobe Firefly"),
        ("Software", "Imagen 3", "Google Imagen"),
        ("Software", "Gemini 2.5 Flash Image", "Google Gemini image model"),
        ("Software", "DALL-E 3", "OpenAI DALL-E"),
        ("Software", "DALL.E", "OpenAI DALL-E"),
        ("Software", "gpt-image-1", "OpenAI GPT Image"),
        ("Software", "Bing Image Creator", "Bing Image Creator"),
        ("Software", "Microsoft Designer", "Microsoft Designer"),
        ("Model", "Leonardo.Ai", "Leonardo.Ai"),
        ("Software", "FLUX.1 dev", "FLUX.1"),
    ],
)
def test_tool_identity_exif_fields(tmp_path, field, text, generator):
    (e,) = _cards(_jpeg(tmp_path, "x", {field: text}))
    _check_card(e, field, generator)


@pytest.mark.parametrize("text,generator", [("Adobe Firefly", "Adobe Firefly"), ("DALL·E 2", "OpenAI DALL-E")])
def test_tool_identity_xmp_creator_tool(tmp_path, text, generator):
    # XMP is UTF-8, so the interpunct spelling can only be written here (EXIF ASCII turns it into "?")
    (e,) = _cards(_xmp(tmp_path, "ct", XMP_FIELDS[1][1].format(text)))
    _check_card(e, "xmp:CreatorTool", generator)


def test_xmp_attribute_form(tmp_path):
    q = tmp_path / "attr.jpg"
    Image.new("RGB", (8, 8)).save(
        q,
        "JPEG",
        xmp=(
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="Midjourney"/>'
            b"</rdf:RDF></x:xmpmeta>"
        ),
    )
    (e,) = _cards(str(q))
    _check_card(e, "xmp:CreatorTool", "Midjourney")


def test_xmp_texts_ignores_junk(tmp_path):
    bad = tmp_path / "bad.jpg"
    Image.new("RGB", (8, 8)).save(bad, "JPEG", xmp=b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><oops></x:xmpmeta>')
    assert iptc.xmp_texts(str(bad)) == {}
    assert iptc.xmp_texts(str(tmp_path / "missing.jpg")) == {}


# --- negatives: ordinary captions and names are never labels ------------------------

REVIEWER_CASES = [
    "Foto dalle Alpi", "Dalle Nogare", "Imagen 2 de 10", "imagen2.jpg",
    "Photo from a Midjourney workshop", "Not made with Midjourney",
    "Camera: Stable Diffusion is not used",
]
ORDINARY = [
    "Leica", "Leica Camera AG", "Leica M10", "NIKON CORPORATION", "Canon", "Apple",
    "Adobe Photoshop 25.1", "Leonardo da Vinci", "Gemini Photos", "Editor de imagen",
    "notmidjourneyx", "Midjourneys", "unstable diffusions", "Dallas", "Dalles", "Idealogram",
    "ideogram of Chinese characters", "OpenAI-compatible exporter", "Meta AI glasses",
    "Imagined with AI", "Imagined without help", "Fluxx", "Bing", "made with love", "Dalle",
    "Imagen", "Gemini", "Leonardo",
]


@pytest.mark.parametrize("text", REVIEWER_CASES + ORDINARY)
def test_no_false_labels_in_any_field(tmp_path, text):
    for p in _everywhere(tmp_path, "neg", text):
        assert _cards(p) == [], (p, text)
    for field in _generators.CAPTION_FIELDS + _generators.TOOL_FIELDS:
        assert _generators.generator_in(field, text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Midjourney", "Stable Diffusion XL", "Ideogram AI", "Adobe Firefly", "Imagen 3", "DALL-E 3",
        "Made with Google AI", "Made with OpenAI DALL-E 3", "Made with OpenAI DALL-E and a private caption",
        f"Made with OpenAI DALL-E {REQ_ID}", "x " * 800 + "Midjourney",
    ],
)
def test_caption_fields_match_only_the_exact_observed_phrase(tmp_path, text):
    for field in ("ImageDescription", "Artist"):
        assert _cards(_jpeg(tmp_path, f"c-{field}", {field: text})) == []
    assert _cards(_jpeg(tmp_path, "c-uc", user_comment=b"ASCII\x00\x00\x00" + text.encode())) == []
    assert _cards(_jpeg(tmp_path, "c-xp", xp=text)) == []
    assert _cards(_xmp(tmp_path, "c-dc", XMP_FIELDS[0][1].format(text))) == []


@pytest.mark.parametrize("text", ["Made with OpenAI DALL-E", "Made with Google AI", "Midjourney is great"])
def test_labels_only_count_in_their_own_field_class(tmp_path, text):
    # a caption label in a tool-identity field, and a sentence about a product there, are not labels
    for field in ("Software", "Make", "Model"):
        assert _cards(_jpeg(tmp_path, f"t-{field}", {field: text})) == []
    assert _cards(_xmp(tmp_path, "t-ct", XMP_FIELDS[1][1].format(text))) == []


def test_google_credit_counts_only_in_photoshop_credit(tmp_path):
    assert _cards(_jpeg(tmp_path, "g1", {"ImageDescription": "Made with Google AI"})) == []
    assert _cards(_xmp(tmp_path, "g2", XMP_FIELDS[0][1].format("Made with Google AI"))) == []


def test_firefly_camera_line_is_not_a_label(tmp_path):
    for field in ("Make", "Model"):
        assert _cards(_jpeg(tmp_path, f"ff-{field}", {field: "Firefly"})) == []


def test_camera_stable_diffusion_sentence_in_every_field(tmp_path):
    for p in _everywhere(tmp_path, "cam", "Camera: Stable Diffusion is not used"):
        assert _cards(p) == []


def test_camera_still_a_camera(tmp_path):
    evs = exif.check(_jpeg(tmp_path, "leica", {"Make": "Leica Camera AG", "Model": "Leica M10"}))
    assert [e.raw.get("source_class") for e in evs] == ["camera"] and not _cards(str(tmp_path / "leica.jpg"))


def test_plain_software_card_keeps_working(tmp_path):
    (e,) = exif.check(_jpeg(tmp_path, "ps", {"Software": "Adobe Photoshop 25.1"}))
    assert e.raw["ai_label"] is False and "Photoshop" in e.finding


def test_a_caption_that_mentions_a_product_leaves_the_other_cards_alone(tmp_path):
    p = _jpeg(tmp_path, "cap", {"ImageDescription": "Photo from a Midjourney workshop", "Make": "Leica", "Model": "M10"})
    evs = exif.check(p)
    assert [e.raw.get("source_class") for e in evs] == ["camera"]
    assert evs[0].raw["make"] == "Leica" and "Midjourney" not in repr(evs)


# --- raw never carries the matched field's text -----------------------------------

def test_matched_field_text_is_not_in_other_cards_raw(tmp_path):
    p = _jpeg(tmp_path, "mix", {"Make": "Ideogram AI", "Software": "Adobe Photoshop 25.1"})
    evs = exif.check(p)
    card = [e for e in evs if e.raw.get("ai_label")]
    assert len(card) == 1
    _check_card(card[0], "Make", "Ideogram")
    other = [e for e in evs if not e.raw.get("ai_label")]
    assert len(other) == 1 and other[0].raw["make"] is None and "Ideogram" not in repr(other[0].raw)


def test_matched_software_text_is_not_in_raw(tmp_path):
    (e,) = exif.check(_jpeg(tmp_path, "sw", {"Software": "Stable Diffusion XL"}))
    _check_card(e, "Software", "Stable Diffusion")


# --- the ladder: concerns, never signed ------------------------------------------

def test_label_gives_concerns_never_signed(tmp_path):
    p = _png_exif(tmp_path, "ladder", {USER_COMMENT: "Made with OpenAI DALL-E"})
    answer, _reason = decide(exif.check(p))
    assert answer == "concerns"


def test_reviewer_captions_give_no_concerns(tmp_path):
    for text in REVIEWER_CASES:
        answer, _reason = decide(exif.check(_jpeg(tmp_path, "lad", {"ImageDescription": text})))
        assert answer != "concerns", text


# --- the list itself --------------------------------------------------------------

def test_every_entry_has_pattern_generator_and_sourced_evidence():
    assert _generators.CAPTION_LABELS and _generators.TOOL_LABELS
    for label in _generators.GENERATOR_LABELS:
        assert label.pattern == label.pattern.lower() and label.generator and label.fields
        assert label.evidence.split(":")[0] in {"OBSERVED", "PRIMARY", "NAME"}
    for label in _generators.CAPTION_LABELS:  # caption fields: observed or documented only
        kind = label.evidence.split(":")[0]
        assert kind in {"OBSERVED", "PRIMARY"} and set(label.fields) <= set(_generators.CAPTION_FIELDS)
        if kind == "OBSERVED":
            assert re.search(r"\d+ of \d+", label.evidence)
        else:
            assert "https://" in label.evidence and re.search(r"20\d\d", label.evidence)
    for label in _generators.TOOL_LABELS:  # bare product names: tool-identity fields only
        assert set(label.fields) <= set(_generators.TOOL_FIELDS)
    names = {g for l in _generators.GENERATOR_LABELS for g in [l.generator]}
    for required in ("OpenAI DALL-E", "Ideogram", "Midjourney", "Stable Diffusion", "Adobe Firefly",
                     "Bing Image Creator", "Leonardo.Ai", "Google Imagen", "Google AI (Gemini)"):
        assert required in names
    assert not any("meta ai" in g.lower() for g in names)  # "Imagined with AI" is a platform label, not metadata
