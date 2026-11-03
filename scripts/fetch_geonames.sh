#!/usr/bin/env bash
# Downloads GeoNames cities5000 (every place with 5,000 people or more), the country table and the
# first-level region names (admin1CodesASCII) into data/geonames/ (gitignored), checks each file
# against the sha256 pinned below, and builds the offline place index the context rung reads
# ($SECONDLOOK_GEONAMES_DB, default ~/.cache/secondlook/geonames.sqlite).
# Licence: CC BY 4.0, GeoNames (docs/LICENSES.md).
#
# GeoNames republishes these files every day, so a fresh download will not match the pins after the
# day they were taken. The script then stops. To accept a newer download after checking it, rerun
# with its hash: GEONAMES_ACCEPT_CITIES, GEONAMES_ACCEPT_COUNTRIES and/or GEONAMES_ACCEPT_ADMIN1=<sha256>.
set -euo pipefail
cd "$(dirname "$0")/.."
D=data/geonames
BASE=https://download.geonames.org/export/dump
# Pinned 28 Oct 2026 (cities5000.txt inside the zip is dated 2026-10-28).
PIN_CITIES=${GEONAMES_ACCEPT_CITIES:-2c3cd97e0e829001cdb951277e2d9f6a00e9b1abeae8b72fbdbdfb24799c347e}
PIN_COUNTRIES=${GEONAMES_ACCEPT_COUNTRIES:-5c710efde90ee4cca15001c9f6a97ae7a74c7020b365504913ddc3afeb6d3671}
# Pinned 28 Oct 2026 (Last-Modified 28 Oct 2026 02:56 GMT).
PIN_ADMIN1=${GEONAMES_ACCEPT_ADMIN1:-2e4867f5397acb056ea26377601b57af37cb4c81399770225018e9e439e91ad3}
DB=${SECONDLOOK_GEONAMES_DB:-$HOME/.cache/secondlook/geonames.sqlite}
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
mkdir -p "$D"
while read -r name pin; do
  [ -s "$D/$name" ] || { curl -fsSL --max-time 300 -o "$D/$name.part" "$BASE/$name" && mv "$D/$name.part" "$D/$name"; }
  got=$(shasum -a 256 "$D/$name" | cut -d' ' -f1)
  if [ "$got" = "$pin" ]; then echo "ok        $got  $D/$name"; else
    echo "MISMATCH  $got  $D/$name (pinned $pin)"
    echo "GeoNames republishes daily. Check the file, then rerun with GEONAMES_ACCEPT_<CITIES|COUNTRIES|ADMIN1>=$got"
    exit 1
  fi
done <<LIST
cities5000.zip $PIN_CITIES
countryInfo.txt $PIN_COUNTRIES
admin1CodesASCII.txt $PIN_ADMIN1
LIST
( cd "$D" && unzip -o -q cities5000.zip cities5000.txt )
"$PY" -m secondlook.places --build "$D/cities5000.txt" "$D/countryInfo.txt" "$D/admin1CodesASCII.txt" "$DB" \
  --source "$BASE/cities5000.zip, $BASE/countryInfo.txt and $BASE/admin1CodesASCII.txt" \
  --sha256 "cities5000.zip $PIN_CITIES; countryInfo.txt $PIN_COUNTRIES; admin1CodesASCII.txt $PIN_ADMIN1" \
  --downloaded-at "$(date -u -r "$D/cities5000.zip" +%Y-%m-%dT%H:%M:%SZ)"
