"""Voice notes: a local transcript, the claims heard in it, then claim receipts.

How a voice note was made is ALWAYS "can't tell": Second Look has no check for how
audio was made (recorded, edited, cloned or synthesised), so this rung never says.
What it does:

1. Transcribes the note on this computer with whisper.cpp (MIT,
   https://github.com/ggml-org/whisper.cpp) and a pinned model file, checked by
   sha256 before use. Nothing is sent anywhere.
2. Picks out candidate claims: plain statements with enough content words to look up.
3. Looks each one up in the offline ClaimReview index (claim.py, S6) and returns the
   publishers' own ratings word for word, with credit and date. No verdict of its own.

Model: ggml-<name>.bin from https://huggingface.co/ggerganov/whisper.cpp (MIT), pinned to
revision MODEL_REVISION. Put it in ~/.cache/whisper or set SECONDLOOK_WHISPER_DIR.

Usage:
    python -m secondlook.rungs.voice note.m4a [--db claims.sqlite] [--model small.en]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path

from secondlook.rungs import claim as claim_rung
from secondlook.types import Evidence, Result

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPin:
    file: str
    sha256: str
    size: int


MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"   # ggerganov/whisper.cpp on Hugging Face
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/" + MODEL_REVISION + "/{file}"
PINNED_MODELS = {
    # sha256 and size as Hugging Face lists them for that revision (LFS oid), read 28 Oct 2026.
    "small.en": ModelPin("ggml-small.en.bin",
                         "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d", 487614201),
    "base.en": ModelPin("ggml-base.en.bin",
                        "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002", 147964211),
}
DEFAULT_MODEL = "small.en"
MODEL_DIR_ENV = "SECONDLOOK_WHISPER_DIR"
WHISPER_ENV = "SECONDLOOK_WHISPER_CLI"
DEFAULT_MODEL_DIR = Path.home() / ".cache" / "whisper"

AUDIO_EXTS = frozenset(".wav .wave .m4a .mp4 .aac .mp3 .ogg .oga .opus .flac .caf .aif .aiff .amr .webm".split())
MAX_BYTES = 50 * 1024 * 1024     # a voice note, not an album
MAX_SECONDS = 10 * 60            # longer notes are refused with a card that says so, never cut
DECODE_CAP_S = MAX_SECONDS + 1   # decoding stops 1 s past the limit: enough to tell over from under
TIMEOUT_S = 300
MAX_CLAIMS = 10
MIN_CLAIM_TERMS = 3              # content words a sentence needs before it is looked up
TRANSCRIPT_SHOWN_CHARS = 600

HOW_MADE = "We can't tell how this voice note was made."
NO_TRANSCRIPT = "No transcript: the local speech-recognition model is not available, so no claim was looked up."
NO_SPEECH = "No speech was heard in this voice note, so no claim was looked up."
TOO_LONG = (f"This voice note is longer than {MAX_SECONDS // 60} minutes, so it was not transcribed and nothing "
            f"said in it was looked up. Second Look reads notes of up to {MAX_SECONDS // 60} minutes in full "
            "and never cuts one short.")

# Why a note has no transcript: (kind, finding, proves). Each reason has its own card, so a bad
# file is never blamed on a missing model and a long note is never read in part without a word.
_NOT_READ = {
    "too_big": (f"This file is over {MAX_BYTES // (1024 * 1024)} MB, so it was not read and nothing said "
                "in it was looked up.",
                "That the file is larger than Second Look reads for a voice note."),
    "too_long": (TOO_LONG,
                 f"That the decoded audio runs past {MAX_SECONDS // 60} minutes. None of it was transcribed."),
    "unreadable": ("This file could not be read as audio, so no transcript was made and nothing was looked up.",
                   "That the file could not be decoded as audio on this computer."),
    "decoder_unavailable": ("No transcript: this computer has no decoder for this audio format (ffmpeg or "
                            "afconvert), so nothing was looked up.",
                            "That no audio decoder was found; the file itself may be fine."),
    "transcript_unavailable": (NO_TRANSCRIPT, "Nothing: no transcript was made, so nothing said was looked up."),
    "transcript_failed": ("No transcript: speech recognition did not finish on this voice note, so nothing "
                          "was looked up.",
                          "That the local speech-recognition run failed or ran out of time."),
}
SUMMARY = ("We can't tell how this voice note was made. The transcript is machine-made on this "
           "computer; any fact-check shown is the publisher's own rating of the claim it names.")


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Transcript:
    text: str
    segments: list[Segment]
    model: str
    model_sha256: str
    engine: str
    seconds: float                # length of the whole note, counted from its decoded samples
    raw: dict = field(default_factory=dict)


@dataclass
class NotRead:
    """No transcript, and why: a key of _NOT_READ, plus details for the card's raw."""
    kind: str
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------- transcription

