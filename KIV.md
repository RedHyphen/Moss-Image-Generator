# Moss-Image-Generator — Keep-in-View Review

Review date: 2026-10-27
Repository: <repository-root>
Review type: end-to-end design, architecture, implementation, security, persistence, performance, testing, and developer-experience review with remediation follow-up

## Executive summary

Moss-Image-Generator is a coherent local-first product. FastAPI owns authentication, API validation, SQLite state, local media, and a persistent serialized queue. A loopback vLLM-Omni worker owns MOSS-Image inference. The active product correctly rejects unsupported uploaded-image conditioning instead of silently using another backend.

The strongest decisions are the local-only deployment, loopback-only worker, account-scoped reads, CSRF/session controls, path containment, server-side validation, and real local-worker/browser evidence recorded in the repository.

The initial review found boundary risks in direct-job ownership, generation finalization, media reconciliation, restart identity, deployment paths, telemetry scope, queue semantics, and secret handling. These have been remediated in focused commits. The current 80%-of-total/free-VRAM ceiling is a useful policy guard but is not proof of safe runtime memory on an 8 GB card.

The project now has a committed Selenium browser gate, lock-enforcing installation, migration regression coverage, and CI wiring. The recommended 8GB VRAM path remains hardware-validation dependent because exact fit varies by GPU, driver, runtime build, resident processes, and workload. No Critical finding was identified.

## Scope and methodology

Inspected repository structure, README and docs, Compose/native scripts, FastAPI routes, SQLite schema/migrations, storage and inference adapters, queue lifecycle, React UI, tests, and current runtime state. The principal flow traced was:

browser login and prompt
  → authenticated and CSRF-protected API
  → request and ownership validation
  → SQLite message/job state
  → serialized in-process worker
  → loopback vLLM-Omni request
  → base64 PNG validation and local write
  → gallery/message/job finalization
  → SSE/polling and React rendering

The initial review was read-only. The follow-up then implemented each actionable finding in focused commits, with focused tests and a final full gate. Runtime data was not modified by the remediation or browser checks; browser checks use temporary data and a deterministic local mock worker.

## Architecture assessment

The system is a single FastAPI web process plus one persistent in-process queue worker, a SQLite database in WAL mode, local upload/output storage, and a separate vLLM-Omni service. The main implementation boundaries are:

- backend/app/main.py: composition root, middleware, authentication, request models, routes, and response shaping.
- backend/app/monitoring.py: independently testable web-runtime diagnostics and probe caching.
- backend/app/db.py: SQLite schema, migrations, ownership filters, queue state, and transactions.
- backend/app/jobs.py: job claim, inference invocation, output persistence, terminal state, and restart recovery.
- backend/app/inference.py: private vLLM request contract, negative prompt forwarding, output decoding, and PNG normalization.
- backend/app/controller.py: deterministic conversational intent and execution-plan metadata.
- backend/app/storage.py: upload validation, containment, filename handling, and media accounting.
- frontend/src/main.tsx: authenticated UI state, conversations, composer, queue, layout editor, worker card, and gallery.
- compose.yaml and scripts/: Docker/native startup, local-only worker policy, VRAM resolution policy, and health checks.

SQLite is authoritative for users, sessions, conversations, messages, jobs, gallery rows, and references. Media files are separate filesystem state referenced by database paths. The worker owns the application-side job lifecycle while vLLM owns the actual inference request; each claimed job now carries a durable attempt ID through the local request headers.

The boundaries are understandable for the current scope. The largest maintainability pressure is that main.py and main.tsx are large orchestration modules and that file/database state is not represented by one durable finalization protocol.

## End-to-end coverage

Fully traced:

- setup/login/logout, sessions, CSRF, account-scoped reads, and protected routes;
- conversation selection, prompt submission, negative-prompt propagation, planning, queueing, worker processing, gallery persistence, and UI rendering;
- direct job submission, layout/dimension validation, worker health gating, queued cancellation, retry, and SSE snapshots;
- local vLLM payload construction, PNG validation, local storage, metadata, and rendered output;
- uploads, reference ownership, media quota, gallery-to-reference promotion, delete, and account history cleanup;
- local bind policy, loopback worker isolation, API-key use, and Docker/native launchers;
- automatic resolution calculation and frontend preset filtering;
- multiple-subject negative-prompt request wiring and live worker spot checks.

Partially verified or unverified:

- recommended 8GB VRAM fit, peak VRAM, throughput, and sustained behavior;
- crash/restart during a live vLLM request and duplicate/late completion behavior;
- migration of stored data between native and Docker path layouts;
- high-volume cleanup, concurrency, long-lived SSE load, and host-level firewall policy.

## Findings summary

