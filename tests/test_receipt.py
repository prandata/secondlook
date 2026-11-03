"""Evidence receipt (R1, DAT-870): schema, hashes, JSON, PDF, CLI and web. No network."""
import asyncio
import hashlib
import io
import json
import re
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from secondlook import cli, ladder, receipt
from secondlook.explain import explain
from secondlook.rungs import voice
from secondlook.receipt import LIMITS, SCHEMA, build, canonical_json, write_json, write_pdf
from secondlook.types import Evidence, Result
from secondlook.web.app import MAX_REQUEST, create_app
from tests.conftest import sample

ROOT = Path(__file__).resolve().parent.parent
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)
CARD_KEYS = {"index", "rung", "finding", "proves", "does_not_prove", "strength", "raw", "raw_dropped"}
TOP_KEYS = {"schema", "created_at", "tool", "versions", "file", "answer", "summary", "demo", "evidence",
            "inputs", "limits"}


def make_result(version="abc1234") -> Result:
    return Result(
        answer="concerns",
        evidence=[
            Evidence("iptc", "An AI label in the XMP says trainedAlgorithmicMedia.",
                     "The file carries an AI label.",
                     "Anyone can write or remove this label, so it says nothing about how the image was made.",
                     "metadata", {"ai_label": True, "digital_source_type": "trainedAlgorithmicMedia"}),
            Evidence("classifier", "Classifier score 0.91, at or above the frozen threshold 0.80.",
                     "A signal that the image looks like the AI images the model was tuned on.",
                     "It can be wrong on generators it never saw, after compression, and on edited photos.",
                     "signal", {"score": 0.91, "above_threshold": True}),
            Evidence("origin", "Checked: iptc, classifier.",
                     "Only that those checks ran.",
                     "Not finding a record does not show the image is unaltered or camera-made.",
                     "none", {"limit": True}),
        ],
        summary="2 concern(s) found; see the reasons. None of them is proof on its own.",
        version=version,
    )


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "photo.png"
    p.write_bytes(PNG)
    return p


def squash(text: str) -> str:
    """Remove all whitespace: the PDF wraps lines, and a long word may be split."""
    return re.sub(r"\s+", "", text)


def pdf_text(path) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(str(path)).pages)


# --- build: schema, fields, hashes ------------------------------------------------------------

def test_receipt_has_the_schema_and_every_field(image):
    result = make_result()
    r = build(result, image)
    assert set(r) == TOP_KEYS
    assert r["schema"] == SCHEMA == "second-look-receipt/1"
    assert r["tool"] == {"name": "secondlook", "version": "abc1234"}
    # The plain-language summary the page shows, not the ladder's own line (DAT-850).
    assert r["answer"] == "concerns" and r["summary"] == explain(result).summary != result.summary
    assert r["inputs"] == {} and r["demo"] is False
    assert r["limits"] == LIMITS and LIMITS.startswith("Second Look never says an image is real")
    created = datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - created).total_seconds()) < 60
    v = r["versions"]
    assert v["python"].startswith("3.") and v["package"] and "reportlab" in v["libraries"]


def test_every_card_is_kept_with_all_its_fields(image):
    result = make_result()
    r = build(result, image)
    assert len(r["evidence"]) == len(result.evidence)
    for i, (card, ev) in enumerate(zip(r["evidence"], result.evidence), start=1):
        assert set(card) == CARD_KEYS and card["index"] == i
        for key in ("rung", "finding", "proves", "does_not_prove", "strength"):
            assert card[key] == getattr(ev, key)
        assert card["raw"] == ev.raw and card["raw_dropped"] == []


