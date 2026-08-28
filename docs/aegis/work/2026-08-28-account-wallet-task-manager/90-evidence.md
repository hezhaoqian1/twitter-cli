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

- Fresh complete non-smoke suite: `318 passed, 6 deselected, 1 warning in
  11.58s`.
- Fresh adapter suite: `14 passed in 0.46s`.
- Fresh static checks: Ruff, Mypy, compileall, and `git diff --check` passed.
- Fresh frontend production build: Vite completed successfully in `1.54s`.
- Aegis workspace `check` and `bundle` commands completed with exit status 0;
  these validate workspace structure and packaging, not external evidence
  sufficiency.

## Closed-Slice Boundary

- Batch 4 adapters and UI are complete based on synthetic backend tests,
  static checks, production build output, and visual browser checks.
- Provider credentials, cookies, wallet keys, recovery material, and hosted
  database/Redis connection values remain excluded from source, fixtures,
  logs, and evidence.
- Live PostgreSQL/Redis startup and migration smoke remain outstanding because
  the local Docker daemon is unavailable; readiness currently reports
  PostgreSQL `down` and Redis `ok`.
- Encrypted backup/restore and Railway deployment verification are the next
  implementation slices.
