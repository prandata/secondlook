"""Classifier rung: Community Forensics (Park and Owens, CVPR 2025), run locally.

Code: https://github.com/JeongsooP/Community-Forensics (MIT).
Weights: https://huggingface.co/OwensLab/commfor-model-384 (MIT), pinned below by revision and sha256.

It reports a score and never a verdict. The score counts as a concern only when it is at or
above a threshold frozen in advance on the L4 dev split (classifier_threshold.json, schema
second-look-threshold/2, the L2 harness's format) AND that exact threshold file passed its
pre-registered gate (classifier_gate.json names the threshold sha256 it tested; the project lead's
ruling on DAT-850: the gate gates the product). With no frozen threshold the card still shows
the score, and above_threshold is False. With a frozen threshold but no PASS for its sha256 (no
status file, FAIL, INVALID, a status for another threshold, an unreadable file) the card shows the
score, the threshold and the test result, and raises_concern is False, so the ladder can't act on it.

Preprocessing is upstream's test transform (dataloader.get_transform, mode="test"): resize the
short side to 440, centre crop 384, scale to [0, 1], ImageNet mean and std; sigmoid of the one
logit. Upstream does not apply EXIF orientation, so neither do we.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..types import Evidence

log = logging.getLogger("secondlook.rungs.classifier")

MODEL_NAME = "Community Forensics ViT-S/16 384 (OwensLab/commfor-model-384)"
MODEL_REPO = "OwensLab/commfor-model-384"
MODEL_REVISION = "6076002bf0d9dd37537f965ee2f06f826c333b61"
MODEL_SHA256 = "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
TIMM_ARCH = "vit_small_patch16_384.augreg_in21k_ft_in1k"
RESIZE, CROP = 440, 384
MEAN, STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
# Checked from the header, before any pixel is decoded or resized. Resize(440) scales the short side,
# so a 30000 x 3 PNG of 356 bytes would grow to about 4.4M x 440 (8.4 GB RSS measured, S3 review).
MAX_PIXELS = 100_000_000  # decoded size; above Pillow's own 89.5M warning, below its 179M error
MAX_ASPECT = 8            # long side / short side; caps the resized long side at 8 x 440 = 3520

DEFAULT_WEIGHTS = Path.home() / ".cache" / "secondlook" / "models" / "commfor-model-384" / "model.safetensors"
DEFAULT_THRESHOLD = Path(__file__).with_name("classifier_threshold.json")
DEFAULT_GATE = Path(__file__).with_name("classifier_gate.json")
GATE_SCHEMA = "second-look-gate-status/1"
GATE_RESULTS = ("PASS", "FAIL", "INVALID")
# How a failed arm reads on the card (gate_run.ARMS).
ARM_WORDS = {"original": "photos in their original format", "format_matched": "photos re-saved as JPEG"}

PROVES = ("The image shares statistical traits with the AI-generated images this model was trained on, "
          "at a level we fixed in advance on separate test images.")
DOES_NOT_PROVE = ("It is not proof that the image was made by AI, or by a camera. The model can be wrong, "
                  "especially on generators it never saw, heavy compression, screenshots and edited photos.")
DOES_NOT_PROVE_LOW = ("A low score is not proof that a camera took it. Many AI images score low, "
                      "especially after compression or from generators the model never saw.")

_lock = threading.Lock()
_model = None
_device = None


def weights_path() -> Path:
    return Path(os.environ.get("SECONDLOOK_COMMFOR_WEIGHTS") or DEFAULT_WEIGHTS)


def threshold_path() -> Path:
    return Path(os.environ.get("SECONDLOOK_CLASSIFIER_THRESHOLD") or DEFAULT_THRESHOLD)


def gate_path() -> Path:
    return Path(os.environ.get("SECONDLOOK_CLASSIFIER_GATE") or DEFAULT_GATE)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_weights(dest: Path | None = None) -> Path:
    """Download the pinned MIT weights once and check their sha256. Free, no account."""
    import urllib.request

    dest = dest or weights_path()
    if dest.exists() and sha256_file(dest) == MODEL_SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/model.safetensors"
    tmp = dest.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
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
    """Build the ViT and load the pinned weights (strict). Cached; thread safe."""
    global _model, _device
    with _lock:
        if _model is not None:
            return _model, _device
        import timm
        import torch
        from safetensors.torch import load_file

        path = weights_path()
        got = sha256_file(path)
        if got != MODEL_SHA256:
            raise RuntimeError(f"weights at {path} have sha256 {got}, not the pinned {MODEL_SHA256}")
        model = timm.create_model(TIMM_ARCH, pretrained=False, num_classes=1)
        state = {k.removeprefix("vit."): v for k, v in load_file(str(path)).items()}
        model.load_state_dict(state, strict=True)
        device = pick_device()
        model.eval().to(device)
        _model, _device = model, device
        torch.set_grad_enabled(False)
        return _model, _device


def _transform():
    from torchvision import transforms

    return transforms.Compose([
        transforms.Resize(RESIZE),
        transforms.CenterCrop(CROP),
        transforms.ToTensor(),
        transforms.Normalize(mean=MEAN, std=STD),
    ])


class CantAnalyse(ValueError):
    """The file is not one we can score safely (not a regular file, too many pixels, too thin)."""


def _check_size(w: int, h: int) -> None:
    if w < 1 or h < 1:
        raise CantAnalyse(f"image is {w} x {h}")
    if w * h > MAX_PIXELS:
        raise CantAnalyse(f"image is {w} x {h} = {w * h} pixels, over the {MAX_PIXELS} cap")
    if max(w, h) > MAX_ASPECT * min(w, h):
        raise CantAnalyse(f"image is {w} x {h}, longer than {MAX_ASPECT}:1")


def score_images(paths: list[str]) -> list[float]:
    """Score image files in one batch. Raises on unreadable or extreme files; check() is the safe wrapper."""
    import torch
    from PIL import Image

    model, device = load_model()
    tf = _transform()
    batch = []
    for p in paths:
        if not stat.S_ISREG(os.stat(p).st_mode):  # a FIFO would block Image.open forever
            raise CantAnalyse(f"{p} is not a regular file")
        with Image.open(p) as im:
            _check_size(*im.size)  # header only; nothing decoded yet
            batch.append(tf(im.convert("RGB")))
    x = torch.stack(batch).to(device)
    with torch.inference_mode():
        out = torch.sigmoid(model(x).float()).flatten().cpu().tolist()
    return [float(s) for s in out]


def _text(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _frozen_problem(cfg) -> str | None:
    """Why this is not a frozen threshold for these weights, or None. Same rules as the L2
    harness's gate.harness.load_threshold, plus the threshold must lie in [0, 1]."""
    if not isinstance(cfg, dict):
        return "not a JSON object"
    if cfg.get("schema") != "second-look-threshold/2" or cfg.get("ai_if") != "score>=threshold":
        return "unexpected schema or rule"
    clf = cfg.get("classifier")
    if not isinstance(clf, dict) or not all(_text(clf.get(k)) for k in ("name", "version", "weights_sha256")):
        return "classifier name, version and weights_sha256 are required"
    if clf["weights_sha256"] != MODEL_SHA256:
        return "frozen for other weights"
    t = cfg.get("threshold")
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not 0.0 <= t <= 1.0:
        return f"threshold {t!r} is not a finite number in [0, 1]"
    for key in ("dev_split_sha256", "test_split_sha256", "frozen_by"):
        if not _text(cfg.get(key)):
            return f"{key} is missing"
    try:
        at = datetime.fromisoformat(cfg.get("frozen_at"))
    except (TypeError, ValueError):
        return f"frozen_at {cfg.get('frozen_at')!r} is not an ISO 8601 time"
    if at.utcoffset() is None:
        return "frozen_at has no time zone"
    if at > datetime.now(timezone.utc):
        return "frozen_at is in the future"
    d = cfg.get("phash_distance")
    if isinstance(d, bool) or not isinstance(d, int) or d < 0:
        return "phash_distance must be a whole number, 0 or more"
    if cfg.get("abstain") != {"rule": "none"}:
        return "abstain must be {\"rule\": \"none\"}: this rung never abstains"
    return None


