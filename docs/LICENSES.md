# Licences

Status: pre-release review, updated 29 Oct 2026 (DAT-865). The package tables were generated on 28 Oct 2026. Second Look itself is MIT (`LICENSE`, Inferno Tech Pty Ltd).

How this was checked: the two package tables below are **generated, not typed**. The script `docs/licences-dump.py` reads the licence text from each package's own metadata (License-Expression, then the License field, then the Trove classifiers). A package that declares nothing is printed as "(none declared)" and checked by hand. Model, code and data licences in the later sections are from the upstream pages named in each row. Where a licence could not be confirmed, the row says so. Re-check before any redistribution.

The build rule is: nothing paid, local and open-source only (MIT, Apache, CC0, CC BY). A few licences outside that list were accepted case by case. Each such row names who accepted it, or says it is still waiting for a decision. A row that names no one has not been accepted.

## Code dependencies

### Demo install (`.[test,provenance,web]`, `requirements/demo.lock`)

Generated on 28 Oct 2026. Python 3.12.13; `uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -e '.[test,provenance,web]'`, then `uv pip list --format freeze` into the script (`licences-dump.py installed <freeze file>`). That is every package the install put in the environment, direct and transitive: 32 packages. They are the 32 pins in `requirements/demo.lock`, with the same versions (checked 29 Oct 2026).

| Package | Version | Licence (from package metadata) |
|---|---|---|
| `annotated-doc` | 0.0.5 | MIT |
| `annotated-types` | 0.8.0 | MIT |
| `anyio` | 4.15.1 | MIT |
| `c2pa-python` | 0.38.2 | MIT OR Apache-2.0 |
| `certifi` | 2026.10.23 | MPL-2.0 (classifiers: Mozilla Public License 2.0 (MPL 2.0)) |
| `cffi` | 2.1.1 | MIT-0 |
| `charset-normalizer` | 3.5.2 | MIT |
| `click` | 8.5.0 | BSD-3-Clause |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| `fastapi` | 0.162.0 | MIT |
| `h11` | 0.16.0 | MIT (classifiers: MIT License) |
| `httpcore` | 1.0.9 | BSD-3-Clause |
| `httpx` | 0.28.1 | BSD-3-Clause (classifiers: BSD License) |
| `idna` | 3.20 | BSD-3-Clause |
| `iniconfig` | 2.3.1 | MIT |
| `opentelemetry-api` | 1.45.1 | Apache-2.0 |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause |
| `pillow` | 13.0.0 | MIT-CMU |
| `pluggy` | 1.7.0 | MIT (classifiers: MIT License) |
| `pycparser` | 3.12 | BSD-3-Clause |
| `pydantic` | 2.14.1 | MIT |
| `pydantic-core` | 2.50.1 | MIT |
| `pygments` | 2.21.0 | BSD-2-Clause |
| `pytest` | 9.2.0 | MIT |
| `python-multipart` | 0.0.32 | Apache-2.0 |
| `requests` | 2.34.2 | Apache-2.0 (classifiers: Apache Software License) |
| `starlette` | 1.10.0 | BSD-3-Clause |
| `toml` | 0.10.2 | MIT (classifiers: MIT License) |
| `typing-extensions` | 4.16.0 | PSF-2.0 |
| `typing-inspection` | 0.4.4 | MIT |
| `urllib3` | 2.8.0 | MIT |
| `uvicorn` | 0.54.0 | BSD-3-Clause |

**Not on the plan's licence list, and what was decided:**

| Item | Licence | Decision |
|---|---|---|
| `certifi` (via `c2pa-python` and `requests`) | MPL-2.0 | File-level copyleft. Used as a dependency, unmodified. Accepted by the project lead, 28 Oct 2026. |
| `pillow` | MIT-CMU | Accepted by the project lead. |
| `starlette` (via FastAPI) | BSD-3-Clause | FastAPI cannot be used without it. |
| `uvicorn` | BSD-3-Clause | Accepted by the project lead (S5 review). |
| `httpx` (tests only) | BSD-3-Clause | Accepted by the project lead with `uvicorn` and `starlette` (`docs/S5-WEB.md`). |
| `cffi` | MIT-0 | Permissive, no attribution needed. Accepted by the project lead, 28 Oct 2026, DAT-795. |

Notes on direct dependencies:

| Package | Used for | Notes |
|---|---|---|
| `c2pa-python` | C2PA rung | The wheel carries no LICENSE file in its metadata directory; the licence is given in the package metadata only. The native `libc2pa` it ships statically links `ring`, `rustls` and `reqwest` (permissive, per the S2 review). |
| `pillow` | IPTC and EXIF rungs, C2PA rung support; batch (B1): header size checks and the 64-bit difference hash for near-duplicate copies | MIT-CMU. No new dependency was added for the hash. |
| `fastapi`, `uvicorn`, `python-multipart` | Demo page | |
| `pytest`, `httpx` | Tests only | |

