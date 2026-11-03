"""Recorded Muse Glimmer runs (DAT-850, mock judging item 3a): every judge sees the Meta model's deep check.

The server offers a recording only for the same bytes (sha256) checked with no caption; its label says what it is,
the model, where and when it ran, the seconds, and that it is not live. The page asks only when no Glimmer server
answers, shows the label before the recording, never mixes it with a live run, and never changes the answer.
When Glimmer is up the button runs live, as before. The page tests run in headless-run (chrome-headless-shell,
muted) against a server on 127.0.0.1 whose Glimmer URL is a closed port; no real model server is used.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from secondlook import receipt as receipts  # noqa: E402
from secondlook.investigator import DeskNote  # noqa: E402
from secondlook.rungs import exif as exif_rung  # noqa: E402
from secondlook.web import recorded  # noqa: E402
from secondlook.web import samples as sample_catalogue  # noqa: E402
from secondlook.web.app import REQUEST_LIMITS, STATIC, create_app  # noqa: E402
from tests.test_w11_receipt_page import _free_port, _headless_run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "samples" / "recorded"
RUNGS = [("exif", exif_rung.check)]
INV = (STATIC / "investigate.js").read_text()
HTML = (STATIC / "index.html").read_text()
# Meta's pinned text model (docs/LICENSES.md, scripts/fetch_glimmer.sh).
PINNED_MODEL_SHA = "4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e"
# The words a label or the page's recorded-run copy must never use: no live, truth, accuracy or detection claim.
BANNED = ("live run", "real", "authentic", "proves", "accurate", "accuracy", "detects", "detection")
# Markup a recording's text could carry. The page must show it as text and never parse it.
HOSTILE = '<img src="/pwned.png" onerror="window.__pwned=1"><script>window.__pwned=2</script>'
# The one captioned recording (DAT-850, mock judging v2 item 1): the hero case on a mock post of ours with the M1
# test's own-wording caption (scripts/glimmer_smoke.py; licence check A190), checked against the 29 Oct 2026 feed.
PENTAGON = COMMITTED / "pentagon.json"
PENTAGON_CAPTION = ("BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. Pentagon is under "
                    "attack. Stay safe everyone")
PENTAGON_PICK = "Photo shows the U.S. Pentagon is under attack"
FEED_SHA = "3c1470845f2f4adc4254bb3cc2b73349aec6b0cc6de7bc5ab15ecc71712b6024"
FEED_URL = "https://storage.googleapis.com/datacommons-feeds/claimreview/20261029-141029/data.json"
LATEST_URL = "https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json"
# The deep-check box's line when health gave no answer or said Glimmer is unavailable for a reason other than
# not_running (DAT-850 A228): it does not say "not running" then.
UNSURE = ("The deep check is off on this computer just now: Second Look could not confirm its model. The answer above "
          "comes from the file checks alone.")


def _step8() -> tuple[str, str]:
    """The feed URL judges' step 8 downloads and the sha256 it checks that file against."""
    text = (ROOT / "docs" / "README-for-judges.md").read_text()
    line = next(ln for ln in text.splitlines() if "curl -L -o claimreview.json" in ln)
    url = re.search(r"curl -L -o claimreview\.json (\S+)", line).group(1)
    sha = re.search(r'echo "([0-9a-f]{64})  claimreview\.json" \| shasum -a 256 -c', line).group(1)
    assert f"--sha256 {sha}" in line and f"--source {url}" in line, "the index records the same file and URL"
    return url, sha


def _jpeg(path: Path, colour=(90, 120, 160)) -> Path:
    from PIL import Image
    Image.new("RGB", (64, 48), colour).save(path, "JPEG")
    return path


