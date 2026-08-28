# Account, Wallet, and Task Manager Design

Date: `2026-08-28`  
Status: `proposed for user review`  
Scope: local-first, single-administrator management application

## 1. Decision Summary

Build a separate local web application around the existing `twitter-cli`
substrate. It manages imported social accounts, wallet sources and derived
addresses, immutable account-address bindings, and independently executable
tasks. The first production shape is:

```text
React management UI
        |
FastAPI local API
        |
PostgreSQL <----> Redis
        |              |
Encrypted vault     Worker processes
                         |
               Per-task browser contexts
```

The same containers run on a laptop and later on a server. PostgreSQL owns
durable state and resource leases. Redis transports queued work only; a lost
Redis item is reconstructed from durable job rows.

## 2. Confirmed Product Constraints

1. The application has one administrator in v1.
2. Import accepts tab-separated rows in this order:
   `login account`, `password`, `TOTP secret`, `email`, `email password`,
   `token`, `Cookie`.
3. Wallets support private-key import and BIP-39 mnemonic import with Ethereum
   derivation path `m/44'/60'/0'/0/index`.
4. An imported social account may bind to one wallet address only. A wallet
   address may bind to one social account only.
5. A completed binding is immutable. The record can be archived, but neither
   side can be reassigned.
6. Each row can be run manually through `Bind`, `Repost`, and `Claim` actions.
7. Batch execution defaults to 10 independently scheduled rows.
8. A delayed external result remains visible as `waiting_external_validation`;
   it is not marked failed merely because the first poll did not update.
9. Secrets are encrypted at rest. The administrator retains a recovery value
   that can decrypt a migrated database backup later.
10. Performance, isolation, and recovery matter more than maximizing
    concurrent browser sessions.

## 3. Product Boundary

### In Scope

- Import, validation, duplicate reporting, and archival of account data.
- Import, derivation, export, and archival of wallet data.
- Immutable account-address pairing.
- Manual runs and queued batch runs for binding, repost verification, and
  reward-claim workflows supplied by an integration adapter.
- Per-row status, events, retry, pause, cancel, and audit history.
- Encrypted local persistence, backup creation, restoration, and later server
  migration.

### Explicitly Deferred

- Multi-user permissions, teams, or approval workflows.
- Rebinding or reassignment of a completed pairing.
- Cross-project task templates and generalized campaign authoring.
- Native desktop packaging.
- High-availability, multi-region, or active-active workers.
- Any unbounded background action that is not represented by a durable task
  record.

## 4. User Workflows

### 4.1 First Run and Vault Setup

1. The administrator creates a management password.
2. The application generates a random vault key and encrypts it twice:
   once with a key derived from the management password and once with a
   generated recovery key.
3. The setup screen displays the recovery key once, in a copyable format. It
   is never written to logs, exports, task events, screenshots, or source code.
4. The application requires the password to unlock the vault for the current
   local session. Sensitive operations, including mnemonic/private-key export,
   require a fresh password check.

The recovery key is the portability guarantee. The management password is the
normal everyday unlock. Either can unwrap the same vault key when the matching
metadata is present.

### 4.2 Account Import

1. The administrator uploads a UTF-8 TSV file or pastes rows.
2. The import screen maps and validates the required columns.
3. Before saving, it shows parsed rows, non-sensitive diagnostics, and
   duplicate/conflict reasons.
4. Valid rows are encrypted and stored. Duplicate token, account handle, or
   existing session records are skipped by default and recorded in the import
   result.
5. The application creates an `import_batch` and immutable `import_row`
   evidence for every submitted row without retaining plaintext secret values.

### 4.3 Wallet Import and Derivation

1. The administrator imports either one private key or one BIP-39 mnemonic.
2. For a mnemonic, the administrator chooses a start index and count.
3. The application derives addresses using the fixed BIP-44 path and displays
   public addresses for confirmation.
4. The encrypted source and each derived wallet are stored separately.
5. A derived address already known to the vault is reported as a duplicate and
   is not recreated.

### 4.4 Binding

1. The administrator opens an unbound account or unbound address and selects
   the matching counterpart.
2. The UI shows the pairing as a one-time decision. It validates both
   resources are active and unbound before the task is created.
3. A `bind` job acquires a lease on both resources and runs in a fresh browser
   context dedicated to that job.
4. The job records each observable transition and then polls the integration
   status until it reaches a confirmed result or a deadline.
5. Only a confirmed external binding creates the immutable binding record.