**Build tool (not installed into the environment):** `setuptools>=69` is the build backend named in `pyproject.toml`. It is MIT (version 84.2.1 in the classifier lock below).

Python itself is PSF-licensed. The standard library parts the rungs use (XML parsing, SQLite, hashing) need no separate entry.

### Classifier extra (`requirements/classifier.lock`)

Generated on 28 Oct 2026 by resolving `pyproject.toml` with `uv pip compile -p 3.12 pyproject.toml --extra classifier --extra test`, then reading each licence from PyPI for the pinned version (`licences-dump.py pypi <lock file>`). It lists 31 packages, direct and transitive: the 27 pins in `requirements/classifier.lock` (versions checked equal, 29 Oct 2026) plus `pytest`, `iniconfig`, `pluggy` and `pygments`, which the `test` extra adds and the lock does not hold. The same package at the same version may also appear in the table above. The classifier extra pins `torch>=2.11` in `pyproject.toml`.

| Package | Version | Licence (from package metadata) |
|---|---|---|
| `anyio` | 4.15.1 | MIT |
| `click` | 8.5.0 | BSD-3-Clause |
| `filelock` | 4.2.4 | MIT |
| `fsspec` | 2026.9.0 | BSD-3-Clause |
| `h11` | 0.16.0 | MIT (classifiers: MIT License) |
| `hf-xet` | 1.7.0 | Apache-2.0 |
| `httpcore2` | 2.16.0 | BSD-3-Clause |
| `httpx2` | 2.16.0 | BSD-3-Clause |
| `huggingface-hub` | 2.3.0 | Apache-2.0 (classifiers: Apache Software License) |
| `idna` | 3.20 | BSD-3-Clause |
| `iniconfig` | 2.3.1 | MIT |
| `jinja2` | 3.1.6 | BSD License |
| `markupsafe` | 3.0.4 | BSD-3-Clause |
| `mpmath` | 1.3.0 | BSD (classifiers: BSD License) |
| `networkx` | 3.7 | BSD-3-Clause |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause |
| `pillow` | 13.0.0 | MIT-CMU |
| `pluggy` | 1.7.0 | MIT (classifiers: MIT License) |
| `pygments` | 2.21.0 | BSD-2-Clause |
| `pytest` | 9.2.0 | MIT |
| `pyyaml` | 6.0.3 | MIT (classifiers: MIT License) |
| `safetensors` | 0.8.0 | Apache Software License |
| `setuptools` | 84.2.1 | MIT |
| `sympy` | 1.14.0 | BSD (classifiers: BSD License) |
| `timm` | 1.0.30 | Apache-2.0 (classifiers: Apache Software License) |
| `torch` | 2.14.1 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| `torchvision` | 0.29.1 | BSD |
| `tqdm` | 4.70.1 | MPL-2.0 AND MIT |
| `truststore` | 0.10.5 | MIT |
| `typing-extensions` | 4.16.0 | PSF-2.0 |

**MPL-2.0 items, both:**

| Item | Licence | Decision |
|---|---|---|
| `certifi` | MPL-2.0 | Accepted by the project lead, 28 Oct 2026 (see above). |
| `tqdm` 4.70.1 (comes in through `huggingface-hub`, which `timm` pulls in; classifier extra only) | MPL-2.0 AND MIT | Accepted by the project lead, 28 Oct 2026, DAT-795, on the same basis as `certifi`. It is an installed dependency that we neither modify nor vendor, so MPL-2.0's file-level terms add no duty for our code. |

`torch` reports a composite expression (Apache-2.0, Apache-2.0 WITH LLVM-exception, BSD-2-Clause, BSD-3-Clause, BSL-1.0, MIT). `numpy` reports `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`. Their terms outside the build rule were accepted by the project lead, 28 Oct 2026, DAT-795, as permissive:

| Item | Licence terms accepted | Decision |
|---|---|---|
| `numpy` 2.5.4 | 0BSD, Zlib | Permissive. Accepted by the project lead, 28 Oct 2026, DAT-795. |
| `torch` 2.14.1 | BSL-1.0, Apache-2.0 WITH LLVM-exception | Permissive. Accepted by the project lead, 28 Oct 2026, DAT-795. |

Each acceptance is per package and per licence term: it does not cover the same licence in another package. Packages that declare only a Trove classifier or "BSD" (`safetensors`, `jinja2`, `torchvision`, `mpmath`, `sympy`) are as the package declares them; their exact SPDX identifier was not checked.

### Gate harness and gate run (DAT-852)

