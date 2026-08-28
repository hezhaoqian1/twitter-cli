# Account, Wallet, and Task Manager Implementation Plan

## Goal

Implement the local-first single-administrator management application described
in the approved design spec. The application will import social accounts and
wallets, store sensitive values in an encrypted vault, enforce immutable
account-wallet bindings, execute independent jobs with durable leases, expose
manual and batch controls, and deploy to Railway with PostgreSQL and Redis.

The first delivery must be usable locally with generated fixtures and must
preserve the existing `twitter-cli` package and commands.

## Architecture

```text
manager_ui/             React + TypeScript operations console
        |
manager_api/             FastAPI HTTP API and application services
        |
PostgreSQL               durable records, leases, task events
        |
Redis                    disposable queue transport
        |
manager worker           scheduler + one-job browser contexts
        |
existing twitter_cli     X request substrate behind an adapter
```

The API and worker share domain/application packages but have separate
process entry points. PostgreSQL is the source of truth. Redis is only a
delivery optimization; the scheduler reconstructs queued work from
`task_jobs`.

## Tech Stack

- Python 3.10+ backend, matching the current package.
- FastAPI, Pydantic, SQLAlchemy 2, Alembic, and psycopg.
- Redis client with explicit queue acknowledgement and recovery sweep.
- Playwright for isolated browser contexts.
- `cryptography` for AES-256-GCM and key wrapping primitives.
- Argon2id through `argon2-cffi`.
- React + TypeScript + Vite for the management UI.
- PostgreSQL for local and Railway persistence.
- Docker Compose for local PostgreSQL/Redis and Railway-compatible services.
- Pytest for backend/domain tests; Playwright for UI smoke tests.

## Baseline / Authority Refs

- `docs/aegis/BASELINE-GOVERNANCE.md`
- `docs/aegis/baseline/2026-08-28-initial-baseline.md`
- `docs/aegis/specs/2026-08-28-account-wallet-task-manager-design.md`
- `README.md`
- `docs/x-cookie-auth-flow.md`
- `docs/kredo-wallet-login-flow.md`
- Existing `twitter_cli/`, `scripts/`, and `tests/` layout

## Compatibility Boundary

1. Existing `twitter` commands, imports, auth priority, and tests remain
   unchanged.
2. The manager calls `twitter_cli` only through an adapter. Manager services
   never import database code into `twitter_cli`.
3. Existing scripts remain diagnostic tools. Production task state is stored
   in manager tables, not script output files.
4. No secret values are added to source, fixtures, Markdown, screenshots,
   command arguments, or logs.
5. Railway values are supplied through `DATABASE_URL` and `REDIS_URL`
   variables; their concrete values are not committed.

## TDD Route

- Mode: `off`
- Decision: `light`
- Strict authority: `not applicable`
- Test posture: domain regression tests, integration tests with disposable
  services, and focused UI smoke tests after each slice
- Reason: the user requested a complete system design and implementation path,
  not strict test-first development. The security and persistence boundaries
  still require proportional tests before each slice is accepted.
- Verification: run focused pytest suites after each batch, then the complete
  non-smoke suite and deployment smoke checks.

## Scope Check

### Facts

- The current repository is a Python CLI with X auth and browser probe code.
- The existing Kredo flow has delayed external state propagation.
- The user selected local-first deployment with Railway as the later server
  target.
- The user selected one administrator, immutable bindings, independent jobs,
  a default batch size of 10, and recoverable encrypted storage.

### Assumptions

- The first manager UI can be a sibling application in this repository.
- PostgreSQL and Redis are available through Docker locally.
- The integration adapter can reuse existing X behavior without changing its
  public CLI contract.
- Browser evidence is retained only when explicitly enabled.

### Unknowns to isolate behind adapters

- Provider-specific binding and reward endpoint details.
- Exact external status propagation time.
- Browser memory cost at concurrency above two.
- Whether Railway needs a separate browser service after measured load.

## Requirement Ready Check

- Requirement source refs: user decisions in this thread; design spec.
- Goals and scope refs: design spec sections 1-4.
- User/scenario refs: design spec sections 4-5.
- Requirement item refs: import, wallet derivation, immutable binding, manual
  actions, batch size 10, independent jobs, encryption, recovery.
