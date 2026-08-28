# Account, Wallet, and Task Manager Execution Intent

## Requested Outcome

Implement the approved account, wallet, and task manager in the current
workspace, starting with the backend runtime substrate.

## Scope

- Add the manager application as a sibling of the existing CLI.
- Start with Batch 1, Task 1 from the approved implementation plan.
- Keep the existing `twitter_cli` package and its CLI contract unchanged.

## Non-goals

- Do not modify existing uncommitted CLI, script, test, README, or `.gitignore`
  changes.
- Do not add live provider credentials, wallet material, or connection strings
  to files, logs, fixtures, or commits.
- Do not begin provider adapter actions during the runtime-substrate slice.

## Success Evidence

- Typed manager configuration loads required environment variables.
- Local Docker Compose configuration is valid.
- API live/readiness endpoints are covered by focused tests.
- Existing non-smoke CLI regression suite remains green.

## Stop Conditions

- Pause for a failing baseline, missing required runtime dependency, or
  discovered plan/spec contradiction.
- Return to planning if the slice requires changing the existing CLI contract
  or a new unplanned architecture owner.

## Baseline Read Set

- `docs/aegis/BASELINE-GOVERNANCE.md`
- `docs/aegis/baseline/2026-08-28-initial-baseline.md`
- `docs/aegis/specs/2026-08-28-account-wallet-task-manager-design.md`
- `docs/aegis/plans/2026-08-28-account-wallet-task-manager-implementation.md`
- `README.md`
- current `twitter_cli/`, `scripts/`, and `tests/` layout

## Baseline Usage

- Required refs: all documents in the baseline read set.
- Acknowledged refs: all required refs above.
- Cited refs: existing CLI compatibility, PostgreSQL durable-state boundary,
  Redis transport boundary, and no-secret telemetry constraint.
- Missing refs: no existing manager runtime exists.
- Decision: continue.

## Impact Statement

- Affected layers: new manager API package, local service configuration, and
  manager-specific tests.
- Existing owner impact: none; `twitter_cli` stays the X client substrate.
- Invariants: no secrets in tracked files; manager state remains outside CLI
  modules; PostgreSQL and Redis are independently health-checked.
- Compatibility: existing CLI command entry point and tests remain unchanged.

## Execution Readiness View

- Intent Lock: local-first, single-administrator manager.
- Scope Fence: Batch 1 runtime substrate only.
- Baseline Lock: do not change current CLI ownership or behavior.
- Owner / Contract Constraints: manager runtime owns config and health checks;
  no vault, task engine, or adapter behavior in this slice.
- Compatibility Boundary: preserve existing CLI and never commit connection
  values or account/wallet secrets.
- Retirement Boundary: no direct script persistence is introduced.
- Test Obligations: focused manager health/config tests plus existing non-smoke
  suite.
- Review Gates: inspect the first manager slice before schema work.
- Drift / Rewind Rules: pause if new code would alter `twitter_cli` or require
  a provider protocol decision.
- Evidence Required: Compose validation, focused tests, and non-smoke suite.
