"""A synthetic L4 folder for the gate-run tests: generated images, a fake classifier, a git repo.

Everything is made here, offline. Real-class images are dark 768 x 512 JPEGs with camera EXIF; AI-class
images are bright 512 x 512 PNGs. The fake classifier reads the mean brightness of the file it is
given (so it really scores the arm's own file), plus two sets of item ids that force a flag or a miss.
This proves the wiring, the order of operations and the refusals. It says nothing about any detector.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

from secondlook.types import Evidence

TOPICS = ("landscape", "object", "building", "plant", "animal", "food")
PAST = "2026-10-27T00:00:00Z"
COMMIT_DATE = "2026-10-28T00:00:00Z"


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_csv(path: Path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cols))
        w.writeheader()
        w.writerows(rows)


def make_real(path: Path, idx: int):
    base = Image.linear_gradient("L").resize((768, 512)).point(lambda v: int(v * 0.43))
    im = Image.merge("RGB", (base, base.point(lambda v: v // 2), base.point(lambda v: v // 3)))
    ImageDraw.Draw(im).rectangle([(idx % 30) * 24, (idx // 30 % 19) * 24, (idx % 30) * 24 + 19, (idx // 30 % 19) * 24 + 19],
                                 fill=(idx % 251, (idx * 3) % 251, (idx * 7) % 251))
    ex = Image.Exif()
    ex[0x010F] = "FixtureCam"
    ex[0x0110] = "FX-1"
    im.save(path, "JPEG", quality=92, exif=ex)


def make_ai(path: Path, idx: int):
    base = Image.linear_gradient("L").resize((512, 512)).point(lambda v: 150 + int(v * 0.4))
    im = Image.merge("RGB", (base, base, base.point(lambda v: v - 20)))
    ImageDraw.Draw(im).rectangle([(idx % 25) * 20, (idx // 25 % 25) * 20, (idx % 25) * 20 + 15, (idx // 25 % 25) * 20 + 15],
                                 fill=(idx % 251, (idx * 5) % 251, (idx * 11) % 251))
    im.save(path, "PNG")


def fake_classifier(flag_ids=(), miss_ids=(), calls=None):
    """A rung list [("classifier", check)]. Score 0.9 for a bright file or a forced flag, else 0.1."""
    flag, miss = set(flag_ids), set(miss_ids)

    def check(path):
        if calls is not None:
            calls.append(path)
        stem = Path(path).stem
        with Image.open(path) as im:
            mean = ImageStat.Stat(im.convert("L")).mean[0]
        hi = (mean > 128 or stem in flag) and stem not in miss
        return [Evidence(rung="classifier", finding="fixture score", proves="nothing", does_not_prove="anything",
                         strength="signal" if hi else "none", raw={"score": 0.9 if hi else 0.1, "above_threshold": hi})]
    return [("classifier", check)]


def git(repo: Path, *args, date=COMMIT_DATE):
    env = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date, "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
           "HOME": str(repo)}
    return subprocess.run(["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "-C", str(repo),
                           *args], check=True, capture_output=True, text=True, env=env)


@dataclass
class Fixture:
    root: Path
    l4: Path
    repo: Path
    threshold: Path
    n: dict = field(default_factory=dict)

    def argv(self, *extra, run_id="fx1") -> list[str]:
        return ["--l4-dir", str(self.l4), "--threshold", str(self.threshold), "--synthetic", "--run-id", run_id,
                "--out", str(self.root / "runs" / run_id), *extra]

    def paths(self):
        return {"dev": self.l4 / "splits" / "dev.csv", "test": self.l4 / "splits" / "test.csv"}


def build(root: Path, dev_real=6, dev_ai=6, test_real=6, test_ai=6, reserve=2, rejected=3,
          gate_arms=("original", "format_matched"), training_data="Fixture: none. Generated test images only.",
          commit=True, spare_rows=(), tweak=None, lock_screen=True, protocol_text="fixture protocol\n") -> Fixture:
    """`spare_rows`: extra real-class reserve units nobody uses, as (spare_for, topic, spare_rank) tuples.
    `tweak(d)`: edits d["groups"], d["manifest"] ({label: [rows]}), d["units"] and d["rejections"] in memory
    before any file is written, so the lock, ACCEPTED.json and the ledger all agree with the edit. If any row of a
    class's groups or manifest has a `study` key, that file gets a `study` column (the v2 layout, L4 PROTOCOL.md part K;
    rows without the key get a blank cell). `protocol_text` is the locked PROTOCOL.md."""
    root = Path(root)
    l4, repo = root / "l4", root / "repo"
    for d in (l4 / "splits", l4 / "scripts", l4 / "images" / "original" / "real", l4 / "images" / "original" / "ai", repo):
        d.mkdir(parents=True, exist_ok=True)

    # units: ids, topics, files
    units = {"dev": [], "test": []}
    rows = {"dev": [], "test": []}
    files = {}
    counter = {"real": 0, "ai": 0}         # one running index per class, so no two images are the same file
    for split, nr, na in (("dev", dev_real, dev_ai), ("test", test_real, test_ai)):
        for label, n in (("real", nr), ("ai", na)):
            for i in range(n):
                uid = f"{split}-{label}-{i}"
                p = l4 / "images" / "original" / label / (uid + (".jpg" if label == "real" else ".png"))
                (make_real if label == "real" else make_ai)(p, counter[label])
                counter[label] += 1
                role = "reserve" if (split == "test" and label == "real" and i >= nr - reserve and nr > reserve) else "primary"
                # a reserve's topic follows its order, so the first rejected primaries (topics 0, 1, 2) find a
                # reserve in their topic (spare_rank 1) and the third finds none: the count falls
                topic = TOPICS[(i - (nr - reserve)) % len(TOPICS)] if role == "reserve" else TOPICS[i % len(TOPICS)]
                units[split].append({"unit_id": uid, "label": label, "topic": topic, "sha256": sha(p), "role": role})
                files[uid] = (label, p)

    # the locked L4 files: manifests and groups in the shape of L4 build_groups.py (one row per unit here)
    (l4 / "PROTOCOL.md").write_text(protocol_text)
    (l4 / "scripts" / "fetch.py").write_text("# fixture\n")
    groups = {"real": [], "ai": []}
    man = {"real": [], "ai": []}

    def add_unit(label, uid, split, topic, role, spare_for="", spare_rank=""):
        groups[label].append({"unit_id": uid, "split": "spare" if role == "reserve" else split, "topic": topic,
                              "spare_for": spare_for, "spare_rank": spare_rank})
        man[label].append({"split": "spare" if role == "reserve" else split, "topic": topic, "unit_id": uid,
                           "role": role, "spare_for": spare_for, "spare_rank": spare_rank})

    rank: dict = {}
    for split in ("dev", "test"):
        for u in units[split]:
            if u["role"] == "reserve":
                rank[(split, u["topic"])] = rank.get((split, u["topic"]), 0) + 1
                add_unit(u["label"], u["unit_id"], split, u["topic"], "reserve", split, str(rank[(split, u["topic"])]))
            else:
                add_unit(u["label"], u["unit_id"], split, u["topic"], "primary")
    # rejected primaries still count as planned, as in a real manifest
    rej = []
    for i in range(rejected):
        add_unit("real", f"rej-{i}", "test", TOPICS[i % 6], "primary")
        rej.append({"unit_id": f"rej-{i}", "split": "test", "label": "real", "topic": TOPICS[i % 6],
                    "check": "C3 faces and persons", "reason": "fixture: a face was found"})
    for k, (spare_for, topic, spare_rank) in enumerate(spare_rows):
        add_unit("real", f"spare-{k}", spare_for, topic, "reserve", spare_for, str(spare_rank))
    d = {"groups": groups, "manifest": man, "units": units, "rejections": rej}
    if tweak:
        tweak(d)
    units, rej = d["units"], d["rejections"]
    gcols = ("unit_id", "split", "topic", "spare_for", "spare_rank")
    mcols = ("split", "topic", "unit_id", "role", "spare_for", "spare_rank")
    for name, label in (("manifest-real.csv", "real"), ("manifest-ai.csv", "ai")):
        write_csv(l4 / name, mcols + (("study",) if any("study" in r for r in man[label]) else ()), man[label])
    for name, label in (("groups-real.csv", "real"), ("groups-ai.csv", "ai")):
        write_csv(l4 / name, gcols + (("study",) if any("study" in r for r in groups[label]) else ()), groups[label])
    (l4 / "SCREEN-CONFIG.json").write_text(json.dumps({"schema": "fixture", "phash_distance": 8, "tools": []}))
    locked = ("PROTOCOL.md", "manifest-real.csv", "manifest-ai.csv", "groups-real.csv", "groups-ai.csv", "scripts/fetch.py")
    if lock_screen:
        locked += ("SCREEN-CONFIG.json",)
    (l4 / "LOCK.json").write_text(json.dumps({"sha256": {n: sha(l4 / n) for n in locked}}))

    for split in ("dev", "test"):
        for u in units[split]:
            label, p = files[u["unit_id"]]
            rows[split].append({"item_id": u["unit_id"], "label": label, "path": str(p), "group": u["unit_id"],
                                "sha256": u["sha256"]})

    # splits
    cols = ("item_id", "label", "path", "group", "sha256")
    write_csv(l4 / "splits" / "dev.csv", cols, rows["dev"])
    write_csv(l4 / "splits" / "test.csv", cols, rows["test"])
    (l4 / "splits" / "SPLIT.json").write_text(json.dumps({
        "dev": {"sha256": sha(l4 / "splits" / "dev.csv")}, "test": {"sha256": sha(l4 / "splits" / "test.csv")},
        "params": {"phash_distance": 8}}))

    # ACCEPTED.json, then its hash in LEDGER.md
    acc = {"schema": "second-look-accepted/1", "protocol_sha256": sha(l4 / "PROTOCOL.md"),
           "lock_sha256": sha(l4 / "LOCK.json"), "screen_config_sha256": sha(l4 / "SCREEN-CONFIG.json"),
           "units": units, "rejections": rej}
    (l4 / "ACCEPTED.json").write_text(json.dumps(acc, indent=1))
    (l4 / "LEDGER.md").write_text(f"# ledger\n\nACCEPTED.json sha256 {sha(l4 / 'ACCEPTED.json')}\n")

    # threshold.json, committed in a git repo
    thr = repo / "threshold.json"
    thr.write_text(json.dumps({
        "schema": "second-look-threshold/2", "threshold": 0.5, "ai_if": "score>=threshold", "abstain": {"rule": "none"},
        "classifier": {"name": "fixture-classifier", "version": "0", "weights_sha256": "0" * 64},
        "dev_split_sha256": sha(l4 / "splits" / "dev.csv"), "test_split_sha256": sha(l4 / "splits" / "test.csv"),
        "l4_lock_sha256": sha(l4 / "LOCK.json"), "phash_distance": 8, "gate_arms": list(gate_arms),
        "frozen_at": PAST, "frozen_by": "fixture", "training_data_statement": training_data}, indent=1))
    git(repo, "init", "-q")
    if commit:
        git(repo, "add", "threshold.json")
        git(repo, "commit", "-q", "-m", "freeze threshold")
    return Fixture(root=root, l4=l4, repo=repo, threshold=thr,
                   n={"dev_real": dev_real, "dev_ai": dev_ai, "test_real": test_real, "test_ai": test_ai})
