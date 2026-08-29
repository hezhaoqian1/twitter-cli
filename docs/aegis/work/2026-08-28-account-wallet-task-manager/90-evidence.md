# Evidence Bundle

Date: `2026-08-28`

## Batch 1, Task 1

- Manager runtime tests: `3 passed`.
- Existing non-smoke suite: `261 passed, 6 deselected`.
- Compose validation: `docker compose -f docker-compose.manager.yml config`
  completed successfully.
- Static checks: `ruff check manager_api tests/manager`, `mypy manager_api`,
  and `git diff --check` completed successfully.
- Python syntax: `python -m compileall -q manager_api tests/manager` completed
  successfully.

## Scope Evidence

- Added typed settings, FastAPI live/readiness routes, local PostgreSQL/Redis
  Compose services, and a manager API image.
- Health readiness reports only `ok`/`down` dependency states; exception text
  and connection values are not returned.
- Existing `twitter_cli` public behavior remains covered by the full regression
  suite.
- No account credentials, cookies, wallet material, private keys, or concrete
  hosted-service connection values were added to the new files.

## Batch 1, Task 2

- SQLite metadata creation: `4 passed`.
- Manager runtime regression: `3 passed`.
- Full non-smoke regression: `265 passed, 6 deselected`.
- Alembic migration smoke: temporary SQLite database upgraded to
  `0001_manager_core`; subsequent `alembic check` reported no new operations.
- Static checks: Ruff, mypy, compileall, and `git diff --check` completed
  successfully.
- Compose validation: `docker compose -f docker-compose.manager.yml config`
  completed successfully.

## Schema Evidence

- All manager tables are registered in `Base.metadata`.
- Active account-wallet uniqueness is enforced independently for accounts and
  wallets through partial unique indexes.
- Event and audit metadata use the database column name `metadata` while
  avoiding SQLAlchemy's reserved declarative attribute name.
- Wallet source and wallet archive timestamps use timezone-aware datetime
  columns, matching the account and binding lifecycle fields.
- String-backed enums work on the declared Python 3.10+ runtime range.

## Batch 2, Task 3: Vault Evidence

- Vault-focused tests: `5 passed`.
- Argon2id derives independent password and recovery wrapping keys from stored
  salts and explicit parameters.
- AES-256-GCM encrypts both wrapped vault keys and per-field envelopes with
  authenticated table, record, field, and secret-version context.
- Password unlock, recovery unlock, lock, cache TTL expiry, duplicate
  initialization, wrong-password handling, and cross-context tamper rejection
  are covered.
- Pydantic unlock input uses `SecretStr`; tests confirm the password is absent
  from object representations and JSON dumps.
- No plaintext vault key is persisted; initialization returns the generated
  recovery key only through the one-time service result.

## Batch 2, Task 3 Closeout

- Focused Vault suite: `5 passed in 2.79s`.
- Full non-smoke suite after the Vault change: `270 passed, 6 deselected`.
- Static verification: Ruff, mypy, compileall, and `git diff --check`
  completed successfully.
- The slice stayed within the Vault/schema boundary and did not change the
  existing `twitter_cli` contract.

## Residual Risk

- The API image and local services have been statically validated; a live
  PostgreSQL/Redis container startup and migration check remains outstanding.
- HTTP Vault unlock/session wiring is not part of the account import slice, so
  the import service requires an explicitly unlocked `VaultService` for
  encrypted commit. The next Vault API slice will provide that request-scoped
  runtime dependency.
- Wallet derivation, task services, worker recovery, backup/restore, and UI
  behavior remain future slices.

## Batch 2, Task 4: Account Import Evidence

- Account TSV contract is seven columns in the current user-confirmed order:
  `handle`, `password`, `totp`, `email`, `email_password`, `token`, `cookie`.
- Preview preserves one-based physical line numbers and classifies malformed,
  duplicate-in-file, existing-account, and conflicting-session rows.
- Preview and account list schemas contain masked identity/status fields only;
  source content, plaintext secrets, ciphertext, and secret envelopes are not
  returned.
