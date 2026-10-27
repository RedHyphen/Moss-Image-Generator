# Moss-Image-Generator

Self-hosted, local-first conversational text-to-image generation using
MOSS-Image Base/SFT through a local loopback vLLM-Omni worker.

## What it does

- Generates real images with MOSS-Image locally on this host.
- Supports structured layout generation, prompts, negative prompts, seeds,
  resolution controls, queueing, progress, gallery history, and metadata.
- Persists conversations, outputs, jobs, and account ownership in SQLite/files.
- Provides responsive desktop/mobile UI, authenticated sessions, local-only
  operation, and offline use after required resources are installed.
- Provides a confirmed, account-scoped **Clear history** action that removes
  that user's conversations and media without deleting model caches or other
  accounts' data.

MOSS-Image's verified API is text/layout generation only. The active product
contains no image-editing pipeline: img2img, inpainting, outpainting, and
multi-reference conditioning are not exposed. Uploaded-image requests are
rejected clearly instead of silently discarding
their pixels.

When a conversation already has a completed image, a follow-up instruction
such as “add a red cape” is treated as an additive prompt continuation: the
previous generation prompt is carried into the next MOSS request and the new
output is linked to its parent version. MOSS still does not receive the prior
raster, so this provides text continuity, not guaranteed subject or pixel
preservation.

## Quick start

```bash
./scripts/install.sh
./scripts/check_hardware.sh
./scripts/prepare_docker_env.sh
./scripts/docker_up.sh
set -a; source .env.docker; set +a
./scripts/check_vllm_worker.sh
./scripts/smoke_live_moss.sh
```

The web UI and worker bind to loopback by default, and the verified deployment
keeps both services local to this host. The verified deployment uses the
official Docker worker with FP16 and layerwise host offload. Native worker
installation remains an explicit, experimental opt-in with
`MOSS_INSTALL_LOCAL_WORKER=true`. Do not add router forwarding, tunnels, UPnP,
or public inference endpoints.

Native installation requires Python 3.11 or newer. The Docker runtime uses
Python 3.12.

## Local Docker deployment

```bash
./scripts/prepare_docker_env.sh
./scripts/docker_up.sh
```

The web app is available at `http://127.0.0.1:8002`. The worker listens only
on `127.0.0.1:8001` and is never exposed to browsers or remote clients. The
default `8gb-turing` profile is the recommended 8GB VRAM configuration with
FP16 and layerwise host offload. Resolution is automatically sized
to the lower of 80% of detected total VRAM and 80% of currently free VRAM when
the NVIDIA probe can read it, then rounded down to standard values. See
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the native and Docker paths.

## Verification

```bash
make check
```

## Developer guide

The repository layout, ownership boundaries, coding conventions, local
workflow, testing evidence rules, and safe staging checklist are documented in
[CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). Use `make check` as the canonical
pre-commit check.

Read [docs/MODEL_RESEARCH.md](docs/MODEL_RESEARCH.md),
[docs/USER_GUIDE.md](docs/USER_GUIDE.md), and
[docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) for verified capabilities and
known limitations.

## Portfolio context

Moss-Image-Generator is published as a portfolio artifact, not as an official
product release or hosted service. It is a local-first demonstration with no
production support commitment. The recommended hardware baseline is 8GB VRAM;
exact fit and throughput depend on the selected GPU, driver, runtime build,
resident processes, and workload.

Moss-Image-Generator is an independent project and is not affiliated with or
endorsed by OpenMOSS, vLLM, or other upstream projects. Model weights and
third-party dependencies remain subject to their own licenses and notices.