_hash_cache: dict[tuple[str, int, int], str] = {}


def _sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _hash_cache:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _hash_cache[key] = h.hexdigest()
    return _hash_cache[key]


def model_path(name: str = DEFAULT_MODEL, warn: bool = True) -> Path | None:
    """The pinned model file, or None if it is missing or its sha256 differs from the pin. warn=False says
    nothing: the page's health poll and every transcription ask this, and the process says it once at start
    (log_setup_once) instead (DAT-850 A263)."""
    say = log.warning if warn else (lambda *a: None)
    pin = PINNED_MODELS.get(name) if isinstance(name, str) else None
    if pin is None:
        say("no pinned whisper model named %r", name)
        return None
    path = Path(os.environ.get(MODEL_DIR_ENV) or DEFAULT_MODEL_DIR) / pin.file
    try:
        if not path.is_file() or path.stat().st_size != pin.size:
            say("voice notes: off. The whisper model %s is not at %s; fetch it from %s", name, path,
                MODEL_URL.format(file=pin.file))
            return None
        if _sha256(path) != pin.sha256:
            say("voice notes: off. The whisper model at %s does not match its pinned sha256; not used", path)
            return None
    except OSError as e:
        say("voice notes: off. The whisper model is unreadable: %s", e)
        return None
    return path


_setup_logged = False


def log_setup_once() -> None:
    """Say once per process, at server start, why voice notes are off, and only where they would be used:
    whisper-cli is installed but its pinned model is missing or wrong. With no whisper-cli the page's own
    "not installed" line says it, and the terminal stays quiet. Never raises."""
    global _setup_logged
    if _setup_logged:
        return
    _setup_logged = True
    try:
        if _whisper_cli():
            model_path(DEFAULT_MODEL, warn=True)
    except Exception as e:  # a log line must never stop the server
        log.debug("voice setup check failed: %s", e)


def _whisper_cli() -> str | None:
    return shutil.which(os.environ.get(WHISPER_ENV) or "whisper-cli")


def _engine_version(cli: str) -> str:
    """whisper.cpp prints no version flag; the Homebrew Cellar path carries it when installed that way."""
    m = re.search(r"/whisper[.-]cpp/(\d[\w.]*)/", os.path.realpath(cli))
    return f"whisper.cpp {m.group(1)}" if m else "whisper.cpp"


def is_audio(path) -> bool:
    try:
        p = Path(path)
        return p.is_file() and p.suffix.lower() in AUDIO_EXTS
    except (TypeError, ValueError, OSError):
        return False