| Item | Licence | Notes |
|---|---|---|
| L2 gate harness (`gate/`), commit `b8679a9` | Written for this project. Its source folder has no licence file, so it is covered by the repo's MIT licence only once that is confirmed | Python standard library only. Vendored unchanged. See `gate/VENDORED.md`. Confirm the licence line before publishing. |
| L4 recipe R1 (`scripts/derive.py`, `tests/vendored_l4/test_derive.py`), L4 commit `7cb1175` (the test file is unchanged since `da81be6`) | Written for this project (L4 test-set repo has no licence file); same MIT confirmation as the harness | Needs Pillow only. Copied unchanged; `gate_run.py` refuses a copy whose sha256 differs. L4 `d25c3a1` split `decode_rgb()` out of `derive()`; output byte-identical to `da81be6`, checked by a test. |
| `scikit-learn` 1.9.1 (the `gate` extra: the PROTOCOL part G shortcut baseline) | BSD-3-Clause (from package metadata) | Pulls in `scipy` 1.18.1 (BSD classifier; its licence text is the BSD-3 variant), `joblib` 1.6.0 (BSD-3-Clause), `threadpoolctl` 3.7.0 (BSD-3-Clause) `numpy` (see above), and, as locked in `requirements/demo.lock`, `cloudpickle` 3.1.2 (BSD-3-Clause, pulled in by joblib) and `narwhals` 2.28.0 (MIT); see "Gate extra in the demo lock" below. Free, local, no account. |

**Considered and not used (batch, B1, DAT-871):** `imagehash` (BSD-2-Clause) for the perceptual hash. Its difference hash is a few lines over Pillow, and Pillow is already a dependency, so batch adds no package. `concurrent.futures`, `csv`, `html` and `hashlib` are the standard library.

### Receipt additions (R1, DAT-870; `r1-receipt`)

Generated on 28 Oct 2026 with `docs/licences-dump.py installed` on the Python 3.12 environment of this branch (`.[test,provenance,web,classifier]`). `reportlab` (PDF writing) is in the `web` and `receipt` extras; `pypdf` (reads the PDF back in tests) is in the `test` extra. Both are also pinned, with hashes, in `requirements/demo.in` and `requirements/demo.lock` (DAT-870 fix round), so `scripts/demo.sh`, `make test` and `make clean-clone-test` install them; the versions and licences above were re-read from the package metadata at those pins. ReportLab's other dependencies, `pillow` and `charset-normalizer`, are already in the tables above.

| Package | Version | Licence (from package metadata) |
|---|---|---|
| `reportlab` | 5.1.0 | BSD License (classifier; the metadata License field reads "BSD license (see license.txt for details)") |
| `pypdf` | 6.21.3 | BSD-3-Clause |

### Gate extra in the demo lock (R1, DAT-870 fix round 2)

`scikit-learn` (the `gate` extra, used by `secondlook/gate_baseline.py` and checked first by `secondlook.gate_run`) is now in `requirements/demo.in` as `scikit-learn>=1.5`, the same specifier the `gate` extra uses in `pyproject.toml`, and locked with hashes in `requirements/demo.lock`, so `scripts/demo.sh`, `make test` and `make clean-clone-test` install it and the pre-registered gate can be re-run from the quickstart. Versions and licences were read on 29 Oct 2026 from the package metadata of a Python 3.12 environment installed from `requirements/demo.lock`. `numpy` 2.5.4 is already in the tables above and is the same pin as `requirements/classifier.lock`.

| Package | Version | Licence (from package metadata) | Why it is installed |
|---|---|---|---|
| `scikit-learn` | 1.9.1 | BSD-3-Clause | direct (`gate` extra) |
| `scipy` | 1.18.1 | BSD License (classifier only; the License field is the BSD-3 variant text, "Copyright (c) 2001-2002 Enthought, Inc. 2003, SciPy Developers") | scikit-learn |
| `joblib` | 1.6.0 | BSD-3-Clause | scikit-learn |
| `threadpoolctl` | 3.7.0 | BSD-3-Clause | scikit-learn |
| `cloudpickle` | 3.1.2 | BSD-3-Clause (License field "BSD-3-Clause"; classifier BSD License) | joblib |
| `narwhals` | 2.28.0 | MIT | scikit-learn |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | scikit-learn, scipy (already listed above) |

| Item | Licence | Decision |
|---|---|---|
| Bitstream Vera Sans fonts (`Vera.ttf`, `VeraBd.ttf`), shipped inside the `reportlab` wheel and embedded as subsets in the receipt PDF | Bitstream Vera licence (`reportlab/fonts/bitstream-vera-license.txt`): permissive, allows use, copying, embedding and redistribution, only requires the copyright and licence notice to travel with copies of the font, and forbids selling the fonts by themselves | **Accepted (the project lead, 28 Oct).** Permissive font licence, outside the build rule's list until this decision. Used unmodified, never extracted or sold separately, and its licence file travels with the `reportlab` wheel. A swap to another font is a one-line change in `secondlook/receipt.py` (`_register_fonts`). |
| Playfair Display (receipt PDF title), the font already vendored for the demo page | SIL OFL 1.1 | **Accepted (the project lead, 28 Oct).** Also accepted for the demo page (see "Font" below). The receipt reuses the same file and its `OFL.txt`, and embeds a subset in the PDF. |

