#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "npm is required for the frontend" >&2; exit 1; }

uv venv .venv
uv pip install --python .venv/bin/python -r backend/requirements-dev.txt
(cd frontend && npm ci)
cp -n .env.example .env 2>/dev/null || true
if [[ "${MOSS_INSTALL_LOCAL_WORKER:-false}" == "true" ]]; then
  ./scripts/install_local_worker.sh
else
  echo "Skipped experimental native vLLM-Omni install. Set MOSS_INSTALL_LOCAL_WORKER=true to opt in."
fi
echo "Installed the local application. Review .env, then follow docs/INSTALLATION.md for Docker or native operation."
