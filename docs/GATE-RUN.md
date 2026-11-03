# The gate run: one command (DAT-852, S7 stage 3)

```
scripts/gate_run.sh --l4-dir <L4 folder> --dry-run     # sizes, steps and checks; changes nothing
scripts/gate_run.sh --l4-dir <L4 folder>               # the run
```

Same thing as `python -m secondlook.gate_run`. Offline, free, no network. Install with
`uv pip install -p .venv/bin/python -e '.[test,provenance,web,classifier,gate]'`.

The design is the project lead's ruling `ec3ad415` on DAT-849: gated arms `original` and `format_matched` (both must
pass); `compressed_q70_1080` and `size_matched_512` are reported; the threshold is frozen on dev and committed before
any test image is scored; the shortcut rule is the tester's as written; floors are the harness's (INVALID below them).

## What the command does, in order

| # | Step | Refuses (exit 2, no prediction written) when |
|---|---|---|
| 1 | dependencies | Pillow (with `ImageCms`, `ImageOps`, `features`) or scikit-learn cannot be imported. Checked first, so a missing `gate` extra stops the run before any scoring |
| 2 | L4 `LOCK.json` and the split files | any locked L4 file changed after the lock; the lock does not name all of `calibrate.L4_LOCKED` **and `SCREEN-CONFIG.json`** (L4 `fetch.py` locks seven files; an unlocked config could be edited after the lock and ACCEPTED.json re-hashed to match); `dev.csv`, `test.csv` or `SPLIT.json` do not match each other |
| 3 | `SCREEN-CONFIG.json` | its pHash distance (`phash_distance`, or L4's `perceptual_hash.same_unit_if_distance_at_most`) is not the split's |
| 4 | accepted file(s): `ACCEPTED.json`, or each `--accepted` | the schema is not one of the two named below, or the files disagree; its `protocol_sha256` differs from the LOCK.json entry for `PROTOCOL.md`, or its `lock_sha256` or `screen_config_sha256` from the files they name; its own sha256 is not in `LEDGER.md` (PROTOCOL.md part D) |
| 5 | accepted units = split units | a unit is missing, extra, relabelled, or its original's sha256 differs |
| 6 | frozen reserves | a unit accepted or rejected is in neither `groups-*.csv` nor `manifest-*.csv`; the two disagree; a unit is accepted as the wrong role, split or topic; a replacement is out of order (see below) |
| 7 | **floors** | not a refusal: **INVALID**, see below; **or** the floors are met but the accepted set is PROVISIONAL (below) |
| 8 | `threshold.json` | harness validation fails; frozen against other splits; `gate_arms` is not `[original, format_matched]`; no `training_data_statement` (PROTOCOL.md F) |
| 9 | split files against the harness | the harness's own input checks (hash coverage, source groups, independence) would refuse them. Run here so the refusal comes before any file is derived or written |
| 10 | threshold committed first | the file is not tracked, differs from its commit, or was committed at or after now. Checked again immediately before the first test image is scored |
| 11 | recipe R1 code | `scripts/derive.py` is not the file L4 froze (sha256 in `gate_run.py`) |
| 12 | originals | an original is missing or its sha256 differs from the split csv |
| 13 | classifier rung and weights | the rung is not loadable, the weights file is missing, or its sha256 is not the one `threshold.json` was frozen for (checked now, before thousands of files are derived) |
| 14 | output folder | it already holds files (use a new `--out` per run) |
| then | derive | each arm is made from the ORIGINAL (one generation); a file already on disk must be byte-identical to what R1 makes; an image that cannot be derived stops the run |
| then | score | the classifier rung scores every test image in all four arms; the full ladder runs on `original` and its answers go to `ladder-answers.csv`; a missing score stops the run, never a default |
| then | harness | `predictions.csv` to the vendored L2 harness (`gate/`), which writes `RESULT.md` and `result.json` |
| then | baseline | PROTOCOL part G: depth-3 tree, `random_state=0`, fitted on dev, scored on test, per arm (`baseline.json`) |
| then | report | `RESULTS.md` in the run folder and at `docs/RESULTS.md` |

Exit code: 0 PASS, 1 FAIL, 3 INVALID (as the harness), 2 refused, 4 crashed. PASS, FAIL and INVALID are all published.
**Any unexpected exception exits 4, never 1**: 1 means the gate failed. A refusal in steps 1 to 14, in derive or in
score leaves no predictions behind (a derived image folder may exist). The one refusal that can come after
`predictions.csv` is written is the harness refusing its inputs; the message then says files were written and where.

## Floors first, then INVALID

Before anything is derived or scored the command counts the accepted units (after attrition and after the frozen reserves):
dev at least 100 real and 100 AI, and each gated arm's test set at least 400 real and 200 AI (both gated arms are made
from the same accepted originals, so they have the same counts). A shortfall is **published as INVALID**
(the project lead c014e175): `RESULTS.md` (in the run folder and at `docs/RESULTS.md`) with the counts against the floors, the
reasons and the attrition table, plus `gate-run.json`; **exit 3; no image derived, no image scored, no predictions
file**. The page says it holds no detector result, no balanced accuracy and no shortcut baseline. Floors are never
lowered. A refusal of the inputs (a changed lock, a bad hash, a reserve out of order) still comes first and exits 2:
the counts are only trusted once the lock holds. `--dry-run` reports a floor shortfall as `[INVALID]` and exits 3.

