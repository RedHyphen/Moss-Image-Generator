from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .storage import media_usage_bytes


runtime_probe_cache: tuple[float, dict[str, Any]] | None = None
runtime_probe_lock = threading.Lock()


def detect_runtime_compatibility() -> dict[str, Any]:
    """Probe the active Python environment without importing torch in-process."""
    global runtime_probe_cache
    with runtime_probe_lock:
        now = time.monotonic()
        if runtime_probe_cache and now - runtime_probe_cache[0] < 30:
            return runtime_probe_cache[1]
        probe = {
            "python_executable": sys.executable,
            "torch_version": None,
            "torch_cuda_version": None,
            "torch_cuda_available": False,
            "torch_device_count": 0,
            "torch_devices": [],
            "error": "PyTorch is not installed in the web environment",
        }
        code = (
            "import json, torch; "
            "devices=[]; "
            "[devices.append({'index':i,'name':torch.cuda.get_device_name(i),'capability':'.'.join(map(str,torch.cuda.get_device_capability(i)))}) for i in range(torch.cuda.device_count())]; "
            "print(json.dumps({'torch_version':torch.__version__,'torch_cuda_version':torch.version.cuda,'torch_cuda_available':bool(torch.cuda.is_available()),'torch_device_count':torch.cuda.device_count(),'torch_devices':devices}))"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, timeout=8, check=False
            )
            if result.returncode == 0:
                payload = json.loads(result.stdout.strip())
                probe.update(payload)
                probe["error"] = ""
            else:
                probe["error"] = (result.stderr.strip() or "PyTorch runtime probe failed")[-1000:]
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            probe["error"] = str(exc)
        runtime_probe_cache = (now, probe)
        return probe


def detect_hardware(settings) -> dict[str, Any]:
    """Collect web-runtime diagnostics; the vLLM worker owns inference GPU state."""
    gpu = {"available": False, "error": "nvidia-smi unavailable"}
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            proc = subprocess.run(
                [
                    nvidia,
                    "--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu,compute_cap,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0:
                records = []
                for line in proc.stdout.splitlines():
                    fields = [field.strip() for field in line.split(",")]
                    if len(fields) != 7:
                        continue

                    def number(value: str, cast):
                        try:
                            return cast(value)
                        except (TypeError, ValueError):
                            return None

                    memory_total_mb = number(fields[2], float)
                    memory_used_mb = number(fields[3], float)
                    records.append(
                        {
                            "name": fields[0],
                            "driver_version": fields[1],
                            "memory_total_bytes": int(memory_total_mb * 1024 * 1024) if memory_total_mb is not None else None,
                            "memory_used_bytes": int(memory_used_mb * 1024 * 1024) if memory_used_mb is not None else None,
                            "utilization_percent": number(fields[4], float),
                            "compute_capability": fields[5],
                            "temperature_c": number(fields[6], float),
                        }
                    )
                if records:
                    gpu = {"available": True, "raw": proc.stdout.strip(), "gpus": records, **records[0]}
                else:
                    gpu = {"available": False, "error": "nvidia-smi returned no parseable GPU records", "raw": proc.stdout.strip()}
            else:
                gpu = {"available": False, "error": proc.stderr.strip() or "nvidia-smi failed"}
        except (OSError, subprocess.TimeoutExpired) as exc:
            gpu = {"available": False, "error": str(exc)}
    memory_bytes = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory_bytes = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    tools = {name: bool(shutil.which(name)) for name in ("vllm", "comfy", "docker", "nvidia-smi", "nvtop", "nvcc")}
    runtime = detect_runtime_compatibility()
    cuda_toolkit = None
    nvcc = shutil.which("nvcc")
    if nvcc:
        try:
            result = subprocess.run([nvcc, "--version"], capture_output=True, text=True, timeout=3, check=False)
            for line in result.stdout.splitlines():
                if "release " in line:
                    cuda_toolkit = line.split("release ", 1)[1].split(",", 1)[0].strip()
                    break
        except (OSError, subprocess.TimeoutExpired):
            pass
    addresses = []
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(socket.gethostname(), None) if entry[4]})
    except socket.gaierror:
        pass
    return {
        "scope": "web-runtime",
        "os": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or platform.machine(),
        "cpu_cores": os.cpu_count(),
        "ram_bytes": memory_bytes,
        "python": platform.python_version(),
        "network_addresses": addresses,
        "gpu": gpu,
        "inference_tools": tools,
        "runtime": {**runtime, "cuda_toolkit_version": cuda_toolkit},
        "disk_free_bytes": shutil.disk_usage(settings.root).free,
        "configured_limits": {
            "max_pixels": settings.max_pixels,
            "max_pixels_source": settings.max_pixels_source,
            "vram_total_bytes": settings.vram_total_bytes,
            "vram_budget_bytes": settings.vram_budget_bytes,
            "vram_free_bytes": settings.vram_free_bytes,
            "max_queue": settings.max_queue,
            "max_media_bytes": settings.max_media_bytes,
            "media_usage_bytes": media_usage_bytes(settings.uploads_dir, settings.images_dir),
        },
    }
