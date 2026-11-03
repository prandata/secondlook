#!/usr/bin/env bash
# Second Look (in development): one command that sets up and launches the local demo.
#
#   ./scripts/demo.sh                  set up, then serve on http://127.0.0.1:8765
#   ./scripts/demo.sh --offline        same, but never touch the network (needs one earlier run)
#   ./scripts/demo.sh --no-serve       set up only (used by make setup and the clean-clone test)
#
# Needs Apple silicon and macOS 14 or later (the pinned torch has wheels for nothing else), and uv.
#
# Steps, each skipped when already done:
#   1. .venv with Python 3.12 (uv)
#   2. pinned dependencies, every one hash-checked (requirements/build.lock for the build backend,
#      requirements/demo.lock, and requirements/classifier.lock when secondlook/rungs/classifier.py
#      is in the tree), then the package installed with --no-build-isolation so nothing else is fetched
#   3. model weights, each pinned and sha256-checked: Community Forensics (MIT, Hugging Face revision) and,
#      when secondlook/rungs/watermark.py is in the tree, Meta Video Seal 1.0 (MIT, 228 MB, for the watermark rung)
#   4. C2PA trust lists: the official list (CC BY 4.0) and the interim list, each by sha256
#   5. the 9 demo samples and the test fixtures, downloaded from their public sources (scripts/fetch_samples.sh,
#      each file checked by sha256) into samples/ and data/test-fixtures/
#   6. the web app on 127.0.0.1 only, with the "Try a sample" gallery, then one warm-up check so the first drop is quick
#
# Nothing here is paid, needs an account, or sends an image anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OFFLINE=0 SERVE=1 CLASSIFIER=auto WATERMARK=auto STUB=0
PORT="${SECONDLOOK_PORT:-8765}"
WEIGHTS="${SECONDLOOK_COMMFOR_WEIGHTS:-$HOME/.cache/secondlook/models/commfor-model-384/model.safetensors}"
SEAL_WEIGHTS="${SECONDLOOK_VIDEOSEAL_WEIGHTS:-$HOME/.cache/secondlook/models/videoseal/y_256b_img.jit}"

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; cat <<'EOF'
Options:
  --offline          no network: install from the uv cache, require weights and trust lists present
  --no-serve         stop after setup
  --no-classifier    skip the classifier dependencies (torch) even if the rung is present
  --no-watermark     skip the 228 MB Video Seal weights; on a cant_tell answer the page then lists the watermark check under "could not run"
  --stub             serve canned demo answers (marked as a demo), not the real ladder
  --port N           port on 127.0.0.1 (default 8765, or SECONDLOOK_PORT)
Environment: SECONDLOOK_COMMFOR_WEIGHTS, SECONDLOOK_VIDEOSEAL_WEIGHTS, SECONDLOOK_PORT.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --no-serve) SERVE=0 ;;
    --no-classifier) CLASSIFIER=0 ;;
    --no-watermark) WATERMARK=0 ;;
    --stub) STUB=1 ;;
    --port) PORT="${2:?--port needs a number}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "demo.sh: unknown option $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\n== %s\n' "$*"; }
die() { printf 'demo.sh: %s\n' "$*" >&2; exit 1; }
sha() { shasum -a 256 "$1" | cut -d' ' -f1; }

# fetch <url> <dest> <sha256>: keep dest if it already matches; otherwise download (unless offline),
# check the hash, and only then move it into place. A mismatch stops the script.
fetch() {
  local url="$1" dest="$2" pin="$3"
  if [ -s "$dest" ] && [ "$(sha "$dest")" = "$pin" ]; then
    echo "ok (cached)  $pin  $dest"; return 0
  fi
  [ "$OFFLINE" = 1 ] && die "offline and $dest is missing or does not match sha256 $pin; run once without --offline"
  mkdir -p "$(dirname "$dest")"
  echo "downloading $(basename "$dest") ..."
  # --progress-bar draws one line on stderr while the big weight files come down (87 MB and 228 MB); --max-time 600 as before.
  curl -fL --progress-bar --retry 2 --max-time 600 -o "$dest.part" "$url" || { rm -f "$dest.part"; die "download failed: $url"; }
  local got; got="$(sha "$dest.part")"
  [ "$got" = "$pin" ] || { rm -f "$dest.part"; die "sha256 mismatch for $url: got $got, pinned $pin"; }
  mv "$dest.part" "$dest"
  echo "ok (fetched) $pin  $dest"
}