## Frozen reserves (PROTOCOL.md part D)

`groups-real.csv`, `groups-ai.csv` and the manifests are locked, so `spare_for` and `spare_rank` cannot change after
the lock. The command checks `ACCEPTED.json` against them:

- every accepted or rejected unit is in the groups and the manifest of its own class, and the two files agree on its
  role, split, `spare_for` and `spare_rank`; a reserve has `split = spare` and `spare_for` = the split it serves;
- a unit accepted into a split is that split's primary unit, or a reserve whose `spare_for` is that split; its topic is
  the frozen topic;
- for each split and topic, the number of rejected primaries is the number of replacements owed. The reserves of that
  split and topic are taken in ascending `spare_rank`: a reserve that was itself rejected is skipped in order; the
  first accepted reserves fill the owed places; the next reserve in order must be untouched. A reserve passed over, or
  a reserve **accepted** when none was owed, is refused as "replacement out of order". If the reserves run
  out, the count falls and `RESULTS.md` reports it. A reserve **rejected** past the replacements owed is counted
  (`rejected_unneeded`) and reported, not refused: L4's `screen.py` screens every reserve it fetched, a rejection adds no
  unit to the set, and refusing it would stop a run over a routine C1 rejection. A reserve L4 lists in
  `unfetched_reserves` stops the walk there ("no reserve is skipped unseen"): the place is still owed
  (`awaiting_fetch`), the set is PROVISIONAL (below), and a reserve used behind it is refused.

The `RESULTS.md` line about reserves is printed only when this check ran (it reports the counts it found); otherwise
the page says the order was not checked.

## The v2 study (L4 PROTOCOL.md part K, DAT-849)

v2 is v1's accepted units plus a top-up of new real units. The command reads it from a v2 `--l4-dir` with the same file
names; nothing about v1 changes (a folder with no `study` column is read exactly as before).

- **`groups-real.csv` and `manifest-real.csv`** hold v1's rows and the top-up rows (a deterministic concatenation, locked
  by the v2 LOCK) with a `study` column, `v1` or `v2`. The AI files stay v1: a `v2` AI unit is refused (K2). A `study`
  that is not `v1` or `v2`, blank, or different in groups and manifest is refused.
- **Reserves are kept per (split, topic, study)** (K5). A top-up spare replaces only a rejected top-up unit, a v1 reserve
  only a v1 unit, so the queue and the owed count are per study and a v1 and a v2 reserve may share a `spare_rank`. A
  message names the study (`test / landscape / study v2`) only when the folder has the column. `ctx["reserve_check_by_study"]`
  holds the used, skipped and unfilled counts per study.
- **`ACCEPTED.json`** (`second-look-accepted/1`) carries a `study` on every unit and rejection when the folder has the
  column; it must equal the study the frozen manifests give the unit. In a v1 folder a `study` is optional and must be `v1`.
- **`protocol_sha256`** is compared with the LOCK.json entry for `PROTOCOL.md` (the file on disk has already been proved
  equal to it), so an accepted file that carries v1's PROTOCOL.md hash under the v2 lock is refused.