def _record_for(path: Path, **change) -> dict:
    """A committed recording, re-keyed to this file's bytes, so the tests do not need the fetched samples."""
    rec = copy.deepcopy(json.loads((COMMITTED / "02.json").read_text()))
    rec["sample"].update(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    rec.update(change)
    return rec


def _hostile(rec: dict) -> dict:
    """Markup in every recorded string the page shows: the label (via machine), the note and the footnote."""
    note = rec["note"]
    rec.update(machine=HOSTILE + " M5", llama_cpp_build=HOSTILE)
    rec["model"] = {**rec["model"], "file": HOSTILE}
    note.update(explanation_label=HOSTILE, explanation_lines=[{"template": "summary", "text": HOSTILE}],
                image_text=[HOSTILE], facts=[{"place": HOSTILE, "device": HOSTILE, "date": None}], label=HOSTILE)
    note["steps"] = [{**note["steps"][0], "tool": HOSTILE, "result": HOSTILE}]
    note["evidence"] = note["evidence"] + [{"rung": "hostile", "finding": HOSTILE, "proves": HOSTILE,
                                            "does_not_prove": HOSTILE, "strength": "weak", "raw": {}}]
    return rec


def _write(folder: Path, name: str, rec: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(rec))


def _post(client, path: Path, caption=None, name=None):
    return client.post("/api/recorded", files={"file": (name or path.name, path.read_bytes(), "image/jpeg")},
                       data={"caption": caption} if caption is not None else None)


@pytest.fixture
def setup(tmp_path):
    img = _jpeg(tmp_path / "recorded.jpg")
    rec_dir = tmp_path / "rec"
    _write(rec_dir, "r.json", _record_for(img))
    return img, rec_dir, TestClient(create_app(stub=False, rungs=RUNGS, recorded_dir=str(rec_dir)))


# ---- the committed recordings -------------------------------------------------------------------------

def test_the_committed_recordings_are_whole_and_from_republishable_samples():
    recs = recorded.load(COMMITTED)
    by_id = {r["sample"]["id"]: r for r in recs.values() if r["caption"] is None}
    assert 2 <= len(by_id) <= 3, sorted(by_id)
    assert [r["sample"]["id"] for r in recs.values() if r["caption"] is not None] == ["pentagon"], \
        "one captioned recording, the hero case"
    catalogue = {s.id: s for s in sample_catalogue.CATALOGUE}
    for r in recs.values():
        assert r["note"]["version"] == r["git_commit"] and r["llama_cpp_build"], r["sample"]["id"]
    for sid, r in by_id.items():
        s = catalogue[sid]
        assert sid not in ("01", "05", "06") and "SA" not in s.licence, f"{sid} is share-alike ({s.licence})"
        assert r["sample"]["sha256"] == s.sha256 and r["sample"]["file"] == s.file, "keyed to the pinned bytes"
        assert r["model"]["sha256"] == PINNED_MODEL_SHA, "Meta's pinned GGUF, not the Ollama copy"
        assert r["caption"] is None and r["note"]["glimmer"] == "ran" and r["note"]["demo"] is False
        assert any(st["by"] == "model" for st in r["note"]["steps"]), "the model chose at least one check"
        # A recorded run is never a server run (the project lead, DAT-850): no run id in the file or in what is served.
        assert r["note"].get("receipt_run") is None and recorded.public(r)["note"]["receipt_run"] is None
        assert (COMMITTED / f"{sid}.json").is_file()
    assert any(r["note"]["image_text"] for r in by_id.values()), "one recording shows the printed-text reading"


def test_no_committed_recording_names_a_home_folder():
    """Mock judging v2 R9: the classifier card's threshold and gate files are written repo-relative."""
    for path in sorted(COMMITTED.glob("*.json")):
        text = path.read_text()
        assert "/Users/" not in text and str(Path.home()) not in text and str(ROOT) not in text, path.name
        note = json.loads(text)["note"]
        files = [e["raw"][k] for e in note["evidence"] for k in ("threshold_file",) if k in e.get("raw", {})]
        files += [e["raw"]["gate"]["gate_file"] for e in note["evidence"]
                  if isinstance(e.get("raw", {}).get("gate"), dict) and "gate_file" in e["raw"]["gate"]]
        assert files, f"{path.name}: the classifier card names its files"
        for f in files:
            assert not Path(f).is_absolute() and (ROOT / f).is_file(), f


def test_the_pentagon_recording_is_the_hero_case_with_its_caption_pick_and_fact_check():
    rec = json.loads(PENTAGON.read_text())
    recorded.validate(rec)
    image = COMMITTED / rec["sample"]["file"]
    assert image.is_file() and hashlib.sha256(image.read_bytes()).hexdigest() == rec["sample"]["sha256"], \
        "the mock post is committed beside its recording, with the recorded bytes"
    assert rec["caption"] == PENTAGON_CAPTION
    import ast
    smoke = ast.parse((ROOT / "scripts" / "glimmer_smoke.py").read_text())
    captions = next(n.value for n in smoke.body if isinstance(n, ast.Assign) and n.targets[0].id == "CAPTIONS")
    assert ast.literal_eval(captions)[0] == PENTAGON_CAPTION, "the M1 test's own wording, not the post's"
    assert "rating_explanation" not in PENTAGON.read_text(), "no PolitiFact article text is stored"
    assert rec["caption_sha256"] == hashlib.sha256(PENTAGON_CAPTION.encode()).hexdigest()
    assert rec["claims_index"]["feed_sha256"] == FEED_SHA, "the 29 Oct 2026 feed file, not a later `latest`"
    # It names the dated copy judges' step 8 downloads, by the same sha256, never the moving `latest` (a reviewer,
    # PR #48 note 1); so does the fact-check card's own feed record.
    assert _step8() == (FEED_URL, FEED_SHA)
    assert rec["claims_index"]["source"] == FEED_URL and "/latest/" not in PENTAGON.read_text()
    feeds = [e["raw"]["feed"] for e in rec["note"]["evidence"] if e["rung"] == "claim" and "feed" in e["raw"]]
    assert feeds and all(f["source"] == FEED_URL and f["feed_sha256"] == FEED_SHA for f in feeds)
    note = rec["note"]
    assert note["answer"] == "cant_tell" and note["glimmer"] == "ran" and note["receipt_run"] is None
    looked = [st for st in note["steps"] if st["tool"] == "lookup_claim"]
    assert looked and all(st["by"] == "model" and st["search_source"] == "model" for st in looked)
    assert all(st["arguments"]["text"] in PENTAGON_CAPTION for st in looked), "every pick is words from the caption"
    found_by = [st for st in looked if st["template"] == "lookup_claim.found"]
    assert [st["arguments"]["text"] for st in found_by] == [PENTAGON_PICK], "the clause pick found the review"
    found = [e for e in note["evidence"] if e["rung"] == "claim" and e["raw"].get("review_url")]
    assert found and found[0]["raw"]["publisher"] == "politifact.com" and found[0]["raw"]["rating"] == "Pants on Fire"
    assert recorded.public(rec)["caption"] == PENTAGON_CAPTION and recorded.public(rec)["note"]["receipt_run"] is None


# ---- the label (CRITERIA 3.3: what it is, when and where it ran, not live) -------------------------------

def test_label_says_recorded_not_live_model_machine_date_and_seconds(tmp_path):
    rec = _record_for(_jpeg(tmp_path / "a.jpg"), machine="Apple M5 Max (128 GB)",
                      date_utc="2026-10-29T14:58:40Z", seconds=74.8)
    assert recorded.label(rec) == ("Recorded run, not live. Muse Glimmer 30B on Apple M5 Max (128 GB), "
                                   "29 Oct 2026 UTC, 75 s.")


def test_every_committed_label_passes_the_claims_rule():
    for r in recorded.load(COMMITTED).values():
        text = recorded.label(r)
        assert text.startswith("Recorded run, not live. Muse Glimmer 30B on ")
        assert r["machine"] in text and f"{round(r['seconds'])} s." in text
        assert "—" not in text and "–" not in text, "no dashes in public copy"
        for word in BANNED:
            assert word not in text.lower().replace("not live", ""), word


def test_the_page_copy_for_a_recording_makes_no_live_accuracy_or_detection_claim():
    offer = re.sub(r"\s+", " ", re.search(r'id="inv-recorded-offer" hidden>(.*?)</p>', HTML, re.S).group(1))
    assert offer == ("No Muse Glimmer server is running here now, so the deep check cannot run live. We recorded one "
                     "on this exact file (the same sha256 fingerprint), and you can read it here.")
    copy_ = [offer, "Show the recorded run (not live)"] + re.findall(
        r'"((?:Desk note · recorded|Recorded run, not live ·|Muse Glimmer is not running|The deep check is off on'
        r' this computer: its|A file you drop'
        r'|Showing a recorded run|To run deep checks live)[^"]*)"', INV)
    assert len(copy_) == 9, copy_   # 9: the line for no Glimmer server and no recording (persona test, 30 Oct)
    for text in copy_:
        assert "—" not in text and "–" not in text, text
        for word in BANNED:
            assert word not in text.lower().replace("not live", "").replace("run live", ""), (word, text)


# ---- the pins: the hashes in a recording are Meta's pinned files ----------------------------------------

def _fetch_pins() -> dict:
    script = (ROOT / "scripts" / "fetch_glimmer.sh").read_text()
    return dict(re.findall(r"^(TEXT_FILE|TEXT_SHA|MMPROJ_FILE|MMPROJ_SHA)=(\S+)$", script, re.M))


def test_every_recording_names_the_pinned_model_and_projector_files():
    """The model hash is of the file the running server reported; the projector's is of the file
    scripts/glimmer_server.sh names (llama-server does not report it). Both must be fetch_glimmer.sh's pins."""
    pins = _fetch_pins()
    assert len(pins) == 4 and pins["TEXT_SHA"] == PINNED_MODEL_SHA
    recs = recorded.load(COMMITTED)
    assert recs
    for r in recs.values():
        m = r["model"]
        assert (m["file"], m["sha256"]) == (pins["TEXT_FILE"], pins["TEXT_SHA"]), r["sample"]["id"]
        assert (m["projector_file"], m["projector_sha256"]) == (pins["MMPROJ_FILE"], pins["MMPROJ_SHA"]), \
            r["sample"]["id"]


# ---- the recording script writes what the page serves ---------------------------------------------------

def _working_secondlook_tar() -> bytes:
    """secondlook/ as this checkout has it, as a tar: what the tests' fake `git archive` of their fake commit gives,
    so the script's snapshot holds the code under test, with no git needed."""
    import io
    import tarfile
    buf = io.BytesIO()
    skip = lambda t: None if "__pycache__" in t.name or "/trust/interim" in t.name else t  # noqa: E731
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(ROOT / "secondlook", arcname="secondlook", filter=skip)
    return buf.getvalue()


def _load_script(head="c" * 40, status="", argv=("02",), git=None, script=None, after=None, own_modules=True):
    """The script, loaded as `python scripts/record_investigations.py <argv>` loads it (argv: a run of sample 02 by
    default). Its read of secondlook/'s state and HEAD before the repo imports gets status ("" is clean) and head
    (default the "c" * 40 the tests' git fakes give); None for either is git failing. `git archive` of that head gives
    this checkout's secondlook/ (the snapshot the script imports from, A228) and the ignored-file listing gives none;
    every other command runs as it would. Each git command the load runs is added to `git` if given.

    script: another copy of the script (its own ROOT is then its repo, and git runs for real there); after(cmd) runs
    after each command. own_modules: secondlook is out of sys.modules while the script loads, as in its own process,
    so it imports its snapshot; the test process's modules are put back after."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("record_investigations",
                                                  script or ROOT / "scripts" / "record_investigations.py")
    mod = importlib.util.module_from_spec(spec)
    real, said = subprocess.run, {("git", "rev-parse", "HEAD"): head,
                                  ("git", "status", "--porcelain", "--", "secondlook"): status}
    archive = ("git", "archive", "--format=tar", head, "--", "secondlook")
    ignored = ("git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", "secondlook")

    def fake(cmd, *a, **k):
        if cmd[0] == "git" and git is not None:
            git.append(tuple(cmd))
        if k.get("cwd") == ROOT and tuple(cmd) == archive:
            return subprocess.CompletedProcess(cmd, 0, _working_secondlook_tar(), b"")
        if k.get("cwd") == ROOT and tuple(cmd) == ignored:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if tuple(cmd) in said and k.get("cwd") == ROOT:
            if said[tuple(cmd)] is None:
                raise subprocess.CalledProcessError(128, cmd, "", "fatal: not a git repository")
            return subprocess.CompletedProcess(cmd, 0, said[tuple(cmd)] + "\n", "")
        out = real(cmd, *a, **k)
        if after:
            after(tuple(cmd))
        return out

    ours = lambda: [m for m in sys.modules if m.split(".")[0] == "secondlook"]  # noqa: E731
    kept = {m: sys.modules.pop(m) for m in ours()} if own_modules else {}
    path_was = list(sys.path)
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGHUP)}   # the snapshot sets its own
    subprocess.run, argv_was, sys.argv = fake, sys.argv, ["record_investigations.py", *argv]
    try:
        spec.loader.exec_module(mod)
    finally:
        subprocess.run, sys.argv = real, argv_was
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if own_modules:
            for m in ours():
                del sys.modules[m]
            sys.modules.update(kept)
        sys.path[:] = path_was
    return mod


def _fake_note(path: str, caption=None) -> DeskNote:
    from secondlook.investigator import Step
    return DeskNote(file=Path(path).name, answer="cant_tell", summary="s", evidence=[], explanation="e",
                    steps=[Step(n=1, tool="read_text_in_image", arguments={}, result="Read 1 line.", ok=True,
                                by="model", seconds=1.5, template="step.read_text")],
                    image_text=["HELLO"], model_seconds=1.5, seconds=2.0)


def test_the_recording_script_writes_a_valid_recording_of_the_live_body(tmp_path, monkeypatch):
    mod = _load_script()
    img = _jpeg(tmp_path / "s02.jpg")
    model, mmproj = tmp_path / "model.gguf", tmp_path / "mmproj.gguf"
    model.write_bytes(b"model bytes")
    mmproj.write_bytes(b"projector bytes")
    commit = "c" * 40
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "Credit", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())

    def run(*cmd):
        if cmd[0] == "git":
            return {"status": "", "rev-parse": commit}[cmd[1]]   # a clean, committed tree
        return {"machdep.cpu.brand_string": "Apple Test Chip", "hw.memsize": str(64 << 30)}[cmd[-1]]

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(mod, "server_files", lambda url: (model, mmproj, "b1-test"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    def investigate(path, caption, client, rungs):
        time.sleep(0.1)   # a recording of 0.0 s is refused, as no real deep check is that quick
        return _fake_note(path)

    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    assert mod.main() == 0

    rec = json.loads((tmp_path / "out" / "02.json").read_text())
    recorded.validate(rec)
    assert recorded.load(tmp_path / "out") == {sample.sha256: rec}, "keyed by the sample's bytes"
    assert rec["sample"] == {"id": "02", "file": img.name, "sha256": sample.sha256, "licence": "CC BY 4.0",
                             "credit": "Credit"}
    assert rec["model"] == {"name": recorded.MODEL_NAME, "served_as": "muse-glimmer", "file": "model.gguf",
                            "sha256": hashlib.sha256(b"model bytes").hexdigest(), "projector_file": "mmproj.gguf",
                            "projector_sha256": hashlib.sha256(b"projector bytes").hexdigest()}
    assert rec["caption"] is None and rec["git_commit"] == commit and rec["llama_cpp_build"] == "b1-test"
    assert rec["machine"] == "Apple Test Chip (64 GB)" and rec["seconds"] >= 0.1
    # The note is the body POST /api/investigate returns for the same DeskNote, but for the version and rungs.
    client = TestClient(create_app(stub=False, rungs=RUNGS, investigator=_fake_note))
    live = client.post("/api/investigate", files={"file": (img.name, img.read_bytes(), "image/jpeg")}).json()
    assert set(rec["note"]) == set(live)
    # A recorded run is never a server run: its run id is null, so no receipt takes picks from it (DAT-850).
    assert rec["note"]["receipt_run"] is None
    assert {k: v for k, v in rec["note"].items() if k not in ("version", "rungs")} == \
        {k: v for k, v in live.items() if k not in ("version", "rungs")}
    assert rec["note"]["version"] == commit and rec["note"]["rungs"] == ["exif"] == live["rungs"]


def test_the_recording_script_refuses_share_alike_samples(tmp_path, monkeypatch):
    mod = _load_script()
    img = _jpeg(tmp_path / "s01.jpg")
    sample = sample_catalogue.Sample("01", img.name, "T", "K", "C", "CC BY-SA 4.0", "src", "0" * 64)
    monkeypatch.setattr(mod, "run", lambda *cmd: "c" * 40 if cmd[:2] == ("git", "rev-parse") else "")
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"01": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "01", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit, match="CC BY-SA"):
        mod.main()
    assert not (tmp_path / "out").exists()


# ---- the sha256 match ---------------------------------------------------------------------------------

def test_the_same_bytes_get_the_recording_under_its_label(setup):
    img, _, client = setup
    res = _post(client, img, name="any-other-name.jpg")
    assert res.status_code == 200
    body = res.json()
    assert list(body)[:3] == ["recorded", "live", "label"], "the label comes before the content"
    assert body["recorded"] is True and body["live"] is False
    assert body["label"].startswith("Recorded run, not live. Muse Glimmer 30B on ")
    assert body["sha256"] == hashlib.sha256(img.read_bytes()).hexdigest()
    assert body["note"]["steps"] and body["model"]["sha256"] == PINNED_MODEL_SHA


def test_no_recording_for_any_other_file(setup, tmp_path):
    img, _, client = setup
    other = _jpeg(tmp_path / "other.jpg", (200, 30, 30))
    assert other.read_bytes() != img.read_bytes()
    assert _post(client, other).status_code == 204
    # Same name as the recorded file, one byte more: still another file.
    twin = tmp_path / "twin" / img.name
    twin.parent.mkdir()
    twin.write_bytes(img.read_bytes() + b"\0")
    res = _post(client, twin)
    assert res.status_code == 204 and res.content == b""


def test_no_recording_when_the_check_had_a_caption(setup):
    img, _, client = setup
    assert _post(client, img, caption="Taken in Utah.").status_code == 204
    assert _post(client, img, caption="   ").status_code == 200, "a blank caption is no caption, as on the page"


@pytest.fixture
def captioned(tmp_path):
    """The Pentagon recording, re-keyed to a test file's bytes, with its caption."""
    img = _jpeg(tmp_path / "post.jpg")
    rec = json.loads(PENTAGON.read_text())
    rec["sample"].update(file=img.name, sha256=hashlib.sha256(img.read_bytes()).hexdigest())
    rec_dir = tmp_path / "rec"
    _write(rec_dir, "pentagon.json", rec)
    return img, rec, TestClient(create_app(stub=False, rungs=RUNGS, recorded_dir=str(rec_dir)))


def test_a_captioned_recording_is_offered_for_its_caption_only(captioned):
    img, rec, client = captioned
    res = _post(client, img, caption=PENTAGON_CAPTION)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["caption"] == PENTAGON_CAPTION and body["caption_sha256"] == rec["caption_sha256"]
    assert body["claims_index"]["feed_sha256"] == FEED_SHA and body["note"]["receipt_run"] is None
    # Trimmed as the page trims it: the same caption.
    assert _post(client, img, caption="  " + PENTAGON_CAPTION + "\n").status_code == 200
    # Any other caption, or none, gets nothing: a check with other words is not the check that was recorded.
    for other in (None, "", "   ", PENTAGON_CAPTION[:-1], PENTAGON_CAPTION.lower(), PENTAGON_PICK,
                  PENTAGON_CAPTION + "!", PENTAGON_CAPTION.replace("BREAKING: ", "")):
        res = _post(client, img, caption=other)
        assert res.status_code == 204 and res.content == b"", repr(other)
    # The right caption on any other file gets nothing either.
    assert _post(client, _jpeg(img.parent / "other.jpg", (1, 2, 3)), caption=PENTAGON_CAPTION).status_code == 204


@pytest.mark.parametrize("change", [
    {"caption_sha256": None},                                           # a caption with no binding
    {"caption_sha256": "0" * 64},                                       # bound to other words
    {"caption_sha256": hashlib.sha256(PENTAGON_PICK.encode()).hexdigest()},
    {"caption": " " + PENTAGON_CAPTION},                                # not trimmed: never what the page sends
    {"caption": ""}, {"caption": 7},
    {"caption": "x" * 2001, "caption_sha256": hashlib.sha256(b"x" * 2001).hexdigest()},
    {"caption": None},                                                  # a binding with no caption
])
def test_a_captioned_recording_that_is_not_bound_to_its_caption_is_never_offered(tmp_path, change):
    rec = {**json.loads(PENTAGON.read_text()), **change}
    with pytest.raises(ValueError, match="caption"):
        recorded.validate(rec)
    _write(tmp_path / "rec", "p.json", rec)
    assert recorded.load(tmp_path / "rec") == {}


def test_a_receipt_after_a_captioned_recording_carries_no_picks(captioned):
    """#44's ruling holds for the captioned recording: its run id is null, so the receipt asked for after it, with
    the same file and caption, carries no picked words, and an id sent anyway adds none."""
    img, _, client = captioned
    shown = _post(client, img, caption=PENTAGON_CAPTION).json()
    assert shown["note"]["receipt_run"] is None
    files = {"file": (img.name, img.read_bytes(), "image/jpeg")}
    plain = client.post("/api/receipt?format=json", files=files, data={"caption": PENTAGON_CAPTION})
    assert plain.status_code == 200, plain.text
    assert receipts.PICKED_KEY not in json.loads(plain.content)["inputs"] and "x-secondlook-picks" not in plain.headers
    forged = client.post("/api/receipt?format=json", files=files,
                         data={"caption": PENTAGON_CAPTION, "deep_run": "recorded-run-pentagon"})
    assert forged.status_code == 200, forged.text
    assert receipts.PICKED_KEY not in json.loads(forged.content)["inputs"]
    assert forged.headers["x-secondlook-picks"] == "not-added"


def test_the_recording_script_records_a_file_of_ours_with_its_caption(tmp_path, monkeypatch):
    mod = _load_script()
    img = _jpeg(tmp_path / "post.jpg")
    cap = tmp_path / "caption.txt"
    cap.write_text("  " + PENTAGON_CAPTION + "\n")
    db = tmp_path / "claims.sqlite"
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO meta VALUES(?,?)", [("feed_sha256", FEED_SHA), ("downloaded_at", "2026-10-29T14:14:26Z"),
                                                     ("feed_date_modified", "2026-10-29T14:10:29Z"), ("rows", "99385"),
                                                     ("source", LATEST_URL)])
    con.commit()
    con.close()
    model, mmproj = tmp_path / "model.gguf", tmp_path / "mmproj.gguf"
    model.write_bytes(b"model bytes")
    mmproj.write_bytes(b"projector bytes")
    seen = []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def investigate(path, caption, client, rungs):
        seen.append(caption)
        time.sleep(0.1)
        note = _fake_note(path)
        # A path under the repo in a card, as the classifier writes its threshold file: written repo-relative.
        note.evidence = [{"rung": "classifier", "finding": "f", "proves": "p", "does_not_prove": "d",
                          "strength": "none", "raw": {"threshold_file": str(ROOT / "secondlook" / "x.json"),
                                                      "gate": {"gate_file": str(ROOT / "y.json")}, "other": "/tmp/z",
                                                      "rating_explanation": "The publisher's article text."}},
                         {"rung": "claim", "finding": "f", "proves": "p", "does_not_prove": "d", "strength": "none",
                          "raw": {"feed": {"source": LATEST_URL, "feed_sha256": FEED_SHA}}}]
        return note

    monkeypatch.setenv("SECONDLOOK_CLAIMS_DB", str(db))
    monkeypatch.setattr(mod, "run", lambda *cmd: {"status": "", "rev-parse": "c" * 40}[cmd[1]] if cmd[0] == "git"
                        else {"machdep.cpu.brand_string": "Apple Test Chip", "hw.memsize": str(64 << 30)}[cmd[-1]])
    monkeypatch.setattr(mod, "server_files", lambda url: (model, mmproj, "b1-test"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    args = ["record_investigations.py", "--file", str(img), "--caption-file", str(cap),
            "--id", "pentagon", "--licence", "Our own", "--credit", "Made by us"]
    # The index was built from `latest`: with no dated copy named, nothing runs (the model included).
    monkeypatch.setattr(sys, "argv", [*args, "--out", str(tmp_path / "o0")])
    with pytest.raises(SystemExit, match="must name the dated feed copy it searched, not " + re.escape(LATEST_URL)):
        mod.main()
    assert seen == [] and not (tmp_path / "o0").exists()
    monkeypatch.setattr(sys, "argv", [*args, "--feed-url", FEED_URL, "--out", str(tmp_path / "out")])
    assert mod.main() == 0
    assert seen == [PENTAGON_CAPTION], "the deep check ran with the trimmed caption"
    rec = json.loads((tmp_path / "out" / "pentagon.json").read_text())
    recorded.validate(rec)
    assert rec["caption"] == PENTAGON_CAPTION and rec["caption_sha256"] == recorded.caption_sha256(PENTAGON_CAPTION)
    assert rec["sample"] == {"id": "pentagon", "file": img.name, "sha256": hashlib.sha256(img.read_bytes()).hexdigest(),
                             "licence": "Our own", "credit": "Made by us"}
    assert rec["claims_index"]["feed_sha256"] == FEED_SHA and rec["claims_index"]["rows"] == "99385"
    assert rec["claims_index"]["file_sha256"] == hashlib.sha256(db.read_bytes()).hexdigest()
    raw = rec["note"]["evidence"][0]["raw"]
    assert raw == {"threshold_file": "secondlook/x.json", "gate": {"gate_file": "y.json"}, "other": "/tmp/z"}
    assert rec["claims_index"]["source"] == FEED_URL == rec["note"]["evidence"][1]["raw"]["feed"]["source"]
    # A share-alike file of ours is refused, as a share-alike sample is; no claims index means no captioned run.
    monkeypatch.setattr(sys, "argv", [*args, "--licence", "CC BY-SA 4.0", "--out", str(tmp_path / "o2")])
    with pytest.raises(SystemExit, match="BY-SA"):
        mod.main()
    monkeypatch.setenv("SECONDLOOK_CLAIMS_DB", str(tmp_path / "missing.sqlite"))
    monkeypatch.setattr(sys, "argv", [*sys.argv[:sys.argv.index("--licence")], "--licence", "Our own",
                                      "--credit", "Made by us", "--out", str(tmp_path / "o3")])
    with pytest.raises(SystemExit, match="claims index"):
        mod.main()
    assert not (tmp_path / "o2").exists() and not (tmp_path / "o3").exists()


def test_the_recording_names_the_snapshots_commit_whatever_the_ladder_read(tmp_path, monkeypatch):
    """The code that ran is the snapshot of the starting commit, so the note's version is that commit. In the snapshot
    the ladder reads "unknown" (it is not a git repo), or another repo's HEAD if the temp folder sits inside one; the
    note never carries that (A241: the old "HEAD moved" check on the note's version could no longer fire, a reviewer,
    PR #51 note 3). A moved HEAD is caught by the read just before saving (the next test)."""
    mod = _load_script()
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def investigate(path, caption, client, rungs):
        time.sleep(0.1)
        note = _fake_note(path)
        note.version = "d" * 40   # what git said from inside the temp folder: not the commit the snapshot holds
        return note

    model = tmp_path / "model.gguf"
    model.write_bytes(b"m")
    monkeypatch.setattr(mod, "run", lambda *cmd: {"status": "", "rev-parse": "c" * 40}[cmd[1]] if cmd[0] == "git"
                        else {"machdep.cpu.brand_string": "Chip", "hw.memsize": str(8 << 30)}[cmd[-1]])
    monkeypatch.setattr(mod, "server_files", lambda url: (model, None, "b1"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    assert mod.main() == 0
    rec = json.loads((tmp_path / "out" / "02.json").read_text())
    assert rec["git_commit"] == "c" * 40 and rec["note"]["version"] == "c" * 40


def test_the_recording_script_refuses_when_head_moves_after_the_ladder_step(tmp_path, monkeypatch):
    """A commit during the later model steps leaves the note's version (the ladder step's commit) unchanged, so
    the script reads HEAD again just before saving and refuses if it is not the starting commit (Codex, PR #48)."""
    mod = _load_script()
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())
    start, later = "c" * 40, "d" * 40
    head = {"now": start}
    rev_parses = []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def investigate(path, caption, client, rungs):
        time.sleep(0.1)
        note = _fake_note(path)
        note.version = start      # the ladder step read HEAD before anyone committed
        head["now"] = later       # then a commit lands during the model steps
        return note

    def run(*cmd):
        if cmd[0] == "git":
            if cmd[1] == "rev-parse":
                rev_parses.append(head["now"])
                return head["now"]
            return ""
        return {"machdep.cpu.brand_string": "Chip", "hw.memsize": str(8 << 30)}[cmd[-1]]

    model = tmp_path / "model.gguf"
    model.write_bytes(b"m")
    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(mod, "server_files", lambda url: (model, None, "b1"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit, match=r"HEAD moved during the run \(cccccccccccc at the start, "
                                         r"dddddddddddd just before saving\)"):
        mod.main()
    assert rev_parses == [start, later], "HEAD is read at the start and again just before saving"
    assert not (tmp_path / "out" / "02.json").exists()


def test_the_recording_script_parses_and_reads_git_before_it_imports_the_repo():
    """The arguments, then the read of secondlook/'s state and HEAD, refused there: the first things the script does
    after the standard library. Nothing of secondlook (or anything else outside the standard library) is imported
    before them (Codex, PR #48 @ a1f0ff7 and @ 17e3440)."""
    import ast
    tree = ast.parse((ROOT / "scripts" / "record_investigations.py").read_text())
    read = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "BEFORE_IMPORTS" for t in n.targets))
    first_repo = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.ImportFrom)
                      and (n.module or "").startswith("secondlook"))
    assert read < first_repo
    assert {c.func.id for c in ast.walk(tree.body[read]) if isinstance(c, ast.Call)} == {"parse_args", "committed_tree"}
    for n in tree.body[:read]:
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module] if isinstance(n, ast.ImportFrom) else []
        assert all(m.split(".")[0] in sys.stdlib_module_names | {"__future__"} for m in names), names