### 4.5 Repost and Claim

1. The user starts `Repost` or `Claim` from an existing binding row, a task
   detail page, or a selected batch.
2. The system creates a durable job with a deterministic idempotency key.
3. The job runs only when its resource leases are available.
4. The job transitions to `waiting_external_validation` when the external
   system has accepted the action but has not yet reflected its result.
5. The UI supports reload/poll, retry after a terminal failure, pause before
   dispatch, and cancel before a non-reversible external action begins.

### 4.6 Batch Execution

1. The administrator filters eligible bindings, previews the exact selected
   rows, and selects an action.
2. The UI creates a named batch. The default dispatch limit is 10 jobs.
3. The scheduler dispatches only jobs that do not conflict on account or
   wallet lease keys.
4. Each job receives its own task context, browser context directory, trace
   identifier, time budget, and retry policy.
5. Batch progress is derived from job rows, never inferred from browser
   activity alone.

## 5. Information Architecture and UI

The UI is a dense local operations console: quiet, table-first, and optimized
for repeated review. Use a light background, neutral data surfaces, restrained
violet for primary commands, and semantic status colors. Avoid dashboard
decoration that competes with operational state.

### 5.1 Navigation

| Page | Primary purpose | Key actions |
|---|---|---|
| Overview | current queue health, recent events, exceptions | resume batch, inspect exception |
| Accounts | imported social accounts and session health | import, verify, archive, start task |
| Wallets | sources, derived addresses, and safe export | import, derive, export, archive |
| Bindings | immutable account-address pairings | create binding, inspect history |
| Tasks | all batches and jobs | create batch, pause, retry, cancel |
| Task Detail | per-job evidence and external state | poll now, retry, open trace |
| Vault & Backup | unlock state, backup, restore, recovery check | create backup, verify restore |

### 5.2 Tables

Tables are the canonical work surface. They provide server-side filtering,
sorting, pagination, row selection, visible status chips, and a narrow action
column. A selected account or binding opens a side panel rather than a second
card-heavy page.

Account columns: handle, import source, session health, binding state, last
activity, active lease, and actions.

Wallet columns: public address, source type, derivation path/index, binding
state, last activity, active lease, and actions.

Binding columns: account, address, external task state, binding timestamp,
latest job state, and actions.

### 5.3 Row Actions

The binding table exposes these command buttons:

- `Bind`: visible only for an eligible unbound account-address selection.
- `Repost`: visible only for a confirmed binding with an eligible task.
- `Claim`: visible only after the integration reports the prerequisite state.

Buttons are disabled with a precise displayed reason when a row is archived,
locked by another job, already terminal, or awaiting external validation.

## 6. Architecture

### 6.1 Owners

| Component | Responsibility | Does not own |
|---|---|---|
| UI | user input, query views, command initiation | secret decryption rules |
| API | validation, authorization for the local admin, transactions | background execution |
| Vault service | encryption, decryption, key wrapping, redaction | business status transitions |
| Repository layer | database transactions and queries | browser or API calls |
| Scheduler | eligibility, lease acquisition, dispatch | external protocol details |
| Worker | one job execution and evidence capture | batch selection |
| Integration adapters | external workflow operations and status reads | database locking |
| Existing `twitter_cli` package | X client behavior used by an adapter | manager persistence |

### 6.2 Adapter Boundary

Each external service has an adapter with a deliberately small contract:

```text
validate_account(account_secret) -> AccountHealth
prepare_wallet(wallet_secret) -> WalletContext
start_bind(account, wallet) -> ExternalOperation
read_bind_status(operation) -> ExternalStatus
start_repost(binding, task_target) -> ExternalOperation
read_repost_status(operation) -> ExternalStatus
start_claim(binding, task_target) -> ExternalOperation
read_claim_status(operation) -> ExternalStatus
```

Adapters return normalized results and redacted evidence. They do not mutate
database rows directly. This keeps external integration changes from leaking
into task scheduling or vault code.

### 6.3 Browser Isolation

- One worker job creates one browser context and one temporary profile
  directory.
- Context directories are owned by the job ID, created with restrictive file
  permissions, and removed after a configurable evidence-retention window.
- Browser contexts never contain two social accounts or two wallet identities.
- A job may reload and poll its own external state, but it never reuses a
  context from another job.
- Screenshots and network traces are opt-in diagnostic artifacts, encrypted,
  redacted where possible, and referenced from the task event stream.

## 7. Data Model

