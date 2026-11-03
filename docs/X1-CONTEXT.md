# X1: the context rung (DAT-872)

Status: in development, 28 Oct 2026, branch `x1-context` from `main` at `9d1c538`. WIN-PLAN feature F4 (a gap: verification desks check this by hand).

## What it does

`secondlook/rungs/context.py` holds the caption's date, place and device against the file's own facts:

| Claimed | The file's own fact | Disagrees when |
|---|---|---|
| Date (`2026-10-01` or `2026-10-01T09:30+10:00`) | EXIF `DateTimeOriginal` with `OffsetTimeOriginal`; the `exif:DateTimeOriginal` in a valid C2PA record's `stds.exif` assertion; the `when` on its `c2pa.created` action | More than 2 days apart. By the clock when both sides give a time with an offset, otherwise by calendar day (the file's wall-clock day against the claimed day). Each file time is compared on its own. |
| Place (`Australia`, `Texas`, `Paris, France`) | EXIF GPS position | Countries and first-level regions first (fix round, below). A country or region disagrees when the GPS position is within 25 km of a listed place outside it and the nearest listed place inside it is more than 50 km farther away. A city counts only when unambiguous (10 times the people of the next place of that name, or one left after the caption's country or region), and disagrees when more than 50 km away and the GPS position is clearly in another first-level region. Any reading that fits the GPS position agrees; an ambiguous name is not compared. Every distance allows for the precision the GPS fields were written with (half a step of the smallest part written, so whole degrees allow about 67 km at Paris's latitude) and any `GPSHPositioningError`. A `, qualifier` keeps only places in that country (name, ISO3, or a capitalised ISO2) or first-level region (`Texas`, `TX`); an unknown qualifier is ignored. |
| Device (`iPhone 15 Pro`) | EXIF `Make` (and `Model`, shown) | Both makes are recognised and not of one family. Families: Olympus and OM System; Ricoh and Pentax; Huawei, Honor and Leica; Leica with Xiaomi and with Panasonic; OnePlus, Oppo, realme and Hasselblad; Hasselblad and DJI; Sony and Minolta. A "Google Nexus" gives no make. |

A disagreement is one `metadata` card per field and file fact, with `raw["contradiction"] = True`. `ladder._is_concern` counts it (and only a boolean `True`). Agreement, or nothing to compare, is one `none` card that lists what was and was not compared. No context card can be `signed`: `_valid_c2pa` needs `rung == "c2pa"`, and a test pins that.

The rung returns nothing when it has no context, and with the default rungs the ladder does not run it without one, so every existing result is unchanged unless a caption or a claimed fact is given.

## The contract

- `types.py`: `"context"` added to `Evidence.rung`; new `ClaimFacts(date, place, device, source)` and `Context(caption=None, claims=[], facts=None)`.
- `ladder.run(path, rungs=None, context=None)` calls `check(path, context=context)` only on a rung whose signature has a `context` parameter or `**kwargs`; every other rung is still called `check(path)`.
- `secondlook/extract.py`: `Extractor = Callable[[str], ClaimFacts]`; `typed()` for typed facts (`source="typed"`), `rule_based()` for captions (`source="model"`), `merge()` (typed wins field by field; the source stays `"typed"` only when every fact given was typed). M2 (DAT-873) has no separate Glimmer extractor: its investigator keeps a fact only through the verbatim path (`verbatim_facts`, then `parse_date_text`, which is `find_date` here). `ambiguous_date()` finds an all-number date that reads day first or month first; no date is kept from it, and the context card says so.
- CLI: `secondlook check FILE --caption TEXT --claimed-date D --claimed-place P --claimed-device X`. Web: `POST /api/check` takes an optional `caption` form field (2,000 characters at most).

## Places

`scripts/fetch_geonames.sh` downloads GeoNames `cities5000.zip`, `countryInfo.txt` and `admin1CodesASCII.txt` (CC BY 4.0), checks their pinned sha256 and builds `~/.cache/secondlook/geonames.sqlite` (or `$SECONDLOOK_GEONAMES_DB`) with `python -m secondlook.places --build`. 69,978 places, 31 MB. Nothing is fetched at run time; without the index, or with one built before the fix round (schema 1), the card says the place list is not installed and compares nothing.

