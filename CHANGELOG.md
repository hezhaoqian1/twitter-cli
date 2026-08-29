# Changelog

## 0.9.0.3 - 2026-08-29

### Changed
- The headed workbench now opens the X page for manual binding and reposting.
- X is no longer reposted automatically or closed by the workbench.
- The workbench API no longer requires or returns an automatic repost target.

## 0.9.0.2 - 2026-08-29

### Added
- Split the operations console into independent verify, bind, repost, and claim stage batches.
- Added a stage summary view so operators can see readiness, waiting work, and failures per stage.
- Added separate batch actions for stage-oriented workflow runs.

### Changed
- The manager now requeues due external polls before scheduling, so delayed repost checks resume automatically.
- The API and UI now show redacted target state instead of raw task targets.
- The local API runs through the Clash tunnel with the `create_app()` factory entry point.

### Fixed
- Repost polling no longer repeats the repost action while waiting for Kredo validation.
- The Clash tunnel now closes idle connections quietly instead of printing timeout tracebacks.
- The operations overview now reflects the current `8000` backend and `5178` UI runtime.

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