@pytest.mark.parametrize("before, refusal", [
    (("c" * 40, " M secondlook/ladder.py"), r"secondlook/ has uncommitted changes before the imports"),
    (("c" * 40, "?? secondlook/rungs/new.py"), r"secondlook/ has uncommitted changes before the imports"),
    (("c" * 40, None), r"uncommitted changes before the imports, or git cannot say"),
    (("unknown", ""), r"cannot tell which commit HEAD is \(unknown before the imports\)"),
    ((None, ""), r"cannot tell which commit HEAD is \(git rev-parse failed before the imports\)"),
])
def test_the_recording_script_refuses_a_dirty_tree_before_it_imports_the_repo(monkeypatch, before, refusal):
    """Importing secondlook runs its module-level code, so a dirty tree (or one git cannot vouch for) is refused where
    it is read, before any secondlook module is imported (Codex, PR #48 @ 17e3440). secondlook is taken out of
    sys.modules for the load, and an import hook records (and stops) any import of it."""
    tried = []

    class Hook:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] == "secondlook":
                tried.append(name)
                raise ImportError(f"secondlook imported before the refusal: {name}")
            return None

    for name in [m for m in sys.modules if m.split(".")[0] == "secondlook"]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Hook(), *sys.meta_path])
    with pytest.raises(SystemExit, match=refusal):
        _load_script(*before)
    assert tried == [] and not [m for m in sys.modules if m.split(".")[0] == "secondlook"]


def test_the_recording_script_refuses_when_head_moved_during_its_imports(tmp_path, monkeypatch):
    """A checkout while the script imports the repo's code would load one commit's code and record another's. So
    main() reads HEAD again and it must be the commit read before the imports (Codex, PR #48 @ a1f0ff7). Nothing
    runs and nothing is written."""
    mod = _load_script("d" * 40)
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())
    ran = []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            ran.append("client")

        def check(self):
            return None

    # main() reads a clean tree at "c" * 40: the commit HEAD moved to while the repo's code was imported.
    monkeypatch.setattr(mod, "run", lambda *cmd: {"status": "", "rev-parse": "c" * 40}[cmd[1]])
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", lambda *a, **k: ran.append("investigate"))
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: ran.append("ladder"))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit, match=r"HEAD moved while the script started \(dddddddddddd before the imports, "
                                         r"cccccccccccc after\)"):
        mod.main()
    assert ran == [] and not (tmp_path / "out").exists(), "refused before anything runs or is written"


def test_resave_and_help_read_no_git_state(tmp_path, monkeypatch, capsys):
    """--resave keeps each recording's own commit, so it reads no git state, before the imports or after; with git
    failing it still re-saves. --help reads none either (Codex, PR #48 @ 17e3440)."""
    path = tmp_path / "02.json"
    path.write_text((COMMITTED / "02.json").read_text())
    git = []
    mod = _load_script(None, None, argv=("--resave", str(path)), git=git)
    assert git == [] and mod.BEFORE_IMPORTS is None and mod.CODE == mod.ROOT, "no snapshot for --resave"
    real = subprocess.run

    def failing(cmd, *a, **k):
        if cmd[0] == "git":
            git.append(tuple(cmd))
            raise subprocess.CalledProcessError(128, cmd, "", "fatal: not a git repository")
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", failing)
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "--resave", str(path)])
    assert mod.main() == 0 and git == []
    assert json.loads(path.read_text()) == json.loads((COMMITTED / "02.json").read_text())
    monkeypatch.setattr(subprocess, "run", real)
    with pytest.raises(SystemExit) as stop:
        _load_script(None, None, argv=("--help",), git=git)
    assert stop.value.code == 0 and git == [] and "--resave" in capsys.readouterr().out


def test_the_recording_script_refuses_an_edit_made_before_the_rungs_load(tmp_path, monkeypatch):
    """The rungs load after the model is hashed, so an edit to secondlook/ then runs in the recording while HEAD and
    the note's version stay the starting commit. The read just before saving checks secondlook/'s state as well as
    HEAD, and nothing is saved (Codex, PR #48 @ 17e3440)."""
    mod = _load_script()
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())
    tree, ran = {"status": ""}, []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def server_files(url):
        tree["status"] = " M secondlook/rungs/exif.py"    # edited while the model is hashed, before the rungs load
        return model, None, "b1"

    def investigate(path, caption, client, rungs):
        ran.append(path)
        time.sleep(0.1)
        return _fake_note(path)     # version "c" * 40: the note cannot tell

    model = tmp_path / "model.gguf"
    model.write_bytes(b"m")
    monkeypatch.setattr(mod, "run", lambda *cmd: {"status": tree["status"], "rev-parse": "c" * 40}[cmd[1]]
                        if cmd[0] == "git" else {"machdep.cpu.brand_string": "Chip", "hw.memsize": str(8 << 30)}[cmd[-1]])
    monkeypatch.setattr(mod, "server_files", server_files)
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit, match="secondlook/ has uncommitted changes just before saving"):
        mod.main()
    assert ran == [str(img)], "the run got as far as saving"
    assert not (tmp_path / "out" / "02.json").exists()


def _repo_with_the_script(tmp_path: Path) -> tuple[Path, str]:
    """A throwaway git repo holding this checkout's secondlook/ and recording script, committed: the script run there
    names that commit, and git runs for real."""
    import shutil
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "secondlook", repo / "secondlook", ignore=shutil.ignore_patterns("__pycache__", "interim"))
    (repo / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / "record_investigations.py", repo / "scripts" / "record_investigations.py")
    (repo / ".gitignore").write_text("__pycache__/\nsecondlook/trust/interim/\n")

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                               "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                              cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-q", "-m", "the committed code")
    return repo.resolve(), git("rev-parse", "HEAD")


