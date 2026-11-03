"""Record Muse Glimmer deep checks of demo samples, so a judge with no model installed can see one (DAT-850).

For each sample named, this runs the investigator exactly as the page's POST /api/investigate does (the
installed rungs, no caption) against the running Glimmer server, and writes samples/recorded/<id>.json:
the response body the page renders, plus the sample's sha256, the model and projector files' sha256, the
llama.cpp build, the git commit (HEAD at the start, read again just before saving, and refused if it moved),
the machine, the date (UTC) and the wall-clock seconds (after one untimed warm-up of the file checks, as the
page warms them before it says Ready). The page offers a
recording only for a file with the same sha256, and only when no Glimmer server answers
(secondlook/web/recorded.py). Nothing here is evidence: the answer in a recording is still the ladder's.

Only samples we may republish a reading of: CC BY, public domain, or our own changed copies of those. 01, 05
and 06 are CC BY-SA and are refused.

One captioned recording (DAT-850, mock judging v2 item 1): --file names a file of ours outside the catalogue and
--caption-file its caption. The deep check runs with that caption, as the page's does when a caption is typed,
against the claims index SECONDLOOK_CLAIMS_DB names. The recording stores the caption, its sha256 (the page is
offered the recording only for the same caption) and the index's feed file: its sha256, the feed's own date and
when it was fetched, since a later feed may give other matches. It names the dated copy of the feed it searched,
never the moving `latest` URL: the index's source if that is a dated copy (`claim --build --source`, as judges' step
8 builds it), or --feed-url. Either way the copy's folder must be the feed's own dateModified, to the second.

A fact-check card's `rating_explanation` (the publisher's article text) is not stored: a recording keeps only
what the page's strip shows. Paths under the repo in the note (the classifier card's threshold and gate files) are written repo-relative, so a
recording names no home folder. --resave rewrites committed recordings that way, with --feed-url names the dated
feed copy a captioned one searched (in claims_index and its fact-check cards), and changes nothing else.

A recording's git_commit names the exact, clean code that produced it. The script parses its arguments, then
reads secondlook/'s state and HEAD and refuses there, before it imports any of the repo's code, unless secondlook/
is committed and git says which commit HEAD is. It then writes `git archive` of that commit's secondlook/ to a
temp folder and imports secondlook from there, never from the working tree, so the bytes that run are the
committed ones: an edit made and undone during a run cannot reach it (Codex, PR #48 @ 6783d83; DAT-850 A228). The
folder also gets the working tree's git-ignored data under secondlook/ (the fetched interim trust list), never a
Python file, and is removed when the script exits, SIGTERM and SIGHUP included. It refuses, before it makes the folder,
if any secondlook module is already imported, since Python would reuse that module rather than the snapshot's (Codex,
PR #51 @ 5f7b182). It reads secondlook/'s state and HEAD again at the start of main() and just before saving, and
refuses unless the tree is still clean at the same commit. --resave reads no git
state and makes no snapshot: it keeps each recording's git_commit as it was.

    scripts/glimmer_server.sh status        # use a running server, or start one
    .venv/bin/python scripts/record_investigations.py --samples samples 02 04 08
    SECONDLOOK_CLAIMS_DB=<index> .venv/bin/python scripts/record_investigations.py \
        --file samples/recorded/pentagon-mock-post.png --caption-file <caption.txt> --id pentagon \
        --licence "<licence>" --credit "<credit>" [--feed-url <dated feed URL>]
    .venv/bin/python scripts/record_investigations.py --resave samples/recorded/*.json [--feed-url <dated feed URL>]
"""
from __future__ import annotations

import argparse
import atexit
import dataclasses
import hashlib
import io
import json
import os
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _git_or_none(*args: str) -> str | None:
    try:
        return run("git", *args)
    except (OSError, subprocess.CalledProcessError):
        return None


COMMIT = re.compile(r"[0-9a-f]{40}")