**Considered and not used:** `exiftool` and `pyexiv2` (the exiv2 binding). Both are GPL-family, which is outside the build rule, so the IPTC and EXIF rungs use the standard library and Pillow instead.

## Model

| Item | Licence | Source |
|---|---|---|
| Community Forensics code (Park and Owens, CVPR 2025) | MIT | github.com/JeongsooP/Community-Forensics |
| Community Forensics ViT-S/16 384 weights, `OwensLab/commfor-model-384` | MIT | huggingface.co/OwensLab/commfor-model-384. Pinned to revision `6076002bf0d9dd37537f965ee2f06f826c333b61`, file sha256 `b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387`, about 87 MB. |

**Not reviewed:** the licences of the data the Community Forensics model was trained on. The MIT licence on the code and weights does not cover that. We have not checked it, and we make no claim about it.

### Meta Video Seal (optional watermark rung; demo only)

The watermark rung looks only for Second Look's own invisible mark (`secondlook/rungs/watermark.py`). The weights are never in this repo: `scripts/demo.sh` and `python -m secondlook.rungs.watermark --fetch` download them to `~/.cache/secondlook/models/videoseal/` and refuse any file whose sha256 differs.

| Item | Licence | Source, read 29 Oct 2026 |
|---|---|---|
| Video Seal code (`facebookresearch/videoseal`) | MIT | The repo's `LICENSE` begins "MIT License" and "Copyright (c) Meta Platforms, Inc. and affiliates"; GitHub's licence API reports `MIT`. Read at the default branch head, commit `870ca7fb33578b90f14c602016b6c2788096226e` (2 Jul 2026). |
| Video Seal 1.0 image model `y_256b_img`, TorchScript weights | MIT, by the repo README: "All models, training code, and evaluation tools released under MIT license" (feature list) and "The model is licensed under an MIT license" (License section), same commit. The weight file carries no licence text of its own, and the download host (`dl.fbaipublicfiles.com`) has no licence page; the README is the only statement. | File `https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit`, named in the repo's `docs/torchscript.md` at the same commit. 228,524,815 bytes, last modified 2 Apr 2025 (HTTP headers, 29 Oct 2026). sha256 `5c7a4581c36fc6090aafdcfb3999123bae5172a4847f22e2da4e7fd1a39d1e1b`, pinned in `watermark.py` and `scripts/demo.sh`; our local copy hashes to the same value (29 Oct 2026). Torch only: the `videoseal` package is not used. |

**Not reviewed:** the licences of the data Video Seal was trained on. The MIT statement does not cover that, and we make no claim about it. Second Look is not affiliated with Meta, and the rung says it does not detect Meta AI images or any company's mark. If the weights were ever redistributed, the MIT notice above would have to go with them; we do not redistribute them.

### whisper.cpp and the Whisper model (optional voice rung)

The voice rung transcribes a voice note on this computer (`secondlook/rungs/voice.py`, `docs/W9-voice.md`). Neither item below is in the repo.

| Item | Licence | Source, read 29 Oct 2026 |
|---|---|---|
| whisper.cpp 1.8.4 (`whisper-cli`, the runtime; installed with Homebrew, not vendored) | MIT | `LICENSE` in `ggml-org/whisper.cpp` at tag `v1.8.4` (published 19 Mar 2026): "MIT License", "Copyright (c) 2023-2026 The ggml authors". The Homebrew formula `whisper-cpp` also gives `MIT`. Homebrew's current stable is 1.9.5; 1.8.4 is the version the voice rung was built and run with (`docs/W9-voice.md`). |
| `ggml-small.en.bin`, OpenAI's Whisper `small.en` model converted to the ggml format | MIT. The Hugging Face model card's front matter says `license: mit`; its title reads "OpenAI's Whisper models converted to ggml format for use with whisper.cpp". The repository has no `LICENSE` file at this revision, so the card is the only statement for the converted file. | `huggingface.co/ggerganov/whisper.cpp` at revision `5359861c739e955e79d9a303bcbc70fb988958b1` (last modified 29 Oct 2024; the repo is not gated). 487,614,201 bytes, sha256 `c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d` (pinned in `voice.py`; our local copy hashes to the same value, 29 Oct 2026). The `base.en` choice (`ggml-base.en.bin`, `docs/W9-voice.md`) comes from the same repository and revision, so the same card covers it. |
| OpenAI Whisper (the original model that the file above converts) | MIT | `LICENSE` in `openai/whisper` begins "MIT License" and "Copyright (c) 2022 OpenAI". Its README says: "Whisper's code and model weights are released under the MIT License." Read at the default branch head, commit `86098128` (31 Aug 2026). |

The lineage matters for redistribution: the converted file is OpenAI's model, so OpenAI's MIT copyright and permission notice is the one that travels with it. We download the file onto the user's own machine and do not redistribute it. **Not reviewed:** the data Whisper was trained on; we make no claim about it.

