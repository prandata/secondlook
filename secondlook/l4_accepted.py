"""Read L4's accepted files for the gate (DAT-852). Offline, no image opened.

Two schemas reach the gate:

* `second-look-accepted/1`, the one the gate was first written against (`units.dev`, `units.test`, `rejections`). One file.
* `second-look-l4-accepted/1`, what L4's `screen.py`, `rederive_d1prime.py` and `join_v2.py` really write
  (`accepted.<split>.<class>`, `rejected`, a role of `primary` or `replacement`). One file that covers dev and test, or
  one file per split (v1's dev screening and its test screening are separate runs, so their files are separate).

`load` turns either into the one shape the checks in `gate_run` and `gate_report` use:

    {"schema", "units": {"dev": [...], "test": [...]}, "rejections": [...], "protocol_sha256", "lock_sha256",
     "screen_config_sha256", "files": [{name, path, sha256, schema, splits, complete, protocol_sha256, ...}],
     "provisional": [file names whose own `complete` is not true]}

A unit is `{unit_id, label, topic, sha256, role, [study], [file], [phash]}` with `role` `primary` or `reserve` (L4's
`replacement` is a reserve that was used). Nothing is dropped or invented: a unit's split is the split of the list it is
in, never a new draw.

The v2 join file (`join_v2.py`) carries only the accepted units; the rejections of v1's units stay in the v1 files it
pins as `join.context`. `load` reads those files from the L4 folder, requires each to have the sha256 the join recorded,
and takes their rejections for the splits they were pinned for (study v1), beside the top-up's own (study v2).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .calibrate import Refused

NATIVE_SCHEMA = "second-look-accepted/1"
L4_SCHEMA = "second-look-l4-accepted/1"
SPLITS = ("dev", "test")
CLASSES = ("real", "ai")
ROLES = ("primary", "reserve")
STUDIES = ("v1", "v2")
HASH_KEYS = ("protocol_sha256", "lock_sha256", "screen_config_sha256")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, what: str) -> dict:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refused(f"{what} {path} is missing or not JSON ({exc})") from None
    if not isinstance(obj, dict):
        raise Refused(f"{what} {path} is not a JSON object")
    return obj


def _unit(entry, split: str, label: str, name: str) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("unit_id"), str) or not entry["unit_id"]:
        raise Refused(f"{name}: an accepted {split} {label} entry has no unit_id")
    uid = entry["unit_id"]
    sha = entry.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise Refused(f"{name}: unit {uid} has no sha256 of its original")
    role = {"replacement": "reserve"}.get(entry.get("role"), entry.get("role"))
    if role not in ROLES:
        raise Refused(f"{name}: unit {uid} has role {entry.get('role')!r}, not primary or replacement")
    out = {"unit_id": uid, "label": label, "topic": entry.get("topic"), "sha256": sha, "role": role}
    for key in ("study", "file", "phash"):
        if entry.get(key) is not None:
            out[key] = entry[key]
    return out


def _rejection(entry, name: str, default_study: str | None) -> dict:
    if not isinstance(entry, dict) or not isinstance(entry.get("unit_id"), str):
        raise Refused(f"{name}: a rejection has no unit_id")
    label = entry.get("class", entry.get("label"))
    if label not in CLASSES:
        raise Refused(f"{name}: rejection of {entry['unit_id']} has class {label!r}, not real or ai")
    out = {"unit_id": entry["unit_id"], "split": entry.get("split"), "label": label, "topic": entry.get("topic"),
           "check": entry.get("check"), "reason": entry.get("reason"), "role": entry.get("role")}
    study = entry.get("study", default_study)
    if study is not None:
        out["study"] = study
    return out


def _l4_units(raw: dict, name: str) -> dict:
    """One second-look-l4-accepted/1 file: {split: [units]}."""
    acc = raw.get("accepted")
    if not isinstance(acc, dict) or not acc:
        raise Refused(f"{name} has no accepted lists")
    stray = sorted(set(acc) - set(SPLITS))
    if stray:
        raise Refused(f"{name} has accepted units for {stray}, which are not dev or test")
    screened = raw.get("splits_screened")
    if screened is not None and sorted(screened) != sorted(acc):
        raise Refused(f"{name}: splits_screened {screened!r} is not the splits it lists units for ({sorted(acc)})")
    units: dict = {}
    for split, by_class in acc.items():
        if not isinstance(by_class, dict) or set(by_class) - set(CLASSES):
            raise Refused(f"{name}: accepted.{split} must hold only the classes {list(CLASSES)}")
        units[split] = [_unit(e, split, label, name) for label in CLASSES for e in by_class.get(label) or []]
    return units


def _context_rejections(raw: dict, l4_dir: Path, name: str) -> list[dict]:
    join = raw["join"]
    out: list[dict] = []
    for ctx in join.get("context") or []:
        if not isinstance(ctx, dict) or not ctx.get("file") or not ctx.get("sha256"):
            raise Refused(f"{name}: join.context entry {ctx!r} has no file and sha256")
        path = Path(l4_dir) / ctx["file"]
        if not path.is_file() or sha256_file(path) != ctx["sha256"]:
            raise Refused(f"{name}: the v1 file it was joined from, {ctx['file']}, is missing or is not the file with "
                          f"sha256 {ctx['sha256']} that the join recorded")
        sub = load_json(path, "v1 context file")
        if sub.get("schema") != L4_SCHEMA:
            raise Refused(f"{ctx['file']} (a join context) has schema {sub.get('schema')!r}, not {L4_SCHEMA!r}")
        splits = set(ctx.get("splits") or [])
        screened = set(sub.get("splits_screened") or (sub.get("accepted") or {}))
        for r in sub.get("rejected") or []:
            rej = _rejection(r, ctx["file"], "v1")
            if rej["split"] == "spare" and not screened <= splits:
                # a rejected reserve is filed under "spare", not under the split it served; with more than one split
                # in the file the join cannot say which, so it is not guessed
                raise Refused(f"{ctx['file']} screened {sorted(screened)} but the join pinned it for {sorted(splits)}: "
                              f"the rejected reserve {rej['unit_id']} cannot be placed in a split")
            if rej["split"] in splits or rej["split"] == "spare":
                out.append(rej)
    return out


def load(paths: list[Path], l4_dir: Path) -> dict:
    """The accepted files as one normalised dict (see the module docstring). Refuses what it cannot read."""
    if not paths:
        raise Refused("no accepted file named")
    files, raws = [], []
    for p in paths:
        p = Path(p)
        raw = load_json(p, p.name)
        if raw.get("schema") not in (NATIVE_SCHEMA, L4_SCHEMA):
            raise Refused(f"{p.name} schema is {raw.get('schema')!r}, not {NATIVE_SCHEMA!r} (the gate's own) or "
                          f"{L4_SCHEMA!r} (what L4 writes)")
        files.append({"name": p.name, "path": str(p), "sha256": sha256_file(p), "schema": raw["schema"],
                      "complete": raw.get("complete"), **{k: raw.get(k) for k in HASH_KEYS}})
        raws.append(raw)
    if len({f["name"] for f in files}) != len(files):
        raise Refused("two accepted files have the same name: " + ", ".join(f["name"] for f in files))
    schemas = {f["schema"] for f in files}
    if len(schemas) > 1:
        raise Refused("the accepted files have different schemas: " + ", ".join(f"{f['name']} {f['schema']}" for f in files))

    if schemas == {NATIVE_SCHEMA}:
        if len(files) > 1:
            raise Refused(f"a {NATIVE_SCHEMA} file holds dev and test itself: give one file, not {len(files)}")
        acc = dict(raws[0])
        files[0]["splits"] = [s for s in SPLITS if s in (acc.get("units") or {})]
        acc["files"], acc["provisional"] = files, []
        return acc

    units: dict = {}
    rejections: list[dict] = []
    for f, raw in zip(files, raws):
        mine = _l4_units(raw, f["name"])
        f["splits"] = sorted(mine)
        f["study"] = raw.get("study")
        for split, us in mine.items():
            if split in units:
                other = next(g["name"] for g in files if split in (g.get("splits") or []) and g is not f)
                raise Refused(f"{f['name']} and {other} both list accepted units for {split}: one file per split")
            units[split] = us
        if isinstance(raw.get("join"), dict):                       # a v2 join: rejections are in the files it pins
            rejections += _context_rejections(raw, Path(l4_dir), f["name"])
            rejections += [_rejection(r, f["name"], raw.get("study") or "v2")
                           for r in (raw.get("topup_screening") or {}).get("rejected") or []]
        else:
            if "rejected" not in raw or not isinstance(raw["rejected"], list):
                raise Refused(f"{f['name']} has no 'rejected' list")
            rejections += [_rejection(r, f["name"], raw.get("study")) for r in raw["rejected"]]
    for key in HASH_KEYS:
        vals = {f[key] for f in files}
        if len(vals) > 1:
            raise Refused(f"the accepted files disagree on {key}: they were not made under one protocol, lock and "
                          f"screening ({', '.join(f['name'] + ' ' + str(f[key])[:12] for f in files)})")
    unfetched = set()
    for raw in raws:
        lists = [raw.get("unfetched_reserves"), (raw.get("topup_screening") or {}).get("unfetched_reserves")]
        for item in (x for lst in lists for x in lst or []):
            label, _, uid = str(item).partition(":")
            unfetched.add((label, uid))
    return {"schema": L4_SCHEMA, "units": units, "rejections": rejections, "files": files, "unfetched": unfetched,
            "provisional": [f["name"] for f in files if f.get("complete") is not True],
            **{k: files[0][k] for k in HASH_KEYS}}


def screen_phash_distance(cfg: dict):
    """The pHash distance a SCREEN-CONFIG.json fixes. The gate's own layout has `phash_distance` at the top; L4's real
    file (screen.py) has it at `perceptual_hash.same_unit_if_distance_at_most`. Returns None if neither is there."""
    if "phash_distance" in cfg:
        return cfg["phash_distance"]
    ph = cfg.get("perceptual_hash")
    return ph.get("same_unit_if_distance_at_most") if isinstance(ph, dict) else None
