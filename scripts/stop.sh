#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
set -a
[[ -f .env ]] && source .env
set +a
PORT="${MOSS_PORT:-8000}"

# Do not kill an unrelated service merely because it owns the same port or uses
# the same command line from another checkout. Match the command, port, and
# this repository's working directory.
if command -v pgrep >/dev/null 2>&1; then
  mapfile -t CANDIDATE_PIDS < <(pgrep -f -- "uvicorn app\.main:app --app-dir backend .*--port ${PORT}" || true)
  PIDS=()
  for pid in "${CANDIDATE_PIDS[@]}"; do
    process_cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
    [[ "$process_cwd" == "$ROOT_DIR" ]] || continue
    PIDS+=("$pid")
  done
  if ((${#PIDS[@]})); then
    kill "${PIDS[@]}"
    echo "Stopped Moss-Image-Generator uvicorn process(es): ${PIDS[*]}"
  else
    echo "No Moss-Image-Generator uvicorn process found for this checkout on port ${PORT}."
  fi
else
  echo "pgrep is unavailable; stop the Moss-Image-Generator uvicorn process manually."
fi
