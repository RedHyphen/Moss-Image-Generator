#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env.docker ]] || { echo "No .env.docker found" >&2; exit 1; }
docker compose --env-file .env.docker -f compose.yaml down