| ID | Severity | Confidence | Status | Short title |
| --- | --- | --- | --- | --- |
| KIV-001 | Medium | Confirmed | Already Addressed | Direct jobs trust client-supplied relationships |
| KIV-002 | High | Confirmed | Already Addressed | Job finalization is not atomic |
| KIV-003 | Medium | Confirmed | Already Addressed | Orphan media is not reconciled |
| KIV-004 | Medium | Strongly supported | Already Addressed | Restart recovery lacks attempt identity |
| KIV-005 | Medium | Strongly supported | Already Addressed | Absolute paths couple deployment modes |
| KIV-006 | Medium | Confirmed | Already Addressed | Worker telemetry probes the wrong runtime |
| KIV-007 | Medium | Confirmed | Already Addressed | Hardware probes can block the event loop |
| KIV-008 | Medium | Requires verification | Requires Verification | Total-VRAM resolution is not a runtime guarantee |
| KIV-009 | Medium | Confirmed | Already Addressed | Worker API key is exposed in command metadata |
| KIV-010 | Medium | Confirmed | Already Addressed | Browser acceptance is not reproducible from the repo |
| KIV-011 | Medium | Confirmed | Already Addressed | Local install does not enforce the frontend lock |
| KIV-012 | Medium | Strongly supported | Already Addressed | Legacy rows are assigned to the first user |
| KIV-013 | Medium | Confirmed | Already Addressed | Shared-path cleanup has a 100,000-row cutoff |
| KIV-014 | Low | Confirmed | Already Addressed | Expired sessions are retained |
| KIV-015 | Low | Confirmed | Already Addressed | Queue capacity policy is inconsistent |
| KIV-016 | Low | Confirmed | Already Addressed | Variation branch is unreachable |
| KIV-017 | Improvement | Design improvement | Already Addressed | Orchestration modules are too broad |
| KIV-018 | Improvement | Design improvement | Already Addressed | No CI/lint/migration quality gate |

Counts: 0 Critical, 1 High, 12 Medium, 3 Low, 2 Improvement.
Confidence: 12 Confirmed, 3 Strongly supported, 1 Requires verification, 2 Design improvement.
Current remediation status: 17 addressed, 1 requires target-hardware verification, and no new finding was identified in the final pass.

## Remediation follow-up

The original finding descriptions below preserve the review evidence. Current
implementation status and verification are recorded here so the historical
finding text is not mistaken for the current behavior.

| Finding | Remediation and current effect | Verification evidence |
| --- | --- | --- |
| KIV-001 | Public direct jobs now reject internal relationship fields; conversational relationships remain server-owned. | `backend/tests/test_api.py` |
| KIV-002 | Image rows, message state, assistant message, and conversation timestamp finalize in one SQLite transaction; failed finalization removes current output files. | `backend/tests/test_db.py`, `backend/tests/test_jobs.py` |
| KIV-003 | Startup reports old unreferenced media and `scripts/reconcile_media.py` provides report-only or explicit-delete reconciliation. | `backend/tests/test_storage.py` |
| KIV-004 | Every claimed attempt receives a persisted UUID and is sent to vLLM with the job identity; retries clear the old attempt. | `backend/tests/test_db.py`, `backend/tests/test_inference.py` |
| KIV-005 | New media references use portable data-relative keys while safe legacy absolute paths remain readable. | `backend/tests/test_storage.py`, `backend/tests/test_jobs.py` |
| KIV-006 | Web diagnostics identify the web runtime and no longer stand in for vLLM-worker health. | `backend/tests/test_monitoring.py` |
| KIV-007 | Hardware diagnostics run through `asyncio.to_thread` with a coalescing probe lock. | `backend/tests/test_monitoring.py` |
| KIV-008 | Automatic sizing uses 80% total VRAM with standard-value rounding; current free VRAM remains diagnostic telemetry so a resident worker cannot hide stable presets. Exact 8 GB target fit remains unverified. | `backend/app/resolution.py`, `backend/tests/test_config.py`; current fix |
| KIV-009 | Compose uses a portable file-backed Docker secret and mounted entrypoint; native launchers export the key without putting it in argv. | Compose config validation, deployed command inspection, worker health, and real smoke generation |
| KIV-010 | Selenium acceptance uses isolated data and a deterministic local worker, covering setup/login, conversation, negative prompt, queue, terminal result, and rendered PNG. | `make browser-acceptance` |
| KIV-011 | Developer installation now uses `requirements-dev.txt` and `npm ci`; installation and audit completed without lockfile changes. | `npm ci` |
| KIV-012 | Legacy unowned rows are automatically assigned only for a single account; multiple-account databases require `MOSS_LEGACY_OWNER_USERNAME`. | `backend/tests/test_db.py` |
| KIV-013 | Gallery ownership checks query all records with `limit=None` instead of a 100,000-row cap. | `backend/tests/test_monitoring.py` |
| KIV-014 | Expired sessions are deleted on lookup and startup. | `backend/tests/test_db.py` |
| KIV-015 | All route fast-fail checks now use the same process-wide queue scope as the authoritative insert. | `backend/tests/test_api.py`, `backend/tests/test_db.py` |
| KIV-016 | The unused image-reuse/regeneration route, request model, and variation branch were removed; randomized text-to-image batches remain distinct. | `backend/app/main.py`, `backend/tests/test_api.py` |
| KIV-017 | Runtime monitoring was extracted from `main.py` into `monitoring.py` with focused tests; the frontend composition root remains intentionally unchanged pending a future incremental extraction. | `backend/tests/test_monitoring.py` |
| KIV-018 | CI runs the lock-enforcing installer and `make check`; the check includes compile, 123 backend tests, frontend build, shell syntax, and browser acceptance. Migration fixtures are included in the database tests. | `.github/workflows/check.yml` |

