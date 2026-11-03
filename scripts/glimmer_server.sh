#!/usr/bin/env bash
# Start, stop and check a local Meta Muse Glimmer server (llama.cpp llama-server) on loopback only
# (DAT-869). By default it loads Meta's own GGUF files, pinned and sha256-checked by
# scripts/fetch_glimmer.sh; if they are not fetched, it falls back to the Ollama-store copy with a
# warning. The weights never go in this repo. Ollama itself is not used: Ollama.app 0.40.8 kept an
# outbound HTTPS connection open while serving on loopback.
#
#   scripts/glimmer_server.sh start           # llama-server on 127.0.0.1:$GLIMMER_PORT (default 8081)
#   scripts/glimmer_server.sh status          # pid, port, health
#   scripts/glimmer_server.sh egress [N]      # N one-second lsof samples of the server's sockets
#                                             # (GLIMMER_EGRESS_STOP=<file>: stop when it appears)
#   scripts/glimmer_server.sh stop            # stops the pid this script recorded, nothing else
#   scripts/glimmer_server.sh paths           # the model and projector files it would load
#
# Overrides: GLIMMER_MODEL, GLIMMER_MMPROJ (other GGUF paths), GLIMMER_MODELS_DIR (where
# fetch_glimmer.sh put Meta's files), GLIMMER_PORT, GLIMMER_CTX,
# GLIMMER_STATE_DIR, GLIMMER_NO_VISION=1 (text only), LLAMA_SERVER (binary).
#
# The egress check supports one claim only: "the model server process opened no non-loopback
# network connection while it was sampled". It says nothing about other processes on the machine.
set -euo pipefail

PORT="${GLIMMER_PORT:-8081}"
HOST=127.0.0.1
CTX="${GLIMMER_CTX:-16384}"
STATE="${GLIMMER_STATE_DIR:-$HOME/.cache/secondlook/glimmer}"
PIDFILE="$STATE/llama-server-$PORT.pid"
LOG="$STATE/llama-server-$PORT.log"
LSOF=/usr/sbin/lsof
BIN="${LLAMA_SERVER:-$(command -v llama-server || true)}"
MANIFEST="$HOME/.ollama/models/manifests/registry.ollama.ai/library/muse-glimmer/latest"

die() { echo "glimmer_server: $*" >&2; exit 1; }

# The two blobs named in the Ollama manifest: the text model and the vision projector.
ollama_blob() {
  [ -f "$MANIFEST" ] || return 0
  /usr/bin/python3 - "$MANIFEST" "$1" <<'PY'
import json, os, sys
m = json.load(open(sys.argv[1]))
for layer in m.get("layers", []):
    if layer.get("mediaType") == "application/vnd.ollama.image." + sys.argv[2]:
        print(os.path.expanduser("~/.ollama/models/blobs/") + layer["digest"].replace(":", "-"))
        break
PY
}

FETCH="$(dirname "$0")/fetch_glimmer.sh"

# Meta's own file, pinned by scripts/fetch_glimmer.sh, when it is in place with the pinned size (the
# fetch script moves a file into place only after its sha256 matches). Otherwise the Ollama-store
# copy, with a warning: its text model is not byte-identical to Meta's file (docs/LICENSES.md).
pinned_or_ollama() {
  local kind="$1" pinned size
  pinned="$(bash "$FETCH" "$2")"; size="$(bash "$FETCH" "$3")"
  if [ -f "$pinned" ] && [ "$(/usr/bin/stat -f %z "$pinned" 2>/dev/null || stat -c %s "$pinned")" = "$size" ]; then
    echo "$pinned"; return 0
  fi
  echo "glimmer_server: warning: Meta's pinned $kind file is not in place ($pinned);" \
       "falling back to the Ollama-store copy. Run scripts/fetch_glimmer.sh to use Meta's file." >&2
  ollama_blob "$4"
}

model_path() { if [ -n "${GLIMMER_MODEL:-}" ]; then echo "$GLIMMER_MODEL"; else pinned_or_ollama "text model" path text-size model; fi; }
mmproj_path() { if [ -n "${GLIMMER_MMPROJ:-}" ]; then echo "$GLIMMER_MMPROJ"; else pinned_or_ollama "vision projector" mmproj-path mmproj-size projector; fi; }

running_pid() {
  [ -f "$PIDFILE" ] || return 1
  local pid; pid="$(cat "$PIDFILE")"
  # Only our own server: the recorded pid, still llama-server, still on our port.
  ps -p "$pid" -o command= 2>/dev/null | grep -q -- "llama-server.*--port $PORT" || return 1
  echo "$pid"
}

health() { curl --noproxy '*' -fsS --max-time 3 "http://$HOST:$PORT/health" >/dev/null 2>&1; }

cmd_paths() {
  echo "model:  $(model_path)"
  echo "mmproj: $(mmproj_path)"
  echo "server: ${BIN:-not found}"
}