def committed_tree(when: str) -> str:
    """HEAD's full commit id, read with secondlook/'s state (`when` says which read this is). Refused if secondlook/
    has uncommitted changes or git cannot say, and if git cannot say which commit HEAD is (no checkout, git missing,
    an unborn HEAD): a recording never names an unknown commit (a reviewer, PR #48 note 6) or code that is not
    that commit's (Codex, PR #48 @ 17e3440). Standard library only, as it runs before the repo's imports."""
    status = _git_or_none("status", "--porcelain", "--", "secondlook")
    if status is None or status:
        sys.exit(f"record_investigations: secondlook/ has uncommitted changes {when}, or git cannot say; "
                 "record from a committed tree only")
    sha = _git_or_none("rev-parse", "HEAD") or ""
    if not COMMIT.fullmatch(sha):
        sys.exit(f"record_investigations: cannot tell which commit HEAD is ({sha or 'git rev-parse failed'} {when}); "
                 "record from a committed git checkout")
    return sha


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ids", nargs="*", help="sample ids from secondlook/web/samples.py, e.g. 02 04 08")
    ap.add_argument("--samples", default=str(ROOT / "samples"), help="folder scripts/fetch_samples.sh filled")
    ap.add_argument("--out", help="where to write the recordings (default samples/recorded)")
    ap.add_argument("--file", help="one file of ours outside the catalogue, checked with --caption-file")
    ap.add_argument("--caption-file", help="the caption for --file, as UTF-8 text (trimmed)")
    ap.add_argument("--id", help="the recording's id and file name for --file, e.g. pentagon")
    ap.add_argument("--licence", help="the licence of --file")
    ap.add_argument("--credit", help="the credit of --file")
    ap.add_argument("--feed-url", help="the dated feed copy the claims index was built from, if its source is not")
    ap.add_argument("--resave", nargs="+", metavar="JSON",
                    help="rewrite these recordings with repo-relative paths; reads no git state, as each keeps the "
                         "git_commit it was recorded at")
    return ap.parse_args()


# Python files are code, so a snapshot never takes one from the working tree, ignored or not.
NOT_DATA = re.compile(r"(^|/)__pycache__(/|$)|\.(py|pyc|pyo|pyd|pth|so|dylib)$")


def remove_on_signal(code: Path) -> None:
    """On SIGTERM or SIGHUP, remove the snapshot, then exit non-zero (128 + the signal's number). atexit alone does not
    run when a signal ends the process, so an investigation stopped that way left secondlook-recording-* behind (Codex,
    PR #51 @ 5f7b182). The folder goes first, so it is gone even if something catches the exit."""
    def stop(signum, frame):
        shutil.rmtree(code, True)
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)


def snapshot(sha: str) -> Path:
    """A temp folder holding `git archive` of sha's secondlook/ and the working tree's git-ignored data under it,
    removed when the script exits or is stopped by SIGTERM or SIGHUP. Standard library only, as it runs before the
    repo's imports. Refused if git cannot write the archive."""
    try:
        tar = subprocess.run(["git", "archive", "--format=tar", sha, "--", "secondlook"], cwd=ROOT,
                             capture_output=True, check=True).stdout
        ignored = run("git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", "secondlook")
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"record_investigations: git could not write {sha[:12]}'s secondlook/ to a snapshot ({e}); "
                 "record from a committed git checkout")
    code = Path(tempfile.mkdtemp(prefix="secondlook-recording-")).resolve()
    atexit.register(shutil.rmtree, code, True)
    remove_on_signal(code)
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        archive.extractall(code, filter="data")
    for name in filter(None, ignored.split("\0")):
        if not NOT_DATA.search(name) and not (code / name).exists() and (ROOT / name).is_file():
            (code / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, code / name)
    return code


# The arguments first, then secondlook/'s state and HEAD, refused right here if they are not a clean, known commit:
# the repo's imports below run its module-level code, so a dirty tree is refused before any of it runs (Codex, PR #48
# @ 17e3440), and a checkout during the imports is caught when main() reads HEAD again (@ a1f0ff7). --resave (and
# --help) read no git state. None means this load did no read, so main() records nothing from it.
BEFORE_IMPORTS = None if parse_args().resave else committed_tree("before the imports")
# A secondlook module already in sys.modules (a child such as secondlook.investigator, or a lazily loaded rung, as much
# as the package) would be reused by the imports below, so code the snapshot does not hold would run and be recorded
# under its commit (Codex, PR #51 @ 5f7b182). Refused before the snapshot is made, so nothing is written.
PRELOADED = sorted(m for m in sys.modules if m == "secondlook" or m.startswith("secondlook."))
if BEFORE_IMPORTS is not None and PRELOADED:
    sys.exit(f"record_investigations: {', '.join(PRELOADED)} already imported before the snapshot; run the script in "
             "its own Python process")
