#!/usr/bin/env bash
set -euo pipefail

# Read-only LAN-boundary diagnostic. It never changes firewall rules, creates
# mappings, starts services, or prints VLLM_API_KEY.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

read_env_value() {
  local name="$1"
  if [[ -n "${!name:-}" ]]; then
    printf '%s' "${!name}"
    return
  fi
  if [[ -f .env ]]; then
    sed -n "s/^${name}=//p" .env | tail -n 1 | sed 's/^"//; s/"$//' | sed "s/^'//; s/'$//"
  fi
}

WEB_HOST="$(read_env_value MOSS_HOST)"
WEB_PORT="$(read_env_value MOSS_PORT)"
TRUSTED_SUBNETS="$(read_env_value MOSS_TRUSTED_SUBNETS)"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-8000}"

echo "configured_host: ${WEB_HOST}"
echo "configured_port: ${WEB_PORT}"
echo "trusted_subnets: ${TRUSTED_SUBNETS:-<none>}"

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3 || true)"
config_status=0
if [[ -n "$PYTHON_BIN" ]]; then
  if ! "$PYTHON_BIN" - "$WEB_HOST" "$WEB_PORT" "$TRUSTED_SUBNETS" <<'PY'
import ipaddress
import sys

host, port, trusted_text = sys.argv[1:]
try:
    port_number = int(port)
except ValueError:
    print("configuration_valid: no (port is not numeric)")
    raise SystemExit(1)
if not 1 <= port_number <= 65535:
    print("configuration_valid: no (port must be between 1 and 65535)")
    raise SystemExit(1)

trusted = []
for value in (item.strip() for item in trusted_text.split(",") if item.strip()):
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        print(f"configuration_valid: no (invalid trusted subnet: {value})")
        raise SystemExit(1)
    if not (network.is_private or network.is_link_local):
        print(f"configuration_valid: no (public trusted subnet: {value})")
        raise SystemExit(1)
    trusted.append(network)

try:
    address = ipaddress.ip_address(host)
    numeric = True
except ValueError:
    address = None
    numeric = host.lower() == "localhost"

if not numeric:
    print("configuration_valid: no (host must be localhost or numeric)")
    raise SystemExit(1)
if address is not None and not (address.is_loopback or address.is_private or address.is_link_local):
    print("configuration_valid: no (host is public)")
    raise SystemExit(1)
if host in {"0.0.0.0", "::"} and not trusted:
    print("configuration_valid: no (wide bind has no trusted subnet allowlist)")
    raise SystemExit(1)

scope = "loopback-only" if host == "localhost" or (address is not None and address.is_loopback) else "private or link-local network"
print(f"configuration_valid: yes")
print(f"bind_scope: {scope}")
PY
  then
    config_status=1
  fi
else
  echo "configuration_valid: unknown (python3 unavailable)"
  config_status=1
fi

echo "listening_sockets:"
if command -v ss >/dev/null 2>&1; then
  ss -ltn 2>/dev/null | awk -v port=":${WEB_PORT}" 'NR == 1 || index($4, port) {print}' || true
else
  echo "ss: unavailable"
fi

echo "local_interfaces:"
if command -v ip >/dev/null 2>&1; then
  ip -brief address 2>/dev/null || true
  echo "routes:"
  ip route 2>/dev/null | sed -n '1,40p' || true
else
  echo "ip: unavailable"
fi

echo "firewall_observation:"
if command -v ufw >/dev/null 2>&1; then
  ufw status verbose 2>&1 | sed -n '1,80p' || true
elif command -v nft >/dev/null 2>&1; then
  nft list ruleset 2>&1 | sed -n '1,100p' || true
elif command -v iptables >/dev/null 2>&1; then
  iptables -S 2>&1 | sed -n '1,100p' || true
else
  echo "No supported firewall inspection command found"
fi

if (( config_status != 0 )); then
  echo "boundary_result: review required" >&2
  exit 2
fi
echo "boundary_result: configuration checks passed; verify firewall policy and authorized-client access separately"
