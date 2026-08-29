# Manager Operator Guide

This guide describes the local management console at
`http://127.0.0.1:5178/`.

For a Windows desktop setup, see
[`manager-windows-local-run.md`](./manager-windows-local-run.md).

The console manages imported X sessions, wallet addresses, immutable
account-wallet bindings, and Kredo task stages. It is designed for one
operator and independent rows. A slow or failed row does not pause unrelated
rows.

## Start Here

The local stack has three parts:

```text
Browser UI :5178
     |
FastAPI API :8000
     |
PostgreSQL + Redis
     |
Worker processes
```

Start the API and Worker with the same environment values. The local Railway
connection values belong in the untracked `.env.manager` file or in the
process environment. Do not put concrete connection strings, account
credentials, cookies, private keys, or recovery keys in tracked documentation.

Check the API before opening the UI:

```sh
curl -fsS http://127.0.0.1:8000/health/ready
```

The response should report `status: "ok"` and healthy PostgreSQL and Redis
checks.

## First-Time Vault Setup

The Vault protects all sensitive fields:

- X password, TOTP seed, email password, token, and full Cookie;
- wallet private key and mnemonic;
- backup and recovery metadata.

On the first run:

1. Open `Vault`.
2. Select `初始化`.
3. Set the local management password.
4. Record the generated recovery key offline before closing the dialog.
5. Use `解锁` before importing data or creating tasks.

The management password is for normal local unlocks. The recovery key is for
migration and backup recovery. The recovery key is displayed once and is not
recoverable from the UI later.

The Vault page also provides:

- `导出`: create an encrypted portable backup;
- `校验`: verify a backup without changing the current database;
- `恢复`: restore into an empty manager database;
- `立即锁定`: clear the process-local decrypted key.

## Import X Accounts

The account importer accepts exactly seven tab-separated columns:

```text
登录账号    密码    2FA    邮箱    邮箱密码    token    Cookie
```

The UI supports paste or file-based input. Use `预览` first. The preview
reports valid, malformed, duplicate, existing, and conflicting rows without
showing plaintext secrets.

Select `写入 Vault` only after the row count looks correct. The list then shows
the handle, masked email, session health, lifecycle state, and whether an
encrypted secret exists. Raw credentials are not returned by list endpoints.

Server-side import uses the same parser and Vault encryption:

```sh
uv run python scripts/manager_import_operator_data.py \
  --accounts-file ./operator-data/accounts.tsv \
  --private-keys-file ./operator-data/private-keys.txt \
  --vault-password-env WORKER_VAULT_PASSWORD \
  --init-vault \
  --recovery-key-output ./operator-data/vault-recovery.key
```

Run with `--dry-run` first to preview counts without writing rows. The command
prints only aggregate JSON counts. It does not print account handles, cookies,
tokens, private keys, or the recovery key. When initializing a new Vault, store
the generated recovery key file offline and remove it from the server after
backup procedures are verified.

Recommended account sequence:

1. Import all rows.
2. Create a `批量校验` stage for up to 10 accounts.
3. Review account health and retry only failed rows.
4. Continue with binding after the usable accounts are known.

For pasted exports, the full Cookie column is the preferred session input.
Minimal `auth_token` plus `ct0` authentication is a fallback because X may
reject an otherwise valid session with a challenge or `403` response.

## Import or Derive Wallets

Open `地址` and select `导入地址`.

Supported sources:

- private keys, one per line;
- one BIP-39 mnemonic with a start index and derivation count.

For mnemonic derivation, the manager uses the Ethereum BIP-44 path:

```text
m/44'/60'/0'/0/index
```

For private key import, empty lines and lines starting with `#` are ignored.
Use `预览` to inspect public addresses, duplicate rows, and derivation paths.
Only the public address is displayed in the list. The source material remains
encrypted in the Vault.

## Bind Accounts to Addresses

Binding is a separate stage from account verification. It is also immutable
after external confirmation.