### Meta Muse Glimmer 30B (optional local model, lane M1, DAT-869)

Used for two optional features, neither of them evidence: picking which clauses of a caption become the search string for the S6 lookup (the code copies them word for word), and reading the words printed in an image. No model writes explanations. **The weights are never in this repo.** `scripts/fetch_glimmer.sh` downloads Meta's own files to `~/.cache/secondlook/models`, and `scripts/glimmer_server.sh` loads them from there.

| Item | Licence | Source, read 28 Oct 2026 |
|---|---|---|
| Muse Glimmer 30B weights | **Apache License 2.0** | `LICENSE` in huggingface.co/meta-models/Muse-Glimmer-30B at revision `cec94fcfd6512f2cfe708138c32533b346ea4f69`, sha256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`. It is byte-identical to https://www.apache.org/licenses/LICENSE-2.0.txt. The model card's front matter says `license: apache-2.0`, and its text says "**License:** [Apache 2.0]", "**Authors:** Meta Superintelligence Lab". The publishing organisation `meta-models` is shown by Hugging Face as "Meta Inc.", verified. The GGUF repo (`meta-models/Muse-Glimmer-30B-GGUF`, revision `b44f1072cf75e918a1b82a3a17b45c4389b076e7`) carries the same `LICENSE` file (same sha256). |
| Muse Glimmer Usage Policy | Meta's policy, linked from the model card | `USAGE_POLICY.md` in the same repo and revision, sha256 `98a14dab9fd97de1666dc8589d399efab1e7fc3ba4e6230d037d6637ba9481d3`. The model card says: "Our Usage Policy can be found here". The Apache 2.0 text does not refer to it. Whether it binds a user of the Apache-licensed weights was not decided here; we follow it anyway (below). |
| llama.cpp `llama-server` (the runtime; not vendored) | MIT | Homebrew formula `llama.cpp` 0.7.0 (build b11669, commit `620d2225c`), licence field `MIT`. |

**Does the licence allow this use? Yes.** Apache 2.0 section 2 grants "a perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable copyright license to reproduce, prepare Derivative Works of, publicly display, publicly perform, sublicense, and distribute the Work and such Derivative Works". Running the model locally needs nothing more. We do not redistribute the weights, so the section 4 conditions on redistribution (a copy of the licence, notices) do not come into play. Apache 2.0 is on the build rule's list at the top of this file.

**The usage policy, as it bears on Second Look.** Its prohibited uses include, word for word:

- "Collect, process, disclose, generate, or infer private or sensitive information about individuals, including information about individuals' identity, health, or demographic information, unless you have obtained the right to do so in accordance with applicable law" (1.5). The image reader only copies printed words. Its prompt forbids describing the image. No demo sample shows a person. Number plates, phone numbers and email addresses are removed from every reading before it is shown, stored or searched (`redact` in `secondlook/glimmer.py`; the project lead's decision on DAT-869).
- "Generating, promoting, or furthering fraud or the creation or promotion of disinformation" (3.1). The features only look a claim up in published fact-checks. The model gives no rating, and its claim may only use the caption's own words (`grounding_problems`).
- "Representing that the use of Muse Glimmer or outputs are human generated" (3.5). Every model output is labelled "model reading, not evidence" and the model is named.
- "Fail to appropriately disclose to end users any known dangers of your AI system" (4). The label says what the reading is not. `docs/M1-GLIMMER.md` records what the smoke run read, checked against the images.

Its other prohibitions (violence, weapons, critical infrastructure, malware, and so on) do not touch this use. The policy also says "Muse Glimmer is not intended for individuals under the age of 18" (re-read 28 Oct 2026 at the same revision and sha256). the project lead's decision on DAT-869: the README says the Glimmer features are for adult and professional use, quoting that line. Second Look has no age gate.

**Which files are run.** By default the script loads Meta's own files from the GGUF repo, pinned by `scripts/fetch_glimmer.sh` to revision `b44f1072cf75e918a1b82a3a17b45c4389b076e7` and checked by sha256 (the project lead's decision on DAT-869):

| File (Meta's GGUF repo at `b44f1072`) | Bytes | sha256 |
|---|---|---|
| `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` (text model) | 16,756,683,904 | `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e` |
| `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf` (vision projector) | 1,400,328,928 | `f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6` |

The fetch script refuses to start with under 40 GB free, downloads each file to a `.part` file, and moves it into place only when its sha256 matches; a mismatch deletes it. The server script uses a pinned file only when it is in place with the pinned size. If Meta's files are not there, it falls back to the copy in the Ollama store (`muse-glimmer:30b-q4_K_M`, pulled 27 Oct 2026) and prints a warning:

| File | sha256 | Same as Meta's file? |
|---|---|---|
| Vision projector, Ollama blob, 1,400,328,928 bytes | `f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6` | **Yes**: identical to `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf` in Meta's GGUF repo at `b44f1072` (same sha256 and size, from the Hub's file listing). |
| Text model, Ollama blob, 16,756,681,056 bytes | `71b5c9c9abbc2a9e7c13550b6f9fe1f062650117a24490b15a310cbd7de03f8b` | **No.** Meta's `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` is 16,756,683,904 bytes, sha256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`. The Ollama file's embedded chat template is 7,167 characters, against the 9,992 of Meta's current `chat_template.jinja`. The GGUF README says "Earlier GGUFs shipped a template that skipped" its reasoning-effort fix, so this is probably an earlier build of the same quantisation. That is an inference: the weights in the two files were not compared. |