## Detailed findings

### KIV-001 — Direct jobs trust client-supplied relationships

Severity: Medium
Confidence: Confirmed
Affected components: backend/app/main.py:156-175 and 749-778; backend/app/jobs.py:163-212; backend/app/db.py:329-333

Expected behavior: An authenticated user can create relationships only within owned conversations/messages, or the server derives all internal relationship IDs.

Current behavior: JobRequest accepts conversation_id, message_id, and execution_plan. /api/jobs persists them after generic validation without checking that the referenced records exist and belong to the session. The worker later updates messages and creates assistant assets using those values.

End-to-end flow: authenticated request → JobRequest → create_job body.model_dump → params_json → JobWorker.process updates/inserts records using client-provided IDs.

Root cause: A browser-facing request model contains internal worker bookkeeping fields. Ownership validation exists in the conversational route but not the direct route.

Impact: A caller with another account’s identifiers can attempt cross-account message mutation or attach output to another account’s conversation. UUID guessing lowers likelihood but does not make the contract safe.

Evidence: main.py:171-175 declares the fields; main.py:765-776 stores them; jobs.py:163-212 consumes them.

Recommended resolution: Split public and internal enqueue models. Remove relationship fields from the public direct API or validate existence/ownership and derive the execution plan server-side.

Dependencies and risks: Preserve the current UI direct-generation path and retry/export behavior. Add negative cross-account tests before changing migrations.

Verification plan: Two accounts; submit account B jobs using account A conversation/message IDs, nonexistent IDs, and valid same-account IDs. Assert rejection or correct association with no cross-account mutation.

Status: Already Addressed; see remediation follow-up.

### KIV-002 — Job finalization is not atomic

Severity: High
Confidence: Confirmed
Affected components: backend/app/inference.py:84-116; backend/app/jobs.py:108-212; backend/app/db.py:409-415

Expected behavior: A generation completes with a complete consistent file/database state, or failed work is fully recoverable without partial gallery/message state.

Current behavior: inference.py writes PNGs before returning. jobs.py then commits one image row at a time, updates the user message, inserts an assistant message, and inserts message assets. Each database helper commits independently. A later exception leaves earlier files/rows while the worker marks the job failed.

End-to-end flow: vLLM response → PNG writes → image-row commits → message updates/inserts → asset commits → terminal job update. Failure at any later step leaves earlier state.

Root cause: Filesystem writes and related SQLite writes have no shared transaction or compensating cleanup protocol.

Impact: Failed jobs can have partial gallery rows, missing/partial conversation assets, ownerless files, and duplicate-looking outputs after retry. This is a data-integrity defect under disk, database, process, or malformed multi-output failures.

Evidence: inference.py:103-113 writes files; jobs.py:145-176 commits outputs; jobs.py:177-212 commits message state; jobs.py:55-62 records failure.

Recommended resolution: Stage files, commit all related SQLite state in one transaction, then promote files atomically; or add durable finalizing state and idempotent job-keyed reconciliation.

Dependencies and risks: Must preserve completed rows and handle a crash between filesystem promotion and SQLite commit. Retry and account cleanup depend on the new contract.

Verification plan: Inject failures after each file, image row, message, and asset operation. Assert job state, row counts, file set, message state, and retry behavior. Repeat with process termination in an isolated data directory.

Status: Already Addressed; see remediation follow-up.

### KIV-003 — Orphan media is not reconciled

Severity: Medium
Confidence: Confirmed
Affected components: backend/app/storage.py:39-62; backend/app/main.py:330-347; backend/app/jobs.py:126-143; data/images/

Expected behavior: Every media file has an owner, and interrupted/failed files are safely detected and reclaimed after a grace period.

Current behavior: Quota accounting scans files and explicit deletion handles paths from deleted rows. There is no startup or periodic comparison of media files with image/reference rows. Current runtime inspection found images=0 in SQLite but two PNG files in data/images/, including one modified on the review date.

End-to-end flow: file and row lifecycle diverges during failure → quota still counts the file → gallery/account cleanup sees no database path → file remains.

Root cause: SQLite ownership and filesystem lifecycle have no reconciliation owner.

Impact: Disk/media quota growth, stale private outputs after cleanup, and no reliable operator distinction between active and abandoned files.

Evidence: storage.py:39-62 counts files; main.py:330-347 cleans only returned database paths; jobs.py:126-143 cleans only current outputs in the media-limit path; runtime counts show the divergence.

Recommended resolution: Add a safe reconciliation command/startup task that compares canonical approved-root files to database references and quarantines only old unknown files. Use staging names or job IDs and a grace period.