The join output of L4's `scripts/join_v2.py` is read as L4's schema (next section). It carries only the accepted units, so
the gate reads v1's rejections from the v1 files the join pins as `join.context` (each must have the sha256 the join
recorded) and the top-up's from `topup_screening.rejected`. **Not tested on a real v2 file: none exists yet** (the top-up
is not fetched or screened); the tests use a fixture in the shape `join_v2.py` writes. RESULTS.md does not yet split its
counts by study.

## L4's accepted schema, and the split files (DAT-852 bridge)

L4's `screen.py`, `rederive_d1prime.py` and `join_v2.py` write `second-look-l4-accepted/1`: `accepted.<split>.<class>` lists
of units (`unit_id`, `sha256`, `topic`, `role` = `primary` or `replacement`, `file`, `phash`), a flat `rejected` list
(`class`, `split`, `role`, `check`, `reason`, ...), `unfetched_reserves`, and a `complete` flag. The gate reads it
directly (`secondlook/l4_accepted.py`); `replacement` is a reserve that was used. v1's dev and test screenings are separate
runs with separate files, so repeat the flag:

```
scripts/gate_run.sh --l4-dir <L4> --accepted <L4>/ACCEPTED-D1p.json --accepted <L4>/ACCEPTED-test-provisional.json --dry-run
```

The gate's own `second-look-accepted/1` is still read (one file). Each file must name this lock, protocol and screening,
have its sha256 in `LEDGER.md`, and cover its own splits (a split in two files is refused). **The sha256 of every file read
is printed by `--dry-run` and written to `gate-run.json` (`accepted_files`) and to the `RESULTS.md` run table.**

**PROVISIONAL.** A file whose `complete` is not true, or a walk that stopped at an unfetched reserve, makes the set
provisional. A floor missed is still published as INVALID (with a note that says so); a set that clears the floors is
**refused** at check 7, so a PASS or FAIL is never read from part of the set.

**Split files.** L4's dev and test are fixed by unit, so the files are written from the accepted units as given, with no
re-split, seed or re-clustering (`secondlook/gate_splits.py`):

```
.venv/bin/python -m secondlook.gate_splits --l4-dir <L4> --accepted <file> [--accepted <file>] --out <new folder>
scripts/gate_run.sh ... --splits-dir <that folder>
```

It writes `dev.csv` and `test.csv` (`item_id,label,path,group,phash,sha256`, sorted, `item_id` = `group` = the unit id,
`path` relative to `<l4>/images/original`, `phash` L4's own and only if every unit has one) and `SPLIT.json`
(`second-look-split/1`, `params.phash_distance` from SCREEN-CONFIG.json, each file's sha256 and real/AI counts, the sha256
of every accepted file, the lock, protocol and screening hashes; no clock, no seed, so the same inputs give the same bytes).
It refuses a lock that does not hold, an accepted file made under another lock, a split with no units, a unit in both
splits, a missing original, a non-empty `--out`, and an `--out` inside the L4 folder (a screening may be running there)
unless `--allow-inside-l4`. `--hash-originals` also checks each original's sha256. The threshold is frozen against the
sha256 of these two files, so write them first, then calibrate.

## The v2 two-lock arrangement (DAT-852)

v2 is v1's units plus a top-up of new real units (L4 PROTOCOL.md part K). L4's v2 join file, `ACCEPTED-v2.json`, names the
sha256 of **L4's v2 `LOCK.json`** in its `lock_sha256`. The gate folder (`fetch.py --write-gate-dir`) holds v1's rows and the
v2 rows with a `study` column, so its own `LOCK.json` hashes *those* files (a different sha256) and lists the v2 LOCK's sha256
in `v2_lock_sha256`. Two locks, then. **`--v2-lock PATH`** hands the gate L4's v2 `LOCK.json` (the smallest safe way: one
explicit file, pinned by sha256 in the gate folder's own lock, recorded in every output). `gate_splits`, `gate_run` (checks 2
and 4) and `calibrate score|freeze` all take it and all apply `calibrate.verify_v2_lock`:

- the v2 LOCK file's sha256 equals the gate folder `LOCK.json`'s `v2_lock_sha256`;
- its `gate_view` hashes equal the gate folder's `manifest-real.csv` and `groups-real.csv` (both must be listed);
- each `context` file (v1's final accepted file, which the join reads v1's rejections from) is in the gate folder, inside it,
  with the sha256 the v2 LOCK records;
