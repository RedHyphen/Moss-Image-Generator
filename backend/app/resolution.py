from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


# Resolution sizing is calibrated against the existing conservative 8GB
# profile. The worker reserves 20% of total VRAM as headroom and rounds down to
# a standard pixel budget so custom aspect ratios remain subject to one clear
# server-side ceiling. Current free VRAM is telemetry only: the resident worker
# itself occupies VRAM, so using free memory here would make the advertised
# ceiling shrink after the worker starts and would incorrectly hide stable
# presets such as 1024x1024 on larger cards.
VRAM_HEADROOM_RATIO = 0.8
BASELINE_VRAM_BYTES = 8 * 1024**3
BASELINE_MAX_PIXELS = 589_824
MIN_STANDARD_PIXELS = 512 * 512
STANDARD_PIXEL_BUDGETS = (
    512 * 512,
    768 * 768,
    1024 * 768,
    1024 * 1024,
    1280 * 720,
    1536 * 1024,
    1536 * 1536,
    2048 * 1536,
    2048 * 2048,
)


@dataclass(frozen=True)
class ResolutionBudget:
    max_pixels: int
    source: str
    vram_total_bytes: int | None
    vram_budget_bytes: int | None
    vram_free_bytes: int | None


def query_total_vram_bytes() -> int | None:
    """Return the first visible NVIDIA GPU's total VRAM, if available."""
    nvidia_smi = os.getenv("MOSS_NVIDIA_SMI", "nvidia-smi")
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        try:
            memory_mib = float(line.strip().split()[0])
        except (IndexError, ValueError):
            continue
        if memory_mib > 0:
            return int(memory_mib * 1024 * 1024)
    return None


def query_used_vram_bytes() -> int | None:
    """Return the first visible NVIDIA GPU's current used VRAM, if available."""
    nvidia_smi = os.getenv("MOSS_NVIDIA_SMI", "nvidia-smi")
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        try:
            memory_mib = float(line.strip().split()[0])
        except (IndexError, ValueError):
            continue
        if memory_mib >= 0:
            return int(memory_mib * 1024 * 1024)
    return None


def _standard_max_pixels(pixel_budget: int) -> int:
    eligible = [pixels for pixels in STANDARD_PIXEL_BUDGETS if pixels <= pixel_budget]
    return max(eligible, default=MIN_STANDARD_PIXELS)


def resolve_resolution_budget(raw_value: str | None = None) -> ResolutionBudget:
    """Resolve an explicit pixel limit or an 80%-VRAM automatic limit."""
    configured = (raw_value if raw_value is not None else os.getenv("MOSS_MAX_PIXELS", "auto")).strip().lower()
    if configured not in {"", "auto"}:
        try:
            max_pixels = int(configured)
        except ValueError as exc:
            raise ValueError("MOSS_MAX_PIXELS must be an integer or auto") from exc
        if max_pixels < MIN_STANDARD_PIXELS:
            raise ValueError(f"MOSS_MAX_PIXELS must be at least {MIN_STANDARD_PIXELS}")
        return ResolutionBudget(max_pixels, "configured", None, None, None)

    total_vram_bytes = query_total_vram_bytes()
    if total_vram_bytes is None:
        return ResolutionBudget(BASELINE_MAX_PIXELS, "auto-fallback", None, None, None)

    total_budget_bytes = int(total_vram_bytes * VRAM_HEADROOM_RATIO)
    used_vram_bytes = query_used_vram_bytes()
    free_vram_bytes = None
    vram_budget_bytes = total_budget_bytes
    if used_vram_bytes is not None:
        free_vram_bytes = max(0, total_vram_bytes - used_vram_bytes)
    calibrated_pixels = int(BASELINE_MAX_PIXELS * vram_budget_bytes / (BASELINE_VRAM_BYTES * VRAM_HEADROOM_RATIO))
    return ResolutionBudget(
        _standard_max_pixels(calibrated_pixels),
        "auto",
        total_vram_bytes,
        vram_budget_bytes,
        free_vram_bytes,
    )