# Where secondlook is imported from: a snapshot of that commit, so no edit in the working tree runs (A228); the
# working tree only for --resave and --help, which run no check.
CODE = ROOT if BEFORE_IMPORTS is None else snapshot(BEFORE_IMPORTS)

sys.path.insert(0, str(CODE))

import secondlook  # noqa: E402
from secondlook import glimmer as g  # noqa: E402
from secondlook import investigator as inv  # noqa: E402
from secondlook import ladder  # noqa: E402
from secondlook import rungs as rung_registry  # noqa: E402
from secondlook.rungs import claim as claim_rung  # noqa: E402
from secondlook.web import recorded  # noqa: E402
from secondlook.web import samples as sample_catalogue  # noqa: E402

if Path(secondlook.__file__).resolve().parent != CODE / "secondlook":
    sys.exit(f"record_investigations: secondlook was imported from {Path(secondlook.__file__).parent}, not from "
             f"{CODE / 'secondlook'}; run the script in its own Python process")

SHARE_ALIKE = {"CC BY-SA 4.0"}
FETCH = ROOT / "scripts" / "fetch_glimmer.sh"
# The working tree's samples/recorded/, not the snapshot's (recorded.DEFAULT_DIR is under the code imported).
DEFAULT_OUT = ROOT / "samples" / "recorded"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def machine() -> str:
    """The chip and memory, e.g. "Apple M5 Max (128 GB)"; platform.platform() off macOS."""
    try:
        chip = run("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string")
        mem = int(run("/usr/sbin/sysctl", "-n", "hw.memsize")) // (1 << 30)
        return f"{chip} ({mem} GB)"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return platform.platform()


def server_files(url: str) -> tuple[Path, Path | None, str]:
    """The model file the server loaded (from its /props), the projector file and the llama.cpp build."""
    props = g.GlimmerClient(url)._call("/props")
    model = Path(props["model_path"])
    # llama-server does not report its projector path; glimmer_server.sh names the one it loads.
    mmproj = Path(run("bash", str(ROOT / "scripts" / "glimmer_server.sh"), "paths").split("mmproj:", 1)[1]
                  .splitlines()[0].strip())
    return model, (mmproj if mmproj.is_file() else None), str(props.get("build_info") or "unknown")