- Commit creates one `ImportBatch` and one `ImportRow` per input line, skips
  duplicate/conflicting rows by default, and encrypts password, TOTP, mailbox
  password, token, and Cookie fields with Vault AAD bound to the secret record.
- Focused import and redaction suite: `5 passed`.
- Full non-smoke suite: `275 passed, 6 deselected`.
- Ruff, mypy, compileall, and `git diff --check` completed successfully.

## Batch 2, Task 6: Binding Evidence

- Binding service suite: `4 passed`.
- Pending intents require active, unbound account and wallet resources.
- Confirming a binding records `bound_at` and an external reference; repeated
  confirmation with the same reference is idempotent.
- Confirmed pairings reject changes and remain non-reassignable after archive.
- Archived resources, active binding intents, historical confirmed bindings,
  and current account/wallet leases return distinct conflict codes.
- Binding API routes are registered and list responses contain public identity
  fields only.
- Full non-smoke suite after binding work: `287 passed, 6 deselected`.
- Ruff, mypy, compileall, and `git diff --check` completed successfully.

## Vault HTTP Runtime Wiring Evidence

- Focused Vault/import/runtime suite: `14 passed`.
- `VaultRuntime` is shared by request-scoped `VaultService` instances and
  expires or clears the in-memory key according to the configured TTL.
- Vault HTTP routes expose only status, lifecycle results, and the one-time
  recovery key; password and recovery inputs use `SecretStr` request schemas.
- Account commit receives the shared runtime-backed Vault service instead of
  constructing an isolated locked service.
- `mypy` and Ruff checks for the changed manager API/Vault files passed.

## Batch 2, Task 5: Wallet Evidence

- Wallet derivation and validation suite: `5 passed`.
- MetaMask-compatible BIP-44 derivation is verified against generated fixture
  addresses for multiple indices.
- Private-key input accepts canonical `0x` or raw 32-byte hexadecimal and
  stores only encrypted source/private-key material.
- Duplicate normalized addresses are classified and skipped without creating
  a second wallet record.
- Wallet preview/commit/derive/list routes are registered and use the shared
  in-memory Vault runtime for encrypted operations.
- Wallet API redaction test verifies that mnemonic material is absent from
  responses and that a locked Vault returns `423`.
- Focused manager suite after wallet routing: `13 passed`.
- Full non-smoke suite after wallet routing: `283 passed, 6 deselected`.
- Ruff, mypy, compileall, and `git diff --check` completed successfully.

## Batch 2, Task 7: Task State Evidence

- Task creation derives a deterministic idempotency key from task kind,
  resource/binding scope, and a SHA-256 digest of the external target; raw
  targets are not exposed in task responses.
- Duplicate active task creation reuses the existing durable job, including
  under a uniqueness race, while retries keep the same idempotency boundary
  and increment `attempt`.
- Permitted state transitions, pause/cancel/retry/poll rules, lease ownership
  checks, and expired-lease recovery are enforced by `TaskService`.
- Every state mutation appends one redacted, ordered event in the same
  transaction boundary as the job update.

## Batch 3, Task 8: Lease, Queue, and Worker Evidence

- Lease acquisition deduplicates resource keys and grants all account/wallet
  keys atomically; a conflicting key produces no partial grant.
- Scheduler capacity respects worker and browser concurrency limits and
  selects eligible jobs round-robin across batches.
- Lease release requires the matching owner token; expired leases are removed
  before replacement and recovered jobs return to `queued` with a recorded
  recovery event.
- Redis reliable-list messages contain only task and lease identifiers;
  acknowledgement occurs after durable worker completion, and failed message
  handling requeues the payload.
- Worker success, typed invalid outcomes, exception redaction, graceful
  shutdown, and expired-lease recovery are covered by deterministic tests.
- Focused lease/worker suite: `6 passed`.

## Batch 4, Task 9: Normalized Adapter Evidence

