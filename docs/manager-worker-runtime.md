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

## Required Variables

The Worker reads these values from its environment:

```text
DATABASE_URL
REDIS_URL
SESSION_SECRET
MANAGER_KREDO_WORKFLOW_FACTORY=module.path:factory_name
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
WORKER_LEASE_TTL_SECONDS
WORKER_RECOVERY_INTERVAL_SECONDS
WORKER_HEARTBEAT_INTERVAL_SECONDS
WORKER_HEARTBEAT_TTL_SECONDS
WORKER_IDLE_SLEEP_SECONDS
```

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
