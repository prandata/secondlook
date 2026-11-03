"""Tests for the voice rung. Speech comes from macOS `say` into a file (synthetic, no real
voices); fact-checks come from a small synthetic ClaimReview feed (not real reviews).
The end-to-end tests need whisper-cli and the pinned model and are skipped without them."""
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from secondlook import ladder
from secondlook.rungs import claim, voice
from secondlook.types import Evidence

CLAIM_TEXT = "The national museum sold its famous dinosaur skeleton to a private collector."
SPOKEN = ("Hi, so I heard that the national museum sold its famous dinosaur skeleton to a private "
          "collector. Can you call me back? I hope you are well.")


def _kinds(cards):
    return [c.raw.get("kind") for c in cards]


# ---------------------------------------------------------------- claims, no audio needed

def test_extract_drops_questions_requests_and_feelings():
    text = ("Hey guys, I heard that the national museum sold its famous dinosaur skeleton. "
            "Is that true? Please forward this to everyone. I hope you are well. Thanks!")
    assert voice.extract_claims(text) == ["The national museum sold its famous dinosaur skeleton."]


def test_extract_strips_lead_ins_and_keeps_order_without_repeats():
    text = ("Apparently the river flooded the old town square on Monday. "
            "They say that the council closed every school in the district. "
            "Apparently the river flooded the old town square on Monday.")
    assert voice.extract_claims(text) == [
        "The river flooded the old town square on Monday.",
        "The council closed every school in the district.",
    ]


def test_extract_needs_enough_content_words_and_respects_limit():
    assert voice.extract_claims("It is cold. Yes it was.") == []
    many = " ".join(f"Shop number {i} closed its doors forever today." for i in range(30))
    assert len(voice.extract_claims(many)) == voice.MAX_CLAIMS
    assert len(voice.extract_claims(many, limit=2)) == 2


@pytest.mark.parametrize("bad", [None, 42, b"bytes", ["list"]])
def test_extract_never_raises(bad):
    assert voice.extract_claims(bad) == []


# ---------------------------------------------------------------- never raises, never a verdict

@pytest.mark.parametrize("bad", [None, 42, "", "/no/such/file.wav", ["x"]])
def test_check_returns_empty_for_non_audio(bad, tmp_path):
    assert voice.check(bad) == []
    png = tmp_path / "image.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert voice.check(str(png)) == []


def test_no_engine_still_says_cant_tell(tmp_path, monkeypatch):
    note = tmp_path / "note.wav"
    _silence(note, 1)
    monkeypatch.setenv(voice.WHISPER_ENV, str(tmp_path / "no-such-whisper"))
    cards = voice.check(str(note))
    assert _kinds(cards) == ["how_made", "transcript_unavailable"]
    assert cards[0].finding == voice.HOW_MADE
    assert all(isinstance(c, Evidence) and c.does_not_prove for c in cards)
    res = voice.analyse(str(note))
    assert res.answer == "cant_tell"
    assert "can't tell how this voice note was made" in res.summary


