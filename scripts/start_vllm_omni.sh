#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Load local defaults without allowing an explicitly supplied environment
# variable (CI, systemd, or an operator's shell) to be silently overwritten.
# This matters for validation: tests and deployments must be able to override
# a checked-in .env template without changing that file.
CONFIG_ENV_KEYS=(
  VLLM_IMAGE MOSS_MODEL MOSS_MODEL_PATH VLLM_HOST VLLM_PORT
  CUDA_VISIBLE_DEVICES MOSS_GPU_PROFILE VLLM_TENSOR_PARALLEL_SIZE VLLM_MODE
  MOSS_ALLOW_EXISTING_GPU_SERVICE MOSS_ALLOW_WIDE_BIND MOSS_ALLOW_FP16_EXPERIMENTAL
  VLLM_DTYPE MOSS_VLLM_CPU_OFFLOAD MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG
  VLLM_API_KEY MOSS_ALLOW_UNAUTHENTICATED_VLLM MOSS_ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM
  MOSS_ALLOW_UNTRUSTED_VLLM_IMAGE PYTORCH_CUDA_ALLOC_CONF
)
declare -A EXPLICIT_ENV_VALUES=()
for key in "${CONFIG_ENV_KEYS[@]}"; do
  if [[ -v "$key" ]]; then
    EXPLICIT_ENV_VALUES["$key"]="${!key}"
  fi
done
if [[ -f .env ]]; then
  set -a
  # The checked-in example keeps the worker loopback-only. A local .env may
  # provide the model path and the host's GPU profile without leaking secrets.
  source .env
  set +a
fi
for key in "${!EXPLICIT_ENV_VALUES[@]}"; do
  printf -v "$key" '%s' "${EXPLICIT_ENV_VALUES[$key]}"
  export "$key"
done
# An explicit CPU-offload request supersedes a default layerwise config from
# .env. Keep an explicitly supplied value for both settings invalid instead of
# silently choosing one.
if [[ -v 'EXPLICIT_ENV_VALUES[MOSS_VLLM_CPU_OFFLOAD]' ]] \
  && [[ ! -v 'EXPLICIT_ENV_VALUES[MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG]' ]] \
  && [[ "${MOSS_VLLM_CPU_OFFLOAD,,}" == "true" ]]; then
  MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG=""
  export MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG
fi

# Run the MOSS-Image worker on this host. It does not change firewall rules,
# create tunnels, or publish a router port. Keep the worker on loopback.
IMAGE="${VLLM_IMAGE:-vllm/vllm-omni:nightly}"
MODEL="${MOSS_MODEL:-OpenMOSS-Team/MOSS-Image-SFT}"
MODEL_PATH="${MOSS_MODEL_PATH:-}"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8001}"
GPU="${CUDA_VISIBLE_DEVICES:-0}"
GPU_PROFILE="${MOSS_GPU_PROFILE:-8gb-turing}"
VLLM_TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-1}"
VLLM_MODE="${VLLM_MODE:-native}"
ALLOW_EXISTING_GPU_SERVICE="${MOSS_ALLOW_EXISTING_GPU_SERVICE:-false}"
ALLOW_WIDE_BIND="${MOSS_ALLOW_WIDE_BIND:-false}"
ALLOW_FP16_EXPERIMENTAL="${MOSS_ALLOW_FP16_EXPERIMENTAL:-false}"
VLLM_DTYPE="${VLLM_DTYPE:-auto}"
VLLM_CPU_OFFLOAD="${MOSS_VLLM_CPU_OFFLOAD:-false}"
VLLM_DIFFUSION_OFFLOAD_CONFIG="${MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG:-}"
VLLM_API_KEY="${VLLM_API_KEY:-}"
ALLOW_UNAUTHENTICATED_VLLM="${MOSS_ALLOW_UNAUTHENTICATED_VLLM:-false}"
ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM="${MOSS_ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM:-false}"
ALLOW_UNTRUSTED_IMAGE="${MOSS_ALLOW_UNTRUSTED_VLLM_IMAGE:-false}"
export VLLM_API_KEY