def test_the_recording_script_runs_the_committed_code_not_an_edit_made_and_undone(tmp_path, monkeypatch):
    """Codex on PR #48 @ 6783d83: an edit to secondlook/ made after the script read git and undone before the save
    check was imported, ran, and was saved under the clean commit. The script now imports secondlook from `git archive`
    of the commit it read (A228), so the edit never loads, and the run records that commit with the committed code.
    The fetched trust data (git-ignored) goes into the snapshot; an ignored Python file does not."""
    pytest.importorskip("PIL")
    repo, sha = _repo_with_the_script(tmp_path)
    ladder_py = repo / "secondlook" / "ladder.py"
    committed = ladder_py.read_bytes()
    interim = repo / "secondlook" / "trust" / "interim"
    interim.mkdir(parents=True)
    (interim / "anchors.pem").write_text("fetched trust data")
    (interim / "stray.py").write_text("raise SystemExit('an ignored Python file ran')\n")
    edits = []

    def after(cmd):   # the edit lands just after the read of HEAD before the imports, and before the imports
        if cmd == ("git", "rev-parse", "HEAD") and not edits:
            ladder_py.write_bytes(committed + b"\nEDITED_IN_THE_WORKING_TREE = True\n")
            edits.append(cmd)

    mod = _load_script(script=repo / "scripts" / "record_investigations.py", after=after)
    ladder_py.write_bytes(committed)    # undone before anything reads secondlook/'s state again
    assert edits and mod.ROOT == repo and mod.BEFORE_IMPORTS == sha
    assert not hasattr(mod.ladder, "EDITED_IN_THE_WORKING_TREE"), "the edit in the working tree was imported"
    code = mod.CODE
    assert code != repo and Path(mod.ladder.__file__).resolve() == code / "secondlook" / "ladder.py"
    assert (code / "secondlook" / "ladder.py").read_bytes() == committed
    assert (code / "secondlook" / "trust" / "interim" / "anchors.pem").read_text() == "fetched trust data"
    assert not (code / "secondlook" / "trust" / "interim" / "stray.py").exists()
    assert mod.DEFAULT_OUT == repo / "samples" / "recorded", "recordings go to the working tree, not the snapshot"

    # The run itself: unaffected, it records the commit it read, from the snapshot's code.
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())
    model = tmp_path / "model.gguf"
    model.write_bytes(b"m")
    seen = []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def investigate(path, caption, client, rungs):
        seen.append(hasattr(mod.ladder, "EDITED_IN_THE_WORKING_TREE"))
        time.sleep(0.1)
        note = _fake_note(path)
        note.version = sha
        return note

    monkeypatch.setattr(mod, "server_files", lambda url: (model, None, "b1"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    assert mod.main() == 0 and seen == [False]
    assert json.loads((tmp_path / "out" / "02.json").read_text())["git_commit"] == sha


def test_the_recording_script_refuses_secondlook_imported_from_anywhere_but_its_snapshot():
    """In its own process the script imports secondlook from its snapshot. If secondlook was already imported from
    somewhere else (the working tree here), it refuses rather than record code that is not the snapshot's."""
    with pytest.raises(SystemExit, match=r"^record_investigations: secondlook(, .+)? already imported before the "
                                         r"snapshot"):
        _load_script(own_modules=False)


def test_the_recording_script_refuses_a_cached_child_module_and_writes_nothing(tmp_path, monkeypatch):
    """Codex on PR #51 @ 5f7b182: with secondlook.investigator cached from the working tree and the package itself
    not, the package imported from the snapshot and passed the check, but `from secondlook import investigator` reused
    the cached working-tree module, so code the snapshot does not hold would run under its commit. Any secondlook module
    already in sys.modules is now refused before the snapshot is made: no archive is read and no folder is written."""
    import tempfile

    import secondlook.investigator as working_tree_investigator
    for name in [m for m in sys.modules if m.split(".")[0] == "secondlook" and m != "secondlook.investigator"]:
        monkeypatch.delitem(sys.modules, name)
    assert sys.modules["secondlook.investigator"] is working_tree_investigator and "secondlook" not in sys.modules
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    git = []
    with pytest.raises(SystemExit, match=r"^record_investigations: secondlook\.investigator already imported before "
                                         r"the snapshot; run the script in its own Python process$"):
        _load_script(git=git, own_modules=False)
    assert not [cmd for cmd in git if cmd[1] in ("archive", "ls-files")], "the snapshot was started"
    assert list(tmp_path.glob("secondlook-recording-*")) == []
    assert "secondlook" not in sys.modules, "the package was imported after the refusal"


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_signal_removes_the_snapshot_and_exits_non_zero(tmp_path, sig):
    """Codex on PR #51 @ 5f7b182: the snapshot was removed only by atexit, which does not run when a signal ends the
    process, so stopping a long investigation left secondlook-recording-* behind. A real process loads the script in a
    throwaway repo (a snapshot is made), says where the snapshot is, and waits; it is sent the signal and must remove
    the snapshot and exit non-zero. TMPDIR is the test's own folder, so only this process's snapshots are counted."""
    pytest.importorskip("PIL")
    repo, _ = _repo_with_the_script(tmp_path)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    script = str(repo / "scripts" / "record_investigations.py")
    load = ("import importlib.util, sys, time\n"
            "sys.argv = ['record_investigations.py', '02']\n"
            f"spec = importlib.util.spec_from_file_location('record_investigations', {script!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "print(mod.CODE, flush=True)\n"
            "time.sleep(60)\n")
    env = {**os.environ, "TMPDIR": str(tmp)}
    env.pop("PYTHONPATH", None)
    proc = subprocess.Popen([sys.executable, "-c", load], cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        code = Path(proc.stdout.readline().strip())
        assert code.is_dir() and code.name.startswith("secondlook-recording-") and code.parent == tmp.resolve(), \
            proc.stderr.read() if proc.poll() is not None else code
        proc.send_signal(sig)
        assert proc.wait(timeout=20) == 128 + sig
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert not code.exists() and list(tmp.glob("secondlook-recording-*")) == []


def test_the_recording_script_refuses_when_git_cannot_write_the_snapshot():
    mod = _load_script()
    with pytest.raises(SystemExit, match=r"git could not write 000000000000's secondlook/ to a snapshot"):
        mod.snapshot("0" * 40)


def test_resave_makes_paths_repo_relative_and_changes_nothing_else(tmp_path, monkeypatch):
    mod = _load_script()
    rec = _record_for(_jpeg(tmp_path / "a.jpg"))
    card = next(e for e in rec["note"]["evidence"] if "threshold_file" in e["raw"])
    card["raw"]["threshold_file"] = str(ROOT / card["raw"]["threshold_file"])
    path = tmp_path / "a.json"
    path.write_text(json.dumps(rec))
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "--resave", str(path)])
    assert mod.main() == 0
    out = json.loads(path.read_text())
    assert not Path(next(e for e in out["note"]["evidence"] if "threshold_file" in e["raw"])["raw"]["threshold_file"]).is_absolute()
    card["raw"]["threshold_file"] = str(Path(card["raw"]["threshold_file"]).relative_to(ROOT))
    assert out == rec


def test_resave_names_the_dated_feed_copy_a_captioned_recording_searched(tmp_path, monkeypatch):
    """a reviewer, PR #48 note 1: claims_index and the fact-check card's feed name the dated copy, never `latest`;
    only a dated copy whose folder is the feed's own dateModified, to the second, is taken."""
    mod = _load_script()
    rec = json.loads(PENTAGON.read_text())
    rec = mod.with_feed_source(rec, FEED_SHA, LATEST_URL)   # as it was saved from an index built from `latest`
    path = tmp_path / "pentagon.json"
    path.write_text(json.dumps(rec))
    wrong = FEED_URL.replace("141029", "141030")
    for extra, refused in (([], LATEST_URL), (["--feed-url", wrong], wrong), (["--feed-url", LATEST_URL], LATEST_URL)):
        monkeypatch.setattr(sys, "argv", ["record_investigations.py", "--resave", str(path), *extra])
        with pytest.raises(SystemExit, match="not " + re.escape(refused)):
            mod.main()
        assert json.loads(path.read_text()) == rec, "nothing is written when the copy is refused"
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "--resave", str(path), "--feed-url", FEED_URL])
    assert mod.main() == 0
    out = json.loads(path.read_text())
    assert out == json.loads(PENTAGON.read_text()), "the committed recording is this re-save, and only the URLs moved"
    assert "/latest/" not in path.read_text()
    # A recording without a caption has no feed, so it needs no URL and a resave leaves it as it was.
    plain = tmp_path / "02.json"
    plain.write_text((COMMITTED / "02.json").read_text())
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "--resave", str(plain)])
    assert mod.main() == 0 and json.loads(plain.read_text()) == json.loads((COMMITTED / "02.json").read_text())