Dependencies and risks: Preserve promoted references, shared paths, legacy rows, symlinks, and recently written outputs.

Verification plan: Test referenced/shared/active/recent/old-orphan/symlink/outside-root cases and verify only the intended old orphan is removed or quarantined.

Status: Already Addressed; see remediation follow-up.

### KIV-004 — Restart recovery lacks inference attempt identity

Severity: Medium
Confidence: Strongly supported
Affected components: backend/app/jobs.py:25-31; backend/app/db.py:512-523; backend/app/inference.py:84-96

Expected behavior: After restart, the app knows whether an external inference attempt is alive and does not duplicate or orphan work.

Current behavior: Startup marks all running/cancelling jobs failed. A retry can then be submitted, but the vLLM request has no durable request/attempt ID or idempotency contract. A request may have continued in the separate worker.

End-to-end flow: app claims → long vLLM request → app restart/network loss → row becomes failed → retry → original request may still finish independently.

Root cause: Job persistence models only the web process lifecycle, not the external attempt lifecycle.

Impact: Duplicate GPU work, duplicate output, late ownerless output, misleading failure, and unsafe retries.

Evidence: db.py:512-523 marks rows failed; jobs.py:25-31 records assistant errors; inference.py:84-96 sends a request without durable attempt identity.

Recommended resolution: Add a durable attempt/lease and propagate a unique job attempt ID where supported. Distinguish lost contact from inference failure and reconcile any late result before gallery insertion.

Dependencies and risks: Depends on worker metadata/cancellation support and shutdown semantics. Do not add blind retries.

Verification plan: Long-running mock worker; terminate and restart app; complete original request; assert no duplicate/ownerless output. Repeat timeout, network reset, queued cancellation, and retry.

Status: Already Addressed; see remediation follow-up.

### KIV-005 — Absolute paths couple deployment modes

Severity: Medium
Confidence: Strongly supported
Affected components: backend/app/config.py:142-143; backend/app/storage.py:27-36; backend/app/jobs.py:163-168; compose.yaml:49-67; docs/INSTALLATION.md:75-83

Expected behavior: Data remains usable when supported native and Docker modes use the same data directory, or mode switching is explicitly prohibited.

Current behavior: Upload and generated rows store absolute paths. Native uses the project data path; Docker uses /var/lib/moss/data. File access resolves the stored absolute path against the current root.

End-to-end flow: one deployment writes path → another deployment mounts data at a different path → file is missing or rejected outside root even if the media exists.

Root cause: Environment-specific filesystem paths are persisted instead of relative media keys.

Impact: Gallery/reference loss after mode changes, cleanup failure, and backup/restore path rewriting.

Evidence: storage.py:80-85 returns a Path; jobs.py:163-168 stores it; compose.yaml:49-67 maps the Docker path; docs/INSTALLATION.md documents both deployment modes.

Recommended resolution: Persist relative keys such as images/UUID.png and resolve against the configured root. Provide a tested migration for current absolute rows.

Dependencies and risks: Preserve shared/promoted files, legacy paths, strict containment, and export behavior.

Verification plan: Create native-style data, reopen with Docker-style roots, and test serving, deletion, promotion, cleanup, and export, including legacy/outside-root rows.

Status: Already Addressed; see remediation follow-up.

### KIV-006 — Worker telemetry probes the wrong runtime

Severity: Medium
Confidence: Confirmed
Affected components: backend/app/main.py:1099-1200; frontend/src/main.tsx:547-556; compose.yaml:4-7

Expected behavior: Worker health UI reports the GPU environment that performs inference, or clearly labels web-container diagnostics.

Current behavior: /api/system runs nvidia-smi, nvcc, and a subprocess using the web process sys.executable. Compose gives GPU access to moss-image-generator-vllm, not web. The UI labels values Host GPU, PyTorch/CUDA, and CUDA devices beside vLLM worker health.

End-to-end flow: browser polls system → web container probes itself → UI presents values as worker information.

Root cause: Worker health and web/host diagnostics are combined without a telemetry boundary.

Impact: Operators can mistake unavailable web-container CUDA data for a worker failure or trust values that do not describe worker allocation, especially during 8 GB diagnosis.

Evidence: main.py:1110-1128 uses sys.executable; main.py:1141-1244 builds system data; compose.yaml:4-7 assigns GPU to the worker; main.tsx:547-556 renders the fields.

Recommended resolution: Add bounded worker telemetry or label web diagnostics separately. Keep the worker endpoint loopback-only and authenticated.

Dependencies and risks: Avoid creating a new unbounded GPU polling workload or leaking worker secrets.

Verification plan: Compare UI values with worker logs and nvidia-smi in separate containers; test worker down, web probe unavailable, and multi-GPU cases.

Status: Already Addressed; see remediation follow-up.

### KIV-007 — Hardware probes can block the event loop

Severity: Medium
Confidence: Confirmed
Affected components: backend/app/main.py:1099-1244; frontend system polling

Expected behavior: Slow diagnostics must not block prompt submission, SSE, auth, or other request handling.