def _pcm16k_seconds(path: Path) -> float | None:
    """Seconds of audio in a 16 kHz mono 16-bit WAV, counted from the samples actually in the
    file (up to DECODE_CAP_S), not from its header. None for any other file."""
    try:
        with wave.open(str(path), "rb") as w:
            if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                return None
            frames, cap = 0, DECODE_CAP_S * 16000
            while frames < cap:
                chunk = w.readframes(min(60 * 16000, cap - frames))
                if not chunk:
                    break
                frames += len(chunk) // 2
            return frames / 16000
    except Exception:  # any header the wave module can't parse: not a 16 kHz WAV
        return None


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _afinfo_seconds(src: Path) -> float | None:
    """The length macOS afinfo reads from the file's header, or None."""
    afinfo = shutil.which("afinfo")
    if not afinfo:
        return None
    try:
        r = subprocess.run([afinfo, str(src)], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"estimated duration:\s*([\d.]+)\s*sec", r.stdout or "")
    return float(m.group(1)) if m else None


def _decode(src: Path, out: Path) -> NotRead | None:
    """Decode any audio to 16 kHz mono 16-bit WAV, the input whisper.cpp reads. None when
    `out` was written. ffmpeg stops DECODE_CAP_S in, so a long note costs no more than the
    limit to measure. afconvert (used only without ffmpeg) has no such stop, so a note whose
    header says it is over the limit is refused before decoding; the decoded length is still
    checked after."""
    ffmpeg, afconvert = _ffmpeg(), shutil.which("afconvert")
    if ffmpeg:
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
               "-t", str(DECODE_CAP_S), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(out)]
    elif afconvert:
        header = _afinfo_seconds(src)
        if header is not None and header > MAX_SECONDS:
            return NotRead("too_long", {"seconds_read": round(header, 3), "limit_seconds": MAX_SECONDS,
                                        "measured_by": "afinfo (file header), before decoding"})
        cmd = [afconvert, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(out)]
    else:
        log.warning("neither ffmpeg nor afconvert found; cannot read %s", src.suffix)
        return NotRead("decoder_unavailable")
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("audio conversion failed: %s", e)
        return NotRead("unreadable")
    if r.returncode != 0 or not out.is_file():
        return NotRead("unreadable")
    return None


def transcribe(path, model: str = DEFAULT_MODEL) -> Transcript | NotRead:
    """Transcribe a whole voice note on this computer, or say why not. The length is measured
    from the decoded samples before any transcript is made, for every format: a note over
    MAX_SECONDS is refused, never cut. Never raises."""
    try:
        src = Path(path)
        if not is_audio(src):
            return NotRead("unreadable")
        size = src.stat().st_size
        if size > MAX_BYTES:
            return NotRead("too_big", {"bytes": size, "limit_bytes": MAX_BYTES})
        with tempfile.TemporaryDirectory(prefix="secondlook-voice-") as tmp:
            wav, seconds = src, _pcm16k_seconds(src)
            if seconds is None:
                wav = Path(tmp) / "in.wav"
                failed = _decode(src, wav)
                if failed:
                    return failed
                seconds = _pcm16k_seconds(wav)
            if not seconds:
                return NotRead("unreadable", {"seconds_read": seconds})
            if seconds > MAX_SECONDS:
                return NotRead("too_long", {"seconds_read": round(seconds, 3), "limit_seconds": MAX_SECONDS,
                                            "measured_by": "decoded samples"})
            cli, mpath = _whisper_cli(), model_path(model, warn=False)  # said once at start, not per note
            if not cli or not mpath:
                if not cli:
                    log.warning("whisper-cli not found (brew install whisper-cpp, MIT)")
                return NotRead("transcript_unavailable")
            cmd = [cli, "-m", str(mpath), "-f", str(wav), "-l", "en", "-oj", "-of", str(Path(tmp) / "out"),
                   "-np", "-t", str(min(8, os.cpu_count() or 4))]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
            out = Path(tmp) / "out.json"
            if r.returncode != 0 or not out.is_file():
                log.warning("whisper-cli failed (exit %s): %s", r.returncode, (r.stderr or "")[-300:])
                return NotRead("transcript_failed", {"exit": r.returncode})
            data = json.loads(out.read_text(encoding="utf-8", errors="replace"))
        segs = []
        for s in data.get("transcription") or []:
            off = s.get("offsets") or {}
            text = (s.get("text") or "").strip()
            if text and not _is_marker(text):
                segs.append(Segment(int(off.get("from") or 0), int(off.get("to") or 0), text))
        return Transcript(
            text=" ".join(s.text for s in segs),
            segments=segs,
            model=model,
            model_sha256=PINNED_MODELS[model].sha256,
            engine=_engine_version(cli),
            seconds=seconds,
            raw={"model_file": PINNED_MODELS[model].file, "model_revision": MODEL_REVISION,
                 "language": (data.get("result") or {}).get("language")},
        )
    except Exception as e:  # a rung must never take the caller down
        log.warning("transcription failed: %s", e)
        return NotRead("transcript_failed")