- a file both locks hash and the gate folder holds unchanged (PROTOCOL.md, SCREEN-CONFIG.json, the AI manifest and groups,
  `scripts/fetch.py`) has the same sha256 in both.

Then, and only then, an accepted file must name the **v2 LOCK's** sha256 (not the folder's own `LOCK.json`); its protocol and
screening hashes are checked as before. Without `--v2-lock` the rule is unchanged, and the refusal says to pass it when the
file names the v2 LOCK the folder lists. A threshold that carries `v2_lock_sha256` (`calibrate freeze --v2-lock` writes it)
is refused unless this run verified that v2 LOCK; `SPLIT.json` (`l4.v2_lock_sha256`, `v2_lock_gate_view`, `v2_lock_context`)
and `gate-run.json` record it too. Files the gate folder does not hold (the v2 LOCK's own manifest and script hashes) are not
re-proved by the gate: the v2 LOCK's sha256, pinned in the folder's lock, stands for them.

`gate_splits` also takes `--file-base DIR`: a unit's relative `file` is read from there (default the L4 folder given), for a
gate folder that holds no images. Every original must resolve inside that base: a `file` with `..` that leaves it, an absolute
path outside it, or a symlink (the file or a directory on its path) that leads outside it is refused, with nothing written.
`SPLIT.json` records the resolved base as `file_base`; `gate_run` carries it into `gate-run.json` (the INVALID record too) and
the `The run` table of `RESULTS.md` ("Originals read from"), and shows "not recorded in SPLIT.json" for a split file written
before it was recorded.

```
R=<folder holding the run inputs>; L4=$R/L4-test-set
.venv/bin/python -m secondlook.gate_splits --l4-dir $R/gate-v2/l4 --accepted $L4/ACCEPTED-v2.json \
    --original-root $L4/images --file-base $L4 --v2-lock $L4/LOCK.json --hash-originals --out <new folder>
.venv/bin/python -m secondlook.gate_run --dry-run --l4-dir $R/gate-v2/l4 --splits-dir <new folder> \
    --accepted $L4/ACCEPTED-v2.json --ledger $L4/LEDGER.md --original-root $L4/images --v2-lock $L4/LOCK.json \
    --derived-root <new folder 2> --out <new folder 3>
```

Real files, read only (L4 at `a1ab3d9`, v2 LOCK sha256 `5dfc2d53...`): `gate_splits` writes dev 138 real + 131 AI and
test 461 real + 315 AI, and the dry run passes checks 1 to 7 and stops at check 8 only, for want of a frozen threshold.
`tests/test_gate_v2_locks.py` holds the fixture tests and every refusal; `tests/test_gate_file_base.py` holds the `--file-base`
containment, the record in `SPLIT.json`, `gate-run.json` and `RESULTS.md`, and a normal real-layout case. The acceptance was
repeated after the containment fix with L4 at `70bea3a` (which adds new files only; the v2 LOCK is still `5dfc2d53...`) into new
folders outside the L4 folder: the same counts, and the dry run stops at check 8 only.

## What `RESULTS.md` holds

The run table with every hash, then one section per arm: the allowed sentence of PROTOCOL line 105 (N, rate, bound,
arm), the table, the gate conditions for gated arms, the shortcut baseline with its exact intervals and the shortcut
label, and the per-topic table. After the arms: attrition (planned, rejected, reserve units used, accepted), the
ladder answer counts, and the limits. No figure is added up across arms. Nothing says more than the allowed wording.

## Files the command expects (and the assumptions to check)

- `<l4>/LOCK.json`: as written by L4 `fetch.py --write-lock` (`{"sha256": {...}}`).
- `<l4>/SCREEN-CONFIG.json`: a JSON object with the screening tools, versions, thresholds and the pHash distance (`phash_distance`, or L4's `perceptual_hash.same_unit_if_distance_at_most`). It must be
  named in `LOCK.json` (the run refuses a lock that omits it).
- `<l4>/ACCEPTED.json`, or each `--accepted` file: L4's `second-look-l4-accepted/1` (above), or the gate's own
  `second-look-accepted/1`, which was an assumption made before L4 defined its output. The latter's fields: `protocol_sha256`, `lock_sha256`, `screen_config_sha256`,
  `units.dev` and `units.test` (lists of `{unit_id, label, topic, sha256, role}`, `role` is `primary` or `reserve`) and
  `rejections` (list of `{unit_id, split, label, topic, check, reason}`). `l4_accepted.load` turns either into this shape,
  so `Plan.check_units`, `Plan.check_reserves` and `gate_report.attrition` read one thing.
- `<l4>/LEDGER.md`: must contain the sha256 of `ACCEPTED.json`.
- `<l4>/splits/{dev.csv,test.csv,SPLIT.json}`: `dev.csv` and `test.csv` with `item_id,label,path,group,sha256`
  (one row per unit, `group` = unit id), `SPLIT.json` with `dev.sha256`, `test.sha256` and `params.phash_distance`.
  Override with `--splits-dir`, `--dev-csv`, `--test-csv`, `--split-json`.
- `threshold.json` (default `secondlook/rungs/classifier_threshold.json`): the harness schema, plus
  `training_data_statement`. `calibrate.py` on main does not write that field or `gate_arms`; the per-arm calibration
  (a reviewer, DAT-848) must, or whoever commits the threshold adds them before it is committed.

## Shortcut baseline, as built

Features of the file the classifier receives in that arm: `is_jpeg`, width, height, megapixels, aspect ratio, file bytes,
bytes per pixel, the source JPEG quality (estimated from the original's quantisation tables, `-1` for PNG), and, in
`original` only, camera EXIF present and camera make present (derived arms carry no metadata by construction).

Balanced accuracy (for the baseline and for the detector, per arm) is reported as the two class recalls, each with its
exact two-sided 95% Clopper-Pearson interval, plus their mean with a separate interval built from two 97.5% two-sided
Clopper-Pearson intervals (one per class, 0.05 split over two classes, the tester 8167c26b, adopted by the project lead 9d763557),
`[(L_real + L_AI) / 2, (U_real + U_AI) / 2]`. Its exact label is "conservative, at least 95%, conditional on per-class
binomial unit sampling". Averaging the two 95% ends would have no coverage argument, so it is not used. No simultaneous
claim is made across arms or topics, and the one-sided gate bounds are unchanged.

Label: baseline at 0.75 or more is "shortcut-exposed"; the report says the detector "exceeds this specified shortcut
baseline by at least 10 points" or "does not exceed" it. It never says "content-based".

## Diagnostic-arm coverage

The gated population is the accepted units, scored in `original` and `format_matched`. A conversion failure in the
diagnostic arm `size_matched_512` does not stop the run and does not change that population: it is written to
`derive-failures.json` and reported as that arm's coverage and attrition. The L2 harness needs every arm in its input to
cover every test unit, so a partly covered diagnostic arm is left out of `predictions.csv` and kept, with the same
scoring, in `predictions-partial-arms.csv`; its table in `RESULTS.md` states its own N. A failure in `original`,
`format_matched` or `compressed_q70_1080` (a required arm) still stops the run.

## Test fixture

`tests/gate_fixture.py` builds a synthetic L4 folder (generated images, a fake classifier that reads the brightness of
the file it is given, a git repo holding `threshold.json`). `tests/test_gate_run.py` runs the whole command on it:
PASS, FAIL (one gated arm failing alone), INVALID (below a floor, with no file derived), the dry run, exit 4 for a crash,
the reserve-order cases and every refusal above. Its manifests and groups files have the L4 columns, and a `tweak` hook
edits them before the lock is written, so each test changes one thing and the lock, ACCEPTED.json and ledger still agree. The
fixture's `RESULTS.md` carries a "SYNTHETIC FIXTURE RUN" banner and is never written to `docs/RESULTS.md`.

## The held-out diagnostic arm (`secondlook.gate_heldout`, L4 PROTOCOL.md part I6 to I8, DAT-852)

A separate command for the 300 images of `heldout_cf_publiceval` (18 generators the detector's authors held out of its
training, Community Forensics PublicEval). **Reported, never gated**: it changes no gate result, threshold, floor or accepted
population, and it prints no PASS or FAIL (exit 0 is "done").

```
R=<folder holding the run inputs>; L4=$R/L4-test-set
.venv/bin/python -m secondlook.gate_heldout --dry-run --l4-dir $L4 --heldout $L4/heldout-v2/ACCEPTED-heldout.json \
    --threshold <frozen threshold.json> --v2-lock $L4/LOCK.json --gate-run <the main v2 gate run folder> \
    --dev-csv <splits>/dev.csv --original-root $L4/images --out <new folder>
```

Drop `--dry-run` for the run. I6 and I7 are done by the gate's own code: `gate_run.derive_all` makes `original`,
`format_matched` and `compressed_q70_1080` with `scripts/derive.py` unchanged (the diagnostic `size_matched_512` arm is not
made: `derive_all` and `score_test` take an `arms` argument, default unchanged), `gate_run.score_test` scores them with the
classifier rung and the frozen threshold, and `gate_run.threshold_commit` proves the git order. What is new is the checks,
the counts and the wording.

| Check | Refuses (exit 2, nothing scored, nothing written) when |
|---|---|
| v2 LOCK | `--v2-lock` is not a LOCK with a `sha256` table |
| `ACCEPTED-heldout.json` | wrong schema or arm; its `lock_sha256` is not the v2 LOCK's sha256; a locked file it names differs from the v2 LOCK's table or from the file on disk; `derive_script_sha256` is not the frozen recipe's; `candidates.csv` is not the list pre-registered in I2 (`76bd065c...`); an image is listed twice |
| `LOCK-heldout.json` | missing (I6 needs it before any score); it does not record the current sha256 of `candidates.csv`, `publiceval-meta.csv`, `manifest-heldout.csv`, `heldout-provenance.csv`, `ACCEPTED-heldout.json`; `candidates.csv` or `publiceval-meta.csv` differ from I2's pre-registered hashes; its six `parquet_lfs_sha256` values differ from I1's |
| manifest and images | an accepted image is not in `manifest-heldout.csv` or differs on sha256, generator or path; a file is missing, leaves the L4 folder, or its sha256 differs. Nothing is dropped or re-picked |
| `threshold.json` | missing; harness validation fails; `gate_arms` is not `[original, format_matched]`; no `training_data_statement`; its `v2_lock_sha256` is absent or not this v2 LOCK; not committed first (tracked, clean, committed before now, checked again before scoring) |
| dev split | its sha256 is not the `dev_split_sha256` the threshold was frozen against, or a dev original differs (the part G tree is fitted on dev) |
| main gate run | `--gate-run` lacks `gate-run.json` and `result.json`; it used another threshold or v2 LOCK; `result.json` has no false-alarm figure for one of the three arms. The false-alarm figure beside the catch rate is **quoted** from it, not recomputed (I6) |
| recipe, classifier, output | `derive.py` is not the frozen recipe; no classifier rung or other weights than the threshold's; `--out` already holds files |

Outputs in `--out`: `RESULTS-heldout.md` (I7 and I8), `heldout-run.json` (every pin: the held-out accepted file, the
threshold and its commit, `derive.py`, the classifier weights, the v2 LOCK, `LOCK-heldout.json`, the dev split, the
predictions), `predictions-heldout.csv`, `counts.json`, `baseline.json`, `hashes-derived.csv`, `derive-env.json`.
The report holds, per arm, the I8 sentence with N, M, the arm and both bounds, a per-generator table (a_g/n_g with its exact
one-sided 95% Clopper-Pearson lower bound, labelled descriptive), the I8 limit line, and the part G baseline's AI calls on
these files (labelled tree fitted on DiffusionDB dev); then attrition per generator and provenance hits per generator. No
pooled figure. The run stops before writing if the text holds a banned phrase from I8. A catch is a verdict of `ai`; an
abstention is never a catch. Putting this under its own heading in `RESULTS.md` is left to the lead.

**`LOCK-heldout.json` has no writer yet, and its format is this command's reading of I6:** `{"schema":
"second-look-heldout-lock/1", "sha256": {<the five files>: <sha256>}, "parquet_lfs_sha256": {"HFCF_PublicEval_0.parquet":
..., "_5": ...}}`. L4 writes it before any score; if L4 prefers another shape, `check_lock_heldout` is the one place to change.

`tests/test_gate_heldout.py` runs the whole command on a small fixture (a fake classifier, two generators of four images)
and holds the Clopper-Pearson values, the counts, the I8 wording and every refusal above.