Current behavior: async /api/system calls synchronous detect_hardware. It runs nvidia-smi, optionally nvcc, and a Python subprocess with an eight-second timeout. A 30-second cache reduces frequency but does not remove blocking refreshes.

End-to-end flow: frontend refresh → /api/system → synchronous subprocess → event-loop stall → prompt/SSE latency or apparent failure.

Root cause: Operational probes run inside the request handler without an executor/background snapshot.

Impact: Slow or broken diagnostics can delay the product’s primary workflow when the operator most needs it.

Evidence: main.py:1125-1137 uses synchronous subprocess.run; main.py:1141-1211 runs more probes; main.py:1240-1244 calls the probe directly from the async route.

Recommended resolution: Move probes to a bounded executor/background task and serve the latest snapshot with age/status metadata. Coalesce concurrent refreshes.

Dependencies and risks: Preserve explicit stale/unavailable UI semantics and worker readiness behavior.

Verification plan: Mock probes that sleep to timeout, send concurrent prompt/SSE requests, and measure event-loop latency.

Status: Already Addressed; see remediation follow-up.

### KIV-008 — Total-VRAM resolution is not a runtime guarantee

Severity: Medium
Confidence: Requires verification
Affected components: backend/app/resolution.py:8-94; backend/app/config.py:192-208; scripts/docker_up.sh:18-22

Expected behavior: The automatic maximum avoids realistic OOM conditions for the configured model, offload mode, batch, steps, and co-resident processes.

Current behavior: The limit reads the first visible GPU’s total VRAM, reserves 20%, scales from an 8 GB baseline, and rounds to standard pixel budgets. It does not inspect free VRAM, worker residency, allocator fragmentation, batch, steps, or peak usage.

End-to-end flow: total memory probe → static ceiling → frontend/API pixel validation → runtime allocation by vLLM.

Root cause: Static total-VRAM calibration is treated as a resource guarantee even though runtime memory is workload-dependent.

Impact: The ceiling may be too conservative or unsafe; a valid request can still OOM on the recommended 8GB VRAM configuration. No exact 8GB OOM threshold was reproduced, so this remains a verification-required risk.

Evidence: resolution.py:37-63 queries total only; resolution.py:83-94 computes the 80% budget; docker_up.sh:18-22 applies it; docs/TEST_RESULTS.md:45-47 says exact 8 GB validation is pending.

Recommended resolution: Measure peak memory per GPU/profile and include parameters that materially affect memory. Keep the 80% rule as a safety budget, expose its calibration source, and preserve an explicit risk-labeled override.

Dependencies and risks: Synchronize presets, docs, and the 8 GB claim. Free-memory checks alone are racy.

Verification plan: On the recommended 8GB VRAM configuration, record free/peak VRAM, dimensions, batch, steps, warm/cold state, and failures for all standard presets.

Status: Requires Verification; runtime sizing is improved, but exact 8GB VRAM fit and peak behavior remain hardware-dependent.

### KIV-009 — Worker API key is exposed in command metadata

Severity: Medium
Confidence: Confirmed
Affected components: compose.yaml:15-32; scripts/start_vllm_omni.sh; runtime .env.docker

Expected behavior: Worker credentials should not be exposed through argv or container inspection metadata.

Current behavior: Compose expands VLLM_API_KEY after the vLLM --api-key flag. Docker records the expanded command, making it readable to users with Docker inspection access or equivalent host privileges.

End-to-end flow: ignored env file → Compose interpolation → stored container command → process/inspect exposure → web uses the key in Authorization.

Root cause: Secret distribution uses command interpolation rather than a secret file or safer environment mechanism.

Impact: Host Docker users or leaked inspection output can recover the worker key. The worker is loopback-only, which limits but does not eliminate the risk.

Evidence: compose.yaml:25-28; runtime inspection during review confirmed the expanded value was present in container command metadata.

Recommended resolution: Use Docker secrets or a protected secret file if supported by the worker image; otherwise use a launcher that avoids putting the value in argv. Rotate the key afterward.

Dependencies and risks: The web container still needs to authenticate. Ensure logs, health output, and frontend responses never expose it.

Verification plan: Inspect Docker metadata, process command lines, logs, and health output after remediation; confirm authenticated health/generation.

Status: Already Addressed; see remediation follow-up.

### KIV-010 — Browser acceptance is not reproducible from the repository

Severity: Medium
Confidence: Confirmed
Affected components: Makefile:8-20; backend/tests/; frontend/src/main.tsx; docs/TEST_RESULTS.md:22-29; CONTRIBUTING.md:13-15

Expected behavior: The documented acceptance gate is executable against an isolated app and covers login, conversation selection, prompt send, queue transition, terminal result, and rendered output.

Current behavior: make check runs shell syntax, Python compilation, pytest, and frontend build. It does not launch the app or drive a browser. Historical docs record Chromium acceptance, and the current review ran a targeted browser request-wiring check, but no browser test is committed or wired into the standard check.

End-to-end flow: UI/API change → make check remains green → browser submission/render defect is found only by manual or external harness.

