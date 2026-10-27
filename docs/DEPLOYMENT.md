# Deployment

MOSS is local-only by default. The FastAPI web app binds to `127.0.0.1:8002`;
the local vLLM-Omni image worker binds to `127.0.0.1:8001`. Prompts, generated
images, model weights, and optional local chat assistance remain on this host.
The application rejects non-loopback inference URLs while
`MOSS_LOCAL_ONLY=true`.

## Verified local Docker deployment

The supported and verified deployment is Docker Compose with the official
`vllm/vllm-omni:nightly` worker. It keeps both the web app and inference API on
loopback:

```bash
./scripts/prepare_docker_env.sh
./scripts/docker_up.sh
set -a; source .env.docker; set +a
./scripts/check_vllm_worker.sh
./scripts/smoke_live_moss.sh
```

The worker uses GPU 0, the local Hugging Face/model cache under `data/models`,
and explicit layerwise offload for both the DiT and text encoder. The first
startup requires an NVIDIA driver, NVIDIA Container Toolkit, and a network
connection to obtain the image and model. Set `HF_HUB_OFFLINE=1` only after
those assets are cached.

## Experimental native local deployment

Install the web application and the matching local vLLM-Omni environment:

```bash
./scripts/install.sh
```

The installer creates `.venv` for FastAPI. Native worker installation is an
explicit opt-in with `MOSS_INSTALL_LOCAL_WORKER=true`; it creates `.venv-worker`
with the pins in `scripts/install_local_worker.sh`. Those native pins are not
the verified Docker worker and must pass both `check_vllm_worker.sh` and the
real `smoke_live_moss.sh` request on the target machine before use. Keep vLLM
and vLLM-Omni on the same major/minor release line.

Copy `.env.example` to `.env` if the installer did not do so, then review:

```bash
MOSS_LOCAL_ONLY=true
MOSS_VLLM_URL=http://127.0.0.1:8001
MOSS_GPU_PROFILE=8gb-turing
VLLM_MODE=native
VLLM_HOST=127.0.0.1
VLLM_PORT=8001
CUDA_VISIBLE_DEVICES=0
VLLM_DTYPE=float16
MOSS_ALLOW_FP16_EXPERIMENTAL=true
```

Set `MOSS_MODEL_PATH` to an absolute local checkpoint directory when operating
offline. The directory must contain `model_index.json`. If it is empty, vLLM
uses the configured Hugging Face model ID and the local Hugging Face cache.
Download the model once, then set `MOSS_OFFLINE_MODE=true` and
`HF_HUB_OFFLINE=1`.

Start and verify the worker before starting the web app:

```bash
source .venv-worker/bin/activate
./scripts/start_vllm_omni.sh
./scripts/check_vllm_worker.sh
./scripts/smoke_live_moss.sh
./scripts/start.sh
```

The smoke test is the real `/v1/images/generations` path and validates the
returned PNG. It does not create a placeholder or use a remote fallback.

The web app is available at `http://127.0.0.1:8002`. The worker is not a
browser-facing service and must remain on loopback port 8001.

## Recommended 8GB VRAM profile

This profile is intended for constrained systems with approximately 8GB of
available VRAM. Exact fit and throughput depend on the GPU, driver, vLLM-Omni
build, resident processes, and workload.

The profile uses:

- FP16 instead of the official BF16 resident path for compatibility with the
  constrained profile and older GPU support.
- vLLM-Omni layerwise diffusion offload for the DiT and text encoder.
- One visible GPU and one queued request.
- An automatic pixel ceiling that reserves 20% of total VRAM and rounds down
  to the nearest standard budget. An 8GB card resolves to 589,824 pixels;
  this host's 16GB RTX 4060 Ti resolves to 1,048,576 pixels (1024×1024).

This is a deliberate experimental compatibility path. The launcher checks GPU
memory and compute capability, refuses BF16 on unsupported devices, and refuses
an 8GB-class GPU without an offload configuration. It cannot prove inference on
a machine whose NVIDIA driver is unavailable; run `./scripts/check_hardware.sh`
and the real smoke test on the target host before calling the deployment
validated.

When `MOSS_MAX_PIXELS=auto`, native startup resolves the ceiling from the first
visible NVIDIA GPU. Docker startup resolves it on the host in
`scripts/docker_up.sh` because the web container does not need direct GPU
access. Set a numeric `MOSS_MAX_PIXELS` only as an intentional override.

The official MOSS model remains a 5B diffusion model. Resident full-model
serving is not expected to fit in 8GB; offload lowers VRAM residency at the
cost of substantial transfer time. Reduce resolution or steps if the target
driver still reports CUDA out-of-memory.

## Security and operations

Keep `MOSS_LOCAL_ONLY=true`, `MOSS_ALLOW_EXTERNAL_INFERENCE=false`, and the
worker on `127.0.0.1`. The default uses `MOSS_HOST=127.0.0.1` with an empty
`MOSS_TRUSTED_SUBNETS`. Do not add router forwarding, UPnP/NAT-PMP, tunnels,
or public reverse proxies. A local bearer key is
optional but may be configured with `VLLM_API_KEY` for defense in depth. The
Compose worker receives that value through a mounted secret/environment and not
as a command-line argument.

Before stopping or restarting the web app or worker, inspect the queue and
worker logs. Wait for a running generation to finish or document that it is
stuck; a restart can mark an active job failed and does not safely resume it.

The web app stores SQLite state and generated media under `data/`; model and
worker caches are also local and intentionally ignored by Git.
