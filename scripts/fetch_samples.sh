#!/usr/bin/env bash
# Fills two real folders from public sources. Nothing is bundled or redistributed, and every file is
# checked against a sha256 pinned here, so a changed or swapped file stops the script.
#
#   samples/             the 9 demo samples a judge drops on the page (gitignored)
#                        01 to 05 are downloaded; 06 to 09 are made from them by scripts/derive_samples.py
#   data/test-fixtures/  public inputs the test suite reads (gitignored): C2PA and IPTC test files and
#                        the c2pa-python public test certificate and key. The key is a published test
#                        key; it never goes into samples/.
#
#   scripts/fetch_samples.sh              fetch whatever is missing or does not match
#   scripts/fetch_samples.sh --offline    no network: every file must already be present and match
#   scripts/fetch_samples.sh --demo-only  skip the test fixtures
#
# Licences of the demo samples (as stated on each Wikimedia Commons file page on 28 Oct 2026):
#   01 CC BY-SA 4.0, Mark Clifton (WashuOtaku), via Flickr     02 CC BY 4.0, Bobjgalindo
#   03, 04 public domain (PD-algorithm), Adobe Firefly Image 3, prompted by Jiri Vedral
#   05 CC BY-SA 4.0, Thilo Parg, own work and Adobe Firefly AI
#   06 to 09 are changed copies of 01, 03 and 04 made on your machine; they carry those licences.
# Sources, dates and what each file shows: DEMO-SAMPLES MANIFEST.md (docs/LICENSES.md summarises it).
# Python with Pillow is needed for the derived files: PY (default .venv/bin/python).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OFFLINE=0 FIXTURES=1
for a in "$@"; do
  case "$a" in
    --offline) OFFLINE=1 ;;
    --demo-only) FIXTURES=0 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "fetch_samples.sh: unknown option $a" >&2; exit 2 ;;
  esac
done

PY="${PY:-$ROOT/.venv/bin/python}"
UA="SecondLook/0.1 (local research tool; fetches public sample files)"
die() { printf 'fetch_samples.sh: %s\n' "$*" >&2; exit 1; }
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

# A folder that is a link would let a download land somewhere else (it did once: a link into the
# owner's folders). Refuse links, and make the folders as real directories.
for d in samples data data/test-fixtures; do
  [ ! -L "$d" ] || die "$d is a symbolic link; remove it (rm $d) and run this again"
done

# get <url> <dest> <sha256>: keep dest if it matches; else download (unless offline), check, move into place.
get() {
  local url="$1" dest="$2" pin="$3"
  if [ -f "$dest" ] && [ ! -L "$dest" ] && [ "$(sha "$dest")" = "$pin" ]; then
    printf 'ok (cached)  %s  %s\n' "$pin" "$dest"; return 0
  fi
  [ "$OFFLINE" = 0 ] || die "offline and $dest is missing or does not match sha256 $pin; run once without --offline"
  rm -f "$dest"
  curl -fsSL -A "$UA" --retry 5 --retry-delay 10 --max-time 300 -o "$dest.part" "$url" || { rm -f "$dest.part"; die "download failed: $url"; }
  local got; got="$(sha "$dest.part")"
  [ "$got" = "$pin" ] || { rm -f "$dest.part"; die "sha256 mismatch for $url: got $got, pinned $pin"; }
  mv "$dest.part" "$dest"
  printf 'ok (fetched) %s  %s\n' "$pin" "$dest"
}

# check <dest> <sha256>: for a file made locally.
check() {
  [ -f "$1" ] && [ "$(sha "$1")" = "$2" ] || die "$1 is missing or does not match sha256 $2"
  printf 'ok (derived) %s  %s\n' "$2" "$1"
}

W=https://upload.wikimedia.org/wikipedia/commons
mkdir -p samples
echo "== demo samples -> samples/"
get $W/3/30/260906-0903_Atlantic.jpg                                   samples/01-camera-pixel10pro-atlantic-sign.jpg    12583a7485499524f93c0f06b9a74cd3d5f7fdb38841e3cfc930695a8b08977a
get $W/a/a9/A_welcome_sign_Idaho_Utah_border.jpg                       samples/02-camera-pixel10pro-utah-sign.jpg        fbb1bb7183dc018e092ec3dda7a565543e605f25fe78d21818dbf61a888800d5
get $W/a/ab/Adobe_Firefly_generated_landscape_photography.jpg          samples/03-ai-firefly-mountain-landscape.jpg      4413739275382578d694dd02817c79c3714dafc6e5aa77ae8e80f59161618338
get $W/b/b7/Adobe_Firefly_generated_typography_logo.jpg                samples/04-ai-firefly-typography-logo.jpg         5550fa2572d435234d6a750ce93b21a711cccd9c650dd1cdd6d400fb1351b3b6
get $W/c/cd/Bentley_4.5_Litre_1927_YU_5837_2a.jpg                      samples/05-ai-edit-photoshop-firefly-bentley.jpg  cb5a122e070e7601b222b3ffb39d5914e4db27d1dcb94d4c4a623e0f6adca8af