@pytest.mark.parametrize("when", ["start", "before saving", "both"])
@pytest.mark.parametrize("git_says", ["unknown", "", "HEAD", "fatal", "c" * 39])
def test_the_recording_script_refuses_when_it_cannot_tell_the_commit(tmp_path, monkeypatch, when, git_says):
    """a reviewer, PR #48 note 6: a recording never names an unknown commit. If git cannot say which commit HEAD
    is, at the start or on the re-read just before saving, nothing is saved."""
    mod = _load_script()
    img = _jpeg(tmp_path / "s02.jpg")
    sample = sample_catalogue.Sample("02", img.name, "T", "K", "C", "CC BY 4.0", "src",
                                     hashlib.sha256(img.read_bytes()).hexdigest())
    rev_parses, ran = [], []

    class Client:
        url = "http://127.0.0.1:1"

        def __init__(self, **_):
            pass

        def check(self):
            return None

    def investigate(path, caption, client, rungs):
        ran.append(path)
        time.sleep(0.1)
        note = _fake_note(path)
        note.version = "unknown"    # the ladder could not tell either
        return note

    def run(*cmd):
        if cmd[0] == "git":
            if cmd[1] != "rev-parse":
                return ""
            rev_parses.append(cmd)
            if when == "both" or (when == "start") == (len(rev_parses) == 1):
                if git_says == "fatal":
                    raise subprocess.CalledProcessError(128, cmd, "", "fatal: not a git repository")
                return git_says
            return "c" * 40
        return {"machdep.cpu.brand_string": "Chip", "hw.memsize": str(8 << 30)}[cmd[-1]]

    model = tmp_path / "model.gguf"
    model.write_bytes(b"m")
    monkeypatch.setattr(mod, "run", run)
    monkeypatch.setattr(mod, "server_files", lambda url: (model, None, "b1"))
    monkeypatch.setattr(mod.g, "GlimmerClient", Client)
    monkeypatch.setattr(mod.inv, "investigate", investigate)
    monkeypatch.setattr(mod.ladder, "run", lambda *a, **k: None)
    monkeypatch.setattr(mod.rung_registry, "load", lambda which=None: [] if which else list(RUNGS))
    monkeypatch.setattr(mod.sample_catalogue, "available", lambda folder: {"02": (sample, img)})
    monkeypatch.setattr(sys, "argv", ["record_investigations.py", "02", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit, match="cannot tell which commit HEAD is"):
        mod.main()
    # "both" is the case the earlier check let through: the same non-commit text twice compared equal.
    assert len(rev_parses) == (2 if when == "before saving" else 1)
    assert ran == ([str(img)] if when == "before saving" else []), "at the start, nothing runs"
    assert not (tmp_path / "out" / "02.json").exists()


def test_a_sample_is_matched_by_its_bytes(tmp_path, monkeypatch):
    img = _jpeg(tmp_path / "s1.jpg")
    other = _jpeg(tmp_path / "s2.jpg", (10, 10, 10))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    monkeypatch.setattr(sample_catalogue, "CATALOGUE", (
        sample_catalogue.Sample("02", "s1.jpg", "T", "K", "C", "CC BY 4.0", "src", sha(img)),
        sample_catalogue.Sample("03", "s2.jpg", "T", "K", "C", "Public domain", "src", sha(other))))
    rec_dir = tmp_path / "rec"
    _write(rec_dir, "02.json", _record_for(img))
    client = TestClient(create_app(stub=False, rungs=RUNGS, samples_dir=str(tmp_path), recorded_dir=str(rec_dir)))
    assert client.get("/api/samples/02/recorded").json()["sha256"] == sha(img)
    assert client.get("/api/samples/03/recorded").status_code == 204, "a sample with no recording: no content"
    for bad in ("../02", "99", "s1.jpg"):
        assert client.get(f"/api/samples/{bad}/recorded").status_code == 404, bad


@pytest.mark.parametrize("change", [
    {"caption": "Taken in Utah."},
    {"seconds": 0},
    {"machine": " "},
    {"date_utc": "29 Oct 2026"},
    {"git_commit": "f0f7e87"},
    {"schema": "second-look-recorded-run/0"},
])
def test_an_incomplete_recording_is_never_offered(tmp_path, change):
    img = _jpeg(tmp_path / "a.jpg")
    rec = _record_for(img, **change)
    with pytest.raises(ValueError):
        recorded.validate(rec)
    _write(tmp_path / "rec", "r.json", rec)
    assert recorded.load(tmp_path / "rec") == {}


@pytest.mark.parametrize("note_change", [{"glimmer": "unavailable"}, {"demo": True}, {"steps": None, "answer": None},
                                         {"receipt_run": "a0b1c2d3e4f5a6b7c8d9e0f1"}])
def test_a_recording_without_a_model_run_is_never_offered(tmp_path, note_change):
    rec = _record_for(_jpeg(tmp_path / "a.jpg"))
    rec["note"].update(note_change)
    for k, v in note_change.items():
        if v is None:
            del rec["note"][k]
    with pytest.raises(ValueError):
        recorded.validate(rec)


def test_the_model_sha_and_name_are_required(tmp_path):
    rec = _record_for(_jpeg(tmp_path / "a.jpg"))
    rec["model"] = {**rec["model"], "sha256": ""}
    with pytest.raises(ValueError):
        recorded.validate(rec)
    rec["model"] = {**rec["model"], "sha256": PINNED_MODEL_SHA, "name": "Llama"}
    with pytest.raises(ValueError):
        recorded.validate(rec)


def test_the_stub_offers_no_recordings(setup):
    img, rec_dir, _ = setup
    client = TestClient(create_app(stub=True, recorded_dir=str(rec_dir)))
    assert _post(client, img).status_code == 204
    assert client.get("/api/health").json()["capabilities"]["recorded"] is False


def test_health_says_whether_recordings_are_here(setup, tmp_path):
    _, _, client = setup
    h = client.get("/api/health").json()
    assert h["capabilities"]["recorded"] is True and h["details"]["recorded"] == {"count": 1}
    empty = TestClient(create_app(stub=False, rungs=RUNGS, recorded_dir=str(tmp_path / "none")))
    assert empty.get("/api/health").json()["capabilities"]["recorded"] is False


def test_hostile_markup_in_a_recording_is_served_as_plain_text(tmp_path):
    img = _jpeg(tmp_path / "a.jpg")
    rec = _hostile(_record_for(img))
    recorded.validate(rec)
    _write(tmp_path / "rec", "r.json", rec)
    client = TestClient(create_app(stub=False, rungs=RUNGS, recorded_dir=str(tmp_path / "rec")))
    body = _post(client, img).json()
    assert HOSTILE in body["label"] and body["note"]["image_text"] == [HOSTILE], "served as is, as JSON text"
    # The page puts recorded text in with textContent and text nodes only; it never parses markup.
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createRange"):
        assert sink not in INV, sink


def test_the_recorded_route_keeps_the_upload_limits(setup, tmp_path):
    _, _, client = setup
    assert REQUEST_LIMITS["/api/recorded"] == REQUEST_LIMITS["/api/check"]
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    assert client.post("/api/recorded", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 415


def test_a_receipt_after_a_recorded_run_carries_no_picks(setup):
    """the project lead's ruling (DAT-850, PR #42 x PR #44): a recorded run is never a server run, so its body carries
    receipt_run null, and a receipt asked for after it carries no picked claim words."""
    img, _, client = setup
    shown = _post(client, img).json()
    assert "receipt_run" in shown["note"] and shown["note"]["receipt_run"] is None
    files = {"file": (img.name, img.read_bytes(), "image/jpeg")}
    plain = client.post("/api/receipt?format=json", files=files)
    assert plain.status_code == 200, plain.text
    assert receipts.PICKED_KEY not in json.loads(plain.content)["inputs"] and "x-secondlook-picks" not in plain.headers
    # Nothing a recording holds is on record as a deep check here, so even an id sent with it adds no picks.
    forged = client.post("/api/receipt?format=json", files=files, data={"deep_run": "recorded-run-02"})
    assert forged.status_code == 200, forged.text
    assert receipts.PICKED_KEY not in json.loads(forged.content)["inputs"]
    assert forged.headers["x-secondlook-picks"] == "not-added"
    assert forged.headers["x-secondlook-picks-reason"] == receipts.NOT_ADDED["unknown"]


# ---- the live path is unchanged -----------------------------------------------------------------------

def test_investigate_runs_live_even_for_a_recorded_file(setup):
    """A recording is never served from /api/investigate: the live investigator runs, as before."""
    img, rec_dir, _ = setup
    calls = []

    def live(path, caption):
        calls.append(Path(path).read_bytes())
        return DeskNote(file=Path(path).name, answer="cant_tell", summary="live", evidence=[], steps=[],
                        explanation="live")

    client = TestClient(create_app(stub=False, rungs=RUNGS, investigator=live, recorded_dir=str(rec_dir)))
    body = client.post("/api/investigate", files={"file": (img.name, img.read_bytes(), "image/jpeg")}).json()
    assert calls == [img.read_bytes()]
    assert body["summary"] == "live" and body["glimmer"] == "ran"
    assert "recorded" not in body and "live" not in body and body["label"] != recorded.label(_record_for(img))


def test_the_page_asks_for_a_recording_only_when_glimmer_is_down():
    # The recording is asked for only after /api/health says no Glimmer server answers (judge persona test, 30 Oct:
    # the line and the button follow capabilities.glimmer, not whether a recording matches).
    check = INV.split("async function deepCheck(of, id) {", 1)[1].split("async function offerRecording", 1)[0]
    assert "if (caps.glimmer === true) {" in check and "if (caps.recorded) offerRecording(of, id, notRunning);" in check
    assert check.index("caps.glimmer === true") < check.index("offerRecording(of, id, notRunning)")
    # A recording's deep-evidence event carries its label and a null run id, so the strip says it is recorded and
    # no receipt takes picks from it (DAT-850, mock judging v2 item 1; #44's ruling).
    click = INV.split('recGo.addEventListener("click"', 1)[1].split("go.addEventListener", 1)[0]
    assert "receipt_run: null" in click and "recorded: recording.label" in click
    # The label leads the note it belongs to.
    note = HTML.split('id="inv-note"', 1)[1]
    assert note.index('id="inv-recorded-label"') < note.index('id="inv-eyebrow">Desk note')


# ---- the page, in a real headless browser ---------------------------------------------------------------

SERVER = r"""
import json, sys, uvicorn
from secondlook.rungs import exif
from secondlook.web import samples as sample_catalogue
from secondlook.web.app import create_app
# The gallery holds one sample, "02", whose bytes are the recorded file's, so the sample route is tested
# without the fetched samples.
sample_catalogue.CATALOGUE = (sample_catalogue.Sample(**json.loads(sys.argv[4])),)
app = create_app(stub=False, rungs=[("exif", exif.check)], recorded_dir=sys.argv[2], samples_dir=sys.argv[3])
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
"""

# A241 (DAT-850, a reviewer on PR #51 note 2): the page tests that need health to say "not running" drive that
# answer here. The real server's health is read once, with no deadline, and every ask of the page's gets it back at
# once with Glimmer not_running. Left to the server, desk.glimmer()'s 1.5 s deadline turns a slow probe of the closed
# port into "unavailable" on a busy host, and the page then rightly says the neutral line instead.
HEALTH_DOWN = r"""
  const real = await (await page.request.get(base + "/api/health", { timeout: 60000 })).json();
  const down = { ...real, capabilities: { ...real.capabilities, glimmer: false },
                 details: { ...real.details, glimmer: { ready: false, reason: "not_running" } } };
"""

DOWN_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], asked = [], live = [];
  __HEALTH_DOWN__
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") return route.fulfill({ json: down });
    if (url.pathname === "/api/recorded") asked.push(url.pathname);
    if (url.pathname === "/api/investigate") live.push(url.pathname);
    if (url.pathname === "/api/receipt") {
      const body = route.request().postDataBuffer();
      receiptAsked.push({ deepRun: !!body && body.toString("latin1").includes('name="deep_run"') });
    }
    return route.continue();
  });
  const receiptAsked = [];
  const vis = sel => page.locator(sel).isVisible();
  await page.goto(base + "/");
  await page.evaluate(() => { window.__localLive = document.getElementById("inv-local").textContent; });
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const answerBefore = await page.locator("#answer").textContent();
  const localLive = await page.evaluate(() => window.__localLive);
  const offered = { offer: await page.locator("#inv-recorded-offer").textContent(), liveButton: await vis("#inv-go"),
                    button: await page.locator("#inv-recorded-go").textContent(),
                    local: await page.locator("#inv-local").textContent() };
  await page.click("#inv-recorded-go");
  await page.locator("#inv-note").waitFor({ state: "visible" });
  const shown = await page.evaluate(() => {
    const note = document.getElementById("inv-note");
    const first = [...note.children].find(c => !c.hidden);
    return { firstId: first && first.id, label: document.getElementById("inv-recorded-label").textContent,
             focused: document.activeElement && document.activeElement.id,
             invAnswer: document.getElementById("inv-answer").textContent,
             status: document.getElementById("inv-status").textContent,
             meta: document.getElementById("inv-meta").textContent,
             eyebrow: document.getElementById("inv-eyebrow").textContent,
             steps: document.querySelectorAll("#inv-steps li").length,
             setup: document.getElementById("inv-setup").hidden };
  });
  const answerAfter = await page.locator("#answer").textContent();
  // A receipt asked for after the recorded run: no run id goes with it, and none of its words are picks.
  const [res] = await Promise.all([page.waitForResponse(r => new URL(r.url()).pathname === "/api/receipt"),
                                   page.click("#receipt-json")]);
  await page.waitForFunction(() => /^Saved /.test(document.getElementById("receipt-status").textContent));
  const receipt = { asked: receiptAsked, status: res.status(), picks: res.headers()["x-secondlook-picks"] || null,
                    ...await page.evaluate(() => ({ pickedLine: !document.getElementById("receipt-picked").hidden,
                                                    said: document.getElementById("receipt-status").textContent })) };
  // A different file: nothing recorded is offered, and the live button is back.
  await page.setInputFiles("#file", "__OTHER__");
  await page.waitForFunction(() => !document.getElementById("result").hasAttribute("aria-busy")
                             && document.getElementById("inv-note").hidden);
  // Health says not_running, so the page asks for a recording of this file too (there is none): wait for that ask
  // (60 s bound, not 8 s), so the line read below is the one set after health answered, at any load (A241).
  const n = asked.length;
  const deadline = Date.now() + 60000;
  while (asked.length === n && Date.now() < deadline) await page.waitForTimeout(100);
  await page.waitForTimeout(800);
  const other = { offer: await vis("#inv-recorded-offer"), button: await vis("#inv-recorded-go"),
                  liveButton: await vis("#inv-go"), label: await vis("#inv-recorded-label") };
  const localAfter = await page.locator("#inv-local").textContent();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { offered, shown, answerBefore, answerAfter, other, asked, live, offHost, localLive, localAfter, receipt };
}
"""

UP_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], asked = [], live = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {   // Glimmer up, as /api/health says when its server answers
      const res = await route.fetch();
      const h = await res.json();
      h.capabilities.glimmer = true;
      return route.fulfill({ response: res, json: h });
    }
    if (url.pathname === "/api/recorded" || /\/recorded$/.test(url.pathname)) asked.push(url.pathname);
    if (url.pathname === "/api/investigate") {
      live.push(url.pathname);
      return route.fulfill({ json: __LIVE__ });
    }
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-go").waitFor({ state: "visible" });
  await page.waitForTimeout(2500);  // time for any recorded-run offer to appear; none should
  const before = { offer: await vis("#inv-recorded-offer"), button: await vis("#inv-recorded-go"),
                   liveButton: await vis("#inv-go") };
  await page.click("#inv-go");
  await page.locator("#inv-note").waitFor({ state: "visible" });
  const out = { before, asked, live, offHost,
                label: await vis("#inv-recorded-label"),
                invAnswer: await page.locator("#inv-answer").textContent(),
                eyebrow: await page.locator("#inv-eyebrow").textContent(),
                meta: await page.locator("#inv-meta").textContent(),
                local: await page.locator("#inv-local").textContent(),
                status: await page.locator("#inv-status").textContent() };
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return out;
}
"""


SAMPLE_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], asked = [], live = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (/recorded$/.test(url.pathname)) asked.push(url.pathname);
    if (url.pathname === "/api/investigate") live.push(url.pathname);
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  await page.goto(base + "/");
  await page.click('#gallery button[data-id="02"]');
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const answerBefore = await page.locator("#answer").textContent();
  const offered = await page.evaluate(() => {
    const b = document.getElementById("inv-recorded-go").getBoundingClientRect();
    return { buttonHeight: b.height, liveButton: !document.getElementById("inv-go").hidden,
             offerWeight: getComputedStyle(document.getElementById("inv-recorded-offer")).fontWeight,
             local: document.getElementById("inv-local").textContent };
  });
  await page.click("#inv-recorded-go");
  await page.locator("#inv-note").waitFor({ state: "visible" });
  await page.waitForTimeout(1200);  // the smooth scroll to the label
  const shown = await page.evaluate(() => {
    const label = document.getElementById("inv-recorded-label");
    return { label: label.textContent, focused: document.activeElement && document.activeElement.id,
             scrollMargin: getComputedStyle(label).scrollMarginTop,
             labelTop: Math.round(label.getBoundingClientRect().top),
             eyebrow: document.getElementById("inv-eyebrow").textContent,
             meta: document.getElementById("inv-meta").textContent,
             steps: document.querySelectorAll("#inv-steps li").length };
  });
  const answerAfter = await page.locator("#answer").textContent();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { offered, shown, answerBefore, answerAfter, asked, live, offHost };
}
"""

# Two checks on the page's requests: the caption goes with the file to /api/recorded (so a captioned check is
# refused), and a recording that answers after a newer check started is dropped.
CAPTION_RACE_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], asked = [], held = [];
  let hold = false, lateDone = false;
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/recorded") {
      const body = (req.postDataBuffer() || Buffer.alloc(0)).toString("latin1");
      const res = await route.fetch();
      asked.push({ caption: /name="caption"\r\n\r\nTaken in Utah\.\r\n/.test(body), status: res.status() });
      if (hold) {
        hold = false;
        held.push(res.status());
        await new Promise(r => setTimeout(r, 4000));
        await route.fulfill({ response: res });
        lateDone = true;
        return;
      }
      return route.fulfill({ response: res });
    }
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  const settled = () => page.waitForFunction(() => !document.getElementById("result").hasAttribute("aria-busy"));
  const until = async (ok) => { const end = Date.now() + 15000; while (!ok() && Date.now() < end) await page.waitForTimeout(100); };
  await page.goto(base + "/");

  // 1. With a caption: the recorded file is sent with it, the server has no recording for that, nothing is offered.
  await page.fill("#caption", "Taken in Utah.");
  await page.setInputFiles("#file", "__RECORDED__");
  await settled();
  await until(() => asked.length >= 1);
  await page.waitForTimeout(800);
  const captioned = { asked: asked.slice(), offer: await vis("#inv-recorded-offer"),
                      button: await vis("#inv-recorded-go"), liveButton: await vis("#inv-go") };

  // 2. A fresh page, no caption, and the recording's answer is held back 4 s. A newer file is checked meanwhile.
  await page.goto(base + "/");   // a fresh page: no caption
  hold = true;
  await page.setInputFiles("#file", "__RECORDED__");
  await until(() => held.length === 1);
  await page.setInputFiles("#file", "__OTHER__");
  await settled();
  await until(() => lateDone);
  await page.waitForTimeout(1000);
  const raced = { held: held.slice(), lateDone, asked: asked.slice(1), offer: await vis("#inv-recorded-offer"),
                  button: await vis("#inv-recorded-go"), liveButton: await vis("#inv-go"),
                  label: await vis("#inv-recorded-label"),
                  local: await page.locator("#inv-local").textContent() };
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { captioned, raced, offHost };
}
"""

