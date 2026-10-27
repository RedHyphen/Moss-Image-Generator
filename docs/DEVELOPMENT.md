# Development Guide

This guide is the source of truth for changing Moss-Image-Generator. Read it
before changing routes, queue behavior, inference, storage, or the browser
submission flow.

## Repository map

```text
backend/app/       FastAPI application and local runtime services
backend/tests/     Backend unit and integration-style tests
frontend/src/      React/TypeScript browser application
scripts/           Installation, validation, startup, and worker operations
docs/              Architecture, deployment, research, usage, and evidence
deploy/systemd/    Reviewed service templates; never enabled automatically
data/              Ignored runtime state, media, SQLite, and model caches
```

Keep these boundaries stable:

| Area | Owns | Must not own |
| --- | --- | --- |
| `main.py` | HTTP routes, request validation, auth dependencies, response shaping | vLLM payload construction or direct filesystem policy |
| `controller.py` | Request interpretation and execution-plan metadata | Image pixels, model calls, or account access |
| `db.py` | SQLite schema, migrations, ownership filters, persistence | HTTP concerns or filesystem deletion |
| `jobs.py` | Serialized queue state and job lifecycle | Browser rendering or network policy |
| `inference.py` | Private local vLLM client and returned-image validation | User authorization or fake fallback output |
| `storage.py` | Upload/output validation, paths, PNG normalization, media limits | Queue decisions or model conditioning |
| `layout.py` | Public layout schema validation and model-boundary conversion | Storage references or prompt routing |
| `frontend/src/main.tsx` | Authenticated UI state, forms, queue display, gallery rendering | Secrets, server-side validation, or inference calls |

The request path is always:

```text
browser → authenticated API → validation → account-scoped queue
        → loopback vLLM-Omni → validated local output → SQLite/files → UI
```

MOSS-Image is text/layout generation only. Do not add an image-editing
fallback, public inference endpoint, placeholder image, or silent remote
service.

## Local workflow

Install the project-local environments first:

```bash
./scripts/install.sh
```

Run the standard static and automated checks:

```bash
make check
```

The individual checks are useful when iterating:

```bash
make backend-test       # PYTHONPATH=backend .venv/bin/pytest -q
make frontend-build     # TypeScript + Vite production build
make browser-acceptance # headless Firefox: login → prompt → local mock worker → rendered PNG
make syntax             # bash -n scripts/*.sh
make compile            # Python bytecode compilation
```

The development script defaults to a loopback API at `127.0.0.1:8000` and a
Vite browser at `127.0.0.1:5173`. It reads `.env` for application settings but
uses the `MOSS_DEV_*` values for its bind and Vite proxy, so the production
bind cannot accidentally point the browser proxy at the wrong port:

```bash
./scripts/dev.sh
```

For a LAN development browser, set `MOSS_DEV_HOST`, `MOSS_DEV_API_URL`, and
`MOSS_DEV_FRONTEND_HOST` explicitly. Keep the worker at `127.0.0.1:8001`.

For Docker, use the checked-in Compose shape and the ignored local env file:

```bash
./scripts/prepare_docker_env.sh
docker compose --env-file .env.docker -f compose.yaml config --quiet
./scripts/docker_up.sh
```

Before restarting a worker or web service, inspect the queue and logs. Wait
for active generation to finish or document that it is hung.

## Coding conventions

### All languages

- Use the existing stack and dependency versions unless a change is justified
  in the documentation.
- Keep files focused. Put new behavior in the module that owns its boundary;
  do not grow a convenience helper into a second service layer.
- Use section headers in large modules: constants/types, helpers, lifecycle,
  route or component groups, and integration boundaries.
- Prefer named constants for policy values, limits, route-independent defaults,
  and repeated strings. Constants use `UPPER_SNAKE_CASE`; local variables and
  functions use the language convention.
- Keep error messages actionable and safe: include the failed stage, never
  include passwords, API keys, session tokens, or raw prompt data in logs.

### Python

- Use Python 3.11+, 4-space indentation, type annotations, and small
  snake_case functions.
- Order a module as imports, constants/types, exceptions, pure helpers,
  service classes, then integration entrypoints/routes.
- Keep validation at the boundary with Pydantic or explicit helpers. Raise
  domain-specific errors from services and translate them to HTTP responses at
  the route boundary.
- Use async for network, queue, and other I/O; keep pure transformations
  synchronous and easy to test.
- Database methods own SQL and transactions. Callers must not construct ad hoc
  SQL for normal application behavior.

### TypeScript/React

- Keep TypeScript strict and use functional components.
- Order a module as imports, types, immutable constants, pure formatting or
  validation helpers, API helpers/hooks, the page shell, then presentational
  components.
- Use `camelCase` for variables, functions, props, and state; use
  `UPPER_SNAKE_CASE` for immutable policy constants; use `PascalCase` for
  components and types.
- Prefer `unknown` plus narrowing over `any`. Keep server response types close
  to the API boundary and do not duplicate server validation in a way that
  changes its meaning.
- Keep browser actions accessible: labels, button names, keyboard submission,
  visible loading/error states, and a real terminal result must remain usable.

### Shell and configuration

- Start scripts with `set -euo pipefail`, quote paths, use task-specific
  variable names, and validate user-controlled configuration before starting
  services.
- Keep worker APIs loopback-only. Any private-network access goes through the
  authenticated web app; never add router forwarding, tunnels, UPnP/NAT-PMP,
  or public cloud inference.
- Put safe defaults in `.env.example`; put machine-specific secrets and
  addresses only in ignored `.env`/`.env.docker` files.

## Testing expectations

Classify evidence accurately:

- **Automated:** `make check`, focused tests, type checks, and shell syntax.
- **Browser acceptance:** real login, conversation selection, prompt send,
  queue transition, terminal job state, and rendered output.
- **Integration:** web app talking to the local vLLM worker and local storage.
- **Hardware:** GPU visibility, VRAM fit, worker initialization, and a real
  image request on the target card.
- **Blocked:** a check that could not run; record the reason and remaining
  risk.

`make browser-acceptance` uses temporary application data and a deterministic
local mock worker; it never touches the checked-in `data/` directory or the
live vLLM process. Install Firefox and geckodriver for the local run, or let
Selenium Manager provision geckodriver in an environment that allows it.

Passing route tests or `/health` is not browser acceptance. Any change to the
prompt, queue, worker, storage, or UI submission path requires the real browser
flow before it is called complete.

## Change handoff

Use a focused commit subject such as `Fix local worker memory offload`. The
change summary should link the relevant docs and state:

1. symptom and reproduction;
2. execution path and root cause;
3. corrective change;
4. automated/browser/integration/hardware validation;
5. remaining risk or follow-up.

Before staging, inspect the index:

```bash
git status --short --ignored
git diff --check
git diff --cached --name-only
```

The staged list must not contain `data/`, `.env.docker`, model files,
`frontend/dist/`, virtual environments, databases, or test output.