All primary keys are UUIDv7. All timestamps are UTC. Sensitive columns use
encrypted binary envelopes and must never be selected in list endpoints.

### 7.1 Core Tables

| Table | Purpose | Important constraints |
|---|---|---|
| `social_accounts` | non-secret account identity and lifecycle | unique normalized handle; active/archived |
| `account_secrets` | encrypted account credentials/session material | one current secret version per account |
| `wallet_sources` | mnemonic or imported-key source metadata | source may produce many wallets |
| `wallets` | public address and derivation metadata | unique normalized address |
| `wallet_secrets` | encrypted private material or source reference | no plaintext address secrets |
| `account_wallet_bindings` | immutable pairing | unique active account; unique active wallet |
| `import_batches` | import request summary | immutable source metadata and counts |
| `import_rows` | per-line import result | unique batch and line number |
| `task_batches` | user-created group of jobs | configurable dispatch cap |
| `task_jobs` | durable execution state | unique idempotency key |
| `task_events` | append-only redacted timeline | monotonically increasing sequence per job |
| `resource_leases` | short-lived account/wallet locks | unique lease key |
| `audit_logs` | local admin security and data events | append-only |
| `vault_metadata` | wrapped vault key and KDF parameters | exactly one active vault |

### 7.2 Binding Constraints

`account_wallet_bindings` contains `social_account_id`, `wallet_id`,
`state`, `bound_at`, `external_reference`, and `archived_at`.

Database enforcement:

```sql
create unique index one_active_binding_per_account
  on account_wallet_bindings (social_account_id)
  where archived_at is null;

create unique index one_active_binding_per_wallet
  on account_wallet_bindings (wallet_id)
  where archived_at is null;
```

No update endpoint may change `social_account_id` or `wallet_id` after the
record reaches `bound`. Archiving ends operational use while preserving the
historical relationship.

### 7.3 Task Job Shape

`task_jobs` contains:

- `id`, `batch_id`, `kind`, `state`, `attempt`, and `priority`;
- `social_account_id`, `wallet_id`, and optional `binding_id`;
- `idempotency_key`, `lease_keys`, `scheduled_at`, `started_at`, and
  `finished_at`;
- `external_operation_ref`, redacted `result_summary`, and `failure_code`;
- `poll_deadline_at`, `next_poll_at`, and `cancel_requested_at`.

The idempotency key is:

```text
<kind>:<binding-or-resource-pair>:<external-target>
```

Creating a duplicate key returns the existing job instead of creating another
external action. Explicit retry increments that job's attempt number and keeps
the same idempotency key; it never creates a second action for the same
binding/resource pair and external target.

## 8. Task State Machine

```text
draft -> queued -> leased -> running
                         |       |
                         |       +-> waiting_external_validation
                         |                 |             |
                         |                 v             v
                         +--------------> succeeded   failed

queued -> paused
queued -> cancelled
failed -> queued (explicit retry, new attempt)
waiting_external_validation -> queued (explicit recheck after deadline)
```

Rules:

1. Only the worker that owns an unexpired lease may move a job to `running`.
2. Every transition writes an append-only `task_event` in the same database
   transaction.
3. `waiting_external_validation` retains the resource leases only while an
   interactive context must remain alive. Otherwise it releases them and
   schedules a poll-only continuation. The job's durable idempotency key still
   prevents another action for the same target while polling is in progress.
4. A worker crash leaves a lease to expire. The scheduler can recover the job
   after the grace period and records a recovery event.
5. Repost and claim commands use provider-specific idempotency when available;
   otherwise the adapter checks the external state before replaying an action.

## 9. Performance, Isolation, and Scheduling

### 9.1 Concurrency Model

The default batch contains 10 jobs, but it is not a promise to run all 10 at
once. The scheduler uses:

- global worker concurrency: `3` locally by default;
- browser concurrency: `2` locally by default;
- per-account concurrency: `1`;
- per-wallet concurrency: `1`;
- per-integration configurable throughput limits;
- adaptive backoff for retryable external failures.

The server deployment may raise global concurrency after observing CPU,
memory, browser stability, and external-service response behavior. Per-resource
concurrency stays at one.

### 9.2 Resource Leases

For a job involving account `A` and wallet `W`, the scheduler atomically
acquires:

```text
account:A
wallet:W
```

Both lease rows are created or neither is. Lease acquisition uses a database
transaction with unique keys and expiration. This is the canonical
non-interference mechanism: two batches may run concurrently, but no two jobs
can operate the same account or wallet at the same time.

