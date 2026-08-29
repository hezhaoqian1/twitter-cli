# Manager Worker Runtime

The manager runs the API and Worker as separate processes over the same
PostgreSQL and Redis configuration.

## Worker Service

Use the repository image and set the Worker service start command to:

```sh
python scripts/manager_migrate.py && exec python scripts/manager_worker.py
```

The API service keeps the `railway.json` start command and serves
`/health/live`. The Worker service does not need a public port.

The manager Docker image installs Playwright Chromium during build so the same
image can run both the API and browser-backed Worker. For local compose runs,
`docker-compose.manager.yml` starts `api` and `worker` as separate services and
mounts `./artifacts` into the Worker container so screenshots remain visible on
the host.

## Required Variables

The Worker reads these values from its environment:

```text
DATABASE_URL
REDIS_URL
SESSION_SECRET
MANAGER_KREDO_WORKFLOW_FACTORY=manager_api.adapters.kredo_browser_workflow:kredo_workflow_factory
WORKER_VAULT_PASSWORD
```

`WORKER_VAULT_PASSWORD` unlocks the process-local vault key during Worker
startup. Keep it in the platform secret store. It is never printed or written
to task events. When it is absent, the Worker still starts and reports
vault-locked task failures until the process is configured with the unlock
secret.

The optional tuning variables are:

```text
WORKER_CONCURRENCY
BROWSER_CONCURRENCY
MANAGER_KREDO_BROWSER_TIMEOUT_SECONDS
MANAGER_KREDO_BROWSER_ARTIFACT_DIR
MANAGER_KREDO_BROWSER_HEADED
KREDO_BROWSER_PROXY
KREDO_BROWSER_CHANNEL
WORKER_LEASE_TTL_SECONDS
WORKER_RECOVERY_INTERVAL_SECONDS
WORKER_HEARTBEAT_INTERVAL_SECONDS
WORKER_HEARTBEAT_TTL_SECONDS
WORKER_IDLE_SLEEP_SECONDS
```

## Semi-Automatic Workbench Runtime

The management API also exposes a local headed-browser workbench for operator
controlled binding and claiming:

```http
POST /api/bindings/{binding_id}/manual-workbench
POST /api/bindings/manual-workbenches
```

This path is separate from the Worker queue. It decrypts one selected
account-wallet binding inside the API process, writes a temporary X Cookie file
under a private temp directory, injects the wallet key into a child browser
process through the child environment, and starts
`scripts/kredo_wallet_login_probe.py` with:

```text
--headed --keep-open --no-bind-twitter --no-wait-task-state
```

The bulk endpoint accepts at most 10 binding ids per request. Each id becomes
one independent browser process and one isolated browser context, so a slow
Kredo page does not block the other selected rows.

The headed workbench has two browser tabs with different responsibilities:

- The main tab stays on Kredo's task page for manual X binding, task refresh,
  and reward claiming.
- When the operator clicks Kredo's manual `前往 X` action, the OAuth X tab is
  opened or repaired from `about:blank` and remains open for manual binding
  and reposting. The workbench does not click repost or close the tab.

When the operator later clicks Kredo's manual X-binding action, Kredo may
create an `about:blank` popup before its bind API returns `authorizeUrl`. The
probe watches both events and navigates that same popup to X as soon as both
pieces are available. The repaired OAuth popup stays open for the operator.

`KREDO_BROWSER_PROXY` configures the browser network path independently from
the PostgreSQL/Redis Clash tunnel. Set it to
`http://127.0.0.1:7890` when the local network requires Clash. Existing
browser processes must be closed and relaunched after changing it.

For local use, the endpoint can unlock the Vault from `WORKER_VAULT_PASSWORD`
when the UI Vault session is locked. Keep that value only in `.env.manager` or
the server secret store. The HTTP response is intentionally public-only:
binding id, process id, and screenshot path.

`MANAGER_KREDO_WORKFLOW_FACTORY` is the bridge between the durable task
system and the browser workflow that was proven manually. The built-in factory
creates one isolated browser context per task, injects the local wallet
provider, loads the imported X Cookie for that account, and returns a redacted
status payload to the Worker.

The factory is stage-oriented:

| Worker stage | Browser action |
|---|---|
| `bind` | Wallet login, open Kredo task, authorize X binding, poll binding state. |
| `repost` | X repost is submitted by the X adapter; Kredo browser workflow only polls Kredo's delayed validation state. |
| `claim` | Preflight Kredo status, then click the claim action only for eligible rows. |
| `status` | Read-only Kredo task polling for delayed callback or repost validation. |

This keeps slow Kredo propagation from blocking unrelated accounts. Operators
can run a bind batch, wait for external state to settle, then create a repost
batch only for bound rows, and later create a claim batch only for rows whose
repost validation has completed.

For a no-provider dry run, point both adapters at the built-in synthetic
fixtures:

