# W7 (DAT-795): one-command demo, transcript

Branch `w7-packaging` (a reviewer, 28 Oct 2026, AEDT), from `s5-web` (`b151010`). Fix round on a reviewer's FAIL at `90e7eef` (DAT-795, comment `1e9d07ea`) by a worker: new commits on top of `90e7eef`, then `main` merged in so the packaging is tested on the integrated tree.

## What it does

- `scripts/demo.sh` (Apple silicon, macOS 14 or later, uv): venv (Python 3.12); every install hash-checked, from `requirements/build.lock` (setuptools), `requirements/demo.lock` and `requirements/classifier.lock` (torch); the package installed with `--no-build-isolation`; the Community Forensics weights by Hugging Face revision `6076002b` and sha256 (the pins in `classifier.py`); the official C2PA trust list and the interim list by sha256 (the pins in `trust/SOURCE.md` and `c2pa.py`); the samples; the page on 127.0.0.1. `--offline` never touches the network and stops if a pinned file is missing.
- `scripts/fetch_samples.sh`: fills a real `samples/` with the 9 demo samples and `data/test-fixtures/` with the public files the tests read. Every file has a pinned sha256. Nothing is bundled. Files 01 to 05 are downloaded from Wikimedia Commons; 06 to 09 are made from them by `scripts/derive_samples.py` and checked against their sha256 (the output depends on Pillow, pinned in `demo.lock`). It refuses a symbolic link as `samples/`, so it can never write into another folder.
- `requirements/*.in` and `*.lock`: the locks pin the versions the S3 and S5 lanes ran with (torch 2.14.1, timm 1.0.30, fastapi 0.162.0, c2pa-python 0.38.2, pillow 13.0.0).
- `Makefile`: `make demo`, `make setup`, `make test`, `make clean-clone-test`, `make lock`.
- `scripts/clean_clone_test.sh`: clones HEAD, runs setup with a fresh empty HOME and a scrubbed environment, then (network denied except loopback) a control curl, `make test`, `demo.sh --offline`, `/api/health`, and all 9 samples through `/api/check`. It fails on: no samples, `samples/` not exactly the 9 files, a wrong answer, an empty card list, an empty `does_not_prove`, `demo` true, an engine that is not the ladder, or any of c2pa, iptc, exif or classifier not loaded.

## Fix round, against the review

| Review finding | Change |
|---|---|
| 1. A judge has no samples | `fetch_samples.sh` downloads them (URL and sha256 per file); `demo.sh` runs it. No symlink. |
| 2. Unhashed setuptools fetch | `requirements/build.lock` (setuptools 84.2.1, the version already in `classifier.lock`, two hashes) and `--no-build-isolation`. With network denied and an empty uv cache, the editable install succeeds; the old isolated build fails ("failed to lookup address information"). |
| 3. Vacuous clean-clone test | Rewritten as above; pass 1 runs on a fresh HOME. |
| Platform | README and `demo.sh` say Apple silicon, macOS 14 or later; `demo.sh` stops early with a message on anything else. |
| Inert Ollama step, `--no-llama` | Removed. N3 and DAT-869 add it back when they merge. |
| Second quickstart | `docs/README-for-judges.md` now points at `./scripts/demo.sh`. |
| Symlink clash with `fetch_samples.sh`, private key through the link | No link. The private key (a public c2pa-python test key the tests need to sign test files) is fetched only into `data/test-fixtures/`, never into `samples/`. The clean-clone test fails if `samples/` holds any other file or a private key. |
| Test count | Below, from a fresh-HOME run with samples fetched. |

## Checks run (28 Oct 2026, M5, Python 3.12.13)

1. `make clean-clone-test` at `d7eefb3` (the code commit; the later commits change only docs): **PASS**. Pass 1 on an empty HOME; pass 2 with the network denied.