def repo_relative(value):
    """value with every string that is a path under the repo, or under the snapshot the code ran from, made
    repo-relative (a home folder is no one's business, and the snapshot plan's grep stops on one); everything else
    as it was."""
    if isinstance(value, dict):
        return {k: repo_relative(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repo_relative(v) for v in value]
    if isinstance(value, str):
        for base in (CODE, ROOT):
            if value.startswith(str(base) + os.sep):
                return Path(value).relative_to(base).as_posix()
    return value


# A fact-check card's raw fields that are the publisher's own article text. A recording keeps only what the page's
# strip shows (the rating phrase, the claim line, the publisher, the link and the CC BY credit), so these are dropped
# (licence check A190, DAT-850).
ARTICLE_TEXT = ("rating_explanation",)


def without_article_text(value):
    """value with every ARTICLE_TEXT key removed, at any depth."""
    if isinstance(value, dict):
        return {k: without_article_text(v) for k, v in value.items() if k not in ARTICLE_TEXT}
    if isinstance(value, list):
        return [without_article_text(v) for v in value]
    return value


# The fact-check feed is published at a moving `latest` URL and as dated copies, one folder per copy, e.g.
# claimreview/20261029-141029/data.json: the file judges' step 8 pins, whose own dateModified is 2026-10-29T14:10:29
# (a reviewer, PR #48 @ a445f6a, item 4). A recording names the dated copy: one file, checked by feed_sha256,
# not whatever `latest` is on the day it is read (PR #48 note 1).
DATED_FEED = re.compile(r"https://storage\.googleapis\.com/datacommons-feeds/claimreview/(\d{8})-(\d{6})/data\.json")
FEED_DATE = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(?:Z|\+00:00)")


def feed_source(index: dict, url: str | None) -> str:
    """The dated feed URL a captioned recording names: url if given, else the index's own source. Refused unless it
    is a dated copy whose folder is the feed's own dateModified (UTC), to the second."""
    src = url or str(index.get("source") or "")
    m, d = DATED_FEED.fullmatch(src), FEED_DATE.fullmatch(str(index.get("feed_date_modified") or ""))
    if not (m and d and m.group(1) + m.group(2) == "".join(d.groups())):
        sys.exit(f"record_investigations: the recording must name the dated feed copy it searched, not {src or 'none'}"
                 f" (feed dateModified {index.get('feed_date_modified') or 'unknown'}); pass --feed-url "
                 "https://storage.googleapis.com/datacommons-feeds/claimreview/<YYYYMMDD-HHMMSS>/data.json")
    return src


def with_feed_source(value, feed_sha256: str, src: str):
    """value with the source of every record of this feed file (claims_index and the fact-check cards' feed, each
    holding feed_sha256) set to src, at any depth; everything else as it was."""
    if isinstance(value, dict):
        out = {k: with_feed_source(v, feed_sha256, src) for k, v in value.items()}
        if out.get("feed_sha256") == feed_sha256 and "source" in out:
            out["source"] = src
        return out
    if isinstance(value, list):
        return [with_feed_source(v, feed_sha256, src) for v in value]
    return value


def claims_index() -> dict:
    """The fact-check index a captioned deep check searched: its file's sha256 and the feed it was built from."""
    db = Path(os.environ.get(claim_rung.DB_ENV) or claim_rung.DEFAULT_DB)
    if not db.is_file():
        sys.exit(f"record_investigations: a captioned recording needs the claims index; {db} is not there "
                 f"(set {claim_rung.DB_ENV})")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()
    keep = ("source", "licence", "feed_date_modified", "downloaded_at", "feed_sha256", "rows")
    return {"file_sha256": sha256(db), **{k: meta.get(k, "") for k in keep}}


def resave(paths: list[str], feed_url: str | None = None) -> int:
    """Rewrite committed recordings with repo-relative paths, and a captioned one with the dated feed copy it
    searched (feed_source). Nothing else changes, and each must still validate."""
    for name in paths:
        path = Path(name)
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["note"] = repo_relative(rec["note"])
        if rec.get("claims_index"):
            rec = with_feed_source(rec, rec["claims_index"]["feed_sha256"], feed_source(rec["claims_index"], feed_url))
        recorded.validate(rec)
        path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        print(f"{path.name}: re-saved with repo-relative paths"
              + (f", feed {rec['claims_index']['source']}" if rec.get("claims_index") else ""), flush=True)
    return 0


def body_of(note, version: str, rung_names: list[str]) -> dict:
    """The body POST /api/investigate returns for this note (secondlook/web/app.py), so the page renders it as is.
    Its version is the commit the snapshot was archived from, whatever the ladder read: in the snapshot (not a git
    repo) ladder._version() says "unknown", or an enclosing repo's HEAD if the temp folder sits inside one (A241)."""
    body = dataclasses.asdict(note)
    body["version"] = version
    body["engine"] = "ladder"
    body["rungs"] = rung_names
    body["demo"] = any(e.get("raw", {}).get("stub") for e in body["evidence"])
    body["glimmer"] = "unavailable" if any(str(p).startswith(inv.UNAVAILABLE) for p in body["problems"]) else "ran"
    # A recorded run is never a server run: no server kept its picked clauses, so a receipt never takes picks
    # from it (the project lead, DAT-850 PR #44). The live body's run id is null here, always.
    body["receipt_run"] = None
    return body


def main() -> int:
    a = parse_args()
    if a.resave:
        return resave(a.resave, a.feed_url)

    commit = committed_tree("at the start")
    if commit != BEFORE_IMPORTS:
        sys.exit(f"record_investigations: HEAD moved while the script started "
                 f"({(BEFORE_IMPORTS or 'no read')[:12]} before the imports, {commit[:12]} after); "
                 "the code loaded is not the commit a recording would name")
    # (id, Sample, path, caption): the catalogue samples, then the one file of ours with its caption.
    jobs: list = []
    if a.file:
        if a.ids:
            sys.exit("record_investigations: --file records one file; name no sample ids with it")
        if not (a.caption_file and a.id and a.licence and a.credit):
            sys.exit("record_investigations: --file needs --caption-file, --id, --licence and --credit")
        if re.search(r"\bBY-SA\b|share.?alike", a.licence, re.I):
            sys.exit(f"record_investigations: {a.licence}; record CC BY, public domain or our own files only")
        path = Path(a.file).resolve()
        caption = Path(a.caption_file).read_text(encoding="utf-8").strip()
        if not path.is_file() or not caption or len(caption) > recorded.MAX_CAPTION:
            sys.exit("record_investigations: --file must exist and its caption must be 1 to 2,000 characters")
        s = sample_catalogue.Sample(a.id, path.name, "", "", a.credit, a.licence, "", sha256(path))
        jobs.append((a.id, s, path, caption))
    elif not a.ids:
        sys.exit("record_investigations: name sample ids, or --file with its caption, or --resave")
    gallery = sample_catalogue.available(a.samples) if a.ids else {}
    for i in a.ids:
        if i not in gallery:
            sys.exit(f"record_investigations: sample {i} is not in {a.samples} with its pinned sha256")
        if gallery[i][0].licence in SHARE_ALIKE:
            sys.exit(f"record_investigations: sample {i} is {gallery[i][0].licence}; record CC BY, public "
                     "domain or our own files only")
        jobs.append((i, *gallery[i], None))
    index = claims_index() if any(c for *_, c in jobs) else None
    if index:   # checked before the model runs: a recording names the dated feed copy, never `latest`
        index["source"] = feed_source(index, a.feed_url)

    client = g.GlimmerClient(reasoning="low")
    client.check()      # stops here, with the reason, if no muse-glimmer server answers
    model, mmproj, build = server_files(client.url)
    print(f"model {model.name}: hashing", flush=True)
    model_sha = sha256(model)
    mmproj_sha = sha256(mmproj) if mmproj else None
    loaded = rung_registry.load()
    rung_names = [name for name, _ in loaded + rung_registry.load(rung_registry.CONTEXT_RUNGS)]
    out = Path(a.out or DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)
    # The page loads its checks' weights before it says Ready (scripts/demo.sh warms up on a sample), so a
    # click there does not pay that load. Do the same once here, untimed, so the seconds match a click.
    print("warming the file checks (untimed)", flush=True)
    ladder.run(str(jobs[0][2]), rungs=loaded)

    for i, s, path, caption in jobs:
        date = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        t0 = time.monotonic()
        note = inv.investigate(str(path), caption, client, rungs=loaded)
        seconds = round(time.monotonic() - t0, 1)
        body = without_article_text(repo_relative(body_of(note, commit, rung_names)))
        if caption:
            body = with_feed_source(body, index["feed_sha256"], index["source"])
        if body["glimmer"] != "ran":
            sys.exit(f"record_investigations: Glimmer did not run on {s.file}: {body['problems']}")
        rec = {
            "schema": recorded.SCHEMA,
            "sample": {"id": s.id, "file": s.file, "sha256": sha256(path), "licence": s.licence, "credit": s.credit},
            "caption": caption,
            "caption_sha256": recorded.caption_sha256(caption),
            "model": {"name": recorded.MODEL_NAME, "served_as": g.MODEL, "file": model.name, "sha256": model_sha,
                      "projector_file": mmproj.name if mmproj else None, "projector_sha256": mmproj_sha},
            "llama_cpp_build": build,
            "git_commit": commit,
            "machine": machine(),
            "os": platform.platform(),
            "date_utc": date,
            "seconds": seconds,
            "seconds_cover": "the whole deep check, the file checks included; their weights were loaded "
                             "before timing, as the page loads them before it says Ready",
            "note": body,
        }
        if caption:
            rec["claims_index"] = index
        recorded.validate(rec)
        # The code that ran is the snapshot of the starting commit, and the note names that commit (body_of). A
        # commit or an edit to secondlook/ in the working tree during the run would not show in the note, so
        # secondlook/'s state and HEAD are read again here, just before saving: still clean, at the starting commit
        # (Codex, PR #48 @ a445f6a and @ 17e3440). The read the note's version used to give died with the snapshot
        # (a reviewer, PR #51 note 3), and was removed in A241.
        now = committed_tree("just before saving")
        if now != commit:
            sys.exit(f"record_investigations: HEAD moved during the run ({commit[:12]} at the start, "
                     f"{now[:12]} just before saving); record from a tree nobody commits to meanwhile")
        (out / f"{i}.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
        print(f"{s.id} {s.file}: {body['answer']}, {len(body['steps'])} steps, {seconds} s,"
              f" printed lines {len(body['image_text'])}, HEAD {now[:12]} checked before saving", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