- Acceptance/verification refs: design spec section 17.
- Open blocker questions: none for the first implementation plan; provider
  details remain adapter inputs.
- Decision: `ready`

## Baseline Usage

- Required baseline refs: governance, initial baseline, design spec, existing
  CLI/auth/flow documents.
- Delivered context refs: prior confirmed product decisions and Railway choice.
- Acknowledged before plan refs: all required baseline refs above.
- Cited in plan refs: compatibility, adapter boundary, async state, and
  existing owner sections.
- Missing refs: no existing UI or database implementation baseline.
- Decision: `continue`

## Change Necessity

- User-visible need: import and operate hundreds of account-wallet pairs with
  isolated manual and batch tasks.
- No-change option: continue using standalone CLI scripts and browser probes.
- Why code change is necessary: scripts have no durable bindings, database
  leases, vault, API, UI, or crash recovery.
- Minimum change boundary: add a sibling `manager_api/`, `manager_ui/`, and
  deployment configuration while keeping `twitter_cli/` stable.
- Decision: `code-change`

## Existence Check

### New manager application owner

- Existing owner / reuse candidate: `twitter_cli` and `scripts/`.
- Why insufficient: these surfaces own CLI requests and diagnostics, not
  persistence, scheduling, browser isolation, or UI commands.
- Creation proof: the user requires durable multi-account management and
  independent concurrent jobs.
- Entropy/retirement impact: manager owns orchestration; existing CLI remains
  the X adapter substrate. No duplicate database or scheduler is added.
- Decision: `add-with-proof`

### Redis transport

- Existing owner / reuse candidate: none.
- Why insufficient: PostgreSQL durable rows need a low-latency delivery path
  for independent workers.
- Creation proof: measured or integration-tested queue dispatch and recovery.
- Entropy/retirement impact: Redis remains disposable transport; a later
  measured single-process mode may remove it through a separate design update.
- Decision: `add-with-proof`

### Separate browser service

- Existing owner / reuse candidate: worker process with per-job contexts.
- Why insufficient: none at the first deployment scale.
- Creation proof: only add after browser memory or CPU measurements exceed the
  worker service budget.
- Entropy/retirement impact: defer to avoid a second browser owner.
- Decision: `defer`

## Architecture Integrity Lens

- Invariant: PostgreSQL owns durable task, binding, and lease state.
- Canonical owner/contract: repository/application services own mutations;
  adapters return normalized external observations.
- Responsibility overlap avoided: UI never decrypts or mutates records directly;
  workers never decide batch selection.
- Higher-level simplification: one scheduler owns lease acquisition for both
  manual and batch jobs; no caller-side locking.
- Retirement/falsifier: do not add compatibility logic to `twitter_cli`; if an
  adapter requires new client behavior, add a narrow adapter-facing method and
  preserve the CLI contract.
- Verdict: proceed with sibling manager application and stable adapter boundary.

## Plan-Time Complexity Check

- Artifact class: source, test, migration, and decision-plan artifacts.
- Target files: new `manager_api/`, `manager_ui/`, `tests/manager/`,
  `alembic/`, Docker/Railway configuration, and this plan.
- Current pressure: the existing Python package contains a broad client module,
  but the manager does not need to grow it.
- Projected pressure: multiple bounded new owners; no existing file receives
  persistence, scheduler, or UI responsibilities.
- Budget result: `within-budget`.
- Planned governance: keep vault, repository, scheduler, worker, adapter, and
  API routers in separate modules; review each slice before the next.

## File Map

### Backend

- `manager_api/__init__.py`: package marker.
- `manager_api/config.py`: typed environment configuration.
- `manager_api/main.py`: FastAPI app factory and health endpoints.
- `manager_api/db/base.py`: SQLAlchemy metadata and model registration.
- `manager_api/db/session.py`: engine and transaction/session lifecycle.
- `manager_api/models/`: SQLAlchemy models for core tables.
- `manager_api/schemas/`: Pydantic request/response schemas with redaction.
- `manager_api/repositories/`: transaction-scoped persistence methods.
- `manager_api/services/vault.py`: key derivation, envelope encryption,
  wrapping, unlock, and redaction.