```
ok   samples/ is a real folder with exactly the 9 demo files
ok   caches came from the fresh HOME (weights under .../home/.cache/secondlook)
ok   control: outbound request refused inside the sandbox
332 passed, 3 skipped in 119.79s (0:01:59)
ok   /api/health {"ok":true,"engine":"ladder","rungs":["c2pa","iptc","exif","classifier"],"version":"d7eefb38..."}
ok   engine is the ladder and the c2pa, iptc, exif and classifier rungs are loaded
ok   01-camera-pixel10pro-atlantic-sign.jpg             signed_camera  cards=4 (c2pa, exif, exif, classifier)
ok   02-camera-pixel10pro-utah-sign.jpg                 signed_camera  cards=4 (c2pa, exif, exif, classifier)
ok   03-ai-firefly-mountain-landscape.jpg               signed_ai      cards=2 (c2pa, classifier)
ok   04-ai-firefly-typography-logo.jpg                  signed_ai      cards=2 (c2pa, classifier)
ok   05-ai-edit-photoshop-firefly-bentley.jpg           signed_ai      cards=4 (c2pa, exif, exif, classifier)
ok   06-stripped-pixel10pro-atlantic-sign.jpg           cant_tell      cards=2 (classifier, origin)
ok   07-stripped-firefly-mountain-landscape.jpg         cant_tell      cards=2 (classifier, origin)
ok   08-iptc-ai-label-firefly-mountain-landscape.jpg    concerns       cards=2 (iptc, classifier)
ok   09-iptc-ai-label-firefly-typography-logo.jpg       concerns       cards=3 (iptc, iptc, classifier)
ok   9 of 9 samples gave the expected answer with a non-empty card list
PASS clean-clone test at d7eefb388b86c7f3cee0849e60c0bd96e96e8d8c
```

2. Full suite in the worktree, Python 3.12, every extra installed (the three locks cover test, provenance, web and classifier): **332 passed, 3 skipped** in 35.7 s. The 3 skips are `tests/test_claim.py` tests that need the real ClaimReview feed index (`SECONDLOOK_CLAIMS_DB`). This matches the merge record kept in the private build repository (332 passed, 3 skipped, with fetched samples). The earlier "297 passed, 38 skipped" was a run with no samples.
3. `fetch_samples.sh` against a scratch copy: a symlinked `samples/` is refused and the target folder stays empty; `--offline` with no samples stops with a sha256 message. 12 of the 13 test-fixture URLs (all but the unversioned IPTC one) were moved from branch heads to commit-pinned URLs, and all 13 files, plus the 5 originals, are byte-identical to the download from the branch head; samples 01 to 05 match the hashes in `DEMO-SAMPLES/MANIFEST.md`; 06 to 09 re-derive to the manifest's hashes.
4. Disk, measured after a cold-cache install of the three locks: uv cache 726 MB, `.venv` 715 MB, plus the 87 MB Community Forensics weights, the 228.5 MB Video Seal weights and about 25 MB of samples. That is about 1.8 GB by default (726 + 715 + 87 + 228.5 + 25 = 1,781.5 MB) and about 1.6 GB with `--no-watermark` (1,553 MB).
   Re-measured on 29 Oct 2026 (DAT-870 fix round 2), after `scikit-learn` and its dependencies, `reportlab` and `pypdf` joined `requirements/demo.lock`: a cold-cache install of the three locks gives uv cache 882.5 MB and `.venv` 868.3 MB (`du -sk`, in decimal MB). Default: 882.5 + 868.3 + 87 + 228.5 + 25 = 2,091 MB, about 2.1 GB. With `--no-watermark`: 1,863 MB, about 1.9 GB. `README.md` carries these figures. The figures in item 4 above are the earlier measurement, kept as the record of that run.

## Not covered

- The uv-managed Python is not hash-pinned by us. The test machine has Homebrew Python 3.12 on PATH, so uv downloaded nothing;; on a Mac without one, uv would download its own Python build.
- The classifier threshold is frozen, but its v2 gate failed ([RESULTS-v2.md](RESULTS-v2.md)), so the classifier card shows the score, the threshold and the failed test, and raises no concern (`secondlook/rungs/classifier_gate.json`). That comes from the classifier rung, not from this script.
- The Wikimedia, IPTC and GitHub files are public downloads: if a host removes one, setup stops with a sha256 or download error rather than using a different file.
