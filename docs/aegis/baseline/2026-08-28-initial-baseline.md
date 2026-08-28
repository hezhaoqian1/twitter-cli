# twitter-cli Initial Baseline

Date: `2026-08-28`  
Status: `initial dual-baseline snapshot`

## 1. Purpose

This baseline captures the repository before the proposed local management
application is planned. It separates the existing command-line package from
the future control-plane application so later work can identify accidental
coupling and scope drift.

## 2. Workspace Structure

- `twitter_cli/`: current Python package and X client implementation.
- `scripts/`: operational probes and session-import utilities.
- `tests/`: unit and smoke-test coverage for the CLI and local probes.
- `docs/`: verified flow notes.
- `README.md`: public CLI documentation and primary user-facing authority for
  existing commands.

## 3. Current Authority Surfaces

- `AGENTS.md`: workspace process instructions.
- `README.md`: CLI contract and supported commands.
- `docs/x-cookie-auth-flow.md`: session import and verification observations.
- `docs/kredo-wallet-login-flow.md`: wallet login and asynchronous task-flow
  observations.
- No pre-existing management application specification, database schema, API,
  worker runtime, or UI architecture exists.

## 4. Product / Requirement Baseline

### 4.1 Current Truth

The repository is currently a terminal-first X client. The next product phase
is a local single-administrator management application for imported social
accounts, wallet identities, immutable account-address relationships, and
independent task runs. The user has confirmed that performance, task
independence, and non-interference are first-class requirements.

### 4.2 Non-negotiables

1. Each account and wallet must remain independently operable.
2. An account and a wallet address are both single-binding resources.
3. Sensitive data must be encrypted at rest and recoverable with the
   administrator's retained recovery material.
4. A delayed third-party task status is not treated as an immediate failure.
5. Local-first deployment must have a straightforward server migration path.

### 4.3 Product Non-goals

- Multi-administrator collaboration in v1.
- Automatic rebinding of an account or address.
- Combining account records into a shared browser profile.
- Replacing the existing `twitter` CLI contract during the first delivery.

## 5. Architecture / Runtime Boundary Baseline

### 5.1 Current Truth

The current canonical X client owner is the `twitter_cli` Python package. It
uses cookie-based session material and exposes CLI commands. Browser probes
exist in `scripts/`, but there is no persistent job scheduler or durable data
model.

The new manager must be an application layer that calls a stable adapter around
the existing client behavior. It must own its own API, database, task queue,
browser lifecycle, and encryption vault.

### 5.2 Architecture Non-negotiables

1. Existing CLI commands remain backward compatible.
2. The manager owns persistence; scripts and CLI commands do not write directly
   to the manager database.
3. Worker processes receive a single resolved task and never share a browser
   context between account identities.
4. Database state is the source of truth for task status, leases, and bindings.

### 5.3 Architecture Non-goals

- A distributed microservice fleet for v1.
- SQLite as the durable queue and lock owner.
- Plaintext secret files, browser profiles, or task payloads in the repository.

## 6. Ownership / Contract Snapshot

| Surface | Current owner | Planned owner |
|---|---|---|
| X request behavior | `twitter_cli` | X integration adapter over `twitter_cli` |
| Imported records | none | manager database |
| Sensitive values | environment/session files | encrypted manager vault |
| Wallet signing | Kredo browser probe | wallet browser adapter |
| Batch scheduling | none | task worker and queue |
| Management UI | none | local web application |

## 7. Current State and Risks

- Existing browser and API observations show that third-party task status can
  update asynchronously.
- Imported credentials and wallet material are sensitive and must remain
  redacted in all logs, exports, and tests.
- The repository's `.gitignore` ignores JSON broadly, so future encrypted
  backup artifacts need an explicit external storage path rather than
  repository storage.

## 8. Alignment Use

Read this baseline before planning the application layer. Report `scope: both`
when a change affects the management workflow and the existing CLI adapter
boundary.

## 9. Compatibility Boundary

Existing CLI installation, auth priority, command names, and test fixtures must
continue to work while the management application is introduced.
