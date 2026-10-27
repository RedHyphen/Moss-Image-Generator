# Test Results

Test date: 2026-10-23.

## Remediation verification

Verification date: 2026-10-26.

- `make check` passed: shell syntax, Python compilation, 123 backend tests,
  frontend TypeScript/Vite build, and Selenium browser acceptance.
- Browser acceptance used temporary application data and a deterministic local
  mock vLLM endpoint. It passed setup/login, conversation selection, negative
  prompt entry, prompt submission, queue transition, terminal result, and
  rendered PNG checks without touching the live database or worker.
- `npm ci` completed successfully with no reported vulnerabilities and did not
  change the committed lockfile.
- Database migration coverage now verifies explicit legacy ownership selection,
  expired-session cleanup, global queue capacity, and transactional generation
  finalization.
- Runtime monitoring, portable media keys, media reconciliation, worker attempt
  identity, and secret-argv checks passed in their focused test suites.
- The refreshed Docker deployment is healthy on loopback, vLLM serves
  `OpenMOSS-Team/MOSS-Image-SFT`, the worker command contains no `--api-key`,
  and a real authenticated 512×512 two-step MOSS generation produced a valid
  PNG in `/tmp`.

Remaining verification: recommended 8GB VRAM fit/throughput, live
crash/restart during inference, native/Docker data migration, and host-level
firewall policy.

## Passed

- Backend regression suite: `123 passed` with pytest-asyncio.
- Python compilation: `python -m py_compile backend/app/*.py backend/tests/*.py`.
- Frontend production build: `npm run build` passed with TypeScript and Vite.
- Launcher shell syntax and 8GB-profile simulation passed.
- Local-only URL validation rejects non-loopback inference targets by default.
- Resolution sizing is automatic: it reserves 20% of visible GPU VRAM and
  rounds down to a standard pixel budget. The 8GB baseline resolves to 589,824
  pixels; the current 16GB host resolves to 1,048,576 pixels.
- Live NVIDIA/vLLM-Omni inference: passed on the host's RTX 4060 Ti using the
  official CUDA 13 nightly image, which contains the `MOSSImagePipeline`
  registry entry. Explicit DiT plus text-encoder layerwise host offload reduced
  model-runner residency to 3.96 GiB; the worker completed its warm-up and
  served `OpenMOSS-Team/MOSS-Image-SFT` on authenticated loopback port 8001.
- Local Docker deployment: passed; the web container is healthy on
  `127.0.0.1:8002` and the worker remains on `127.0.0.1:8001`.
- Browser acceptance: passed through Chromium against the local URL using a
  temporary dedicated account: login, conversation selection, prompt send,
  visible queue transition, terminal completion, and a rendered PNG in the
  conversation UI. The temporary account, job, image, and conversation were
  removed after the check.
- Docker end-to-end generation: passed through the browser-backed conversation
  path with a real 512×512, 2-step local MOSS request; no placeholder or remote
  fallback was used.
- Account verification: `iamred2708` exists in the mounted database with a
  working password hash; plaintext credentials are not stored.
- Account isolation: database and API regression tests confirm conversations,
  images, references, and jobs are not returned across accounts.
- Mobile behavior: the production bundle includes the viewport declaration and
  responsive conversation/sidebar breakpoints through 720px and 620px.

## Intentional limitations

- MOSS-Image has no verified uploaded-image conditioning in the active API.
  Image-to-image editing, inpainting, outpainting, and variations are rejected
  clearly rather than routed to a different model or simulated from text.
- Local-only operation still requires checking the host firewall; the
  application does not create forwarding, UPnP, tunnels, or public listeners.
  The vLLM worker remains loopback-only.
- The 8GB Turing FP16 path remains target-hardware-validation pending. This
  host is a 16GB RTX 4060 Ti, so the live run validates the offload path but
  does not prove performance or fit on the recommended 8GB VRAM configuration.

## Root-cause notes

- Conversation leakage risk came from legacy global list/get calls and rows
  without ownership. The migration added `user_id` fields, backfilled legacy
  records to the first account, and applied owner filters at every protected
  route. Regression tests cover the complete API boundary.
- The previous editor path produced poor identity/realism results and did not
  meet the MOSS goal. Root-cause correction removed that backend, its model
  controls, and its scripts instead of presenting it as MOSS conditioning.
- Prompt-send diagnosis: the backend accepted and completed prompts, but the
  browser could retain the discarded frontend after rollback and account
  switching could retain an invalid conversation ID. No-cache HTML headers and
  account-state reset/validation were added; the fix was rebuilt and verified
  with the authenticated end-to-end generation above. The first temporary
  browser harness run also exposed a selector mistake before submission; the
  corrected harness exercised the actual controls and passed.
