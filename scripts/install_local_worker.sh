#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_VENV="${MOSS_WORKER_VENV:-$ROOT_DIR/.venv-worker}"
PYTHON_VERSION="${MOSS_WORKER_PYTHON:-3.12}"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
}
[[ "$WORKER_VENV" = /* ]] || {
  echo "MOSS_WORKER_VENV must be an absolute path" >&2
  exit 1
}

echo "Creating local vLLM-Omni environment at $WORKER_VENV (Python $PYTHON_VERSION)"
uv venv --python "$PYTHON_VERSION" --seed "$WORKER_VENV"

# vLLM-Omni must match vLLM's major/minor line. These native pins are retained
# as an experimental alternative; the verified deployment path is the official
# Docker nightly image configured by compose.yaml.
uv pip install --python "$WORKER_VENV/bin/python" \
  "vllm==0.31.0" \
  "vllm-omni==0.31.0rc1" \
  --torch-backend=auto

echo "Installed the local vLLM-Omni worker. Activate it before starting MOSS:"
echo "  source $WORKER_VENV/bin/activate"
echo "  ./scripts/start_vllm_omni.sh"
