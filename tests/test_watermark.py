"""Watermark rung (W8, DAT-795). Fast tests use a fake model; the real-model tests run only when the
pinned Video Seal weights are on disk (python -m secondlook.rungs.watermark --fetch)."""
import io
import json
import os

import pytest

from secondlook import cli
from secondlook.ladder import decide, run
from secondlook.rungs import load, watermark as wm
from secondlook.rungs.stub import make

torch = pytest.importorskip("torch")
Image = pytest.importorskip("PIL.Image")

REAL = wm.weights_path().exists() and wm.sha256_file(wm.weights_path()) == wm.MODEL_SHA256
FORBIDDEN = ("detects ai", "meta ai image", "is real", "accurate", "authentic")


class FakeModel:
    """Always reads back the same 256 bits, so a test can set how many match our message."""

    def __init__(self, bits):
        self.bits = bits

    def detect(self, x):
        signs = torch.tensor([1.0 if b else -1.0 for b in self.bits])
        return torch.cat([torch.zeros(1), signs])[None]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    w = tmp_path / "w.jit"; w.write_bytes(b"fake")
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(w))

    def use(bits):
        monkeypatch.setattr(wm, "_model", FakeModel(bits))
        monkeypatch.setattr(wm, "_device", "cpu")
    return use


def png(tmp_path, name="a.png", size=(64, 48)):
    p = tmp_path / name
    Image.new("RGB", size, (120, 80, 40)).save(p)
    return str(p)


def flip(bits, n):
    return [1 - b if i < n else b for i, b in enumerate(bits)]


def test_message_is_256_bits_from_the_key():
    bits = wm.message_bits()
    assert len(bits) == 256 and set(bits) <= {0, 1}
    assert bits == wm.message_bits(wm.DEMO_KEY) != wm.message_bits("other key")
    assert wm.key_id() != wm.DEMO_KEY and len(wm.key_id()) == 12


def test_threshold_is_fixed_and_strict():
    assert wm.MIN_MATCH == 175
    assert wm.p_value(wm.MIN_MATCH) < 1e-8
    assert wm.p_value(128) > 0.4
    assert wm.p_value(0) == 1.0 and wm.p_value(256) == 2 ** -256


def test_present_is_signal_with_limits(fake, tmp_path):
    fake(wm.message_bits())
    [e] = wm.check(png(tmp_path))
    assert e.rung == "watermark" and e.strength == "signal"
    assert e.raw["present"] is True and e.raw["matched_bits"] == 256
    assert "the absence of a mark says nothing about how the image was made" in e.does_not_prove.lower()
    assert "does not detect meta ai images" in e.does_not_prove.lower()


def test_threshold_edges(fake, tmp_path):
    want = wm.message_bits()
    fake(flip(want, 256 - wm.MIN_MATCH))
    assert wm.check(png(tmp_path))[0].raw["present"] is True
    fake(flip(want, 256 - wm.MIN_MATCH + 1))
    [e] = wm.check(png(tmp_path))
    assert e.raw["present"] is False and e.strength == "none"


def test_absent_says_absence_proves_nothing(fake, tmp_path):
    fake(flip(wm.message_bits(), 128))
    [e] = wm.check(png(tmp_path))
    assert e.raw["present"] is False and e.raw["matched_bits"] == 128
    assert e.does_not_prove.startswith("The absence of a mark says nothing about how the image was made.")


def test_wording_never_overclaims(fake, tmp_path):
    for bits in (wm.message_bits(), flip(wm.message_bits(), 128)):
        fake(bits)
        e = wm.check(png(tmp_path))[0]
        for text in (e.finding, e.proves):
            assert not any(f in text.lower() for f in FORBIDDEN), text


def test_watermark_never_changes_the_answer(fake, tmp_path):
    fake(wm.message_bits())
    present = wm.check(png(tmp_path))
    assert decide(present)[0] == "cant_tell"
    camera = make("c2pa", "signed", valid=True, trusted=True, source_class="camera", ai_ingredient=False)
    assert decide(present + [camera])[0] == "signed_camera"


def test_never_raises_on_bad_input(fake, tmp_path):
    fake(wm.message_bits())
    junk = tmp_path / "junk.jpg"; junk.write_bytes(b"not an image")
    assert wm.check(str(junk)) == []
    assert wm.check(str(tmp_path / "missing.png")) == []
    assert wm.check(str(tmp_path)) == []
    assert wm.check(png(tmp_path, "thin.png", (900, 10))) == []
    fifo = tmp_path / "fifo"; os.mkfifo(fifo)
    assert wm.check(str(fifo)) == []


def test_missing_weights_raises_not_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(tmp_path / "nope.jit"))
    with pytest.raises(RuntimeError, match="weights not found"):
        wm.check(png(tmp_path))


def test_wrong_weights_refused(monkeypatch, tmp_path):
    w = tmp_path / "w.jit"; w.write_bytes(b"tampered")
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(w))
    monkeypatch.setattr(wm, "_model", None)
    with pytest.raises(RuntimeError, match="not the pinned"):
        wm.load_model()
    with pytest.raises(RuntimeError, match="not the pinned"):
        wm.check(png(tmp_path))


