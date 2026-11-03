# S2 provenance rungs (DAT-847), stage 1 plus fix round

Lane S2 of the build plan. Three rungs, each
`check(path) -> list[Evidence]`, coded to the shared contract in
`secondlook/types.py` (committed verbatim from the build plan so this branch runs on
its own; S1's copy is the owner of that file).

| Rung | File | Library and licence | Strength it can give |
|---|---|---|---|
| C2PA | `secondlook/rungs/c2pa.py` | c2pa-python 0.38.2, MIT OR Apache-2.0 (PyPI metadata); C2PA Trust List, CC BY 4.0 (`secondlook/trust/`) | `signed` only when the manifest validates **and** its signer is on the C2PA Trust List; `metadata` otherwise |
| IPTC | `secondlook/rungs/iptc.py` | Python stdlib `xml.etree`; Pillow 13.0.0 (MIT-CMU) for compressed PNG XMP | `metadata` |
| EXIF | `secondlook/rungs/exif.py` | Pillow 13.0.0 (MIT-CMU) | `metadata` |

exiftool and pyexiv2/exiv2 were not used: both are GPL-family, outside the
owner's MIT, Apache, CC0, CC BY list. MIT-CMU is a permissive MIT-family
licence; flagged here so the reviewer can confirm it is acceptable.

## What the ladder (S1) can key on: `Evidence.raw`

C2PA (one Evidence when a manifest is present):
- `valid` (bool): validation_state is Valid or Trusted and the active manifest has no failure code other than `signingCredential.untrusted`.
- `trusted` (bool): validation_state is Trusted, under one of two lists read in this order: the official C2PA Trust List pinned in `secondlook/trust/` (conformance-public commit 47009ee, 2026-10-17), then the C2PA interim trust list from contentcredentials.org, fetched into `secondlook/trust/interim/` by `scripts/fetch_trust.sh` and loaded only when its sha256 matches the pin. The interim list is asked only when the official one does not trust the signer, and its reading is used only when it does. Strength is `signed` only when `trusted` is True: a valid manifest from a signer on neither list (anyone can make a certificate) is `metadata`.
- `trusted_by`: `"official"`, `"interim"` or None. The card's finding names the list: "The signer is on the C2PA Trust List (2026-10-17)." or "The signer is on the C2PA interim trust list, which C2PA describes as temporary."
- `trust_list`: the record of the list that trusted the signer (None when neither did). `trust_lists`: both lists as loaded, each with `id`, name, source URL, date, sha256 (`sha256` for the official PEM, `files` for the interim anchors and allowed list) and `loaded` (with `not_loaded` saying why, if not).
- `source_class`: `"ai"` (active manifest declares `trainedAlgorithmicMedia` or `compositeWithTrainedAlgorithmicMedia`, or declares no source type and the claim generator names an AI image tool from the narrow list in `_sources.py`), `"camera"` (declares `digitalCapture`, `computationalCapture`, `negativeFilm`, `positiveFilm` or `print`, and the generator names no AI tool), else `"other"` (including a declared capture whose generator names an AI tool: the claims conflict). Only URIs in the IPTC `cv.iptc.org/newscodes/digitalsourcetype/` or `c2pa.org/digitalsourcetype/` namespaces count. A declared capture is never AI evidence.
- `ai_label` (bool): True when a valid manifest's AI claim can't be taken as signed (untrusted signer declaring AI; capture plus an AI tool name; AI only in an ingredient's history). The ladder reads it as a concern.
- A file that only points to an online manifest gives one `metadata` Evidence with `remote_manifest_url` and `valid` None; it is never fetched.
- `ai_ingredient` (bool): another manifest in the store declares an AI source type.
- also `failures`, `status_codes`, `signer_issuer`, `signer_common_name`, `sign_time`, `claim_generator`, `digital_source_types`, `actions`, `ingredient_count`, `embedded`, `sdk`.

Ladder mapping: rule 1 `signed_ai` = `rung=="c2pa" and strength=="signed" and raw.valid is True and raw.trusted is True and raw.source_class=="ai"`; rule 2 `signed_camera` = the same with `raw.source_class=="camera" and raw.ai_ingredient is False`; rule 3 `concerns` = any Evidence with `raw.ai_label` True (IPTC/XMP label, an EXIF or XMP text field naming an AI tool, PNG generator settings), plus an invalid C2PA manifest is worth listing as a reason.

IPTC: `source_class`, `ai_label`, `field`, `values`; or `fields` (AISystemUsed etc.) with `ai_label` True.
EXIF: `make`, `model`, `software`, `datetime_original`, `lens`, `has_gps` (never coordinates), `ai_label`, `ai_tool_name`, `png_keys`. A PNG text key counts only when its value has the generator's shape (A1111 `Steps: N, Sampler:`, ComfyUI graph JSON with `class_type` or `nodes`/`links`, InvokeAI settings); no text is copied out.

Generator labels (DAT-848): `secondlook/rungs/_generators.py` holds the curated lists, one `Label(pattern, generator, evidence, fields)` row each, in two classes. **Caption-type fields** (EXIF ImageDescription, UserComment, XPComment, Artist; XMP dc:description, photoshop:Credit) match only an exact label phrase, the whole field and nothing else, that is OBSERVED in real files or PRIMARY (documented by a vendor or standards body, with URL and date): "Made with OpenAI DALL-E" (OBSERVED, 34 files) and "Made with Google AI" (PRIMARY: IPTC, 28 Oct 2024; XMP photoshop:Credit only). So "Foto dalle Alpi", "Photo from a Midjourney workshop" and "Not made with Midjourney" give nothing. **Tool-identity fields** (EXIF Software, Make, Model; XMP xmp:CreatorTool) match a product name in its own spelling (NAME), case-insensitive, but only when the field is that name plus version or edition words ("Midjourney V6", "Stable Diffusion XL", "DALL-E 3", "Imagen 3"; "Ideogram AI" in Make is OBSERVED, 68 files). A sentence that mentions a product ("Camera: Stable Diffusion is not used", "Imagen 2 de 10") is not a tool name. Bare "Firefly" is read from Software and CreatorTool only (a Firefly camera line exists); "Imagen" needs its number and "Gemini" its image-model shape. Dropped: "Imagined with AI" (a visible platform label, not file metadata), Meta AI, bare Gemini, Imagen, Leonardo, Flux, OpenAI. A match gives a `metadata` card, "The file's <field> names <generator>, an AI image tool.", whose `raw` is only `field`, `generator`, `ai_tool_name`, `ai_label` True: concerns, never `signed_*`. The text is matched and dropped, and a matched Make, Model or Software is also left out of the other cards' raw, so request ids and prompts never reach a card. The card's does_not_prove says a missing label proves nothing. A Make or Model that names a generator ("Ideogram AI") no longer also gets a "names a camera" card. PNG text chunks are still matched by shape only (A1111 `parameters`), not by name. Not matched, because it names no generator: the DALL-E 2 MakerNote `OpenAI--req_<id>` (a request id). The C2PA claim-generator list in `_sources.py` is unchanged. Recount on the 612 Community Forensics public eval files (heldout-v2, `scripts/heldout_label_recount.py`): 34 of 34 DALL-E 2 (UserComment) and 68 of 68 Ideogram (Make) give `concerns`, 0 of the other 510.

`algorithmicMedia` is deliberately not "ai": IPTC defines it as media made by an algorithm without sampled training data.

## Offline
`remote_manifest_fetch` and `ocsp_fetch` are set False in the c2pa Settings. Note: c2pa-python 0.38.2 accepts unknown setting keys silently, so this relies on the key names in c2pa-rs; the reviewer should confirm with a network-off run.

## Trust lists (stage 3)
`bash scripts/fetch_trust.sh` fetches the interim list and checks it against the pinned sha256; without it the rung still runs, records the interim list as not loaded, and a signer only the interim list knows reads as untrusted (`metadata`). The 9-sample demo run is in `docs/S2-STAGE3-DEMO.md`.

## Samples (`scripts/fetch_samples.sh`, into gitignored `samples/`, sha256 printed)

| File | Source, licence | Result |
|---|---|---|
| iptc-board-signed.jpg | iptc.org sample | C2PA present but **Invalid: claimSignature.mismatch** (and signer untrusted); IPTC DigitalSourceType digitalCapture; EXIF Apple iPhone 12 mini |
| py-C.jpg | contentauth/c2pa-python fixtures, Apache-2.0 | Valid, `algorithmicMedia`, class other |
| org-adobe-CAI.jpg | c2pa-org/public-testfiles legacy 1.4, CC BY-SA 4.0 (test input only, not redistributed) | Valid edit chain, 2 ingredients, class other |
| py-E-sig-CA.jpg | c2pa-python fixtures | Invalid, claimSignature.mismatch |
| py-XCA.jpg | c2pa-python fixtures | Invalid, assertion.dataHash.mismatch |
| py-A.jpg | c2pa-python fixtures | no manifest; EXIF Canon EOS REBEL T3 |
| g22-pixel-capture.jpg | c2pa-org/public-testfiles PR #29 at ae61d4c (Google LLC, CC BY-SA 4.0; not merged to main) | **Trusted**, Pixel Camera, computationalCapture -> `signed_camera` |
| g22-photos-ai-edit.jpg | same | **Trusted**, Google Photos, compositeWithTrainedAlgorithmicMedia -> `signed_ai` |
| g22-ai-transcoded.jpg | same | **Trusted**, algorithmicallyEnhanced, AI in an ingredient -> `concerns` |
| made at test time | signed with the c2pa-python test cert | untrusted: `metadata`; with the test root added (test only) trainedAlgorithmicMedia -> `signed` ai, digitalCapture -> `signed` camera |
| stripped copies | Pillow re-encode of three samples | all rungs return [] |

The IPTC sample fails its claim signature (a reviewer verified the COSE
signature independently: it does not verify). It stays as a negative case; the
three Google samples above are the valid, trusted replacements.

## Run
```
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements-s2.txt
scripts/fetch_samples.sh
.venv/bin/python -m pytest -q
```
