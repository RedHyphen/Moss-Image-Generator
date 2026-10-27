# Troubleshooting

## Root-cause workflow

Trace the complete path before changing code:

```text
browser → authenticated API → validation → account-scoped queue
        → vLLM-Omni worker → b64_json output → local storage → UI
```

Record the symptom, reproduction, failing stage, evidence, root cause,
corrective change, validation result, and remaining risk. Do not hide a
backend failure with a hardcoded success response or a fabricated image.

## Worker unavailable

Run the read-only checks first:

```bash
./scripts/check_hardware.sh
./scripts/check_vllm_worker.sh
./scripts/smoke_live_moss.sh
```

Confirm the worker is listening only on `127.0.0.1:8001`, that the local web
app can reach it, and that any configured bearer key is present without
printing it.
The web app must not be changed to use a public or remote inference service as
a fallback. Check the worker logs and the vLLM model identifier before
changing offload or precision settings.

## Generation fails or is slow

Check the persistent job error, the vLLM worker log, and available VRAM/RAM.
The local 8GB profile uses layerwise offload and a serialized queue because the
MOSS checkpoint is larger than a comfortable resident budget on an 8GB VRAM
configuration. Reduce resolution, steps, or queue pressure only after confirming the
actual failure. Do not claim that a request completed if the returned image
was not validated and stored.

## Prompt appears unable to send after a rollback or account switch

Trace the browser request in the web logs and inspect the authenticated
conversation ID. A direct API success is not enough: the browser may still
have a cached entrypoint, or it may retain a conversation selected under a
different account. The entrypoint is now served with `Cache-Control: no-store`,
and login/logout resets account-scoped conversation state. Refresh the page
once after deploying this fix. If submission returns `200` but waits, inspect
the queue: MOSS requests are serialized and a prior active job may be using
the worker. Do not restart until that job completes or is proven hung.

## Uploaded-image request is rejected

This is expected: the active product has no image-editing pipeline. Official
MOSS-Image generation does not accept an uploaded-image conditioning tensor, so
the controller rejects img2img, inpainting, outpainting, and multi-reference
requests before queueing. This protects users from a prompt-only result being
presented as an edit.

## Conversations or images are missing

Confirm that the browser is logged into the intended account. Ownership is
enforced on conversation, reference, image, and job queries; another account
should receive an empty list or 404. Check `data/moss.sqlite3` and the
container logs without exposing prompt contents or session secrets. Do not
remove or recreate the database before taking a backup.

## Restart and active jobs

Before rebuilding or restarting, inspect the queue and wait for an active
request to complete. Restart only when no job is running or when the job is
confirmed hung. A restart marks interrupted jobs failed and preserves their
metadata; it does not silently rerun expensive MOSS inference.

## Local boundary

Keep `MOSS_HOST=127.0.0.1`, `MOSS_TRUSTED_SUBNETS` empty,
`MOSS_VLLM_URL=http://127.0.0.1:8001`, and `MOSS_LOCAL_ONLY=true`. Do not
expose the worker port or add forwarding, UPnP, tunnels, or public reverse
proxies.
