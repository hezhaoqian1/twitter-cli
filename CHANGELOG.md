# Changelog

## 0.9.0.1 - 2026-08-28

- Add read-only runtime metrics for Redis queues, task states, resource leases,
  task completion time, and Worker heartbeat summaries.
- Add runtime metrics to the Overview page for batch operations monitoring.
- Keep Railway PostgreSQL and Redis smoke verification routed through Clash
  `127.0.0.1:7890`.

## 0.9.0 - 2026-08-28

- Add encrypted account import and vault backup/restore workflows.
- Add wallet import and derivation with immutable account-wallet bindings.
- Add task batches, Redis-backed queueing, resource leases, retries, and worker recovery.
- Add Kredo and X adapter contracts plus operational scripts.
- Add the React/Vite operations console for accounts, wallets, bindings, tasks, and vault status.
- Add local and Railway deployment documentation and configuration.