### 9.3 Fair Dispatch

The scheduler selects the oldest eligible job per batch in round-robin order,
then checks lease availability. A batch with a blocked account does not hold up
unrelated accounts in that batch or other batches.

### 9.4 Targets

For an initial local deployment with up to 500 accounts and 500 addresses:

| Measure | Target |
|---|---|
| account or wallet list page | p95 under 500 ms |
| import preview, 500 rows | under 3 s excluding vault unlock |
| queue dispatch decision | under 250 ms |
| post-crash lease recovery | under 2 minutes |
| status poll persistence | under 1 s per response |
| secret values in logs/events | zero |

## 10. Vault, Backup, and Recovery

### 10.1 Encryption Format

- Generate one 256-bit random vault data-encryption key.
- Encrypt each sensitive value with AES-256-GCM using a fresh random nonce and
  authenticated metadata: table name, record ID, field name, and secret
  version.
- Derive a password-wrapping key with Argon2id. Store KDF salt and parameters
  in `vault_metadata`.
- Generate a high-entropy recovery key and wrap the same vault key with an
  independent recovery-key derivation.
- Store only ciphertext envelopes, wrapped vault keys, salts, nonces, and
  version metadata in PostgreSQL.

Sensitive values include account passwords, TOTP seeds, mailbox passwords,
session tokens, complete cookie sets, private keys, mnemonics, and any
provider-issued refresh material.

### 10.2 Recovery Material

The initial setup emits exactly two user-held values:

1. Management password chosen by the administrator.
2. Generated vault recovery key, shown once and required for disaster
   restoration if the management password is lost.

The application never sends these values over the network. It does not retain
them in browser autofill, shell commands, logs, task events, or backups in
plaintext.

### 10.3 Backups and Migration

The Vault & Backup page creates an encrypted backup package containing:

- PostgreSQL logical backup;
- vault metadata and encryption format version;
- non-secret application configuration;
- checksum manifest and restore instructions.

The package is encrypted with a key derived from the recovery key. Restore
creates a new local database, verifies checksums, unwraps the vault key, and
runs a read-only integrity check before enabling task dispatch. Migrating to a
server means moving the encrypted package and starting the same container
versions; no secret re-entry is required.

### 10.4 Sensitive Data Rules

- Never return ciphertext or plaintext secrets in list APIs.
- Export private keys or mnemonics only after a fresh password check; create an
  audit event with metadata only.
- Mask account data in UI and show no secret values in screenshots.
- Tests use generated fixtures only. Production values are never copied into
  tests, fixtures, documentation, or commits.

## 11. APIs

The first API is local-only and bound to loopback by default. It uses a local
administrator session, CSRF protection for browser commands, and a short vault
unlock window.

Representative resources:

```text
POST   /api/imports/accounts/preview
POST   /api/imports/accounts/commit
GET    /api/accounts
POST   /api/accounts/{id}/verify
POST   /api/wallet-sources
POST   /api/wallet-sources/{id}/derive
GET    /api/wallets
POST   /api/bindings
GET    /api/bindings
POST   /api/task-batches
GET    /api/task-batches/{id}
POST   /api/task-jobs/{id}/retry
POST   /api/task-jobs/{id}/pause
POST   /api/task-jobs/{id}/cancel
POST   /api/task-jobs/{id}/poll
POST   /api/vault/backups
POST   /api/vault/restore/verify
```

The UI never receives unwrapped private material except through a deliberate,
freshly authenticated export endpoint.

## 12. Failures and Operator Experience

| Situation | System behavior |
|---|---|
| Duplicate import row | report conflict, skip by default, retain redacted import result |
| Existing active binding | block bind before queueing and explain which invariant applies |
| Account or wallet already leased | leave job queued and show blocking job ID/status |
| Browser crash | append event, release after expiry, schedule recovery |
| External state delayed | move to `waiting_external_validation`, schedule polls |
| External terminal rejection | mark failed with normalized code and redacted evidence |
| Worker restart | recover eligible expired leases from PostgreSQL |
| Incorrect unlock password | do not distinguish vault existence from bad input |
| Failed restore validation | keep dispatch disabled and preserve original backup |

## 13. Observability and Audit

Every job has a trace ID. Structured logs include job ID, batch ID, account ID,
wallet ID, adapter, state transition, elapsed time, and error class; they
exclude secret content and full external URLs containing sensitive query
parameters.

The audit log records:

