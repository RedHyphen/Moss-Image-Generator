#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose is required" >&2; exit 1; }
[[ -f .env.docker ]] || {
  echo "Missing .env.docker; run ./scripts/prepare_docker_env.sh first" >&2
  exit 1
}

set -a
source .env.docker
set +a

if [[ "${MOSS_MAX_PIXELS:-auto}" == "auto" ]]; then
  MOSS_MAX_PIXELS="$(PYTHONPATH="$ROOT_DIR/backend" python3 -c 'from app.resolution import resolve_resolution_budget; print(resolve_resolution_budget("auto").max_pixels)')"
  export MOSS_MAX_PIXELS
  echo "Auto resolution ceiling: ${MOSS_MAX_PIXELS} pixels (80% VRAM budget, standard rounded)"
fi

python3 - "${MOSS_HOST:-127.0.0.1}" "${MOSS_TRUSTED_SUBNETS:-}" <<'PY'
import ipaddress
import sys

host, trusted_value = sys.argv[1:]
try:
    address = ipaddress.ip_address(host)
except ValueError as exc:
    raise SystemExit("MOSS_HOST must be a numeric loopback, private, or link-local address") from exc
if address.version != 4 or address.is_unspecified or not (
    address.is_loopback or address.is_private or address.is_link_local
):
    raise SystemExit("MOSS_HOST must be a loopback, private, or link-local IPv4 address")
try:
    trusted = [ipaddress.ip_network(value.strip(), strict=False) for value in trusted_value.split(",") if value.strip()]
except ValueError as exc:
    raise SystemExit(f"MOSS_TRUSTED_SUBNETS is invalid: {exc}") from exc
if not address.is_loopback and not any(address in network for network in trusted):
    raise SystemExit("MOSS_TRUSTED_SUBNETS must explicitly include a non-loopback configured host")
PY

docker compose --env-file .env.docker -f compose.yaml up -d --build
echo "Moss-Image-Generator: http://${MOSS_HOST:-127.0.0.1}:${MOSS_PORT:-8002}"
echo "Check status: docker compose --env-file .env.docker -f compose.yaml ps"
