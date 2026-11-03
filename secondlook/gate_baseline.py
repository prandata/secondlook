"""The file-facts shortcut baseline (L4 PROTOCOL.md part G), one per arm.

A reader that sees no pixels is run beside the detector so a shortcut cannot pass as detection.
Model, frozen by the protocol: one depth-3 decision tree (scikit-learn defaults,
random_state=0), fitted on the dev units and scored on the test units, in each arm. It reads no
detector output and it is an evaluation report, not a gate.

Features of what the classifier receives in that arm (no pixels):
    is_jpeg, width, height, megapixels, aspect_ratio (w / h), file_bytes, bytes_per_pixel,
    source_jpeg_quality (estimated from the ORIGINAL's quantisation tables; -1 when the original
    is not a JPEG), and, in the `original` arm only, camera_exif_present and camera_make_present.
Derived arms carry no metadata by construction (PROTOCOL A), so those two features are
constant there; the source JPEG quality stays, as part G says.

Labels (the project lead ruling ec3ad415 on DAT-849, item 3: the tester's rule adopted as written):
    baseline balanced accuracy >= 0.75  -> "shortcut-exposed"
    and the detector's balanced accuracy exceeds the baseline's by at least 10 points -> the report
    says it "exceeds this specified shortcut baseline by at least 10 points"; otherwise it says it
    does not. It never says "content-based" (the project lead, c014e175 item B4).
The harness's PASS, FAIL or INVALID stands as computed; the label sits next to it.

Balanced accuracy is reported as the two class recalls, each with an exact two-sided 95%
Clopper-Pearson interval, plus the mean with BA_LABEL: two 97.5% two-sided Clopper-Pearson
intervals (Bonferroni, 0.05 / 2), one per class, whose ends are averaged (the tester 8167c26b, adopted by
the project lead 9d763557). No simultaneous claim is made across arms or topics.
"""
from __future__ import annotations

import functools
import io
import math
from pathlib import Path

from gate.stats import lower_bound, upper_bound

EXPOSED_AT = 0.75        # PROTOCOL G, ruling ec3ad415 item 3
BEAT_BY = 0.10
BA_LABEL = "conservative, at least 95%, conditional on per-class binomial unit sampling"
BA_CLASS_CONFIDENCE = 1 - (1 - 0.95) / 2     # 97.5% two-sided per class: the 0.05 budget split over two classes
FEATURES = ("is_jpeg", "width", "height", "megapixels", "aspect_ratio", "file_bytes", "bytes_per_pixel",
            "source_jpeg_quality", "camera_exif_present", "camera_make_present")

# IJG / libjpeg standard luminance quantisation table (JPEG Annex K.1), natural order.
_STD_LUMA = (16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
             14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
             49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99)


