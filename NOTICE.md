# Notice

Second Look is MIT-licensed (`LICENSE`). It uses the third-party works below. Full licence rows, versions, sources and checksums are in `docs/LICENSES.md`.

## Credits for material licensed CC BY

| Work | Licence | Where it appears |
|---|---|---|
| Data Commons ClaimReview feed ("Data Commons"), with each fact-check credited to its own publisher | CC BY, as stated on datacommons.org/factcheck/download | The claim rung names "Data Commons" and the fact-checker wherever a match is shown. The feed itself is not in this repository. |
| GeoNames `cities5000`, `countryInfo` and `admin1CodesASCII` ("GeoNames") | CC BY 4.0 | The context rung and its card name GeoNames. A 180-row excerpt of `cities5000` (`cities-mini.txt`) and the two small files are in `tests/fixtures/geonames/`, unchanged. |
| C2PA Trust List, from `c2pa-org/conformance-public` (C2PA) | CC BY 4.0 | `secondlook/trust/C2PA-TRUST-LIST.pem`, unmodified, with its source and sha256 in `secondlook/trust/SOURCE.md`. |
| Community Forensics public evaluation set (Park and Owens, CVPR 2025) | CC BY 4.0 | Research evaluation only. The images are not in this repository; only row ids, file hashes and aggregate results are. |

## Font

Playfair Display, SIL Open Font License 1.1, in `secondlook/web/static/fonts/` with its `OFL.txt`.

## Models and tools fetched at run time, not in this repository

Meta Video Seal (MIT), Muse Glimmer 30B (Apache-2.0), whisper.cpp (MIT), the OpenAI Whisper model in ggml form (MIT), Community Forensics weights (MIT) and llama.cpp (MIT). Their sources, pinned versions and checksums are in `docs/LICENSES.md`. Second Look is not affiliated with Meta or OpenAI.

## Not yet cleared

The IFCN signatories list behind `secondlook/rungs/factcheckers.py` has no licence or terms that we could find. See `docs/LICENSES.md`.
