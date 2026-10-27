#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKER_URL="${MOSS_VLLM_URL:-http://127.0.0.1:8001}"
WORKER_URL="${WORKER_URL%/}"
MODEL="${MOSS_MODEL:-OpenMOSS-Team/MOSS-Image-SFT}"
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
python3 "${ROOT_DIR}/scripts/validate_inference_url.py" \
  "${WORKER_URL}" "${MOSS_ALLOW_EXTERNAL_INFERENCE:-false}" "${MOSS_LOCAL_ONLY:-true}"

case "${MODEL}" in
  OpenMOSS-Team/MOSS-Image-Base|OpenMOSS-Team/MOSS-Image-SFT) ;;
  *)
    echo "MOSS_MODEL must be OpenMOSS-Team/MOSS-Image-Base or OpenMOSS-Team/MOSS-Image-SFT" >&2
    exit 2
    ;;
esac

AUTH_ARGS=()
if [[ -n "${VLLM_API_KEY:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${VLLM_API_KEY}")
fi

echo "Worker: ${WORKER_URL}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moss-worker-check.XXXXXX")"
trap 'rm -rf "${TEMP_DIR}"' EXIT
HEALTH_FILE="${TEMP_DIR}/health.json"
MODELS_FILE="${TEMP_DIR}/models.json"
curl --connect-timeout 5 --max-time 10 --fail-with-body --silent --show-error "${AUTH_ARGS[@]}" \
  "${WORKER_URL}/health" >"${HEALTH_FILE}"
curl --connect-timeout 5 --max-time 10 --fail-with-body --silent --show-error "${AUTH_ARGS[@]}" \
  "${WORKER_URL}/v1/models" >"${MODELS_FILE}"
MODEL="${MODEL}" python3 - "${MODELS_FILE}" <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"vLLM /v1/models did not return valid JSON: {exc}") from exc

served = {
    str(item.get("id"))
    for item in payload.get("data", [])
    if isinstance(item, dict) and item.get("id")
}
model = os.environ["MODEL"]
if model not in served:
    raise SystemExit(f"configured MOSS model is not served: {model}; available={sorted(served)}")
print(f"Served model: {model}")
PY
echo "vLLM worker health: PASS"