Manual path:

1. Open `账号`.
2. Choose an active, unbound account.
3. Select `绑定`.
4. Choose an active, unbound address.
5. Create the task.

Batch path:

1. Open `账号`.
2. Select `批量绑定`.
3. Auto-fill or choose up to 10 account-address pairs.
4. Create the batch.
5. Wait for the Kredo callback and state propagation.

A pending binding is not the same as a confirmed binding. The pair becomes
available for repost and claim only after the binding state is `已绑定`.
Confirmed pairs are not reassigned. Archive the old record if it is no longer
needed.

When the account file and private-key file are maintained as matching rows,
prefer `账号` -> `文件配对绑定` in the management console. Paste the same account
TSV and one-private-key-per-line input, preview the aggregate counts, then
create the bind stage. It derives each wallet address from the private key
input, finds the matching imported database records, and creates only a `bind`
stage for rows that pass preflight checks.

The server-side script is the same backend path and is intended for deployment
checks or emergency operations:

```sh
uv run python scripts/manager_create_bound_pairs_from_files.py \
  --accounts-file ./operator-data/accounts.tsv \
  --private-keys-file ./operator-data/private-keys.txt \
  --name "bind wave 1" \
  --limit 10 \
  --dispatch-limit 10 \
  --dry-run
```

Remove `--dry-run` to create the pending bindings and bind tasks. The output is
aggregate JSON only. It does not print handles, cookies, tokens, private keys,
or full addresses. Rows with duplicate handles, duplicate wallet addresses,
missing imports, unhealthy accounts, existing bindings, or active leases are
left out of the batch. A skipped row does not shift later rows: line 12 of the
account file is paired only with line 12 of the private-key file.

## Semi-Automatic Browser Workbench

Use the browser workbench when Kredo/X binding needs human confirmation in the
headed page. This is the primary operator path for the current Kredo task.

Open `绑定`, select one or more non-archived rows, then select `打开工作台`.
The API starts one independent headed browser process per selected binding, up
to 10 at a time. Each process:

1. loads the selected row's imported X Cookie into an isolated browser context;
2. injects the selected row's wallet private key as the local wallet provider;
3. opens Kredo's task page;
4. leaves the main browser tab on the Kredo task page/modal;
5. when you click Kredo's `前往 X`, opens the X page in a separate tab and
   keeps that tab open for your manual binding and repost actions.

The workbench does not automatically bind X or claim rewards. Binding and
claiming remain manual after the wallet connection is ready.

The browser uses two tabs with separate responsibilities:

- Main Kredo tab: remains open for manual `前往 X`, wallet confirmation,
  binding, task refresh, and reward claim.
- X tab: is opened by Kredo's manual `前往 X` action and remains open. The
  operator completes the X binding and repost manually, then closes it when
  finished.

If Kredo creates an OAuth tab at `about:blank`, the workbench waits for the
authorization URL returned by Kredo and navigates that same tab to X. The tab
stays open for the operator; the workbench does not repost or close it.

The response only includes the binding id, local browser process id, and
screenshot path. It does not return account passwords,
TOTP seeds, email passwords, tokens, cookies, private keys, or mnemonics.

Use this mode in waves:

1. Select up to 10 pending or bound rows.
2. Select `打开工作台`.
3. Wait for the headed browsers to show Kredo.
4. In each browser, complete the Kredo binding or claim action manually.
5. Refresh the manager binding page or run the read-only status sync later.

The workbench does not require a full Worker loop and does not chain into the
next stage automatically. A row whose Kredo status updates slowly can remain
open while other rows proceed.

## Stage-Oriented Operation

Run each external step as its own unit. Do not treat login, binding, repost,
and claim as one mandatory end-to-end chain, because rows can be in different
states and Kredo's repost validation can update slowly.

Recommended flow:

1. `批量校验`: verify imported X sessions first.
2. `批量绑定`: bind only verified accounts to available addresses.
3. Refresh or wait until rows become `已绑定`.
4. `批量转发`: submit repost for bound rows.
5. Poll task status until Kredo marks the repost as verified.
6. `批量领取`: claim only rows whose repost validation has completed.

Manual buttons follow the same unit boundaries:

```text
账号行: 绑定
绑定行: 转发 / 领取 / 同步
任务行: 暂停 / 重试 / 轮询 / 批量轮询 / 批量重试
```

Each click creates or reuses one durable task for that single operation. It
does not automatically continue into the next operation.

The binding page bulk buttons count only rows that can currently enter that
stage. A selected row that already has a repost task is left out of a new
repost batch. A selected row that already has a claim task is left out of a new
claim batch. Retry failed work from `任务` so the retry stays attached to the
original task event history.

Use the two task-page maintenance actions for slow or mixed rows:

- `批量轮询`: requeue waiting external-validation tasks that already have an
  external operation reference. This is for Kredo/X state that updates slowly.
- `批量重试`: requeue failed tasks for one stage. This is for rows whose last
  worker attempt reached a terminal failure and should be attempted again.

The same stages can be created from a server shell after the API database is
configured:

```sh
uv run python scripts/manager_stage_status.py
uv run python scripts/manager_next_stage.py

uv run python scripts/manager_create_stage_batch.py verify --dry-run
uv run python scripts/manager_create_stage_batch.py verify --name "verify wave 1" --limit 10 --dispatch-limit 10

uv run python scripts/manager_create_stage_batch.py bind --dry-run
uv run python scripts/manager_create_stage_batch.py bind --name "bind wave 1" --limit 10 --dispatch-limit 10

uv run python scripts/manager_create_stage_batch.py repost --target "https://x.com/.../status/..." --dry-run
uv run python scripts/manager_create_stage_batch.py repost --target "https://x.com/.../status/..." --name "repost wave 1" --limit 10 --dispatch-limit 10

uv run python scripts/manager_create_stage_batch.py claim --dry-run
uv run python scripts/manager_create_stage_batch.py claim --name "claim ready rows" --limit 10 --dispatch-limit 10
```

`bind` defaults to healthy accounts only. Use `--include-unverified` only when
you intentionally want to bind accounts whose verification has not passed yet.
`repost` skips rows that already have a repost task for the same target.
`claim` selects only rows with a succeeded repost task and no existing claim
task. The API applies the same claim readiness rule, so a direct stage request
cannot queue a claim while Kredo repost validation is still pending.

On a server, the normal loop is:

```text
status -> create next ready stage -> worker drain/worker loop -> status
```

Use `manager_acceptance_audit.py` when you want one read-only snapshot with the
stage table, the next recommended action, and every currently available
command template:

```sh
uv run python scripts/manager_acceptance_audit.py --limit 10
```

The management UI uses the same snapshot through:

```http
GET /api/runtime/acceptance-audit
```

Treat this as the normal binding acceptance view. It surfaces Kredo task API
status, pollable bind callbacks, syncable pending bindings, retryable failures,
and the next aggregate action without requiring a manual browser check.

Run it before and after each batch. For mixed bind rows, it may list `poll`,
`sync_bind_status`, and `retry` at the same time; execute those as separate
units so one slow callback does not block the rest of the accounts.

If a stage is slow but not failed, use `manager_requeue_stage_polls.py`. If a
stage is failed, use `manager_retry_stage_failures.py`. These two commands are
separate on purpose so a slow Kredo callback does not get treated as a fresh
external action.

Bind status is interface-first. The browser worker uses wallet login and X
OAuth only to reach the task flow, then opens the Kredo task page and reads the
Kredo task API responses (`tasks/twitter` and `tasks/overview`) to decide
whether the row is bound. Page buttons are used to trigger a refresh; they are
not the source of truth.

