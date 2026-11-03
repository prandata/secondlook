#!/usr/bin/env bash
# Clean-clone test: clone this repo's HEAD to a temp path and prove the quickstart works there.
#
#   pass 1  ./scripts/demo.sh --no-serve with a fresh, empty HOME (so empty uv, Hugging Face and weights
#           caches) and a scrubbed environment. The network is allowed: this is the judge's first run.
#   pass 2  under sandbox-exec with all outbound network denied except loopback, same HOME:
#           a control curl must fail; make test must pass; ./scripts/demo.sh --offline serves on
#           127.0.0.1; /api/health must list the real rungs (watermark included); each of the 9 demo samples must come back
#           from /api/check with its expected answer and a non-empty list of evidence cards.
#
# It fails on anything short of that: no samples, a missing sample, an unexpected extra file in
# samples/, a wrong answer, an empty card list, or a server that is not the real ladder.
# Only committed work is tested (a clone sees HEAD). The temp folder and the server it started are
# removed on exit. The Video Seal weights are fetched in pass 1 and the watermark rung is exercised in pass 2. macOS only (sandbox-exec).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || echo "note: uncommitted changes in $ROOT are not tested"
command -v sandbox-exec >/dev/null || { echo "sandbox-exec not found (macOS only)" >&2; exit 2; }
fail() { echo "FAIL: $*" >&2; exit 1; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/secondlook-clean.XXXXXX")"
CLONE="$TMP/second-look"
FRESH_HOME="$TMP/home"
SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then kill "$SERVER_PID"; wait "$SERVER_PID" 2>/dev/null || true; fi
  rm -rf "$TMP"
}
trap cleanup EXIT

mkdir "$FRESH_HOME"
PORT="$(/usr/bin/python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
# A scrubbed environment: only PATH, a fresh empty HOME and the port. No SECONDLOOK_* path override,
# so the weights land under the fresh HOME and nothing is reused from the real one.
CLEAN=(env -i PATH="$PATH" HOME="$FRESH_HOME" LANG=en_US.UTF-8 SECONDLOOK_PORT="$PORT")
NO_NET='(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))'
OFFLINE=(sandbox-exec -p "$NO_NET")

# file name, answer it must give. Answers are the ones in DEMO-SAMPLES/MANIFEST.md.
EXPECTED="01-camera-pixel10pro-atlantic-sign.jpg signed_camera
02-camera-pixel10pro-utah-sign.jpg signed_camera
03-ai-firefly-mountain-landscape.jpg signed_ai
04-ai-firefly-typography-logo.jpg signed_ai
05-ai-edit-photoshop-firefly-bentley.jpg signed_ai
06-stripped-pixel10pro-atlantic-sign.jpg cant_tell
07-stripped-firefly-mountain-landscape.jpg cant_tell
08-iptc-ai-label-firefly-mountain-landscape.jpg concerns
09-iptc-ai-label-firefly-typography-logo.jpg concerns"
N_EXPECTED=9

echo "== clone $HEAD_SHA -> $CLONE"
git clone -q --no-hardlinks "$ROOT" "$CLONE"
git -C "$CLONE" checkout -q --detach "$HEAD_SHA"
cd "$CLONE"
[ -z "$(ls -A "$FRESH_HOME")" ] || fail "the fresh HOME is not empty before pass 1"

echo "== pass 1: ./scripts/demo.sh --no-serve (fresh HOME $FRESH_HOME, scrubbed env, network allowed)"
"${CLEAN[@]}" ./scripts/demo.sh --no-serve