cmd_start() {
  [ -n "$BIN" ] && [ -x "$BIN" ] || die "llama-server not found (brew install llama.cpp, build b10353 or newer)"
  local model mmproj; model="$(model_path)"; mmproj="$(mmproj_path)"
  [ -s "$model" ] || die "model GGUF not found: '$model' (pull muse-glimmer in Ollama or set GLIMMER_MODEL)"
  if pid="$(running_pid)"; then echo "already running: pid $pid on $HOST:$PORT"; return 0; fi
  if "$LSOF" -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then die "port $PORT is taken by another process"; fi
  mkdir -p "$STATE"
  local vision=()
  if [ "${GLIMMER_NO_VISION:-}" != 1 ]; then
    [ -s "$mmproj" ] || die "vision projector not found: '$mmproj' (or set GLIMMER_NO_VISION=1)"
    vision=(--mmproj "$mmproj")
  fi
  # No proxy variables, offline mode, no web UI, loopback bind.
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
    HF_HUB_OFFLINE=1 \
    nohup "$BIN" -m "$model" "${vision[@]}" -a muse-glimmer \
      --host "$HOST" --port "$PORT" --offline --no-webui --jinja \
      -ngl 99 -c "$CTX" -np 1 \
      >"$LOG" 2>&1 </dev/null &
  echo $! >"$PIDFILE"
  local i
  for i in $(seq 1 240); do
    health && { echo "running: pid $(cat "$PIDFILE") on http://$HOST:$PORT (log $LOG)"; return 0; }
    running_pid >/dev/null || { tail -20 "$LOG" >&2; die "llama-server exited during start"; }
    sleep 1
  done
  die "llama-server did not report healthy within 240 s (log $LOG)"
}

cmd_stop() {
  local pid
  if pid="$(running_pid)"; then kill "$pid"; echo "stopped pid $pid"; else echo "not running"; fi
  rm -f "$PIDFILE"
}

cmd_status() {
  local pid
  if pid="$(running_pid)"; then
    echo "pid $pid on $HOST:$PORT, health: $(health && echo ok || echo not-ready)"
  else
    echo "not running"; return 1
  fi
}

# A line of `lsof -nP -i` output is loopback when every address in its NAME column is 127.0.0.0/8
# or ::1 (a LISTEN line has one address, a connection two). Anything else, including a wildcard
# `*:port` or a UDP socket, counts as non-loopback.
is_loopback_line() {
  local a ok=1
  for a in $(echo "$1" | awk '{print $9}' | tr '>' '\n' | sed 's/-$//'); do
    case "$a" in 127.*:*|\[::1\]:*) ;; *) ok=0 ;; esac
  done
  [ "$ok" = 1 ]
}

# lsof lines on stdin -> {"loopback_lines": n, "non_loopback_lines": n}; lines that are not lsof
# socket lines (headers, timestamps) are skipped. Used by the tests as a negative control.
cmd_classify() {
  local line loop=0 other=0
  while IFS= read -r line; do
    echo "$line" | awk '{exit !($8 == "TCP" || $8 == "UDP")}' || continue
    if is_loopback_line "$line"; then loop=$((loop + 1)); else other=$((other + 1)); fi
  done
  printf '{"loopback_lines": %s, "non_loopback_lines": %s}\n' "$loop" "$other"
}

cmd_egress() {
  local n="${1:-30}" pid out="$STATE/egress-$PORT-$(date -u +%Y%m%dT%H%M%SZ).log"
  pid="$(running_pid)" || die "server not running"
  local loop=0 other=0 samples=0 i line
  for i in $(seq 1 "$n"); do
    # GLIMMER_EGRESS_STOP: a file whose appearance ends sampling early (a caller's run is over).
    [ -n "${GLIMMER_EGRESS_STOP:-}" ] && [ -e "$GLIMMER_EGRESS_STOP" ] && break
    running_pid >/dev/null || die "server exited during sampling"
    samples=$((samples + 1))
    while IFS= read -r line; do
      [ -z "$line" ] && continue
      echo "$(date -u +%H:%M:%S) $line" >>"$out"
      if is_loopback_line "$line"; then loop=$((loop + 1)); else other=$((other + 1)); fi
    done < <("$LSOF" -nP -i -a -p "$pid" 2>/dev/null | tail -n +2)
    [ "$i" -lt "$n" ] && sleep 1
  done
  printf '{"pid": %s, "samples": %s, "loopback_lines": %s, "non_loopback_lines": %s, "log": "%s"}\n' \
    "$pid" "$samples" "$loop" "$other" "$out"
  [ "$other" -eq 0 ]
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  egress) shift; cmd_egress "${1:-30}" ;;
  paths) cmd_paths ;;
  classify) cmd_classify ;;
  *) sed -n '2,22p' "$0"; exit 2 ;;
esac
