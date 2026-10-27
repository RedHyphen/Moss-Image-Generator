#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -x .venv/bin/uvicorn ]] || { echo "Run ./scripts/install.sh first" >&2; exit 1; }
trap 'kill 0' INT TERM EXIT
set -a
[[ -f .env ]] && source .env
set +a

# Development defaults are loopback-only. Override these when testing a
# browser against a deliberately configured private-network bind.
DEV_HOST="${MOSS_DEV_HOST:-127.0.0.1}"
DEV_PORT="${MOSS_DEV_PORT:-8000}"
DEV_API_URL="${MOSS_DEV_API_URL:-http://${DEV_HOST}:${DEV_PORT}}"
DEV_FRONTEND_HOST="${MOSS_DEV_FRONTEND_HOST:-127.0.0.1}"
export MOSS_HOST="$DEV_HOST" MOSS_PORT="$DEV_PORT" MOSS_DEV_API_URL="$DEV_API_URL"

.venv/bin/uvicorn app.main:app --app-dir backend --host "$DEV_HOST" --port "$DEV_PORT" &
(cd frontend && npm run dev -- --host "$DEV_FRONTEND_HOST") &
wait