- `manager_api/services/imports.py`: TSV preview/commit and duplicate policy.
- `manager_api/services/wallets.py`: private-key/mnemonic import and derivation.
- `manager_api/services/bindings.py`: immutable pairing transaction.
- `manager_api/services/tasks.py`: job creation and state transitions.
- `manager_api/scheduler.py`: eligibility scan, fair dispatch, and leases.
- `manager_api/worker.py`: one-job execution entry point and recovery.
- `manager_api/adapters/protocol.py`: normalized integration contract.
- `manager_api/adapters/x_adapter.py`: existing `twitter_cli` bridge.
- `manager_api/adapters/kredo_adapter.py`: Kredo browser/API workflow.
- `manager_api/api/routers/`: account, wallet, binding, task, vault, and
  health routes.

### Data and deployment

- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/0001_manager_core.py`
- `docker-compose.manager.yml`
- `Dockerfile.manager`
- `railway.json`
- `.env.manager.example`
- `scripts/manager_migrate.py`
- `scripts/manager_backup.py`

### Frontend

- `manager_ui/package.json`
- `manager_ui/src/main.tsx`
- `manager_ui/src/app.tsx`
- `manager_ui/src/api/client.ts`
- `manager_ui/src/features/overview/`
- `manager_ui/src/features/accounts/`
- `manager_ui/src/features/wallets/`
- `manager_ui/src/features/bindings/`
- `manager_ui/src/features/tasks/`
- `manager_ui/src/features/vault/`
- `manager_ui/src/styles/`

### Tests

- `tests/manager/test_vault.py`
- `tests/manager/test_imports.py`
- `tests/manager/test_wallet_derivation.py`
- `tests/manager/test_bindings.py`
- `tests/manager/test_task_state.py`
- `tests/manager/test_leases.py`
- `tests/manager/test_worker_recovery.py`
- `tests/manager/test_adapters.py`
- `tests/manager/test_api_redaction.py`
- `tests/manager/test_backup_restore.py`
- `manager_ui/src/**/*.test.tsx`

## Execution Plan

Each task is intended to be one small, independently reviewable change. Each
task ends with the listed verification command and a commit. Secrets are
provided through the environment only.

### Batch 1: Backend substrate and local services

#### 1. Add manager dependencies and configuration

Files: `pyproject.toml`, `manager_api/`, `.env.manager.example`,
`docker-compose.manager.yml`, `Dockerfile.manager`.

Why: establish a separate runtime without changing the CLI entry point.

Change necessity: the existing package has no web, database, queue, or worker
runtime; the minimum new boundary is the manager package and its service
configuration.

Steps:

1. Add backend dependencies and a manager development extra to `pyproject.toml`
   without changing the existing `twitter` script.
2. Create typed settings with required `DATABASE_URL`, `REDIS_URL`, session
   secret, worker concurrency, browser concurrency, and poll timings.
3. Add `.env.manager.example` using placeholders only:
   `DATABASE_URL=postgresql://...`, `REDIS_URL=redis://...`.
4. Add Docker Compose services for PostgreSQL and Redis with named volumes,
   health checks, and non-secret defaults.
5. Add a backend image that starts only the API by default; worker startup is
   a separate command.
6. Add `/health/live` and `/health/ready`; readiness checks PostgreSQL and
   Redis connectivity without decrypting secrets.

Verification:

```bash
docker compose -f docker-compose.manager.yml config
uv run pytest tests -m 'not smoke' -q
```

Commit: `feat(manager): add backend runtime substrate`

#### 2. Add database metadata, migrations, and transaction helpers

Files: `manager_api/db/base.py`, `manager_api/db/session.py`,
`manager_api/models/*.py`, `alembic.ini`, `alembic/env.py`,
`alembic/versions/0001_manager_core.py`, `tests/manager/conftest.py`.

Why: make PostgreSQL the durable owner of accounts, wallets, bindings, jobs,
events, leases, imports, audit records, and vault metadata.

Change necessity: no existing persistence boundary can represent the required
invariants or crash recovery.

Steps:

1. Define UUIDv7 IDs, UTC timestamps, lifecycle enums, and model relationships.
2. Define encrypted envelope columns as bytes plus version and metadata fields.
3. Add unique normalized handle/address indexes and partial unique binding
   indexes for one active binding per account and wallet.
4. Add append-only task event and audit log models.
5. Add `resource_leases` with a unique `lease_key`, owner token, expiry, and
   indexes for expired-lease scans.
6. Generate one migration and ensure it contains all indexes and constraints.
7. Add a transaction fixture that creates isolated PostgreSQL schemas or
   disposable databases for integration tests.

Verification:

```bash
docker compose -f docker-compose.manager.yml up -d postgres redis
uv run alembic upgrade head
uv run pytest tests/manager/test_bindings.py tests/manager/test_task_state.py -q
```

Commit: `feat(manager): add durable manager schema`

### Batch 2: Vault and data import

#### 3. Implement the encrypted vault

Files: `manager_api/services/vault.py`, `manager_api/models/vault.py`,
`manager_api/schemas/vault.py`, `tests/manager/test_vault.py`.

Why: protect account credentials, full cookies, TOTP seeds, mailbox
credentials, private keys, mnemonics, and provider tokens at rest.

Change necessity: plaintext persistence would violate the confirmed recovery
and secret-handling requirements.

Steps:

1. Generate a random 256-bit vault data key on initialization.
2. Derive password and recovery wrapping keys with Argon2id using stored salts
   and explicit parameters.
3. Wrap the vault data key separately with password and recovery keys using
   AES-GCM.
4. Encrypt each field with a fresh nonce and authenticated metadata containing
   table, record, field, and secret version.
5. Provide `initialize`, `unlock_with_password`, `unlock_with_recovery_key`,
   `encrypt_field`, `decrypt_field`, `lock`, and redaction helpers.
6. Keep the unwrapped vault key only in an in-memory runtime cache with a
   configurable TTL; never serialize it.
7. Raise the same external error for a bad password and missing vault to avoid
   revealing vault existence through the API.

Verification:

```bash
uv run pytest tests/manager/test_vault.py -q
uv run mypy manager_api/services/vault.py
```

Commit: `feat(manager): add recoverable encrypted vault`

#### 4. Implement account TSV preview and commit

Files: `manager_api/services/imports.py`, account models/repositories/schemas,
`manager_api/api/routers/accounts.py`, `tests/manager/test_imports.py`,
`tests/manager/test_api_redaction.py`.

Why: import hundreds of account rows while showing duplicates and diagnostics
without exposing secret fields.

Change necessity: the current batch checker is a one-shot CLI diagnostic and
does not persist rows or import outcomes.

Steps:

1. Parse the seven-column TSV contract with strict line numbers and UTF-8
   validation.
2. Normalize handles and classify rows as valid, malformed, duplicate within
   file, existing account, or conflicting active session.
3. Preview returns only masked handle/email and diagnostic codes.
4. Commit creates an import batch and one import row per input line.
5. Encrypt password, TOTP, mailbox password, token, and full Cookie values
   before database insertion.
6. Apply skip-duplicate policy by default and record every decision.
7. Add paginated account list and account health endpoints that never select
   decrypted fields.

Verification:

```bash
uv run pytest tests/manager/test_imports.py tests/manager/test_api_redaction.py -q
```

Commit: `feat(manager): add account import workflow`

#### 5. Implement wallet import and deterministic derivation

Files: `manager_api/services/wallets.py`, wallet models/repositories/schemas,
`manager_api/api/routers/wallets.py`, `tests/manager/test_wallet_derivation.py`.

Why: manage imported keys and mnemonic-derived addresses with MetaMask
compatible Ethereum paths.

Change necessity: no current owner persists wallet sources or derived address
metadata.

Steps:

1. Accept a private key or mnemonic only through a vault-unlock command body;
   do not include secrets in URLs or query strings.
2. Normalize and validate Ethereum addresses and private-key formats.
3. Derive using `m/44'/60'/0'/0/index`, with explicit start index and count.
4. Return public addresses and derivation metadata for preview.
5. Encrypt source material and private keys before commit.
6. Prevent duplicate normalized addresses and retain redacted import results.
7. Require a fresh password check for mnemonic/private-key export and append
   an audit event without the exported value.