CAPTIONED_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const caption = __CAPTION__;
  const offHost = [], asked = [], live = [], receiptAsked = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/investigate") live.push(url.pathname);
    if (url.pathname === "/api/recorded") {
      const res = await route.fetch();
      asked.push(res.status());
      return route.fulfill({ response: res });
    }
    if (url.pathname === "/api/receipt") {
      const body = route.request().postDataBuffer();
      receiptAsked.push({ deepRun: !!body && body.toString("latin1").includes('name="deep_run"') });
    }
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  const text = sel => page.locator(sel).textContent();
  const settled = () => page.waitForFunction(() => !document.getElementById("result").hasAttribute("aria-busy"));
  const until = async (ok) => { const end = Date.now() + 15000; while (!ok() && Date.now() < end) await page.waitForTimeout(100); };
  // Where the click lands (design review of PR #48, S1, S2, N1, N2): what is in view, what has focus, the strip's
  // pick line, the link's height, and the note's cards.
  const view = () => page.evaluate(() => {
    const box = id => document.getElementById(id).getBoundingClientRect();
    const strip = document.getElementById("factcheck"), a = document.querySelector("#fc-line a");
    return { focused: document.activeElement && document.activeElement.id,
             firstInStrip: ([...strip.children].find(c => !c.hidden) || {}).id,
             stripTop: Math.round(box("factcheck").top), bandTop: Math.round(box("fc-recorded").top),
             pickBottom: Math.round(box("fc-pick").bottom), height: innerHeight,
             pick: document.getElementById("fc-pick").hidden ? null : document.getElementById("fc-pick").textContent,
             link: a && { height: Math.round(a.getBoundingClientRect().height), text: a.textContent },
             cards: document.getElementById("inv-cards").textContent + document.getElementById("inv-rerun-list").textContent,
             cardsShown: !document.getElementById("inv-cards-wrap").hidden,
             overflowX: document.documentElement.scrollWidth > innerWidth };
  });
  const views = {};

  // 1. The recorded file with the recorded caption: offered, then shown under its label.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  await page.fill("#caption", caption);
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const answer = await text("#answer");
  const stripBefore = await vis("#factcheck");
  await page.click("#inv-recorded-go");
  await page.locator("#inv-note").waitFor({ state: "visible" });
  await page.locator("#factcheck").waitFor({ state: "visible" });
  const shown = { label: await text("#inv-recorded-label"), caption: await text("#inv-recorded-caption"),
                  captionVisible: await vis("#inv-recorded-caption"), reading: await text("#inv-reading-list"),
                  readingTitle: await text("#inv-reading-title"), invAnswer: await text("#inv-answer"),
                  strip: await vis("#factcheck"), fcTitle: await text("#fc-title"), fcRecorded: await text("#fc-recorded"),
                  fcLine: await text("#fc-line"), fcCredit: await text("#fc-credit"), answerAfter: await text("#answer") };
  await page.waitForTimeout(1200);  // the smooth scroll to the strip
  views[1280] = await view();
  const [res] = await Promise.all([page.waitForResponse(r => new URL(r.url()).pathname === "/api/receipt"),
                                   page.click("#receipt-json")]);
  await page.waitForFunction(() => /^Saved /.test(document.getElementById("receipt-status").textContent));
  const receipt = { asked: receiptAsked.slice(), status: res.status(), picks: res.headers()["x-secondlook-picks"] || null,
                    pickedLine: await vis("#receipt-picked") };

  // The same click on a 390 px phone screen.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + "/");
  await page.fill("#caption", caption);
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  await page.click("#inv-recorded-go");
  await page.locator("#factcheck").waitFor({ state: "visible" });
  await page.waitForTimeout(1200);
  views[390] = await view();
  await page.setViewportSize({ width: 1280, height: 900 });

  // 2. The same file with another caption, then with none: nothing is offered, and no strip shows.
  const others = [];
  for (const other of [caption + " Stay safe everyone", ""]) {
    await page.goto(base + "/");
    if (other) await page.fill("#caption", other);
    const n = asked.length;
    await page.setInputFiles("#file", "__RECORDED__");
    await settled();
    await until(() => asked.length > n);
    await page.waitForTimeout(800);
    others.push({ status: asked[asked.length - 1], offer: await vis("#inv-recorded-offer"),
                  button: await vis("#inv-recorded-go"), liveButton: await vis("#inv-go"), strip: await vis("#factcheck") });
  }
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { answer, stripBefore, shown, views, receipt, others, asked, live, offHost };
}
"""

HOSTILE_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], fetched = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/pwned.png") fetched.push(url.pathname);
    return route.continue();
  });
  await page.goto(base + "/");
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  await page.click("#inv-recorded-go");
  await page.locator("#inv-note").waitFor({ state: "visible" });
  await page.waitForTimeout(1000);
  const out = await page.evaluate(() => {
    const inv = document.getElementById("investigate");
    return { pwned: window.__pwned === undefined ? null : window.__pwned,
             parsed: inv.querySelectorAll("img, script, [onerror]").length,
             label: document.getElementById("inv-recorded-label").textContent,
             steps: document.getElementById("inv-steps").textContent,
             reading: document.getElementById("inv-reading-list").textContent,
             cards: document.getElementById("inv-cards").textContent + document.getElementById("inv-rerun-list").textContent,
             meta: document.getElementById("inv-meta").textContent,
             explain: document.getElementById("inv-explain-label").textContent };
  });
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { ...out, offHost, fetched };
}
"""


# The judge persona test of 30 Oct (DAT-850): with no Glimmer server, the deep-check box said "Muse Glimmer runs on
# this computer" and showed a live button for every file with no recording. Now the line and the button follow
# /api/health's capabilities.glimmer. Health is held back for the first file until the task has read the state before
# it answers, and refused for the last one, to see that no answer reads as no Glimmer.
GLIMMER_DOWN_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const shots = __SHOTS__;
  const offHost = [], asked = [], live = [], held = [];
  let holdHealth = false, failHealth = false;
  __HEALTH_DOWN__
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health" && failHealth) return route.abort();
    if (url.pathname === "/api/health") {
      if (holdHealth) await new Promise(r => held.push(r));
      return route.fulfill({ json: down });
    }
    if (/recorded$/.test(url.pathname)) asked.push(url.pathname);
    if (url.pathname === "/api/investigate") live.push(url.pathname);
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  const text = sel => page.locator(sel).textContent();
  const state = async () => ({ local: await text("#inv-local"), liveButton: await vis("#inv-go"),
                               sample: await vis("#inv-sample"), offer: await vis("#inv-recorded-offer"),
                               recordedButton: await vis("#inv-recorded-go") });
  const OFF = "The deep check is off on this computer: its model is not running here.";
  const off = () => page.waitForFunction(t => document.getElementById("inv-local").textContent.startsWith(t), OFF);
  const UNSURE = "The deep check is off on this computer just now: Second Look could not confirm its model.";
  const unsure = () => page.waitForFunction(t => document.getElementById("inv-local").textContent.startsWith(t), UNSURE);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  const atLoad = await state();

  // 1. A file with no recording, health held back: the answer shows while the page is still asking.
  holdHealth = true;
  await page.setInputFiles("#file", "__OTHER__");
  await page.waitForFunction(() => !document.getElementById("result").hasAttribute("aria-busy")
                             && document.getElementById("answer").textContent.trim() !== "");
  for (const end = Date.now() + 60000; !held.length && Date.now() < end;) await page.waitForTimeout(50);
  const asking = await state();
  holdHealth = false;
  const heldAsks = held.length;
  held.forEach(r => r());
  await off();
  const answered = await state();
  await page.waitForTimeout(800);
  const settled = await state();
  const answer = await text("#answer");
  if (shots) {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.waitForTimeout(300);
      await page.locator("#investigate").scrollIntoViewIfNeeded();
      await page.locator("#investigate").screenshot({ path: shots + "/deep-check-no-glimmer-" + width + ".png" });
      await page.locator("#receipt").screenshot({ path: shots + "/receipt-panel-" + width + ".png" });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  }

  // 2. The recorded file: the recording is still offered, under its own line.
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const recordedFile = await state();

  // 3. Health refused: no answer reads as no Glimmer, in neutral words (A228), and nothing recorded is offered either.
  failHealth = true;
  await page.setInputFiles("#file", "__OTHER__");
  await unsure();
  await page.waitForTimeout(800);
  const noHealth = await state();
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { atLoad, asking, heldAsks, answered, settled, answer, recordedFile, noHealth, asked, live, offHost,
           real: { glimmer: real.capabilities.glimmer, reason: (real.details.glimmer || {}).reason } };
}
"""

DOWN_TASK = DOWN_TASK.replace("__HEALTH_DOWN__", HEALTH_DOWN.strip())
GLIMMER_DOWN_TASK = GLIMMER_DOWN_TASK.replace("__HEALTH_DOWN__", HEALTH_DOWN.strip())


@pytest.fixture
def page_server(tmp_path, request):
    if not _headless_run():
        pytest.skip("headless-run (chrome-headless-shell) is not installed here")
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    img = _jpeg(gallery / "recorded.jpg")
    other = _jpeg(tmp_path / "other.jpg", (160, 120, 90))
    rec_dir = tmp_path / "rec"
    rec = _record_for(img)
    if getattr(request, "param", None) == "captioned":   # the Pentagon recording, re-keyed to this file's bytes
        rec = json.loads(PENTAGON.read_text())
        rec["sample"].update(file=img.name, sha256=hashlib.sha256(img.read_bytes()).hexdigest())
    _write(rec_dir, "r.json", _hostile(rec) if getattr(request, "param", None) == "hostile" else rec)
    sample = {"id": "02", "file": img.name, "title": "Recorded test image", "kind": "Test", "credit": "Test",
              "licence": "CC BY 4.0", "source": "test", "sha256": hashlib.sha256(img.read_bytes()).hexdigest()}
    port, dead = _free_port(), _free_port()   # nothing listens on `dead`: Glimmer is down
    script = tmp_path / "server.py"
    script.write_text(SERVER)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "SECONDLOOK_GLIMMER_URL": f"http://127.0.0.1:{dead}"}
    log = open(tmp_path / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, str(script), str(port), str(rec_dir), str(gallery), json.dumps(sample)],
                            cwd=ROOT, env=env,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        end = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > end:
                    pytest.fail("server did not start: " + (tmp_path / "server.log").read_text()[-2000:])
                time.sleep(0.2)
        yield port, img, other
    finally:
        os.killpg(proc.pid, signal.SIGTERM)  # the server's own process group, started above
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(10)
        log.close()


def _run(task_text: str, job: str, tmp_path: Path) -> dict:
    task = tmp_path / f"{job}.mjs"
    task.write_text(task_text)
    end = time.monotonic() + 180
    while True:
        run = subprocess.run([_headless_run(), "--job", job, "--owner-task", "DAT-850", "--task", str(task),
                              "--timeout", "150", "--navigation-timeout", "60000"],
                             capture_output=True, text=True, timeout=210)
        lines = [ln for ln in run.stdout.splitlines() if ln.startswith("{")]
        receipt = json.loads(lines[-1]) if lines else None
        said = run.stderr + str((receipt or {}).get("error"))
        if "capacity" in said and "busy" in said and time.monotonic() < end:
            time.sleep(5)  # both host browser slots are taken by other jobs
            continue
        break
    if "capacity" in said and "busy" in said:
        pytest.skip("the host's headless browser slots stayed busy for 3 minutes")
    if not (receipt and receipt["status"] == "completed"):
        found = re.search(r"receipt: (\S+)receipt\.json", run.stderr)
        log = Path(found.group(1) if found else "/nonexistent") / "task.log"
        pytest.fail(f"headless-run: {receipt and receipt.get('error')}\n{run.stderr[-1500:]}\n"
                    + (log.read_text()[-2500:] if log.is_file() else ""))
    return receipt["result"]


def test_page_offers_the_recording_only_for_its_file_when_glimmer_is_down(page_server, tmp_path):
    port, img, other = page_server
    task = DOWN_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img)).replace("__OTHER__", str(other))
    r = _run(task, "secondlook-recorded-down", tmp_path)
    label = recorded.label(_record_for(img))
    assert r["offHost"] == [] and r["live"] == [], "no live run, and nothing leaves 127.0.0.1"
    assert r["offered"]["liveButton"] is False and r["offered"]["button"] == "Show the recorded run (not live)"
    assert "No Muse Glimmer server is running here now" in r["offered"]["offer"]
    assert "(the same sha256 fingerprint)" in r["offered"]["offer"]
    # S3: while the offer shows, the line above it no longer says Glimmer runs here. Before any answer the page
    # says it is looking, and for a file with no recording it says Glimmer is not running here (persona test, 30 Oct).
    assert r["offered"]["local"].startswith("Muse Glimmer is not running on this computer now, so nothing is sent")
    assert r["localLive"].strip() == "Looking for Muse Glimmer on this computer."
    assert r["localAfter"].startswith("The deep check is off on this computer: its model is not running here.")
    assert r["shown"]["firstId"] == "inv-recorded-label" and r["shown"]["label"] == label, "label first"
    assert r["shown"]["focused"] == "inv-recorded-label"
    assert r["shown"]["invAnswer"].startswith("Answer in the recorded run, from Second Look's fixed rules: ")
    assert "not live" in r["shown"]["status"] and "does not change the answer above" in r["shown"]["status"]
    assert r["shown"]["setup"] is True, "no setup box between the status and the label"
    assert "recorded with Muse-Glimmer-30B" in r["shown"]["meta"] and r["shown"]["steps"] >= 1
    # S2: the eyebrow and the start of the footnote say it too, for when the label has scrolled away.
    assert r["shown"]["eyebrow"] == "Desk note · recorded run, not live"
    assert r["shown"]["meta"].startswith("Recorded run, not live · Model: ")
    assert r["answerBefore"] == r["answerAfter"], "the recording did not change the answer"
    # A recorded run is never a server run (DAT-850): the receipt after it sends no run id and has no picks.
    assert r["receipt"]["asked"] == [{"deepRun": False}] and r["receipt"]["status"] == 200
    assert r["receipt"]["picks"] is None and r["receipt"]["pickedLine"] is False
    assert "picked claim words" not in r["receipt"]["said"]
    assert len(r["asked"]) == 2, r["asked"]
    assert r["other"] == {"offer": False, "button": False, "liveButton": False, "label": False}, \
        "Glimmer is down: no live button for a file with no recording"


def test_page_runs_live_as_before_when_glimmer_is_up(page_server, tmp_path):
    port, img, _ = page_server
    live = {**_record_for(img)["note"], "summary": "live body"}
    task = (UP_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__LIVE__", json.dumps(live)))
    r = _run(task, "secondlook-recorded-up", tmp_path)
    assert r["offHost"] == []
    assert r["before"] == {"offer": False, "button": False, "liveButton": True}
    assert r["asked"] == [], "no recording is asked for while Glimmer is up"
    assert r["live"] == ["/api/investigate"] and r["label"] is False
    assert r["invAnswer"].startswith("Answer from Second Look's fixed rules")
    assert r["status"].startswith("Deep check done in ")
    assert r["eyebrow"] == "Desk note" and r["meta"].startswith("Model: ")
    assert r["local"].strip().startswith("Muse Glimmer runs on this computer.")


def test_page_offers_the_recording_for_a_gallery_sample(page_server, tmp_path):
    """The README's route: pick the sample under "Try a sample", then press the button."""
    port, img, _ = page_server
    r = _run(SAMPLE_TASK.replace("__PORT__", str(port)), "secondlook-recorded-sample", tmp_path)
    assert r["offHost"] == [] and r["live"] == []
    assert r["asked"] == ["/api/samples/02/recorded"], "the sample route, not the upload route"
    assert r["offered"]["liveButton"] is False and r["offered"]["buttonHeight"] >= 44, "S1: a 44 px target"
    assert r["offered"]["offerWeight"] == "600", "N3"
    assert r["offered"]["local"].startswith("Muse Glimmer is not running on this computer now")
    assert r["shown"]["label"] == recorded.label(_record_for(img)) and r["shown"]["focused"] == "inv-recorded-label"
    assert r["shown"]["scrollMargin"] == "16px" and r["shown"]["labelTop"] >= 0, "N1"
    assert r["shown"]["eyebrow"] == "Desk note · recorded run, not live"
    assert r["shown"]["meta"].startswith("Recorded run, not live · Model: ") and r["shown"]["steps"] >= 1
    assert r["answerBefore"] == r["answerAfter"]


