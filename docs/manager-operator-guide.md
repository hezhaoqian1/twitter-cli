# Manager Operator Guide

This guide describes the local management console at
`http://127.0.0.1:5178/`.

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

- one private key;
- one BIP-39 mnemonic with a start index and derivation count.

For mnemonic derivation, the manager uses the Ethereum BIP-44 path:

```text
m/44'/60'/0'/0/index
```

Use `预览` to inspect public addresses and derivation paths. Only the public
address is displayed in the list. The source material remains encrypted in
the Vault.

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
