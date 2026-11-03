# W9 voice notes (DAT-851, Stage B): a local transcript, then claim receipts

`secondlook/rungs/voice.py` takes a voice note and returns evidence cards. The answer for a
voice note is always `cant_tell`: Second Look has no check for how audio was made.

## What it does

1. **How it was made: can't tell.** The first card always says "We can't tell how this voice
   note was made." `analyse()` always returns `answer="cant_tell"`, whatever is found.
2. **Local transcript.** whisper.cpp (`whisper-cli`, MIT, https://github.com/ggml-org/whisper.cpp)
   runs on this computer. Non-WAV audio (m4a, mp3, ogg and others) is converted to 16 kHz mono
   WAV with ffmpeg, or with macOS `afconvert` when ffmpeg is missing. Nothing is sent anywhere.
3. **Candidate claims.** Statements with at least 3 content words. Questions, requests
   ("please forward this") and feelings ("I hope") are dropped, as are lead-ins such as
   "I heard that" and "apparently". At most 10 claims, in order, with no repeats.
4. **Claim receipts.** Each claim goes through S6's `lookup_claim` (branch `s6-claims` @
   `348a05d`, merged into this branch). The cards are S6's own, word for word, with
   `raw.heard` added: the sentence and the time in the note where it starts. When the index
   can't be read, the card says that, never "no fact-check found".

## Pinned model

| Name | File | sha256 | Size |
|---|---|---|---|
| `small.en` (default) | `ggml-small.en.bin` | `c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d` | 487,614,201 |
| `base.en` | `ggml-base.en.bin` | `a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002` | 147,964,211 |

Source: https://huggingface.co/ggerganov/whisper.cpp at revision
`5359861c739e955e79d9a303bcbc70fb988958b1` (licence MIT on the model card). The hashes are the
LFS sha256 values the Hugging Face API lists for that revision, read 28 Oct 2026. The downloaded
small.en file and the cached base.en file both match them. A model file whose size or sha256
differs is not used.

```bash
brew install whisper-cpp            # whisper.cpp 1.8.4 was used here
mkdir -p ~/.cache/whisper && curl -L -o ~/.cache/whisper/ggml-small.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-small.en.bin
python -m secondlook.rungs.voice note.m4a --db claims.sqlite
```

`SECONDLOOK_WHISPER_DIR` points at another model folder and `SECONDLOOK_WHISPER_CLI` at
another `whisper-cli`.

## Limits found while building it

- **Spelling.** The English models write US spelling. In a check against S6's index,
  "The mayor of Springfield was hospitalized after a sudden illness." found nothing, while the
  same sentence with "hospitalised" matched. So a voice note can miss a fact-check written in
  Australian or British spelling. This is a recall loss, not a wrong match, and S6's strict
  matching is unchanged.
- **Capitals.** The model may write "National Museum" for "national museum". S6 folds case,
  so the lookup is unaffected.
- **Contract field.** `Evidence.rung` has no `voice` value, and `types.py` belongs to S1, so
  every card here uses `rung="claim"`, with `raw.kind` set to one of `how_made`, `transcript`,
  `transcript_unavailable`, `no_speech`, `no_claims`, `claim` or `no_match`, and with
  `raw.source="voice"`. Adding `voice` to the Literal is a question for S1. These cards never
  move `ladder.decide` away from `cant_tell`, and a test checks that.
- Not in the default image rungs (`DEFAULT_RUNGS`), so `secondlook check <image>` is unchanged.
  For anything that is not an audio file, `check(path)` returns `[]`.
- Notes over 50 MB are refused, with a `too_big` card.
- **Notes over 10 minutes are refused, never cut** (fix round for a reviewer's F1 at
  `6262a54`). See "Length limit" below.
- Each reason for no transcript has its own card, so a bad file is never blamed on the model
  (F2): `too_big`, `too_long`, `unreadable`, `decoder_unavailable`, `transcript_unavailable`
  (whisper-cli or the pinned model is missing) and `transcript_failed`.

## Length limit: refused, not cut

Every note is measured before any transcript, from its decoded samples rather than from a
header field:

- **16 kHz mono 16-bit WAV** goes to whisper.cpp as it is. Its length is counted from the
  samples actually in the file, so a header that claims 4 GB does not count.
- **Every other format** (other WAV, MP3, M4A, OGG/Opus and the rest) is decoded with ffmpeg,
  which stops 601 s in (`DECODE_CAP_S`). The decoded length then tells over from under exactly,
  and a long note costs no more than the limit to measure. Before this fix ffmpeg stopped at
  exactly 600 s, so a 12-minute m4a measured 600.0 s and was transcribed in part with no notice.
- **Without ffmpeg**, macOS `afconvert` decodes instead. It has no way to stop early, so a note
  whose header (`afinfo`) says it is over the limit is refused before decoding (a reviewer's
  F3: no multi-GB temp file from an honest file), and the decoded length is still checked after.

A note over 600 s gets a `too_long` card: "This voice note is longer than 10 minutes, so it was
not transcribed and nothing said in it was looked up. Second Look reads notes of up to 10 minutes
in full and never cuts one short." `raw` carries `seconds_read`, `limit_seconds` and
`measured_by`. The answer stays `cant_tell`. A note under the limit is transcribed whole (no
`-d` cap is passed to whisper.cpp), and the transcript card shows its length, for example
"Transcript of the whole note (9:55)".

**Why refuse rather than transcribe in full:** the 50 MB cap allows hours of audio at
voice-note bitrates (50 MB of Opus at 24 kbit/s is about 4.9 hours), and each external call has a
300 s timeout, so a full transcript of every file under the size cap cannot be promised. A part
transcript is the silent miss this round fixes: claims after the cut are never looked up. A
refusal is complete, bounded and says what happened.

## Tests

`tests/test_voice.py` has 40 tests. The speech is synthetic, made with macOS `say` into WAV
and m4a files, and the fact-checks come from a synthetic ClaimReview feed. The end-to-end
tests are skipped when `say`, `whisper-cli` or the pinned model is missing.

The length tests use generated 440 Hz sine tones only (ffmpeg `lavfi`), 5 s under and 5 s over
the limit, in 16 kHz WAV, 22.05 kHz WAV, MP3, M4A and OGG/Opus. Two more check the exact
boundary (600 s read, 600.5 s refused) and the old failure (an m4a 1 s over). The afconvert path
is tested for WAV, MP3 and M4A, also with the header check switched off. A stub `whisper-cli`
records the audio it was given, so these tests prove the whole note reached the transcriber, and
that a refused note never reached it, without needing the model. They are skipped without
ffmpeg (or afconvert, for its tests).