for tool in uv curl shasum; do command -v "$tool" >/dev/null || die "$tool is required (uv: brew install uv)"; done
UV_NET=()
[ "$OFFLINE" = 1 ] && UV_NET=(--offline)

if [ "$CLASSIFIER" = auto ] && [ -f secondlook/rungs/classifier.py ]; then CLASSIFIER=1; fi
if [ "$CLASSIFIER" = 1 ]; then
  # requirements/classifier.lock pins torch 2.14.1, which PyPI ships only as macosx_14_0_arm64 for Mac.
  [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || die "this needs a Mac with Apple silicon (the pinned torch has no wheel for $(uname -s) $(uname -m))"
  mac_major="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
  [ "${mac_major:-0}" -ge 14 ] || die "this needs macOS 14 or later (found ${mac_major:-unknown}); the pinned torch has no wheel for older macOS"
fi

say "1/6 Python 3.12 virtual environment"
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  echo "ok (exists)  .venv ($(.venv/bin/python -V))"
else
  uv venv ${UV_NET[@]+"${UV_NET[@]}"} -q -p 3.12 .venv
  echo "ok (made)    .venv ($(.venv/bin/python -V))"
fi
PY="$ROOT/.venv/bin/python"

say "2/6 Pinned dependencies (every install hash-checked)"
# build.lock first: the build backend for the package install below, so no build step fetches it unhashed.
LOCKS=(requirements/build.lock requirements/demo.lock)
[ "$CLASSIFIER" = 1 ] && LOCKS+=(requirements/classifier.lock)
for lock in "${LOCKS[@]}"; do
  uv pip install ${UV_NET[@]+"${UV_NET[@]}"} -q -p "$PY" --require-hashes -r "$lock"
  echo "ok           $lock"
done
[ "$CLASSIFIER" = 1 ] || echo "skipped      requirements/classifier.lock (no classifier rung in this tree, or --no-classifier)"
# --no-build-isolation: build with the hash-locked setuptools above instead of fetching a fresh one.
uv pip install ${UV_NET[@]+"${UV_NET[@]}"} -q -p "$PY" --no-deps --no-build-isolation -e .
echo "ok           secondlook $(git rev-parse --short HEAD 2>/dev/null || echo '(no git)') installed editable"

say "3/6 Model weights (Community Forensics, MIT; Meta Video Seal, MIT)"
# Same pins as secondlook/rungs/classifier.py (MODEL_REVISION, MODEL_SHA256).
fetch "https://huggingface.co/OwensLab/commfor-model-384/resolve/6076002bf0d9dd37537f965ee2f06f826c333b61/model.safetensors" \
  "$WEIGHTS" b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387
export SECONDLOOK_COMMFOR_WEIGHTS="$WEIGHTS"
# The watermark rung is on by default (secondlook/rungs/__init__.py DEFAULT_RUNGS), so a missing file would
# put "watermark could not run" on every check a judge makes. Same pins as secondlook/rungs/watermark.py
# (MODEL_URL, MODEL_SHA256). It needs torch, which the classifier lock installs.
if [ "$WATERMARK" = auto ] && [ -f secondlook/rungs/watermark.py ] && [ "$CLASSIFIER" = 1 ]; then WATERMARK=1; fi
if [ "$WATERMARK" = 1 ]; then
  fetch "https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit" \
    "$SEAL_WEIGHTS" 5c7a4581c36fc6090aafdcfb3999123bae5172a4847f22e2da4e7fd1a39d1e1b
  export SECONDLOOK_VIDEOSEAL_WEIGHTS="$SEAL_WEIGHTS"
else
  echo "skipped      Video Seal weights (no watermark rung in this tree, --no-watermark, or --no-classifier)"
fi

say "4/6 C2PA trust lists"
# Same pins as secondlook/trust/SOURCE.md and INTERIM_LIST in secondlook/rungs/c2pa.py.
fetch "https://raw.githubusercontent.com/c2pa-org/conformance-public/47009eedfe6721edd0ce3e69b89c1101cd6f03e5/trust-list/C2PA-TRUST-LIST.pem" \
  secondlook/trust/C2PA-TRUST-LIST.pem b3949271a07b007e92ef1d18050b55388f5e0d2686a16fd864176c48421af8ae
fetch https://contentcredentials.org/trust/anchors.pem \
  secondlook/trust/interim/anchors.pem db57e50b9bd4e4c786cbb91f38fd85e2af6a9d5412438a8cddcc42688df07ae5
fetch https://contentcredentials.org/trust/allowed.sha256.txt \
  secondlook/trust/interim/allowed.sha256.txt 3597b20c9c20a0daadc7fab0d8c63534bd333ec6a715ef7117c7a4588caa15bb

say "5/6 Demo samples and test fixtures (public sources, every file checked by sha256)"
SAMPLE_ARGS=()
[ "$OFFLINE" = 1 ] && SAMPLE_ARGS=(--offline)
PY="$PY" ./scripts/fetch_samples.sh ${SAMPLE_ARGS[@]+"${SAMPLE_ARGS[@]}"}

if [ "$SERVE" = 0 ]; then
  say "Set up. Start it with: ./scripts/demo.sh --offline"
  exit 0
fi

say "6/6 Web app on http://127.0.0.1:$PORT (keep this terminal open; Ctrl-C stops it)"
ARGS=(--port "$PORT")
[ -d samples ] && ARGS+=(--samples "$ROOT/samples")   # the "Try a sample" gallery; the page hides it when this is missing
[ "$STUB" = 1 ] && ARGS+=(--stub)
[ "$OFFLINE" = 1 ] && export HF_HUB_OFFLINE=1

# The first check after a start loads the classifier and watermark models (about 30 s, more under load), so a judge's
# first drop would look hung. Start the server, wait until THIS script's server answers /api/health, run one check on
# a bundled sample over 127.0.0.1 (the result is thrown away), and only then say the page is ready.
LSOF="$(command -v lsof || true)"; [ -x /usr/sbin/lsof ] && LSOF=/usr/sbin/lsof
[ -n "$LSOF" ] || die "lsof is required to confirm that the page being served is this script's own server"
BASE="http://127.0.0.1:$PORT"
# listeners_on_port prints the PIDs listening on this TCP port on any address; listener_is_ours succeeds only if
# the server this script launched is one of them.
listeners_on_port() { "$LSOF" -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true; }
listener_is_ours() { listeners_on_port | grep -qx "$SERVER_PID"; }
[ -z "$(listeners_on_port)" ] || die "port $PORT is already in use by another process (PID $(listeners_on_port | head -1)); stop it or try --port N"

# Loopback only, whatever the shell has set: every curl below passes --noproxy '*' so http_proxy, ALL_PROXY and
# friends cannot route a request to this page (or sample 06 in the warm-up) through an outside proxy. The server
# itself never needs the network (the C2PA rung has remote fetches switched off), so it runs with the proxy
# variables removed (all spellings) and, with --offline, with the Hugging Face libraries forced offline too.
SERVER_ENV=(env -u http_proxy -u https_proxy -u all_proxy -u ftp_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u FTP_PROXY
  NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1)
[ "$OFFLINE" = 1 ] && SERVER_ENV+=(HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1)

SERVER_PID="" WARM_PID="" PGID=""
# group_alive: succeeds while any live (not zombie) process is in the server's process group. /bin/ps is setuid root,
# and sandbox-exec refuses to run it ("execvp() of 'ps' failed: Operation not permitted"), so when ps cannot run this
# falls back to kill -0 on the group. On macOS that fails for a group whose only members are zombies, so both agree
# (DAT-1236 D2: with no fallback, group_alive was always false in the sandbox and cleanup left the server running).
group_alive() {
  local out
  if out="$(ps -ax -o pgid=,stat= 2>/dev/null)"; then
    printf '%s\n' "$out" | awk -v g="$PGID" '$1 == g && $2 !~ /^Z/ { n++ } END { exit !n }'
  else
    kill -0 -- "-$PGID" 2>/dev/null
  fi
}
# server_group: prints the server's process group id, or nothing if the server is gone. Under sandbox-exec ps cannot
# run, and under set -e and pipefail the old `ps | tr` here ended this script before the server was up (DAT-1236 D2).
# Without ps: a group id is its leader's PID and is not reused while the group lives, so if kill -0 reaches group
# $SERVER_PID, that is the server's own group; if only the server itself answers, it is in another group.
server_group() {
  local out
  if out="$(ps -o pgid= -p "$SERVER_PID" 2>/dev/null)"; then
    printf '%s\n' "$out" | tr -d ' '
  elif kill -0 -- "-$SERVER_PID" 2>/dev/null; then
    printf '%s\n' "$SERVER_PID"
  elif kill -0 "$SERVER_PID" 2>/dev/null; then
    printf 'unknown\n'
  fi
  return 0
}
# One cleanup for every way out (normal end, die, Ctrl-C, TERM, set -e). The server runs as the leader of its own
# process group (see the launch below), so this signals that whole group and nothing outside it: TERM first, up to
# 5 s for the group to empty (uvicorn waits for a check still running in a worker thread), then KILL for whatever is
# left. Children and grandchildren are in the group, so none is left behind, whether the server exits first or not.
# The warm-up check it started is stopped too, so a stuck warm-up never holds the terminal.
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  set +e   # a failing kill or wait below must not replace the exit code this script is leaving with
  [ -n "$WARM_PID" ] && kill "$WARM_PID" 2>/dev/null
  if [ -n "$PGID" ] && group_alive; then
    kill -TERM -- "-$PGID" 2>/dev/null
    local stop_by=$((SECONDS + 5))   # elapsed time, not a loop count: a loaded machine slows each sleep
    while group_alive && [ "$SECONDS" -lt "$stop_by" ]; do sleep 0.2; done
    if group_alive; then
      echo "demo.sh: the server did not stop within 5 s; killing it" >&2
      kill -KILL -- "-$PGID" 2>/dev/null
    fi
  fi
  [ -n "$SERVER_PID" ] && wait "$SERVER_PID" 2>/dev/null   # reap it; until now a dead leader keeps its group id from being reused
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Job control (set -m) puts a background job in a new process group whose id is its PID (macOS has no setsid by
# default). Switched off again right after, so the warm-up curl below stays in this script's group. Standard input is
# /dev/null because a background group that reads the terminal is stopped. A terminal's Ctrl-C reaches only the
# foreground group (this script), so the INT trap above is what stops the server's group.
set -m
"${SERVER_ENV[@]}" "$PY" -m secondlook.web "${ARGS[@]}" </dev/null &
SERVER_PID=$!
set +m
PGID="$SERVER_PID"
# Never signal a group this script does not own: the server must be the leader of a group of its own. If it is gone
# already the checks below report it; if it is alive in some other group, stop it by PID only and refuse to go on.
server_pgid="$(server_group)"
if [ -n "$server_pgid" ] && [ "$server_pgid" != "$SERVER_PID" ]; then
  PGID=""
  kill -KILL "$SERVER_PID" 2>/dev/null
  die "could not start the server in its own process group (group $server_pgid, expected $SERVER_PID)"
fi
echo "Loading the models, about 30 seconds on first start"
# "Up" means: the launched server is still running, answers /api/health, and is the listener on the port. A busy
# port makes the launched server exit (checked first, every round), and a foreign listener that answers is refused.
# The 300 s limit is elapsed time (bash's SECONDS), not a count of attempts.
up=0 deadline=$((SECONDS + 300))
while [ "$SECONDS" -lt "$deadline" ]; do
  kill -0 "$SERVER_PID" 2>/dev/null || { wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""; die "the server stopped before it answered (is port $PORT already in use? try --port N); not ready"; }
  if curl -fsS --noproxy '*' --max-time 2 "$BASE/api/health" >/dev/null 2>&1; then
    kill -0 "$SERVER_PID" 2>/dev/null || continue   # it died just now: the next round reports it
    listener_is_ours || die "something other than this script's server is answering on port $PORT; not ready"
    up=1; break
  fi
  sleep 0.5
done
[ "$up" = 1 ] || die "no answer from $BASE/api/health within 300 s"
WARM="samples/06-stripped-pixel10pro-atlantic-sign.jpg"
if [ "$STUB" = 1 ]; then
  :   # the stub loads no models, nothing to warm
elif [ -f "$WARM" ]; then
  # In the background and waited for, so a signal reaches the trap now instead of after a stuck warm-up's 300 s.
  curl -fsS --noproxy '*' --max-time 300 -o /dev/null -F "file=@$WARM" "$BASE/api/check" &
  WARM_PID=$!
  wait "$WARM_PID" || echo "note: the warm-up check did not finish; the first check on the page may be slow"
  WARM_PID=""
else
  echo "note: $WARM is missing, so the models were not warmed up; the first check on the page may be slow"
fi
kill -0 "$SERVER_PID" 2>/dev/null || { wait "$SERVER_PID" 2>/dev/null || true; SERVER_PID=""; die "the server stopped during the warm-up; not ready"; }
echo "Ready: open $BASE (keep this terminal open; Ctrl-C stops it)"
echo "Drop any file from samples/ on the page, or pick one under \"Try a sample\"."
wait "$SERVER_PID"