_MARKER = re.compile(r"^[\[(][^\])]*[\])]$")   # whisper's "[BLANK_AUDIO]", "(music)" and the like


def _is_marker(text: str) -> bool:
    return bool(_MARKER.match(text.strip()))


# ---------------------------------------------------------------- claims

_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_LEAD_IN = re.compile(
    r"^(?:(?:so|well|okay|ok|um+|uh+|hey|hi|look|listen|guys|everyone)\b[,\s]*)*"
    r"(?:(?:i|we)\s+(?:just\s+)?(?:think|believe|heard|read|saw|guess|reckon)\s+(?:that\s+)?"
    r"|apparently[,\s]+|(?:they|people)\s+(?:say|are saying)\s+(?:that\s+)?)?",
    re.IGNORECASE,
)
_NOT_A_CLAIM = re.compile(
    r"^(?:please|can you|could you|would you|let me|let's|call me|text me|send|forward|share|thanks|thank you"
    r"|i (?:feel|hope|want|love|hate|miss)|bye|see you)\b",
    re.IGNORECASE,
)


def extract_claims(text, limit: int = MAX_CLAIMS) -> list[str]:
    """Candidate claims in a transcript: statements, not questions, requests or feelings,
    with at least MIN_CLAIM_TERMS content words once lead-ins like "I heard that" are
    dropped. Order kept, repeats dropped. Never raises."""
    if not isinstance(text, str):
        return []
    out: list[str] = []
    seen: set[frozenset] = set()
    for m in _SENTENCE.finditer(text):
        s = " ".join(m.group(0).split())
        if not s or s.endswith("?"):
            continue
        s = _LEAD_IN.sub("", s).strip(" ,")
        if not s or _NOT_A_CLAIM.match(s):
            continue
        key = frozenset(claim_rung.terms(s))
        if len(key) < MIN_CLAIM_TERMS or key in seen:
            continue
        seen.add(key)
        out.append(s[0].upper() + s[1:])
        if len(out) >= max(1, limit if isinstance(limit, int) else MAX_CLAIMS):
            break
    return out


def _when(t: Transcript, sentence: str) -> int | None:
    """Start time (ms) of the segment where a claim sentence begins, if it can be found."""
    probe = " ".join(sentence.split()[:3]).lower()
    for s in t.segments:
        if probe and probe in s.text.lower():
            return s.start_ms
    return None


# ---------------------------------------------------------------- evidence

def _how_made_card() -> Evidence:
    return Evidence(
        rung="claim",
        finding=HOW_MADE,
        proves="Nothing about how the audio was made: Second Look has no check for recorded, "
               "edited, cloned or synthesised voices.",
        does_not_prove="That the voice is or is not who it sounds like, or that the note was or was "
                       "not made with AI.",
        strength="none",
        raw={"kind": "how_made", "source": "voice"},
    )


