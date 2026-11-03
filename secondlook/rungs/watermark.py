"""Watermark rung: is Second Look's OWN invisible mark in this image? Demo only.

Model: Meta's Video Seal 1.0, image model y_256b_img, as the published TorchScript file
(https://github.com/facebookresearch/videoseal, docs/torchscript.md; MIT, "The model is licensed
under an MIT license", README). Pinned below by sha256. It needs only torch, and runs locally on MPS.

`secondlook mark <in> <out>` embeds one fixed 256-bit message (ours). check() reads 256 bits back and
counts how many match that message. With no mark, each bit is a coin toss, so matches sit near 128;
we report the mark as present only at MIN_MATCH or more (p about 2e-9 for fair coin bits, fixed here
in advance). The mark survives JPEG q70 and resizing in our own run (docs/watermark-robustness.md).

What it can't do, and we never say it does:
- It does not detect Meta AI images, or any company's mark. It only looks for our own message.
- The demo message comes from a key in this public source code, so anyone with the open model can
  add it. A present mark means "someone embedded this message", not "Second Look made this".
- The absence of a mark says nothing about how the image was made.
"""
from __future__ import annotations

import hashlib
import logging
import os
import stat
import threading
import time
from math import comb
from pathlib import Path

from ..types import Evidence, FailedToLoad, NotInstalled

log = logging.getLogger("secondlook.rungs.watermark")

MODEL_NAME = "Meta Video Seal 1.0 (image model y_256b_img, TorchScript)"
MODEL_URL = "https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit"
MODEL_SHA256 = "5c7a4581c36fc6090aafdcfb3999123bae5172a4847f22e2da4e7fd1a39d1e1b"
NBITS = 256
MIN_MATCH = 175  # of 256; fixed before any test. P(>= 175 | fair coin bits) = 2.08e-9
DEMO_KEY = "second-look demo mark v1"
MAX_PIXELS = 100_000_000  # same caps as the classifier rung, checked from the header
MAX_ASPECT = 8

DEFAULT_WEIGHTS = Path.home() / ".cache" / "secondlook" / "models" / "videoseal" / "y_256b_img.jit"

LIMIT = "The absence of a mark says nothing about how the image was made."
ONLY_OURS = ("This looks only for Second Look's own mark. It does not detect Meta AI images "
             "or any other company's watermark.")

_lock = threading.Lock()
_model = None
_device = None


def weights_path() -> Path:
    return Path(os.environ.get("SECONDLOOK_VIDEOSEAL_WEIGHTS") or DEFAULT_WEIGHTS)


def key() -> str:
    return os.environ.get("SECONDLOOK_WATERMARK_KEY") or DEMO_KEY


def message_bits(k: str | None = None) -> list[int]:
    """The 256 bits we embed: sha256 of the key, most significant bit first."""
    digest = hashlib.sha256((k if k is not None else key()).encode()).digest()
    return [(byte >> (7 - i)) & 1 for byte in digest for i in range(8)]


def key_id(k: str | None = None) -> str:
    """A short id for the key, safe to show; it is not the key."""
    return hashlib.sha256(b"key-id:" + (k if k is not None else key()).encode()).hexdigest()[:12]


def p_value(matched: int, n: int = NBITS) -> float:
    """Chance of matched or more of n bits if each bit were a fair coin toss (no mark)."""
    return sum(comb(n, i) for i in range(max(matched, 0), n + 1)) / 2 ** n


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_weights(dest: Path | None = None) -> Path:
    """Download the pinned MIT TorchScript file once and check its sha256. Free, no account."""
    import urllib.request

    dest = dest or weights_path()
    if dest.exists() and sha256_file(dest) == MODEL_SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(MODEL_URL, tmp)
    got = sha256_file(tmp)
    if got != MODEL_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"weights sha256 {got} != pinned {MODEL_SHA256}")
    tmp.replace(dest)
    return dest


def pick_device() -> str:
    import torch

    want = os.environ.get("SECONDLOOK_DEVICE")
    if want:
        return want
    return "mps" if torch.backends.mps.is_available() else "cpu"


def load_model():
    """Load the pinned TorchScript model, after checking its sha256. Cached; thread safe."""
    global _model, _device
    with _lock:
        if _model is not None:
            return _model, _device
        import warnings

        import torch

        path = weights_path()
        got = sha256_file(path)
        if got != MODEL_SHA256:
            raise FailedToLoad(f"weights at {path} have sha256 {got}, not the pinned {MODEL_SHA256}")
        try:
            with warnings.catch_warnings():  # torch.jit.load is deprecated in favour of torch.export
                warnings.simplefilter("ignore", FutureWarning)
                model = torch.jit.load(str(path), map_location="cpu")
        except Exception as exc:
            raise FailedToLoad(f"weights at {path} would not load: {exc!r}") from exc
        device = pick_device()
        model.eval().to(device)
        _model, _device = model, device
        return _model, _device


class CantAnalyse(ValueError):
    """Not a file we can read safely (not a regular file, too many pixels, too thin)."""


def _open_rgb(path: str):
    from PIL import Image, ImageOps

    if not stat.S_ISREG(os.stat(path).st_mode):  # a FIFO would block Image.open forever
        raise CantAnalyse(f"{path} is not a regular file")
    with Image.open(path) as im:
        w, h = im.size  # header only; nothing decoded yet
        if w < 1 or h < 1 or w * h > MAX_PIXELS or max(w, h) > MAX_ASPECT * min(w, h):
            raise CantAnalyse(f"image is {w} x {h}, outside the caps")
        return ImageOps.exif_transpose(im).convert("RGB")