- `XAdapter` bridges the existing `twitter_cli` client contract for account
  verification and repost actions without owning transport setup.
- `KredoAdapter` isolates each bind, repost, claim, and status call in a
  factory-owned workflow context, maps provider state aliases to normalized
  task states, and preflights delayed or already-completed repost/claim work.
- Adapter material has secret-safe representations; nested evidence and typed
  provider errors are redacted before they cross into task events.
- Adapter fake suite: `14 passed in 0.46s`.

## Batch 4, Task 10: Operations UI Evidence

- The React/Vite UI builds successfully with the bundled workspace Node:
  `tsc -b` followed by `vite build`.
- Production build result: `1578 modules transformed`; generated assets were
  `dist/index.html`, CSS, and JavaScript bundles.
- Responsive layout was visually checked at mobile, tablet, and desktop
  widths using:
  `/tmp/hashkey-manager-layout-v2-mobile.png`,
  `/tmp/hashkey-manager-layout-v2-tablet.png`, and
  `/tmp/hashkey-manager-layout-v2-desktop.png`.
- Browser verification observed HTTP 200 API requests and no new application
  errors. The tablet task area now switches to a stacked layout at the
  `max-width: 900px` breakpoint.

## Current Verification Baseline

- Fresh complete non-smoke suite: `336 passed, 6 deselected, 1 warning`.
- Fresh focused batch/backup suite: `5 passed in 4.82s`.
- Fresh production-code type check: `mypy manager_api` passed for 54 source
  files.
- Fresh Ruff check: `All checks passed!`.
- Fresh Python compile check and `git diff --check` passed.
- Fresh frontend checks: `tsc -b` passed and Vite transformed 1578 modules
  into a successful production build.
- Full `mypy manager_api tests/manager` remains red only on fixture-level
  annotations in ten test files; it does not report production code errors.

## Batch 5, Task 11: Ordered Pair Workflow Evidence

Superseded on 2026-08-29: the public ordered workflow API was removed after
the operator model moved to explicit single-stage batches. Use
`POST /api/tasks/stages` for verify, bind, repost, and claim.

- Added a durable `account_wallet` workflow batch that creates one independent
  `verify_account -> bind -> repost` chain per selected pair.
- Added a self-referential predecessor field to `task_jobs`; the scheduler only
  dispatches a dependent job after its predecessor reaches `succeeded`.
- Each child job retains the account and wallet lease keys for its own pair, so
  a failed or delayed pair does not prevent unrelated pairs from advancing.
- Added a now-retired workflow endpoint and management-console dialog for
  explicit account/address pairing, repost target, and default dispatch limit
  10.
- Synthetic workflow suite: `6 passed`; manager suite after the change:
  `68 passed, 1 warning`.
- Fresh SQLite migration smoke reached Alembic `head` through
  `0002_task_workflows`.
- No provider credentials, cookies, private keys, mnemonics, or remote
  database connection strings were used in this evidence.

## Batch 5, Task 12: Backup and Batch Lifecycle Evidence

- Encrypted backup/restore tests cover round trips, checksum tampering,
  manifest tampering, wrong recovery keys, malformed packages, and non-empty
  restore targets.
- Backup HTTP routes return only a sanitized summary; the downloadable
  package is encrypted and the UI reports format, table, row, key, and
  checksum status without displaying secrets.
- Batch pause, resume, and cancel transitions are durable, and the scheduler
  skips non-active batches.
- Pending external polling observes cancellation and releases the pair lease
  through the worker path.
- Fresh focused batch/backup suite: `5 passed`.

## Closed-Slice Boundary

- Batch 4 adapters and UI are complete based on synthetic backend tests,
  static checks, production build output, and visual browser checks.
- Provider credentials, cookies, wallet keys, recovery material, and hosted
  database/Redis connection values remain excluded from source, fixtures,
  logs, and evidence.
- Live PostgreSQL/Redis startup and migration smoke are verified through the
  local Clash tunnel; direct TCP access remains unavailable from this host.