def test_unusable_bit_count_raises(fake, tmp_path, monkeypatch):
    fake(wm.message_bits())
    monkeypatch.setattr(wm, "matches", lambda img, k=None: 999)
    with pytest.raises(RuntimeError, match="not a usable result"):
        wm.check(png(tmp_path))


def test_ladder_lists_missing_weights_as_could_not_run(monkeypatch, tmp_path):
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(tmp_path / "nope.jit"))
    r = run(png(tmp_path), rungs=[("watermark", wm.check)])
    assert r.answer == "cant_tell"
    assert [e for e in r.evidence if e.rung == "watermark"] == []
    card = next(e for e in r.evidence if e.raw.get("limit"))
    assert card.raw["rungs_failed"] == ["watermark"] and card.raw["rungs_run"] == []
    assert "could not run: watermark" in card.finding


def test_ladder_lists_wrong_weights_as_could_not_run(monkeypatch, tmp_path):
    w = tmp_path / "w.jit"; w.write_bytes(b"tampered")
    monkeypatch.setenv("SECONDLOOK_VIDEOSEAL_WEIGHTS", str(w))
    monkeypatch.setattr(wm, "_model", None)
    r = run(png(tmp_path), rungs=[("watermark", wm.check)])
    card = next(e for e in r.evidence if e.raw.get("limit"))
    assert card.raw["rungs_failed"] == ["watermark"]
    assert "could not run: watermark" in card.finding


def test_ladder_counts_a_working_rung_as_run(fake, tmp_path):
    fake(wm.message_bits())
    r = run(png(tmp_path), rungs=[("watermark", wm.check)])
    assert [e.rung for e in r.evidence] == ["watermark", "origin"]
    card = r.evidence[-1]
    assert card.raw["rungs_run"] == ["watermark"] and card.raw["rungs_failed"] == []


def test_registered_as_a_default_rung():
    assert "watermark" in dict(load(("watermark",)))


def test_mark_refuses_to_overwrite(tmp_path, capsys):
    src = png(tmp_path)
    assert cli.main(["mark", src, src, "--force"]) == 1
    out = tmp_path / "o.png"; out.write_bytes(b"keep")
    assert cli.main(["mark", src, str(out)]) == 1
    assert out.read_bytes() == b"keep"
    assert cli.main(["mark", src, str(tmp_path / "o.unknownext")]) == 1
    assert "secondlook mark:" in capsys.readouterr().err


# Real model: mark, then read back through the same paths a user takes.

real = pytest.mark.skipif(not REAL, reason="pinned Video Seal weights not downloaded")


@pytest.fixture
def photo(tmp_path):
    # Smooth gradient plus noise, 640 x 480: stands in for a photo without shipping one.
    g = torch.linspace(0, 1, 640)[None, None, :].expand(3, 480, 640)
    x = (0.6 * g + 0.4 * torch.rand(3, 480, 640, generator=torch.Generator().manual_seed(7))).clamp(0, 1)
    p = tmp_path / "photo.png"
    Image.fromarray((x.permute(1, 2, 0).numpy() * 255).astype("uint8")).save(p)
    return str(p)


@pytest.fixture
def real_model(monkeypatch):
    monkeypatch.delenv("SECONDLOOK_WATERMARK_KEY", raising=False)
    monkeypatch.setattr(wm, "_model", None)  # drop any fake left by an earlier test


@real
def test_real_mark_then_check(real_model, photo, tmp_path, capsys):
    out = tmp_path / "marked.png"
    assert cli.main(["mark", photo, str(out)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["present"] is True and info["matched_after_save"] >= wm.MIN_MATCH
    [e] = wm.check(str(out))
    assert e.raw["present"] is True and e.strength == "signal"
    [u] = wm.check(photo)
    assert u.raw["present"] is False


@real
def test_real_survives_jpeg_q70_and_resize(real_model, photo, tmp_path):
    marked = wm.embed(wm._open_rgb(photo))
    buf = io.BytesIO(); marked.save(buf, "JPEG", quality=70)
    assert wm.matches(Image.open(io.BytesIO(buf.getvalue())).convert("RGB")) >= wm.MIN_MATCH
    half = marked.resize((320, 240), Image.BICUBIC)
    assert wm.matches(half) >= wm.MIN_MATCH


@real
def test_real_other_key_is_not_ours(real_model, photo, monkeypatch):
    marked = wm.embed(wm._open_rgb(photo), k="someone else's key")
    assert wm.matches(marked) < wm.MIN_MATCH


@real
def test_real_full_ladder_shows_card(real_model, photo, tmp_path):
    out = tmp_path / "m.jpg"
    wm.mark(photo, str(out))
    r = run(str(out), rungs=[("watermark", wm.check)])
    assert r.answer == "cant_tell"
    # cant_tell adds the ladder's own limit card; look only at the watermark card
    assert [e.raw["present"] for e in r.evidence if e.rung == "watermark"] == [True]