def test_file_name_size_and_sha256_are_correct(tmp_path):
    data = b"some image bytes " * 100_000   # more than one hashing chunk
    f = tmp_path / "Holiday 1.jpg"
    f.write_bytes(data)
    r = build(make_result(), f)
    assert r["file"] == {"name": "Holiday 1.jpg", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_file_name_is_a_base_name_not_a_path(image):
    assert build(make_result(), image)["file"]["name"] == "photo.png"
    assert str(image.parent) not in json.dumps(build(make_result(), image))


def test_unreadable_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build(make_result(), tmp_path / "gone.jpg")


def test_inputs_are_recorded_as_given(image):
    r = build(make_result(), image, inputs={"caption": "Flood on Main Street, 3 Oct", "claims": ["a", "b"]})
    assert r["inputs"] == {"caption": "Flood on Main Street, 3 Oct", "claims": ["a", "b"]}


def test_large_blobs_are_dropped_with_a_descriptor(image):
    blob = bytes(range(256)) * 40
    long_text = "x" * (receipt.MAX_TEXT + 1)
    result = make_result()
    result.evidence[0].raw = {"thumbnail": blob, "xmp": long_text, "nested": {"more": [b"ab", "ok"]}, "score": 0.5}
    r = build(result, image)
    raw = r["evidence"][0]["raw"]
    assert raw["thumbnail"] == {"dropped": "bytes", "length": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    assert raw["xmp"]["dropped"] == "text" and raw["xmp"]["length"] == len(long_text)
    assert raw["xmp"]["sha256"] == hashlib.sha256(long_text.encode()).hexdigest() and raw["xmp"]["head"] == "x" * 200
    assert raw["nested"]["more"][0]["dropped"] == "bytes" and raw["nested"]["more"][1] == "ok"
    assert raw["score"] == 0.5, "small values are kept"
    notes = r["evidence"][0]["raw_dropped"]
    assert any(n.startswith("raw.thumbnail") for n in notes) and any(n.startswith("raw.xmp") for n in notes)
    assert any(n.startswith("raw.nested.more[0]") for n in notes)
    assert len(json.dumps(r)) < 10_000


def test_odd_values_in_raw_still_give_valid_canonical_json(image):
    result = make_result()
    result.evidence[0].raw = {"nan": float("nan"), "inf": float("-inf"), 7: "int key", "obj": object(),
                              "t": (1, 2), "s": {"b", "a"}, "when": datetime(2026, 10, 1, tzinfo=timezone.utc),
                              "p": Path("/a/b"), "many": list(range(receipt.MAX_ITEMS + 5))}
    r = build(result, image)
    json.loads(canonical_json(r))   # allow_nan=False would raise on a stray NaN
    raw = r["evidence"][0]["raw"]
    assert raw["nan"] == "nan" and raw["inf"] == "-inf" and raw["7"] == "int key"
    assert raw["obj"].startswith("<object object") and raw["t"] == [1, 2] and raw["s"] == ["a", "b"]
    assert raw["when"] == "2026-10-01T00:00:00+00:00" and raw["p"] == "/a/b"
    assert len(raw["many"]) == receipt.MAX_ITEMS + 1 and raw["many"][-1] == {"dropped": "items", "count": 5}


def test_very_deep_raw_is_cut_off_not_recursed_forever(image):
    result = make_result()
    raw = {"leaf": 1}
    for _ in range(receipt.MAX_DEPTH + 2):
        raw = {"d": raw}
    result.evidence[0].raw = raw
    r = build(result, image)
    json.loads(canonical_json(r))
    assert "nested too deep" in json.dumps(r) and r["evidence"][0]["raw_dropped"]


def test_stub_cards_mark_the_receipt_as_demo(image):
    result = make_result()
    result.evidence[0].raw = {"stub": True}
    assert build(result, image)["demo"] is True


# --- JSON -------------------------------------------------------------------------------------

def test_write_json_is_canonical_and_has_a_matching_sha256_file(image, tmp_path):
    r = build(make_result(), image)
    out = tmp_path / "out" / "r.json"
    out.parent.mkdir()
    assert write_json(r, out) == out
    data = out.read_bytes()
    assert data == canonical_json(r) and data.endswith(b"}\n")
    assert json.loads(data) == r
    assert data.decode() == json.dumps(r, sort_keys=True, indent=2, ensure_ascii=False) + "\n", "sorted keys"
    side = (out.parent / "r.json.sha256").read_text()
    assert side == f"{hashlib.sha256(data).hexdigest()}  r.json\n", "sha256sum format"


def test_write_json_into_a_directory_names_the_file_after_the_image(image, tmp_path):
    r = build(make_result(), image)
    out = write_json(r, tmp_path)
    assert out.name == "photo.png.receipt.json" and (tmp_path / "photo.png.receipt.json.sha256").exists()


def test_same_input_gives_the_same_output_apart_from_created_at(image, tmp_path):
    a = build(make_result(), image, inputs={"caption": "x"})
    b = build(make_result(), image, inputs={"caption": "x"})
    a.pop("created_at"), b.pop("created_at")
    assert a == b
    assert canonical_json(a) == canonical_json(b)


def paths(value, prefix=()):
    """Every key path in a nested dict/list, as tuples (list positions count as keys)."""
    out = set()
    if isinstance(value, dict):
        for k, v in value.items():
            out |= {prefix + (k,)} | paths(v, prefix + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out |= {prefix + (i,)} | paths(v, prefix + (i,))
    return out


def test_the_volatile_list_is_exactly_the_two_run_timings():
    assert receipt.VOLATILE_FIELDS == {("classifier", ("seconds",)), ("watermark", ("seconds",))}


def test_receipt_doc_lists_every_volatile_field():
    doc = (ROOT / "docs" / "RECEIPT.md").read_text(encoding="utf-8")
    for rung, path in receipt.VOLATILE_FIELDS:
        assert f"`{rung}`" in doc and f"`raw.{'.'.join(path)}`" in doc


def test_classifier_and_watermark_cards_drop_only_the_top_level_raw_seconds(image):
    clf_raw = {"score": 0.91, "model": "m", "device": "cpu", "seconds": 0.4496, "above_threshold": True}
    wm_raw = {"present": True, "matched_bits": 90, "bits": 96, "device": "cpu", "seconds": 0.0211}
    ev = [Evidence("classifier", "f", "p", "d", "signal", dict(clf_raw)),
          Evidence("watermark", "f", "p", "d", "signal", dict(wm_raw))]
    cards = build(Result("concerns", ev, "s", "v"), image)["evidence"]
    assert cards[0]["raw"] == {k: v for k, v in clf_raw.items() if k != "seconds"}
    assert cards[1]["raw"] == {k: v for k, v in wm_raw.items() if k != "seconds"}
    assert cards[0]["raw_dropped"] == ["raw.seconds (per-run timing)"]
    assert cards[1]["raw_dropped"] == ["raw.seconds (per-run timing)"]
    assert '"seconds"' not in canonical_json(build(Result("concerns", ev, "s", "v"), image)).decode()


def test_voice_transcript_card_keeps_its_seconds_and_every_segment_end_ms(image):
    t = voice.Transcript(
        text="It rained. The bridge is closed.",
        segments=[voice.Segment(0, 1800, "It rained."), voice.Segment(1800, 4650, "The bridge is closed."),
                  voice.Segment(4650, 61250, "Yes.")],
        model="base.en", model_sha256="0" * 64, engine="whisper-cli 1.7", seconds=61.25,
        raw={"model_file": "ggml-base.en.bin", "model_revision": "r1", "language": "en"})
    card = voice._transcript_card(t, ["The bridge is closed."])
    assert card.raw["transcript"]["seconds"] == 61.25
    r = build(Result("cant_tell", [card], "s", "v"), image)["evidence"][0]
    assert r["raw"]["transcript"]["seconds"] == 61.25
    assert [s["end_ms"] for s in r["raw"]["transcript"]["segments"]] == [1800, 4650, 61250]
    assert [s["start_ms"] for s in r["raw"]["transcript"]["segments"]] == [0, 1800, 4650]
    assert r["raw"] == card.raw and r["raw_dropped"] == []
    assert '"end_ms": 61250' in canonical_json(build(Result("cant_tell", [card], "s", "v"), image)).decode()


@pytest.mark.parametrize("rung", ["classifier", "watermark", "claim", "c2pa", "exif", "iptc", "origin", "stub", "new_rung"])
def test_nothing_is_dropped_unless_it_is_on_the_list(image, rung):
    """Every timing-looking key, at the top and nested: only listed (rung, path) pairs may go missing."""
    raw = {"seconds": 1.5, "elapsed": 2, "duration": 3, "timestamp": "t", "checked_at": "t", "run_at": "t",
           "started_at": "t", "finished_at": "t", "decode_ms": 12, "end_ms": 9, "load_seconds": 4.2,
           "seconds_read": 61.2, "limit_seconds": 60, "sign_time": "2025-12-30T18:04:30+00:00",
           "transcript": {"seconds": 61.25, "segments": [{"start_ms": 0, "end_ms": 1800, "text": "x"}]},
           "detail": {"seconds": 0.2, "elapsed": 1, "kept": [{"duration": 5, "n": 1}]},
           "score": 0.9}
    before = paths(raw)
    card = build(Result("cant_tell", [Evidence(rung, "f", "p", "d", "none", raw)], "s", "v"), image)["evidence"][0]
    listed = {path for r, path in receipt.VOLATILE_FIELDS if r == rung}
    assert before - paths(card["raw"]) == listed
    assert card["raw_dropped"] == [f"raw.{'.'.join(p)} (per-run timing)" for p in sorted(listed)]


def test_times_that_come_from_the_file_are_kept(image):
    ev = [Evidence("c2pa", "f", "p", "d", "signed", {"sign_time": "2025-12-30T18:04:30+00:00"}),
          Evidence("exif", "f", "p", "d", "metadata", {"datetime_original": "2025:12:30 11:04:29"}),
          Evidence("voice", "f", "p", "d", "none", {"heard": {"sentence": "x", "start_ms": 4200},
                                                    "seconds_read": 61.2, "limit_seconds": 60})]
    r = build(Result("cant_tell", ev, "s", "v"), image)
    assert [c["raw"] for c in r["evidence"]] == [e.raw for e in ev]
    assert all(c["raw_dropped"] == [] for c in r["evidence"])


def test_inputs_are_not_stripped_of_timing_keys(image):
    r = build(make_result(), image, inputs={"seconds": 5, "note": "as given"})
    assert r["inputs"] == {"seconds": 5, "note": "as given"}


def test_same_receipt_gives_byte_identical_json_and_pdf(image, tmp_path):
    r = build(make_result(), image)
    one, two = tmp_path / "1", tmp_path / "2"
    one.mkdir(), two.mkdir()
    for d in (one, two):
        write_json(r, d / "r.json")
        write_pdf(r, d / "r.pdf")
    assert (one / "r.json").read_bytes() == (two / "r.json").read_bytes()
    assert (one / "r.json.sha256").read_bytes() == (two / "r.json.sha256").read_bytes()
    assert (one / "r.pdf").read_bytes() == (two / "r.pdf").read_bytes()


def test_a_different_created_at_changes_only_that_line(image, tmp_path, monkeypatch):
    monkeypatch.setattr(receipt, "_now", lambda: "2026-10-28T01:00:00Z")
    a = build(make_result(), image)
    monkeypatch.setattr(receipt, "_now", lambda: "2026-10-28T02:00:00Z")
    b = build(make_result(), image)
    assert a != b and {k for k in a if a[k] != b[k]} == {"created_at"}


# --- PDF --------------------------------------------------------------------------------------

def test_pdf_text_has_the_answer_every_card_limit_and_the_hashes(image, tmp_path):
    result = make_result()
    r = build(result, image)
    out = write_pdf(r, tmp_path / "r.pdf")
    assert out.read_bytes().startswith(b"%PDF-")
    text = squash(pdf_text(out))
    for ev in result.evidence:
        for field in (ev.finding, ev.proves, ev.does_not_prove, ev.strength):
            assert squash(field) in text, field
    assert squash(explain(result).summary) in text   # the page's summary (DAT-850)
    assert squash(LIMITS) in text
    assert r["file"]["sha256"] in text and r["file"]["name"] in text
    assert hashlib.sha256(canonical_json(r)).hexdigest() in text, "the PDF quotes the JSON receipt's hash"
    assert "Concernsfound(concerns)" in text and "abc1234" in text and SCHEMA in text
    assert "Doesnotprove" in text and "Strength" in text


def test_pdf_says_score_only_for_a_classifier_score_that_raises_no_concern(image, tmp_path):
    # PR #33: a threshold without a gate PASS makes the classifier score-only. The PDF's Strength column says
    # "Score only", as the page does, not the bare "signal" beside "raises no concern".
    ev = Evidence("classifier", "Community Forensics detector score 0.990 out of 1. This score raises no concern on its own.",
                  "Nothing on its own.", "It is not proof that the image was made by AI.", "signal",
                  {"score": 0.99, "above_threshold": True, "raises_concern": False})
    r = build(Result("cant_tell", [ev], "s", "v"), image)
    assert "Scoreonly" in squash(pdf_text(write_pdf(r, tmp_path / "r.pdf")))


def test_pdf_is_one_or_two_pages_with_every_font_embedded(image, tmp_path):
    out = write_pdf(build(make_result(), image), tmp_path / "r.pdf")
    reader = PdfReader(str(out))
    assert 1 <= len(reader.pages) <= 2
    fonts = {}
    for page in reader.pages:
        for ref in page["/Resources"]["/Font"].values():
            font = ref.get_object()
            fonts[font["/BaseFont"]] = font
    assert len(fonts) >= 2
    for name, font in fonts.items():
        desc = font["/FontDescriptor"]
        assert "/FontFile2" in desc or "/FontFile3" in desc, f"{name} is not embedded"
    assert not any("Helvetica" in n or "Times" in n or "Courier" in n for n in fonts)


def test_pdf_page_footer_says_in_development_and_not_meta(image, tmp_path):
    text = pdf_text(write_pdf(build(make_result(), image), tmp_path / "r.pdf"))
    assert "Second Look (in development)" in text and "Page 1 of" in text
    assert "not affiliated with Meta" in text


def test_pdf_with_no_cards_and_with_inputs(image, tmp_path):
    r = build(Result("cant_tell", [], "We can't tell how this image was made.", "abc"), image,
              inputs={"caption": "Flooded street, 3 Oct"})
    text = squash(pdf_text(write_pdf(r, tmp_path / "r.pdf")))
    assert "Can'ttell(cant_tell)" in text
    assert "producednoevidencecards" in text and squash("Flooded street, 3 Oct") in text


def test_pdf_marks_a_demo_answer(image, tmp_path):
    r = build(make_result(), image)
    r["demo"] = True
    assert "DEMOANSWER" in squash(pdf_text(write_pdf(r, tmp_path / "r.pdf")))
    r["demo"] = False
    assert "DEMOANSWER" not in squash(pdf_text(write_pdf(r, tmp_path / "r2.pdf")))


def test_pdf_with_many_cards_and_long_text_still_builds(image, tmp_path):
    result = make_result()
    long = "A limit stated in full, never shortened. " * 60
    result.evidence = [Evidence("exif", f"Finding {i}", "Proves something small.", long, "metadata", {})
                       for i in range(12)]
    out = write_pdf(build(result, image), tmp_path / "r.pdf")
    text = squash(pdf_text(out))
    # A long row is split across pages, so its words are interleaved with the other columns on extraction:
    # count the sentences instead. None may be cut.
    assert text.count("nevershortened.") == 12 * 60, "every does_not_prove is complete, across page breaks"


def test_pdf_text_that_the_font_cannot_draw_is_flagged_not_fatal(image, tmp_path):
    result = make_result()
    result.evidence[0] = Evidence("exif", "Camera → phone éè", "p", "Limit → here", "metadata", {})
    r = build(result, image)
    out = write_pdf(r, tmp_path / "r.pdf")
    text = squash(pdf_text(out))
    assert "Camera?phoneéè" in text
    assert "couldnotbeshown" in text.lower()
    assert "→" in canonical_json(r).decode(), "the JSON keeps the exact text"


def test_pdf_escapes_markup_in_text(image, tmp_path):
    result = make_result()
    result.evidence[0] = Evidence("exif", "Maker <b>bold</b> & co", "p<br/>", "d", "metadata", {})
    text = squash(pdf_text(write_pdf(build(result, image), tmp_path / "r.pdf")))
    assert squash("Maker <b>bold</b> & co") in text and "p<br/>" in text


# --- limits line and copy ---------------------------------------------------------------------

def test_limits_line_is_sourced_from_claims_md():
    claims = (ROOT / "docs" / "CLAIMS.md").read_text().replace("`", "")
    for source in ("An image is genuine, authentic, or safe to trust".lower(),
                   "signed_camera means a signer's record says a camera captured it",
                   "cant_tell means we found nothing either way",
                   "A concern is a reason to look closer",
                   "That a signed record proves a claim about the picture".lower(),
                   "it is a fact-checker",
                   "affiliated with Meta"):
        assert source.lower() in claims.lower(), source
    for sentence in ("signed_camera means a signer's record says a camera captured it.",
                     "cant_tell means we found nothing either way.",
                     "A concern is a reason to look closer, not a verdict."):
        assert sentence in LIMITS


def test_receipt_copy_makes_no_banned_claims(image, tmp_path):
    texts = [LIMITS, *receipt.ANSWER_LABELS.values(), pdf_text(write_pdf(build(make_result(), image), tmp_path / "r.pdf"))]
    for t in texts:
        for pattern in (r"detects? ai", r"\baccura", r"this is real", "—"):
            assert not re.search(pattern, t, re.I), pattern


# --- no network -------------------------------------------------------------------------------

def test_building_and_writing_use_no_network(image, tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network used")

    for name in ("socket", "create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, refuse)
    r = build(make_result(), image, inputs={"caption": "c"})
    write_json(r, tmp_path / "r.json")
    write_pdf(r, tmp_path / "r.pdf")
    assert (tmp_path / "r.pdf").stat().st_size > 1000


def test_module_imports_no_network_library():
    src = (ROOT / "secondlook" / "receipt.py").read_text()
    for bad in ("socket", "urllib", "http", "requests", "httpx", "ftplib", "smtplib"):
        assert not re.search(rf"^\s*(import|from)\s+{bad}\b", src, re.M), bad


# --- real ladder, end to end ------------------------------------------------------------------

def test_real_signed_file_gives_a_valid_receipt_and_pdf(made, test_root_trusted, tmp_path):
    result = ladder.run(made["signed_ai"])
    r = build(result, made["signed_ai"])
    assert r["answer"] == result.answer == "signed_ai"
    assert r["file"]["sha256"] == hashlib.sha256(Path(made["signed_ai"]).read_bytes()).hexdigest()
    assert any(c["rung"] == "c2pa" and c["strength"] == "signed" for c in r["evidence"])
    json.loads(canonical_json(r))
    text = squash(pdf_text(write_pdf(r, tmp_path / "r.pdf")))
    for c in r["evidence"]:
        assert squash(c["does_not_prove"]) in text


def test_two_real_ladder_runs_give_identical_receipts_apart_from_created_at(tmp_path, monkeypatch):
    """The real ladder with the real classifier, twice on one sample: wall-clock timing must not reach the receipt."""
    from secondlook.rungs import classifier

    if not classifier.weights_path().exists():
        pytest.skip("classifier weights missing: run python -m secondlook.rungs.classifier --fetch")
    sample_path = sample("commons-pixel10pro-utah-sign.jpg")
    results = [ladder.run(sample_path), ladder.run(sample_path)]
    clf = [e for e in results[0].evidence if e.rung == "classifier"]
    assert clf and "seconds" in clf[0].raw, "the classifier card must carry its timing here, or this test proves nothing"
    receipts, jsons, pdfs = [], [], []
    for i, result in enumerate(results):
        monkeypatch.setattr(receipt, "_now", lambda i=i: f"2026-10-28T0{i}:00:00Z")
        receipts.append(build(result, sample_path))
    a, b = receipts
    assert a["created_at"] != b["created_at"]
    card = next(c for c in a["evidence"] if c["rung"] == "classifier")
    assert "seconds" not in card["raw"] and "raw.seconds (per-run timing)" in card["raw_dropped"]
    assert {k for k in a if a[k] != b[k]} == {"created_at"}
    # With only created_at removed: identical JSON, identical sha256.
    for r in receipts:
        r.pop("created_at")
        jsons.append(canonical_json(r))
    assert jsons[0] == jsons[1]
    assert hashlib.sha256(jsons[0]).hexdigest() == hashlib.sha256(jsons[1]).hexdigest()
    # The PDF quotes created_at and the JSON hash, so fix created_at to compare PDF bytes.
    for n, result in enumerate(results):
        monkeypatch.setattr(receipt, "_now", lambda: "2026-10-28T00:00:00Z")
        r = build(result, sample_path)
        d = tmp_path / str(n)
        d.mkdir()
        write_json(r, d / "r.json")
        write_pdf(r, d / "r.pdf")
        pdfs.append(((d / "r.json").read_bytes(), (d / "r.json.sha256").read_bytes(), (d / "r.pdf").read_bytes()))
    assert pdfs[0] == pdfs[1]


# --- CLI --------------------------------------------------------------------------------------

def test_cli_writes_json_sha256_and_pdf_into_the_folder(image, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [("x", lambda p: make_result().evidence[:2])])
    out = tmp_path / "receipts" / "deep"
    assert cli.main(["check", str(image), "--receipt", str(out)]) == 0
    captured = capsys.readouterr()
    # stdout is what `check` prints without --receipt: the result and E1's explanation
    assert set(json.loads(captured.out)) == {"answer", "evidence", "summary", "version", "explanation"}
    names = sorted(p.name for p in out.iterdir())
    assert names == ["photo.png.receipt.json", "photo.png.receipt.json.sha256", "photo.png.receipt.pdf"]
    data = json.loads((out / "photo.png.receipt.json").read_text())
    assert data["schema"] == SCHEMA and data["answer"] == "concerns"
    assert data["file"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert data["tool"]["version"] == json.loads(captured.out)["version"]
    side = (out / "photo.png.receipt.json.sha256").read_text().split()
    assert side[0] == hashlib.sha256((out / "photo.png.receipt.json").read_bytes()).hexdigest()
    assert squash(data["evidence"][0]["does_not_prove"]) in squash(pdf_text(out / "photo.png.receipt.pdf"))
    assert "receipt:" in captured.err


def test_pdf_hash_line_names_the_json_built_with_it(image, tmp_path, capsys, monkeypatch):
    """A249 (DAT-850, judge re-run 30 Oct, old snag 4): the PDF said its hash was of "the .json file beside this
    PDF", but the page's JSON button runs a new check with its own created_at, so that file could not match. The
    line now names the JSON built with this PDF and says a separate download can differ; the CLI's .json, written
    from the same receipt, still matches the hash the PDF quotes."""
    text = squash(pdf_text(write_pdf(build(make_result(), image), tmp_path / "r.pdf")))
    assert squash(receipt.PDF_JSON_SHA_LINE) in text
    assert "beside" not in text and "separateJSONdownloadisanewcheckandcandiffer" in text
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [("x", lambda p: make_result().evidence[:2])])
    out = tmp_path / "cli"
    assert cli.main(["check", str(image), "--receipt", str(out)]) == 0
    capsys.readouterr()
    beside = hashlib.sha256((out / "photo.png.receipt.json").read_bytes()).hexdigest()
    assert beside in squash(pdf_text(out / "photo.png.receipt.pdf"))


def test_cli_plain_still_writes_the_receipt(image, tmp_path, capsys, monkeypatch):
    """--plain prints only the explanation (E1); --receipt (R1) must still write its files."""
    monkeypatch.setattr(ladder.rung_registry, "load", lambda *a, **k: [("x", lambda p: make_result().evidence[:2])])
    out = tmp_path / "receipts"
    assert cli.main(["check", "--plain", str(image), "--receipt", str(out)]) == 0
    printed = capsys.readouterr().out
    assert not printed.lstrip().startswith("{")  # plain text, not the JSON
    assert sorted(p.name for p in out.iterdir()) == [
        "photo.png.receipt.json", "photo.png.receipt.json.sha256", "photo.png.receipt.pdf"]


def test_cli_without_receipt_writes_nothing(image, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["check", str(image)]) == 0
    assert [p.name for p in tmp_path.iterdir()] == ["photo.png"]


def test_cli_missing_file_still_prints_the_answer_but_no_receipt(tmp_path, capsys):
    out = tmp_path / "r"
    assert cli.main(["check", str(tmp_path / "gone.png"), "--receipt", str(out)]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["answer"] == "cant_tell"
    assert "no receipt written" in captured.err and not out.exists()


def test_cli_keeps_the_json_when_the_pdf_library_is_missing(image, tmp_path, capsys, monkeypatch):
    def no_pdf(*a, **k):
        raise ImportError("write_pdf needs ReportLab")

    monkeypatch.setattr(receipt, "write_pdf", no_pdf)
    assert cli.main(["check", str(image), "--receipt", str(tmp_path)]) == 3
    assert (tmp_path / "photo.png.receipt.json").exists() and (tmp_path / "photo.png.receipt.json.sha256").exists()
    assert "no PDF written" in capsys.readouterr().err


# --- web --------------------------------------------------------------------------------------

def post(client, path, name="holiday.png", data=PNG, **kw):
    return client.post(path, files={"file": (name, data, "image/png")}, **kw)


@pytest.fixture
def client():
    rung = lambda p: make_result().evidence[:2]   # noqa: E731
    return TestClient(create_app(stub=False, rungs=[("fake", rung)]))


def test_api_receipt_returns_the_pdf(client):
    r = post(client, "/api/receipt")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="holiday.png.receipt.pdf"'
    assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in r.headers["content-security-policy"]
    reader = PdfReader(io.BytesIO(r.content))
    text = squash("\n".join(p.extract_text() for p in reader.pages))
    assert hashlib.sha256(PNG).hexdigest() in text and squash(LIMITS) in text
    assert squash(make_result().evidence[0].does_not_prove) in text
    assert "DEMOANSWER" not in text


def test_api_check_without_the_flag_has_no_receipt(client):
    assert "receipt" not in post(client, "/api/check").json()


def test_api_check_receipt_flag_adds_the_receipt_json(client):
    body = post(client, "/api/check", params={"receipt": "1"}).json()
    rc = body["receipt"]
    assert rc["schema"] == SCHEMA and rc["file"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert rc["file"]["name"] == "holiday.png" and rc["answer"] == body["answer"]
    assert rc["tool"]["version"] == body["version"] and rc["demo"] is False
    assert [c["rung"] for c in rc["evidence"]] == [e["rung"] for e in body["evidence"]]
    assert "secondlook-" not in json.dumps(rc), "no temp path in the receipt"
    assert {k: v for k, v in body.items() if k != "receipt"}["answer"] == "concerns"


def test_api_receipt_on_the_stub_engine_says_demo():
    stub = TestClient(create_app(stub=True))
    rc = post(stub, "/api/check", name="demo-signed-ai.png", params={"receipt": "1"}).json()["receipt"]
    assert rc["demo"] is True
    text = squash(PdfReader(io.BytesIO(post(stub, "/api/receipt", name="demo-signed-ai.png").content)).pages[0].extract_text())
    assert "DEMOANSWER" in text


def test_api_receipt_keeps_the_upload_guards(client):
    assert post(client, "/api/receipt", name="notes.txt", data=b"x").status_code == 415
    assert post(client, "/api/receipt", data=b"").status_code == 400
    assert client.post("/api/receipt").status_code == 422

    sent = []

    async def receive():
        raise AssertionError("the body was read")

    async def send(message):
        sent.append(message)

    for headers, status in (([(b"content-length", str(MAX_REQUEST + 1).encode())], 413), ([], 411)):
        sent.clear()
        scope = {"type": "http", "method": "POST", "path": "/api/receipt", "headers": headers,
                 "query_string": b"", "http_version": "1.1", "scheme": "http", "root_path": "",
                 "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 1)}
        asyncio.run(client.app(scope, receive, send))
        assert sent[0]["status"] == status


def test_server_still_binds_to_loopback_only():
    src = (ROOT / "secondlook" / "web" / "__main__.py").read_text()
    assert 'host="127.0.0.1"' in src and "0.0.0.0" not in src


def test_api_receipt_failure_is_a_plain_500_and_keeps_nothing(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret path /tmp/x")

    monkeypatch.setattr(receipt, "write_pdf", boom)
    r = post(client, "/api/receipt")
    assert r.status_code == 500 and "secret" not in r.text and "receipt failed" in r.json()["detail"]