- Encrypted backup/restore and batch lifecycle controls are implemented and
  verified. Railway runtime verification is complete through the local Clash
  tunnel; hosted deployment and live provider end-to-end actions remain open.

## Batch 4, Task 11: Kredo Balance Evidence

- The Kredo account summary contract was recorded from the frontend bundle:
  `points`, `cashHsk.available`, and `portfolio.positionsValueHsk`.
- Added `KredoBalanceSnapshot`, one replaceable row per binding, with numeric
  precision, sync status, last successful timestamp, and safe error code.
- Added `balance_sync` task creation, isolated account/wallet lease scope, and
  worker execution through `KredoAdapter.account_summary`.
- Sync failures preserve the last successful numeric values and only update
  the redacted status/error fields.
- Added `GET /api/balances`, `GET /api/balances/{binding_id}`, and
  `POST /api/balances/sync`; binding responses also include the latest balance.
- The binding UI now shows Points, available HSK, position HSK, and sync time,
  with row-level and selected-batch sync actions.
- Focused adapter and balance suite: `20 passed`.
- Raw provider responses, account credentials, cookies, wallet keys, and
  remote connection strings remain excluded from source, tests, logs, and
  evidence.

## Railway Runtime Configuration Check

- The local manager runtime now reads the supplied Railway PostgreSQL and
  Redis values from the Git-ignored `.env.manager` file.
- The requested connection details are recorded in the Git-ignored
  `docs/local-railway-runtime.md` file only; no concrete value was added to
  tracked source, fixtures, logs, or committed evidence.
- On 2026-08-28, DNS resolved both Railway proxy hosts, while direct TCP
  connections to PostgreSQL `34945` and Redis `35427` timed out.
- Local unit and integration tests continue to use isolated SQLite databases
  and deterministic doubles; the private `.env.manager` path is reserved for
  real dependency smoke checks and migrations.
- Added `scripts/manager_clash_tunnel.py`, a generic local TCP forwarder that
  uses Clash HTTP `CONNECT` without logging application payloads.
- Through Clash `127.0.0.1:7890`, PostgreSQL and Redis probes passed and
  `uv run python scripts/manager_clash_tunnel.py -- uv run python
  scripts/manager_migrate.py` reached Alembic `head` on 2026-08-28.
- The first live migration exposed PostgreSQL incompatibility in the
  SQLite-oriented batch rebuilds. Revisions `0002`, `0003`, and `0004` now
  use PostgreSQL-native ALTER operations and retain batch rebuilds only for
  SQLite.

## Batch 5, Task 13: Scheduler and Cancellation Hardening

- Batch dispatch now accounts for active leases per `TaskBatch` and enforces
  each batch's `dispatch_limit` before selecting another child job.
- Queued dependents of failed, blocked, or cancelled predecessors transition
  to `blocked` with the redacted failure code `dependency_failed`, so they do
  not remain indefinitely eligible-looking in the queue.
- A retryable predecessor recovery requeues blocked dependents when the
  predecessor succeeds, preserving the ordered workflow chain.
- Queued and paused cancellation is immediate; leased cancellation releases
  resource leases; running cancellation records a request and the worker
  commits `cancelled` after the handler returns.
- Regression coverage includes batch-capacity enforcement, dependency
  blocking, dependency recovery, lease release, and cancellation winning over
  a late worker success.
- Fresh complete non-smoke suite: `336 passed, 6 deselected, 1 warning`.
- Fresh checks: Ruff, mypy for `manager_api`, Python compileall, and
  `git diff --check` all passed.
- Frontend validation with the bundled Node runtime passed `tsc -b` and
  Vite production build (`1578 modules transformed`).

## Railway Connectivity via Clash

- Direct TCP access remains unavailable from this workstation.
- Through local Clash HTTP `127.0.0.1:7890`, PostgreSQL returned `SELECT 1`,
  Redis returned `PING=True`, and `/health/ready` reported both dependencies
  as `ok` on 2026-08-28.
- The probe did not write application data and did not print connection
  credentials.

