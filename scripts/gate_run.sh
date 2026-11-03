#!/usr/bin/env bash
# The Day-3 gate run as one command (DAT-852). Offline, free. See docs/GATE-RUN.md.
#   scripts/gate_run.sh --l4-dir <L4 folder> --dry-run     # sizes, steps and checks; changes nothing
#   scripts/gate_run.sh --l4-dir <L4 folder>               # the run
# Exit: 0 PASS, 1 FAIL, 3 INVALID, 2 refused (nothing written), 4 crashed.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${SECONDLOOK_PYTHON:-.venv/bin/python}" -m secondlook.gate_run "$@"