case "${GPU_PROFILE}" in
  8gb-turing)
    # The FP16 MOSS path is experimental, while
    # layerwise host offload is the required memory-saving strategy.
    if [[ "${VLLM_DTYPE}" == "auto" ]]; then
      VLLM_DTYPE="float16"
    fi
    if [[ -z "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" ]] && [[ "${VLLM_CPU_OFFLOAD,,}" != "true" ]]; then
      VLLM_DIFFUSION_OFFLOAD_CONFIG='{"mode":"layer","components":["dit","text_encoder"],"layer_options":{"dit":{"weight_transfer":"rank-local"},"text_encoder":{"weight_transfer":"rank-local"}},"pin_memory":true}'
    fi
    if [[ -z "${MOSS_ALLOW_FP16_EXPERIMENTAL:-}" ]]; then
      ALLOW_FP16_EXPERIMENTAL=true
    fi
    echo "Using 8GB Turing local profile: FP16 plus layerwise host offload; generation is expected to be slow and must be validated on the target GPU." >&2
    ;;
  auto)
    ;;
  *)
    echo "MOSS_GPU_PROFILE must be 8gb-turing or auto" >&2
    exit 1
    ;;
esac

export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

if [[ -z "${VLLM_API_KEY}" ]]; then
  LOOPBACK_BIND=false
  case "${VLLM_HOST}" in
    127.0.0.1|::1|localhost) LOOPBACK_BIND=true ;;
  esac
  if [[ "${ALLOW_UNAUTHENTICATED_VLLM,,}" != "true" ]] && {
    [[ "${LOOPBACK_BIND}" != "true" ]] || [[ "${ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM,,}" != "true" ]]
  }; then
    echo "Refusing to start an unauthenticated vLLM image endpoint. Set VLLM_API_KEY, or keep VLLM_HOST=127.0.0.1 and explicitly review MOSS_ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM=true; clients must use the authenticated web app." >&2
    exit 1
  fi
  if [[ "${LOOPBACK_BIND}" == "true" ]] && [[ "${ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM,,}" == "true" ]] && [[ "${ALLOW_UNAUTHENTICATED_VLLM,,}" != "true" ]]; then
    echo "Using loopback-only unauthenticated vLLM; clients must use the authenticated web app, not the worker port." >&2
  fi
fi

case "${MODEL}" in
  OpenMOSS-Team/MOSS-Image-Base|OpenMOSS-Team/MOSS-Image-SFT) ;;
  *)
    echo "MOSS_MODEL must be OpenMOSS-Team/MOSS-Image-Base or OpenMOSS-Team/MOSS-Image-SFT" >&2
    exit 1
    ;;
esac