def _to_tensor(img, device):
    from torchvision.transforms.functional import to_tensor

    return to_tensor(img).unsqueeze(0).float().to(device)


def read_bits(img) -> list[int]:
    """The 256 bits the detector reads from a PIL image."""
    import torch

    model, device = load_model()
    with torch.inference_mode():
        preds = model.detect(_to_tensor(img, device))
    if preds.dim() == 4:  # [B, 1+K, H, W]: average over pixels
        preds = preds.mean(dim=(-1, -2))
    return [int(v > 0) for v in preds[0, 1:].float().cpu().tolist()]


def matches(img, k: str | None = None) -> int:
    want = message_bits(k)
    got = read_bits(img)
    return sum(int(a == b) for a, b in zip(got, want))


def embed(img, k: str | None = None):
    """Return a new PIL image carrying our message."""
    import torch
    from torchvision.transforms.functional import to_pil_image

    model, device = load_model()
    msg = torch.tensor([message_bits(k)], dtype=torch.float32, device=device)
    with torch.inference_mode():
        out = model.embed(_to_tensor(img, device), msg)
    return to_pil_image(out[0].clamp(0, 1).cpu())


def mark(src: str, dst: str, force: bool = False) -> dict:
    """Embed our mark in src and write dst (format from its extension; JPEG at quality 95).
    Reads dst back and reports how many bits match. Raises on bad input; the CLI reports it."""
    from PIL import Image

    s, d = Path(src), Path(dst)
    if d.exists() and (not force or d.resolve() == s.resolve()):
        raise FileExistsError(f"{dst} exists; pass --force to replace it (never the input itself)")
    fmt = Image.registered_extensions().get(d.suffix.lower())
    if fmt is None:
        raise ValueError(f"can't tell the output format from {dst!r}; use .png, .jpg or .webp")
    img = _open_rgb(src)
    t0 = time.perf_counter()
    wm = embed(img)
    seconds = time.perf_counter() - t0
    opts = {"quality": 95} if fmt in ("JPEG", "WEBP") else {}
    tmp = d.with_name(d.name + ".part")
    try:
        wm.save(tmp, format=fmt, **opts)  # no partial file at dst if the save fails
        tmp.replace(d)
    finally:
        tmp.unlink(missing_ok=True)
    with Image.open(d) as back:
        got = matches(back.convert("RGB"))
    return {"in": str(s), "out": str(d), "model": MODEL_NAME, "weights_sha256": MODEL_SHA256,
            "key_id": key_id(), "bits": NBITS, "matched_after_save": got, "min_match": MIN_MATCH,
            "present": got >= MIN_MATCH, "device": _device, "seconds": round(seconds, 4)}


def check(path: str) -> list[Evidence]:
    """Read our mark from one image.

    Two kinds of "no card", kept apart on purpose:
    - This file can't be read safely (bad, missing, extreme): return []. The rung had nothing to say.
    - The rung can't run at all (weights missing or failing the sha256, torch missing, model won't
      load or gives nonsense): RAISE. The ladder catches that, leaves the rung out of "ran" and lists
      it under "These checks could not run", so a broken setup never reads as "ran, found nothing".
    """
    if not weights_path().exists():
        raise NotInstalled(f"Video Seal weights not found at {weights_path()}; "
                           "run python -m secondlook.rungs.watermark --fetch")
    try:
        img = _open_rgb(path)  # refuse bad files before paying for the model load
    except ImportError:  # Pillow missing: the rung can't run
        raise
    except Exception as exc:  # bad or extreme file: nothing for this rung to say about it
        log.info("watermark rung can't read %s: %s", path, exc)
        return []
    load_model()  # one-off load stays out of the per-image time; raises on a missing torch or bad sha256
    t0 = time.perf_counter()
    got = matches(img)
    seconds = time.perf_counter() - t0
    if not 0 <= got <= NBITS:
        raise RuntimeError(f"watermark rung read {got!r} matching bits for {path}; not a usable result")

    present = got >= MIN_MATCH
    raw = {"present": present, "matched_bits": got, "bits": NBITS, "min_match": MIN_MATCH,
           "p_value_if_unmarked": p_value(got), "key_id": key_id(), "demo_key": key() == DEMO_KEY,
           "model": MODEL_NAME, "weights_sha256": MODEL_SHA256, "device": _device,
           "seconds": round(seconds, 4)}
    if present:
        finding = (f"Second Look's own invisible watermark is present: {got} of {NBITS} bits match our "
                   f"message (we count {MIN_MATCH} or more as present).")
        proves = ("Someone embedded our message with Meta's open Video Seal model, as `secondlook mark` "
                  "does, into this image or an earlier copy of it.")
        dnp = ("It does not show who made the image or how: our demo key is public, so anyone can add this "
               f"mark. {ONLY_OURS} {LIMIT}")
        return [Evidence(rung="watermark", finding=finding, proves=proves, does_not_prove=dnp,
                         strength="signal", raw=raw)]
    finding = (f"Second Look's own watermark was not found: {got} of {NBITS} bits match our message, "
               f"near the {NBITS // 2} expected by chance.")
    return [Evidence(rung="watermark", finding=finding,
                     proves="Only that our own mark is not readable in this copy.",
                     does_not_prove=f"{LIMIT} {ONLY_OURS}", strength="none", raw=raw)]


def _main(argv=None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(prog="python -m secondlook.rungs.watermark")
    p.add_argument("--fetch", action="store_true", help="download the pinned weights")
    p.add_argument("files", nargs="*")
    a = p.parse_args(argv)
    if a.fetch:
        print(fetch_weights())
    for f in a.files:
        print(json.dumps({"file": f, "matched_bits": matches(_open_rgb(f)), "min_match": MIN_MATCH}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