DERIVED="06-stripped-pixel10pro-atlantic-sign.jpg 135f4578afd3a0fcd3c1d0f254d42a0d66e5a733954b913f39e2a8974522257b
07-stripped-firefly-mountain-landscape.jpg e7828da31f47369aeaa12e62227f1f37ff9779ba54e747cce02fef87e82502b6
08-iptc-ai-label-firefly-mountain-landscape.jpg 70f5070988eeca4669ca22f512e531938e5ad54b0cc6fc4d2001e53a289d5936
09-iptc-ai-label-firefly-typography-logo.jpg 52d331a1b22c9444241a4b28714614ca7b48578e7edad83215661d68ead44f50"
need_derive=0
while read -r name pin; do
  { [ -f "samples/$name" ] && [ ! -L "samples/$name" ] && [ "$(sha "samples/$name")" = "$pin" ]; } || need_derive=1
done <<EOF
$DERIVED
EOF
if [ "$need_derive" = 1 ]; then
  [ -x "$PY" ] || die "$PY not found; run ./scripts/demo.sh (it makes the venv first) or set PY"
  "$PY" -c 'import PIL' 2>/dev/null || die "Pillow is not installed in $PY"
  "$PY" scripts/derive_samples.py samples
fi
while read -r name pin; do check "samples/$name" "$pin"; done <<EOF
$DERIVED
EOF

[ "$FIXTURES" = 1 ] || exit 0

# Pinned to commits, not to branch heads. The IPTC file has no version in its URL, so its hash is the pin.
PYC=https://raw.githubusercontent.com/contentauth/c2pa-python/5897963a044a57268e43ece7090bcdf155546b5a/tests
ORG=https://raw.githubusercontent.com/c2pa-org/public-testfiles/22beccc075707475b038d8789d0136c009e43143/legacy/1.4/image/jpeg
# Google-contributed C2PA 2.2 conformance samples, signed by certificates on the C2PA Trust List.
# From PR c2pa-org/public-testfiles#29, pinned to its head commit (not merged to main when pinned).
G22=https://raw.githubusercontent.com/c2pa-org/public-testfiles/ae61d4c5cee15cf641cd9455916c75db81753567/2.2/image/good/jpeg
F=data/test-fixtures
mkdir -p "$F"
echo "== test fixtures -> $F/"
# name, url, licence of the source (Apache-2.0 contentauth/c2pa-python; CC BY-SA 4.0 c2pa-org/public-testfiles)
get https://iptc.org/std/MediaProvenance/SampleContent/IPTC/iptc-board-signed.jpg $F/iptc-board-signed.jpg d2f16f8f3c2ad53b0ab2d22623505b4418cd15b62f538909dbb1c53e7241621d
get $PYC/fixtures/C.jpg                                  $F/py-C.jpg                 4cbac1af485155f878d12a610da851c2938f2ce886189c61d9da7a0a43bd00b5
get $PYC/fixtures/A.jpg                                  $F/py-A.jpg                 f999fd78bfe8a83c96e468a078830ba94485bc1bc6fd086fb94a43bd29dd0f23
get $PYC/fixtures/files-for-reading-tests/CA.jpg         $F/py-CA.jpg                e71bff58fc57640803e6e65f7534e2fb0c2f99018c85276cc14b30f04427cc76
get $PYC/fixtures/files-for-reading-tests/E-sig-CA.jpg   $F/py-E-sig-CA.jpg          20dc072a4c843d3b7d7cb4f4002fbb4d8f20d7ee859ca726f2458c1e0358a98c
get $PYC/fixtures/files-for-reading-tests/XCA.jpg        $F/py-XCA.jpg               81f20702a2e81611b29aa773f811b59ade6095acd3d089d6ffdae6b591d6d5a5
get $PYC/fixtures/es256_certs.pem                        $F/py-es256_certs.pem       f18a293626ac261b18201e269ee0ed9c10bc30afb3b5c8708bb16e41a05bf040
get $PYC/fixtures/es256_private.key                      $F/py-es256_private.key     f547ec8dbf24e50b51cf9924e58c250d246ed8844304408a072801fcf70a95d7
get $PYC/trust_config_test_settings.json                 $F/py-trust_config_test_settings.json 311d9704a5f2999f57e65bc874cac4d118f1bfd8c2fe04d8f0c78a4779c2fd67
get $ORG/adobe-20220124-CAI.jpg                          $F/org-adobe-CAI.jpg        37ac97882b8d9371d8a5a781ff9434804b8c19addda0048251a6f64ea0a0c40f
get $G22/c.jpg                                           $F/g22-pixel-capture.jpg    01f236f0f49cd7f3ab182fa6996dfa31d920ef5d54366c185951a000a88b6a2f
get $G22/b.jpg                                           $F/g22-photos-ai-edit.jpg   d949c56121ddbbc39ce05c8520816e531281aedb9d6ab345bb31db044fcacaeb
get $G22/a.jpg                                           $F/g22-ai-transcoded.jpg    1994444eeec5d30a90e52f793f2764aa51e164a10334c60b991444369ccd30c6
# The same two Commons files as demo samples 02 and 03, under the names the tests use.
get $W/a/a9/A_welcome_sign_Idaho_Utah_border.jpg         $F/commons-pixel10pro-utah-sign.jpg        fbb1bb7183dc018e092ec3dda7a565543e605f25fe78d21818dbf61a888800d5
get $W/a/ab/Adobe_Firefly_generated_landscape_photography.jpg $F/commons-firefly-mountain-landscape.jpg 4413739275382578d694dd02817c79c3714dafc6e5aa77ae8e80f59161618338
echo "ok   $(./scripts/list_samples.sh samples | wc -l | tr -d ' ') demo samples, $(ls $F | wc -l | tr -d ' ') test fixtures"