Root cause: The gate is documented but not represented by a maintained test artifact and CI job.

Impact: The most important product path can regress while repository checks pass.

Evidence: Makefile:8-20 has no browser target; docs/TEST_RESULTS.md:22-29 is historical evidence; CONTRIBUTING.md:13-15 requires a separate live check without providing one.

Recommended resolution: Add a Selenium or Playwright test target with temporary data and a deterministic mock worker, plus a separate opt-in live-worker test. Include negative prompt, terminal job, rendered PNG, failure, account switch, and cleanup cases.

Dependencies and risks: Never touch the live database in the automated path. Label mocked, integration, browser, and hardware evidence separately.

Verification plan: Run isolated browser tests against a temporary app and mock worker, then run the opt-in live browser smoke against the local worker.

Status: Already Addressed; see remediation follow-up.

### KIV-011 — Local install does not enforce the frontend lock

Severity: Medium
Confidence: Confirmed
Affected components: scripts/install.sh:7-18; frontend/package.json; frontend/package-lock.json; Dockerfile:1-7

Expected behavior: Local installation reproduces the committed dependency graph without silently rewriting the lockfile.

Current behavior: Docker uses npm ci, but scripts/install.sh uses npm install. Backend setup also uses an external uv executable and requirements without a lock/hash workflow.

End-to-end flow: developer installer → registry/local npm resolution → local dependency graph can differ from Docker/another developer → build behavior drifts.

Root cause: Local and container installation use different reproducibility contracts.

Impact: Non-repeatable builds, accidental lockfile changes, and difficult rollback.

Evidence: scripts/install.sh:10-12; Dockerfile:7; docs/INSTALLATION.md:94-96 claims package-lock reproducibility.

Recommended resolution: Use npm ci for normal local installs and document a separate dependency-update command. Define or explicitly justify the backend lock strategy.

Dependencies and risks: Preserve intentional dependency updates; do not remove the update workflow.

Verification plan: Install twice in clean temporary environments, compare dependency trees/builds, and verify dirty lockfiles fail clearly.

Status: Already Addressed; see remediation follow-up.

### KIV-012 — Legacy rows are assigned to the first user

Severity: Medium
Confidence: Strongly supported
Affected components: backend/app/db.py:116-157; backend/tests/test_db.py; docs/TEST_RESULTS.md:51-54

Expected behavior: Legacy unowned data is assigned through an explicit privacy-preserving migration choice.

Current behavior: Initialization assigns NULL user_id rows in references, conversations, images, and jobs to MIN(users.id).

End-to-end flow: old shared database → initialization → every legacy row assigned to first account → account-scoped routes expose it there.

Root cause: Legacy data has no ownership provenance, so migration uses a silent convenience guess.

Impact: Historical data may be exposed to the wrong account. The risk is limited to legacy rows but is difficult to reverse without backup.

Evidence: db.py:154-157; documented historical correction in docs/TEST_RESULTS.md:51-54.

Recommended resolution: Require a named operator account, an unassigned/claimable state, or an ownership manifest, with backup and migration counts.

Dependencies and risks: Existing installations may depend on current behavior; provide a compatibility/migration path.

Verification plan: Legacy fixture with multiple accounts and unowned rows; verify chosen owner, audit output, backup, and isolation.

Status: Already Addressed; see remediation follow-up.

### KIV-013 — Shared-path cleanup has a 100,000-row cutoff

Severity: Medium
Confidence: Confirmed
Affected components: backend/app/main.py:421-429; backend/app/db.py:417-423; delete and account cleanup routes

Expected behavior: Before unlinking a file, every possible database reference is considered.

Current behavior: _image_record_uses_path calls list_images(limit=100000). An older shared row beyond that limit is invisible and the file can be treated as unreferenced.

End-to-end flow: delete/cleanup resolves path → bounded list misses older shared row → row is removed and shared file can be unlinked.

Root cause: A bounded list API is used for a correctness-critical existence check.

Impact: Potential data loss at sufficient scale for shared/promoted/legacy files. Unique generated paths make it low-frequency, but the guard exists specifically for sharing.

Evidence: main.py:423; db.py:417-423; cleanup calls at main.py:341-343 and 1011-1017.

Recommended resolution: Query by canonical media key or use a media-reference table/count. Never use a truncated list for deletion safety.

Dependencies and risks: Handle legacy path normalization and promotion.

Verification plan: Create more than 100,000 rows with an older shared path, delete the newer owner, and assert the file remains.

Status: Already Addressed; see remediation follow-up.

### KIV-014 — Expired sessions are retained

Severity: Low
Confidence: Confirmed
Affected components: backend/app/db.py:180-201

Expected behavior: Expired sessions remain unusable and are periodically removed.

Current behavior: get_session returns None for expired rows but never deletes them. Rows are removed only on logout or account cascade.

End-to-end flow: repeated logins/abandoned sessions → stale rows accumulate → backups retain stale authentication metadata.

Root cause: Expiry is a read-time predicate without cleanup.

