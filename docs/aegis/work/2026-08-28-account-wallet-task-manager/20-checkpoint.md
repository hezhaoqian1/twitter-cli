# Execution Checkpoint

Date: `2026-08-28`

## Current Todo

1. Establish baseline test evidence. (completed)
2. Implement Batch 1, Task 1: manager runtime substrate. (completed)
3. Verify configuration, manager tests, and existing CLI regression tests. (completed)
4. Implement Batch 1, Task 2: database metadata, migrations, and transaction
   helpers. (completed)
5. Verify schema, migration, static checks, and regression tests. (completed)
6. Implement Batch 2, Task 3: encrypted Vault service and schemas. (completed)
7. Implement Batch 2, Task 4: account TSV preview and commit. (completed)
8. Implement Batch 2, Task 5: wallet import and deterministic derivation. (completed)
9. Implement Batch 2, Task 6: immutable account-wallet binding service and API. (completed)
10. Implement Batch 2, Task 7: durable task state transitions and idempotency. (completed)
11. Implement Batch 3, Task 8: leases, fair scheduling, and worker recovery. (completed)
12. Implement Batch 4, Task 9: normalized integration adapters. (completed)
13. Implement Batch 4, Task 10: operations management UI. (completed)
14. Implement Batch 4, Task 11: read-only Kredo Points and HSK balance sync.
    (completed)
15. Implement Batch 5, Task 12: encrypted backup and restore. (completed)
16. Implement Batch 5, Task 13: Railway deployment and end-to-end verification.
    (in progress)

## Active Slice

Batch 5, Task 13: Railway deployment and end-to-end verification.

## Completed

- Approved design and implementation plan are committed.
- Railway deployment configuration is recorded without concrete credentials.
- Manager schema, model relationships, durable task records, leases, audit
  records, vault metadata, and the initial Alembic migration are implemented.
- Python 3.10-compatible string enums, timezone-aware archive timestamps, and
  reserved `metadata` column mappings are verified.
- Vault package, Argon2id/AES-GCM implementation, and focused tests are
  complete and committed as the preceding slice.
- Account import service accepts the confirmed seven-column TSV contract:
  `handle`, `password`, `totp`, `email`, `email_password`, `token`, `cookie`.
- Preview and commit routers return masked identities and diagnostics only;
  accepted secret fields are encrypted before persistence.
- `VaultRuntime` now owns the process-local unwrapped key, with TTL expiry and
  immediate lock clearing.
- FastAPI exposes redacted Vault status, initialize, password/recovery unlock,
  and lock routes; initialization returns the recovery key only in that
  one-time response.
- Account commit now receives the shared runtime-backed `VaultService`, so an
  unlock in one request is usable by a later commit request in the same API
  process.
- Wallet source preview validates private keys and BIP-39 mnemonics, derives
  MetaMask-compatible `m/44'/60'/0'/0/{index}` addresses, classifies duplicate
  addresses, and encrypts accepted source/private-key material.
- Wallet HTTP routes are registered in the application and use the shared
  runtime-backed Vault dependency for commit and derivation.
- Wallet list responses expose only public address and binding/state metadata.
- Binding service creates pending intents only for active, unbound resources,
  finalizes them with an external reference, preserves immutable history, and
  exposes stable conflict codes for archived, bound, and leased resources.
- Binding API exposes create, confirm, archive, detail, and history list routes.
- Task state transitions, deterministic idempotency keys, retry/poll commands,
  redacted append-only events, and worker-facing state rules are implemented.
- Scheduler and lease repository acquire all resource leases atomically, apply
  fair batch dispatch, release only matching owners, and recover expired jobs.
- Redis reliable-list delivery and worker shutdown/recovery paths are
  implemented and covered with deterministic test doubles.
- X and Kredo integration adapters expose typed, normalized operations and
  observations, preserve delayed provider states for polling, preflight
  idempotent repost/claim actions, isolate one workflow context per call, and
  redact provider evidence and errors.
- The React/Vite operations UI provides the requested overview, account,
  wallet, binding, task, and Vault surfaces with manual row actions, batch
  controls, import flows, redacted secret handling, and responsive laptop,
  tablet, and mobile layouts.
- Kredo account summary fields are normalized into a replaceable
  binding-scoped snapshot: Points, available HSK, and open-position HSK value.
- `balance_sync` is a read-only durable task with isolated account/wallet
  leases; its failure state preserves the last successful numeric snapshot.
- Binding and balance APIs expose the latest snapshot and allow selected or
  all-bound records to be queued for a fresh sync.
- The binding UI shows Points/HSK columns and supports single-row or selected
  batch synchronization.
- Encrypted backup packages contain the manager schema and vault metadata,
  use an independent recovery-key envelope, verify checksums before restore,
  and reject malformed or non-empty restore targets.
- Vault HTTP routes expose backup download, backup verification, and restore;
  the UI surfaces the result summary without exposing secret material.
- Batch rows expose pause, resume, and cancel controls; the scheduler skips
  non-active batches and workers cancel pending external polling cleanly.
- Batch dispatch limits now cap active leases per batch, independent of global
  worker capacity.
- Failed or cancelled predecessors now move queued dependents to an explicit
  `blocked` state; retrying the predecessor can requeue the blocked chain.
