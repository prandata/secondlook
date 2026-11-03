#!/usr/bin/env bash
# Prints the demo samples in samples/ (or the folder given), one name per line, sorted.
# samples/recorded/ holds the recorded Muse Glimmer runs (secondlook/web/recorded.py), not a demo
# sample, so it is left out. scripts/fetch_samples.sh counts with this and scripts/clean_clone_test.sh
# compares with it, so neither counts that folder as a tenth sample. Only a folder of that name is left
# out: a file named recorded is listed, so the clean-clone check still catches it (DAT-850 A228).
set -euo pipefail
dir="${1:-samples}"
names="$(ls "$dir")"
printf '%s\n' "$names" | while IFS= read -r name; do
  [ -n "$name" ] || continue
  if [ "$name" = recorded ] && [ -d "$dir/$name" ]; then continue; fi
  printf '%s\n' "$name"
done | sort
