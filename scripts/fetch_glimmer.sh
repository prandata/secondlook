#!/usr/bin/env bash
# Fetch Meta's own Muse Glimmer 30B GGUF files from Hugging Face, pinned to one revision and checked
# by sha256 (DAT-869 follow-up). The weights go to $GLIMMER_MODELS_DIR, never into this repo.
#
#   scripts/fetch_glimmer.sh          # the text model and the vision projector (about 18.2 GB)
#   scripts/fetch_glimmer.sh text     # the text model only (about 16.8 GB)
#   scripts/fetch_glimmer.sh pins     # the pinned repo, revision, files, sizes and sha256
#   scripts/fetch_glimmer.sh path     # where the text model goes (scripts/glimmer_server.sh reads it)
#
# Public files, no account and no token (the repo is not gated). The script refuses to start with
# less than $GLIMMER_MIN_FREE_GB (default 40) GB free where the files go. Each file downloads to
# <name>.part and is moved into place only after its sha256 matches the pin; a mismatch deletes the
# .part file. A file already in place with the pinned size is checked again and kept if it matches.
#
# Overrides: GLIMMER_MODELS_DIR (default ~/.cache/secondlook/models), GLIMMER_MIN_FREE_GB,
# GLIMMER_HF_BASE (another mirror of the same files; the sha256 pins still apply).
set -euo pipefail

REPO=meta-models/Muse-Glimmer-30B-GGUF
REV=b44f1072cf75e918a1b82a3a17b45c4389b076e7
TEXT_FILE=Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
TEXT_SIZE=16756683904
TEXT_SHA=4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e
MMPROJ_FILE=mmproj-Muse-Glimmer-30B-Q4_K_M.gguf
MMPROJ_SIZE=1400328928
MMPROJ_SHA=f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6

DIR="${GLIMMER_MODELS_DIR:-$HOME/.cache/secondlook/models}/$REPO@$REV"
BASE="${GLIMMER_HF_BASE:-https://huggingface.co/$REPO/resolve/$REV}"
MIN_FREE_GB="${GLIMMER_MIN_FREE_GB:-40}"

die() { echo "fetch_glimmer: $*" >&2; exit 1; }

sha_of() { /usr/bin/shasum -a 256 "$1" | awk '{print $1}'; }
size_of() { /usr/bin/stat -f %z "$1" 2>/dev/null || stat -c %s "$1"; }

free_gb() {
  local d="$1"
  while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
  df -Pk "$d" | awk 'NR == 2 {print int($4 / 1048576)}'
}

fetch_one() {
  local name="$1" size="$2" sha="$3" dest part got
  dest="$DIR/$name"; part="$dest.part"
  if [ -f "$dest" ] && [ "$(size_of "$dest")" = "$size" ]; then
    echo "checking $name ..."
    got="$(sha_of "$dest")"
    [ "$got" = "$sha" ] && { echo "ok: $dest"; return 0; }
    die "$dest is in place but its sha256 is $got, not the pinned $sha; move it away and run again"
  fi
  echo "downloading $name ($size bytes) from $BASE"
  # -C - resumes a .part file left by an earlier run; -L follows the Hub's redirect to its CDN.
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
    curl -fL --retry 3 --retry-delay 5 -C - -o "$part" "$BASE/$name" \
    || { rm -f "$part"; die "download of $name failed"; }
  got="$(sha_of "$part")"
  if [ "$got" != "$sha" ]; then
    rm -f "$part"
    die "sha256 mismatch for $name: got $got, pinned $sha (the .part file is deleted)"
  fi
  mv "$part" "$dest"
  echo "ok: $dest"
}

cmd_fetch() {
  local need_text="$1" need_mmproj="$2" free
  free="$(free_gb "$DIR")"
  [ "$free" -ge "$MIN_FREE_GB" ] || die "only $free GB free under $DIR; need at least $MIN_FREE_GB GB"
  mkdir -p "$DIR"
  [ "$need_text" = 1 ] && fetch_one "$TEXT_FILE" "$TEXT_SIZE" "$TEXT_SHA"
  [ "$need_mmproj" = 1 ] && fetch_one "$MMPROJ_FILE" "$MMPROJ_SIZE" "$MMPROJ_SHA"
  return 0
}

case "${1:-all}" in
  all) cmd_fetch 1 1 ;;
  text) cmd_fetch 1 0 ;;
  pins)
    echo "repo: $REPO"
    echo "revision: $REV"
    echo "text: $TEXT_FILE $TEXT_SIZE $TEXT_SHA"
    echo "mmproj: $MMPROJ_FILE $MMPROJ_SIZE $MMPROJ_SHA" ;;
  path) echo "$DIR/$TEXT_FILE" ;;
  mmproj-path) echo "$DIR/$MMPROJ_FILE" ;;
  text-size) echo "$TEXT_SIZE" ;;
  mmproj-size) echo "$MMPROJ_SIZE" ;;
  *) sed -n '2,17p' "$0"; exit 2 ;;
esac