```text
MANAGER_X_ADAPTER_FACTORY=manager_api.synthetic_kredo:build_synthetic_x_adapter
MANAGER_KREDO_WORKFLOW_FACTORY=manager_api.synthetic_kredo:synthetic_workflow_factory
```

The synthetic path uses the same Vault, scheduler, Redis queue contract, Worker
loop, task transitions, and row-level readiness rules. It confirms bindings
immediately, makes repost validation pending once, then succeeds on the next
poll so the claim stage can be tested without external accounts or browser
state.

Screenshots are written under `MANAGER_KREDO_BROWSER_ARTIFACT_DIR`, defaulting
to `artifacts/kredo-worker/`. File names contain only the task kind, a short
task id prefix when available, and a random suffix.

## Binding Diagnostics

The browser workflow separates three binding outcomes:

- `bound`: Kredo already reports the current X handle on the task state. The
  binding row is confirmed and can enter the repost stage.
- `pending_bind`: the bind action completed locally, but Kredo has not yet
  published the final task state. The task waits for an external-status poll.
- `failed`: OAuth or task state showed a terminal problem. Examples include an
  X OAuth `401` or `403`, a missing authorize URL, or an authorize popup that
  stayed on the X authorization page and never reached the Kredo callback.

A waiting bind poll is read-only. If it sees Kredo still reporting `unbound`
for that binding, the task exits as failed instead of waiting forever. Retry
that row from the `任务` page after inspecting its screenshot and failure code.
Create a new bind stage only for rows that have not yet entered binding.

## Worker Doctor

Before dispatching real jobs, run the no-account runtime check:

```sh
python scripts/manager_worker_doctor.py --launch-browser
```

It verifies:

1. required settings parse correctly;
2. `MANAGER_KREDO_WORKFLOW_FACTORY` loads and returns a context manager;
3. Playwright imports;
4. headless Chromium can launch.

Add `--check-network` when the process should also prove PostgreSQL and Redis
connectivity with `SELECT 1` and `PING`.

Use synthetic mode before a deploy or after changing Worker wiring:

```sh
python scripts/manager_worker_doctor.py --synthetic
```

That command auto-fills the local synthetic factory variables for the check and
skips browser dependency checks unless `--launch-browser` is also provided.

## Creating Stage Batches From Shell

Import operator files before creating work:

```sh
python scripts/manager_import_operator_data.py \
  --accounts-file ./operator-data/accounts.tsv \
  --private-keys-file ./operator-data/private-keys.txt \
  --vault-password-env WORKER_VAULT_PASSWORD \
  --init-vault \
  --recovery-key-output ./operator-data/vault-recovery.key
```

Use `--dry-run` first to verify row counts. The private-key file uses one key
per line; blank lines and lines starting with `#` are ignored. The import
command reuses the API's account TSV parser, MetaMask-compatible wallet
validation, and Vault field encryption. Its output is aggregate JSON only.

The UI and API are not required for every batch. A server-side operator can
inspect current readiness, then explicitly choose one stage command from the
configured database:

```sh
python scripts/manager_stage_status.py
python scripts/manager_stage_status.py --json
python scripts/manager_next_stage.py
```

The status command is read-only. It prints resource totals plus, for each
stage, how many rows are ready for a new stage batch, waiting for external
state, failed, pollable, syncable, and retryable. `syncable` is currently used
for pending Kredo/X bindings that can be checked through the task API without
clicking OAuth again. The next-stage command is also
read-only. It prints a suggested single-stage command, preferring slow status
polling before failed-task retry, and failed-task retry before creating a new
stage batch. It never creates the stage itself and never continues into a later
stage after the suggested command finishes.

For a single acceptance snapshot that combines stage counts, the next
recommended action, and all currently available command templates, run:

```sh
python scripts/manager_acceptance_audit.py --limit 10
python scripts/manager_acceptance_audit.py --limit 10 --json
```

Use this before and after each real batch. It is read-only and prints only
aggregate counts plus command templates. A typical mixed bind state can show
`pollable` rows, `syncable` rows, and `retryable` rows at the same time; handle
them as independent units instead of starting a new end-to-end flow.

The UI reads the same recommendation from:

```http
GET /api/runtime/next-stage
```

It returns only `action`, `stage`, a generic command template, and a reason.

The overview acceptance panel reads the full read-only checklist from:

```http
GET /api/runtime/acceptance-audit
```

Use that endpoint when deciding whether binding succeeded. It is the
interface-first path: Kredo `tasks/twitter` and `tasks/overview` are the source
of truth, while page buttons and modal refreshes are only used to make Kredo
emit a fresh task state.

```sh
python scripts/manager_create_stage_batch.py verify --limit 10 --dispatch-limit 10
python scripts/manager_create_stage_batch.py bind --limit 10 --dispatch-limit 10
python scripts/manager_create_stage_batch.py repost --target "https://x.com/.../status/..." --limit 10 --dispatch-limit 10
python scripts/manager_create_stage_batch.py claim --limit 10 --dispatch-limit 10
```

