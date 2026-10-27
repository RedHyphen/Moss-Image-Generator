#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Perform one real MOSS-Image request against the authorized local vLLM-Omni
# worker. This script deliberately has no fake-image or text-only fallback.
BASE_URL="${MOSS_VLLM_URL:-http://127.0.0.1:8001}"
MODEL="${MOSS_MODEL:-OpenMOSS-Team/MOSS-Image-SFT}"
PROMPT="${MOSS_LIVE_PROMPT:-A small red sailboat on a calm lake at sunrise, cinematic landscape photography}"
SIZE="${MOSS_LIVE_SIZE:-512x512}"
STEPS="${MOSS_LIVE_STEPS:-50}"
OUTPUT="${MOSS_LIVE_OUTPUT:-/tmp/moss-live-smoke.png}"
TIMEOUT="${MOSS_LIVE_TIMEOUT_SECONDS:-3600}"

python3 "${ROOT_DIR}/scripts/validate_inference_url.py" \
  "${BASE_URL}" "${MOSS_ALLOW_EXTERNAL_INFERENCE:-false}" "${MOSS_LOCAL_ONLY:-true}"

case "${MODEL}" in
  OpenMOSS-Team/MOSS-Image-Base|OpenMOSS-Team/MOSS-Image-SFT) ;;
  *)
    echo "MOSS_MODEL must be OpenMOSS-Team/MOSS-Image-Base or OpenMOSS-Team/MOSS-Image-SFT" >&2
    exit 2
    ;;
esac

if [[ ! "${SIZE}" =~ ^[1-9][0-9]*x[1-9][0-9]*$ ]]; then
  echo "MOSS_LIVE_SIZE must have the form WIDTHxHEIGHT" >&2
  exit 2
fi
WIDTH="${SIZE%x*}"
HEIGHT="${SIZE#*x}"
if (( WIDTH < 512 || WIDTH > 2048 || HEIGHT < 512 || HEIGHT > 2048 || WIDTH % 16 != 0 || HEIGHT % 16 != 0 )); then
  echo "MOSS_LIVE_SIZE must be between 512x512 and 2048x2048 with dimensions divisible by 16" >&2
  exit 2
fi
if [[ ! "${STEPS}" =~ ^[1-9][0-9]*$ || ! "${TIMEOUT}" =~ ^[1-9][0-9]*$ ]]; then
  echo "MOSS_LIVE_STEPS and MOSS_LIVE_TIMEOUT_SECONDS must be positive integers" >&2
  exit 2
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/moss-live-smoke.XXXXXX")"
trap 'rm -rf "${TEMP_DIR}"' EXIT
HEALTH_FILE="${TEMP_DIR}/health.json"
MODELS_FILE="${TEMP_DIR}/models.json"
REQUEST_FILE="${TEMP_DIR}/request.json"
RESPONSE_FILE="${TEMP_DIR}/response.json"

AUTH_ARGS=()
if [[ -n "${VLLM_API_KEY:-}" ]]; then
  AUTH_ARGS=(-H "Authorization: Bearer ${VLLM_API_KEY}")
fi

echo "Worker: ${BASE_URL}"
curl -fsS --connect-timeout 5 --max-time 10 "${AUTH_ARGS[@]}" \
  "${BASE_URL%/}/health" >"${HEALTH_FILE}"
curl -fsS --connect-timeout 5 --max-time 10 "${AUTH_ARGS[@]}" \
  "${BASE_URL%/}/v1/models" >"${MODELS_FILE}"

MODEL="${MODEL}" python3 - "${MODELS_FILE}" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
served = {
    str(item.get("id"))
    for item in payload.get("data", [])
    if isinstance(item, dict) and item.get("id")
}
model = os.environ["MODEL"]
if model not in served:
    raise SystemExit(
        f"requested MOSS model is not served: {model}; available={sorted(served)}"
    )
print(f"Served model: {model}")
PY

MODEL="${MODEL}" PROMPT="${PROMPT}" SIZE="${SIZE}" STEPS="${STEPS}" \
  python3 - "${REQUEST_FILE}" <<'PY'
import json
import os
import sys
from pathlib import Path

payload = {
    "model": os.environ["MODEL"],
    "prompt": os.environ["PROMPT"],
    "size": os.environ["SIZE"],
    "n": 1,
    "num_inference_steps": int(os.environ["STEPS"]),
    "guidance_scale": 3.0,
    "flow_shift": 5.0,
    "negative_prompt": "",
    "response_format": "b64_json",
    "extra_params": {
        "uncond_skip_layers": [22],
        "slg_start": 0.01,
        "slg_stop": 1.0,
    },
}
Path(sys.argv[1]).write_text(json.dumps(payload), encoding="utf-8")
PY

echo "Submitting one real image-generation request (${SIZE}, ${STEPS} steps)..."
curl -fsS --connect-timeout 10 --max-time "${TIMEOUT}" "${AUTH_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d "@${REQUEST_FILE}" "${BASE_URL%/}/v1/images/generations" >"${RESPONSE_FILE}"

OUTPUT="${OUTPUT}" python3 - "${RESPONSE_FILE}" <<'PY'
import base64
import binascii
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

response = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
items = response.get("data")
if not isinstance(items, list) or not items or not isinstance(items[0], dict):
    raise SystemExit("MOSS response did not contain data[0]")
encoded = items[0].get("b64_json")
if not isinstance(encoded, str) or not encoded:
    raise SystemExit("MOSS response did not contain data[0].b64_json")
try:
    raw = base64.b64decode(encoded, validate=True)
except (binascii.Error, ValueError) as exc:
    raise SystemExit(f"MOSS returned invalid base64 image data: {exc}") from exc

png_signature = b"\x89PNG\r\n\x1a\n"
if len(raw) < 24 or raw[:8] != png_signature or raw[12:16] != b"IHDR":
    raise SystemExit("MOSS response was not a valid PNG raster")
width, height = struct.unpack(">II", raw[16:24])
if not width or not height:
    raise SystemExit("MOSS returned a PNG with invalid dimensions")

destination = Path(os.environ["OUTPUT"]).expanduser()
destination.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile(
    mode="wb",
    prefix=f".{destination.name}.",
    suffix=".tmp",
    dir=destination.parent,
    delete=False,
) as handle:
    temporary = Path(handle.name)
    handle.write(raw)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, destination)
print(f"Real MOSS inference passed: {destination} ({width}x{height}, {len(raw)} bytes)")
PY