if [[ -n "${MODEL_PATH}" ]]; then
  if [[ "${VLLM_MODE}" != "native" ]]; then
    echo "MOSS_MODEL_PATH is supported only with VLLM_MODE=native; Docker uses the mounted Hugging Face cache." >&2
    exit 1
  fi
  if [[ "${MODEL_PATH}" != /* || "${MODEL_PATH}" == *$'\n'* || "${MODEL_PATH}" == *$'\r'* ]]; then
    echo "MOSS_MODEL_PATH must be an absolute path without newlines" >&2
    exit 1
  fi
  if [[ ! -d "${MODEL_PATH}" || ! -r "${MODEL_PATH}/model_index.json" ]]; then
    echo "MOSS_MODEL_PATH must contain a readable model_index.json: ${MODEL_PATH}" >&2
    exit 1
  fi
fi

if [[ -n "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" ]]; then
  if [[ "${VLLM_CPU_OFFLOAD,,}" == "true" ]]; then
    echo "MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG cannot be combined with MOSS_VLLM_CPU_OFFLOAD=true" >&2
    exit 1
  fi
  python3 - "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" <<'PY'
import json
import sys

try:
    value = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit(f"MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG must be valid JSON: {exc}") from exc
if not isinstance(value, dict):
    raise SystemExit("MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG must be a JSON object")
PY
fi

if ! [[ "${VLLM_TENSOR_PARALLEL_SIZE}" =~ ^[1-9][0-9]*$ ]]; then
  echo "VLLM_TENSOR_PARALLEL_SIZE must be a positive integer" >&2
  exit 1
fi
if ! [[ "${VLLM_PORT}" =~ ^[1-9][0-9]*$ ]] || (( VLLM_PORT > 65535 )); then
  echo "VLLM_PORT must be between 1 and 65535" >&2
  exit 1
fi
case "${VLLM_MODE}" in
  native|docker) ;;
  *)
    echo "VLLM_MODE must be native or docker" >&2
    exit 1
    ;;
esac
if [[ "${VLLM_MODE}" == "docker" ]]; then
  case "${IMAGE}" in
    vllm/vllm-omni:nightly|vllm/vllm-omni:mosimage|vllm/vllm-omni:mosimage-cu129) ;;
    *)
      if [[ "${ALLOW_UNTRUSTED_IMAGE,,}" != "true" ]]; then
        echo "Refusing an untrusted VLLM_IMAGE=${IMAGE}; use an official MOSS image or explicitly review MOSS_ALLOW_UNTRUSTED_VLLM_IMAGE=true." >&2
        exit 1
      fi
      echo "WARNING: using explicitly approved non-official VLLM_IMAGE=${IMAGE}" >&2
      ;;
  esac
fi
IFS=',' read -r -a SELECTED_GPUS <<< "${GPU}"
if (( ${#SELECTED_GPUS[@]} < VLLM_TENSOR_PARALLEL_SIZE )); then
  echo "VLLM_TENSOR_PARALLEL_SIZE=${VLLM_TENSOR_PARALLEL_SIZE} requires at least that many selected GPUs; got ${GPU}" >&2
  exit 1
fi

if [[ "${VLLM_HOST}" == "0.0.0.0" || "${VLLM_HOST}" == "::" ]] && [[ "${ALLOW_WIDE_BIND,,}" != "true" ]]; then
    echo "Refusing a wide vLLM bind (${VLLM_HOST}). Keep VLLM_HOST on loopback for local mode, or explicitly review MOSS_ALLOW_WIDE_BIND=true." >&2
  exit 1
fi

# Never let a deployment typo turn the inference worker into a public listener.
# Numeric addresses are checked directly; hostnames are resolved and every
# result must remain loopback/private/link-local. Wide binds retain the
# explicit review switch above because their actual boundary must come from a
# host firewall, but public numeric or DNS targets are never accepted.
command -v python3 >/dev/null 2>&1 || { echo "python3 is required to validate VLLM_HOST" >&2; exit 1; }
python3 - "${VLLM_HOST}" "${ALLOW_WIDE_BIND}" <<'PY'
import ipaddress
import socket
import sys

host, allow_wide = sys.argv[1:]
if host in {"0.0.0.0", "::"}:
    if allow_wide.lower() == "true":
        print(f"WARNING: vLLM wide bind {host} requires an independently verified host firewall", file=sys.stderr)
        raise SystemExit(0)
    raise SystemExit("wide bind requires MOSS_ALLOW_WIDE_BIND=true")

def local(address):
    return address.is_loopback or address.is_private or address.is_link_local

try:
    addresses = [ipaddress.ip_address(host.strip("[]"))]
except ValueError:
    if host.lower() == "localhost":
        addresses = [ipaddress.ip_address("127.0.0.1")]
    else:
        try:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            ]
        except (OSError, ValueError) as exc:
            raise SystemExit(f"could not resolve VLLM_HOST={host}: {exc}") from exc
        if not addresses:
            raise SystemExit(f"VLLM_HOST={host} resolved to no addresses")

if not all(local(address) for address in addresses):
    rendered = ", ".join(str(address) for address in addresses)
    raise SystemExit(f"VLLM_HOST must resolve only to loopback/private/link-local addresses; got {rendered}")
PY

# The official MOSS recipe loads BF16 DiT/text-encoder weights. vLLM rejects
# BF16 on pre-Ampere GPUs (compute capability < 8.0). Require an explicit
# operator override before attempting the unverified FP16 fallback.
if command -v nvidia-smi >/dev/null 2>&1; then
  # Inspect every explicitly selected GPU. A decision based only on GPU 0 can
  # silently select an incompatible precision or underestimate a mixed-GPU
  # tensor-parallel deployment.
  GPU_RECORDS="$(nvidia-smi --id="${GPU}" --query-gpu=index,memory.total,compute_cap --format=csv,noheader,nounits 2>/dev/null || true)"
  LOW_VRAM_SELECTED=false
  NON_BF16_SELECTED=false
  if [[ -n "${GPU_RECORDS}" ]]; then
    while IFS=',' read -r GPU_INDEX GPU_MEMORY GPU_COMPUTE; do
      GPU_MEMORY="${GPU_MEMORY//[[:space:]]/}"
      GPU_COMPUTE="${GPU_COMPUTE//[[:space:]]/}"
      if [[ "${GPU_MEMORY}" =~ ^[0-9]+$ ]] && (( GPU_MEMORY <= 8192 )); then
        LOW_VRAM_SELECTED=true
      fi
      if [[ "${GPU_COMPUTE}" =~ ^([0-9]+)\.([0-9]+)$ ]] && (( BASH_REMATCH[1] < 8 )); then
        NON_BF16_SELECTED=true
      fi
    done <<< "${GPU_RECORDS}"
  fi
  OFFLOAD_CONFIGURED=false
  if [[ -n "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" ]]; then
    OFFLOAD_CONFIGURED=true
  fi
  if [[ "${LOW_VRAM_SELECTED}" == "true" ]] && [[ "${VLLM_CPU_OFFLOAD,,}" != "true" ]] && [[ "${OFFLOAD_CONFIGURED}" != "true" ]]; then
    if (( VLLM_TENSOR_PARALLEL_SIZE < 2 )); then
      echo "Detected an 8GB-class selected GPU; MOSS is a 5B model and cannot be assumed to fit on one GPU without CPU offload." >&2
      echo "Set MOSS_VLLM_CPU_OFFLOAD=true or provide MOSS_VLLM_DIFFUSION_OFFLOAD_CONFIG with layerwise/module offload after reviewing the experimental path." >&2
      exit 1
    fi
    echo "WARNING: allowing ${VLLM_TENSOR_PARALLEL_SIZE}-way MOSS tensor parallelism across ${GPU}; per-GPU memory and output behavior are not yet validated." >&2
  fi
  if [[ "${NON_BF16_SELECTED}" == "true" ]]; then
    if [[ "${VLLM_DTYPE}" == "bfloat16" || "${VLLM_DTYPE}" == "bf16" ]]; then
      echo "At least one selected GPU has compute capability below 8.0; BF16 is not supported by vLLM on the selected set." >&2
      exit 1
    fi
    if [[ "${VLLM_DTYPE}" == "auto" || "${VLLM_DTYPE}" == "float16" || "${VLLM_DTYPE}" == "half" ]]; then
      if [[ "${ALLOW_FP16_EXPERIMENTAL,,}" != "true" ]]; then
        echo "At least one selected GPU has compute capability below 8.0; official MOSS BF16 serving is not supported on the selected set." >&2
        echo "Review the unverified FP16 path, then set MOSS_ALLOW_FP16_EXPERIMENTAL=true VLLM_DTYPE=float16 if you accept the risk." >&2
        exit 1
      fi
      if [[ "${VLLM_DTYPE}" == "auto" ]]; then
        VLLM_DTYPE="float16"
      fi
      echo "WARNING: using experimental MOSS FP16 mode on the selected GPU set (${GPU}); output and memory behavior are not officially validated." >&2
    fi
  fi
fi

if [[ "${ALLOW_EXISTING_GPU_SERVICE,,}" != "true" ]]; then
  if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet cp-ray-worker.service 2>/dev/null; then
    echo "A cp-ray-worker.service is already using this host's GPU. Stop it before starting MOSS-Image, or explicitly set MOSS_ALLOW_EXISTING_GPU_SERVICE=true after reviewing the resource conflict." >&2
    exit 1
  fi
  if pgrep -af '[r]ay start|[v]llm serve' >/dev/null 2>&1; then
    echo "An existing Ray/vLLM process is using this host. Stop it before starting MOSS-Image, or explicitly set MOSS_ALLOW_EXISTING_GPU_SERVICE=true after reviewing the resource conflict." >&2
    exit 1
  fi
fi

if [[ "$VLLM_MODE" == "native" ]]; then
  if ! command -v vllm >/dev/null 2>&1 && [[ -x "$ROOT_DIR/.venv-worker/bin/vllm" ]]; then
    export PATH="$ROOT_DIR/.venv-worker/bin:$PATH"
  fi
  command -v vllm >/dev/null 2>&1 || { echo "VLLM_MODE=native requires vllm on PATH" >&2; exit 1; }
  # Docker receives the selected devices through --gpus below. Native vLLM
  # needs the equivalent process-level visibility restriction explicitly;
  # otherwise an unset CUDA_VISIBLE_DEVICES can expose every GPU on the host.
  export CUDA_VISIBLE_DEVICES="$GPU"
  SERVE_MODEL="${MODEL}"
  NATIVE_ARGS=(vllm serve "$SERVE_MODEL" --omni --enforce-eager --host "$VLLM_HOST" --port "$VLLM_PORT")
  if [[ -n "${MODEL_PATH}" ]]; then
    NATIVE_ARGS[2]="${MODEL_PATH}"
    NATIVE_ARGS+=(--served-model-name "$MODEL")
  fi
  if [[ "$VLLM_TENSOR_PARALLEL_SIZE" != "1" ]]; then
    NATIVE_ARGS+=(--tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE")
  fi
  if [[ "${VLLM_DTYPE}" != "auto" ]]; then
    NATIVE_ARGS+=(--dtype "${VLLM_DTYPE}")
  fi
  if [[ "${VLLM_CPU_OFFLOAD,,}" == "true" ]]; then
    NATIVE_ARGS+=(--enable-cpu-offload)
  fi
  if [[ -n "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" ]]; then
    NATIVE_ARGS+=(--diffusion-offload-config "${VLLM_DIFFUSION_OFFLOAD_CONFIG}")
  fi
  exec "${NATIVE_ARGS[@]}"
fi

docker pull "$IMAGE"
VLLM_ARGS=(vllm serve "$MODEL" --omni --enforce-eager --host 0.0.0.0 --port 8000)
if [[ "$VLLM_TENSOR_PARALLEL_SIZE" != "1" ]]; then
  VLLM_ARGS+=(--tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE")
fi
if [[ "${VLLM_DTYPE}" != "auto" ]]; then
  VLLM_ARGS+=(--dtype "${VLLM_DTYPE}")
fi
if [[ "${VLLM_CPU_OFFLOAD,,}" == "true" ]]; then
  VLLM_ARGS+=(--enable-cpu-offload)
fi
if [[ -n "${VLLM_DIFFUSION_OFFLOAD_CONFIG}" ]]; then
  VLLM_ARGS+=(--diffusion-offload-config "${VLLM_DIFFUSION_OFFLOAD_CONFIG}")
fi
DOCKER_ENV_ARGS=()
if [[ -n "${VLLM_API_KEY}" ]]; then
  DOCKER_ENV_ARGS=(-e VLLM_API_KEY)
fi
exec docker run --rm --name moss-image-generator-vllm --gpus "device=${GPU}" --ipc=host \
  -p "${VLLM_HOST}:${VLLM_PORT}:8000" \
  -v "${HOME}/.cache/huggingface:/root/.cache/huggingface" \
  "${DOCKER_ENV_ARGS[@]}" \
  -e HF_TOKEN \
  "$IMAGE" "${VLLM_ARGS[@]}"