def test_model_must_match_its_pin(tmp_path, monkeypatch):
    blob = b"not really a model"
    good = voice.ModelPin("ggml-fake.bin", hashlib.sha256(blob).hexdigest(), len(blob))
    bad = voice.ModelPin("ggml-fake.bin", "0" * 64, len(blob))
    (tmp_path / "ggml-fake.bin").write_bytes(blob)
    monkeypatch.setenv(voice.MODEL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(voice, "PINNED_MODELS", {"good": good, "bad": bad})
    assert voice.model_path("good") == tmp_path / "ggml-fake.bin"
    assert voice.model_path("bad") is None
    assert voice.model_path("unpinned") is None
    assert voice.model_path(None) is None


def test_voice_cards_never_move_the_ladder():
    cards = [voice._how_made_card(),
             Evidence("claim", "x rated it False", "p", "d", "none", {"kind": "claim", "rating": "False"})]
    assert ladder.decide(cards)[0] == "cant_tell"


# ---------------------------------------------------------------- end to end with whisper.cpp

def _silence(path: Path, seconds: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * seconds)


def _have_engine() -> bool:
    return bool(shutil.which("say") and voice._whisper_cli() and voice.model_path(voice.DEFAULT_MODEL))


needs_engine = pytest.mark.skipif(not _have_engine(), reason="needs macOS say, whisper-cli and the pinned model")


def _feed_item(text, url, rating, checker="Example Checker", checker_url="https://checker.example/"):
    return {"@type": "DataFeedItem", "item": [{
        "@type": "ClaimReview",
        "author": {"@type": "Organization", "name": checker, "url": checker_url},
        "claimReviewed": text, "datePublished": "2026-09-30", "url": url,
        "reviewRating": {"@type": "Rating", "alternateName": rating},
    }]}


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("claims")
    feed = d / "feed.json"
    feed.write_text(json.dumps({"@type": "DataFeed", "dataFeedElement": [
        _feed_item(CLAIM_TEXT, "https://checker.example/museum", "Mostly false: the skeleton was loaned"),
        _feed_item("The city zoo released two lions into the park.", "https://checker.example/zoo", "False"),
    ]}))
    out = d / "claims.sqlite"
    claim.build_index(feed, out, downloaded_at="2026-10-28T00:00:00Z", sha256="test")
    return out


@pytest.fixture(scope="module")
def spoken_wav(tmp_path_factory):
    p = tmp_path_factory.mktemp("say") / "note.wav"
    subprocess.run(["say", "-o", str(p), "--data-format=LEI16@16000", SPOKEN], check=True, timeout=60)
    return p


@needs_engine
def test_voice_note_to_claim_receipt(spoken_wav, db):
    res = voice.analyse(str(spoken_wav), db=db)
    assert res.answer == "cant_tell"
    kinds = _kinds(res.evidence)
    assert kinds[:2] == ["how_made", "transcript"]
    t = res.evidence[1].raw["transcript"]
    assert "dinosaur skeleton" in t["text"].lower()
    assert t["model"] == "small.en" and t["model_sha256"] == voice.PINNED_MODELS["small.en"].sha256
    # the model may capitalise "National Museum"; the lookup folds case
    assert [c.lower() for c in res.evidence[1].raw["candidate_claims"]] == [CLAIM_TEXT.lower()]
    found = [c for c in res.evidence if c.raw.get("kind") == "claim"]
    assert len(found) == 1
    card = found[0]
    assert card.raw["rating"] == "Mostly false: the skeleton was loaned"     # word for word
    assert card.raw["review_url"] == "https://checker.example/museum"
    assert card.raw["date_published"] == "2026-09-30"
    assert card.raw["heard"]["sentence"].lower().startswith("the national museum")
    assert card.raw["heard"]["start_ms"] == 0
    assert "Published by checker.example" in card.finding    # not on the IFCN list: never called a fact-checker
    assert ladder.decide(res.evidence)[0] == "cant_tell"


@needs_engine
def test_m4a_is_converted_and_unmatched_claims_say_so(tmp_path, db):
    note = tmp_path / "note.m4a"
    subprocess.run(["say", "-o", str(note), "--file-format=m4af",
                    "The town library is giving away free bicycles to every student this month."],
                   check=True, timeout=60)
    cards = voice.check_voice(str(note), db=db)
    assert _kinds(cards) == ["how_made", "transcript", "no_match"]
    assert "bicycles" in cards[1].raw["transcript"]["text"].lower()
    assert cards[2].finding.startswith(claim.NO_MATCH_MESSAGE)


@needs_engine
def test_missing_index_is_not_shown_as_no_match(spoken_wav, tmp_path):
    cards = voice.check_voice(str(spoken_wav), db=tmp_path / "missing.sqlite")
    assert _kinds(cards)[:2] == ["how_made", "transcript"]
    assert cards[-1].finding == claim.UNAVAILABLE_MESSAGE
    assert cards[-1].raw["status"] == "unavailable"


@needs_engine
def test_silence_gives_no_speech(tmp_path, db):
    note = tmp_path / "quiet.wav"
    _silence(note, 2)
    cards = voice.check_voice(str(note), db=db)
    assert _kinds(cards) == ["how_made", "transcript", "no_speech"]
    assert voice.analyse(str(note), db=db).answer == "cant_tell"


# ---------------------------------------------------------------- length: refused, never cut
# Every format is measured from its decoded samples before any transcript. The audio is a
# generated sine tone; whisper-cli is a stub that reports how much audio it was given, so
# these run without the model and prove the whole note reached the transcriber.

UNDER, OVER = voice.MAX_SECONDS - 5, voice.MAX_SECONDS + 5
TONE_ARGS = {                       # suffix: ffmpeg output options
    "wav": ["-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le"],         # read as is
    "wave": ["-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav"],   # not 16 kHz: decoded first
    "mp3": ["-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "32k"],
    "m4a": ["-ar", "16000", "-ac", "1", "-c:a", "aac", "-b:a", "32k"],
    "ogg": ["-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "24k"],
}
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg to make and decode tones")


@pytest.fixture(scope="module")
def tone(tmp_path_factory):
    d, made = tmp_path_factory.mktemp("tones"), {}

    def make(suffix: str, seconds: float) -> Path:
        key = (suffix, seconds)
        if key not in made:
            out = d / f"tone-{seconds:g}s.{suffix}"
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                            "-i", f"sine=frequency=440:sample_rate=16000:duration={seconds}",
                            *TONE_ARGS[suffix], str(out)], check=True, timeout=120)
            made[key] = out
        return made[key]
    return make


STUB = """#!{python}
import json, os, sys, wave
a = sys.argv[1:]
with open(os.environ["VOICE_STUB_LOG"], "a") as log:
    log.write(json.dumps(a) + "\\n")
wav, prefix = a[a.index("-f") + 1], a[a.index("-of") + 1]
with wave.open(wav, "rb") as w:
    seconds = w.getnframes() / w.getframerate()
with open(prefix + ".json", "w") as f:
    json.dump({{"result": {{"language": "en"}}, "transcription": [{{
        "offsets": {{"from": 0, "to": int(seconds * 1000)}},
        "text": "stub heard %.3f seconds" % seconds}}]}}, f)
"""


@pytest.fixture
def stub_whisper(tmp_path, monkeypatch):
    cli = tmp_path / "whisper-stub"
    cli.write_text(STUB.format(python=sys.executable))
    cli.chmod(0o755)
    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"stub")
    calls = tmp_path / "whisper-calls.jsonl"
    monkeypatch.setenv(voice.WHISPER_ENV, str(cli))
    monkeypatch.setenv("VOICE_STUB_LOG", str(calls))
    monkeypatch.setattr(voice, "model_path", lambda name=voice.DEFAULT_MODEL, warn=True: model)
    return calls