def _transcript_card(t: Transcript, claims: list[str]) -> Evidence:
    shown = t.text if len(t.text) <= TRANSCRIPT_SHOWN_CHARS else t.text[:TRANSCRIPT_SHOWN_CHARS].rstrip() + "…"
    mins, secs = divmod(round(t.seconds), 60)
    return Evidence(
        rung="claim",
        finding=f"Transcript of the whole note ({mins}:{secs:02d}), made on this computer "
                f"({t.engine}, model {t.model}): “{shown}”",
        proves="What a speech-recognition model running on this computer heard. The audio was not "
               "sent anywhere.",
        does_not_prove="That these are exactly the words said (machine transcripts make mistakes and use "
                       "US spelling), who said them, or that anything said is true.",
        strength="none",
        raw={"kind": "transcript", "source": "voice", "transcript": dataclasses.asdict(t),
             "candidate_claims": claims},
    )


def _status_card(kind: str, finding: str, proves: str, extra: dict | None = None) -> Evidence:
    return Evidence(
        rung="claim",
        finding=finding,
        proves=proves,
        does_not_prove="Whether a fact-check of anything said exists. Second Look gives no true or "
                       "false verdict of its own.",
        strength="none",
        raw={"kind": kind, "source": "voice"} | (extra or {}),
    )


def check_voice(path, db=None, model: str = DEFAULT_MODEL) -> list[Evidence]:
    """Every card for one voice note: how it was made (always can't tell), the transcript,
    then any published fact-checks of the claims heard. [] for a path that is not audio.
    Never raises."""
    try:
        if not is_audio(path):
            return []
        cards = [_how_made_card()]
        t = transcribe(path, model)
        if isinstance(t, NotRead):
            finding, proves = _NOT_READ[t.kind]
            cards.append(_status_card(t.kind, finding, proves, t.raw))
            return cards
        claims = extract_claims(t.text)
        cards.append(_transcript_card(t, claims))
        if not t.text:
            cards.append(_status_card("no_speech", NO_SPEECH, "That the model heard no words."))
            return cards
        if not claims:
            cards.append(_status_card("no_claims", "No checkable claim was heard, so nothing was looked up.",
                                      "That no statement in the transcript was long enough to look up."))
            return cards
        found_any = False
        for c in claims:
            lookup = claim_rung.lookup_claim(c, db)
            if lookup.status == "unavailable":
                cards.append(claim_rung.to_evidence(lookup)[0])
                return cards
            heard = {"sentence": c, "start_ms": _when(t, c)}
            for e in claim_rung.to_evidence(lookup):
                e.raw = e.raw | {"source": "voice", "kind": "claim", "heard": heard}
                cards.append(e)
                found_any = True
        if not found_any:
            cards.append(_status_card(
                "no_match",
                f"{claim_rung.NO_MATCH_MESSAGE} ({len(claims)} claim(s) heard and looked up.)",
                "That the offline index holds no published fact-check close to the claims heard.",
                {"claims": claims},
            ))
        return cards
    except Exception as e:  # a rung must never take the caller down
        log.warning("voice rung failed: %s", e)
        return [_how_made_card()]


def check(path: str) -> list[Evidence]:
    """Contract signature: check(path) -> list[Evidence]. [] for anything that is not audio."""
    return check_voice(path)


def analyse(path, db=None, model: str = DEFAULT_MODEL) -> Result:
    """A Result for one voice note. The answer is always "cant_tell": nothing here can tell
    how audio was made, and a fact-check of what was said is not a verdict on the note."""
    from secondlook.ladder import _version
    return Result(answer="cant_tell", evidence=check_voice(path, db, model), summary=SUMMARY,
                  version=_version())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="secondlook-voice", description="Second Look voice note (in development)")
    p.add_argument("file")
    p.add_argument("--db", default=None, help=f"claim index (default ${claim_rung.DB_ENV} or {claim_rung.DEFAULT_DB})")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PINNED_MODELS))
    a = p.parse_args(argv)
    log_setup_once()  # why voice notes are off, once; transcribe() stays quiet
    json.dump(dataclasses.asdict(analyse(a.file, a.db, a.model)), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