[ -d samples ] && [ ! -L samples ] || fail "samples/ is not a real folder"
want="$(printf '%s\n' "$EXPECTED" | cut -d' ' -f1 | sort)"
have="$(./scripts/list_samples.sh samples)"  # leaves out samples/recorded/, the recorded runs
[ "$have" = "$want" ] || { echo "samples/ holds:"; echo "$have"; fail "samples/ is not exactly the $N_EXPECTED expected demo files"; }
echo "ok   samples/ is a real folder with exactly the $N_EXPECTED demo files"
if grep -rIl 'PRIVATE KEY' samples >/dev/null 2>&1; then fail "a private key file is in samples/"; fi
[ -s "$FRESH_HOME/.cache/secondlook/models/commfor-model-384/model.safetensors" ] || fail "weights are not under the fresh HOME"
[ -s "$FRESH_HOME/.cache/secondlook/models/videoseal/y_256b_img.jit" ] || fail "Video Seal weights are not under the fresh HOME"
echo "ok   caches came from the fresh HOME (weights under $FRESH_HOME/.cache/secondlook)"

echo "== pass 2: network denied except loopback"
if "${CLEAN[@]}" "${OFFLINE[@]}" curl -sS --max-time 5 -o /dev/null https://huggingface.co 2>/dev/null; then
  fail "the sandbox let a control request out"
fi
echo "ok   control: outbound request refused inside the sandbox"

echo "== make test (offline)"
"${CLEAN[@]}" "${OFFLINE[@]}" make test 2>&1 | tee "$TMP/pytest.log" | tail -5
grep -Eq '[0-9]+ passed' "$TMP/pytest.log" || fail "make test reported no passing tests"
if grep -Eq '[0-9]+ (failed|error)' "$TMP/pytest.log"; then fail "make test reported failures"; fi
echo "ok   test count with fetched samples on a fresh HOME: $(grep -E '[0-9]+ passed' "$TMP/pytest.log" | tail -1)"

echo "== ./scripts/demo.sh --offline on 127.0.0.1:$PORT"
"${CLEAN[@]}" "${OFFLINE[@]}" ./scripts/demo.sh --offline --port "$PORT" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
# Wait for demo.sh's own "Ready:" line, as a judge does (the guide says so): it comes after demo.sh has checked the
# listener and finished its warm-up check. Until DAT-1236 D2, demo.sh died before that line inside the sandbox and
# this loop went on as soon as /api/health answered, so its first check ran beside the warm-up. 600 s is demo.sh's
# own two limits (300 s for /api/health, 300 s for the warm-up), elapsed time.
ready_by=$((SECONDS + 600))
until grep -q '^Ready: ' "$TMP/server.log" 2>/dev/null; do
  kill -0 "$SERVER_PID" 2>/dev/null || { cat "$TMP/server.log"; fail "demo.sh exited before Ready"; }
  [ "$SECONDS" -lt "$ready_by" ] || { cat "$TMP/server.log"; fail "no Ready line from demo.sh in 600 s"; }
  sleep 0.5
done
curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/health" >"$TMP/health.json" 2>/dev/null || true
[ -s "$TMP/health.json" ] || { cat "$TMP/server.log"; fail "no /api/health after Ready"; }
echo "ok   /api/health $(cat "$TMP/health.json")"
/usr/bin/python3 - "$TMP/health.json" <<'PY' || fail "/api/health is not the real ladder with the real rungs"
import json, sys
h = json.load(open(sys.argv[1]))
assert h["ok"] is True and h["engine"] == "ladder", h
missing = {"c2pa", "iptc", "exif", "classifier", "watermark"} - set(h["rungs"])
assert not missing, "rungs not loaded: %s" % sorted(missing)
PY
echo "ok   engine is the ladder and the c2pa, iptc, exif, classifier and watermark rungs are loaded"
page="$(curl -fsS "http://127.0.0.1:$PORT/")" || fail "GET / failed"
printf '%s' "$page" | grep -q '<html' || fail "/ did not serve the page"
echo "ok   / serves the page"

n=0
while read -r name answer; do
  f="samples/$name"
  [ -f "$f" ] || fail "missing sample $f"
  body="$(curl -fsS --max-time 120 -F "file=@$f" "http://127.0.0.1:$PORT/api/check")" || fail "/api/check on $f"
  printf '%s' "$body" | /usr/bin/python3 -c '