## Runtime Observability Slice

- Added read-only `GET /api/runtime/metrics` aggregation for Redis ready and
  processing queue depth, durable task counts, active and expiring leases,
  latest task completion, and redacted Worker heartbeat counts.
- The Overview page now renders the runtime snapshot separately from account,
  wallet, binding, and task totals.
- Runtime metrics use the existing Railway configuration and can be checked
  through `127.0.0.1:7890` without exposing provider credentials or vault
  ciphertext.
- Focused runtime tests: `6 passed`.
- Fresh complete suite after the slice: `338 passed, 6 deselected, 1 warning`.
- Fresh static checks: Ruff and mypy passed. Bundled Node validation passed
  `tsc -b` and the Vite production build (`1578 modules transformed`).

## Worker Runtime Slice

- Added `manager_api.heartbeat.WorkerHeartbeat`, which writes an ISO timestamp
  under one process ID in `manager:workers:heartbeats` and removes only that
  field during shutdown.
- Added `TaskRunner.run_forever`, which periodically recovers expired leases,
  dispatches durable jobs, consumes reliable-list messages, commits each
  lifecycle boundary, and continues after one task-level exception.
- Added `scripts/manager_worker.py` as the independent Worker process entry
  point. It keeps X/Kredo providers behind the existing adapter contracts and
  loads the Kredo workflow factory from an environment module path.
- Added focused tests for heartbeat ownership, clean stop, durable completion,
  and queue drain behavior: `7 passed`.
- Fresh complete non-smoke suite: `340 passed, 6 deselected, 1 warning`.
- Ruff and mypy passed for the manager runtime and Worker entry point.
- Bundled Node validation passed `tsc -b` and the Vite production build
  (`1578 modules transformed`).
- Through Clash `127.0.0.1:7890`, PostgreSQL returned `SELECT 1` and Redis
  returned `PING=True`; no application data was written.

## Frontend Runtime Verification

- Vite served the manager UI at `http://127.0.0.1:5178/` using the bundled
  Node runtime.
- The API was started through `127.0.0.1:7890` with
  `manager_api.main:create_app --factory`; the module uses an app factory.
- `/health/ready` returned `{"status":"ok","checks":{"postgres":"ok","redis":"ok"}}`.
- The Tasks page rendered `API 在线` and returned an empty task page from
  PostgreSQL without console errors.
- At 390px width, the mobile Tasks page had equal `390px` document and client
  widths, confirming no horizontal overflow. The filter panel occupied the
  available content width.
- No account credentials, cookies, wallet keys, private keys, or provider
  connection values were included in this evidence.

## Stage Batch Workflow Evidence

- Added the stage-oriented workflow contract documented in
  `docs/manager-stage-batches.md`.
- Added `POST /api/tasks/stages`, with stage values `verify`, `bind`,
  `repost`, and `claim`.
- The Accounts page now exposes separate `批量校验` and `批量绑定` actions.
  Repost and claim remain on confirmed binding rows and selected binding
  batches.
- New stage batches use `workflow_type=stage:<name>` and their child tasks do
  not set `depends_on_task_id`, so a delayed repost validation does not block
  other operator stages.
- Synthetic runner acceptance passed with four separate stage batches:
  `jobs=16`, `bindings_bound=4`, `delayed_reposts_polled=4`, `repost_calls=4`,
  `claim_calls=4`, and empty ready/processing queues.
- Focused manager suite passed: `343 passed, 6 deselected, 1 warning`.
- Fresh checks passed: Ruff for `manager_api`, `scripts`, and `tests/manager`;
  bundled TypeScript compilation; and Vite production build.
- Browser QA with mocked API data passed for Overview, Accounts, Verify
  dialog, Bind dialog, Bindings, and Tasks pages. Screenshots are stored under
  `artifacts/qa/stage-ui-*.png`.
- No real account credentials, cookies, tokens, private keys, mnemonics, or
  Railway connection strings were added to tracked source, tests, screenshots,
  or evidence.