## Choices made here, for the reviewer

1. **The GPS position is never copied out**, only the distance, following the EXIF rung's rule. A desk might want the nearest town to the GPS point; that would put the location in the result, so it is left for the project lead to decide.
2. **A signed answer still outranks a caption contradiction** (rule order unchanged, as asked). On a trusted signed capture with a contradicting caption the answer is `signed_camera`, the contradiction card is listed, and the summary does not mention it. Whether the summary should is a ladder change for the project lead.
3. **The rule-based caption reader reports `source="model"`**, the contract's only non-typed value. The docstring says the facts were read by software.
4. **The GeoNames pin breaks daily.** GeoNames republishes every day, so a fresh download stops at the sha256 check. The script says so and takes `GEONAMES_ACCEPT_CITIES=<sha256>` / `GEONAMES_ACCEPT_COUNTRIES=<sha256>` to accept a newer file on purpose.
5. **The context rung joins only a check with a context.** It is in `rungs.CONTEXT_RUNGS`, not `DEFAULT_RUNGS`: `ladder.run` with the default rungs adds it only when `context` is given, and the web page does the same per request. A check with no caption gives exactly the result it gave before, including the limit card's list of checks. `/api/health` lists `context` as available.

## Fix round after a reviewer FAIL @ c7dae20 (28 Oct 2026)

the project lead's rules: a false concern from a truthful caption is the worst error, and a miss is acceptable.

- **F1, countries and states.** `places.lookup` matched "Australia" against city names and alternate names, found a town in Cuba and raised a concern on a Sydney photo. The index now also holds the country table and GeoNames first-level regions (`admin1CodesASCII.txt`, pinned and credited), and `places.Index.resolve` returns every reading of a name: countries, regions and cities. A country's name is read only as a country. The GPS position is placed by distances to listed places, not borders: it is outside a region only when a listed place outside is within 25 km and the nearest listed place inside is more than 50 km farther. Probed on the real index: every one of the 245 countries with a listed place, on its largest place, agrees; the 32 names in `tests/test_context.py` `REGION_NAMES` agree on a place inside and disagree on a far one; border and remote points (Tijuana, Point Roberts, Konstanz and Kreuzlingen, Llívia, Campione, Albury and Wodonga) agree, and Uluru, Saibai Island, Cambridge Bay and Qaanaaq are not placed.
- **Rule 2, cities.** A city counts only when unambiguous; then the rule is more than 50 km and another first-level region. Added for safety: any same-name place within 50 km of the GPS position agrees, so "Paris" from Paris, Texas and "Canton" from Canton, Ohio are no concern although Paris, France and Guangzhou dominate. When the name is also a region's, a city of that name blocks a contradiction only if one of 100,000 people or more exists (Washington, DC; Victoria, British Columbia), so "California" on a Sydney photo is a concern despite small Californias in Maryland and Brazil.
- **F2.** "Paris, Texas" narrows by the region's name; "Paris, TX" still works.
- **F3.** The card's last sentence is now "...and agreement doesn't show how the picture was made." CLAIMS.md's context section no longer says "genuine". Its line 25, from main, lists "genuine" among the claims the product never makes; that line is left as it is.
- **F4.** The place words are read in any case ("In Paris, France"). "From" is used only when no "in", "at", "near" or "outside" is followed by a place, because "visitors from India in Sydney" says where people come from. The first full date stays the rule, and the card says "(the first full date in the caption)".
- **Known misses, by design.** Bare "Victoria", "Rome" (GeoNames lists "Rome" as another name of Lomé, 2.2 million) and "Springfield" are ambiguous and not compared. A point with no listed place within 25 km is not placed. Codes count only in capitals, and two-letter codes only after a comma, so "Ben" is never Benin and "IT" alone is never Italy.
