"""J13 (DAT-1375): the static replay site builder, on a tiny synthetic queue. No browser, no model, no network."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

from secondlook import queue

ROOT = Path(__file__).resolve().parents[1]
PIL = pytest.importorskip("PIL.Image")


def _load():
    spec = importlib.util.spec_from_file_location("build_replay_site", ROOT / "scripts" / "build_replay_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


brs = _load()

# (label, generator, sq512 score): one image per band, plus an AI image in likely_fine (a routing error)
CASES = [("real", "", 0.40), ("ai", "Stable Diffusion 1.5", 0.99), ("real", "", 0.0001), ("ai", "Stable Diffusion 3", 0.0001)]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


@pytest.fixture()
def world(tmp_path):
    crops = tmp_path / "sq512"
    crops.mkdir()
    split, index, scores, crop_info = [], [], [], {}
    for i, (label, gen, score) in enumerate(CASES):
        src_sha = _sha(f"source-{i}".encode())
        name = f"{src_sha[:20]}.jpg"
        PIL.new("RGB", (32, 32), (40 * i, 90, 120)).save(crops / name, "JPEG")
        crop_sha = _sha((crops / name).read_bytes())
        kind = "Fake/SD" if label == "ai" else "Real/FORLAB"
        split.append({"sha256": src_sha, "label": label, "generator": gen, "source_ref": f"Facebook/{kind}/{i:05d}.jpg",
                      "group": "", "split": "heldout", "k3": "0", "edit_rank": "", "caption_rank": ""})
        index.append({"normalised_path": f"data/normalised/sq512/{name}", "sha256_normalised": crop_sha,
                      "source_sha256": src_sha, "label": label, "generator": gen, "source_dataset": brs.TRUEFAKE_SOURCE})
        scores.append({"sha256": src_sha, "label": label, "generator": gen, "licence": "CC BY 4.0",
                       "sq512_answer": "cant_tell", "sq512_classifier_score": score,
                       "original_answer": "cant_tell", "original_classifier_score": score})
        crop_info[crop_sha] = (name, score)
    split.append({**split[0], "sha256": "d" * 64, "split": "dev"})  # dev rows are never used
    _write_csv(tmp_path / "split.csv", split)
    _write_csv(crops / "index.csv", index)
    _write_csv(tmp_path / "scores.csv", scores)

    # the queue run, as `secondlook queue --form sq512` writes it (receipts are stand-ins)
    qdir = tmp_path / "work" / "queue"
    (qdir / "receipts").mkdir(parents=True)
    bands = queue.load_bands(form="sq512")
    rows = []
    for k, (crop_sha, (name, score)) in enumerate(sorted(crop_info.items())):
        band, reason = queue.band_for("cant_tell", score, bands)
        receipt = {"schema": "second-look-receipt/1", "answer": "cant_tell", "summary": "Second Look can't tell.",
                   "limits": "Second Look never says an image is real, genuine or authentic.",
                   "tool": {"version": "abc123"}, "created_at": "2026-11-02T00:00:00Z",
                   "evidence": [{"rung": "classifier", "strength": "signal", "finding": f"Score {score}.",
                                 "proves": "Nothing on its own.", "does_not_prove": "Not proof either way.",
                                 "raw": {"score": score, "model": "m", "weights_sha256": "0" * 64}}]}
        rel = f"receipts/{k:04d}-{name}.json"
        data = (json.dumps(receipt, indent=2) + "\n").encode()
        (qdir / rel).write_bytes(data)
        (qdir / (rel + ".sha256")).write_text(f"{_sha(data)}  {Path(rel).name}\n")
        rows.append({"band": band, "file": name, "detector_score": score, "answer": "cant_tell", "reason": reason,
                     "sha256": crop_sha, "receipt_json": rel, "receipt_sha256": _sha(data),
                     "receipt_pdf": None, "receipt_error": None})
    ranked = queue.rank(rows)
    counts = {"images": 4, "checked": 4, "skipped": 0, "failed": 0}
    for b in queue.BAND_ORDER:
        counts[b] = sum(r["band"] == b for r in ranked)
    doc = {"schema": queue.SCHEMA, "version": "abc123", "counts": counts, "queue": ranked,
           "bands": {"form": "sq512", "sha256": bands["sha256"]}}
    (qdir / "queue.json").write_text(json.dumps(doc))
    (qdir / "queue.csv").write_text("rank\n")
    (qdir / "run.json").write_text(json.dumps({"jobs": 1, "elapsed_seconds": 1.0}))
    return tmp_path


def _build(world: Path, **kw) -> Path:
    held = brs.held_out_rows(brs.read_csv(world / "split.csv"), expected=4)
    crops = brs.stage_crops(held, world / "sq512", world / "work" / "images")
    scores = brs.read_csv(world / "scores.csv")
    forms = [brs.tally_form(scores, held, f) for f in ("sq512", "original")]
    out = world / "site"
    brs.build_site(world / "work" / "queue", world / "work" / "images", crops, forms, out, **kw)
    return out


def test_site_pages_links_and_rules(world):
    out = _build(world)
    pages = sorted(out.rglob("*.html"))
    assert len(pages) == 4 + 3  # one page per image, the queue, results and how-to-read
    for p in pages:
        text = p.read_text()
        assert "Not affiliated with Meta" in text, p
        assert "<script" not in text.lower(), p
        # nothing is fetched from elsewhere: every src and stylesheet is relative
        for url in re.findall(r'(?:src|<link[^>]*href)="([^"]+)"', text):
            assert not re.match(r"^(https?:)?//", url), (p, url)
        if "<img" in text:
            assert "CC BY 4.0" in text and "TrueFake" in text, p
        # every relative link resolves to a file in the site (works from file:// and GitHub Pages)
        for url in re.findall(r'href="([^"#]+)', text):
            if not re.match(r"^https?:", url):
                assert (p.parent / url).resolve().is_file(), (p, url)
        # "real" appears only inside a denial, never as a claim about an image
        stripped = re.sub(r"(?i)not\s+proof\s+that\s+(the\s+image|it)\s+is\s+real|never\s+says\s+an\s+image\s+is\s+real", "", text)
        assert not re.search(r"(?i)\breal\b", stripped), p
        assert "Pending" in text or p.name in ("about.html",), p
    assert "url(http" not in (out / "assets" / "site.css").read_text()
    index = (out / "index.html").read_text()
    assert "Routing error: an AI image in Likely fine" in index
    assert index.count("Routing error") == 1
    # the image and receipt files are byte-identical copies, listed in SHA256SUMS.txt
    for line in (out / "SHA256SUMS.txt").read_text().splitlines():
        digest, rel = line.split("  ")
        assert _sha((out / rel).read_bytes()) == digest


def test_arena_slots_hold_no_numbers(world):
    out = _build(world)
    html = (out / "results.html").read_text()
    arena = html[html.index('<section class="arena"'):]
    arena = arena[:arena.index("</section>")]
    assert arena.count("Pending") == 3
    assert not re.search(r"\d+(\.\d+)?\s*%|\d+\s*/\s*\d+", arena)


def test_non_truefake_rows_are_refused(world):
    rows = brs.read_csv(world / "split.csv")
    rows[0]["source_ref"] = "openfake/core/test/0001.png"
    with pytest.raises(SystemExit, match="not TrueFake"):
        brs.held_out_rows(rows, expected=4)
    ix = brs.read_csv(world / "sq512" / "index.csv")
    ix[0]["source_dataset"] = "Community Forensics Eval"
    _write_csv(world / "sq512" / "index.csv", ix)
    held = brs.held_out_rows(brs.read_csv(world / "split.csv"), expected=4)
    with pytest.raises(SystemExit, match="not from TrueFake"):
        brs.stage_crops(held, world / "sq512", world / "work" / "images")


def test_altered_crop_is_refused(world):
    ix = brs.read_csv(world / "sq512" / "index.csv")
    (world / "sq512" / Path(ix[0]["normalised_path"]).name).write_bytes(b"not the crop")
    held = brs.held_out_rows(brs.read_csv(world / "split.csv"), expected=4)
    with pytest.raises(SystemExit, match="does not match its sha256"):
        brs.stage_crops(held, world / "sq512", world / "work" / "images")


def test_live_band_must_match_scores(world):
    qj = world / "work" / "queue" / "queue.json"
    doc = json.loads(qj.read_text())
    doc["queue"][0]["band"] = queue.FINE if doc["queue"][0]["band"] != queue.FINE else queue.AUTO
    qj.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="different band"):
        _build(world)


def test_home_folder_paths_are_refused(world, monkeypatch):
    rec = next((world / "work" / "queue" / "receipts").glob("*.json"))
    data = json.loads(rec.read_text())
    data["evidence"][0]["raw"]["gate_file"] = str(Path.home() / "x" / "classifier_gate.json")
    raw = (json.dumps(data, indent=2) + "\n").encode()
    rec.write_bytes(raw)
    qj = world / "work" / "queue" / "queue.json"
    doc = json.loads(qj.read_text())
    for r in doc["queue"]:
        if r["receipt_json"].endswith(rec.name):
            r["receipt_sha256"] = _sha(raw)
    qj.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="home folder"):
        _build(world)
    assert not (world / "site").exists()
    assert _build(world, allow_home_paths=True).is_dir()


def test_no_camera_photo_is_copied_into_the_site(world):
    """Lead ruling, 2 Nov (DAT-1389): camera photos can show identifiable people, so the site publishes no
    thumbnail and no full image of any of them. Their rows keep a placeholder, the receipt and the sha256."""
    out = _build(world)
    index = {r["sha256_normalised"]: r for r in brs.read_csv(world / "sq512" / "index.csv")}
    real = {sha: Path(r["normalised_path"]).stem for sha, r in index.items() if r["label"] == "real"}
    ai = {sha: Path(r["normalised_path"]).stem for sha, r in index.items() if r["label"] == "ai"}
    assert real and ai
    real_bytes = {(world / "sq512" / f"{stem}.jpg").read_bytes() for stem in real.values()}
    image_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff"}
    published = [p for p in out.rglob("*") if p.is_file() and p.suffix.lower() in image_ext]
    for p in published:
        assert p.stem not in real.values(), p  # no copy and no thumbnail under the photo's name
        assert p.read_bytes() not in real_bytes, p  # and no byte copy under any other name
    # every AI image is published, as the full crop and a thumbnail
    assert sorted(p.stem for p in (out / "f").iterdir()) == sorted(ai.values())
    assert sorted(p.stem for p in (out / "t").iterdir()) == sorted(ai.values())
    for page in out.rglob("*.html"):
        text = page.read_text()
        for stem in real.values():
            assert f"t/{stem}." not in text and f"f/{stem}." not in text, (page, stem)
    for sha, stem in real.items():
        text = (out / "i" / f"{stem}.html").read_text()
        assert "<img" not in text and "Camera photo not shown" in text
        assert sha in text  # the image sha256 stays on its page, with the receipt link
        assert 'href="../r/' in text
    index_html = (out / "index.html").read_text()
    assert index_html.count("Photo not shown") == len(real)
    # SHA256SUMS lists only files that are in the site, so `shasum -c` passes
    for line in (out / "SHA256SUMS.txt").read_text().splitlines():
        assert (out / line.split("  ")[1]).is_file()


def test_desk_framing_and_labels(world):
    out = _build(world)
    index = (out / "index.html").read_text()
    lede = index[index.index('<p class="lede">'):]
    lede = lede[:lede.index("</p>")]
    assert "folder" in lede and "which ones should a person look at first" in lede
    assert not re.search(r"(?i)upload an image", index)
    assert '<p class="arena-strip">' in index and '<section class="arena"' not in index
    assert 'class="hash"' not in index  # the bands-file hash lives on the results page
    results = (out / "results.html").read_text()
    assert "Bands file sha256" in results and 'id="arena-h"' in results
    page = next((out / "i").glob("*.html")).read_text()
    assert "Evidence weight: signal" in page and '<span class="strength">signal</span>' not in page
    assert "dev-fitted thresholds (t_lo " in page and "29 Oct classifier gate" in page