def load_threshold(path: Path | None = None) -> dict | None:
    """The frozen threshold for these exact weights, or None if not truly frozen. Never raises."""
    path = path or threshold_path()
    try:
        raw = path.read_bytes()
        cfg = json.loads(raw)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("threshold file %s unreadable: %s", path, exc)
        return None
    problem = _frozen_problem(cfg)
    if problem:
        log.warning("threshold file %s is not a frozen threshold (%s); ignored, score only", path, problem)
        return None
    cfg["threshold"] = float(cfg["threshold"])
    cfg["_sha256"] = hashlib.sha256(raw).hexdigest()  # the bytes we validated, not a second read
    cfg["_path"] = str(path)
    return cfg


def _sha_text(v) -> bool:
    return isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)


def _rate(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0.0 <= v <= 1.0:
        return None
    return float(v)


def load_gate(threshold_sha256: str, path: Path | None = None) -> dict | None:
    """The gate status recorded for this exact threshold file (by its sha256), or None. Never raises.

    None means no usable status: no file, an unreadable or malformed one, or one for another threshold.
    The caller treats None, FAIL and INVALID alike: score only."""
    path = path or gate_path()
    try:
        raw = path.read_bytes()
        st = json.loads(raw)
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("gate status file %s unreadable: %s; score only", path, exc)
        return None
    if not isinstance(st, dict) or st.get("schema") != GATE_SCHEMA or st.get("result") not in GATE_RESULTS:
        log.warning("gate status file %s is not a %s status; score only", path, GATE_SCHEMA)
        return None
    if not (_sha_text(st.get("threshold_sha256")) and _text(st.get("run_id")) and _text(st.get("results"))):
        log.warning("gate status file %s lacks threshold_sha256, run_id or results; score only", path)
        return None
    if st["threshold_sha256"] != threshold_sha256:
        log.info("gate status file %s is for threshold %s, not %s; score only", path, st["threshold_sha256"],
                 threshold_sha256)
        return None
    arm = st.get("failed_arm")
    return {"result": st["result"], "run_id": st["run_id"], "results": st["results"],
            "reason": st["reason"] if _text(st.get("reason")) else None,
            "failed_arm": arm if arm in ARM_WORDS else None,
            "false_positive_upper_bound": _rate(st.get("false_positive_upper_bound")),
            "false_positive_limit": _rate(st.get("false_positive_limit")),
            "threshold_sha256": st["threshold_sha256"], "gate_file": str(path),
            "gate_file_sha256": hashlib.sha256(raw).hexdigest()}


def _pct(v: float) -> str:
    return f"{v * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _score_only(score: float, cfg: dict, gate: dict | None, above: bool = True) -> tuple[str, str]:
    """(finding, proves) for a frozen threshold without a PASS for its sha256. The finding is short: the score
    and that it raises no concern. The threshold and the test result go in `proves`, with the results path,
    since the page cannot open it. Non-breaking spaces keep "out of 1" and "N% limit" whole at 390 px.
    A score below the threshold gets one short line instead (DAT-850, a reviewer's FIX 4 on PR #33): the
    test's numbers are false-flag rates, which are about high scores, so they would be noise on a low one."""
    nb = "\u00a0"
    finding = f"Community Forensics detector score {score:.3f}{nb}out{nb}of{nb}1. This score raises no concern on its own."
    if not above:
        return finding, "Only that this detector did not raise a concern, and its threshold has not passed its test."
    frozen = f"Nothing on its own. A threshold of {cfg['threshold']:.3f} was frozen in advance"
    if gate is None:
        return finding, f"{frozen}, but no passing test of it is on record."
    if gate["result"] == "INVALID":
        return finding, f"{frozen}, but its test was invalid (see {gate['results']})."
    ub, lim, arm = gate["false_positive_upper_bound"], gate["false_positive_limit"], gate["failed_arm"]
    why = ""
    if ub is not None and lim is not None and arm:
        why = (f" In that test, the upper bound on false flags for {ARM_WORDS[arm]} was {_pct(ub)}, "
               f"above the {_pct(lim)}{nb}limit.")
    return finding, f"{frozen}, and our test of it failed.{why} See {gate['results']}."


def check(path: str) -> list[Evidence]:
    try:
        if not weights_path().exists():
            log.info("classifier weights not found at %s; run python -m secondlook.rungs.classifier --fetch",
                     weights_path())
            return []
        load_model()  # the first call loads the model; keep that out of the per-image time
        t0 = time.perf_counter()
        score = score_images([path])[0]
        seconds = time.perf_counter() - t0
    except Exception as exc:  # bad or extreme image, missing torch, bad weights: can't analyse
        log.info("classifier can't analyse %s: %s", path, exc)
        return []
    if not (math.isfinite(score) and 0.0 <= score <= 1.0):
        log.warning("classifier gave a score of %r for %s; can't analyse", score, path)
        return []

    try:
        cfg = load_threshold()
    except Exception as exc:  # load_threshold should not raise; this is the never-raise backstop
        log.warning("threshold file not usable: %s", exc)
        cfg = None
    raw = {"score": round(score, 6), "model": MODEL_NAME, "model_repo": MODEL_REPO,
           "model_revision": MODEL_REVISION, "weights_sha256": MODEL_SHA256,
           "device": _device, "seconds": round(seconds, 4)}
    if cfg is None:
        raw.update(threshold=None, above_threshold=False, threshold_file_sha256=None, gate_result=None, gate=None,
                   raises_concern=False)
        finding = (f"Community Forensics detector score {score:.3f} (0 to 1). No threshold has been "
                   f"frozen yet, so this score is shown for information only.")
        return [Evidence(rung="classifier", finding=finding, proves="Nothing yet: no frozen threshold.",
                         does_not_prove=DOES_NOT_PROVE, strength="signal", raw=raw)]

    try:
        gate = load_gate(cfg["_sha256"])
    except Exception as exc:  # load_gate should not raise; this is the never-raise backstop
        log.warning("gate status file not usable: %s", exc)
        gate = None
    above = score >= cfg["threshold"]
    passed = gate is not None and gate["result"] == "PASS"
    raw.update(threshold=cfg["threshold"], above_threshold=above, threshold_file=cfg["_path"],
               threshold_file_sha256=cfg["_sha256"], dev_split_sha256=cfg.get("dev_split_sha256"),
               frozen_at=cfg.get("frozen_at"), gate_result=gate["result"] if gate else None,
               gate=dict(gate) if gate else None, raises_concern=above and passed)
    if not passed:
        finding, proves = _score_only(score, cfg, gate, above)
        return [Evidence(rung="classifier", finding=finding, proves=proves,
                         does_not_prove=DOES_NOT_PROVE if above else DOES_NOT_PROVE_LOW,
                         strength="signal" if above else "none", raw=raw)]
    if above:
        finding = (f"Community Forensics detector score {score:.3f}, at or above our frozen threshold "
                   f"{cfg['threshold']:.2f}.")
        return [Evidence(rung="classifier", finding=finding, proves=PROVES, does_not_prove=DOES_NOT_PROVE,
                         strength="signal", raw=raw)]
    finding = (f"Community Forensics detector score {score:.3f}, below our frozen threshold "
               f"{cfg['threshold']:.2f}.")
    return [Evidence(rung="classifier", finding=finding,
                     proves="Only that this detector did not raise a concern.",
                     does_not_prove=DOES_NOT_PROVE_LOW, strength="none", raw=raw)]


def _main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m secondlook.rungs.classifier")
    p.add_argument("--fetch", action="store_true", help="download the pinned weights")
    p.add_argument("files", nargs="*")
    a = p.parse_args(argv)
    if a.fetch:
        print(fetch_weights())
    for f in a.files:
        t0 = time.perf_counter()
        s = score_images([f])[0]
        print(json.dumps({"file": f, "score": round(s, 6), "seconds": round(time.perf_counter() - t0, 4),
                          "device": _device}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
