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