def test_page_sends_the_caption_and_drops_a_late_recording(page_server, tmp_path):
    port, img, other = page_server
    task = (CAPTION_RACE_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__OTHER__", str(other)))
    r = _run(task, "secondlook-recorded-race", tmp_path)
    assert r["offHost"] == []
    assert r["captioned"]["asked"] == [{"caption": True, "status": 204}], "the caption went with the file"
    assert r["captioned"] == {**r["captioned"], "offer": False, "button": False, "liveButton": False}
    # The held answer was a recording (200) and came after the newer check: it is dropped.
    assert r["raced"]["held"] == [200] and r["raced"]["lateDone"] is True
    assert {"caption": False, "status": 204} in r["raced"]["asked"], "the newer file was asked about too"
    assert (r["raced"]["offer"], r["raced"]["button"], r["raced"]["label"], r["raced"]["liveButton"]) == \
        (False, False, False, False)
    assert r["raced"]["local"].startswith("The deep check is off on this computer: its model is not running")


TRIM_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [], sent = [];
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/recorded") {
      const body = (req.postDataBuffer() || Buffer.alloc(0)).toString("utf8");
      const m = /name="caption"\r\n\r\n([\s\S]*?)\r\n--/.exec(body);
      sent.push(m ? m[1] : null);
    }
    return route.continue();
  });
  const until = async (ok) => { const end = Date.now() + 15000; while (!ok() && Date.now() < end) await page.waitForTimeout(100); };
  const out = [];
  for (const value of __VALUES__) {
    await page.goto(base + "/");
    await page.fill("#caption", value);
    const count = await page.locator("#caption-count").textContent();
    const before = sent.length;
    await page.setInputFiles("#file", "__RECORDED__");
    await until(() => sent.length > before);
    out.push({ count, sent: sent.length > before ? sent[sent.length - 1] : "(nothing asked)" });
  }
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { out, offHost };
}
"""

# Every character Python's str.strip removes. The page trims the same set (a reviewer, PR #48 note 4): JS trim()
# would also drop U+FEFF and keep U+001C to U+001F and U+0085.
PY_SPACE = "".join(chr(c) for c in range(0x110000) if chr(c).isspace())


def test_page_trims_a_caption_as_the_server_does(page_server, tmp_path):
    port, img, other = page_server
    assert "\x85" in PY_SPACE and "\x1c" in PY_SPACE and "\ufeff" not in PY_SPACE
    values = [PY_SPACE + "ab" + PY_SPACE, "ab\ufeff", "\x85\x1c\x1f"]
    task = (TRIM_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__VALUES__", json.dumps(values)))
    r = _run(task, "secondlook-caption-trim", tmp_path)
    assert r["offHost"] == []
    # The caption the page counts and sends is Python's strip of what was typed; a blank one is not sent at all.
    assert r["out"] == [{"count": "2 of 2,000 characters.", "sent": "ab"},
                        {"count": "3 of 2,000 characters.", "sent": "ab\ufeff"},
                        {"count": "Up to 2,000 characters.", "sent": None}]
    assert [recorded.caption_sha256(v) for v in values[:2]] == [recorded.caption_sha256(o["sent"]) for o in r["out"][:2]]


@pytest.mark.parametrize("page_server", ["hostile"], indirect=True)
def test_page_shows_hostile_markup_in_a_recording_as_text(page_server, tmp_path):
    port, img, _ = page_server
    r = _run(HOSTILE_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img)),
             "secondlook-recorded-hostile", tmp_path)
    assert r["pwned"] is None and r["parsed"] == 0 and r["fetched"] == [] and r["offHost"] == [], "nothing parsed or run"
    for where in ("label", "steps", "reading", "cards", "meta", "explain"):
        assert HOSTILE in r[where], where


@pytest.mark.parametrize("page_server", ["captioned"], indirect=True)
def test_page_shows_the_captioned_recording_with_its_pick_and_fact_check_for_its_caption_only(page_server, tmp_path):
    """Mock judging v2 item 1: with no model and no index here, the hero case's recording shows the caption, the
    clause Glimmer picked (as its pick), the fact-check strip with its CC BY credit and "Can't tell", under the
    "Recorded run, not live" label, and only for the same file and caption. The receipt after it has no picks."""
    port, img, _ = page_server
    task = (CAPTIONED_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__CAPTION__", json.dumps(PENTAGON_CAPTION)))
    r = _run(task, "secondlook-recorded-captioned", tmp_path)
    rec = json.loads(PENTAGON.read_text())
    label = recorded.label(rec)
    assert r["offHost"] == [] and r["live"] == [], "no live run, and nothing leaves 127.0.0.1"
    assert r["answer"].startswith("Can't tell")
    assert r["shown"]["answerAfter"] == r["answer"], "the recording did not change the answer"
    assert r["stripBefore"] is False, "no strip before the recorded run is opened: this page has no index"
    s = r["shown"]
    assert s["label"] == label and s["label"].startswith("Recorded run, not live.")
    assert s["captionVisible"] and s["caption"] == f"Caption in the recorded run, in full: \u201c{PENTAGON_CAPTION}\u201d"
    assert s["invAnswer"] == "Answer in the recorded run, from Second Look's fixed rules: Can't tell"
    assert (f"Muse Glimmer's pick from the caption, searched in published fact-checks: \u201c{PENTAGON_PICK}\u201d"
            in s["reading"]) and s["readingTitle"] == "Model readings (model reading, not evidence)"
    assert s["strip"] and s["fcTitle"] == "Published fact-check found"
    assert s["fcRecorded"] == label + " This fact-check was found in that run."
    assert s["fcLine"].startswith("\u201cPants on Fire\u201d") and "politifact.com" in s["fcLine"]
    assert "(CC BY)" in s["fcCredit"] and s["fcCredit"].endswith("This one: politifact.com.")
    for width, v in r["views"].items():
        # S1: the click lands on the strip, with focus on its title, and the strip opens with the recording's label,
        # so "Recorded run, not live" is the first line in view (the strip's 16 px scroll margin above it).
        assert v["focused"] == "fc-title", width
        assert v["firstInStrip"] == "fc-recorded" and 0 <= v["stripTop"] <= 24 and v["bandTop"] >= 0, (width, v)
        # S2: the strip names the words Glimmer searched with that found the match (step 4, lookup_claim.found),
        # as its pick, in the same view; its other pick found nothing and is not named here.
        assert v["pick"] == f"Searched with the words Muse Glimmer picked from the caption: \u201c{PENTAGON_PICK}\u201d"
        assert v["pickBottom"] <= v["height"], (width, v)
        # N1: the link is a 44 px target on its own line.
        assert v["link"] == {"height": 44, "text": "Read it at politifact.com"}, (width, v)
        # N2: the note does not repeat the strip as a card with its raw URL; it says where the fact-check is.
        assert v["cardsShown"] and "politifact.com/factchecks" not in v["cards"] and "Pants on Fire" not in v["cards"]
        assert "shown in the strip under the answer" in v["cards"]
        assert v["overflowX"] is False, width
    # A recorded run is never a server run: the receipt after it sends no run id and has no picks.
    assert r["receipt"] == {"asked": [{"deepRun": False}], "status": 200, "picks": None, "pickedLine": False}
    # Another caption, or none: the server has no recording for that check, and the page offers none.
    assert r["others"] == [{"status": 204, "offer": False, "button": False, "liveButton": False, "strip": False}] * 2


def test_page_says_glimmer_is_not_running_and_shows_no_live_button_when_glimmer_is_down(page_server, tmp_path):
    """Judge persona test, 30 Oct (DAT-850): the deep-check box takes its line and its live button from /api/health's
    capabilities.glimmer, not from whether a recording matches. SECONDLOOK_SHOTS_DIR also saves the box at 1280
    and 390 px."""
    port, img, other = page_server
    shots = os.environ.get("SECONDLOOK_SHOTS_DIR", "")
    task = (GLIMMER_DOWN_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__OTHER__", str(other)).replace("__SHOTS__", json.dumps(shots)))
    r = _run(task, "secondlook-glimmer-down", tmp_path)
    assert r["offHost"] == [] and r["live"] == [], "no live run, and nothing leaves 127.0.0.1"
    # The real server, with nothing on its Glimmer port, never says Glimmer is up (not_running, or unavailable when
    # its probe misses the 1.5 s deadline); the page's health answer is the task's, not_running (A241).
    assert r["real"]["glimmer"] is False and r["real"]["reason"] in ("not_running", "unavailable"), r["real"]
    assert r["heldAsks"] == 1, "the state before health answers was read while the page's health ask was held"
    off = ("The deep check is off on this computer: its model is not running here. The answer above comes from the "
           "file checks alone.")
    nothing = {"liveButton": False, "sample": False, "offer": False, "recordedButton": False}
    assert r["atLoad"] == {"local": "Looking for Muse Glimmer on this computer.", **nothing}
    assert r["answer"].strip(), "the answer was on screen"
    # Before health answers: no claim that Glimmer runs here, and no button to press.
    assert r["asking"] == {"local": "Looking for Muse Glimmer on this computer.", **nothing}
    # After: it says Glimmer is not running, and still no live button, then or later.
    assert r["answered"] == {"local": off, **nothing} and r["settled"] == r["answered"]
    assert "runs on this computer" not in r["settled"]["local"]
    # A recorded file still gets its offer, with the recorded line.
    assert r["recordedFile"]["recordedButton"] is True and r["recordedFile"]["liveButton"] is False
    assert r["recordedFile"]["local"].startswith("Muse Glimmer is not running on this computer now, so nothing is sent")
    # No answer from health: fail closed, with no button, and the neutral line, as health never said not running.
    assert r["noHealth"] == {"local": UNSURE, **nothing}
    assert r["asked"] == ["/api/recorded", "/api/recorded"], "asked for a recording only after health answered"
    if shots:
        for width in (1280, 390):
            assert (Path(shots) / f"deep-check-no-glimmer-{width}.png").stat().st_size > 0


# A228 (DAT-850). a reviewer on PR #48: Glimmer up for one file and down for the next relied on reset() to hide the
# live button, and no test pinned it. a reviewer also: the box said "not running" for every no from health, and
# its health ask had no time limit. Health here is the real server's (nothing listens on its Glimmer port, so
# not_running), changed per step by the route: "up" says Glimmer answers, "unavailable" says it is unavailable for
# another reason, "refused" fails the request, and "hang" holds it until the end of the task.
GLIMMER_SWITCH_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const shots = __SHOTS__;
  const offHost = [], asked = [], live = [], hung = [];
  let mode = "up";
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") {
      if (mode === "refused") return route.abort();
      if (mode === "hang") { await new Promise(r => hung.push(r)); return route.abort().catch(() => {}); }
      const res = await route.fetch();
      const h = await res.json();
      if (mode === "up") {
        h.capabilities.glimmer = true;
        h.details.glimmer = { ready: true, model: "muse-glimmer", vision: true };
      }
      if (mode === "unavailable") h.details.glimmer = { ready: false, reason: "unavailable" };
      return route.fulfill({ response: res, json: h });
    }
    if (/recorded$/.test(url.pathname)) asked.push(url.pathname);
    if (url.pathname === "/api/investigate") live.push(url.pathname);
    return route.continue();
  });
  const vis = sel => page.locator(sel).isVisible();
  const text = sel => page.locator(sel).textContent();
  const state = async () => ({ local: await text("#inv-local"), liveButton: await vis("#inv-go"),
                               sample: await vis("#inv-sample"), offer: await vis("#inv-recorded-offer"),
                               recordedButton: await vis("#inv-recorded-go") });
  const says = t => page.waitForFunction(t => document.getElementById("inv-local").textContent.startsWith(t), t,
                                         { timeout: 30000 });
  const answered = () => page.waitForFunction(() => !document.getElementById("result").hasAttribute("aria-busy")
                                              && document.getElementById("answer").textContent.trim() !== "");
  const OFF = "The deep check is off on this computer: its model is not running here.";
  const UNSURE = "The deep check is off on this computer just now";
  const shoot = async name => {
    if (!shots) return;
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.waitForTimeout(300);
      await page.locator("#investigate").scrollIntoViewIfNeeded();
      await page.locator("#investigate").screenshot({ path: shots + "/" + name + "-" + width + ".png" });
    }
    await page.setViewportSize({ width: 1280, height: 900 });
  };
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");

  // 1. Glimmer up for file A: the live button shows.
  await page.setInputFiles("#file", "__A__");
  await page.locator("#inv-go").waitFor({ state: "visible" });
  const up = await state();
  // 2. Glimmer down for file B (no recording): the button from file A is gone, and the line says not running.
  mode = "down";
  await page.setInputFiles("#file", "__B__");
  await answered();
  await says(OFF);
  await page.waitForTimeout(500);
  const down = await state();
  // 3. Health says unavailable for another reason: the neutral line, no button.
  mode = "unavailable";
  await page.setInputFiles("#file", "__A__");
  await says(UNSURE);
  await page.waitForTimeout(500);
  const unavailable = await state();
  await shoot("deep-check-glimmer-unconfirmed");
  // 4. The recorded file, health unavailable: the offer and the line above it are neutral too.
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const recordedUnsure = { ...(await state()), offerText: (await text("#inv-recorded-offer")).trim() };
  await shoot("deep-check-recorded-unconfirmed");
  // 5. The same file, health not_running: the offer says no server is running, as index.html has it.
  mode = "down";
  await page.setInputFiles("#file", "__B__");
  await says(OFF);
  await page.setInputFiles("#file", "__RECORDED__");
  await page.locator("#inv-recorded-go").waitFor({ state: "visible" });
  const recordedOff = { ...(await state()), offerText: (await text("#inv-recorded-offer")).trim() };
  // 6. Health refused: neutral, no button.
  mode = "refused";
  await page.setInputFiles("#file", "__B__");
  await says(UNSURE);
  const refused = await state();
  // 7. Health hangs: the page asks, says it is looking, and gives up at its 6 s limit with the neutral line.
  mode = "hang";
  await page.setInputFiles("#file", "__A__");
  await answered();
  const t0 = Date.now();
  await page.waitForTimeout(1000);
  const looking = await state();
  await says(UNSURE);
  const gaveUpAfterMs = Date.now() - t0 + 1000;
  const hangSettled = await state();
  const heldAsks = hung.length;
  hung.forEach(r => r());
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { up, down, unavailable, recordedUnsure, recordedOff, refused, looking, gaveUpAfterMs, hangSettled,
           heldAsks, asked, live, offHost };
}
"""


