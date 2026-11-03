#!/usr/bin/env bash
# Fetches the C2PA interim trust list from contentcredentials.org into secondlook/trust/interim/
# (gitignored: the list states no licence, so it is fetched, not committed) and checks each file
# against the sha256 pinned in secondlook/rungs/c2pa.py (INTERIM_LIST). The C2PA rung loads the
# interim list only when both files match; otherwise it records the list as not loaded.
set -euo pipefail
cd "$(dirname "$0")/.."
D=secondlook/trust/interim
mkdir -p "$D"
while read -r name pin; do
  [ -s "$D/$name" ] || { curl -fsSL --max-time 60 -o "$D/$name.part" "https://contentcredentials.org/trust/$name" && mv "$D/$name.part" "$D/$name"; }
  got=$(shasum -a 256 "$D/$name" | cut -d' ' -f1)
  if [ "$got" = "$pin" ]; then echo "ok        $got  $D/$name"; else echo "MISMATCH  $got  $D/$name (pinned $pin)"; exit 1; fi
done <<LIST
anchors.pem db57e50b9bd4e4c786cbb91f38fd85e2af6a9d5412438a8cddcc42688df07ae5
allowed.sha256.txt 3597b20c9c20a0daadc7fab0d8c63534bd333ec6a715ef7117c7a4588caa15bb
LIST