- vault initialization, unlock, lock, backup, restore verification;
- imports, duplicate decisions, archive actions;
- pairing creation and archive;
- task creation, cancel, pause, retry, and terminal state;
- sensitive export metadata without secret material.

The overview page shows queue depth, workers alive, leased resources, waiting
external validations, recent failures, and backup health.

## 14. Alternatives Considered

### A. Single Python process with SQLite and background threads

Fast to start but weak for concurrent browser runs, lease recovery, and a later
server move. Rejected.

### B. FastAPI, PostgreSQL, Redis, and dedicated workers

One deployable local stack with durable transactions, clear queue ownership,
and a direct server migration path. Recommended.

### C. Many microservices with a browser fleet

Appropriate only for significantly larger scale. It adds operational owners and
failure modes before there is evidence that they are needed. Deferred.

## 15. Architecture Integrity Review

### Invariants

1. Database rows are the source of truth for bindings, job state, and leases.
2. A worker never shares browser state between identities.
3. One account and one wallet each have at most one active task lease.
4. One account and one wallet each have at most one active immutable binding.
5. Secrets are encrypted at rest and absent from operational telemetry.

### Canonical Owners

- Vault service owns all encryption and decryption.
- Scheduler owns eligibility and lease acquisition.
- Worker owns one job execution.
- Adapter owns external protocol interaction.
- Repository layer owns durable state changes.

### Retirement and Falsifiers

The manager must not use direct script files as a production source of truth.
If a single-process queue proves sufficient under measured load and recovery
tests, Redis may be removed in a later design revision; it is not removed
before those tests exist.

### ADR Signal

Implementation planning should create ADRs for:

1. vault envelope format and recovery-package format;
2. lease transaction and worker recovery semantics;
3. adapter contract and browser-context lifecycle.

## 16. Acceptance Criteria

1. Importing a 500-row TSV produces per-row validation results, duplicate
   reporting, and no plaintext secrets in database logs or UI output.
2. Importing a mnemonic derives deterministic Ethereum addresses using the
   stated path and prevents duplicates.
3. A successfully bound account-address pair cannot be reassigned through UI,
   API, or direct application service calls.
4. Two jobs that share an account or address do not run concurrently; two jobs
   with distinct resources can run concurrently.
5. Worker termination during a task leads to a visible recovery event and an
   eligible retry without losing the durable task record.
6. Delayed external verification remains visible, pollable, and separate from
   terminal failure.
7. Backup created on a local instance restores into a fresh instance using the
   recovery key and preserves the ability to decrypt test secrets.
8. Existing `twitter` CLI commands and their current tests remain compatible.
9. The interface supports manual row actions and batch actions without hiding
   resource conflicts.

## 17. Implementation Sequencing

After this design is approved, implementation planning should split work into:

1. application scaffold, local containers, database migrations, and vault;
2. account/wallet import and management APIs;
3. binding model, task scheduler, leases, and worker recovery;
4. adapter boundary and browser lifecycle;
5. operations UI and batch controls;
6. backup/restore, observability, and end-to-end verification.

## Appendix A: Design Artifacts

### TaskIntentDraft

- Outcome: a local operational application for independent account, wallet,
  binding, and task management.
- Success evidence: immutable bindings, isolated execution, durable task
  recovery, encrypted secrets, successful backup/restore, and usable manual
  plus batch UI.
- Stop condition: written design is reviewed and approved before planning.
- Primary risks: secret recovery failure, shared browser state, duplicate
  external actions, and premature scale complexity.

### BaselineReadSetHint

- `AGENTS.md`
- `README.md`
- `docs/x-cookie-auth-flow.md`
- `docs/kredo-wallet-login-flow.md`
- current `twitter_cli/`, `scripts/`, and `tests/` layout

### BaselineUsageDraft

- Required baseline refs: project README and verified X/Kredo flow documents.
- Delivered context refs: prior user decisions in this thread.
- Acknowledged before plan refs: this design spec and initial baseline.
- Cited in design refs: existing CLI owns X behavior; existing flow notes
  establish eventual external consistency.
- Missing refs: no current database or UI baseline exists.
- Decision: continue to user review.

### ImpactStatementDraft

- Affected layers: new UI, API, data model, vault, scheduler, workers, adapter
  boundary, and deployment.
- Existing owner impact: preserve `twitter_cli` as the X client substrate.
- Invariants: resource exclusivity, immutable bindings, encrypted secrets,
  durable job state.
- Compatibility: current CLI behavior remains unchanged.
- Non-goals: multi-user and distributed fleet behavior in v1.
