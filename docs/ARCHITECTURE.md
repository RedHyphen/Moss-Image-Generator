# Architecture

## Runtime topology

```text
Local browser and local services
        │ authenticated same-origin HTTP/SSE
        ▼
FastAPI web app ── SQLite + data/{uploads,images}
        │ persistent single-worker queue
        └── MOSS jobs ── loopback HTTP ──> local vLLM-Omni worker
                                          └── /v1/images/generations
```

MOSS-Image is the only active image backend. The web container owns
authentication, account-scoped persistence, validation, queue management,
local file storage, and the browser API. The local vLLM-Omni worker performs
the actual MOSS inference and returns `b64_json` images; no remote, public, or
cloud inference fallback exists.

## Backend boundaries

- `backend/app/main.py` — authenticated REST/SSE routes, network policy,
  account ownership checks, model/status APIs, and error handling.
- `backend/app/controller.py` — structured request interpretation. It rejects
  uploaded-image conditioning while MOSS-only mode is enabled, and carries a
  completed generation's prompt into explicit follow-up requests as a
  version-linked prompt continuation. It never claims that MOSS received the
  prior raster.
- `backend/app/db.py` — SQLite schema, migrations, account ownership columns,
  conversation/message/image/reference/job persistence, and queue state.
- `backend/app/jobs.py` — persistent single-worker queue and restart recovery.
- `backend/app/inference.py` — private vLLM-Omni MOSS client and local output
  validation.
- `backend/app/storage.py` — content validation, PNG normalization, dimension
  limits, and safe storage paths.
- `frontend/src/main.tsx` — responsive conversational workspace, MOSS prompt
  controls, progress display, gallery, and account session UI.

Jobs move through `queued → running → completed|failed|cancelled`. The
configured active queue limit is process-wide, so one account cannot bypass
the worker's capacity by submitting through another account. Progress is
shown only when the backend reports a known percentage; otherwise the UI uses
an indeterminate live state. Restarts recover interrupted jobs as failed and
preserve their metadata rather than silently rerunning them.

## Account and security boundary

Every conversation, reference, generated image, and job is associated with the
authenticated user. Routes apply ownership filters before reading, changing,
or serving files. A legacy database with one account can be migrated
automatically; when multiple accounts exist, startup requires the explicit
`MOSS_LEGACY_OWNER_USERNAME` migration choice instead of exposing old rows to
the first account. Passwords are PBKDF2 hashes;
plaintext credentials are never stored in the repository, environment files,
or logs.

The `DELETE /api/account/history` endpoint performs an account-scoped history
clear. It refuses to run while that account has active jobs, deletes database
rows in one transaction, and removes only unreferenced media under the approved
storage roots. Model caches, accounts, and other users' records are outside its
scope.

The web server binds to loopback by default and applies no network allowlist
when `MOSS_TRUSTED_SUBNETS` is empty. Optional private or link-local network
binding requires an explicit trusted-subnet configuration. The vLLM worker
remains on loopback. `MOSS_LOCAL_ONLY=true` rejects non-loopback inference URLs
before any request can be sent. No forwarding, UPnP/NAT-PMP, tunnel, or public
service is configured.

## Image-editing boundary

MOSS-Image's verified API is text/layout generation only. The former local
Diffusers editor and its model-management paths have been removed from the
runtime and Docker image. Upload controls are not part of the active product;
legacy image-conditioned requests fail validation before queue insertion
rather than pretending that text-only regeneration used pixels.
Follow-up prompts can extend the previous text prompt and preserve a parent
image relationship, but they remain new MOSS raster generations without
guaranteed visual continuity.