Impact: Bounded storage growth and unnecessary metadata retention; not an authentication bypass.

Evidence: db.py:188-197.

Recommended resolution: Delete expired rows on lookup or run bounded periodic cleanup, with an expiration index if volume grows.

Dependencies and risks: Test concurrent requests and active-session preservation.

Verification plan: Insert expired/active rows, run lookup/cleanup, and verify only expired rows disappear.

Status: Already Addressed; see remediation follow-up.

### KIV-015 — Queue capacity policy is inconsistent

Severity: Low
Confidence: Confirmed
Affected components: backend/app/main.py:535-543 and 749-777; backend/app/db.py:460-481

Expected behavior: API documentation and response behavior clearly state whether MOSS_MAX_QUEUE is global or per account.

Current behavior: route fast-fail uses queued_count(user_id), while authoritative add_job counts all active jobs globally.

End-to-end flow: account B passes its own count while account A occupies the global slot → insertion is rejected with a generic full-queue response.

Root cause: Fast-fail and commit checks encode different scopes.

Impact: Confusing 429 responses and fairness ambiguity. The transaction guard still prevents over-enqueueing.

Evidence: main.py:539/763; db.py:460-467.

Recommended resolution: Make the global single-worker limit explicit and align the fast-fail/error contract, or deliberately enforce named per-account and global limits.

Dependencies and risks: Per-account limits could overload the one worker.

Verification plan: Two-account queue-capacity-one tests covering submit, retry, cancel, and completion.

Status: Already Addressed; see remediation follow-up.

### KIV-016 — Variation branch is unreachable

Severity: Low
Confidence: Confirmed
Affected components: historical `backend/app/main.py` reuse route and request model; active `frontend/src/main.tsx` prompt-loading and randomized-batch controls; `docs/USER_GUIDE.md`

Expected behavior: Unsupported image-conditioned variation and supported randomized text-to-image batches use one clear API vocabulary.

Current behavior: The image-reuse/regeneration route and its request model are gone. Saved prompts and generation settings are loaded by the active UI, while randomized text-to-image batches remain supported as a separate workflow.

Root cause: Stale image-conditioned variation code remains after the product moved to text/layout-only generation.

Impact: The removed endpoint can no longer expose an unsupported image-conditioned path or the broken regeneration implementation; the active prompt-loading workflow remains available.

Evidence: `backend/app/main.py` no longer defines `ReuseImageRequest` or `/api/images/{image_id}/reuse`; the isolated route inventory and browser acceptance check passed.

Recommended resolution: Completed by removing the dead route and request model while retaining randomized text-to-image batches.

Dependencies and risks: Preserve regenerate and randomized batch behavior.

Verification plan: Run the backend suite, frontend build, browser acceptance flow, route inventory, and stale-symbol scan; all passed in the current snapshot.

Status: Already Addressed; see remediation follow-up.

### KIV-017 — Orchestration modules are too broad

Severity: Improvement
Confidence: Design improvement
Affected components: backend/app/main.py (1,277 lines); frontend/src/main.tsx (563 lines)

Expected behavior: A boundary change should have a small, discoverable impact surface and be independently testable.

Current behavior: main.py combines app setup, auth, account cleanup, conversations, media, jobs, gallery, prompt assistance, and diagnostics. main.tsx combines API state, browser workflow, layout, queue, worker, and gallery presentation.

Root cause: Single composition files grew as workflows were added. Section comments improve navigation but do not provide narrow test seams.

Impact: Higher regression risk and slower review. This is an improvement opportunity, not evidence that the current implementation is nonfunctional.

Recommended resolution: Incrementally extract auth, diagnostics, job API, media API, browser API/state, and presentational components while retaining the current composition roots and contracts.

Dependencies and risks: Avoid broad rewrites; preserve import-time settings and route schemas.

Verification plan: Extract one boundary at a time and run focused tests plus browser acceptance.

Status: Already Addressed incrementally; monitoring was extracted, while the frontend composition root remains a future optional extraction.

### KIV-018 — No CI/lint/migration quality gate

Severity: Improvement
Confidence: Design improvement
Affected components: Makefile:1-20; backend/; frontend/; repository automation

Expected behavior: Every change receives consistent automated syntax, unit, frontend, migration, browser-contract, and security-boundary checks.

Current behavior: make check runs shell syntax, Python compilation, pytest, and frontend production build. No browser test, migration fixture test, lint/type tool, or CI workflow was found.

Root cause: Verification is documented as developer responsibility rather than encoded as a repository gate.

Impact: Changes can pass make check while breaking browser behavior, migrations, async boundaries, or deployment scripts.

Recommended resolution: Add minimal CI around existing checks, then add isolated browser/migration/failure-path tests. Keep live GPU tests opt-in and labeled hardware/integration.

Dependencies and risks: Do not make normal CI depend on a GPU or live production data.

Verification plan: Run the new workflow in a clean environment, collect browser artifacts, verify dependency lock behavior, and retain a separate hardware job.

Status: Already Addressed; CI, browser acceptance, lock enforcement, and migration tests are now wired into the repository gate.

