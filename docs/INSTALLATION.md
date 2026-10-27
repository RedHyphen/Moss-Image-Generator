# Installation

The supported baseline is Ubuntu Linux with project-local environments.
Native installation requires Python 3.11 or newer. The Docker runtime uses
Python 3.12. The verified deployment uses Docker Compose and does not replace
system Python packages. The default deployment is local-only: the web app
listens on `127.0.0.1:8002` and the MOSS-Image vLLM-Omni worker listens on
loopback port 8001. The worker remains loopback-only.

For a local Docker deployment:

```bash
./scripts/prepare_docker_env.sh
./scripts/docker_up.sh
```

The Docker worker uses the local NVIDIA GPU, mounts the local Hugging Face
cache under `data/models`, and applies layerwise offload. The first run may
download the selected MOSS checkpoint; set `HF_HUB_OFFLINE=1` only after the
checkpoint is cached.

`MOSS_MAX_PIXELS=auto` is the default. `scripts/docker_up.sh` measures the
host GPU's VRAM, reserves 20% headroom, and passes the nearest lower standard
pixel budget to the web container. Current used/free VRAM is reported as
telemetry but does not shrink this stable ceiling after the resident worker
starts. Use a numeric value only when an intentional manual override is needed.

## Install and configure

```bash
./scripts/install.sh
```

The installer creates the web environment in `.venv`. It does not install the
native worker by default: the verified deployment path is the local Docker
worker. Set `MOSS_INSTALL_LOCAL_WORKER=true` to explicitly install the
experimental native vLLM-Omni pins into `.venv-worker`; that path must pass the
worker health check and a real smoke request on the target host. The default
`8gb-turing` profile is the recommended 8GB VRAM configuration with FP16 and
layerwise host offload.

Keep `MOSS_LOCAL_ONLY=true` and `MOSS_ALLOW_EXTERNAL_INFERENCE=false` (the
defaults). The application rejects non-loopback inference URLs before startup;
it never silently sends prompts or images to an external worker.

Review `MOSS_MAX_MEDIA_MB` before use. It caps persistent uploads plus generated rasters without counting the separate model cache; raise it only after checking available disk space.

After the checkpoint is cached, set `MOSS_OFFLINE_MODE=true` and
`HF_HUB_OFFLINE=1` to prevent runtime downloads.

For the web host, run `./scripts/check_hardware.sh` to inspect the OS, CPU, RAM, disk, NVIDIA driver/GPU telemetry, `nvtop`, CUDA availability, locally installed inference tools, and Python inference packages. The script is read-only.

The web app is local-only by default. Do not expose the worker port or add
remote access mechanisms.

Compose injects the optional worker bearer key through a mounted secret and the
vLLM environment, not through command-line arguments. Keep VLLM_API_KEY in the
ignored environment file and never paste it into a process command or log.

## Verify and operate Docker

```bash
set -a; source .env.docker; set +a
./scripts/check_vllm_worker.sh
./scripts/smoke_live_moss.sh
docker compose --env-file .env.docker -f compose.yaml ps
./scripts/docker_down.sh
```

Open `http://127.0.0.1:8002` and create the first administrator. The local
worker is the only image-generation backend; the web app does not use an
external or public inference service.

`smoke_live_moss.sh` is an optional end-to-end check: it contacts the configured worker, verifies that the selected MOSS checkpoint is served, submits one real image-generation request, validates the returned PNG, and writes `/tmp/moss-live-smoke.png` by default. It does not generate a placeholder image or fall back to another service. Set `MOSS_LIVE_OUTPUT`, `MOSS_LIVE_PROMPT`, or `MOSS_LIVE_SIZE` to customize the check.

Uploaded-image editing is intentionally unavailable in MOSS-only mode because the verified MOSS API has no image-conditioning input. The UI and API report that limitation instead of silently generating an unrelated text-only image.

For the explicit native-worker path, activate `.venv-worker`, run
`./scripts/start_vllm_omni.sh`, then use `./scripts/start.sh` and
`./scripts/stop.sh` for the web process. The native launcher allows an
unauthenticated worker only on loopback when
`MOSS_ALLOW_LOOPBACK_UNAUTHENTICATED_VLLM=true`; set `VLLM_API_KEY` if you want
defense-in-depth authentication even for the local socket.

The host stores SQLite data, uploads, generated outputs, model weights, and
worker caches under `data/`. Preserve the model cache after the first download.

## Development checks

```bash
python3 -m py_compile backend/app/*.py backend/tests/*.py
bash -n scripts/*.sh
cd frontend && npm ci && npm run build
cd .. && python scripts/test_async.py
```

Frontend versions are pinned in `frontend/package.json` and
`frontend/package-lock.json`; keep both files so transitive versions remain
reproducible.

If `pytest-asyncio` is unavailable, `scripts/test_async.py` runs the ten
async-marked checks directly with their required pytest fixtures. This is a
fallback test runner, not a replacement for the complete plugin-backed suite.

Use `./scripts/dev.sh` for the loopback Vite/FastAPI development workflow.
Set the `MOSS_DEV_*` variables explicitly only when a private-network
development bind is intentionally required. The default Docker bind remains
loopback-only; do not configure port forwarding, UPnP, tunnels, or public
reverse proxies.

## Optional user service

The repository includes `deploy/systemd/moss-image-generator.service`. It is a template
for the current checkout and is not enabled automatically. After reviewing the
absolute paths, `.env`, and firewall policy:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/moss-image-generator.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now moss-image-generator.service
systemctl --user status moss-image-generator.service
```

Use `systemctl --user disable --now moss-image-generator.service` to remove automatic
startup. Do not enable it before the application has been tested on loopback.