Verification:

```bash
uv run pytest tests/manager/test_wallet_derivation.py tests/manager/test_api_redaction.py -q
```

Commit: `feat(manager): add wallet sources and derivation`

### Batch 3: Binding and task engine

#### 6. Implement immutable bindings

Files: `manager_api/services/bindings.py`, binding models/repositories/schemas,
`manager_api/api/routers/bindings.py`, `tests/manager/test_bindings.py`.

Why: prevent reassignment and concurrent selection conflicts.

Change necessity: the database schema alone cannot expose a transaction-safe
one-time pairing command or explain conflicts to the UI.

Steps:

1. Validate both resources are active and unbound inside one transaction.
2. Create a pending binding intent only through the service owner.
3. On confirmed external binding, finalize the record with `bound_at` and
   external reference.
4. Reject updates to either resource ID after `bound`.
5. Allow archive as a lifecycle action while preserving the historical record.
6. Return conflict codes that distinguish archived, already bound, and
   currently leased resources.

Verification:

```bash
uv run pytest tests/manager/test_bindings.py -q
```

Commit: `feat(manager): enforce immutable account wallet bindings`

#### 7. Implement task state transitions and idempotency

Files: `manager_api/services/tasks.py`, task models/repositories/schemas,
`manager_api/api/routers/tasks.py`, `tests/manager/test_task_state.py`.

Why: give manual and batch commands one durable state machine.

Change necessity: no current code owns queued/running/waiting/retry state or
redacted event history.

Steps:

1. Define typed states and permitted transitions from the design spec.
2. Build the idempotency key as
   `<kind>:<binding-or-resource-pair>:<external-target>`.
3. Return the existing active job for duplicate keys.
4. Keep retries on the same job and increment `attempt`.
5. Write one append-only event in the same transaction as every transition.
6. Expose pause, cancel, retry, and poll commands with state-specific rules.
7. Ensure no task response includes secret payloads or raw provider URLs.

Verification:

```bash
uv run pytest tests/manager/test_task_state.py tests/manager/test_api_redaction.py -q
```

Commit: `feat(manager): add durable task state machine`

#### 8. Implement leases, fair scheduling, and worker recovery

Files: `manager_api/scheduler.py`, `manager_api/worker.py`,
`manager_api/repositories/leases.py`, `tests/manager/test_leases.py`,
`tests/manager/test_worker_recovery.py`.

Why: run distinct account-wallet jobs concurrently while preventing same
resource interference.

Change necessity: application-level checks alone race under concurrent workers;
the lease transaction is the minimum shared-resource owner.

Steps:

1. Acquire all account and wallet lease keys in one PostgreSQL transaction.
2. Use owner tokens and expiry timestamps; release only by matching owner.
3. Dispatch oldest eligible jobs in round-robin order across batches.
4. Enforce local defaults of global worker concurrency `3` and browser
   concurrency `2`; keep per-account and per-wallet concurrency at `1`.
5. Acknowledge Redis messages only after the durable state transition.
6. Add a recovery sweep that requeues jobs with expired leases and appends a
   recovery event.
7. Make shutdown release owned leases and leave unfinished jobs recoverable.

Verification:

```bash
uv run pytest tests/manager/test_leases.py tests/manager/test_worker_recovery.py -q
uv run pytest tests/manager/test_task_state.py -q
```

Commit: `feat(manager): add isolated task scheduler and worker recovery`

### Batch 4: External adapters and UI

#### 9. Add normalized integration adapters

Files: `manager_api/adapters/protocol.py`,
`manager_api/adapters/x_adapter.py`, `manager_api/adapters/kredo_adapter.py`,
`tests/manager/test_adapters.py`.

Why: isolate provider protocol details from the task engine.

Change necessity: direct provider calls inside workers would make retries,
state normalization, redaction, and future provider changes cross-cutting.

Steps:

1. Define typed normalized results for account health, operation references,
   external status, and redacted evidence.
2. Wrap the current `twitter_cli` auth and action calls behind the X adapter.
3. Port the existing Kredo headed-browser flow into a per-job adapter
   context without sharing profiles or secrets.
