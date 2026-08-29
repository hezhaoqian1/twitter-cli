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
   - Readiness: requires a confirmed binding with no existing repost task for
     that row and target.
   - Result: submits the repost once, then polls provider status without
     repeating the repost while the task is `waiting_external_validation`.

4. Claim
   - Scope: one confirmed binding.
   - Task kind: `claim`.
   - Batch label: `stage:claim`.
   - Readiness: requires a succeeded repost task and no existing claim task
     for the same binding.
   - Result: claims the reward for the binding.

## Scheduling Model

- Stages are created separately through `POST /api/tasks/stages`.
- Child tasks inside a stage batch do not use `depends_on_task_id`.
- Resource leases stay scoped to the relevant account and wallet, so one
  delayed or failed item does not block unrelated accounts.
- Operators can create a verify batch for all accounts, wait, create a bind
  batch for selected pairs, wait for Kredo state, then create repost and claim
  batches when rows are ready.
- A failed row is retried from the task page so the same durable task keeps its
  history. Creating a new stage batch is reserved for rows that have not yet
  entered that stage.
- Bulk failure handling uses `任务` -> `批量重试` or
  `scripts/manager_retry_stage_failures.py`. It previews selected failed rows
  first, then requeues only when `apply`/`--apply` is set.
- Slow external-state handling uses `任务` -> `批量轮询` or
  `scripts/manager_requeue_stage_polls.py`. It requeues status reads for
  waiting tasks without submitting a second repost or claim.
- The API rejects claim stage creation before repost validation succeeds, which
  keeps CLI, UI, and server-side operators on the same state machine.
- The default dispatch window is 10. It can be lowered for browser-heavy
  provider runs or raised after runtime observation.
- When imported account rows and private-key rows are meant to correspond, use
  `scripts/manager_create_bound_pairs_from_files.py` for the bind stage instead
  of the generic database-order selector. The paired script preserves input
  line order and does not compact later rows when an earlier row is skipped.

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