def test_page_follows_health_from_file_to_file_and_says_not_running_only_when_health_does(page_server, tmp_path):
    """Up for file A, then down for file B: the button goes (mutation: no `go.hidden = true` in reset() fails here).
    "Not running" only for health's not_running; unavailable, a refused request or one past 6 s get the neutral
    line. SECONDLOOK_SHOTS_DIR also saves the neutral box and the neutral recorded offer at 1280 and 390 px."""
    port, img, other = page_server
    b = _jpeg(tmp_path / "file-b.jpg", (60, 160, 90))
    shots = os.environ.get("SECONDLOOK_SHOTS_DIR", "")
    task = (GLIMMER_SWITCH_TASK.replace("__PORT__", str(port)).replace("__RECORDED__", str(img))
            .replace("__A__", str(other)).replace("__B__", str(b)).replace("__SHOTS__", json.dumps(shots)))
    r = _run(task, "secondlook-glimmer-switch", tmp_path)
    assert r["offHost"] == [] and r["live"] == [], "nothing pressed, and nothing leaves 127.0.0.1"
    nothing = {"liveButton": False, "sample": False, "offer": False, "recordedButton": False}
    assert r["up"]["liveButton"] is True and r["up"]["local"].startswith("Muse Glimmer runs on this computer.")
    off = ("The deep check is off on this computer: its model is not running here. The answer above comes from the "
           "file checks alone.")
    assert r["down"] == {"local": off, **nothing}, "file A's live button is hidden for file B"
    assert r["unavailable"] == {"local": UNSURE, **nothing}
    assert "not running" not in r["unavailable"]["local"]
    # The recorded file: the offer and the line say not running only when health says not_running.
    assert r["recordedUnsure"]["recordedButton"] is True and r["recordedUnsure"]["liveButton"] is False
    assert r["recordedUnsure"]["local"] == ("Second Look could not confirm Muse Glimmer on this computer now, so "
                                            "nothing is sent to it. A file you drop is compared with our recordings "
                                            "by sha256 here, on this computer.")
    assert r["recordedUnsure"]["offerText"] == ("Second Look could not confirm Muse Glimmer here now, so the deep check "
                                                "cannot run live. We recorded one on this exact file (the same sha256 "
                                                "fingerprint), and you can read it here.")
    assert r["recordedOff"]["local"].startswith("Muse Glimmer is not running on this computer now, so nothing is sent")
    assert r["recordedOff"]["offerText"].startswith("No Muse Glimmer server is running here now, so the deep check")
    assert r["refused"] == {"local": UNSURE, **nothing}
    # A hung health request: still looking after 1 s, then the neutral line at the 6 s limit, never a button.
    assert r["looking"] == {"local": "Looking for Muse Glimmer on this computer.", **nothing}
    assert r["hangSettled"] == {"local": UNSURE, **nothing} and r["heldAsks"] == 1
    assert r["gaveUpAfterMs"] >= 5500, r["gaveUpAfterMs"]
    # A recording is asked for only when health answered no (steps 2 to 5: five files), never while Glimmer is up,
    # and not when health gave no answer (steps 6 and 7), as before.
    assert r["asked"] == ["/api/recorded"] * 5, r["asked"]
    if shots:
        for name in ("deep-check-glimmer-unconfirmed", "deep-check-recorded-unconfirmed"):
            for width in (1280, 390):
                assert (Path(shots) / f"{name}-{width}.png").stat().st_size > 0


def test_the_deep_check_health_ask_has_the_same_6_s_limit_as_app_js():
    app = (STATIC / "app.js").read_text()
    assert "setTimeout(() => stop.abort(), 6000)" in app
    assert "const HEALTH_MS = 6000;" in INV and "setTimeout(() => stop.abort(), HEALTH_MS)" in INV
    assert 'fetch("/api/health", { signal: stop.signal })' in INV


def test_no_recording_is_an_empty_answer_not_a_404(setup, tmp_path):
    """A249 (DAT-850, judge re-run 30 Oct, new snag 1): every drop of a file with no recording logged a red 404 in
    the browser console. No recording is now 204 with no body, on both routes; an unknown sample id is still 404,
    and the page reads 204 as nothing to offer before it reads any body."""
    _, _, client = setup
    res = _post(client, _jpeg(tmp_path / "plain.jpg", (9, 9, 9)))
    assert res.status_code == 204 and res.content == b""
    js = INV.split("async function offerRecording", 1)[1].split("function showSetup", 1)[0]
    assert "if (res.status === 204 || !res.ok || id !== runId) return;" in js
    assert js.index("res.status === 204") < js.index("res.json()")


# A249: the console after a drop of a file with no recording (health says Glimmer is not running, so the page asks
# /api/recorded), and after loading /batch. Any console error or any response of 400 and up is collected.
CONSOLE_TASK = r"""
export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const shots = __SHOTS__;
  const offHost = [], errors = [], bad = [], asked = [];
  __HEALTH_DOWN__
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", e => errors.push(String(e)));
  page.on("response", r => { if (r.status() >= 400) bad.push(r.status() + " " + new URL(r.url()).pathname); });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    if (url.pathname === "/api/health") return route.fulfill({ json: down });
    if (url.pathname === "/api/recorded") {
      const res = await route.fetch();
      asked.push(res.status());
      return route.fulfill({ response: res });  // the server's own answer, status and body as sent
    }
    return route.continue();
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(base + "/");
  const icon = await page.evaluate(() => (document.querySelector('link[rel="icon"]') || {}).href || null);
  const favicon = (await page.request.get(base + "/favicon.ico")).status();
  await page.setInputFiles("#file", "__OTHER__");
  await page.waitForFunction(() => document.getElementById("inv-local").textContent.startsWith(
    "The deep check is off on this computer: its model is not running here."));
  for (const end = Date.now() + 30000; !asked.length && Date.now() < end;) await page.waitForTimeout(50);
  await page.waitForTimeout(500);
  const offer = await page.locator("#inv-recorded-offer").isVisible();
  const button = await page.locator("#inv-recorded-go").isVisible();
  if (shots) {
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await page.waitForTimeout(300);
      await page.locator("#investigate").scrollIntoViewIfNeeded();
      await page.screenshot({ path: shots + "/a249-no-recording-" + width + ".png" });
    }
  }
  await page.goto(base + "/batch");
  await page.waitForTimeout(500);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { errors, bad, asked, offer, button, icon, favicon, offHost };
}
"""
CONSOLE_TASK = CONSOLE_TASK.replace("__HEALTH_DOWN__", HEALTH_DOWN.strip())


def test_page_logs_no_console_error_for_a_file_with_no_recording(page_server, tmp_path):
    port, _, other = page_server
    shots = os.environ.get("SECONDLOOK_SHOTS_DIR", "")
    task = (CONSOLE_TASK.replace("__PORT__", str(port)).replace("__OTHER__", str(other))
            .replace("__SHOTS__", json.dumps(shots)))
    r = _run(task, "secondlook-a249-console", tmp_path)
    assert r["offHost"] == []
    assert r["errors"] == [] and r["bad"] == [], (r["errors"], r["bad"])
    assert r["asked"] == [204], "the page asked for a recording, and none is not an error"
    assert (r["offer"], r["button"]) == (False, False), "nothing offered for a file with no recording"
    assert r["icon"] == f"http://127.0.0.1:{port}/favicon.ico" and r["favicon"] == 200