## Prioritized remediation roadmap

Completed remediation:

1. Boundary, transaction, reconciliation, restart identity, path, telemetry,
   secret, queue, session, migration, and variation findings are addressed in
   focused commits with regression coverage.
2. The browser acceptance path, lock-enforcing installation, migration tests,
   and CI quality gate are now repository-maintained.

Remaining verification:

3. Run the resolution matrix on the recommended 8GB VRAM configuration, including
   warm/cold state, batch count, steps, peak VRAM, OOM behavior, and throughput.
4. Exercise live crash/restart and native/Docker data migration in an isolated
   backup, then consider any further frontend component extraction separately.

## Verification summary

Checks executed during remediation and final review:

- Full `make check` passed: shell syntax, Python compilation, 123 backend
  tests, frontend TypeScript/Vite build, and Selenium browser acceptance.
- Browser acceptance passed with temporary data and a deterministic local
  worker: setup/login, conversation selection, negative prompt entry, prompt
  submission, queue/terminal transition, and rendered PNG.
- `npm ci` completed successfully with no vulnerabilities and no lockfile
  changes.
- Focused database, storage, job, inference, monitoring, API, and resolution
  tests passed during each focused commit.
- Compose configuration validation, shell syntax, Python compilation, and
  `git diff --check` passed. No live worker restart or production data mutation
  was performed during remediation.

Not claimed as passed:

- exact recommended 8GB VRAM fit/performance;
- crash/restart behavior during a live vLLM request;
- native/Docker media migration;
- host-level firewall policy.

## Review conclusion

The repository is workable for the current single-host local vLLM-Omni deployment and has the right product constraint: it does not fake unsupported image editing or silently use a public backend. The actionable correctness and verification findings are addressed with focused changes and a maintained browser/CI gate. The remaining material risk is hardware-specific: the recommended 8GB VRAM profile still needs direct peak-memory and throughput measurements, and live crash/migration procedures remain unverified.

This KIV document records the original findings, remediation commits, verification
evidence, and remaining risks. Runtime data remains local-only and was not
modified by the automated checks.

## Portfolio publication checklist

This project is intended to be published as a portfolio artifact, not presented
as an official product release, hosted service, or support commitment. The
checklist below is limited to making the source repository safe and clear to
publish publicly; it does not claim production, hardware, or legal readiness for
an official launch.

- [x] Add the MIT license for the original project code. It permits reuse,
      modification, and redistribution while requiring preservation of the
      copyright and license notice; it does not relicense dependencies, model
      weights, or upstream projects.
- [x] Publisher confirmed ownership of the project-based code. No artwork,
      generated images, user prompt history, or model weights are included in
      the clean snapshot; generic example prompts remain only where required
      by the application and tests.
- [x] Remove private hostnames, IP addresses, usernames, absolute filesystem
      paths, and deployment details from the current tree.
- [x] Rewrite or squash Git history so removed remote-worker configuration and
      private infrastructure identifiers are not exposed through the remote
      `main` branch. The branch now contains one sanitized snapshot commit.
- [x] Verify that databases, uploads, generated images, model caches, secrets,
      credentials, and local logs are absent from the sanitized published
      snapshot. Existing clones or forks may retain old unreachable objects.
- [x] Add third-party attribution/NOTICE material for MOSS-Image, vLLM-Omni,
      frontend dependencies, and any distributed container or build artifacts.
- [x] State that Moss-Image-Generator is an independent portfolio project and is
      not affiliated with or endorsed by OpenMOSS, vLLM, or other upstreams.
- [x] Identify model and dependency licenses separately; the project license
      must not imply that it relicenses upstream model weights.
- [x] Run secret scanning, dependency vulnerability scanning, and a generated
      dependency/license inventory immediately before changing visibility.
- [x] Describe the local-only scope, unsupported image-editing behavior,
      recommended 8GB VRAM baseline, and lack of production support in the
      README or portfolio page.
- [x] Not applicable to the current portfolio-only publication. If the project
      later becomes a hosted service, add separate privacy, retention/deletion,
      acceptable-use, and terms-of-service material.

Portfolio publication status: **Pending publisher review** for any final
third-party rights confirmation. The project license, sanitized remote
snapshot, attribution, ownership disclosure, and dependency remediation are
complete.

Dependency/security scan result: `npm audit --omit=dev` reported no findings.
The initial `pip-audit` result covered 100 advisory records across four
packages: Pillow 12.0.0, python-multipart 0.0.20, pytest 8.4.2, and Starlette
0.48.0. The clear compatible upgrades are now applied: FastAPI 0.162.0,
Starlette 1.3.1, python-multipart 0.0.32, pytest 9.1.1,
pytest-asyncio 1.4.1, and Pillow 13.0.0. The Python support floor is now
Python 3.11+, matching Pillow 13. GitHub reported 68 Dependabot alerts on the
default branch, but its alert API was not available without repository
authentication, so individual alert metadata could not be independently
reconciled here. Re-run the authenticated GitHub security view after its
dependency graph refreshes.