If manual validation shows the X authorization returned to Kredo but the
manager row is still `pending`, use `任务` -> `同步绑定状态`. This creates
status-only bind jobs with an external operation reference, so the worker reads
the Kredo task API and does not click the bind/OAuth action again. The command
also pauses queued first-bind jobs for the same pending binding to avoid a
duplicate OAuth attempt.

The overview page shows the same next-step recommendation from the API, so the
browser console and server shell share one decision path. When pending bindings
are syncable but not pollable, the recommendation points to
`manager_sync_bind_status.py` / `任务` -> `同步绑定状态` instead of telling the
operator to wait.

## Repost and Claim

The console intentionally separates repost from claim:

1. Open `绑定`.
2. Select confirmed bindings.
3. Start `批量转发` and provide the target post.
4. Wait for the external validation state to become successful.
5. Select only eligible rows and start `批量领取`.

A repost job submits the action once. If Kredo has accepted the action but its
task page has not updated, the job enters `等待外部校验`. The scheduler polls
the external state later without submitting the repost again.

The binding list now shows a per-row task stage column:

```text
转发状态 / 领取状态
```

Rows marked `可领取` are the only rows included by the bulk claim button. If a
selected row is still waiting for repost validation, it remains selected for
inspection but is left out of the claim batch until the next refresh shows it
as claim-ready.

This is the normal operating pattern:

```text
全部会话校验
      |
批量绑定已通过账号 + 地址
      |
批量转发
      |
等待 Kredo 回写
      |
只领取已满足条件的行
```

Do not create a claim task for rows still marked as pending repost validation.
The UI keeps task rows and failures independent, so one delayed row can remain
pending while other rows proceed.

## Statuses

| UI status | Meaning |
|---|---|
| `可用` | Resource can be selected for a new operation. |
| `排队中` | Durable task exists and is waiting for a worker. |
| `执行中` | A worker currently owns the task lease. |
| `待确认` | External state has not reached its final confirmation. |
| `等待外部校验` | The external provider accepted the action, but its status is delayed. |
| `已完成` | The operation reached its terminal success state. |
| `失败` | The operation reached a terminal failure and can be inspected or retried. |
| `已绑定` | The account-address association is externally confirmed. |

Use `任务` to inspect batch progress, task events, retries, leases, and
external operation references. Use `总览` to see stage readiness, delayed work,
and failures at a glance.

## Batch and Isolation Rules

- The default dispatch window is 10.
- Each account and wallet has an independent lease key.
- A browser context belongs to one task and one account identity.
- PostgreSQL is the durable source of truth.
- Redis transports ready work and heartbeats.
- Repeated clicks reuse deterministic task keys where supported.
- Pause a batch before dispatch when the external provider needs to be
  observed manually.
- The UI keeps a short in-memory cache for list and summary pages. Switching
  tabs reuses the latest loaded data for about 30 seconds unless you press
  the refresh button or the task page auto-polls.

For larger runs, start with 10 and increase only after observing browser
memory, provider latency, queue depth, and failure rates.

## Backup and Migration

Before moving the manager to another machine or Railway:

1. Unlock the Vault.
2. Export an encrypted backup.
3. Store the backup and recovery key in separate offline locations.
4. Prepare an empty target database.
5. Restore the backup.
6. Run `校验` and unlock with the recovery key.
7. Start the API and Worker against the restored database.
8. Confirm account, wallet, binding, and task counts before dispatching work.

The local `.env.manager` file is intentionally ignored by git. Set
`DATABASE_URL`, `REDIS_URL`, `SESSION_SECRET`, and Worker variables in the
deployment secret store instead of copying them into this guide.

## Acceptance Check

The deterministic local acceptance run exercises the same queue, Vault, and
independent stage model without real provider credentials:

```sh
uv run python scripts/manager_synthetic_e2e.py
```

Expected result:

```text
jobs=16
bindings_bound=4
delayed_reposts_polled=4
repost_calls=4
claim_calls=4
queue_ready=0
queue_processing=0
```