import json, sys
name, want = sys.argv[1], sys.argv[2]
r = json.load(sys.stdin)
assert r["answer"] == want, "%s: answer %r, expected %r" % (name, r["answer"], want)
assert r["demo"] is False and r["engine"] == "ladder", "%s: not the real ladder (demo=%r engine=%r)" % (name, r["demo"], r["engine"])
cards = r["evidence"]
assert isinstance(cards, list) and len(cards) > 0, "%s: no evidence cards" % name
for c in cards:
    assert c["does_not_prove"].strip(), "%s: a %s card has an empty does_not_prove" % (name, c["rung"])
print("ok   %-50s %-14s cards=%d (%s)" % (name, r["answer"], len(cards), ", ".join(c["rung"] for c in cards)))
' "$name" "$answer" || fail "check of $name"
  n=$((n + 1))
done <<EOF
$EXPECTED
EOF
[ "$n" -eq "$N_EXPECTED" ] || fail "checked $n samples, expected $N_EXPECTED"
echo "ok   $n of $N_EXPECTED samples gave the expected answer with a non-empty card list"

# The watermark extra, offline: mark a stripped sample with the CLI; the page must then read our mark on a
# watermark card and still say cant_tell. The unmarked original must show a "not found" card, not "could not run".
"${CLEAN[@]}" "${OFFLINE[@]}" .venv/bin/secondlook mark samples/06-stripped-pixel10pro-atlantic-sign.jpg "$TMP/marked.jpg" >"$TMP/mark.log" 2>&1 || { cat "$TMP/mark.log"; fail "secondlook mark"; }
for pair in "$TMP/marked.jpg True" "samples/06-stripped-pixel10pro-atlantic-sign.jpg False"; do
  f="${pair% *}"; want="${pair#* }"
  body="$(curl -fsS --max-time 120 -F "file=@$f" "http://127.0.0.1:$PORT/api/check")" || fail "/api/check on $f"
  printf '%s' "$body" | /usr/bin/python3 -c '
import json, sys
f, want = sys.argv[1], sys.argv[2] == "True"
r = json.load(sys.stdin)
assert r["answer"] == "cant_tell", "%s: answer %r, a mark must not change it" % (f, r["answer"])
cards = [c for c in r["evidence"] if c["rung"] == "watermark"]
assert len(cards) == 1, "%s: expected one watermark card, got %d" % (f, len(cards))
assert cards[0]["raw"]["present"] is want, "%s: mark present=%r, expected %r" % (f, cards[0]["raw"]["present"], want)
assert cards[0]["does_not_prove"].strip(), "%s: empty does_not_prove" % f
print("ok   watermark %-45s present=%s matched_bits=%d" % (f.rsplit("/", 1)[-1], cards[0]["raw"]["present"], cards[0]["raw"]["matched_bits"]))
' "$f" "$want" || fail "watermark check of $f"
done

kill "$SERVER_PID"; wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""
# demo.sh has exited, so its cleanup has stopped the server's whole process group. Prove it: nothing may still listen on
# the port (DAT-1236 D2: inside the sandbox ps could not run, cleanup skipped the group, and the server outlived this
# script). Up to 5 s for the socket to close after a KILL; then fail and name what is left, without stopping it.
LSOF="$(command -v lsof || true)"; [ -x /usr/sbin/lsof ] && LSOF=/usr/sbin/lsof
[ -n "$LSOF" ] || fail "lsof is required to confirm that the server stopped"
left=""
for _ in $(seq 1 25); do
  left="$("$LSOF" -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
  [ -z "$left" ] && break
  sleep 0.2
done
[ -z "$left" ] || fail "port $PORT is still listening after the stop (PID $(echo $left)); demo.sh did not stop its server"
echo "ok   the server stopped: nothing listens on port $PORT"
echo "PASS clean-clone test at $HEAD_SHA"
