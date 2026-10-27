#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${MOSS_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3 || true)"

echo "host: $(hostname 2>/dev/null || echo unavailable)"
echo "os: $(. /etc/os-release 2>/dev/null && printf '%s %s' "${PRETTY_NAME:-unknown}" "${VERSION_ID:-}" || uname -sr)"
echo "cpu: $(lscpu 2>/dev/null | awk -F: '/Model name/ {gsub(/^ +/, "", $2); print $2; exit}' || echo unavailable)"
echo "cpu_cores: $(nproc 2>/dev/null || echo unavailable)"
echo "ram: $(free -h 2>/dev/null | awk '/^Mem:/ {print $2; exit}' || echo unavailable)"
echo "disk: $(df -h "$ROOT_DIR" 2>/dev/null | awk 'NR==2 {print $4 " free of " $2; exit}' || echo unavailable)"

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia_smi: $(nvidia-smi --version 2>/dev/null | head -n 1 || echo unavailable)"
  echo "gpus:"
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,compute_cap,utilization.gpu --format=csv,noheader,nounits 2>&1 || true
else
  echo "nvidia_smi: unavailable"
fi

if command -v nvtop >/dev/null 2>&1; then
  echo "nvtop: $(nvtop --version 2>/dev/null | head -n 1 || echo installed-but-unreadable)"
  echo "nvtop_snapshot:"
  timeout 5 nvtop --snapshot --no-color 2>&1 | sed -n '1,80p' || true
else
  echo "nvtop: unavailable"
fi

if [[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]]; then
  echo "python: $($PYTHON_BIN --version 2>&1)"
  "$PYTHON_BIN" - <<'PY'
from importlib import metadata

for distribution in ("torch", "vllm", "transformers", "accelerate", "safetensors", "Pillow"):
    try:
        version = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        version = "unavailable"
    except Exception as exc:
        version = f"unreadable ({exc})"
    print(f"python_package_{distribution.lower()}: {version}")

try:
    import torch
    print(f"torch: {torch.__version__}")
    print(f"torch_cuda_build: {torch.version.cuda or 'none'}")
    print(f"torch_cuda_available: {torch.cuda.is_available()}")
    print(f"torch_device_count: {torch.cuda.device_count()}")
    for index in range(torch.cuda.device_count()):
        print(f"torch_device_{index}: {torch.cuda.get_device_name(index)} capability={'.'.join(map(str, torch.cuda.get_device_capability(index)))}")
except Exception as exc:
    print(f"torch: unavailable ({exc})")
PY
else
  echo "python: unavailable"
fi

for tool in vllm comfy docker uv npm; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool: $(command -v "$tool")"
  else
    echo "$tool: unavailable"
  fi
done

echo "diagnostic_scope: read-only; no services, firewall rules, model files, or network mappings were changed"