def _scaled(q: int) -> list[int]:
    s = 5000 // q if q < 50 else 200 - 2 * q
    return [min(255, max(1, (v * s + 50) // 100)) for v in _STD_LUMA]


_TABLES = {q: _scaled(q) for q in range(1, 101)}


def estimate_jpeg_quality(quant_luma) -> int:
    """The IJG quality (1 to 100) whose scaled standard table is closest to this luminance table."""
    table = tuple(quant_luma)
    if len(table) != 64:
        raise ValueError("a luminance quantisation table has 64 entries")
    return _estimate(table)


@functools.lru_cache(maxsize=256)
def _estimate(table: tuple) -> int:
    return min(_TABLES, key=lambda q: (sum(abs(a - b) for a, b in zip(_TABLES[q], table)), q))


def _look(path: Path) -> tuple:
    """(format, (w, h), luminance quantisation table or None, camera make, camera model) of one file."""
    from PIL import Image

    with Image.open(path) as im:
        fmt, size = im.format, im.size
        quant = (getattr(im, "quantization", None) or {}).get(0) if fmt == "JPEG" else None
        exif = im.getexif() if fmt == "JPEG" else {}
        return fmt, size, quant, exif.get(0x010F), exif.get(0x0110)


def file_facts(path: str | Path, source_quality: int | None = None) -> dict:
    """The feature row for one file. A derived file passes `source_quality`, its original's estimated quality."""
    path = Path(path)
    fmt, (w, h), quant, make, model = _look(path)
    quality = source_quality if source_quality is not None else (estimate_jpeg_quality(quant) if quant else -1)
    size = path.stat().st_size
    return {"is_jpeg": int(fmt == "JPEG"), "width": w, "height": h, "megapixels": w * h / 1e6,
            "aspect_ratio": w / h, "file_bytes": size, "bytes_per_pixel": size / (w * h),
            "source_jpeg_quality": quality,
            "camera_exif_present": int(bool(make or model)), "camera_make_present": int(bool(make))}


def _two_sided(k: int, n: int, conf: float = 0.95) -> list[float | None]:
    """Exact Clopper-Pearson two-sided interval (each tail (1 - conf) / 2)."""
    if not n:
        return [None, None]
    tail = 1 - (1 - conf) / 2
    return [lower_bound(k, n, tail) if k else 0.0, upper_bound(k, n, tail) if k < n else 1.0]


def balanced_accuracy(truth: list[str], pred: list[str]) -> dict:
    """Balanced accuracy of 'ai' calls: the two class recalls, each with its exact two-sided 95% interval,
    and the mean with the conservative interval `ba_interval_conservative`.

    The mean's interval is built from two 97.5% two-sided Clopper-Pearson intervals, one per class
    (Bonferroni: 0.05 / 2), as [(L_real + L_ai) / 2, (U_real + U_ai) / 2]. Averaging the two 95% ends
    would carry no coverage argument, so the class table keeps the 95% intervals and this one is separate.
    """
    n_real = sum(t == "real" for t in truth)
    n_ai = sum(t == "ai" for t in truth)
    tn = sum(t == "real" and p == "real" for t, p in zip(truth, pred))
    tp = sum(t == "ai" and p == "ai" for t, p in zip(truth, pred))
    tnr_ci, tpr_ci = _two_sided(tn, n_real), _two_sided(tp, n_ai)
    tnr_bonf, tpr_bonf = _two_sided(tn, n_real, BA_CLASS_CONFIDENCE), _two_sided(tp, n_ai, BA_CLASS_CONFIDENCE)
    both = bool(n_real and n_ai)
    ba = (tn / n_real + tp / n_ai) / 2 if both else None
    conservative = ([(tnr_bonf[0] + tpr_bonf[0]) / 2, (tnr_bonf[1] + tpr_bonf[1]) / 2] if both else None)
    return {"balanced_accuracy": ba, "n_real": n_real, "n_ai": n_ai, "true_negative": tn, "true_positive": tp,
            "tnr_interval_95": tnr_ci, "tpr_interval_95": tpr_ci,
            "tnr_interval_97_5": tnr_bonf, "tpr_interval_97_5": tpr_bonf,
            "ba_interval_conservative": conservative, "ba_interval_label": BA_LABEL}


def fit_and_score(dev_rows: list[dict], test_rows: list[dict]) -> dict:
    """Fit the frozen tree on dev feature rows, score the test rows. Each row: {"label", **FEATURES}."""
    import sklearn
    from sklearn.tree import DecisionTreeClassifier

    def mat(rows):
        return [[float(r[f]) for f in FEATURES] for r in rows]

    ytr = [r["label"] for r in dev_rows]
    if len(set(ytr)) < 2:
        raise ValueError("the dev units hold one class only; the baseline cannot be fitted")
    tree = DecisionTreeClassifier(max_depth=3, random_state=0).fit(mat(dev_rows), ytr)
    truth = [r["label"] for r in test_rows]
    pred = [str(p) for p in tree.predict(mat(test_rows))]
    used = sorted({FEATURES[i] for i in tree.tree_.feature if i >= 0})
    return {**balanced_accuracy(truth, pred), "model": "sklearn DecisionTreeClassifier(max_depth=3, random_state=0)",
            "sklearn_version": sklearn.__version__, "features_used_by_tree": used,
            "n_dev_fit": len(dev_rows)}


def detector_class_table(rows: list[dict]) -> dict:
    """`balanced_accuracy` of the detector's calls (`ai` is a flag; `real` and `abstain` are not)."""
    return balanced_accuracy([r["label"] for r in rows],
                             ["ai" if r["verdict"] == "ai" else "real" for r in rows])


def detector_balanced_accuracy(rows: list[dict]) -> float | None:
    return detector_class_table(rows)["balanced_accuracy"]


def _ba_text(ba: float, interval) -> str:
    """One balanced accuracy, always with its interval and the interval's label, never bare."""
    return f"{ba:.1%} (interval {interval[0]:.1%} to {interval[1]:.1%}; {BA_LABEL})"


def label_arm(baseline_ba: float | None, detector_ba: float | None, baseline_interval=None,
              detector_interval=None) -> dict:
    """The shortcut label for one arm and whether the detector exceeds the baseline by 10 points.

    The 10-point comparison is on the two point estimates, as the ruling wrote it. A rate is printed only with its
    conservative interval; without the intervals the text holds no rate at all (the class tables above it do).
    """
    if baseline_ba is None or detector_ba is None or not (math.isfinite(baseline_ba) and math.isfinite(detector_ba)):
        return {"label": "not computed", "exceeds_baseline_by_10": False,
                "text": "The shortcut baseline could not be computed for this arm."}
    margin = detector_ba - baseline_ba
    ok = margin >= BEAT_BY - 1e-12
    with_rates = baseline_interval is not None and detector_interval is not None
    if baseline_ba >= EXPOSED_AT:
        verdict = ("exceeds this specified shortcut baseline by at least 10 points."
                   if ok else "does not exceed this specified shortcut baseline by at least 10 points.")
        if with_rates:
            text = (f"shortcut-exposed: the file-facts baseline reaches a balanced accuracy of "
                    f"{_ba_text(baseline_ba, baseline_interval)}, 0.75 or more; the detector's is "
                    f"{_ba_text(detector_ba, detector_interval)}, {abs(margin) * 100:.1f} points "
                    f"{'above' if margin >= 0 else 'below'} it (point estimates), so the detector {verdict}")
        else:
            text = ("shortcut-exposed: the file-facts baseline reaches a balanced accuracy of 0.75 or more "
                    f"(rates and intervals are in the class tables above), so the detector {verdict}")
        return {"label": "shortcut-exposed", "exceeds_baseline_by_10": ok, "margin_points": margin * 100,
                "text": text}
    text = ("not shortcut-exposed: the file-facts baseline balanced accuracy is under 0.75"
            + (f" ({_ba_text(baseline_ba, baseline_interval)})." if baseline_interval is not None
               else " (rate and interval are in the class table above)."))
    return {"label": "not shortcut-exposed", "exceeds_baseline_by_10": ok, "margin_points": margin * 100,
            "text": text}