Both are Apache 2.0 either way. Meta's files are about 18.2 GB to download, need no account, and the repo is `gated: false` (Hub API, 28 Oct 2026; the repo head was still `b44f1072`). `GLIMMER_MODEL` and `GLIMMER_MMPROJ` still override both.

## Data

| Dataset | Licence | Used for | Notes |
|---|---|---|---|
| Wikimedia Commons photographs (non-AI test and dev images) | CC0, CC BY or CC BY-SA, as set per file. Files under CC BY-NC variants were excluded. | Test and dev sets | Each file keeps its own licence and author, which are recorded in the manifest. **CC BY-SA files: test input only, used locally, never redistributed; owner decision pending on DAT-868.** CC BY-SA is outside the build rule and no one has accepted it. The images are fetched for local testing and are not stored in this repo. Attribution text is recorded per file. |
| DiffusionDB 2M (AI test and dev images) | CC0 1.0 (dataset card, read 28 Oct 2026) | Test and dev sets | The card says generated images carry a CC0 1.0 dedication. Its NSFW filter "is not perfect", so images are filtered on the published NSFW scores and prompts are limited to a generic vocabulary. |
| Data Commons ClaimReview feed | The compilation is CC BY (stated on datacommons.org/factcheck/download). Each ClaimReview may carry its own `sdLicense`; none of the 100,369 items in our 28 Oct download does. Publishers may have their own terms. | Claim rung | Credit "Data Commons" and name the fact-checker wherever a match is shown. Each review remains the fact-checker's own work. The version of CC BY is not stated on the page. Feed download: 28 Oct 2026, sha256 `80755795cad78e0d880627bd37ce647e19a895f11c3bb1105fc7ae6c5ee92b81`. Not stored in git. |
| Public C2PA and IPTC sample images | Varies | Tests of the provenance rungs | One sample (`org-adobe-CAI.jpg`) is CC BY-SA 4.0: **test input only, used locally, never redistributed; owner decision pending on DAT-868.** The IPTC sample's "test-use" licence was not verified. Samples are test inputs, are git-ignored and are not redistributed. |
| Community Forensics public evaluation set (Park and Owens, CVPR 2025), `OwensLab/CommunityForensics` @ `f0b06f79d20389706bb76c626391fa870e0bf604`, split `PublicEval` (`data/publicEval`) | CC BY 4.0 (dataset card, "for research purposes only"). The card also says each image was "generated by the models with their respective licenses"; we have not reviewed those generator licences. | Research evaluation only: the held-out arm that measures the classifier (DAT-848, DAT-795). Its metadata also showed us two generator labels the EXIF rung now matches (`docs/S2-RUNGS.md`). | Credit: Park and Owens, Community Forensics, CC BY 4.0. We never redistribute the images: they stay outside git, and only row ids, file hashes and aggregate results are published. Accepted by the project lead, 28 Oct 2026 (review-inbox #987, comment 6465552127). Only the generated (label 1) rows are used. |
| The 9 demo samples (`samples/`, from `scripts/fetch_samples.sh`) | 01: CC BY-SA 4.0 (Mark Clifton, via Flickr). 02: CC BY 4.0 (Bobjgalindo). 03 and 04: public domain, PD-algorithm (Adobe Firefly Image 3, prompted by Jiri Vedral). 05: CC BY-SA 4.0 (Thilo Parg, own work and Adobe Firefly AI). 06 to 09: changed copies of 01, 03 and 04, so they carry those licences. | The judges' demo and the clean-clone test | As stated on each Wikimedia Commons file page on 28 Oct 2026, not independently confirmed with the authors. **Nothing is bundled in this repo.** The script downloads each file from its Commons URL, checks a pinned sha256, and makes 06 to 09 on the user's machine with `scripts/derive_samples.py` (re-encode at JPEG q90; 08 and 09 add one IPTC AI label written by us, copied from 03's signed manifest and the Commons author line). No file shows a person. Sources and dates: `DEMO-SAMPLES/MANIFEST.md`. The CC BY-SA files fall under the same open owner decision, DAT-868. |
| C2PA Trust List (`c2pa-org/conformance-public`, commit `47009ee`, 2026-10-17) | CC BY 4.0 (GitHub repository licence, as recorded in `secondlook/trust/SOURCE.md`) | C2PA rung: which signers are trusted | Committed in `secondlook/trust/C2PA-TRUST-LIST.pem`, copied unmodified, with its source, date and sha256 (`b3949271...af8ae`) recorded in `SOURCE.md`. Credit: C2PA, CC BY 4.0. |
| IFCN signatories list: organisation names and the hosts of their own websites (`secondlook/rungs/factcheckers.py`, 182 hosts) | **No licence or terms found for the list.** Not cleared with its publisher. Accepted as facts only by the project lead (Opus 5.5), 30 Oct 2026, DAT-850: an accepted risk, not a licence. See the notes below the table. | Claim rung: labels a claim card when the fact-check's site is on the list. It does not change a rating and says nothing about a page on that site. | Source: the IFCN signatories page, `https://ifcncodeofprinciples.poynter.org/signatories`, read 28 Oct 2026, from the JSON endpoint the page itself loads (`https://ifcn-cop-prod-server-8q9x7.ondigitalocean.app/api/organization/signatories`), by hand with `scripts/build_factchecker_domains.py` (never at run time). Kept: "Verified Signatory" and "In Renewal"; "Expired Signatory" and social platforms left out. |
| GeoNames `cities5000`, `countryInfo` and `admin1CodesASCII` | CC BY 4.0 (stated in the dump's `readme.txt`, read 28 Oct 2026: "This work is licensed under a Creative Commons Attribution 4.0 License"). Provided "as is", with no warranty of accuracy | Context rung: turns a claimed place name (a country, a first-level region or a city) into points to measure the file's GPS position against (X1, DAT-872) | Credit "GeoNames" wherever a looked-up place is shown; the context card names it. `scripts/fetch_geonames.sh` downloads the three files from download.geonames.org/export/dump/ into `data/` (gitignored), checks the sha256 pinned in the script (28 Oct 2026: `cities5000.zip` `2c3cd97e0e829001cdb951277e2d9f6a00e9b1abeae8b72fbdbdfb24799c347e`, `countryInfo.txt` `5c710efde90ee4cca15001c9f6a97ae7a74c7020b365504913ddc3afeb6d3671`, `admin1CodesASCII.txt` `2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3`) and builds a local SQLite index. Not stored in git, except the test fixture below. GeoNames republishes daily, so a later download needs its new hash accepted on purpose (see the script). |
| GeoNames excerpt, `tests/fixtures/geonames/` | CC BY 4.0, GeoNames | Tests of the place lookup and the country and region rules | 180 rows of `cities5000.txt` (every place sharing a name the tests use, the places their GPS points are taken from, the largest place in each region they name, and the places within 25 km of the Pixel 10 Pro sample's GPS), and the whole of `countryInfo.txt` and `admin1CodesASCII.txt`, copied unchanged from the 28 Oct 2026 download above. |

**IFCN list: what its terms say (checked 29 Oct 2026).**

- The IFCN site (`ifcncodeofprinciples.poynter.org`) has no terms-of-use or licence page. Its footer links to the commitments, advisory board, assessors, signatory benefits, a transparency statement, a complaints policy and application pages, and its only legal line is "© 2026 International Fact-Checking Network". The page we read the endpoint from states no licence for the list.
- Poynter's own site publishes "Poynter Terms & Conditions" inside its privacy policy (`https://www.poynter.org/privacy-policy/`). They say "All materials contained on Poynter.org are produced or developed by Poynter ... and are owned exclusively by Poynter" and that a user "may not download, modify, publish, transmit or sell, reproduce, create derivative works from, distribute, perform, link, display, or in any way exploit any of the Project Materials, in whole or in part, except as expressly permitted". Those terms are written for `poynter.org` and its login. Whether they reach the IFCN subdomain or the JSON endpoint is not stated, and we did not take a legal view.
- `robots.txt` on the IFCN site (29 Oct 2026) disallows `GPTBot` and `Google-Extended`, and a few account and application paths. The endpoint's own host serves no `robots.txt` (404). Our read was one by-hand request, not a crawl, and the list is not used to train any model.
- What we kept is facts: each organisation's name and the host of its own website, as the list gives them. We copied no Poynter text, design or logo. The notice in `factcheckers.py` credits the page and the read date.
- **Options that were open** (decided below), with the first as the smallest step: keep the list as facts only; cut the notice and the list to a link to the public signatories page and match nothing; or ask the IFCN for permission in writing. Until it is decided, the row stays "no licence found", and the public copy should say so.
- **Accepted decision: the IFCN list stays as facts only (option 1).** Ruled by the project lead (Opus 5.5) under the owner's 28 Oct 2026 delegation of the hackathon decisions, recorded 30 Oct 2026 (DAT-850). Limits: the list stays organisation names and the hosts of their own websites, as it is now. We add no Poynter or IFCN text, design or logo, and send no permission request. The row above still reads "no licence found": this is an accepted risk, not a licence, and the public copy keeps saying so. It was decided by an agent, not by the IFCN, and it says nothing about any other use of the list.

The M2 smoke screenshots (`docs/M2-SHOTS/`, DAT-873) show the claim text of three PolitiFact ClaimReview entries in that feed, as one-line quotations under a mock post header, made here to test the investigator; the review URLs are in `scripts/investigate_smoke.py`.

**Accepted decision: the video may quote PolitiFact's rating.** Ruled by the project lead (Opus 5.5) under the owner's 28 Oct 2026 delegation of the hackathon decisions, recorded 30 Oct 2026 (DAT-850). What the demo video may show of a PolitiFact fact-check: the rating phrase, the claim line, the fact-checker's name and its link, as a short quotation for review. What it must not show: article text, a PolitiFact logo, the Truth-O-Meter graphic, or a screen recording of politifact.com. We send no permission request. The compilation licence (CC BY, Data Commons) covers the feed, not each publisher's own terms ("Publishers may have their own terms", row above), and PolitiFact's are not recorded here as reviewed, so the residual risk is accepted, not cleared. It covers PolitiFact in this video only, not other fact-checkers or other uses. The Data Commons CC BY credit goes on screen with every match (`FEED_CREDIT`) and in the video's description. The video goes out only on the owner's publish yes, and that ask names this ruling.

Recorded Pentagon case (`samples/recorded/`): a mock post we made with our own caption wording (from the M1 test, `scripts/glimmer_smoke.py`). It carries no PolitiFact text. The recording shows the fact-check strip as the page does: PolitiFact's rating phrase, claim line, name and link from the Data Commons ClaimReview feed (CC BY), with its credit, within the short-quotation limits above (DAT-865).

Excluded on licence: Synthbuster and Chameleon (non-commercial licences).

## Font

| Item | Licence | State |
|---|---|---|
| Playfair Display (demo page headings) | SIL OFL 1.1 | **Vendored** as `secondlook/web/static/fonts/PlayfairDisplay.ttf` (variable, v1.203), with its licence file `OFL.txt` beside it, both copied unchanged from the WSAI desk brand kit. OFL is outside the plan's list; the project lead accepted it on condition that the licence file ships with the font, and it does (`docs/S5-WEB.md`). A test checks both files are present. |
| Favicon `secondlook/web/static/favicon.ico` (DAT-850 A249) | Our own image; the glyph is drawn from the vendored Playfair Display (SIL OFL 1.1) | **Ours.** `scripts/make_favicon.py` draws a cream Playfair Display Bold "S" on the brand burnt orange `#B4532F`, the WSAI desk brand kit's logo recipe (`design/brand/brand.py`, `logo.png`, colours from `brand.json`). The OFL FAQ (openfontlicense.org/ofl-faq, read 30 Oct 2026, question 1.13): "creating any kind of graphic using a font under the OFL does not make the resulting artwork subject to the OFL"; the font file and its `OFL.txt` ship as above. |
| SL Title, the headings font inside `summary.html` (DAT-850 A271): `secondlook/fonts/sl-title-bold-latin.woff2`, 18,852 bytes | SIL OFL 1.1 (a Modified Version of the vendored Playfair Display) | **Ours to ship under the OFL.** `scripts/make_summary_font.py` fixes the vendored `PlayfairDisplay.ttf` at Bold (wght 700), cuts it to Latin and saves it as WOFF2; `batch.py` carries it in `summary.html` as a `data:` font (CSP `font-src data:`), so the page fetches nothing. Licence check (OFL FAQ, openfontlicense.org/ofl-faq, read 31 Oct 2026): entry 2.10 says webfonts are "not an integrated part of the document itself", unlike fonts embedded in a PDF, so the PDF embedding rule (1.12) does not cover this copy; entries 2.5 and 2.6 make an optimised or subsetted webfont a Modified Version, which "would not normally allow the use of RFNs". `OFL.txt` line 1 reserves the name "Playfair Display", so this copy is named "SL Title" in every name record that names the font (IDs 1, 2, 3, 4, 6), and its copyright, licence and licence URL records (IDs 0, 13, 14) are kept unchanged. OFL condition 2 asks that each copy contain the copyright notice and the licence, and condition 5 keeps the copy under the OFL, so `summary.html` carries the full `OFL.txt` text in a comment beside the font. Naming Playfair Display in that comment acknowledges the authors, which condition 4 allows. |
| Aileron Regular, the font built into Pillow 13.0.0 (`ImageFont.load_default(size=...)`) | CC0 1.0 (dotcolon.net/fonts/aileron, read 28 Oct 2026: "Aileron is No Rights Reserved", linking creativecommons.org/publicdomain/zero/1.0) | Ships inside Pillow, nothing added. Used only by `scripts/investigate_smoke.py` (M2, DAT-873) to render the three test screenshots in `docs/M2-SHOTS/`. |

## Not licensed here

No GoComply code or repository is used. No paid or metered API is called. Second Look is not affiliated with Meta, and nothing here grants any right in a third party's name or marks.