Add `--dry-run` first to print the selected row count without inserting task
rows. The script prints only stage name, batch name, dispatch limit, and item
count. It does not print account credentials, cookies, private keys, or the raw
target string.

The Worker does not need to be restarted after a new batch is created. It will
dispatch eligible queued jobs on the next loop, subject to `WORKER_CONCURRENCY`,
`BROWSER_CONCURRENCY`, and the batch's `dispatch_limit`.

Create only one stage batch at a time. After that stage drains or waits for
external validation, return to `manager_stage_status.py` and decide whether the
next explicit action is polling, retrying, another stage batch, or waiting.

For local validation or one-off server maintenance, run a bounded drain instead
of the forever worker:

```sh
python scripts/manager_worker_drain.py --max-cycles 20 --dispatch-limit 10
```

The drain command uses the same `DATABASE_URL`, `REDIS_URL`,
`WORKER_VAULT_PASSWORD`, `MANAGER_X_ADAPTER_FACTORY`, and
`MANAGER_KREDO_WORKFLOW_FACTORY` as the long-running Worker. It dispatches and
consumes immediately runnable jobs, prints `cycles`, `dispatched`, and
`completed`, then exits. Delayed Kredo repost checks remain in
`等待外部校验` until their next poll time or a manual task poll command requeues
them.

To requeue delayed external-status reads in bulk without creating a new stage
batch, use:

```sh
python scripts/manager_requeue_stage_polls.py bind --limit 10
python scripts/manager_requeue_stage_polls.py repost --limit 10
python scripts/manager_requeue_stage_polls.py claim --limit 10
```

Those commands preview counts only. Add `--apply` to requeue selected waiting
tasks. The script only selects waiting tasks that already have an external
operation reference, so the next worker pass performs a status read instead of
starting a fresh bind, repost, or claim action.

The UI exposes the same operation from `任务` -> `批量轮询`. The API endpoint is:

```http
POST /api/tasks/stage-polls
```

For pending bindings that were completed in the browser but never reached a
pollable `waiting_external_validation` task, use `任务` -> `同步绑定状态`:

```http
POST /api/tasks/bind-status-sync
```

Default requests preview counts. With `apply: true`, the API creates
`stage:bind-status` jobs using `external_target = "kredo:bind-status"` and a
pre-filled operation reference. That forces the worker bind handler onto the
read-only `status(...)` path. Any queued first-bind job for the same pending
binding is paused before the status job is queued.

Example body:

```json
{
  "stage": "bind",
  "limit": 10,
  "apply": false
}
```

Set `apply` to `true` after previewing the selected count. The response is
aggregate-only: stage, selected count, requeued count, skipped missing
references, and whether the mutation was applied.

Failed stage tasks are handled separately from delayed status reads. To preview
or retry failures for one stage:

```sh
python scripts/manager_retry_stage_failures.py verify --limit 10
python scripts/manager_retry_stage_failures.py bind --limit 10
python scripts/manager_retry_stage_failures.py repost --limit 10
python scripts/manager_retry_stage_failures.py claim --limit 10
```

Those commands preview counts only. Add `--apply` to requeue selected failed
tasks through the same task state machine used by the single-row Retry button.
The UI exposes the same operation from `任务` -> `批量重试`. The matching API
endpoint is:

```http
POST /api/tasks/stage-retries
```

Example body:

```json
{
  "stage": "bind",
  "limit": 10,
  "apply": false
}
```

The response is aggregate-only: stage, selected count, retried count, and
whether the mutation was applied.

## Runtime Behavior

Each loop iteration:

1. Publishes the Worker heartbeat to the Redis hash
   `manager:workers:heartbeats`.
2. Recovers expired PostgreSQL leases on the configured interval.
3. Dispatches eligible durable jobs to Redis.
4. Consumes one reliable-list message and acknowledges it only after the
   durable task transition and lease release complete.
5. Commits the lifecycle boundary before taking another message.

`SIGTERM` and `SIGINT` stop the loop after the current synchronous operation,
release active leases, commit the final state, and remove this process's
heartbeat. Redis remains transport-only; PostgreSQL remains the task source of
truth.

## Local Clash Probe

When direct Railway TCP access is unavailable, route the read-only dependency
probe through the local Clash HTTP proxy:

```sh
uv run python scripts/manager_clash_tunnel.py \
  --proxy http://127.0.0.1:7890 \
  -- python -c '...'
```

The tunnel replaces only the child process transport endpoints with loopback
ports. It does not log application payloads or credentials.

To run the local API through the same tunnel, use the ASGI factory entry point:

```sh
uv run python scripts/manager_clash_tunnel.py -- \
  uv run --with uvicorn python -m uvicorn \
  manager_api.main:create_app --factory \
  --host 127.0.0.1 --port 8000
```

The `--factory` flag is required because `manager_api.main` exposes
`create_app()` rather than a module-level `app`.