- Cancellation is cooperative for running workers, immediate for leased work,
  and preserves a redacted cancellation-request event.

## Evidence

- Plan: `docs/aegis/plans/2026-08-28-account-wallet-task-manager-implementation.md`
- Design: `docs/aegis/specs/2026-08-28-account-wallet-task-manager-design.md`

## Blockers

None. Direct TCP access is unavailable from this workstation, but the local
Clash HTTP proxy path is verified below.

## Resume State

The user selected inline execution in the current workspace. Account import,
Vault HTTP lifecycle wiring, wallet import/derivation, immutable binding,
durable task state, leases, fair scheduling, worker recovery, normalized
adapters, balance snapshots, balance sync tasks, the operations UI, encrypted
backup/restore, batch lifecycle controls, dependency blocking, and cooperative
cancellation are complete. Existing uncommitted CLI, documentation, script,
and test changes are user-owned and must remain untouched. The remaining work
is final diff review and shipping decisions.

## Drift Check

- Intent: aligned.
- Scope: account TSV preview/commit, Vault lifecycle wiring, wallet
  import/derivation, immutable bindings, durable task state, leases, fair
  scheduling, worker recovery, normalized adapters, balance snapshots, balance
  sync tasks, the operations UI, encrypted backup/restore, and batch lifecycle
  controls are complete; hosted deployment remains outside this local
  checkpoint.
- Compatibility: existing CLI and manager persistence models are explicit
  non-edits.
- Retirement: unchanged.
- New owners: `manager_api.services.imports` owns TSV parsing and import
  decisions; `manager_api.services.vault` remains the sole crypto owner;
  `VaultRuntime` owns process-local key lifetime; wallet source and derivation
  logic is owned by `manager_api.services.wallets`; pairing rules are owned by
  `manager_api.services.bindings`; balance snapshot writes are owned by
  `manager_api.services.balances`; task state and event transitions are owned
  by `manager_api.services.tasks`; lease acquisition and release are owned by
  `manager_api.repositories.leases`; dispatch and recovery are owned by
  `manager_api.scheduler`; one-job execution and queue acknowledgement are
  owned by `manager_api.worker`; provider calls are isolated behind
  `manager_api.adapters`.
- Fallbacks/branches: no new provider or persistence fallback added.
- Test obligations: account import parsing, duplicate classification,
  redacted preview, encrypted commit, API redaction, shared Vault runtime,
  wallet derivation, wallet duplicate classification, and wallet API
  redaction, binding immutability, binding conflicts, task state/event,
  lease exclusivity, fair scheduling, queue delivery, worker recovery, balance
  snapshot, normalized adapters, encrypted backup/restore, batch lifecycle,
  dependency blocking, and cooperative cancellation tests, plus the frontend
  production build, are complete for the closed slices. Live PostgreSQL/Redis
  startup and Alembic migration are verified through the local Clash tunnel;
  hosted deployment and end-to-end provider actions remain open.
- Review gate: Vault encryption remains the only crypto owner. HTTP commit,
  backup, and restore use the shared Vault runtime and never return secret
  material.
- Decision: review the full diff, then ship when the user selects the landing
  path.

## Local Railway Runtime Evidence

- Added a private, Git-excluded `.env.manager` with the supplied Railway
  PostgreSQL and Redis connection values so the manager application, Alembic,
  and runtime scripts share one local configuration source.
- Added the requested private runtime record at
  `docs/local-railway-runtime.md`; it is excluded through
  `.git/info/exclude` and is not part of the repository change set.
- `.env.manager` is mode `0600`.
- DNS resolution succeeded for both Railway proxy hosts on 2026-08-28.
- Direct TCP connection attempts to PostgreSQL port `34945` and Redis port
  `35427` timed out on 2026-08-28. No direct migration or application write
  reached Railway.
- The normal test suite remains dependency-isolated by design. Real Railway
  dependency checks use the private `.env.manager` smoke commands.
- Added `scripts/manager_clash_tunnel.py` to expose the configured Railway
  PostgreSQL and Redis endpoints through local Clash HTTP `CONNECT` tunnels.
- Through the tunnel, PostgreSQL and Redis health checks passed and
  `manager_migrate.py` completed at Alembic `head` on 2026-08-28.
- A fresh tunnel probe on 2026-08-28 established PostgreSQL connectivity,
  returned Redis `PING=True`, and completed without writing application data.
- PostgreSQL-specific migration branches were added to revisions `0002`,
  `0003`, and `0004`; SQLite keeps its existing table-rebuild path.
- A fresh tunnel probe returned PostgreSQL `SELECT 1`, Redis `PING=True`, and
  application readiness with both checks `ok` on 2026-08-28.

## Runtime Observability Checkpoint

- `GET /api/runtime/metrics` now owns the read-only aggregation of queue
  depth, task-state counts, resource leases, completion time, and Worker
  heartbeat summary.
- The Overview page consumes that endpoint and keeps runtime health distinct
  from account and wallet inventory totals.
- Verification: `338 passed, 6 deselected, 1 warning`; Ruff, mypy,
  `git diff --check`, bundled TypeScript compilation, and the Vite production
  build passed.
- The Clash tunnel remains the verified path for Railway PostgreSQL and Redis
  from this workstation.