def _heard_seconds(cards) -> float:
    text = cards[1].raw["transcript"]["text"]
    return float(text.split()[2])


@needs_ffmpeg
@pytest.mark.parametrize("suffix", sorted(TONE_ARGS))
def test_over_limit_is_refused_with_notice(suffix, tone, stub_whisper):
    cards = voice.check_voice(str(tone(suffix, OVER)))
    assert not stub_whisper.exists()                # refused before any transcript
    assert _kinds(cards) == ["how_made", "too_long"]
    assert cards[1].finding == voice.TOO_LONG
    assert "longer than 10 minutes" in cards[1].finding and "not transcribed" in cards[1].finding
    assert cards[1].raw["seconds_read"] > voice.MAX_SECONDS
    assert cards[1].raw["measured_by"] == "decoded samples"
    assert voice.analyse(str(tone(suffix, OVER))).answer == "cant_tell"


@needs_ffmpeg
@pytest.mark.parametrize("suffix", sorted(TONE_ARGS))
def test_under_limit_is_transcribed_whole(suffix, tone, stub_whisper):
    cards = voice.check_voice(str(tone(suffix, UNDER)))
    assert _kinds(cards)[:2] == ["how_made", "transcript"]
    t = cards[1].raw["transcript"]
    assert abs(t["seconds"] - UNDER) < 0.5          # measured, not taken from the cap
    assert abs(_heard_seconds(cards) - UNDER) < 0.5  # the transcriber got all of it
    args = json.loads(stub_whisper.read_text().splitlines()[-1])
    assert "-d" not in args and "--duration" not in args   # no duration cap passed to whisper
    assert "whole note (9:55)" in cards[1].finding


