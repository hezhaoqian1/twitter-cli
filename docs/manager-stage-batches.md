# Manager Stage Batches

The manager treats each account-wallet operation as an independent stage,
not as one mandatory end-to-end chain. This matches the Kredo flow where
external state can update slowly, especially after repost validation.

For the day-to-day operator sequence, see
[Manager Operator Guide](manager-operator-guide.md).

## Stages

1. Verify X sessions
   - Scope: one `social_account_id`.
   - Task kind: `verify_account`.
   - Batch label: `stage:verify`.
   - Result: updates account health only.

2. Bind account to wallet
   - Scope: one active account plus one active wallet.
   - Task kind: `bind`.
   - Batch label: `stage:bind`.
   - Result: creates a pending binding, then confirms it when the provider
     reports success.
   - Binding is immutable after confirmation.

3. Repost
   - Scope: one confirmed binding.
   - Task kind: `repost`.
   - Batch label: `stage:repost`.
   - Result: submits the repost once, then polls provider status without
     repeating the repost while the task is `waiting_external_validation`.

4. Claim
   - Scope: one confirmed binding.
   - Task kind: `claim`.
   - Batch label: `stage:claim`.
   - Result: claims the reward for the binding.

## Scheduling Model

- Stages are created separately through `POST /api/tasks/stages`.
- Child tasks inside a stage batch do not use `depends_on_task_id`.
- Resource leases stay scoped to the relevant account and wallet, so one
  delayed or failed item does not block unrelated accounts.
- Operators can create a verify batch for all accounts, wait, create a bind
  batch for selected pairs, wait for Kredo state, then create repost and claim
  batches when rows are ready.
- The default dispatch window is 10. It can be lowered for browser-heavy
  provider runs or raised after runtime observation.

## Secret Handling

- Account passwords, TOTP seeds, tokens, cookies, wallet private keys, and
  mnemonics live in Vault-encrypted records.
- Tests and acceptance scripts use synthetic credentials and synthetic provider
  adapters.
- Real provider sessions are injected through adapter boundaries and should not
  be copied into source files, fixtures, logs, screenshots, or commit messages.
- Local Railway connection values belong in the ignored `.env.manager` file or
  the process environment, never in tracked documentation.

## Acceptance Evidence

The local synthetic acceptance script drives the same durable runner and vault
through four independently created stage batches:

```bash
uv run python scripts/manager_synthetic_e2e.py
```

Expected summary shape:

```text
jobs=16
bindings_bound=4
delayed_reposts_polled=4
repost_calls=4
claim_calls=4
queue_ready=0
queue_processing=0
```