4. Map delayed external responses to `waiting_external_validation`.
5. Check external state before replaying repost or claim operations.
6. Make all adapter errors typed and safe for task events.
7. Add adapter fakes for deterministic tests; no live credentials in tests.

Verification:

```bash
uv run pytest tests/manager/test_adapters.py -q
uv run ruff check manager_api
```

Commit: `feat(manager): add X and Kredo adapter contracts`

#### 10. Build the operations UI

Files: `manager_ui/` listed in the file map, plus API route tests.

Why: provide the requested high-density management system with manual row
actions and batch controls.

Change necessity: the CLI cannot provide table filtering, side panels, import
preview, task status polling, or batch control ergonomically.

Steps:

1. Create the Vite React app with typed API client and local session handling.
2. Add navigation for Overview, Accounts, Wallets, Bindings, Tasks, Task
   Detail, and Vault & Backup.
3. Implement server-side tables with pagination, filters, sorting, selection,
   status chips, and narrow action columns.
4. Add import preview drawers for account TSV and wallet derivation.
5. Add manual `Bind`, `Repost`, and `Claim` actions with precise disabled
   reasons for locks, archived rows, and pending external validation.
6. Add batch preview, default size 10, pause/cancel/retry controls, and
   polling of task progress.
7. Add vault unlock, backup, restore verification, and fresh-password export
   dialogs without rendering secrets in normal list views.
8. Add responsive layouts for laptop and server-admin widths; keep the table
   as the primary work surface.

Verification:

```bash
cd manager_ui && npm ci && npm run build
cd ..
uv run pytest tests/manager/test_api_redaction.py -q
```

Commit: `feat(manager): add operations management UI`

### Batch 5: Backups, Railway, and end-to-end verification

#### 11. Add encrypted backup and restore

Files: `scripts/manager_backup.py`, `manager_api/services/backup.py`,
`manager_api/api/routers/vault.py`, `tests/manager/test_backup_restore.py`.

Why: preserve recoverability and support local-to-Railway migration.

Change necessity: database backups alone do not prove that the wrapped vault
key and encrypted application data can be decrypted after migration.

Steps:

1. Produce a PostgreSQL logical backup into a temporary file with restrictive
   permissions.
2. Package database dump, vault metadata, version, and checksum manifest.
3. Encrypt the package using a key derived from the recovery key.
4. Restore into an empty database, verify checksums and schema, and keep task
   dispatch disabled until integrity passes.
5. Verify that generated test secrets decrypt after restore.
6. Delete temporary plaintext dump files after successful packaging or failure.

Verification:

```bash
uv run pytest tests/manager/test_backup_restore.py -q
uv run python scripts/manager_backup.py --help
```

Commit: `feat(manager): add encrypted backup and restore`

#### 12. Add Railway deployment configuration

Files: `railway.json`, `Dockerfile.manager`, `scripts/manager_migrate.py`,
`.env.manager.example`, deployment documentation.

Why: make the local stack portable to Railway PostgreSQL and Redis.

Change necessity: server migration requires reproducible service startup,
explicit migrations, health checks, and worker/web separation.

Steps:

1. Configure Railway web and worker services to use the same application image
   and release version.
2. Run Alembic migrations as a release command before worker startup.
3. Bind the API to Railway's injected port and use `DATABASE_URL` and
   `REDIS_URL` variables.
4. Keep actual Railway connection values in the Railway Variables UI only.
5. Set initial server defaults to one worker and browser concurrency `1`.
6. Run migration restore verification before enabling task dispatch.
7. Add health checks that expose queue depth, database readiness, and worker
   heartbeat without secret values.

Verification:

```bash
docker compose -f docker-compose.manager.yml config
uv run python scripts/manager_migrate.py --check
```

Commit: `chore(manager): add Railway deployment configuration`

#### 13. Run complete regression and load checks

Files: `tests/manager/`, `manager_ui/`, `docs/aegis/`.

Why: prove the three critical properties: no shared-resource interference,
recoverability, and acceptable performance.

Steps:

1. Run the full non-smoke backend suite and frontend build.
2. Run a generated 500-row import preview and record p95 duration.
3. Run ten independent jobs and assert at least two distinct jobs can overlap.
4. Run two jobs sharing an account or wallet and assert they never overlap.
5. Kill a worker during a leased job and assert recovery under two minutes.
6. Delay a fake external status and assert the job remains waiting rather than
   failing.
7. Verify no secret value appears in structured logs, task events, API list
   responses, or screenshots.
8. Run a backup/restore cycle and verify test-secret decryption.

Verification:

```bash
uv run pytest tests -m 'not smoke' -q
cd manager_ui && npm run build
cd ..
```

Commit: `test(manager): verify isolation recovery and performance`

## Execution Readiness View

- Intent Lock: local-first, single-admin account/wallet/task manager.
- Scope Fence: import, vault, immutable bindings, independent tasks, UI, and
  Railway deployment; no multi-user or high-availability fleet.
- Baseline Lock: existing `twitter_cli` remains the X client owner.
- Approved Behavior: manual Bind/Repost/Claim, batch default 10, delayed
  external states remain pollable, binding records are immutable.
- Owner/Contract Constraints: vault owns crypto, scheduler owns leases,
  adapters own provider calls, repository owns durable state.
- Compatibility Boundary: existing CLI and tests remain unchanged; no secrets
  in Git or telemetry.
- Retirement Boundary: no direct production script persistence; separate
  browser service remains deferred until measured need.
- Task Batches: backend substrate; vault/import; bindings/tasks; adapters/UI;
  backup/Railway/verification.
- Test Obligations: vault round trip, database constraints, idempotency,
  lease exclusivity, worker recovery, redaction, backup restore, UI build.
- Review Gates: review after each batch and before any live external adapter
  run.
- Drift/Rewind Rules: if a task needs a new owner or changes the CLI contract,
  stop and update the design/plan before coding; if external state is delayed,
  preserve waiting state and poll.
- Evidence Required Before Completion: passing tests, migration check, build,
  generated-load measurements, redaction scan, and restore verification.
- Advisory Boundary: this view prepares execution and is not completion
  authority.

## Risks

1. Browser memory may limit concurrency on Railway. Start at one browser worker
   and raise only after measurements.
2. External task propagation may exceed the initial poll deadline. Keep poll
   continuation durable and visible.
3. A malformed import can create ambiguous identity records. Require preview,
   normalized duplicate checks, and explicit commit.
4. Recovery material loss makes encrypted data unrecoverable. Show the recovery
   key once at setup and verify a restore before enabling production dispatch.
5. Provider changes can break adapters. Keep live protocol code behind typed
   adapters and preserve diagnostic evidence.

## Rollback and Retirement

- Each batch has a separate commit and can be reverted before the next batch is
  merged.
- Existing CLI changes are outside this plan and remain untouched.
- Database migrations are forward-only in normal operation; destructive
  changes require a new reviewed migration and backup.
- Redis can be flushed without losing durable jobs; PostgreSQL is never
  reconstructed from Redis.
- Direct script-based production execution is retired when the worker adapter
  reaches parity and end-to-end tests pass.
- A dedicated browser service is added only after recorded resource metrics
  falsify the single-worker boundary.

## ADR Signals

Create ADRs during implementation for:

1. vault envelope and recovery package format;
2. PostgreSQL lease transaction and worker recovery;
3. adapter contract and browser-context lifecycle;
4. Railway release/migration ordering.

## Plan Self-Review

- Spec coverage: every design acceptance item maps to one or more batches.
- Placeholder scan: no `TBD`, `TODO`, or vague "write tests" tasks.
- Type consistency: task IDs, lease keys, idempotency keys, and adapter result
  names are consistent across batches.
- Compatibility: current CLI remains a stable substrate.
- Change necessity: every source-edit batch states its minimum boundary.
- Existence: new manager, queue, and adapter surfaces have proof and
  retirement boundaries.
- Complexity: no new responsibilities are added to the existing broad client.
- Verification: every batch has exact commands and the final batch has
  generated isolation/recovery checks.
- Dual-track: repair/compatibility lives in adapters; direct scripts retire
  after parity evidence.
- Result: ready for execution choice.