@needs_ffmpeg
def test_limit_is_exact_for_sample_exact_wav(tone, stub_whisper):
    at = voice.check_voice(str(tone("wav", voice.MAX_SECONDS)))
    assert _kinds(at)[1] == "transcript" and _heard_seconds(at) == voice.MAX_SECONDS
    over = voice.check_voice(str(tone("wav", voice.MAX_SECONDS + 0.5)))
    assert _kinds(over) == ["how_made", "too_long"]
    assert over[1].raw["seconds_read"] == voice.MAX_SECONDS + 0.5


@needs_ffmpeg
def test_decoded_note_just_past_limit_is_refused(tone, stub_whisper):
    """The F1 case: ffmpeg used to stop at exactly the limit, so a long m4a read as 600.0 s."""
    cards = voice.check_voice(str(tone("m4a", voice.MAX_SECONDS + 1)))
    assert _kinds(cards) == ["how_made", "too_long"]


def test_wav_length_comes_from_samples_not_header(tmp_path):
    note = tmp_path / "liar.wav"
    _silence(note, 3)
    raw = bytearray(note.read_bytes())
    raw[40:44] = (0xFFFFFFF0).to_bytes(4, "little")    # data chunk claims about 4 GB
    note.write_bytes(bytes(raw))
    assert voice._pcm16k_seconds(note) == 3


afconvert_only = pytest.mark.skipif(not (shutil.which("afconvert") and shutil.which("ffmpeg")),
                                    reason="needs macOS afconvert, and ffmpeg to make the tones")


@afconvert_only
@pytest.mark.parametrize("suffix", ["wave", "mp3", "m4a"])
def test_afconvert_fallback_refuses_long_and_reads_short(suffix, tone, stub_whisper, monkeypatch):
    monkeypatch.setattr(voice, "_ffmpeg", lambda: None)
    long_ = voice.check_voice(str(tone(suffix, OVER)))
    assert _kinds(long_) == ["how_made", "too_long"]
    short = voice.check_voice(str(tone(suffix, UNDER)))
    assert _kinds(short)[1] == "transcript" and abs(_heard_seconds(short) - UNDER) < 0.5


@afconvert_only
def test_afconvert_decoded_length_is_checked_when_header_is_silent(tone, stub_whisper, monkeypatch):
    monkeypatch.setattr(voice, "_ffmpeg", lambda: None)
    monkeypatch.setattr(voice, "_afinfo_seconds", lambda src: None)
    cards = voice.check_voice(str(tone("m4a", OVER)))
    assert _kinds(cards) == ["how_made", "too_long"]
    assert cards[1].raw["measured_by"] == "decoded samples"


# ---------------------------------------------------------------- the card names the real reason

def test_bad_audio_is_not_blamed_on_the_model(tmp_path, stub_whisper):
    junk = tmp_path / "junk.m4a"
    junk.write_bytes(b"not audio at all" * 64)
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    for note in (junk, empty):
        cards = voice.check_voice(str(note))
        assert _kinds(cards) == ["how_made", "unreadable"], note
        assert "model" not in cards[1].finding


def test_oversize_file_says_so(tmp_path, monkeypatch):
    note = tmp_path / "note.wav"
    _silence(note, 1)
    monkeypatch.setattr(voice, "MAX_BYTES", 100)
    cards = voice.check_voice(str(note))
    assert _kinds(cards) == ["how_made", "too_big"]
    assert cards[1].raw["bytes"] > 100


def test_no_decoder_is_named(tmp_path, monkeypatch, stub_whisper):
    note = tmp_path / "note.mp3"
    note.write_bytes(b"ID3" + b"\x00" * 64)
    monkeypatch.setattr(voice, "_ffmpeg", lambda: None)
    real_which = shutil.which
    monkeypatch.setattr(voice.shutil, "which", lambda name, *a, **k: None if name == "afconvert" else real_which(name, *a, **k))
    cards = voice.check_voice(str(note))
    assert _kinds(cards) == ["how_made", "decoder_unavailable"]


def test_every_not_read_kind_has_a_complete_card():
    for kind, (finding, proves) in voice._NOT_READ.items():
        card = voice._status_card(kind, finding, proves)
        assert card.finding and card.proves and card.does_not_prove and card.strength == "none"
