#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -x .venv/bin/uvicorn ]] || { echo "Run ./scripts/install.sh first" >&2; exit 1; }

set -a
[[ -f .env ]] && source .env
set +a

STATIC_DIR="${MOSS_STATIC_DIR:-$ROOT_DIR/frontend/dist}"
if [[ ! -f "$STATIC_DIR/index.html" ]]; then
  if [[ "$STATIC_DIR" != "$ROOT_DIR/frontend/dist" ]]; then
    echo "Configured MOSS_STATIC_DIR has no index.html: $STATIC_DIR" >&2
    exit 1
  fi
  (cd frontend && npm run build)
fi
exec .venv/bin/uvicorn app.main:app --app-dir backend --host "${MOSS_HOST:-127.0.0.1}" --port "${MOSS_PORT:-8000}"
